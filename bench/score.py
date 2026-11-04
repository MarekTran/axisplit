import json
import sys
from pathlib import Path

from .metrics import aggregate, score_case
from .schema import load_gt, load_preds


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True, type=Path, help="bench/cases dir")
    ap.add_argument("--preds", required=True, type=Path, help="dir of <case-id>.json")
    args = ap.parse_args()

    results = []
    for case in sorted(p for p in args.gt.iterdir() if (p / "gt.json").exists()):
        pred = args.preds / f"{case.name}.json"
        if not pred.exists():
            print(f"{case.name}: no prediction file, skipping", file=sys.stderr)
            continue
        r = score_case(load_gt(case), load_preds(pred))
        results.append(r)
        print(f"{case.name}: {r.matched}/{r.n_gt} items, price_exact={r.price_exact}, "
              f"halluc={r.hallucinations}")
    if not results:
        print("nothing to score", file=sys.stderr)
        return 1
    print(json.dumps(aggregate(results), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
