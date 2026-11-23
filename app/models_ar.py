"""Accounts receivable tables (docs/specs/2026-11-21-ar-design.md, section 5).

Kept apart from the AP tables in app/models.py so AP behaviour and tests are untouched.
Money is Decimal; ids are UUID strings; timestamps are UTC.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, Money, Quantity, new_id, utcnow


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255))
    account_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    billing_email: Mapped[str | None] = mapped_column(String(255))  # .example domains only
    payment_terms: Mapped[str] = mapped_column(String(50), default="Net 30")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SalesOrder(Base):
    __tablename__ = "sales_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    order_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(20), default="fulfilled")
    expected_total: Mapped[Decimal | None] = mapped_column(Money)

    customer: Mapped[Customer] = relationship()
    line_items: Mapped[list["SalesOrderLineItem"]] = relationship(cascade="all, delete-orphan")


class SalesOrderLineItem(Base):
    __tablename__ = "sales_order_line_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal | None] = mapped_column(Money)


class ARInvoice(Base):
    __tablename__ = "ar_invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    # Unique: one invoice per order, so re-running never double-invoices.
    sales_order_id: Mapped[str | None] = mapped_column(ForeignKey("sales_orders.id"), unique=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)  # null when terms are unknown
    payment_terms: Mapped[str | None] = mapped_column(String(50))  # terms at issue time
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    # Never coerce a missing amount to 0: these stay NULL when unknown.
    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    tax: Mapped[Decimal | None] = mapped_column(Money)
    total: Mapped[Decimal | None] = mapped_column(Money)
    amount_paid: Mapped[Decimal] = mapped_column(Money, default=Decimal("0.00"))
    balance: Mapped[Decimal | None] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft|open|partial|paid|void
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    customer: Mapped[Customer] = relationship()
    sales_order: Mapped[SalesOrder | None] = relationship()
    line_items: Mapped[list["ARInvoiceLineItem"]] = relationship(cascade="all, delete-orphan")

    @property
    def customer_name(self) -> str:
        return self.customer.name

    @property
    def customer_account(self) -> str:
        return self.customer.account_number

    @property
    def sales_order_number(self) -> str | None:
        return self.sales_order.order_number if self.sales_order else None


class ARInvoiceLineItem(Base):
    __tablename__ = "ar_invoice_line_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ar_invoice_id: Mapped[str] = mapped_column(ForeignKey("ar_invoices.id"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal | None] = mapped_column(Money)
