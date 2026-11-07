"""Receipt readers: a photo in, its items (with boxes), printed total, currency and extent out.

The app reads receipts through one reader, chosen with AXISPLIT_OCR_BACKEND. Each reader owns its
own tuning; the app only relies on the contract (`Backend.read`, `Backend.locate`).
Default reader: PaddleOCR (PP-OCRv6, CPU) reads the text lines, rules turn them into
items, and a decision model picks which line is the total. The vision reader asks a
local vision model instead (Qwen grounding coordinates are 0..1000). The http reader asks a
reader running as its own service (docs/reader-api.md).
Coordinates are 0..1 of the EXIF-normalized photo. No fabricated boxes: malformed geometry fails
extraction explicitly.
"""

import base64
import io
import json
import logging
import mimetypes
import os
import threading
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated

import httpx
import numpy as np
from PIL import Image
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from . import receipt_rules
from .config import new_id
from .models import Item

logger = logging.getLogger(__name__)

Focus = tuple[float, float, float, float]  # x, y, w, h, normalized to the photo
Quad = list[tuple[float, float]]  # 4 corners, TL, TR, BR, BL, normalized to the photo


@dataclass
class ExtractedItem:
    label: str
    price_cents: int
    x: float
    y: float
    w: float
    h: float
    confidence: float | None = None
    quad: Quad | None = None  # the printed line's corners, when the reader has them (not stored yet)

    def to_item(self, receipt_id: str) -> Item:
        return Item(
            id=new_id(), receipt_id=receipt_id, label=self.label,
            price_cents=self.price_cents, x=self.x, y=self.y, w=self.w, h=self.h,
            ocr_confidence=self.confidence,
        )


@dataclass
class Reading:
    """What a reader got off one photo. Only the items are required: a reader returns them even
    when the other parts fail, and those are None. `total_cents` is the printed total, read on its
    own, never the items' sum: the review checks one against the other."""
    items: list[ExtractedItem]
    total_cents: int | None = None
    currency: str | None = None  # ISO code
    extent: Focus | None = None  # where the receipt is, to frame it


def corners(box: Focus) -> Quad:
    x, y, w, h = box
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def bounds(quad: Quad) -> Focus:
    xs, ys = [x for x, _ in quad], [y for _, y in quad]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def optional(part: str, fn, *args):
    """An optional part of a reading: if it fails, the receipt is saved without it."""
    try:
        return fn(*args)
    except Exception:
        logger.exception("Reading the receipt's %s failed; saving it without", part)
        return None


class DetectedItem(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    price_cents: int = Field(ge=0)
    bbox: list[int] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def valid_box(self):
        x1, y1, x2, y2 = self.bbox
        if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
            raise ValueError("bbox must be ordered [x1,y1,x2,y2] within 0..1000")
        return self


class Detection(BaseModel):
    items: list[DetectedItem]


class TotalDetection(BaseModel):
    total_cents: int | None


def inside(quad: Quad, x: float, y: float) -> bool:
    """Whether a point lies in the polygon (ray casting)."""
    hit = False
    for (x1, y1), (x2, y2) in zip(quad, quad[1:] + quad[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit
MAX_SIDE = 1600  # longest image side sent to the model


PROMPT = """Extract the purchased line items from this receipt photograph.
Return JSON: {"items":[{"label":"name as printed","price_cents":100,
"bbox":[x1,y1,x2,y2]}]}.
Prices must be integer cents of the LINE TOTAL (quantity times unit price).
Create ONE entry per purchased price row. Never merge two purchases into one
entry just because they share a category or have the same price. A wrapped name
belongs to the preceding quantity row; a new quantity and price starts a NEW item.
Example: "1 Homemade Mango Tea 6.95" then "1 Homemade Watermelon Tea 6.95"
means TWO separate items of 695 cents each, with separate boxes.
Label must contain ONLY the item name, not category headings or unit-price notes.
Exclude headers, categories, subtotal, tax summaries, discounts and grand total.
Do not invent unreadable items or prices. Empty items is allowed if unreadable.
Each bbox must enclose that item's printed name, quantity and price on the
ORIGINAL WHOLE IMAGE, not a cropped receipt. Coordinates are integers 0..1000:
left/top origin, x scaled by whole image width, y scaled by whole image height.
Boxes must tightly cover the printed line (multiple lines if the name wraps).
"""

FOCUS_NOTE = """The user cropped this image to the purchased item lines and their prices.
The grand total and other numbers on the receipt are outside it. Treat this
cropped image as the whole image for bbox coordinates.
"""

# No example amount: models copied it as the answer when a total was hard to read.
TOTAL_PROMPT = """What is the grand total of this receipt: the final amount to pay, as printed
(often labeled Total, Celkom, Celkem, Suma, Summe, or next to the currency)?
Return JSON with "total_cents": the amount in integer cents (hundredths of the currency
unit), so a printed 12,50 is 1250 and a printed 240 Kč without decimals is 24000.
Use null if no total is readable.
"""


class Backend:
    """A receipt reader. It owns its tuning: the app only relies on this contract."""
    name = "base"

    def read(self, image: Path, area: Quad | None = None) -> Reading:
        """The receipt on a photo; with `area` (the items area the user marked), only the items
        inside it. Raises only when the items can't be read: no items is an empty reading."""
        raise NotImplementedError

    def locate(self, image: Path) -> Focus | None:
        """Where the receipt is on a photo, roughly its text (x, y, w, h); None if unknown."""
        return None


class StubBackend(Backend):
    """Explicit opt-in for isolated UI tests only, never the app default."""
    name = "stub"

    def read(self, image: Path, area: Quad | None = None) -> Reading:
        return Reading([])


class LocalVisionBackend(Backend):
    name = "local-vision"

    def read(self, image: Path, area: Quad | None = None) -> Reading:
        items = self.extract_items(image, bounds(area) if area else None, area)
        return Reading(items, optional("total", self.extract_total, image))

    def extract_items(self, image: Path, focus: Focus | None = None, quad: Quad | None = None) -> list[ExtractedItem]:
        fx, fy, fw, fh = focus or (0.0, 0.0, 1.0, 1.0)
        with Image.open(image) as im:
            if focus:  # Only the items area, upscaled: fewer stray numbers, more pixels per line.
                width, height = im.size
                im = im.crop((round(fx * width), round(fy * height),
                              round((fx + fw) * width), round((fy + fh) * height)))
                scale = MAX_SIDE / max(im.size)
                if scale > 1:  # Uniform, so the crop's 0..1000 frame is unchanged.
                    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
            result = self._ask(im, FOCUS_NOTE + PROMPT if focus else PROMPT, Detection)
        # Boxes come back in the sent image's 0..1000 frame; map them onto the whole photo.
        items = [ExtractedItem(
            label=i.label, price_cents=i.price_cents,
            x=fx + i.bbox[0] / 1000 * fw, y=fy + i.bbox[1] / 1000 * fh,
            w=(i.bbox[2] - i.bbox[0]) / 1000 * fw,
            h=(i.bbox[3] - i.bbox[1]) / 1000 * fh,
        ) for i in result.items]
        return [i for i in items if not quad or inside(quad, i.x + i.w / 2, i.y + i.h / 2)]

    def extract_total(self, image: Path) -> int | None:
        with Image.open(image) as im:  # Whole photo: the total sits outside the items area.
            return self._ask(im, TOTAL_PROMPT, TotalDetection).total_cents

    def _ask(self, im: Image.Image, prompt: str, schema: type[BaseModel]) -> BaseModel:
        # Uniform resize only: normalized boxes keep the frame of the image sent.
        im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=95)
        encoded = base64.b64encode(buf.getvalue()).decode()
        url = os.environ.get("AXISPLIT_OCR_URL", "http://127.0.0.1:8081/v1")
        response = httpx.post(
            f"{url.rstrip('/')}/chat/completions",
            timeout=httpx.Timeout(300, connect=5),
            json={
                "model": os.environ.get("AXISPLIT_OCR_MODEL", "local"),
                "temperature": 0,
                "max_tokens": 4096,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "receipt", "schema": schema.model_json_schema()},
                },
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded}",
                    }},
                ]}],
            },
        )
        response.raise_for_status()
        completion = response.json()["choices"][0]
        if completion.get("finish_reason") == "length":
            raise ValueError("OCR response truncated; no partial items saved")
        return schema.model_validate_json(completion["message"]["content"])


# The total question, worded as benchmarked: 17/19 right on unseen receipts with vllm-d3-nano.
DECIDE_STATE = ("Text lines of a receipt photo, read by OCR from top to bottom. "
                "Lines with an amount are labelled.")
DECIDE_TOTAL = (
    "Which labelled line holds the receipt's total: the final amount the customer pays for the "
    "items on this receipt? Not a subtotal: when a subtotal is followed by discounts, the total is "
    "the amount to pay after them. Not a VAT or tax line, savings, cash given or change. "
    "A card-payment slip may be in the photo too (AMOUNT, TIP, TOTAL); its total includes the tip, "
    "so it is not the receipt's total."
)
LETTERS = [chr(c) for c in range(65, 91)] + [chr(c) for c in range(97, 123)]  # A-Z, a-z


class PaddleBackend(Backend):
    """PP-OCRv6 on the CPU reads the text lines; rules turn them into items with boxes; a decision
    model (llama.cpp's /v1/systemone) picks which line is the total. The total is what rules get
    wrong most: VAT, subtotal, discount and card-slip lines all look like one."""
    name = "paddle"

    def __init__(self):
        self._lock = threading.Lock()  # one OCR pass at a time: two in parallel are slower
        self._engine = None

    def read(self, image: Path, area: Quad | None = None) -> Reading:
        # Two passes with an area: the items from it alone (fewer stray numbers), the rest from the
        # whole photo, since the total is outside it.
        try:
            whole = self._read(image, None)
        except Exception:
            if area is None:
                raise  # the items come from this pass
            logger.exception("Reading the whole photo failed; saving the receipt without its total")
            whole = None
        items = self._items(*(whole if area is None else self._read(image, bounds(area))), area)
        if whole is None:
            return Reading(items)
        return Reading(items, optional("total", self._total, *whole),
                       optional("currency", receipt_rules.currency, whole[0]),
                       optional("extent", self._extent, *whole))

    def locate(self, image: Path) -> Focus | None:
        return self._extent(*self._read(image, None))

    def _items(self, lines, width, height, area: Quad | None) -> list[ExtractedItem]:
        if area:  # Lines centred outside it, such as a total below a tilted last item, are left out.
            lines = [(text, box) for text, box in lines
                     if inside(area, sum(x for x, _ in box) / 4 / width, sum(y for _, y in box) / 4 / height)]
        return [ExtractedItem(
            label=i["label"][:120], price_cents=i["price_cents"],
            x=i["bbox"][0], y=i["bbox"][1], w=i["bbox"][2] - i["bbox"][0], h=i["bbox"][3] - i["bbox"][1],
            # A tilted line's rectangle can poke past the photo's edge: kept on it, like the box.
            quad=[(min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)) for x, y in i["quad"]],
        ) for i in receipt_rules.parse(lines, width, height)["items"]]

    def _total(self, lines, width, height) -> int | None:
        rows = receipt_rules.amount_rows(lines, width, height)
        candidates = [i for i, (_, value) in enumerate(rows) if value is not None and value > 0]
        if not candidates:
            return None
        try:
            return rows[self._pick_total(rows, candidates[-len(LETTERS):])][1]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:  # unreachable, or no usable answer
            logger.warning("Decision model unavailable (%s); taking the total from the rules", e)
            return receipt_rules.parse(lines, width, height)["total_cents"]

    @staticmethod
    def _extent(lines, width, height) -> Focus | None:
        if not lines:
            return None
        xs, ys = [x for _, q in lines for x, _ in q], [y for _, q in lines for _, y in q]
        x, y = float(min(xs)) / width, float(min(ys)) / height
        return x, y, float(max(xs)) / width - x, float(max(ys)) / height - y

    def _pick_total(self, rows: list[tuple[str, int | None]], candidates: list[int]) -> int:
        """Index of the row the decision model chooses as the total, from text only."""
        letter = {i: LETTERS[k] for k, i in enumerate(candidates)}
        text = "\n".join(f"{letter[i]}) {t}" if i in letter else f"   {t}" for i, (t, _) in enumerate(rows))
        url = os.environ.get("AXISPLIT_DECIDE_URL", "http://127.0.0.1:8082/v1")
        body = {
            "state": {"description": DECIDE_STATE, "receipt_lines": text},
            "questions": {"total": {"type": "choice", "instructions": DECIDE_TOTAL,
                                    "criteria": {letter[i]: rows[i][0][:80] for i in candidates}}},
        }
        if model := os.environ.get("AXISPLIT_DECIDE_MODEL"):  # vLLM serves models by name
            body["model"] = model
        key = os.environ.get("AXISPLIT_DECIDE_KEY")  # the server's --api-key
        response = httpx.post(f"{url.rstrip('/')}/systemone", timeout=httpx.Timeout(60, connect=5), json=body,
                              headers={"Authorization": f"Bearer {key}"} if key else None)
        response.raise_for_status()
        choice = response.json()["answers"]["total"]["choice"]
        picked = [i for i, code in letter.items() if code == choice]
        if not picked:
            raise ValueError(f"answer {choice!r} is not one of the candidates")
        return picked[0]

    def _read(self, image: Path, focus: Focus | None) -> tuple[list, int, int]:
        """Text lines as (text, quad in whole-photo pixels) inside the focus area, and the photo size."""
        with self._lock:
            with Image.open(image) as im:
                rgb = im.convert("RGB")
            width, height = rgb.size
            fx, fy, fw, fh = focus or (0.0, 0.0, 1.0, 1.0)
            x1, y1 = int(fx * width), int(fy * height)
            x2, y2 = int((fx + fw) * width), int((fy + fh) * height)
            result = self._ocr()(np.asarray(rgb)[y1:y2, x1:x2, ::-1].copy())  # BGR, as OpenCV
            lines = [] if result.boxes is None else [
                (text, [[x + x1, y + y1] for x, y in box])
                for box, text in zip(result.boxes, result.txts)]
            return lines, width, height

    def _ocr(self):
        if self._engine is None:
            from rapidocr import RapidOCR  # loads ONNX Runtime: only when this backend is used
            from rapidocr.utils.typings import ModelType, OCRVersion
            # First use downloads the models (31 MB) into the cache the flake's model servers use.
            default = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "axisplit-models"
            models = Path(os.environ.get("AXISPLIT_MODEL_CACHE", default)) / "rapidocr"
            self._engine = RapidOCR(params={
                "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL,
                "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL,
                "Det.limit_type": "max", "Det.limit_side_len": 1280,  # detect on at most 1280 px
                "Global.log_level": "error", "Global.model_root_dir": str(models),
                # One thread per physical core: all 16 SMT threads of a Ryzen 7800X3D read an
                # upload in 1.5 s, its 8 cores in 0.95 s (same text).
                "EngineConfig.onnxruntime.intra_op_num_threads": max(1, (os.cpu_count() or 2) // 2),
            })
        return self._engine


def real_box(box: Focus) -> Focus:
    x, y, w, h = box
    if w <= 0 or h <= 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
        raise ValueError("a box is [x, y, w, h] inside the photo, with a width and a height")
    return box


Fraction = Annotated[float, Field(ge=0, le=1)]  # also refuses NaN and infinity
Box = Annotated[tuple[Fraction, Fraction, Fraction, Fraction], AfterValidator(real_box)]


class ReaderItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=120)
    price_cents: int = Field(ge=0, strict=True)
    box: Box | None = None
    quad: tuple[tuple[Fraction, Fraction], ...] | None = Field(None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def geometry(self):
        if self.quad:  # the box follows the quad, so the two can't disagree
            self.box = real_box(bounds(list(self.quad)))
        if self.box is None:
            raise ValueError("an item needs a box or a quad")
        return self


class ReaderReading(BaseModel):
    items: list[ReaderItem]
    total_cents: int | None = Field(None, ge=0, strict=True)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    extent: Box | None = None


class ReaderLocation(BaseModel):
    extent: Box | None = None


class HttpReader(Backend):
    """A reader running as its own service, any language or hardware: the photo goes to
    AXISPLIT_READER_URL as docs/reader-api.md describes, and the answer is checked before anything
    is saved. A failed request or an invalid answer fails the read."""
    name = "http"

    def read(self, image: Path, area: Quad | None = None) -> Reading:
        response = self._post("read", image, {"area": json.dumps(area)} if area else None)
        reading = ReaderReading.model_validate_json(response.content)
        items = [ExtractedItem(i.label, i.price_cents, *i.box, quad=list(i.quad) if i.quad else None)
                 for i in reading.items]
        return Reading(items, reading.total_cents, reading.currency, reading.extent)

    def locate(self, image: Path) -> Focus | None:
        return ReaderLocation.model_validate_json(self._post("locate", image, None).content).extent

    def _post(self, operation: str, image: Path, data: dict | None) -> httpx.Response:
        url = os.environ.get("AXISPLIT_READER_URL")
        if not url:
            raise ValueError("AXISPLIT_OCR_BACKEND=http needs AXISPLIT_READER_URL")
        key = os.environ.get("AXISPLIT_READER_KEY")
        timeout = float(os.environ.get("AXISPLIT_READER_TIMEOUT", "60"))
        kind = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
        with open(image, "rb") as f:
            response = httpx.post(f"{url.rstrip('/')}/v1/{operation}", files={"image": (image.name, f, kind)},
                                  data=data, headers={"Authorization": f"Bearer {key}"} if key else None,
                                  timeout=httpx.Timeout(timeout, connect=5))
        response.raise_for_status()
        return response


@cache
def paddle_backend() -> PaddleBackend:
    """One per process: it holds the loaded OCR models."""
    return PaddleBackend()


_backend: Backend | None = None


def set_backend(backend: Backend) -> None:
    global _backend
    _backend = backend


def get_backend() -> Backend:
    if _backend is not None:
        return _backend
    choice = os.environ.get("AXISPLIT_OCR_BACKEND", "paddle")
    if choice == "stub":
        return StubBackend()
    if choice == "vision":
        return LocalVisionBackend()
    if choice == "paddle":
        return paddle_backend()
    if choice == "http":
        return HttpReader()
    raise ValueError(f"AXISPLIT_OCR_BACKEND={choice!r}: use paddle, vision, http or stub")


def read(image: Path, area: Quad | None = None) -> Reading:
    return get_backend().read(image, area)


def locate(image: Path) -> Focus | None:
    return get_backend().locate(image)
