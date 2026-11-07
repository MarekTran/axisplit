"""Semantic operations on receipts, shared by the HTTP (htmx) and WebSocket
paths. Each op returns a broadcast event dict; status is recomputed."""

import re

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import new_id
from .models import Item, Person, Receipt, ReceiptStatus, TokenAssignment
from .split import agreed, totals


async def recompute_status(session: AsyncSession, receipt: Receipt) -> str:
    items = await get_receipt_items(session, receipt.id)
    shares = totals(items)
    if not items:
        status = ReceiptStatus.AWAITING_ITEMS
    elif not shares:
        status = ReceiptStatus.AWAITING_TOKENS
    elif not all(i.tokens for i in items):
        status = ReceiptStatus.SPLITTING
    elif set(shares) <= agreed(await get_people(session, receipt.id), shares):
        status = ReceiptStatus.SETTLED
    else:
        status = ReceiptStatus.PENDING_CONFIRM
    if status.value != receipt.status:
        receipt.status = status.value
        await session.commit()
    return status.value


async def get_people(session: AsyncSession, receipt_id: str) -> list[Person]:
    return list(await session.scalars(
        select(Person).where(Person.receipt_id == receipt_id).order_by(Person.added_at)))


async def get_receipt_items(session: AsyncSession, receipt_id: str) -> list[Item]:
    stmt = (
        select(Item)
        .where(Item.receipt_id == receipt_id, Item.deleted.is_(False))
        .options(selectinload(Item.tokens))
        .order_by(Item.y, Item.x)
    )
    return list(await session.scalars(stmt))


async def lock_receipt(session: AsyncSession, receipt_id: str) -> None:
    """Holds the receipt until this transaction ends, so changes to its people and their tokens take
    turns: a token is never written for someone removed meanwhile. Call it before reading what the
    change depends on. A write that changes nothing: Postgres locks the row, and SQLite, which has no
    row locks (nor FOR UPDATE), takes its database write lock."""
    await session.execute(update(Receipt).where(Receipt.id == receipt_id).values(status=Receipt.status)
                          .execution_options(synchronize_session=False))


async def apply_op(session: AsyncSession, receipt: Receipt, op: dict) -> dict:
    """Validate & apply one client op, for any person on the receipt. Returns the event to broadcast."""
    kind, receipt_id = op.get("op"), receipt.id
    if kind not in ("place", "unplace", "confirm", "unconfirm"):
        raise HTTPException(422, f"unknown op {kind!r}")
    await lock_receipt(session, receipt_id)
    person = await session.get(Person, (receipt_id, op.get("username") or ""), populate_existing=True)
    if person is None:
        raise HTTPException(404, "Add this person to the receipt first.")
    target = person.username

    if kind in ("place", "unplace"):
        item_id = op.get("item_id")
        item = await session.get(Item, item_id)
        if item is None or item.receipt_id != receipt_id or item.deleted:
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
                return {"type": kind, "item_id": item_id, "username": target, "status": receipt.status}  # no-op
        await session.commit()
        status = await recompute_status(session, receipt)
        return {"type": kind, "item_id": item_id, "username": target, "status": status}

    # Ticked off at the share they have now, or unticked. Optional: nothing waits for it.
    shares = totals(await get_receipt_items(session, receipt_id))
    person.agreed_cents = shares.get(target, 0) if kind == "confirm" else None
    await session.commit()
    status = await recompute_status(session, receipt)
    return {"type": kind, "username": target, "status": status}


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
    await session.commit()
    await recompute_status(session, receipt)
    return item


def parse_cents(price: str) -> int:
    """A price as people type or copy it ("2,10", "1 035,00 Kč", "-0.50") -> cents; ValueError if none."""
    text = re.sub(r"[^\d,.\-]", "", price)
    if "," in text and "." in text:  # the last one is the decimal mark, the other groups thousands
        mark = max(text.rfind(","), text.rfind("."))
        text = re.sub(r"[,.]", "", text[:mark]) + "." + text[mark + 1:]
    return round(float(text.replace(",", ".")) * 100)
