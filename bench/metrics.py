"""Matching + metrics: greedy best-IoU assignment with label gate."""

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .schema import GTItem


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix, iy = max(ax1, bx1), max(ay1, by1)
    xx, xy = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, xx - ix), max(0.0, xy - iy)
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def label_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


@dataclass
class CaseResult:
    n_gt: int
    n_pred: int
    matched: int = 0
    price_exact: int = 0
    price_close: int = 0
    iou_sum: float = 0.0
    hallucinations: int = field(default=0)


def score_case(gt: list[GTItem], pred: list[GTItem], iou_gate=0.3, label_gate=0.6) -> CaseResult:
    res = CaseResult(len(gt), len(pred))
    used_p: set[int] = set()
    for g in gt:
        best, best_score = None, 0.0
        for j, p in enumerate(pred):
            if j in used_p:
                continue
            s = iou(g.bbox, p.bbox)
            if s >= iou_gate and label_sim(g.label, p.label) >= label_gate and s > best_score:
                best, best_score = j, s
        if best is None:
            continue
        used_p.add(best)
        p = pred[best]
        res.matched += 1
        res.iou_sum += best_score
        if p.price_cents == g.price_cents:
            res.price_exact += 1
        elif abs(p.price_cents - g.price_cents) <= max(1, round(g.price_cents * 0.01)):
            res.price_close += 1
    res.hallucinations = len(pred) - len(used_p)
    return res


def aggregate(results: list[CaseResult]) -> dict:
    n_gt = sum(r.n_gt for r in results)
    n_pred = sum(r.n_pred for r in results)
    matched = sum(r.matched for r in results)
    return {
        "cases": len(results),
        "item_recall": matched / n_gt if n_gt else 0.0,
        "item_precision": (matched / n_pred) if n_pred else 0.0,
        "price_exact_of_matched": (sum(r.price_exact for r in results) / matched) if matched else 0.0,
        "price_within_1pct_of_matched": (sum(r.price_exact + r.price_close for r in results) / matched) if matched else 0.0,
        "mean_iou_matched": (sum(r.iou_sum for r in results) / matched) if matched else 0.0,
        "hallucinated_items": sum(r.hallucinations for r in results),
    }
