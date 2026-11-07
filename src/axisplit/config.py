import os
import secrets
from pathlib import Path

BASE_DIR = Path(os.environ.get("AXISPLIT_DATA", Path(__file__).resolve().parents[2] / "data"))
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# A new file: databases from the tab version (axisplit.db) are left as they are, not converted.
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'splits.db'}")

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


def new_id(n: int = 8) -> str:
    return secrets.token_urlsafe(n)[:n]
