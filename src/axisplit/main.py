from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from .config import UPLOAD_DIR
from .db import engine
from .models import Base
from .routers import auth, receipts, tabs, ws

STATIC_DIR = Path(__file__).resolve().parent / "static"


def add_token_count(conn) -> None:
    """create_all never alters existing tables; databases from before token counts lack the column."""
    if "count" not in {c["name"] for c in inspect(conn).get_columns("token_assignments")}:
        conn.execute(text("ALTER TABLE token_assignments ADD COLUMN count INTEGER NOT NULL DEFAULT 1"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # v1: schema-from-models, alembic later
        await conn.run_sync(add_token_count)
    yield


app = FastAPI(title="axisplit", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

app.include_router(auth.router)
app.include_router(tabs.router)
app.include_router(receipts.router)
app.include_router(ws.router)
