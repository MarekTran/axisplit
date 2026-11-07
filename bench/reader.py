"""Score a receipt reader through the app's own call, ocr.read(), as an upload makes it.

The reader is the one AXISPLIT_OCR_BACKEND selects, so a reader running as its own service is
scored exactly as the app would use it. Per receipt it reads the whole photo, then the items area a
user would drag (the GT items' extent plus a margin); the total and currency are the latter's, as
on an upload. Predictions go where `bench.vlm score` reads them:

  python -m bench.reader --name paddle --cases test_photos/new_batch/cases --preds test_photos/new_batch/preds
  AXISPLIT_OCR_BACKEND=http AXISPLIT_READER_URL=http://127.0.0.1:9000 \\
      python -m bench.reader --name my-reader --cases test_photos/new_batch/cases --preds test_photos/new_batch/preds
  python -m bench.vlm score --cases test_photos/new_batch/cases --preds test_photos/new_batch/preds \\
      --split test_photos/new_batch/split.json:heldout
"""

import argparse
import json
from pathlib import Path

from axisplit import ocr

from . import vlm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="predictions subdirectory, e.g. the reader's name")
    ap.add_argument("--cases", type=Path, help="cases dir (default bench/cases)")
    ap.add_argument("--preds", type=Path, help="predictions dir (default bench/preds/vlm)")
    args = ap.parse_args()
    vlm.CASES, vlm.PREDS = args.cases or vlm.CASES, args.preds or vlm.PREDS
    out = vlm.PREDS / args.name
    out.mkdir(parents=True, exist_ok=True)
    reader = ocr.get_backend()
    for case in vlm.cases():
        gt = json.loads((case / "gt.json").read_text())
        image = (case / gt["image"]).resolve()
        result = {}
        for mode, area in (("whole", None), ("focus", ocr.corners(vlm.focus_from_gt(gt)))):
            reading, seconds, error = vlm.timed(reader.read, image, area)
            result[mode] = {"seconds": seconds, "error": error, "items": [
                {"label": i.label, "price_cents": i.price_cents,
                 "bbox": [round(i.x, 4), round(i.y, 4), round(i.x + i.w, 4), round(i.y + i.h, 4)],
                 **({"quad": [[round(x, 4), round(y, 4)] for x, y in i.quad]} if i.quad else {})}
                for i in (reading.items if reading else [])]}
        result["total"] = {"seconds": result["focus"]["seconds"], "error": result["focus"]["error"],
                           "total_cents": reading.total_cents if reading else None}
        result["currency"] = reading.currency if reading else None
        (out / f"{case.name}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
        print(f"{args.name} {case.name}: {len(result['focus']['items'])} items, total "
              f"{result['total']['total_cents']}, {result['currency']} ({result['focus']['seconds']}s)", flush=True)


if __name__ == "__main__":
    main()
