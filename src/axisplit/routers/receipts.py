
import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ocr
from ..config import ALLOWED_IMAGE_TYPES, MAX_UPLOAD_BYTES, UPLOAD_DIR, new_id
from ..db import get_session
from ..deps import get_tab_or_404, get_who, require_member
from ..images import image_digest
from ..models import Confirmation, Receipt, ReceiptStatus, TabMember
from ..ops import add_item, apply_op, get_receipt_items, recompute_status
from ..templ import templates
from ..viewmodels import receipt_summary

router = APIRouter(tags=["receipts"])
logger = logging.getLogger(__name__)


async def detect_receipt(session: AsyncSession, receipt: Receipt) -> str:
    try:
        extracted = await asyncio.to_thread(ocr.extract_items, UPLOAD_DIR / receipt.image_path)
    except Exception:
        logger.exception("Receipt OCR failed for %s", receipt.id)
        return "failed"
    for ex in extracted:
        session.add(ex.to_item(receipt.id))
    await session.commit()
    await recompute_status(session, receipt)
    return "ok" if extracted else "empty"


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
    # Exact-photo duplicates reuse the existing receipt instead of doubling the bill.
    digest = await asyncio.to_thread(image_digest, dest)
    previous = list(await session.scalars(select(Receipt).where(
        Receipt.tab_id == tab_id, Receipt.status != ReceiptStatus.REMOVED,
    ).order_by(Receipt.created_at)))
    for existing in previous:
        if digest == await asyncio.to_thread(image_digest, UPLOAD_DIR / existing.image_path):
            dest.unlink(missing_ok=True)
            return RedirectResponse(f"/r/{existing.id}?duplicate=1", 303)
    receipt = Receipt(id=rid, tab_id=tab.id, uploaded_by=member.username, image_path=dest.name,
                      title=image.filename or "Receipt")
    session.add(receipt)
    await session.commit()

    outcome = await detect_receipt(session, receipt)
    return RedirectResponse(f"/r/{receipt.id}?ocr={outcome}", 303)


@router.post("/r/{receipt_id}/detect")
async def retry_detection(receipt_id: str, request: Request,
                          session: AsyncSession = Depends(get_session)):
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    await require_member(receipt.tab_id, request, session)
    if receipt.status == ReceiptStatus.REMOVED:
        raise HTTPException(410, "Restore this receipt before detecting items.")
    if await get_receipt_items(session, receipt_id):
        raise HTTPException(409, "Receipt already has items; refusing to overwrite assignments")
    outcome = await detect_receipt(session, receipt)
    return RedirectResponse(f"/r/{receipt.id}?ocr={outcome}", 303)


@router.get("/r/{receipt_id}")
async def receipt_page(receipt_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    tab = await get_tab_or_404(session, receipt.tab_id)
    if receipt.status == ReceiptStatus.REMOVED:
        await require_member(receipt.tab_id, request, session)
        return RedirectResponse(f"/t/{receipt.tab_id}?removed={receipt.id}", 303)
    who = get_who(request) or ""
    members = list(await session.scalars(select(TabMember).where(TabMember.tab_id == tab.id)))
    items = await get_receipt_items(session, receipt.id)
    colors = {m.username: m.color for m in members}
    confirmed = set(await session.scalars(
        select(Confirmation.username).where(Confirmation.receipt_id == receipt_id)
    ))
    return templates.TemplateResponse(request, "receipt.html", {
        "tab": tab, "receipt": receipt, "items": items, "members": members, "who": who,
        "colors": colors, "summary": receipt_summary(items, confirmed),
    })


@router.get("/r/{receipt_id}/review")
async def review_fragment(receipt_id: str, request: Request,
                          session: AsyncSession = Depends(get_session)):
    tab = await _tab_for_receipt(session, receipt_id)
    member = await require_member(tab.id, request, session)
    receipt = await session.get(Receipt, receipt_id)
    if receipt.status == ReceiptStatus.REMOVED:
        raise HTTPException(410, "Receipt removed")
    items = await get_receipt_items(session, receipt_id)
    members = list(await session.scalars(select(TabMember).where(TabMember.tab_id == tab.id)))
    confirmed = set(await session.scalars(
        select(Confirmation.username).where(Confirmation.receipt_id == receipt_id)
    ))
    return templates.TemplateResponse(request, "review.html", {
        "receipt": receipt, "items": items, "members": members, "who": member.username,
        "summary": receipt_summary(items, confirmed),
    })


@router.post("/r/{receipt_id}/remove")
async def remove_receipt(receipt_id: str, request: Request,
                         session: AsyncSession = Depends(get_session)):
    tab = await _tab_for_receipt(session, receipt_id)
    await require_member(tab.id, request, session)
    receipt = await session.get(Receipt, receipt_id)
    receipt.status = ReceiptStatus.REMOVED
    await session.commit()
    from ..realtime import hub
    await hub.broadcast(receipt_id, {"type": "removed"})
    return RedirectResponse(f"/t/{tab.id}?removed={receipt_id}", 303)


@router.post("/r/{receipt_id}/restore")
async def restore_receipt(receipt_id: str, request: Request,
                          session: AsyncSession = Depends(get_session)):
    tab = await _tab_for_receipt(session, receipt_id)
    await require_member(tab.id, request, session)
    receipt = await session.get(Receipt, receipt_id)
    if receipt.status == ReceiptStatus.REMOVED:
        receipt.status = ReceiptStatus.AWAITING_ITEMS
        await session.flush()
        await recompute_status(session, receipt)
        await session.commit()
    return RedirectResponse(f"/t/{tab.id}?restored=1", 303)


@router.post("/r/{receipt_id}/items")
async def create_item(receipt_id: str, label: str = Form(...), price: str = Form(...),
                      x: float | None = Form(None), y: float | None = Form(None),
                      request: Request = None, session: AsyncSession = Depends(get_session)):
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    await require_member(receipt.tab_id, request, session)
    if receipt.status == ReceiptStatus.REMOVED:
        raise HTTPException(410, "Restore this receipt before adding items.")
    try:
        cents = round(float(price.replace(",", ".")) * 100)
    except ValueError:
        raise HTTPException(422, "bad price")
    bbox = None
    if x is not None and y is not None:  # tap-to-add: center a line box on the tap point
        bbox = (x - 0.30, y - 0.035, 0.60, 0.07)
    await add_item(session, receipt, label.strip()[:120], cents, bbox=bbox)
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
    event["actor"] = who
    from ..realtime import hub
    await hub.broadcast(receipt_id, event)
    return event


async def _tab_for_receipt(session: AsyncSession, receipt_id: str):
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    return await get_tab_or_404(session, receipt.tab_id)
