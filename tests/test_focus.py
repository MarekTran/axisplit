"""The focus step: a zoom hint for where the receipt is, and the photo turned as the uploader turned it."""

import io

from PIL import Image

from axisplit.models import Receipt
from axisplit.routers import receipts

from test_ux import client, photo  # noqa: F401  (client is a fixture)


async def stored_size(client, receipt_id):
    async with client.sessions() as session:
        receipt = await session.get(Receipt, receipt_id)
    with Image.open(receipts.UPLOAD_DIR / receipt.image_path) as im:
        return im.size, im.convert("RGB").getpixel((0, 0))


async def test_the_hint_says_where_the_receipt_text_is(client):
    hint = await client.post("/focus-hint", files={"image": ("hint.jpg", photo(), "image/jpeg")})
    assert hint.json() == {"extent": [.15, .3, .7, .4]}  # the fixture's text extent


async def test_the_upload_is_turned_like_the_focus_step_showed_it(client):
    sideways = await client.post("/receipts", data={"rotation": "90"},
                                 files={"image": ("check.jpg", photo(), "image/jpeg")})
    (w, h), _ = await stored_size(client, sideways.url.path.rsplit("/", 1)[-1])
    assert (w, h) == (400, 200)  # a 200x400 photo, a quarter turn

    black = Image.new("RGB", (200, 400), "black")
    buffer = io.BytesIO()
    black.save(buffer, format="JPEG")
    tilted = await client.post("/receipts", data={"rotation": "-10"},
                               files={"image": ("tilted.jpg", buffer.getvalue(), "image/jpeg")})
    (w, h), corner = await stored_size(client, tilted.url.path.rsplit("/", 1)[-1])
    assert w > 200 and h > 400  # grown to hold the whole photo
    assert min(corner) > 240  # the gained corners are white like paper, not black
