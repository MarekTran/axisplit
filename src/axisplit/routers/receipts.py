
import asyncio
import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from PIL import Image, ImageOps
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import ocr
from ..colors import free_color
from ..config import ALLOWED_IMAGE_TYPES, MAX_UPLOAD_BYTES, UPLOAD_DIR, new_id
from ..db import get_session
from ..deps import get_receipt_or_404, recent_ids, remember
from ..models import Item, Person, Receipt, TokenAssignment
from ..ops import add_item, apply_op, clamp_box, get_people, get_receipt_items, parse_cents, recompute_status
from ..templ import templates
from ..viewmodels import CURRENCIES, photo_view, receipt_summary

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
        code = await asyncio.to_thread(ocr.currency, path)
    except Exception:
        logger.exception("Receipt text extent or currency failed for %s", receipt.id)
    else:
        if extent:
            receipt.view_x, receipt.view_y, receipt.view_w, receipt.view_h = extent
        if code in CURRENCIES:  # else it keeps this device's last receipt's (see upload_receipt)
            receipt.currency = code
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


@router.post("/receipts")
async def upload_receipt(
    request: Request,
    image: UploadFile = File(...),
    focus_x: float | None = Form(None), focus_y: float | None = Form(None),
    focus_w: float | None = Form(None), focus_h: float | None = Form(None),
    focus_quad: str | None = Form(None),
    rotation: float = Form(0.0),
    session: AsyncSession = Depends(get_session),
):
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(415, f"unsupported image type {image.content_type}")
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
    quad = clean_quad(focus_quad)
    focus = clean_focus(*quad_bounds(quad)) if quad else clean_focus(focus_x, focus_y, focus_w, focus_h)
    # Unread from the photo, the currency is most likely the one of the last receipt on this device.
    last = recent_ids(request)[:1]
    previous = await session.get(Receipt, last[0]) if last else None
    receipt = Receipt(id=rid, image_path=dest.name, title=image.filename or "Receipt",
                      currency=previous.currency if previous else "EUR")
    if focus:
        receipt.focus_x, receipt.focus_y, receipt.focus_w, receipt.focus_h = focus
        receipt.focus_quad = json.dumps(quad) if quad else None
    session.add(receipt)
    await session.commit()

    outcome = await detect_receipt(session, receipt)
    return RedirectResponse(f"/r/{receipt.id}?ocr={outcome}", 303)


@router.post("/focus-hint")
async def focus_hint(image: UploadFile = File(...)):
    """Where the receipt is on a photo still in the focus step (a small copy), so that step can zoom
    to it. Its text's extent held every item on all 46 test receipts. None when unread."""
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
async def retry_detection(receipt_id: str, session: AsyncSession = Depends(get_session)):
    receipt = await get_receipt_or_404(session, receipt_id)
    if await get_receipt_items(session, receipt_id):
        raise HTTPException(409, "Receipt already has items; refusing to overwrite assignments")
    outcome = await detect_receipt(session, receipt)
    return RedirectResponse(f"/r/{receipt.id}?ocr={outcome}", 303)


@router.get("/r/{receipt_id}")
async def receipt_page(receipt_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    receipt = await get_receipt_or_404(session, receipt_id)
    items = await get_receipt_items(session, receipt.id)
    people = await get_people(session, receipt.id)
    try:
        with Image.open(UPLOAD_DIR / receipt.image_path) as im:  # reads the header only
            view = photo_view(receipt, items, im.size)
    except OSError:
        view = None
    response = templates.TemplateResponse(request, "receipt.html", {
        "receipt": receipt, "items": items, "people": people, "colors": {p.username: p.color for p in people},
        "summary": receipt_summary(items, people), "view": view,
    })
    remember(response, request, receipt.id)  # listed on this device's home page
    return response


@router.get("/r/{receipt_id}/review")
async def review_fragment(receipt_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    receipt = await get_receipt_or_404(session, receipt_id)
    items = await get_receipt_items(session, receipt_id)
    people = await get_people(session, receipt_id)
    return templates.TemplateResponse(request, "review.html", {
        "receipt": receipt, "items": items, "people": people, "colors": {p.username: p.color for p in people},
        "summary": receipt_summary(items, people),
    })


@router.post("/r/{receipt_id}/people")
async def add_person(receipt_id: str, username: str = Form(...), session: AsyncSession = Depends(get_session)):
    """Anyone with the link adds people by name; each gets a color nobody else on the receipt has."""
    await get_receipt_or_404(session, receipt_id)
    username = username.strip()[:32]
    if not username:
        raise HTTPException(422, "Type a name.")
    people = await get_people(session, receipt_id)
    if username.casefold() in {p.username.casefold() for p in people}:
        raise HTTPException(409, f"{username} is already on this receipt.")
    person = Person(receipt_id=receipt_id, username=username, color=free_color({p.color for p in people}))
    session.add(person)
    await session.commit()
    await _people_changed(receipt_id)
    return {"username": person.username, "color": person.color}


@router.post("/r/{receipt_id}/people/remove")
async def remove_person(receipt_id: str, username: str = Form(...), session: AsyncSession = Depends(get_session)):
    """Takes someone off the receipt with their tokens; the items they were on are split among the rest."""
    receipt = await get_receipt_or_404(session, receipt_id)
    person = await session.get(Person, (receipt_id, username))
    if person is None:
        raise HTTPException(404, f"{username} isn't on this receipt.")
    await session.execute(delete(TokenAssignment).where(
        TokenAssignment.username == username,
        TokenAssignment.item_id.in_(select(Item.id).where(Item.receipt_id == receipt_id))))
    await session.delete(person)
    await session.commit()
    await recompute_status(session, receipt)
    await _people_changed(receipt_id)
    return {"username": username, "removed": True}


async def _people_changed(receipt_id: str) -> None:
    """Peers re-read the review, which carries the people bar."""
    from ..realtime import hub
    await hub.broadcast(receipt_id, {"type": "people"})


@router.post("/r/{receipt_id}/currency")
async def set_currency(receipt_id: str, currency: str = Form(...), session: AsyncSession = Depends(get_session)):
    """Fixes a currency read wrong (or not at all) off the photo. Amounts stay as they are."""
    receipt = await get_receipt_or_404(session, receipt_id)
    if currency not in CURRENCIES:
        raise HTTPException(422, f"unsupported currency {currency!r}")
    receipt.currency = currency
    await session.commit()
    from ..realtime import hub
    await hub.broadcast(receipt_id, {"type": "currency", "currency": currency})  # peers refresh the review
    return RedirectResponse(f"/r/{receipt_id}", 303)


@router.post("/r/{receipt_id}/items")
async def create_item(receipt_id: str, label: str = Form(...), price: str = Form(...),
                      x: float | None = Form(None), y: float | None = Form(None),
                      w: float | None = Form(None), h: float | None = Form(None),
                      session: AsyncSession = Depends(get_session)):
    receipt = await get_receipt_or_404(session, receipt_id)
    try:
        cents = parse_cents(price)
    except ValueError:
        raise HTTPException(422, "bad price")
    bbox = None
    if x is not None and y is not None:  # centered on x, y: a box dragged on the photo, or a tap
        w, h = (w, h) if w and h else (0.60, 0.07)
        bbox = (x - w / 2, y - h / 2, w, h)
    item = await add_item(session, receipt, label.strip()[:120], cents, bbox=bbox)
    await _items_changed(receipt_id)
    return RedirectResponse(f"/r/{receipt_id}?added={item.id}", 303)  # the page offers Undo


@router.post("/r/{receipt_id}/items/{item_id}/box")
async def redraw_box(receipt_id: str, item_id: str, x: float = Form(...), y: float = Form(...),
                     w: float = Form(...), h: float = Form(...), session: AsyncSession = Depends(get_session)):
    """Move an item's box where it is really printed. Geometry only: shares and ticks stay."""
    _, item = await _editable_item(session, receipt_id, item_id)
    box = clamp_box(x - w / 2, y - h / 2, w, h)
    for key, value in box.items():
        setattr(item, key, value)
    await session.commit()
    event = {"type": "box", "item_id": item_id, **box}
    from ..realtime import hub
    await hub.broadcast(receipt_id, event)
    return event


@router.post("/r/{receipt_id}/items/{item_id}")
async def edit_item(receipt_id: str, item_id: str, label: str = Form(...), price: str = Form(...),
                    session: AsyncSession = Depends(get_session)):
    """Fix a misread name or price. A new price changes shares, so the ticks of those on it lapse."""
    receipt, item = await _editable_item(session, receipt_id, item_id)
    label = label.strip()[:120]
    if not label:
        raise HTTPException(422, "Give the item a name.")
    try:
        cents = parse_cents(price)
    except ValueError:
        raise HTTPException(422, "That price isn’t a number.")
    item.label, item.price_cents = label, cents
    await session.commit()
    await recompute_status(session, receipt)
    await _items_changed(receipt_id)
    return {"item_id": item_id, "label": label, "price_cents": cents}


@router.post("/r/{receipt_id}/items/{item_id}/delete")
async def delete_item(receipt_id: str, item_id: str, session: AsyncSession = Depends(get_session)):
    """Take a line that isn't an item (or was added twice) off the split. Undo restores it."""
    return await _set_deleted(session, receipt_id, item_id, True)


@router.post("/r/{receipt_id}/items/{item_id}/restore")
async def restore_item(receipt_id: str, item_id: str, session: AsyncSession = Depends(get_session)):
    return await _set_deleted(session, receipt_id, item_id, False)


async def _set_deleted(session: AsyncSession, receipt_id: str, item_id: str, deleted: bool):
    receipt, item = await _editable_item(session, receipt_id, item_id, deleted=not deleted)
    item.deleted = deleted  # its tokens leave or come back with it
    await session.commit()
    await recompute_status(session, receipt)
    await _items_changed(receipt_id)
    return {"item_id": item_id, "label": item.label, "deleted": deleted}


async def _editable_item(session: AsyncSession, receipt_id: str, item_id: str,
                         deleted: bool = False) -> tuple[Receipt, Item]:
    receipt = await get_receipt_or_404(session, receipt_id)
    item = await session.get(Item, item_id)
    if item is None or item.receipt_id != receipt_id or item.deleted != deleted:
        raise HTTPException(404, "item not found")
    return receipt, item


async def _items_changed(receipt_id: str) -> None:
    """Peers re-read the item list; their photo follows it."""
    from ..realtime import hub
    await hub.broadcast(receipt_id, {"type": "items"})


@router.post("/r/{receipt_id}/ops")
async def http_op(receipt_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    """The same ops the WS channel carries. Anyone with the link places and ticks for anyone."""
    body = await request.json()
    event = await apply_op(session, await get_receipt_or_404(session, receipt_id), body)
    from ..realtime import hub
    await hub.broadcast(receipt_id, event)
    return event
