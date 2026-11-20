"""Pydantic schemas for invoice extraction and validation flags.

Missing amounts stay None. Nothing here ever fills in a number that was not extracted.
Validation rules live in app/services/validation.py.
"""

from datetime import date
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
    """API response for an ingested invoice."""

    model_config = {"from_attributes": True}

    id: str
    filename: str
    file_hash: str
    status: str
    vendor_name: str | None
    invoice_number: str | None
    po_number: str | None
    total: Decimal | None
    validation_flags: list[ValidationFlag]
    already_processed: bool = False


class FixtureIngestSummary(BaseModel):
    processed: int
    created: int
    invoices: list[InvoiceSummary]
