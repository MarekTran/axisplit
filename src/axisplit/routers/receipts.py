
import asyncio
import json
import logging
import tempfile
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
from ..models import Confirmation, Item, Receipt, ReceiptStatus, TabMember
from ..ops import add_item, apply_op, clamp_box, get_receipt_items, recompute_status
from ..templ import templates
from ..viewmodels import photo_view, receipt_summary

router = APIRouter(tags=["receipts"])
logger = logging.getLogger(__name__)


def clean_focus(x: float | None, y: float | None, w: float | None, h: float | None) -> ocr.Focus | None:
    """The uploader's items area, kept on the photo. Trusted as drawn; ignored if incomplete or tiny."""
    if None in (x, y, w, h):
        return None
    x, y = max(0.0, min(x, 1.0)), max(0.0, min(y, 1.0))
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)
    return (x, y, w, h) if w >= 0.05 and h >= 0.05 else None


def clean_quad(raw: str | None) -> ocr.Quad | None:
    """The area's four corners (JSON [[x, y], ...]), kept on the photo; None if malformed."""
    try:
        points = [(float(x), float(y)) for x, y in json.loads(raw)]
    except (TypeError, ValueError):
        return None
    if len(points) != 4:
        return None
    return [(max(0.0, min(x, 1.0)), max(0.0, min(y, 1.0))) for x, y in points]


def quad_bounds(quad: ocr.Quad) -> tuple[float, float, float, float]:
    xs, ys = [x for x, _ in quad], [y for _, y in quad]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


async def detect_receipt(session: AsyncSession, receipt: Receipt) -> str:
    path = UPLOAD_DIR / receipt.image_path
    focus = clean_focus(receipt.focus_x, receipt.focus_y, receipt.focus_w, receipt.focus_h)
    quad = clean_quad(receipt.focus_quad) if focus else None
    # Two independent reads: the items (inside the focus area) and the printed total (whole photo),
    # so the total can check the items. A failed total never blocks the items.
    extracted, total = await asyncio.gather(
        asyncio.to_thread(ocr.extract_items, path, focus, quad),
        asyncio.to_thread(ocr.extract_total, path),
        return_exceptions=True,
    )
    if isinstance(total, Exception):
        logger.error("Receipt total OCR failed for %s", receipt.id, exc_info=total)
    else:
        receipt.printed_total_cents = total
    try:
        extent = await asyncio.to_thread(ocr.text_extent, path)
    except Exception:
        logger.exception("Receipt text extent failed for %s", receipt.id)
    else:
        if extent:
            receipt.view_x, receipt.view_y, receipt.view_w, receipt.view_h = extent
    if isinstance(extracted, Exception):
        logger.error("Receipt OCR failed for %s", receipt.id, exc_info=extracted)
        await session.commit()
        return "failed"
    for ex in extracted:
        session.add(ex.to_item(receipt.id))
    await session.commit()
    await recompute_status(session, receipt)
    return "ok" if extracted else "empty"


def normalize_pixels(path: Path, rotation: float = 0.0) -> None:
    """Bounding boxes are normalized 0..1 against the *rendered* image.
    Browsers rotate EXIF-tagged JPEGs when displaying; OCR models see raw
    pixels. Baking the transpose into the stored file removes that whole class
    of box-misalignment bugs. Raises on corrupt/truncated images.
    rotation: degrees clockwise the uploader turned the photo; the canvas grows to hold all of it,
    as the focus step showed it, and the corners it gains are white like the paper."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if rotation % 360:
            im = im.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
        fmt = im.format
        if im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGB")
        im.save(path, format=fmt if fmt in ("JPEG", "PNG", "WEBP") else None)


@router.post("/t/{tab_id}/receipts")
async def upload_receipt(
    tab_id: str,
    request: Request,
    image: UploadFile = File(...),
    focus_x: float | None = Form(None), focus_y: float | None = Form(None),
    focus_w: float | None = Form(None), focus_h: float | None = Form(None),
    focus_quad: str | None = Form(None),
    rotation: float = Form(0.0),
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
        normalize_pixels(dest, max(-360.0, min(rotation, 360.0)))
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
    quad = clean_quad(focus_quad)
    focus = clean_focus(*quad_bounds(quad)) if quad else clean_focus(focus_x, focus_y, focus_w, focus_h)
    receipt = Receipt(id=rid, tab_id=tab.id, uploaded_by=member.username, image_path=dest.name,
                      title=image.filename or "Receipt")
    if focus:
        receipt.focus_x, receipt.focus_y, receipt.focus_w, receipt.focus_h = focus
        receipt.focus_quad = json.dumps(quad) if quad else None
    session.add(receipt)
    await session.commit()

    outcome = await detect_receipt(session, receipt)
    return RedirectResponse(f"/r/{receipt.id}?ocr={outcome}", 303)


@router.post("/t/{tab_id}/focus-hint")
async def focus_hint(tab_id: str, request: Request, image: UploadFile = File(...),
                     session: AsyncSession = Depends(get_session)):
    """Where the receipt is on a photo still in the focus step (a small copy), so that step can zoom
    to it. Its text's extent held every item on all 46 test receipts. None when unread."""
    await require_member(tab_id, request, session)
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "image too large")
    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        f.write(data)
        f.flush()
        try:
            extent = await asyncio.to_thread(ocr.text_extent, Path(f.name))
        except Exception:
            logger.exception("Focus hint failed")
            extent = None
    return {"extent": [round(v, 4) for v in extent] if extent else None}


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
    try:
        with Image.open(UPLOAD_DIR / receipt.image_path) as im:  # reads the header only
            view = photo_view(receipt, items, im.size)
    except OSError:
        view = None
    return templates.TemplateResponse(request, "receipt.html", {
        "tab": tab, "receipt": receipt, "items": items, "members": members, "who": who,
        "colors": colors, "summary": receipt_summary(items, confirmed), "view": view,
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
        "colors": {m.username: m.color for m in members}, "summary": receipt_summary(items, confirmed),
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
                      w: float | None = Form(None), h: float | None = Form(None),
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
    if x is not None and y is not None:  # centered on x, y: a box dragged on the photo, or a tap
        w, h = (w, h) if w and h else (0.60, 0.07)
        bbox = (x - w / 2, y - h / 2, w, h)
    await add_item(session, receipt, label.strip()[:120], cents, bbox=bbox)
    return RedirectResponse(f"/r/{receipt_id}", 303)


@router.post("/r/{receipt_id}/items/{item_id}/box")
async def redraw_box(receipt_id: str, item_id: str, x: float = Form(...), y: float = Form(...),
                     w: float = Form(...), h: float = Form(...),
                     request: Request = None, session: AsyncSession = Depends(get_session)):
    """Move an item's box where it is really printed. Geometry only: shares and confirmations stay."""
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    await require_member(receipt.tab_id, request, session)
    if receipt.status == ReceiptStatus.REMOVED:
        raise HTTPException(410, "Restore this receipt before editing it.")
    item = await session.get(Item, item_id)
    if item is None or item.receipt_id != receipt_id:
        raise HTTPException(404, "item not found")
    box = clamp_box(x - w / 2, y - h / 2, w, h)
    for key, value in box.items():
        setattr(item, key, value)
    await session.commit()
    event = {"type": "box", "item_id": item_id, **box}
    from ..realtime import hub
    await hub.broadcast(receipt_id, event)
    return event


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
