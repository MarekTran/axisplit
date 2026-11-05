"""Exact-photo duplicate detection, without a database migration."""

import hashlib
from pathlib import Path

from .config import UPLOAD_DIR
from .models import Receipt


def image_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as image:
        return hashlib.file_digest(image, "sha256").hexdigest()


def duplicate_receipts(receipts: list[Receipt]) -> dict[str, str]:
    """Flag every matching copy; never imply the earlier, possibly empty one is better."""
    groups: dict[str, list[str]] = {}
    for receipt in receipts:
        digest = image_digest(UPLOAD_DIR / receipt.image_path)
        if digest is not None:
            groups.setdefault(digest, []).append(receipt.id)
    duplicates: dict[str, str] = {}
    for copies in groups.values():
        if len(copies) > 1:
            for index, receipt_id in enumerate(copies):
                duplicates[receipt_id] = copies[(index + 1) % len(copies)]
    return duplicates
