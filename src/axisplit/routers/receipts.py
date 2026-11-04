
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ocr
from ..config import ALLOWED_IMAGE_TYPES, MAX_UPLOAD_BYTES, UPLOAD_DIR, new_id
from ..db import get_session
from ..deps import get_tab_or_404, require_member
from ..models import Receipt, TabMember
from ..ops import add_item, apply_op, get_receipt_items, recompute_status
from ..templ import templates

router = APIRouter(tags=["receipts"])


def normalize_pixels(path: Path) -> None:
    """Bounding boxes are normalized 0..1 against the *rendered* image.
    Browsers rotate EXIF-tagged JPEGs when displaying; OCR models see raw
    pixels. Baking the transpose into the stored file removes that whole class
    of box-misalignment bugs. Raises on corrupt/truncated images."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        fmt = im.format
        if im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGB")
        im.save(path, format=fmt if fmt in ("JPEG", "PNG", "WEBP") else None)


@router.post("/t/{tab_id}/receipts")
async def upload_receipt(
    tab_id: str,
    request: Request,
    image: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    member = await require_member(tab_id, request, session)
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(415, f"unsupported image type {image.content_type}")
    tab = await get_tab_or_404(session, tab_id)
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[image.content_type]
    rid = new_id()
    dest = UPLOAD_DIR / f"{rid}{ext}"
    size = 0
    with dest.open("wb") as f:
        while chunk := await image.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                f.close(); dest.unlink(missing_ok=True)
                raise HTTPException(413, "image too large (max 12 MB)")
            f.write(chunk)
    try:
        normalize_pixels(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(415, "could not decode image")
    receipt = Receipt(id=rid, tab_id=tab.id, uploaded_by=member.username, image_path=dest.name,
                      title=image.filename or "Receipt")
    session.add(receipt)
    await session.commit()

    for ex in ocr.extract_items(dest):  # stub returns [] until the OCR benchmark lands
        session.add(ex.to_item(receipt.id))
    await session.commit()
    await recompute_status(session, receipt)
    return RedirectResponse(f"/r/{receipt.id}", 303)


@router.get("/r/{receipt_id}")
async def receipt_page(receipt_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    tab = await get_tab_or_404(session, receipt.tab_id)
    from ..deps import get_who
    who = get_who(request) or ""
    members = list(await session.scalars(select(TabMember).where(TabMember.tab_id == tab.id)))
    items = await get_receipt_items(session, receipt.id)
    colors = {m.username: m.color for m in members}
    return templates.TemplateResponse(request, "receipt.html", {
        "tab": tab, "receipt": receipt, "items": items, "members": members, "who": who,
        "colors": colors,
    })


@router.post("/r/{receipt_id}/items")
async def create_item(receipt_id: str, label: str = Form(...), price: str = Form(...),
                      request: Request = None, session: AsyncSession = Depends(get_session)):
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    await require_member(receipt.tab_id, request, session)
    try:
        cents = round(float(price.replace(",", ".")) * 100)
    except ValueError:
        raise HTTPException(422, "bad price")
    await add_item(session, receipt, label.strip()[:120], cents)
    return RedirectResponse(f"/r/{receipt_id}", 303)


@router.post("/r/{receipt_id}/ops")
async def http_op(receipt_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    """htmx/fetch fallback for the same ops the WS channel carries."""
    body = await request.json()
    tab = await _tab_for_receipt(session, receipt_id)
    actor = body.get("actor") or ""
    from ..deps import require_who
    who = await require_who(request)
    if actor and actor != who:
        raise HTTPException(403, "cannot act for others via HTTP")
    await require_member(tab.id, request, session)
    event = await apply_op(session, tab, receipt_id, {**body, "actor": who})
    from ..realtime import hub
    await hub.broadcast(receipt_id, event)
    return event


async def _tab_for_receipt(session: AsyncSession, receipt_id: str):
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    return await get_tab_or_404(session, receipt.tab_id)
