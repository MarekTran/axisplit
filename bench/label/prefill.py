"""OCR lines and a first guess to label from.

    PYTHONPATH=src:. python -m bench.label.prefill DATASET [ID ...]

For each DATASET/images/<id>.jpg, writes prefill/<id>.json: the photo's size, its OCR lines (text and
quad, 0..1 of the photo) and the entries the app's rules find, with a quantity and unit price where
a line prints them. Also writes overlay/<id>.jpg, the photo with each line's number. An existing
prefill is kept (pass IDs to redo them); overlays are always redrawn.
"""

import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

QTY_UNIT = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(?:ks|x|×|pcs|st|kg|l)?\s*[*x×@à]\s*(-?\d+[.,]\d{2})(?!\d)", re.I)
QTY_HEAD = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(?:x|×|ks|pcs|st)\b|^\s*(\d+)\s+(?=\D)", re.I)
QTY_TAIL = re.compile(r"(?<!\w)(?:x|×)\s*(\d+)\s*$", re.I)


def number(s):
    v = float(s.replace(",", "."))
    return int(v) if v == int(v) else v


def quantity(texts):
    """(quantity, unit cents) as printed in an entry's lines, else (None, None)."""
    for t in texts:
        m = QTY_UNIT.search(t)
        if m:
            q, u = number(m.group(1)), m.group(2)
            return q, round(float(u.replace(",", ".")) * 100)
    for t in texts:
        m = QTY_HEAD.match(t) or QTY_TAIL.search(t)
        if m:
            return number(next(g for g in m.groups() if g)), None
    return None, None


def centre(quad):
    return sum(x for x, _ in quad) / 4, sum(y for _, y in quad) / 4


def inside(pt, quad):
    """Point in a convex quad (any winding)."""
    x, y = pt
    signs = []
    for (x1, y1), (x2, y2) in zip(quad, quad[1:] + quad[:1]):
        signs.append((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1) > 0)
    return all(signs) or not any(signs)


def prefill(path: Path) -> dict:
    from axisplit import receipt_rules
    from axisplit.ocr import PaddleBackend

    with Image.open(path) as im:
        size = ImageOps.exif_transpose(im).size
    raw, w, h = PaddleBackend()._read(path, None)
    assert (w, h) == size, (path, (w, h), size)
    lines = [{"text": t, "quad": [[round(float(x) / w, 5), round(float(y) / h, 5)] for x, y in q]} for t, q in raw if t.strip()]
    parsed = receipt_rules.parse([(l["text"], [(x * w, y * h) for x, y in l["quad"]]) for l in lines], w, h)

    def lines_in(quad):
        return [i for i, l in enumerate(lines) if inside(centre(l["quad"]), quad)]

    entries, taken = [], set()
    for kind, group in (("item", parsed["items"]), ("discount", parsed["discounts"])):
        for it in group:
            own = [i for i in lines_in(it["quad"]) if i not in taken]
            taken.update(own)
            q, u = quantity([lines[i]["text"] for i in own])
            entries.append({"kind": kind, "label": it["label"], "price_cents": it["price_cents"], "quantity": q,
                            "unit_cents": u, "parent": None, "lines": own, "extra_quads": [], "flag": None,
                            "_y": min(y for _, y in it["quad"])})
    entries.sort(key=lambda e: e.pop("_y"))
    total = {"label": "", "price_cents": parsed["total_cents"], "lines": [], "extra_quads": [], "flag": None}
    if parsed["total_cents"] is not None:  # the lowest line printing the total, with a total word on its row
        hits = [i for i, l in enumerate(lines) if i not in taken and parsed["total_cents"] in receipt_rules.amounts(l["text"])]
        if hits:
            k = hits[0]
            cy = centre(lines[k]["quad"])[1]
            row = [i for i, l in enumerate(lines) if abs(centre(l["quad"])[1] - cy) < 0.012 and i not in taken]
            words = [i for i in row if receipt_rules.TOTAL.search(receipt_rules.fold(lines[i]["text"]))]
            total["lines"] = sorted({k, *words})
            total["label"] = receipt_rules.clean_label(lines[words[0]]["text"]) if words else ""
    return {"id": path.stem, "size": list(size), "lines": lines, "items": entries, "total": total}


def overlay(dataset: Path, rid: str, pre: dict):
    """The photo with each OCR line outlined and numbered, for labelling by eye."""
    with Image.open(dataset / "images" / f"{rid}.jpg") as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
    W, H = im.size
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=max(14, W // 70))
    for i, l in enumerate(pre["lines"]):
        pts = [(x * W, y * H) for x, y in l["quad"]]
        d.polygon(pts, outline=(255, 40, 40), width=2)
        x, y = pts[0]
        tag = str(i)
        box = d.textbbox((0, 0), tag, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        tx, ty = max(0, x - tw - 8), max(0, y - 2)
        d.rectangle([tx - 2, ty - 1, tx + tw + 3, ty + th + 5], fill=(255, 235, 59))
        d.text((tx, ty), tag, fill=(0, 0, 0), font=font)
    (dataset / "overlay").mkdir(exist_ok=True)
    im.save(dataset / "overlay" / f"{rid}.jpg", quality=88)


def main():
    dataset = Path(sys.argv[1]).resolve()
    ids = sys.argv[2:] or sorted(p.stem for p in (dataset / "images").glob("*.jpg"))
    (dataset / "prefill").mkdir(exist_ok=True)
    for rid in ids:
        out = dataset / "prefill" / f"{rid}.json"
        if not out.exists() or sys.argv[2:]:
            out.write_text(json.dumps(prefill(dataset / "images" / f"{rid}.jpg"), ensure_ascii=False, indent=1))
        pre = json.loads(out.read_text())
        overlay(dataset, rid, pre)
        n_qty = sum(1 for e in pre["items"] if e.get("quantity") is not None)
        print(f"{rid}: {len(pre['lines'])} lines, {len(pre['items'])} entries guessed ({n_qty} with a quantity), "
              f"total {pre['total']['price_cents']}")


if __name__ == "__main__":
    main()
