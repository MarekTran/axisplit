"""Readable, derived UI state. Amounts are consumption shares, not payment debts."""

from .models import Item
from .split import split_item

STATUS_LABELS = {
    "awaiting_items": "Needs detection",
    "awaiting_tokens": "Ready to split",
    "splitting": "Assigning items",
    "pending_confirm": "Waiting for agreement",
    "settled": "Everyone agreed",
    "removed": "Removed",
}


def receipt_summary(items: list[Item], confirmed: set[str]) -> dict:
    totals: dict[str, int] = {}
    for item in items:
        for share in split_item(item):
            totals[share.username] = totals.get(share.username, 0) + share.cents
    unassigned = [i for i in items if not i.tokens]
    involved = set(totals)
    return {
        "total": sum(i.price_cents for i in items),
        "assigned": sum(totals.values()),
        "unassigned_total": sum(i.price_cents for i in unassigned),
        "unassigned_count": len(unassigned),
        "assigned_count": len(items) - len(unassigned),
        "item_count": len(items),
        "totals": totals,
        "confirmed": sorted(confirmed & involved),
        "waiting": sorted(involved - confirmed),
        "complete": bool(items) and not unassigned,
    }
