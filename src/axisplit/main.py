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


# create_all never alters existing tables, so columns added after a table first shipped are added here.
ADDED_COLUMNS = [
    ("token_assignments", "count", "INTEGER NOT NULL DEFAULT 1"),
    ("token_assignments", "spot", "FLOAT"),
    ("receipts", "focus_x", "FLOAT"),
    ("receipts", "focus_y", "FLOAT"),
    ("receipts", "focus_w", "FLOAT"),
    ("receipts", "focus_h", "FLOAT"),
    ("receipts", "printed_total_cents", "INTEGER"),
]


def add_missing_columns(conn) -> None:
    inspector = inspect(conn)
    for table, column, ddl in ADDED_COLUMNS:
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # v1: schema-from-models, alembic later
        await conn.run_sync(add_missing_columns)
    yield


app = FastAPI(title="axisplit", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

app.include_router(auth.router)
app.include_router(tabs.router)
app.include_router(receipts.router)
app.include_router(ws.router)
