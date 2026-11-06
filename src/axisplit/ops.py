"""Semantic operations on receipts, shared by the HTTP (htmx) and WebSocket
paths. Each op returns a broadcast event dict; status is recomputed."""

from fastapi import HTTPException
from sqlalchemy import delete, select, update
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
    elif all(i.tokens for i in items) and involved and involved <= confirmed:
        status = ReceiptStatus.SETTLED
    elif confirmed and all(i.tokens for i in items):
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
    if receipt.status == ReceiptStatus.REMOVED:
        raise HTTPException(410, "This receipt was removed. Restore it from the tab to edit it.")

    if kind in ("place", "unplace"):
        item_id, target = op.get("item_id"), op.get("username")
        if not (target or "").strip():
            raise HTTPException(422, "username required")
        item = await session.get(Item, item_id)
        if item is None or item.receipt_id != receipt_id:
            raise HTTPException(404, "item not found")
        # place adds one more of target's tokens, unplace takes one off. Single UPDATE/DELETE
        # statements, so rapid taps from several phones can't lose a count.
        match = (TokenAssignment.item_id == item_id, TokenAssignment.username == target)
        if kind == "place":
            spot = op.get("spot")
            spot = max(0.0, min(float(spot), 1.0)) if isinstance(spot, (int, float)) else None
            moved = {} if spot is None else {"spot": spot}  # a tap on the photo moves the token there
            added = await session.execute(
                update(TokenAssignment).where(*match).values(count=TokenAssignment.count + 1, **moved))
            if not added.rowcount:
                session.add(TokenAssignment(item_id=item_id, username=target, spot=spot))
        else:
            taken = await session.execute(update(TokenAssignment).where(
                *match, TokenAssignment.count > 1).values(count=TokenAssignment.count - 1))
            if not taken.rowcount and not (await session.execute(delete(TokenAssignment).where(*match))).rowcount:
                return {"type": kind, "item_id": item_id, "username": target,
                        "status": receipt.status}  # no-op: don't disturb confirmations
        await session.commit()
        # spec §3: moving tokens invalidates previous confirmations
        for c in await session.scalars(select(Confirmation).where(Confirmation.receipt_id == receipt_id)):
            await session.delete(c)
        await session.commit()
        status = await recompute_status(session, receipt)
        return {"type": kind, "item_id": item_id, "username": target, "status": status}

    if kind == "confirm":
        items = await get_receipt_items(session, receipt_id)
        if not items or any(not i.tokens for i in items):
            raise HTTPException(409, "Assign every item before confirming the split.")
        if actor not in involved_users(items):
            raise HTTPException(409, "You have no assigned items to confirm yet.")
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


def clamp_box(x: float, y: float, w: float, h: float) -> dict[str, float]:
    """Keep a normalized box on the photo and big enough to tap."""
    return {"x": max(0.0, min(x, 0.95)), "y": max(0.0, min(y, 0.95)),
            "w": max(0.05, min(w, 1.0 - x)), "h": max(0.03, min(h, 1.0 - y))}


async def add_item(session: AsyncSession, receipt: Receipt, label: str, price_cents: int,
                   bbox: tuple[float, float, float, float] | None = None) -> Item:
    count = len(await get_receipt_items(session, receipt.id))
    if bbox is None:
        bbox = (0.03, min(0.9, 0.05 + count * 0.12), 0.9, 0.1)
    item = Item(id=new_id(), receipt_id=receipt.id, label=label, price_cents=price_cents, **clamp_box(*bbox))
    session.add(item)
    for confirmation in await session.scalars(
        select(Confirmation).where(Confirmation.receipt_id == receipt.id)
    ):
        await session.delete(confirmation)
    await session.commit()
    await recompute_status(session, receipt)
    return item
