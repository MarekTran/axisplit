"""Receipt OCR: items and boxes against the EXIF-normalized image.

Default backend: PaddleOCR (PP-OCRv6, CPU) reads the text lines, rules turn them into
items, and a decision model picks which line is the total. The vision backend asks a
local vision model instead (Qwen grounding coordinates are 0..1000).
Database boxes are 0..1. No fabricated boxes: malformed geometry fails extraction explicitly.
"""

import base64
import io
import logging
import os
import threading
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import httpx
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field, model_validator

from . import receipt_rules
from .config import new_id
from .models import Item

logger = logging.getLogger(__name__)


@dataclass
class ExtractedItem:
    label: str
    price_cents: int
    x: float
    y: float
    w: float
    h: float
    confidence: float | None = None

    def to_item(self, receipt_id: str) -> Item:
        return Item(
            id=new_id(), receipt_id=receipt_id, label=self.label,
            price_cents=self.price_cents, x=self.x, y=self.y, w=self.w, h=self.h,
            ocr_confidence=self.confidence,
        )


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


Focus = tuple[float, float, float, float]  # x, y, w, h of the user's items area, normalized to the photo
Quad = list[tuple[float, float]]  # its corners, when dragged off a rectangle to fit a tilted receipt


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
    name = "base"

    def extract_items(self, image: Path, focus: Focus | None = None, quad: Quad | None = None) -> list[ExtractedItem]:
        """Items inside the focus area: the crop is `focus`, and with `quad` only what is inside it."""
        raise NotImplementedError

    def extract_total(self, image: Path) -> int | None:
        raise NotImplementedError

    def text_extent(self, image: Path) -> Focus | None:
        """Where the photo's text is (x, y, w, h), roughly the receipt; None if unknown."""
        return None


class StubBackend(Backend):
    """Explicit opt-in for isolated UI tests only, never the app default."""
    name = "stub"

    def extract_items(self, image: Path, focus: Focus | None = None, quad: Quad | None = None) -> list[ExtractedItem]:
        return []

    def extract_total(self, image: Path) -> int | None:
        return None


class LocalVisionBackend(Backend):
    name = "local-vision"

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
        self._reads: dict = {}  # without a focus area, an upload's items and total share one read

    def extract_items(self, image: Path, focus: Focus | None = None, quad: Quad | None = None) -> list[ExtractedItem]:
        lines, width, height = self._read(image, focus)
        if quad:  # Lines centred outside it, such as a total below a tilted last item, are left out.
            lines = [(text, box) for text, box in lines
                     if inside(quad, sum(x for x, _ in box) / 4 / width, sum(y for _, y in box) / 4 / height)]
        return [ExtractedItem(
            label=i["label"][:120], price_cents=i["price_cents"],
            x=i["bbox"][0], y=i["bbox"][1], w=i["bbox"][2] - i["bbox"][0], h=i["bbox"][3] - i["bbox"][1],
        ) for i in receipt_rules.parse(lines, width, height)["items"]]

    def extract_total(self, image: Path) -> int | None:
        lines, width, height = self._read(image, None)  # whole photo: the total is outside the focus
        rows = receipt_rules.amount_rows(lines, width, height)
        candidates = [i for i, (_, value) in enumerate(rows) if value is not None and value > 0]
        if not candidates:
            return None
        try:
            return rows[self._pick_total(rows, candidates[-len(LETTERS):])][1]
        except httpx.HTTPError as e:
            logger.warning("Decision model unavailable (%s); taking the total from the rules", e)
            return receipt_rules.parse(lines, width, height)["total_cents"]

    def text_extent(self, image: Path) -> Focus | None:
        lines, width, height = self._read(image, None)  # cached from the total's read
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
        response = httpx.post(f"{url.rstrip('/')}/systemone", timeout=httpx.Timeout(60, connect=5), json={
            "state": {"description": DECIDE_STATE, "receipt_lines": text},
            "questions": {"total": {"type": "choice", "instructions": DECIDE_TOTAL,
                                    "criteria": {letter[i]: rows[i][0][:80] for i in candidates}}},
        })
        response.raise_for_status()
        choice = response.json()["answers"]["total"]["choice"]
        return next(i for i, code in letter.items() if code == choice)

    def _read(self, image: Path, focus: Focus | None) -> tuple[list, int, int]:
        """Text lines as (text, quad in whole-photo pixels) inside the focus area, and the photo size."""
        key = (image, image.stat().st_mtime_ns, focus)
        with self._lock:
            if key not in self._reads:
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
                if len(self._reads) >= 4:
                    self._reads.pop(next(iter(self._reads)))
                self._reads[key] = (lines, width, height)
            return self._reads[key]

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
    raise ValueError(f"AXISPLIT_OCR_BACKEND={choice!r}: use paddle, vision or stub")


def extract_items(image: Path, focus: Focus | None = None, quad: Quad | None = None) -> list[ExtractedItem]:
    return get_backend().extract_items(image, focus, quad)


def extract_total(image: Path) -> int | None:
    return get_backend().extract_total(image)


def text_extent(image: Path) -> Focus | None:
    return get_backend().text_extent(image)
