"""OCR pipeline contract.

Every backend implements extract_items() -> list[ExtractedItem].
The app treats bboxes as advisory: users can always re-place tokens and edit
labels/prices, so item RECALL matters more than pixel-perfect IoU.
"""

from dataclasses import dataclass
from pathlib import Path

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
            id=new_id(),
            receipt_id=receipt_id,
            label=self.label,
            price_cents=self.price_cents,
            x=self.x,
            y=self.y,
            w=self.w,
            h=self.h,
            ocr_confidence=self.confidence,
        )


class Backend:
    name = "base"

    def extract_items(self, image: Path) -> list[ExtractedItem]:  # pragma: no cover
        raise NotImplementedError


class StubBackend(Backend):
    """Until the benchmark picks a model, OCR returns nothing and the
    receipt is filled manually via the 'add item' form. Keeps the whole app
    usable end-to-end today."""

    name = "stub"

    def extract_items(self, image: Path) -> list[ExtractedItem]:
        return []


_backend: Backend = StubBackend()


def set_backend(backend: Backend) -> None:
    global _backend
    _backend = backend


def get_backend() -> Backend:
    return _backend


def extract_items(image: Path) -> list[ExtractedItem]:
    return _backend.extract_items(image)
