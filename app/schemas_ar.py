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


class PaymentApplicationOut(BaseModel):
    model_config = {"from_attributes": True}

    invoice_number: str | None
    amount_applied: Decimal
    match_kind: str
    confidence: float | None
    posted_at: datetime | None
    approved_by: str | None


class BankPaymentOut(BaseModel):
    """A bank payment with its proposed or posted applications."""

    model_config = {"from_attributes": True}

    payment_ref: str
    payment_date: date
    amount: Decimal
    currency: str
    memo: str | None
    payer_name_raw: str | None
    status: str
    confidence: float | None
    match_details: dict | None
    applications: list[PaymentApplicationOut]


class RejectIn(BaseModel):
    reason: str = ""


class ApplicationIn(BaseModel):
    invoice_number: str
    amount: str


class EditIn(BaseModel):
    applications: list[ApplicationIn]


class AgingRowOut(BaseModel):
    model_config = {"from_attributes": True}

    customer_account: str
    customer_name: str
    invoice_number: str
    invoice_date: date
    due_date: date
    original_total: Decimal
    balance: Decimal
    days_past_due: int
    bucket: str


class BucketTotalOut(BaseModel):
    model_config = {"from_attributes": True}

    count: int
    balance: Decimal


class CustomerAgingOut(BaseModel):
    model_config = {"from_attributes": True}

    customer_account: str
    customer_name: str
    buckets: dict[str, Decimal]
    total: Decimal


class AgingReportOut(BaseModel):
    """AR aging as of one date. Read-only: building it changes nothing."""

    model_config = {"from_attributes": True}

    as_of: date
    rows: list[AgingRowOut]
    bucket_totals: dict[str, BucketTotalOut]
    customers: list[CustomerAgingOut]
    total: Decimal


class RiskOut(BaseModel):
    """A customer's late-payment risk. Score and level come only from the rules."""

    model_config = {"from_attributes": True}

    customer_account: str
    customer_name: str
    as_of: date
    score: int
    level: str
    rule_codes: list[str]
    explanation: str
