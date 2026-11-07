"""Interaction constraints and mistake recovery, isolated from the running app DB."""

import io
import json
from types import SimpleNamespace

import httpx
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from axisplit import images, ocr
from axisplit.db import get_session
from axisplit.main import app
from axisplit.models import Base, Confirmation, Item, Receipt, TokenAssignment
from axisplit.routers import receipts
from axisplit.viewmodels import money, money_by_currency, photo_view


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(receipts, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(images, "UPLOAD_DIR", uploads)
    # What the reader returns; currency unread: the tab's last receipt's, else EUR.
    reader = SimpleNamespace(total=1390, currency=None, areas=[])
    monkeypatch.setattr(ocr, "read", lambda _, area: reader.areas.append(area) or ocr.Reading([
        ocr.ExtractedItem("Mango tea", 695, .2, .4, .6, .02),
        ocr.ExtractedItem("Watermelon tea", 695, .2, .43, .6, .02),
    ], reader.total, reader.currency, (.15, .3, .7, .4)))
    monkeypatch.setattr(ocr, "locate", lambda _: (.15, .3, .7, .4))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def isolated_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = isolated_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test", follow_redirects=True) as c:
            await c.post("/claim", data={"username": "Adam", "next": "/"})
            c.sessions = sessions
            c.reader, c.focus = reader, reader.areas
            yield c
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def photo(color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", (200, 400), color).save(buffer, format="JPEG")
    return buffer.getvalue()


async def upload(client):
    tab = await client.post("/tabs", data={"name": "Dinner"})
    tab_id = tab.url.path.rsplit("/", 1)[-1]
    page = await client.post(f"/t/{tab_id}/receipts", files={
        "image": ("check.jpg", photo(), "image/jpeg"),
    })
    return tab_id, page.url.path.rsplit("/", 1)[-1]


async def items_for(client, receipt_id):
    async with client.sessions() as session:
        from axisplit.ops import get_receipt_items
        return await get_receipt_items(session, receipt_id)


async def op(client, receipt_id, kind, **kwargs):
    return await client.post(f"/r/{receipt_id}/ops", json={"op": kind, **kwargs})


async def test_duplicate_upload_opens_existing_receipt(client):
    tab_id, receipt_id = await upload(client)
    duplicate = await client.post(f"/t/{tab_id}/receipts", files={
        "image": ("renamed.jpg", photo(), "image/jpeg"),
    })
    assert duplicate.status_code == 200
    assert duplicate.url.path == f"/r/{receipt_id}"
    assert duplicate.url.params["duplicate"] == "1"
    assert "already in the tab" in duplicate.text
    async with client.sessions() as session:
        assert len(list(await session.scalars(select(Receipt)))) == 1


async def test_confirmation_requires_all_items_assigned(client):
    _, receipt_id = await upload(client)
    first, second = await items_for(client, receipt_id)
    await op(client, receipt_id, "place", item_id=first.id, username="Adam")
    partial = await op(client, receipt_id, "confirm")
    assert partial.status_code == 409
    assert "Assign every item" in partial.text
    await op(client, receipt_id, "place", item_id=second.id, username="Adam")
    confirmed = await op(client, receipt_id, "confirm")
    assert confirmed.json()["status"] == "settled"
    review = await client.get(f"/r/{receipt_id}/review")
    assert "You agreed to your 13,90\u00a0€ share" in review.text
    assert "Withdraw my confirmation" in review.text
    await op(client, receipt_id, "unplace", item_id=second.id, username="Adam")
    review = await client.get(f"/r/{receipt_id}/review")
    assert "Assign every item before confirming" in review.text
    assert "You agreed" not in review.text


async def test_remove_and_undo_preserves_tokens_and_confirmations(client):
    _, receipt_id = await upload(client)
    for item in await items_for(client, receipt_id):
        await op(client, receipt_id, "place", item_id=item.id, username="Adam")
    await op(client, receipt_id, "confirm")
    removed = await client.post(f"/r/{receipt_id}/remove")
    assert removed.status_code == 200
    assert "Undo removal" in removed.text
    assert "0,00\u00a0€" in removed.text
    assert 'class="receipt-card ' not in removed.text
    blocked = await op(client, receipt_id, "confirm")
    assert blocked.status_code == 410
    restored = await client.post(f"/r/{receipt_id}/restore")
    assert "Receipt restored" in restored.text
    assert "13,90\u00a0€" in restored.text
    async with client.sessions() as session:
        receipt = await session.get(Receipt, receipt_id)
        assert receipt.status == "settled"
        assert len(list(await session.scalars(select(TokenAssignment)))) == 2
        assert len(list(await session.scalars(select(Confirmation)))) == 1


async def test_nonmember_cannot_remove_receipt(client):
    _, receipt_id = await upload(client)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as outsider:
        await outsider.post("/claim", data={"username": "Outsider", "next": "/"})
        response = await outsider.post(f"/r/{receipt_id}/remove")
    assert response.status_code == 403
    async with client.sessions() as session:
        assert (await session.get(Receipt, receipt_id)).status == "awaiting_tokens"


async def test_tab_totals_distinguish_assigned_from_unassigned(client):
    tab_id, receipt_id = await upload(client)
    first, _ = await items_for(client, receipt_id)
    await op(client, receipt_id, "place", item_id=first.id, username="Adam")
    page = await client.get(f"/t/{tab_id}")
    assert "Receipt totals" in page.text
    assert "13,90\u00a0€" in page.text
    assert "6,95\u00a0€" in page.text
    assert "not a payment or transfer balance" in page.text.lower()
    assert "owes / gets back" not in page.text


async def test_extra_tokens_weight_the_split_and_come_off_one_at_a_time(client):
    tab_id, receipt_id = await upload(client)
    first, _ = await items_for(client, receipt_id)
    for name in ("Marek", "Nina"):
        await client.post(f"/t/{tab_id}/members", data={"username": name})
    for name in ["Marek"] * 5 + ["Adam", "Nina"]:  # Marek had most of the 6,95 € tea
        await op(client, receipt_id, "place", item_id=first.id, username=name)
    review = await client.get(f"/r/{receipt_id}/review")
    assert "4,97\u00a0€" in review.text and "0,99\u00a0€" in review.text
    assert "×5" in review.text

    await op(client, receipt_id, "unplace", item_id=first.id, username="Marek")
    await op(client, receipt_id, "unplace", item_id=first.id, username="Marek")
    await op(client, receipt_id, "unplace", item_id=first.id, username="Adam")
    noop = await op(client, receipt_id, "unplace", item_id=first.id, username="Adam")
    assert noop.status_code == 200
    async with client.sessions() as session:
        counts = {t.username: t.count for t in await session.scalars(select(TokenAssignment))}
    assert counts == {"Marek": 3, "Nina": 1}
    review = await client.get(f"/r/{receipt_id}/review")
    assert "5,21\u00a0€" in review.text and "1,74\u00a0€" in review.text


async def test_a_token_sits_where_it_was_tapped(client):
    _, receipt_id = await upload(client)
    first, _ = await items_for(client, receipt_id)
    await op(client, receipt_id, "place", item_id=first.id, username="Adam", spot=.3)
    await op(client, receipt_id, "place", item_id=first.id, username="Adam")  # from the list: stays put
    async with client.sessions() as session:
        token = await session.scalar(select(TokenAssignment))
        assert (token.count, token.spot) == (2, .3)
    await op(client, receipt_id, "place", item_id=first.id, username="Adam", spot=7)  # tapped again: moves
    review = await client.get(f"/r/{receipt_id}/review")
    assert 'data-spot="1.0"' in review.text
    page = await client.get(f"/r/{receipt_id}")
    assert "left: 100.0%" in page.text


async def test_focus_area_is_stored_and_sent_to_ocr(client):
    tab = await client.post("/tabs", data={"name": "Dinner"})
    tab_id = tab.url.path.rsplit("/", 1)[-1]
    page = await client.post(f"/t/{tab_id}/receipts", files={"image": ("check.jpg", photo(), "image/jpeg")},
                             data={"focus_x": ".1", "focus_y": ".3", "focus_w": ".8", "focus_h": ".4"})
    receipt_id = page.url.path.rsplit("/", 1)[-1]
    area, = client.focus  # the rectangle's corners
    assert [(round(x, 6), round(y, 6)) for x, y in area] == [(.1, .3), (.9, .3), (.9, .7), (.1, .7)]
    async with client.sessions() as session:
        receipt = await session.get(Receipt, receipt_id)
        assert (receipt.focus_x, receipt.focus_h, receipt.printed_total_cents) == (.1, .4, 1390)
    assert "match the receipt total" in page.text


async def test_dragged_corners_are_stored_and_bound_the_crop(client):
    tab = await client.post("/tabs", data={"name": "Dinner"})
    tab_id = tab.url.path.rsplit("/", 1)[-1]
    quad = [[.1, .3], [.9, .32], [.9, .7], [.1, .66]]
    page = await client.post(f"/t/{tab_id}/receipts", files={"image": ("check.jpg", photo(), "image/jpeg")},
                             data={"focus_x": ".1", "focus_y": ".3", "focus_w": ".8", "focus_h": ".4",
                                   "focus_quad": json.dumps(quad)})
    receipt_id = page.url.path.rsplit("/", 1)[-1]
    sent, = client.focus
    assert sent == [tuple(p) for p in quad]
    async with client.sessions() as session:
        assert json.loads((await session.get(Receipt, receipt_id)).focus_quad) == quad


async def test_printed_total_flags_a_missing_item(client):
    client.reader.total = 2000
    _, receipt_id = await upload(client)
    assert client.focus == [None]  # no focus drawn: whole photo
    review = await client.get(f"/r/{receipt_id}/review")
    assert "Items add up to 13,90\u00a0€" in review.text and "6,10\u00a0€ missing" in review.text
    await client.post(f"/r/{receipt_id}/items", data={"label": "Cake", "price": "6,10",
                                                       "x": ".5", "y": ".6", "w": ".5", "h": ".04"})
    review = await client.get(f"/r/{receipt_id}/review")
    assert "match the receipt total" in review.text
    cake = next(i for i in await items_for(client, receipt_id) if i.label == "Cake")
    assert (round(cake.x, 3), round(cake.y, 3), cake.w, cake.h) == (.25, .58, .5, .04)


async def test_redrawing_a_box_keeps_shares_and_confirmations(client):
    _, receipt_id = await upload(client)
    first, second = await items_for(client, receipt_id)
    for item in (first, second):
        await op(client, receipt_id, "place", item_id=item.id, username="Adam")
    await op(client, receipt_id, "confirm")
    moved = await client.post(f"/r/{receipt_id}/items/{second.id}/box",
                              data={"x": ".5", "y": ".55", "w": ".6", "h": ".04"})
    assert moved.json()["type"] == "box"
    _, second_after = await items_for(client, receipt_id)
    assert (round(second_after.y, 3), second_after.h) == (.53, .04)
    async with client.sessions() as session:
        assert len(list(await session.scalars(select(Confirmation)))) == 1


async def test_board_shows_the_receipt_not_the_whole_photo(client):
    _, receipt_id = await upload(client)
    async with client.sessions() as session:
        receipt = await session.get(Receipt, receipt_id)
        assert (receipt.view_x, receipt.view_w) == (.15, .7)
    page = await client.get(f"/r/{receipt_id}")
    # Text at x .15-.85, y .3-.7, plus a .04 margin, on a 200 x 400 photo: 156 x 192 px of it shown.
    assert 'class="imgwrap cropped"' in page.text and "aspect-ratio: 156 / 192" in page.text
    assert "width: 128.205%; left: -14.103%; top: -54.167%" in page.text


def test_photo_view_needs_something_to_show():
    receipt = Receipt(image_path="x.jpg")
    assert photo_view(receipt, [], (100, 100)) is None  # nothing detected: the whole photo, to draw on
    item = Item(x=.02, y=.02, w=.96, h=.96)
    assert photo_view(receipt, [item], (100, 100)) is None  # the receipt fills the photo


def test_money_is_written_the_local_way():
    assert money(103500, "CZK") == "1\u00a0035,00\u00a0Kč"
    assert money(-50) == "-0,50\u00a0€"
    assert money(1250, "GBP") == "£12,50"
    assert money_by_currency({"CZK": 103500, "EUR": 0}) == "1\u00a0035,00\u00a0Kč"
    assert money_by_currency({}, "CZK") == "0,00\u00a0Kč"


async def test_currency_is_read_inherited_fixable_and_never_summed_across(client):
    client.reader.currency = "CZK"
    tab_id, first = await upload(client)
    page = await client.get(f"/r/{first}")
    assert "6,95\u00a0Kč" in page.text and "Price (Kč)" in page.text and "\u00a0€" not in page.text  # no amount in euros (the picker still lists EUR)
    client.reader.currency = None  # unread: the tab's last receipt's
    second = (await client.post(f"/t/{tab_id}/receipts", files={"image": ("b.jpg", photo("gray"), "image/jpeg")})).url.path.rsplit("/", 1)[-1]
    async with client.sessions() as session:
        assert (await session.get(Receipt, second)).currency == "CZK"
    page = await client.post(f"/r/{second}/currency", data={"currency": "EUR"})
    assert "6,95\u00a0€" in page.text
    assert (await client.post(f"/r/{second}/currency", data={"currency": "XYZ"})).status_code == 422
    tab = await client.get(f"/t/{tab_id}")
    assert "13,90\u00a0Kč + 13,90\u00a0€" in tab.text  # two currencies, two sums
