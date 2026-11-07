"""No accounts: a receipt's link is the key to it, and whoever has it can do everything on it."""

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Receipt

RECENT = "recent"  # cookie: receipts opened on this device, newest first, listed on the home page


async def get_receipt_or_404(session: AsyncSession, receipt_id: str) -> Receipt:
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    return receipt


def recent_ids(request: Request) -> list[str]:
    return [i for i in request.cookies.get(RECENT, "").split(".") if i]  # ids never contain "."


def remember(response, request: Request, receipt_id: str) -> None:
    ids = [receipt_id] + [i for i in recent_ids(request) if i != receipt_id]
    response.set_cookie(RECENT, ".".join(ids[:20]), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365)
