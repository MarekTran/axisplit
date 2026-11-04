from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import new_id
from ..db import get_session
from ..deps import ensure_membership, get_tab_or_404, get_who, require_member, require_who
from ..models import Receipt, Tab, TabMember
from ..ops import get_receipt_items
from ..split import split_item
from ..templ import templates

router = APIRouter(tags=["tabs"])


@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"who": get_who(request)})


@router.post("/tabs")
async def create_tab(name: str = Form(...), request: Request = None, session: AsyncSession = Depends(get_session)):
    who = await require_who(request)
    tab = Tab(id=new_id(), name=name.strip()[:64], created_by=who)
    session.add(tab)
    await session.commit()
    await ensure_membership(session, tab.id, who)
    return RedirectResponse(f"/t/{tab.id}", 303)


@router.get("/t/{tab_id}")
async def tab_page(
    tab_id: str,
    request: Request,
    s: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    tab = await get_tab_or_404(session, tab_id)
    who = get_who(request)
    if who is None:
        return RedirectResponse(f"/claim?next=/t/{tab_id}?s={s or ''}", 302)
    # joining requires either membership or the invite secret (capability link)
    member = await session.get(TabMember, (tab_id, who))
    if member is None:
        if s != tab.invite_secret:
            return templates.TemplateResponse(request, "invite.html", {"tab": tab, "secret": s or ""}, status_code=403)
        member = await ensure_membership(session, tab_id, who)
    members = list(await session.scalars(select(TabMember).where(TabMember.tab_id == tab_id)))
    receipts = list(await session.scalars(select(Receipt).where(Receipt.tab_id == tab_id).order_by(Receipt.created_at.desc())))
    rows = []
    total_cents = 0
    for r in receipts:
        items = await get_receipt_items(session, r.id)
        receipt_total = sum(i.price_cents for i in items)
        total_cents += receipt_total
        rows.append({"receipt": r, "lines": items, "total": receipt_total})
    # ledger
    ledger = {}
    for row in rows:
        for item in row["lines"]:
            for share in split_item(item):
                ledger.setdefault(share.username, []).append(share)
    return templates.TemplateResponse(request, "tab.html", {
        "tab": tab, "who": who, "members": members, "rows": rows,
        "total_cents": total_cents, "ledger": ledger,
    })


@router.post("/t/{tab_id}/members")
async def add_member(tab_id: str, username: str = Form(...), request: Request = None,
                     session: AsyncSession = Depends(get_session)):
    await require_member(tab_id, request, session)
    await get_tab_or_404(session, tab_id)  # existence check
    await ensure_membership(session, tab_id, username.strip()[:32])
    return RedirectResponse(f"/t/{tab_id}", 303)
