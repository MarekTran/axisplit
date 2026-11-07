"""Derived split math. The only persisted split state is TokenAssignment."""

from collections import defaultdict
from dataclasses import dataclass

from .models import Item, Person


@dataclass
class Share:
    username: str
    item_id: str
    item_label: str
    cents: int


def split_item(item: Item) -> list[Share]:
    """Split item price in proportion to each holder's token count. Leftover
    cents go to the largest remainders, ties to the alphabetically-first holder
    (deterministic). With one token each this is an even split."""
    tokens = sorted(item.tokens, key=lambda t: t.username)
    parts = sum(t.count for t in tokens)
    if not parts:
        return []
    exact = {t.username: divmod(item.price_cents * t.count, parts) for t in tokens}
    leftover = item.price_cents - sum(cents for cents, _ in exact.values())
    bonus = set(sorted(exact, key=lambda u: -exact[u][1])[:leftover])  # stable sort keeps name order on ties
    return [Share(t.username, item.id, item.label, exact[t.username][0] + (t.username in bonus)) for t in tokens]


def totals(items: list[Item]) -> dict[str, int]:
    """Each person's share of these items, in cents."""
    owed: dict[str, int] = defaultdict(int)
    for item in items:
        for share in split_item(item):
            owed[share.username] += share.cents
    return dict(owed)


def agreed(people: list[Person], shares: dict[str, int]) -> set[str]:
    """Who is ticked off at the share they have now."""
    return {p.username for p in people if p.agreed_cents is not None and p.agreed_cents == shares.get(p.username, 0)}


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
