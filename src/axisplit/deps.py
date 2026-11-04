from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import SECRET_KEY
from .db import get_session
from .models import Tab, TabMember

_s = URLSafeSerializer(SECRET_KEY, salt="who")


def set_who(response, username: str) -> None:
    response.set_cookie("who", _s.dumps(username), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 90)


def get_who(request: Request) -> str | None:
    try:
        return _s.loads(request.cookies.get("who", ""))
    except BadSignature:
        return None


async def require_who(request: Request) -> str:
    who = get_who(request)
    if not who:
        raise HTTPException(401, "claim a username first")
    return who


async def require_member(tab_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> TabMember:
    who = await require_who(request)
    member = await session.get(TabMember, (tab_id, who))
    if member is None:
        raise HTTPException(403, f"{who} is not a member of tab {tab_id}")
    return member


async def get_tab_or_404(session: AsyncSession, tab_id: str) -> Tab:
    tab = await session.get(Tab, tab_id)
    if tab is None:
        raise HTTPException(404, "tab not found")
    return tab


async def ensure_membership(session: AsyncSession, tab_id: str, username: str) -> TabMember:
    """username-is-ID: claiming a name inside a tab creates membership with a
    fresh color. Also used to add other users by name (spec §2)."""
    member = await session.get(TabMember, (tab_id, username))
    if member is None:
        count = (await session.scalars(select(TabMember).where(TabMember.tab_id == tab_id))).all()
        from .colors import color_for

        member = TabMember(tab_id=tab_id, username=username, color=color_for(len(count)))
        session.add(member)
        await session.commit()
    return member
