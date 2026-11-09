"""Receipt labels: their format, their checks, and the bench ground truth made from them.

A dataset is a directory: images/<id>.jpg, prefill/<id>.json (OCR lines and a first guess, from
`python -m bench.label.prefill`), labels/<id>.json (the labels) and cases/<id>/gt.json (ground
truth for the benchmark, written once a label is reviewed).

A label lists a receipt's priced lines as entries, top to bottom. Each entry points at the OCR
lines it was read from, so its box follows the printed text:

    {"kind": "item", "label": "AMUNDSEN VODKA", "price_cents": 5995,
     "quantity": 5, "unit_cents": 1199, "parent": null, "lines": [41, 42], "extra_quads": [], "flag": null}

- kind: item (something bought); modifier (an add-on or change printed for an item, often under
  it: "+ Cheese", "extra shot", "no onion"); deposit (a bottle or packaging deposit); discount (a
  negative line, for one item or the whole receipt); fee (service charge, tip, delivery: part of
  the total, but not something anyone ordered).
- price_cents: the line total as printed, negative for discounts. 0 for a free modifier.
- quantity, unit_cents: as printed, or null when the receipt doesn't print them. Weighed goods
  may have a fractional quantity (0,512 kg).
- parent: for a modifier, deposit or item discount, the index of the entry it belongs to.
- lines: OCR line numbers (prefill), every line of the entry: name, wrapped name, quantity line,
  price. extra_quads: boxes drawn by hand where OCR missed text.
- flag: a question for the reviewer. Once answered, it moves to flag_resolved, which nothing checks.

The total is {"label", "price_cents", "lines", "extra_quads", "flag"}. The receipt is described by
{"type", "qty_format", "unit_price_printed"}; status is todo, agent (labelled by an agent, not
reviewed), reviewed, or skip. Labels from the first tool ("done", items and discounts only) load as
reviewed.
"""

import json
import math
from collections import Counter
from pathlib import Path

KINDS = ("item", "modifier", "deposit", "discount", "fee")
BELONG = ("modifier", "deposit", "discount")  # kinds that can point at an item
RECEIPT_TYPES = ("restaurant", "bar", "cafe", "fast_food", "supermarket", "shop", "other")
QTY_FORMATS = ("none", "prefix", "suffix", "column", "line", "mixed")
STATUSES = ("todo", "agent", "reviewed", "skip")


def load(dataset: Path, rid: str):
    path = dataset / "labels" / f"{rid}.json"
    if not path.exists():
        return None
    label = json.loads(path.read_text())
    if label.get("status") == "done":
        label["status"] = "reviewed"
    return label


def save(dataset: Path, rid: str, label: dict):
    (dataset / "labels").mkdir(exist_ok=True)
    (dataset / "labels" / f"{rid}.json").write_text(json.dumps({**label, "id": rid}, ensure_ascii=False, indent=1))


def prefill(dataset: Path, rid: str):
    return json.loads((dataset / "prefill" / f"{rid}.json").read_text())


def check(label: dict, pre: dict) -> list[str]:
    """What stops this label from being ground truth. Empty when it's consistent."""
    problems = []
    entries, total = label.get("items", []), label.get("total") or {}
    n_lines = len(pre["lines"])
    owner = {}
    for i, e in enumerate(entries, 1):
        what = f"Entry {i}"
        if e.get("kind") not in KINDS:
            problems.append(f"{what}: kind must be one of {', '.join(KINDS)}.")
        if not (e.get("label") or "").strip():
            problems.append(f"{what}: no name.")
        price = e.get("price_cents")
        if not isinstance(price, int):
            problems.append(f"{what}: no price.")
        elif e.get("kind") == "discount" and price > 0:
            problems.append(f"{what}: a discount is negative.")
        elif e.get("kind") != "discount" and price < 0:
            problems.append(f"{what}: negative, so a discount?")
        for line in e.get("lines", []):
            if not isinstance(line, int) or not 0 <= line < n_lines:
                problems.append(f"{what}: line {line} doesn't exist.")
            elif line in owner:
                problems.append(f"{what}: line {line} is also in entry {owner[line]}.")
            else:
                owner[line] = i
        if not e.get("lines") and not e.get("extra_quads"):
            problems.append(f"{what}: no lines on the photo.")
        qty, unit = e.get("quantity"), e.get("unit_cents")
        if qty is not None and not (isinstance(qty, (int, float)) and qty > 0):
            problems.append(f"{what}: quantity must be a positive number.")
        if unit is not None and not isinstance(unit, int):
            problems.append(f"{what}: unit price must be whole cents.")
        if unit is not None and qty is None:
            problems.append(f"{what}: a unit price needs its quantity.")
        # A unit price is printed rounded to the cent: up to half a cent per unit (a fuel pump's 42,90 is 42,902).
        if isinstance(qty, (int, float)) and qty > 0 and isinstance(unit, int) and isinstance(price, int) \
                and abs(round(qty * unit) - abs(price)) > max(1, math.ceil(qty / 2)):
            problems.append(f"{what}: {fmt_qty(qty)} × {cents(unit)} = {cents(round(qty * unit))}, not {cents(abs(price))}.")
        parent = e.get("parent")
        if parent is not None:
            if e.get("kind") not in BELONG:
                problems.append(f"{what}: only a modifier, deposit or discount belongs to another entry.")
            elif not (isinstance(parent, int) and 0 <= parent < len(entries)) or parent == i - 1:
                problems.append(f"{what}: belongs to an entry that doesn't exist.")
            elif entries[parent].get("kind") != "item":
                problems.append(f"{what}: belongs to entry {parent + 1}, which isn't an item.")
        elif e.get("kind") in ("modifier", "deposit"):
            problems.append(f"{what}: which item is this {e['kind']} for?")
        if e.get("flag"):
            problems.append(f"{what}: flagged: {e['flag']}")
    if not isinstance(total.get("price_cents"), int):
        if not (label.get("note") or "").strip():
            problems.append("Total: no amount. If the photo doesn't show one, say so in the note.")
    elif not total.get("lines") and not total.get("extra_quads"):
        problems.append("Total: no line on the photo.")
    else:
        for line in total.get("lines", []):
            if line in owner:
                problems.append(f"Total: line {line} is also in entry {owner[line]}.")
        added = sum(e["price_cents"] for e in entries if isinstance(e.get("price_cents"), int))
        if added != total["price_cents"] and not (label.get("note") or "").strip():
            problems.append(f"Lines add up to {cents(added)}, the total is {cents(total['price_cents'])}: "
                            f"fix it, or say why in the note.")
    if total.get("flag"):
        problems.append(f"Total: flagged: {total['flag']}")
    receipt = label.get("receipt") or {}
    if receipt.get("type") not in RECEIPT_TYPES:
        problems.append(f"Receipt type: one of {', '.join(RECEIPT_TYPES)}.")
    if receipt.get("qty_format") not in QTY_FORMATS:
        problems.append(f"Quantity format: one of {', '.join(QTY_FORMATS)}.")
    if not isinstance(receipt.get("unit_price_printed"), bool):
        problems.append("Unit price printed: true or false.")
    return problems


def changes(before: dict, after: dict) -> list[str]:
    """What relabelling changed in an earlier label: its lines' names and prices, and the total."""
    def money(v):
        return cents(v) if isinstance(v, int) else "none"

    def key(e):
        return (e.get("label") or "", e.get("price_cents"))
    was, now = Counter(map(key, before.get("items", []))), Counter(map(key, after.get("items", [])))
    out = [f"was {name!r} {money(p)}" for (name, p), n in sorted((was - now).items(), key=str) for _ in range(n)]
    out += [f"now {name!r} {money(p)}" for (name, p), n in sorted((now - was).items(), key=str) for _ in range(n)]
    t0, t1 = (before.get("total") or {}).get("price_cents"), (after.get("total") or {}).get("price_cents")
    if t0 != t1:
        out.append(f"total was {money(t0)}, now {money(t1)}")
    return out


def cents(v: int) -> str:
    return f"{'-' if v < 0 else ''}{abs(v) // 100},{abs(v) % 100:02d}"


def fmt_qty(q) -> str:
    return str(q).replace(".", ",") if isinstance(q, float) else str(q)


def to_gt(label: dict, pre: dict, image: str) -> dict:
    """Bench ground truth: items (everything but discounts) and discounts, each with a box and a
    quad around its lines, plus the new fields where set. Parents index the gt items."""
    lines, (W, H) = pre["lines"], pre["size"]
    entries = label["items"]
    gt_index, n = {}, 0
    for i, e in enumerate(entries):
        if e["kind"] != "discount":
            gt_index[i], n = n, n + 1
    gt = {"image": image, "currency": label.get("currency") or "EUR", "items": [], "discounts": []}
    for i, e in enumerate(entries):
        bbox, quad = geometry(e, lines, W, H)
        out = {"label": e["label"], "price_cents": e["price_cents"], "bbox": bbox, "quad": quad}
        if e["kind"] not in ("item", "discount"):
            out["kind"] = e["kind"]
        for key in ("quantity", "unit_cents"):
            if e.get(key) is not None:
                out[key] = e[key]
        if e.get("parent") is not None:
            out["parent"] = gt_index[e["parent"]]
        gt["discounts" if e["kind"] == "discount" else "items"].append(out)
    t = label["total"]
    if isinstance(t.get("price_cents"), int):  # else not in the photo (the note says why)
        bbox, quad = geometry(t, lines, W, H)
        gt["total"] = {"label": t.get("label", ""), "price_cents": t["price_cents"], "bbox": bbox, "quad": quad}
        if label.get("note"):
            gt["total"]["note"] = label["note"]
    if not gt["discounts"]:
        del gt["discounts"]
    if label.get("receipt"):
        gt["receipt"] = label["receipt"]
    return gt


def geometry(entry, lines, W, H):
    pts = [(x * W, y * H) for i in entry.get("lines", []) for x, y in lines[i]["quad"]]
    pts += [(x * W, y * H) for q in entry.get("extra_quads", []) for x, y in q]
    quad = [[round(x / W, 5), round(y / H, 5)] for x, y in min_area_quad(pts)]
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    return [round(max(0, min(xs)), 5), round(max(0, min(ys)), 5), round(min(1, max(xs)), 5), round(min(1, max(ys)), 5)], quad


def hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_area_quad(points):
    """Smallest rotated rectangle around pixel points, as [TL, TR, BR, BL]."""
    h = hull(points)
    if len(h) < 3:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
    best = None
    for i in range(len(h)):
        (x1, y1), (x2, y2) = h[i], h[(i + 1) % len(h)]
        a = math.atan2(y2 - y1, x2 - x1)
        c, s = math.cos(a), math.sin(a)
        us = [px * c + py * s for px, py in h]
        vs = [-px * s + py * c for px, py in h]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or area < best[0]:
            best = (area, c, s, min(us), max(us), min(vs), max(vs))
    _, c, s, u0, u1, v0, v1 = best
    corners = [(u * c - v * s, u * s + v * c) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    by_sum = sorted(corners, key=lambda p: p[0] + p[1])
    tl, br = by_sum[0], by_sum[-1]
    rest = [p for p in corners if p not in (tl, br)]
    tr, bl = (rest[0], rest[1]) if rest[0][0] - rest[0][1] > rest[1][0] - rest[1][1] else (rest[1], rest[0])
    return [tl, tr, br, bl]
