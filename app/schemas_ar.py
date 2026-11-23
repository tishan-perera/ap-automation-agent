"""JSON shapes for AR API responses. Missing amounts stay null, never 0."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class ARInvoiceLineOut(BaseModel):
    model_config = {"from_attributes": True}

    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


class ARInvoiceSummary(BaseModel):
    """One AR invoice in a list."""

    model_config = {"from_attributes": True}

    invoice_number: str
    customer_account: str
    customer_name: str
    sales_order_number: str | None
    invoice_date: date
    due_date: date | None
    payment_terms: str | None
    currency: str
    total: Decimal | None
    balance: Decimal | None
    status: str


class ARInvoiceDetail(ARInvoiceSummary):
    """One AR invoice with lines and every amount (design spec section 5)."""

    subtotal: Decimal | None
    tax: Decimal | None
    amount_paid: Decimal
    line_items: list[ARInvoiceLineOut]
    pdf_path: str | None
    created_at: datetime
    updated_at: datetime
