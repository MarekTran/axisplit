"""Interaction constraints and mistake recovery, isolated from the running app DB."""

import io

import httpx
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from axisplit import images, ocr
from axisplit.db import get_session
from axisplit.main import app
from axisplit.models import Base, Confirmation, Receipt, TokenAssignment
from axisplit.routers import receipts


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(receipts, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(images, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(ocr, "extract_items", lambda _: [
        ocr.ExtractedItem("Mango tea", 695, .2, .4, .6, .02),
        ocr.ExtractedItem("Watermelon tea", 695, .2, .43, .6, .02),
    ])
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
            yield c
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def photo():
    buffer = io.BytesIO()
    Image.new("RGB", (200, 400), "white").save(buffer, format="JPEG")
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
    assert "You agreed to your 13,90 € share" in review.text
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
    assert "0,00 €" in removed.text
    assert 'class="receipt-card ' not in removed.text
    blocked = await op(client, receipt_id, "confirm")
    assert blocked.status_code == 410
    restored = await client.post(f"/r/{receipt_id}/restore")
    assert "Receipt restored" in restored.text
    assert "13,90 €" in restored.text
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
    assert "13,90 €" in page.text
    assert "6,95 €" in page.text
    assert "not a payment or transfer balance" in page.text.lower()
    assert "owes / gets back" not in page.text
