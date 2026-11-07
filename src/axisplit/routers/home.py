from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import recent_ids
from ..models import Receipt
from ..ops import get_people, get_receipt_items
from ..templ import templates
from ..viewmodels import receipt_summary

router = APIRouter(tags=["home"])


@router.get("/")
async def index(request: Request, session: AsyncSession = Depends(get_session)):
    """Start a split from a photo. Splits opened on this device are listed to get back to; no one
    else's are, since a receipt's link is the key to it."""
    ids = recent_ids(request)
    found = {r.id: r for r in await session.scalars(select(Receipt).where(Receipt.id.in_(ids)))}
    rows = []
    for receipt in (found[i] for i in ids if i in found):
        items = await get_receipt_items(session, receipt.id)
        rows.append({"receipt": receipt, "summary": receipt_summary(items, await get_people(session, receipt.id))})
    return templates.TemplateResponse(request, "index.html", {"rows": rows})
