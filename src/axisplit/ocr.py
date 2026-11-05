"""Local vision OCR: items and boxes against the EXIF-normalized image.

Qwen grounding coordinates are 0..1000; database boxes are 0..1.
No fabricated boxes: malformed geometry fails extraction explicitly.
"""

import base64
import io
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image
from pydantic import BaseModel, Field, model_validator

from .config import new_id
from .models import Item


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


class Backend:
    name = "base"

    def extract_items(self, image: Path) -> list[ExtractedItem]:
        raise NotImplementedError


class StubBackend(Backend):
    """Explicit opt-in for isolated UI tests only, never the app default."""
    name = "stub"

    def extract_items(self, image: Path) -> list[ExtractedItem]:
        return []


class LocalVisionBackend(Backend):
    name = "local-vision"

    def extract_items(self, image: Path) -> list[ExtractedItem]:
        # Uniform resize, no crop: normalized boxes retain the original frame.
        with Image.open(image) as im:
            im = im.convert("RGB")
            im.thumbnail((1600, 1600))
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
                    "json_schema": {"name": "receipt", "schema": Detection.model_json_schema()},
                },
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": PROMPT},
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
        result = Detection.model_validate_json(completion["message"]["content"])
        return [ExtractedItem(
            label=i.label, price_cents=i.price_cents,
            x=i.bbox[0] / 1000, y=i.bbox[1] / 1000,
            w=(i.bbox[2] - i.bbox[0]) / 1000,
            h=(i.bbox[3] - i.bbox[1]) / 1000,
        ) for i in result.items]


_backend: Backend | None = None


def set_backend(backend: Backend) -> None:
    global _backend
    _backend = backend


def get_backend() -> Backend:
    if _backend is not None:
        return _backend
    if os.environ.get("AXISPLIT_OCR_BACKEND") == "stub":
        return StubBackend()
    return LocalVisionBackend()


def extract_items(image: Path) -> list[ExtractedItem]:
    return get_backend().extract_items(image)
