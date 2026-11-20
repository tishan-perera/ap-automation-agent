"""SQLAlchemy 2.x table definitions. Names follow docs/specs/design.md section 5."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Money = Numeric(14, 2)
Quantity = Numeric(14, 4)


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="needs_review")

    vendor_name: Mapped[str | None] = mapped_column(String(255))
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    po_number: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Never coerce a missing amount to 0: these stay NULL when unknown.
    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    tax: Mapped[Decimal | None] = mapped_column(Money)
    total: Mapped[Decimal | None] = mapped_column(Money)

    raw_text: Mapped[str | None] = mapped_column(Text)
    extracted_json: Mapped[dict | None] = mapped_column(JSON)
    validation_flags: Mapped[list] = mapped_column(JSON, default=list)
    exception_reasons: Mapped[list] = mapped_column(JSON, default=list)

    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of_id: Mapped[str | None] = mapped_column(String(36))
    duplicate_kind: Mapped[str | None] = mapped_column(String(10))

    match_type: Mapped[str | None] = mapped_column(String(20))
    match_status: Mapped[str | None] = mapped_column(String(30))
    match_details: Mapped[dict | None] = mapped_column(JSON)

    gl_account: Mapped[str | None] = mapped_column(String(10))
    gl_account_name: Mapped[str | None] = mapped_column(String(100))
    gl_confidence: Mapped[float | None] = mapped_column(Float)
    gl_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(100))

    line_items: Mapped[list["InvoiceLineItem"]] = relationship(cascade="all, delete-orphan")


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal | None] = mapped_column(Money)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    po_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    vendor_name: Mapped[str] = mapped_column(String(255))
    po_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(10), default="open")
    expected_total: Mapped[Decimal | None] = mapped_column(Money)


class POLineItem(Base):
    __tablename__ = "po_line_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal | None] = mapped_column(Money)


class ReceivingRecord(Base):
    __tablename__ = "receiving_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    po_number: Mapped[str] = mapped_column(String(100), index=True)
    received_date: Mapped[date | None] = mapped_column(Date)
    packing_slip_number: Mapped[str | None] = mapped_column(String(100))


class ReceivingLineItem(Base):
    __tablename__ = "receiving_line_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    receiving_id: Mapped[str] = mapped_column(ForeignKey("receiving_records.id"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    quantity_received: Mapped[Decimal | None] = mapped_column(Quantity)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str | None] = mapped_column(String(36), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)


class ProcessedFile(Base):
    __tablename__ = "processed_files"

    file_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(String(36))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    """Non-secret settings only (for example llm_provider). Never store API keys here."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
