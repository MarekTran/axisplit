"""The built-in reader served over the reader API (docs/reader-api.md): a template for a reader of
your own, and the way to test the app's http reader end to end.

  uv run uvicorn --app-dir scripts reader_service:app --port 9000
  AXISPLIT_OCR_BACKEND=http AXISPLIT_READER_URL=http://127.0.0.1:9000 uv run uvicorn axisplit.main:app
"""

import asyncio
import json
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile

from axisplit import ocr

app = FastAPI()
reader = ocr.paddle_backend()


async def with_photo(image: UploadFile, fn, *args):
    with tempfile.NamedTemporaryFile(suffix=Path(image.filename or "photo.jpg").suffix) as f:
        f.write(await image.read())
        f.flush()
        return await asyncio.to_thread(fn, Path(f.name), *args)


@app.post("/v1/read")
async def read(image: UploadFile = File(...), area: str | None = Form(None)):
    reading = await with_photo(image, reader.read, json.loads(area) if area else None)
    return {
        "items": [{"label": i.label, "price_cents": i.price_cents, "box": [i.x, i.y, i.w, i.h],
                   **({"quad": i.quad} if i.quad else {})} for i in reading.items],
        "total_cents": reading.total_cents, "currency": reading.currency, "extent": reading.extent,
    }


@app.post("/v1/locate")
async def locate(image: UploadFile = File(...)):
    return {"extent": await with_photo(image, reader.locate)}
