import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tab(Base):
    __tablename__ = "tabs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    invite_secret: Mapped[str] = mapped_column(String, default=lambda: _rand(12))
    created_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list["TabMember"]] = relationship(back_populates="tab", cascade="all, delete-orphan")
    receipts: Mapped[list["Receipt"]] = relationship(back_populates="tab", cascade="all, delete-orphan")


class TabMember(Base):
    __tablename__ = "tab_members"
    tab_id: Mapped[str] = mapped_column(ForeignKey("tabs.id"), primary_key=True)
    username: Mapped[str] = mapped_column(String, primary_key=True)
    color: Mapped[str] = mapped_column(String)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tab: Mapped[Tab] = relationship(back_populates="members")


class ReceiptStatus(str, enum.Enum):
    AWAITING_ITEMS = "awaiting_items"      # no items yet; OCR failed/pending/manual
    AWAITING_TOKENS = "awaiting_tokens"    # items exist, nobody placed tokens
    SPLITTING = "splitting"                # tokens placed, not all confirmed
    PENDING_CONFIRM = "pending_confirm"    # someone confirmed
    SETTLED = "settled"                    # all involved confirmed
    REMOVED = "removed"                    # reversible removal; assignments retained


class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tab_id: Mapped[str] = mapped_column(ForeignKey("tabs.id"))
    uploaded_by: Mapped[str] = mapped_column(String)
    image_path: Mapped[str] = mapped_column(String)  # path under /uploads
    title: Mapped[str] = mapped_column(String, default="Receipt")
    currency: Mapped[str] = mapped_column(String, default="EUR")
    status: Mapped[str] = mapped_column(String, default=ReceiptStatus.AWAITING_ITEMS.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Focus area the uploader drew around the item lines (normalized 0..1); OCR reads only this.
    focus_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    focus_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    focus_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    focus_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Grand total as printed, read by a separate OCR call to cross-check the items.
    printed_total_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tab: Mapped[Tab] = relationship(back_populates="receipts")
    items: Mapped[list["Item"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    confirmations: Mapped[list["Confirmation"]] = relationship(cascade="all, delete-orphan")


class Item(Base):
    """One detected (or manually added) line on a receipt."""

    __tablename__ = "items"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    receipt_id: Mapped[str] = mapped_column(ForeignKey("receipts.id"))
    label: Mapped[str] = mapped_column(String)
    price_cents: Mapped[int] = mapped_column(Integer)
    # normalized bbox, 0..1 relative to image size
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    w: Mapped[float] = mapped_column(Float, default=1.0)
    h: Mapped[float] = mapped_column(Float, default=0.08)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    receipt: Mapped[Receipt] = relationship(back_populates="items")
    tokens: Mapped[list["TokenAssignment"]] = relationship(cascade="all, delete-orphan")


class TokenAssignment(Base):
    __tablename__ = "token_assignments"
    __table_args__ = (UniqueConstraint("item_id", "username"),)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    username: Mapped[str] = mapped_column(String, primary_key=True)
    # How many of this person's tokens sit on the item; their part of its price is count / total.
    count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Confirmation(Base):
    __tablename__ = "confirmations"
    receipt_id: Mapped[str] = mapped_column(ForeignKey("receipts.id"), primary_key=True)
    username: Mapped[str] = mapped_column(String, primary_key=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _rand(n: int) -> str:
    import secrets

    return secrets.token_urlsafe(n)[:n]
