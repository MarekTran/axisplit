"""Readable, derived UI state. Amounts are consumption shares, not payment debts."""

from .models import Item, Person, Receipt
from .split import agreed, totals

STATUS_LABELS = {
    "awaiting_items": "Needs detection",
    "awaiting_tokens": "Ready to split",
    "splitting": "Assigning items",
    "pending_confirm": "All assigned",
    "settled": "Everyone agreed",
}


def receipt_summary(items: list[Item], people: list[Person]) -> dict:
    shares = totals(items)
    unassigned = [i for i in items if not i.tokens]
    ticked = agreed(people, shares)
    had: dict[str, list[str]] = {}  # what to read out when asking someone: their items, receipt order
    for item in items:
        for token in item.tokens:
            had.setdefault(token.username, []).append(item.label + (" (shared)" if len(item.tokens) > 1 else ""))
    return {
        "total": sum(i.price_cents for i in items),
        "assigned": sum(shares.values()),
        "unassigned_total": sum(i.price_cents for i in unassigned),
        "unassigned_count": len(unassigned),
        "assigned_count": len(items) - len(unassigned),
        "item_count": len(items),
        "totals": shares,
        "had": had,
        "agreed": ticked,
        "waiting": sorted(set(shares) - ticked),
        "complete": bool(items) and not unassigned,
    }


def photo_view(receipt: Receipt, items: list[Item], size: tuple[int, int], margin: float = 0.04) -> dict | None:
    """The part of the photo the board shows: the receipt's text, its items and the focus area,
    plus a margin, instead of the table around it. None shows the whole photo (nothing known yet).
    Returned as CSS: the photo layer's width and offset in % of the visible part, and its aspect."""
    boxes = [(i.x, i.y, i.x + i.w, i.y + i.h) for i in items]
    for x, y, w, h in ((receipt.view_x, receipt.view_y, receipt.view_w, receipt.view_h),
                       (receipt.focus_x, receipt.focus_y, receipt.focus_w, receipt.focus_h)):
        if None not in (x, y, w, h):
            boxes.append((x, y, x + w, y + h))
    if not boxes:
        return None
    x1, y1 = max(0.0, min(b[0] for b in boxes) - margin), max(0.0, min(b[1] for b in boxes) - margin)
    x2, y2 = min(1.0, max(b[2] for b in boxes) + margin), min(1.0, max(b[3] for b in boxes) + margin)
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0 or (w > 0.95 and h > 0.95):
        return None
    width, height = size
    return {"width": round(100 / w, 3), "left": round(-100 * x1 / w, 3), "top": round(-100 * y1 / h, 3),
            "aspect": f"{w * width:.0f} / {h * height:.0f}"}


# How each supported currency is written: sign, and whether it goes before the amount.
CURRENCIES = {"EUR": ("€", False), "CZK": ("Kč", False), "PLN": ("zł", False), "HUF": ("Ft", False),
              "CHF": ("CHF", False), "GBP": ("£", True), "USD": ("$", True)}


def currency_sign(code: str) -> str:
    return CURRENCIES.get(code, (code, False))[0]


def money(cents: int, code: str = "EUR") -> str:
    """1 035,00 Kč, 12,50 €, £12,50: thousands grouped by a no-break space, decimal comma."""
    sign, before = CURRENCIES.get(code, (code, False))
    whole, part = divmod(abs(cents), 100)
    number = f"{'-' if cents < 0 else ''}{whole:,}".replace(",", "\u00a0") + f",{part:02d}"
    return f"{sign}{number}" if before else f"{number}\u00a0{sign}"
