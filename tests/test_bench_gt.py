"""The labelled bench cases stay self-consistent: valid boxes, prices that add up to the printed total."""

import json
from pathlib import Path

import pytest

CASES = sorted(p for p in (Path(__file__).resolve().parents[1] / "bench" / "cases").iterdir()
               if (p / "gt.json").exists() and "total" in json.loads((p / "gt.json").read_text()))


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_case_is_consistent(case):
    gt = json.loads((case / "gt.json").read_text())
    assert (case / gt["image"]).is_file()
    entries = gt["items"] + gt.get("discounts", []) + [gt["total"]]
    for e in entries:
        x1, y1, x2, y2 = e["bbox"]
        assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1, e
        (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = e["quad"]  # TL, TR, BR, BL
        assert all(0 <= c <= 1 for point in e["quad"] for c in point), e
        assert tlx < trx and blx < brx and tly < bly and try_ < bry, e  # ordered, not flipped
    assert all(e["price_cents"] >= 0 for e in gt["items"])
    assert all(e["price_cents"] < 0 for e in gt.get("discounts", []))
    assert sum(e["price_cents"] for e in gt["items"] + gt.get("discounts", [])) == gt["total"]["price_cents"]


def test_every_test_photo_is_labelled():
    photos = {p.stem for p in (Path(__file__).resolve().parents[1] / "test_photos").glob("*.jpg")}
    assert photos == {c.name for c in CASES}
