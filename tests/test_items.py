"""Fixing items after detection: rename, reprice, delete with undo, and adding a missing one."""

import pytest

from axisplit.ops import parse_cents

from test_ux import client, items_for, op, people, upload  # noqa: F401  (client is a fixture)


@pytest.mark.parametrize("typed, cents", [
    ("2,10", 210), ("2.10", 210), ("12", 1200), ("1 035,00 Kč", 103500), ("1.035,00", 103500),
    ("1,035.00", 103500), ("-0,50", -50), ("€ 3,90", 390),
])
def test_prices_are_read_as_people_type_them(typed, cents):
    assert parse_cents(typed) == cents


async def test_a_new_price_lapses_the_ticks_of_those_on_the_item_only(client):
    receipt_id = await upload(client, "Adam", "Nina")
    first, second = await items_for(client, receipt_id)
    await op(client, receipt_id, "place", item_id=first.id, username="Adam")
    await op(client, receipt_id, "place", item_id=second.id, username="Nina")
    for name in ("Adam", "Nina"):
        await op(client, receipt_id, "confirm", username=name)

    renamed = await client.post(f"/r/{receipt_id}/items/{first.id}", data={"label": "Mango iced tea", "price": "6,95"})
    assert renamed.json() == {"item_id": first.id, "label": "Mango iced tea", "price_cents": 695}
    review = await client.get(f"/r/{receipt_id}/review")
    assert "Everyone with a share is ticked off" in review.text

    await client.post(f"/r/{receipt_id}/items/{first.id}", data={"label": "Mango iced tea", "price": "5,95"})
    review = await client.get(f"/r/{receipt_id}/review")
    assert "Mango iced tea" in review.text and "5,95" in review.text
    assert "1 of 2 ticked off" in review.text and "Was 6,95\u00a0€ when ticked" in review.text
    assert {n: p.agreed_cents for n, p in (await people(client, receipt_id)).items()} == {"Adam": 695, "Nina": 695}

    bad = await client.post(f"/r/{receipt_id}/items/{first.id}", data={"label": "Tea", "price": "free"})
    assert bad.status_code == 422


async def test_a_deleted_item_leaves_the_split_and_undo_brings_it_back_with_its_tokens(client):
    receipt_id = await upload(client)
    first, second = await items_for(client, receipt_id)
    await op(client, receipt_id, "place", item_id=first.id, username="Adam")

    deleted = await client.post(f"/r/{receipt_id}/items/{first.id}/delete")
    assert deleted.json() == {"item_id": first.id, "label": "Mango tea", "deleted": True}
    assert [i.id for i in await items_for(client, receipt_id)] == [second.id]
    review = await client.get(f"/r/{receipt_id}/review")
    assert "Mango tea" not in review.text and "0/1 assigned" in review.text
    assert (await op(client, receipt_id, "place", item_id=first.id, username="Adam")).status_code == 404
    assert (await client.post(f"/r/{receipt_id}/items/{first.id}/delete")).status_code == 404

    restored = await client.post(f"/r/{receipt_id}/items/{first.id}/restore")
    assert restored.json()["deleted"] is False
    first_again = (await items_for(client, receipt_id))[0]
    assert first_again.id == first.id and [t.username for t in first_again.tokens] == ["Adam"]


async def test_an_added_item_comes_back_marked_so_the_page_can_offer_undo(client):
    receipt_id = await upload(client)
    page = await client.post(f"/r/{receipt_id}/items", data={
        "label": "Tip", "price": "1 000,00", "x": ".5", "y": ".8", "w": ".6", "h": ".04"})
    added = [i for i in await items_for(client, receipt_id) if i.label == "Tip"][0]
    assert page.url.params["added"] == added.id
    assert added.price_cents == 100000
    assert f'data-edit="{added.id}"' in page.text
