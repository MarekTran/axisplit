"""Derived split math. The only persisted split state is TokenAssignment."""

from collections import defaultdict
from dataclasses import dataclass

from .models import Item


@dataclass
class Share:
    username: str
    item_id: str
    item_label: str
    cents: int


def split_item(item: Item) -> list[Share]:
    """Split item price evenly among token holders; extra cents go to the
    alphabetically-first holders (deterministic)."""
    holders = sorted(t.username for t in item.tokens)
    if not holders:
        return []
    n = len(holders)
    base, rem = divmod(item.price_cents, n)
    return [
        Share(h, item.id, item.label, base + (1 if i < rem else 0))
        for i, h in enumerate(holders)
    ]


def tab_balances(items: list[Item], payer: str) -> dict[str, int]:
    """Net balance per user relative to `payer` (whofronted the money):
    positive = others owes payer, negative = payer owes them. Items with no
    tokens are ignored (not yet split)."""
    owes: dict[str, int] = defaultdict(int)
    for item in items:
        for share in split_item(item):
            owes[share.username] += share.cents
    total = sum(owes.values())
    return {u: owed - (total if u == payer else 0) for u, owed in owes.items()}
