"""Interaction constraints and mistake recovery, isolated from the running app DB."""

import io
import json

import httpx
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from axisplit import ocr
from axisplit.db import get_session
from axisplit.main import app
from axisplit.models import Base, Item, Person, Receipt, TokenAssignment
from axisplit.routers import receipts
from axisplit.viewmodels import money, photo_view


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(receipts, "UPLOAD_DIR", uploads)
    client_focus = []
    monkeypatch.setattr(ocr, "extract_items", lambda _, focus, quad: client_focus.append(focus if quad is None else (focus, quad)) or [
        ocr.ExtractedItem("Mango tea", 695, .2, .4, .6, .02),
        ocr.ExtractedItem("Watermelon tea", 695, .2, .43, .6, .02),
    ])
    monkeypatch.setattr(ocr, "extract_total", lambda _: 1390)
    monkeypatch.setattr(ocr, "text_extent", lambda _: (.15, .3, .7, .4))
    monkeypatch.setattr(ocr, "currency", lambda _: None)  # unread: this device's last receipt's, else EUR
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
            c.sessions = sessions
            c.focus = client_focus
            yield c
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def photo(color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", (200, 400), color).save(buffer, format="JPEG")
    return buffer.getvalue()


async def upload(client, *people, **data):
    """A receipt with these people on it (Adam unless named); returns its id."""
    page = await client.post("/receipts", files={"image": ("check.jpg", photo(), "image/jpeg")}, data=data)
    receipt_id = page.url.path.rsplit("/", 1)[-1]
    for name in people or ("Adam",):
        assert (await client.post(f"/r/{receipt_id}/people", data={"username": name})).status_code == 200
    return receipt_id


async def items_for(client, receipt_id):
    async with client.sessions() as session:
        from axisplit.ops import get_receipt_items
        return await get_receipt_items(session, receipt_id)


async def op(client, receipt_id, kind, **kwargs):
    return await client.post(f"/r/{receipt_id}/ops", json={"op": kind, **kwargs})


async def people(client, receipt_id):
    async with client.sessions() as session:
        return {p.username: p for p in await session.scalars(select(Person).where(Person.receipt_id == receipt_id))}


async def test_anyone_with_the_link_splits_without_signing_in(client):
    receipt_id = await upload(client)
    first, _ = await items_for(client, receipt_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as friend:
        page = await friend.get(f"/r/{receipt_id}")  # no cookies, no name to claim
        assert page.status_code == 200 and "Share link" in page.text
        assert (await friend.post(f"/r/{receipt_id}/people", data={"username": "Nina"})).status_code == 200
        placed = await friend.post(f"/r/{receipt_id}/ops", json={"op": "place", "item_id": first.id, "username": "Adam"})
        assert placed.json()["username"] == "Adam"  # for anyone on the receipt, not only "yourself"
    assert (await op(client, receipt_id, "place", item_id=first.id, username="Ghost")).status_code == 404
    assert (await client.get("/r/nope")).status_code == 404


async def test_people_are_added_by_name_and_removed_with_their_tokens(client):
    receipt_id = await upload(client, "Adam", "Nina")
    assert (await client.post(f"/r/{receipt_id}/people", data={"username": " nina "})).status_code == 409
    assert (await client.post(f"/r/{receipt_id}/people", data={"username": "  "})).status_code == 422
    first, second = await items_for(client, receipt_id)
    for name, item in (("Adam", first), ("Nina", first), ("Nina", second)):
        await op(client, receipt_id, "place", item_id=item.id, username=name)
    before = await people(client, receipt_id)
    assert before["Adam"].color != before["Nina"].color

    removed = await client.post(f"/r/{receipt_id}/people/remove", data={"username": "Nina"})
    assert removed.json() == {"username": "Nina", "removed": True}
    first, second = await items_for(client, receipt_id)
    assert [t.username for t in first.tokens] == ["Adam"] and not second.tokens  # Adam has all of the first now
    review = await client.get(f"/r/{receipt_id}/review")
    assert "Nina" not in review.text and "1/2 assigned" in review.text
    assert (await client.post(f"/r/{receipt_id}/people/remove", data={"username": "Nina"})).status_code == 404
    again = await client.post(f"/r/{receipt_id}/people", data={"username": "Marek"})
    assert again.json()["color"] == before["Nina"].color  # the freed color, not Adam's


async def test_anyone_can_be_ticked_off_and_a_tick_lapses_only_when_their_share_moves(client):
    receipt_id = await upload(client, "Adam", "Nina")
    first, second = await items_for(client, receipt_id)
    await op(client, receipt_id, "place", item_id=first.id, username="Adam")
    ticked = await op(client, receipt_id, "confirm", username="Adam")  # before everything is assigned: fine
    assert ticked.json() == {"type": "confirm", "username": "Adam", "status": "splitting"}
    await op(client, receipt_id, "place", item_id=second.id, username="Nina")
    assert (await op(client, receipt_id, "confirm", username="Nina")).json()["status"] == "settled"
    review = await client.get(f"/r/{receipt_id}/review")
    assert "Everyone with a share is ticked off" in review.text and "Mango tea" in review.text

    await op(client, receipt_id, "place", item_id=second.id, username="Adam")  # Adam shares Nina's tea now
    agreed = {name: p.agreed_cents for name, p in (await people(client, receipt_id)).items()}
    assert agreed == {"Adam": 695, "Nina": 695}  # what they said yes to stays recorded
    review = await client.get(f"/r/{receipt_id}/review")
    assert "Was 6,95\u00a0€ when ticked. Ask again." in review.text
    assert "0 of 2 ticked off" in review.text and "Watermelon tea (shared)" in review.text

    await op(client, receipt_id, "unplace", item_id=second.id, username="Adam")  # undone: both ticks hold again
    assert (await op(client, receipt_id, "unconfirm", username="Nina")).json()["status"] == "pending_confirm"
    review = await client.get(f"/r/{receipt_id}/review")
    assert "1 of 2 ticked off" in review.text and "All assigned" in (await client.get(f"/r/{receipt_id}")).text
    assert (await op(client, receipt_id, "approve", username="Nina")).status_code == 422


async def test_home_lists_the_splits_opened_on_this_device_only(client):
    first = await upload(client)
    page = await client.get("/")
    assert f'href="/r/{first}"' in page.text and "Choose receipt photo" in page.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as other:
        assert f'href="/r/{first}"' not in (await other.get("/")).text
        await other.get(f"/r/{first}")  # opened from a shared link: now listed there too
        assert f'href="/r/{first}"' in (await other.get("/")).text
    second = await upload(client)
    listed = (await client.get("/")).text
    assert listed.index(f'href="/r/{second}"') < listed.index(f'href="/r/{first}"')  # newest first


async def test_extra_tokens_weight_the_split_and_come_off_one_at_a_time(client):
    receipt_id = await upload(client, "Adam", "Marek", "Nina")
    first, _ = await items_for(client, receipt_id)
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
    receipt_id = await upload(client)
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
    page = await client.post("/receipts", files={"image": ("check.jpg", photo(), "image/jpeg")},
                             data={"focus_x": ".1", "focus_y": ".3", "focus_w": ".8", "focus_h": ".4"})
    receipt_id = page.url.path.rsplit("/", 1)[-1]
    assert client.focus == [(.1, .3, .8, .4)]
    async with client.sessions() as session:
        receipt = await session.get(Receipt, receipt_id)
        assert (receipt.focus_x, receipt.focus_h, receipt.printed_total_cents) == (.1, .4, 1390)
    assert "match the receipt total" in page.text


async def test_dragged_corners_are_stored_and_bound_the_crop(client):
    quad = [[.1, .3], [.9, .32], [.9, .7], [.1, .66]]
    page = await client.post("/receipts", files={"image": ("check.jpg", photo(), "image/jpeg")},
                             data={"focus_x": ".1", "focus_y": ".3", "focus_w": ".8", "focus_h": ".4",
                                   "focus_quad": json.dumps(quad)})
    receipt_id = page.url.path.rsplit("/", 1)[-1]
    (focus, sent), = client.focus
    assert [round(v, 6) for v in focus] == [.1, .3, .8, .4] and sent == [tuple(p) for p in quad]
    async with client.sessions() as session:
        assert json.loads((await session.get(Receipt, receipt_id)).focus_quad) == quad


async def test_printed_total_flags_a_missing_item(client, monkeypatch):
    monkeypatch.setattr(ocr, "extract_total", lambda _: 2000)
    receipt_id = await upload(client)
    assert client.focus == [None]  # no focus drawn: whole photo
    review = await client.get(f"/r/{receipt_id}/review")
    assert "Items add up to 13,90\u00a0€" in review.text and "6,10\u00a0€ missing" in review.text
    await client.post(f"/r/{receipt_id}/items", data={"label": "Cake", "price": "6,10",
                                                       "x": ".5", "y": ".6", "w": ".5", "h": ".04"})
    review = await client.get(f"/r/{receipt_id}/review")
    assert "match the receipt total" in review.text
    cake = next(i for i in await items_for(client, receipt_id) if i.label == "Cake")
    assert (round(cake.x, 3), round(cake.y, 3), cake.w, cake.h) == (.25, .58, .5, .04)


async def test_redrawing_a_box_keeps_shares_and_ticks(client):
    receipt_id = await upload(client)
    first, second = await items_for(client, receipt_id)
    for item in (first, second):
        await op(client, receipt_id, "place", item_id=item.id, username="Adam")
    await op(client, receipt_id, "confirm", username="Adam")
    moved = await client.post(f"/r/{receipt_id}/items/{second.id}/box",
                              data={"x": ".5", "y": ".55", "w": ".6", "h": ".04"})
    assert moved.json()["type"] == "box"
    _, second_after = await items_for(client, receipt_id)
    assert (round(second_after.y, 3), second_after.h) == (.53, .04)
    async with client.sessions() as session:
        assert (await session.get(Receipt, receipt_id)).status == "settled"


async def test_board_shows_the_receipt_not_the_whole_photo(client):
    receipt_id = await upload(client)
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


async def test_currency_is_read_inherited_on_this_device_and_fixable(client, monkeypatch):
    monkeypatch.setattr(ocr, "currency", lambda _: "CZK")
    first = await upload(client)
    page = await client.get(f"/r/{first}")
    assert "6,95\u00a0Kč" in page.text and "Price (Kč)" in page.text and "\u00a0€" not in page.text  # no amount in euros (the picker still lists EUR)
    monkeypatch.setattr(ocr, "currency", lambda _: None)  # unread: this device's last receipt's
    second = (await client.post("/receipts", files={"image": ("b.jpg", photo("gray"), "image/jpeg")})).url.path.rsplit("/", 1)[-1]
    async with client.sessions() as session:
        assert (await session.get(Receipt, second)).currency == "CZK"
    page = await client.post(f"/r/{second}/currency", data={"currency": "EUR"})
    assert "6,95\u00a0€" in page.text
    assert (await client.post(f"/r/{second}/currency", data={"currency": "XYZ"})).status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 follow_redirects=True) as other:  # a device with no receipts yet: euros
        third = await other.post("/receipts", files={"image": ("c.jpg", photo("black"), "image/jpeg")})
        assert "6,95\u00a0€" in third.text
