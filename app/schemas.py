"""Pydantic schemas for invoice extraction and validation flags.

Missing amounts stay None. Nothing here ever fills in a number that was not extracted.
Validation rules live in app/services/validation.py.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class InvoiceExtraction(BaseModel):
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    po_number: str | None = None
    currency: str = "USD"
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    extraction_notes: str | None = None


class ValidationFlag(BaseModel):
    code: str
    message: str
    field: str | None = None


class InvoiceSummary(BaseModel):
    """API response for an invoice in a list."""

    model_config = {"from_attributes": True}

    id: str
    filename: str
    file_hash: str
    status: str
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    po_number: str | None
    total: Decimal | None
    validation_flags: list[ValidationFlag]
    exception_reasons: list[str]
    is_duplicate: bool
    duplicate_kind: str | None
    duplicate_of_id: str | None
    match_type: str | None
    match_status: str | None
    gl_account: str | None
    gl_account_name: str | None
    gl_confidence: float | None
    already_processed: bool = False


class InvoiceLineOut(BaseModel):
    model_config = {"from_attributes": True}

    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


class InvoiceDetail(InvoiceSummary):
    """API response for one invoice, with everything the review screen needs."""

    due_date: date | None
    currency: str
    subtotal: Decimal | None
    tax: Decimal | None
    line_items: list[InvoiceLineOut]
    match_details: dict | None
    gl_reason: str | None
    created_at: datetime
    approved_at: datetime | None
    approved_by: str | None


class FixtureIngestSummary(BaseModel):
    processed: int
    created: int
    invoices: list[InvoiceSummary]
