"""OCR text lines (quad + text) -> receipt items, discounts and total, with no model.

Rows: fragments are joined into printed rows along their own slope, so tilted and
curled receipts work. Each item's geometry is the min-area rectangle around its lines.

Tuned on half of a private set of 38 labelled receipts and frozen before the other
half was scored; see the bench PRs before changing a rule.
"""

import re
import unicodedata

import cv2
import numpy as np

DEC = r"-?\d{1,3}(?:[ .]\d{3})*[.,] ?\d{2}|-?\d+[.,] ?\d{2}"
CZK = r"(?:K[cčćČĆ]|CZK)"  # OCR writes Kč as Kc / Kć
AMOUNT = re.compile(rf"(?<![\d.,])(?:{DEC})(?![\d])|(?<![\d.,])-?\d{{1,3}}(?: \d{{3}})*(?=\s*{CZK}(?!\w))")
PAREN = re.compile(r"\([^)]*\)")
# A price printed mid-line ("1ks 21 % Premium 450,00 Kč"): it ends the row with a currency mark,
# and the row has no unit-price marker ("5 ks x 72,00 Kč/ks").
END_PRICE = re.compile(rf"(?:{DEC})\s*(?:{CZK}|€|EUR)\s*$")
MULTIPLY = re.compile(r"(?<!\w)(?:x|×|à|@)(?!\w)|\*|/\s*(?:ks|kg|l|pcs|st)\b", re.I)
QTY_HEAD = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*(?:x|×|ks|pcs|st|\.)?\s+", re.I)
QTY_LINE = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*(?:kg|g|l)?\s*(?:\*|×|x\b|ks\b|pcs\b|st\b)", re.I)  # "3 ks *", "0,853 kg * 2,59"
# Matched on accent-folded text. Several of these also label VAT or card-slip sums ("SPOLU", "TOTAL"):
# the receipt is split at every total and the section whose items add up to its total wins.
SUBTOTAL = re.compile(r"\b(sub\s*total\w*|subtotaal|medzisucet|mezisoucet|zwischensumme)\b", re.I)
TOTAL = re.compile(r"\b(total\w*|totaal\w*|celkom|celkem|suma|summe|k uhrade|na uhradu|spolu|zu zahlen|te betalen|a pagar)\b", re.I)
SAVINGS = re.compile(r"\b(uspora|usetril\w*|saved|savings|ersparnis|bespaard)\b", re.I)  # sums of discounts already listed


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def cents(token):
    t = token.replace(" ", "")  # "1 035", "2. 10"
    neg = t.startswith("-")
    t = t.lstrip("-")
    if re.search(r"[.,]\d{2}$", t):
        whole, frac = t[:-3], t[-2:]
        value = int(re.sub(r"[.,]", "", whole) or 0) * 100 + int(frac)
    else:
        value = int(re.sub(r"[.,]", "", t)) * 100
    return -value if neg else value


def amounts(text):
    """Money amounts outside parentheses, left to right."""
    return [cents(m.group()) for m in AMOUNT.finditer(PAREN.sub(" ", text))]


def letters(text):
    return sum(c.isalpha() for c in PAREN.sub(" ", AMOUNT.sub(" ", text)))


def clean_label(text):
    t = PAREN.sub(" ", text)
    t = AMOUNT.sub(" ", t)
    t = re.sub(rf"(?<!\w){CZK}(?!\w)|\bEUR\b|€", " ", t)
    t = re.sub(r"(?<!\w)\d+(?:[.,/]\d+)*\s*(?:kg|g|ml|l)(?!\w)|(?<!\w)kg(?!\w)", " ", t, flags=re.I)  # weights, volumes noted after names
    t = re.sub(r"/?\s*\d+(?:[.,]\d+)?\s*%", " ", t)                    # VAT rates "5%", "/ 20%"
    t = re.sub(r"(?<!\w)[à@](?!\w)", " ", t)                             # "à 2,70" unit-price marker
    t = re.sub(r"(?<!\w)\d+\s*(?:ks|x|×|pcs|st)(?!\w)", " ", t, flags=re.I)   # "1 ks", "18 x"
    t = re.sub(r"(?<!\w)(?:x|×|ks)(?!\w)", " ", t, flags=re.I)                  # "x 1.80"
    t = re.sub(r"[=*]+", " ", t)
    t = re.sub(r"\s+[A-D]\s*$", " ", t)                                # VAT class letter
    t = QTY_HEAD.sub("", t.strip())
    return re.sub(r"\s+", " ", t).strip(" .:-")


class Line:
    """quad: photo pixels (used for output). f: the same quad in the deskewed frame (used for decisions)."""
    def __init__(self, text, quad, angle=0.0, centre=(0.0, 0.0)):
        self.text = text
        self.quad = np.asarray(quad, float)
        c, s = np.cos(-angle), np.sin(-angle)
        self.f = (self.quad - centre) @ np.array([[c, s], [-s, c]]) + centre
        tl, tr, br, bl = self.f
        self.cx, self.cy = self.f[:, 0].mean(), self.f[:, 1].mean()
        self.h = (np.hypot(*(bl - tl)) + np.hypot(*(br - tr))) / 2
        self.w = (np.hypot(*(tr - tl)) + np.hypot(*(br - bl))) / 2
        dx = (tr[0] - tl[0] + br[0] - bl[0]) / 2
        self.slope = (tr[1] - tl[1] + br[1] - bl[1]) / 2 / dx if dx > 1 else 0.0


def global_angle(raw):
    """Median text angle of the long lines: the whole receipt's tilt in the photo."""
    angles = [np.arctan2(*(l.quad[1] - l.quad[0])[::-1]) for l in raw if l.w > 3 * l.h]
    return float(np.median(angles)) if angles else 0.0


def local_slope(lines, y, span):
    """Median slope of long lines near height y: short fragments ("1", "B") have noisy angles."""
    near = [l.slope for l in lines if abs(l.cy - y) < span and l.w > 3 * l.h]
    return float(np.median(near)) if near else 0.0


def rows_of(lines, width, height):
    x0 = width / 2
    for l in lines:
        l.v = l.cy - local_slope(lines, l.cy, 0.06 * height) * (l.cx - x0)
    rows = []
    for l in sorted(lines, key=lambda l: l.v):
        if rows and abs(l.v - rows[-1]["v"]) < 0.5 * min(l.h, rows[-1]["h"]):
            r = rows[-1]
            r["lines"].append(l)
            r["v"] = float(np.mean([x.v for x in r["lines"]]))
        else:
            rows.append({"v": l.v, "h": l.h, "lines": [l]})
    for r in rows:
        r["lines"].sort(key=lambda l: l.cx)
        r["text"] = " ".join(l.text for l in r["lines"])
    return rows


def quad_of(lines):
    pts = np.concatenate([l.quad for l in lines]).astype(np.float32)
    box = cv2.boxPoints(cv2.minAreaRect(pts))
    # order TL, TR, BR, BL
    s, d = box.sum(1), np.diff(box, axis=1).ravel()
    return [box[s.argmin()], box[d.argmin()], box[s.argmax()], box[d.argmax()]]


def has_name(text):
    return sum(c.isalpha() for c in clean_label(text)) >= 2


def price_column(rows):
    """Right edge of the line totals: where amount-only fragments end (80th percentile)."""
    edges = [l.f[:, 0].max() for r in rows for l in r["lines"] if amounts(l.text) and not has_name(l.text)]
    return float(np.percentile(edges, 80)) if edges else None


def row_total(row, column, tol):
    """The row's line total: the amount in its rightmost fragment that reaches the price column.
    Amounts further left are unit prices or volumes ("x 1.80", "0,7l")."""
    for l in reversed(row["lines"]):
        a = amounts(l.text)
        if a and (column is None or l.f[:, 0].max() >= column - tol):
            return a[-1]
    return None


def parse(lines, width, height):
    """lines: [(text, quad_pixels)] -> {"items": [...], "discounts": [...], "total_cents": int|None}."""
    raw = [Line(t, q) for t, q in lines if t.strip()]
    if not raw:
        return {"items": [], "discounts": [], "total_cents": None}
    # Decide rows and the price column in a frame turned level by the receipt's tilt; output stays in photo pixels.
    angle, centre = global_angle(raw), np.array([width / 2, height / 2])
    lines = [Line(l.text, l.quad, angle, centre) for l in raw]
    rows = rows_of(lines, width, height)
    column, tol = price_column(rows), 0.08 * width
    def section():
        return {"items": [], "discounts": [], "total": None}
    sections, sec, pending, after_sub = [], section(), [], False
    for k, r in enumerate(rows):
        text, folded = r["text"], fold(r["text"])
        amount, named = row_total(r, column, tol), has_name(r["text"])
        items, discounts = sec["items"], sec["discounts"]
        if SUBTOTAL.search(folded):
            after_sub, pending = True, []
            continue
        if SAVINGS.search(folded):
            continue
        if TOTAL.search(folded):
            value = amount if amount is not None else (amounts(text) or [None])[-1]
            if value is None:  # label and amount on separate rows: take the next bare amount
                nxt = [row_total(x, column, tol) for x in rows[k + 1:k + 3] if not has_name(x["text"])]
                value = next((v for v in nxt if v is not None), None)
            if value is None:
                continue
            sec["total"] = value
            sections.append(sec)
            sec, pending, after_sub = section(), [], False
            continue
        if after_sub:  # between subtotal and total: discounts, or the bare total when its label is cut off
            value = amount if amount is not None else (amounts(text) or [None])[-1]
            if value is not None and value < 0 and named:
                discounts.append({"label": clean_label(text), "price_cents": value, "lines": r["lines"]})
            elif value is not None and value > 0 and not named:
                sec["total"] = value  # unnamed negative amounts are the discounts' own sum
            continue
        if amount is None and named and END_PRICE.search(text) and not MULTIPLY.search(text):
            amount = amounts(text)[-1]
        if amount is None:
            # "5ks 21 % Pilsner" starts with a quantity but, with no amount, it is a name, not "3 ks * 0,79"
            if named and (not QTY_LINE.match(text) or not amounts(text)):
                pending.append(r)                      # a name waiting for its price, or a heading
            elif items:
                items[-1]["lines"] += r["lines"]       # "3 ks * 0,79", "(€ 41,50)", "(5 x)"
            continue
        if amount < 0:
            discounts.append({"label": clean_label(text), "price_cents": amount, "lines": r["lines"]})
            pending = []
            continue
        if named and pending and QTY_HEAD.match(pending[-1]["text"]) and not QTY_HEAD.match(text):
            name_rows = [pending[-1]]                  # "1 Homemade" + "Watermelon Tea  6,95"
        elif not named and pending:
            name_rows = [pending[-1]]                  # "ribez vino" + "18 x 1.80 =32.40"
        elif not named:
            continue                                   # an amount with no name to belong to
        else:
            name_rows = []
        label = " ".join(clean_label(x["text"]) for x in name_rows + [r]).strip()
        items.append({"label": label, "price_cents": amount, "rows": name_rows + [r],
                      "lines": [l for x in name_rows for l in x["lines"]] + r["lines"]})
        pending = []
        # A tilted heading can catch the price of the quantity line under it ("COUVERTS 124,50" over
        # "3 AYCE DO - ZO"): the next unpriced row that starts with a quantity is the real name.
    sections.append(sec)

    def adds_up(x):
        return x["total"] is not None and sum(i["price_cents"] for i in x["items"] + x["discounts"]) == x["total"]
    # The receipt is the section whose items add up to its total; else the one with most items.
    best = max(sections, key=lambda x: (adds_up(x), len(x["items"]), x["total"] is not None))
    items, discounts, total = best["items"], best["discounts"], best["total"]
    if total is None:  # items before the last total, e.g. the total row carries no item lines
        total = next((x["total"] for x in sections if x["total"] is not None), None)
    for i, it in enumerate(items):
        first = it["rows"][0]
        if QTY_HEAD.match(first["text"]):
            continue
        k = rows.index(it["rows"][-1]) + 1
        if k < len(rows) and row_total(rows[k], column, tol) is None and QTY_HEAD.match(rows[k]["text"]) \
                and has_name(rows[k]["text"]) and (i + 1 >= len(items) or rows[k] not in items[i + 1]["rows"]):
            it["label"] = clean_label(rows[k]["text"])
            it["lines"] += rows[k]["lines"]
    out = []
    for group in (items, discounts):
        res = []
        for it in group:
            q = quad_of(it["lines"])
            quad = [[round(float(x) / width, 4), round(float(y) / height, 4)] for x, y in q]
            xs, ys = [p[0] for p in quad], [p[1] for p in quad]
            res.append({"label": it["label"], "price_cents": it["price_cents"], "quad": quad,
                        "bbox": [max(0, min(xs)), max(0, min(ys)), min(1, max(xs)), min(1, max(ys))]})
        out.append(res)
    return {"items": out[0], "discounts": out[1], "total_cents": total}


def amount_rows(lines, width, height):
    """lines: [(text, quad_pixels)] -> printed rows top to bottom as (text, amount or None).
    The amount is the row's line total when it reaches the price column, else its last amount:
    the candidates a decision model chooses the receipt total from."""
    raw = [Line(t, q) for t, q in lines]
    angle, centre = global_angle(raw), np.array([width / 2, height / 2])
    rows = rows_of([Line(l.text, l.quad, angle, centre) for l in raw], width, height)
    column = price_column(rows)
    out = []
    for r in rows:
        value = row_total(r, column, 0.08 * width)
        if value is None and amounts(r["text"]):
            value = amounts(r["text"])[-1]
        out.append((r["text"], value))
    return out
