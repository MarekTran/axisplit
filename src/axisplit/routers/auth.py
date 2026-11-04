from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse

from ..deps import get_who, set_who
from ..templ import templates

router = APIRouter(tags=["auth"])


@router.get("/claim")
def claim_form(request: Request, next: str = Query("/")):
    who = get_who(request)
    if who:
        return RedirectResponse(next, 303)
    return templates.TemplateResponse(request, "claim.html", {"next": next})


@router.post("/claim")
def claim(username: str = Form(...), next: str = Form("/")):
    username = username.strip()[:32]
    if not username:
        return RedirectResponse(f"/claim?next={next}", 302)
    resp = RedirectResponse(next, 303)
    set_who(resp, username)
    return resp
