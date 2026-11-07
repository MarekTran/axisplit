import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ReceiptStatus(str, enum.Enum):
    AWAITING_ITEMS = "awaiting_items"      # no items yet; OCR failed/pending/manual
    AWAITING_TOKENS = "awaiting_tokens"    # items exist, nobody placed tokens
    SPLITTING = "splitting"                # tokens placed, some items still unassigned
    PENDING_CONFIRM = "pending_confirm"    # every item assigned; not everyone ticked off yet
    SETTLED = "settled"                    # everyone with a share is ticked off at that share


class Receipt(Base):
    """One split. Its id is the link: anyone who has it can do everything on it."""

    __tablename__ = "receipts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
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
    # Its four corners as JSON [[x, y], ...]: dragged onto a tilted receipt, the area is no rectangle.
    focus_quad: Mapped[str | None] = mapped_column(String, nullable=True)
    # Grand total as printed, read by a separate OCR call to cross-check the items.
    printed_total_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Where OCR found text on the whole photo (normalized 0..1): the board shows this part, the receipt.
    view_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_h: Mapped[float | None] = mapped_column(Float, nullable=True)

    items: Mapped[list["Item"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    people: Mapped[list["Person"]] = relationship(cascade="all, delete-orphan", order_by="Person.added_at")


class Person(Base):
    """Someone splitting a receipt, added by name on it. Their tokens carry the same name."""

    __tablename__ = "people"
    receipt_id: Mapped[str] = mapped_column(ForeignKey("receipts.id"), primary_key=True)
    username: Mapped[str] = mapped_column(String, primary_key=True)
    color: Mapped[str] = mapped_column(String)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Ticked off: the share they agreed to when asked. The tick holds while their share is still this,
    # so a change that moves their amount unticks only them, and undoing it ticks them again.
    agreed_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
    # Deleted from the receipt, kept with its tokens so Undo brings it back as it was.
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    receipt: Mapped[Receipt] = relationship(back_populates="items")
    tokens: Mapped[list["TokenAssignment"]] = relationship(cascade="all, delete-orphan")


class TokenAssignment(Base):
    __tablename__ = "token_assignments"
    __table_args__ = (UniqueConstraint("item_id", "username"),)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    username: Mapped[str] = mapped_column(String, primary_key=True)
    # How many of this person's tokens sit on the item; their part of its price is count / total.
    count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # Where along the item's box this person's token sits, 0..1 from its left: where they last tapped.
    # None (placed from the item list) keeps it at the box's right end.
    spot: Mapped[float | None] = mapped_column(Float, nullable=True)
