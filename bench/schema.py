"""Ground-truth + prediction schema helpers."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GTItem:
    label: str
    price_cents: int
    bbox: tuple[float, float, float, float]  # x1,y1,x2,y2 normalized


def load_gt(case_dir: Path) -> list[GTItem]:
    data = json.loads((case_dir / "gt.json").read_text())
    return [GTItem(i["label"], int(i["price_cents"]), tuple(i["bbox"])) for i in data["items"]]


def load_preds(pred_file: Path) -> list[GTItem]:
    """Predictions share the GT shape (label, price_cents, bbox)."""
    data = json.loads(pred_file.read_text())
    return [GTItem(i["label"], int(i["price_cents"]), tuple(i["bbox"])) for i in data["items"]]
