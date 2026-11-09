"""A receipt's OCR lines and its label's problems, for labelling from a terminal.

    python -m bench.label.show DATASET ID                 # lines, top to bottom, and the label's check
    python -m bench.label.show DATASET ID --crop 40 60    # overlay/<id>-crop.jpg: that band of the
                                                          # photo (% from the top), enlarged, lines numbered
"""

import sys
from pathlib import Path

from PIL import Image

from . import labels


def main():
    dataset, rid = Path(sys.argv[1]).resolve(), sys.argv[2]
    pre = labels.prefill(dataset, rid)
    if "--crop" in sys.argv:
        k = sys.argv.index("--crop")
        top, bottom = float(sys.argv[k + 1]) / 100, float(sys.argv[k + 2]) / 100
        with Image.open(dataset / "overlay" / f"{rid}.jpg") as im:
            W, H = im.size
            band = im.crop((0, int(top * H), W, int(bottom * H)))
            scale = min(2.0, 1800 / band.width)
            band = band.resize((int(band.width * scale), int(band.height * scale)), Image.LANCZOS)
            out = dataset / "overlay" / f"{rid}-crop.jpg"
            band.save(out, quality=90)
        print(f"wrote {out}")
        return
    print(f"{rid}: {len(pre['lines'])} OCR lines (y and x: % of the photo, from the top left)")
    order = sorted(range(len(pre["lines"])), key=lambda i: (round(pre["lines"][i]["quad"][0][1] * 200), pre["lines"][i]["quad"][0][0]))
    for i in order:
        (x, y) = pre["lines"][i]["quad"][0]
        print(f"  [{i:>3}]  y {y * 100:5.1f}  x {x * 100:5.1f}  {pre['lines'][i]['text']}")
    label = labels.load(dataset, rid)
    if label is None:
        print("\nNo label yet. The rules' first guess (prefill items):")
        for n, e in enumerate(pre["items"]):
            print(f"  {n}: {e['kind']:8} {e['label']!r} {e['price_cents']} qty={e.get('quantity')} unit={e.get('unit_cents')} lines={e['lines']}")
        print(f"  total: {pre['total']}")
        return
    print(f"\nLabel ({label.get('status')}): {len(label['items'])} entries")
    problems = labels.check(label, pre)
    print("\n".join(f"  ! {p}" for p in problems) if problems else "  ✓ no problems")


if __name__ == "__main__":
    main()
