"""Semantic operations on receipts, shared by the HTTP (htmx) and WebSocket
paths. Each op returns a broadcast event dict; status is recomputed."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import new_id
from .models import Confirmation, Item, Receipt, ReceiptStatus, Tab, TokenAssignment


async def recompute_status(session: AsyncSession, receipt: Receipt) -> str:
    items = await get_receipt_items(session, receipt.id)
    has_tokens = any(i.tokens for i in items)
    involved = involved_users(items)
    confirmed = {c.username for c in (await session.scalars(
        select(Confirmation).where(Confirmation.receipt_id == receipt.id)))}
    if not items:
        status = ReceiptStatus.AWAITING_ITEMS
    elif not has_tokens:
        status = ReceiptStatus.AWAITING_TOKENS
    elif involved and involved <= confirmed:
        status = ReceiptStatus.SETTLED
    elif confirmed:
        status = ReceiptStatus.PENDING_CONFIRM
    else:
        status = ReceiptStatus.SPLITTING
    if status.value != receipt.status:
        receipt.status = status.value
        await session.commit()
    return status.value


def involved_users(items: list[Item]) -> set[str]:
    return {t.username for i in items for t in i.tokens}


async def get_receipt_items(session: AsyncSession, receipt_id: str) -> list[Item]:
    stmt = (
        select(Item)
        .where(Item.receipt_id == receipt_id)
        .options(selectinload(Item.tokens))
        .order_by(Item.y, Item.x)
    )
    return list(await session.scalars(stmt))


async def apply_op(session: AsyncSession, tab: Tab, receipt_id: str, op: dict) -> dict:
    """Validate & apply one client op. Returns the event to broadcast."""
    kind = op.get("op")
    actor = op.get("actor")
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None or receipt.tab_id != tab.id:
        raise HTTPException(404, "receipt not found")

    if kind in ("place", "unplace"):
        item_id, target = op.get("item_id"), op.get("username")
        if not (target or "").strip():
            raise HTTPException(422, "username required")
        item = await session.get(Item, item_id)
        if item is None or item.receipt_id != receipt_id:
            raise HTTPException(404, "item not found")
        existing = await session.get(TokenAssignment, (item_id, target))
        if kind == "place" and existing is None:
            session.add(TokenAssignment(item_id=item_id, username=target))
            await session.commit()
        elif kind == "unplace" and existing is not None:
            await session.delete(existing)
            await session.commit()
        else:
            return {"type": kind, "item_id": item_id, "username": target,
                    "status": receipt.status}  # no-op: don't disturb confirmations
        # spec §3: moving tokens invalidates previous confirmations
        for c in await session.scalars(select(Confirmation).where(Confirmation.receipt_id == receipt_id)):
            await session.delete(c)
        await session.commit()
        status = await recompute_status(session, receipt)
        return {"type": kind, "item_id": item_id, "username": target, "status": status}

    if kind == "confirm":
        if await session.get(Confirmation, (receipt_id, actor)) is None:
            session.add(Confirmation(receipt_id=receipt_id, username=actor))
            await session.commit()
        status = await recompute_status(session, receipt)
        return {"type": "confirm", "username": actor, "status": status}

    if kind == "unconfirm":
        c = await session.get(Confirmation, (receipt_id, actor))
        if c is not None:
            await session.delete(c)
            await session.commit()
        status = await recompute_status(session, receipt)
        return {"type": "unconfirm", "username": actor, "status": status}

    raise HTTPException(422, f"unknown op {kind!r}")


async def add_item(session: AsyncSession, receipt: Receipt, label: str, price_cents: int) -> Item:
    count = len(await get_receipt_items(session, receipt.id))
    item = Item(
        id=new_id(), receipt_id=receipt.id, label=label, price_cents=price_cents,
        x=0.03, y=min(0.9, 0.05 + count * 0.12), w=0.9, h=0.1,
    )
    session.add(item)
    await session.commit()
    await recompute_status(session, receipt)
    return item
