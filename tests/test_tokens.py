"""Several tokens per person: proportional split math."""

import itertools

from axisplit.models import Item, TokenAssignment
from axisplit.split import split_item


def shares(price_cents, **counts):
    item = Item(id="i", label="Nachos", price_cents=price_cents,
                tokens=[TokenAssignment(username=u, count=n) for u, n in counts.items()])
    return {s.username: s.cents for s in split_item(item)}


def test_one_token_each_is_still_an_even_split():
    assert shares(1000, Nina=1, Adam=1, Marek=1) == {"Adam": 334, "Marek": 333, "Nina": 333}


def test_extra_tokens_are_a_bigger_part():
    # 5 parts Marek, 1 part Adam, 1 part Nina; the spare cent goes to the largest remainder.
    assert shares(500, Marek=5, Adam=1, Nina=1) == {"Adam": 72, "Marek": 357, "Nina": 71}
    assert shares(700, Marek=5, Adam=1, Nina=1) == {"Adam": 100, "Marek": 500, "Nina": 100}


def test_shares_always_add_up_to_the_price():
    for price, a, b, c in itertools.product((1, 99, 695, 3240, -300), (1, 2, 5), (1, 3), (1, 7)):
        assert sum(shares(price, Adam=a, Marek=b, Nina=c).values()) == price

