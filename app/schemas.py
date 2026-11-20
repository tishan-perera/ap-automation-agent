"""Pydantic schemas for invoice extraction, plus basic deterministic checks.

Missing amounts stay None. Nothing here ever fills in a number that was not extracted.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

LINE_MATH_TOLERANCE = Decimal("0.01")


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


def validate_extraction(extraction: InvoiceExtraction) -> list[ValidationFlag]:
    """Return flags for missing totals and line items that do not add up to the total."""
    flags: list[ValidationFlag] = []

    if extraction.total is None:
        flags.append(
            ValidationFlag(
                code="MISSING_TOTAL",
                message="Invoice total is missing or unreadable.",
                field="total",
            )
        )
        return flags

    amounts = [item.amount for item in extraction.line_items]
    if amounts and all(amount is not None for amount in amounts):
        expected = sum(amounts, Decimal(0)) + (extraction.tax or Decimal(0))
        if abs(expected - extraction.total) > LINE_MATH_TOLERANCE:
            flags.append(
                ValidationFlag(
                    code="LINE_MATH_MISMATCH",
                    message=(
                        f"Line items plus tax ({expected}) do not match total ({extraction.total})."
                    ),
                    field="total",
                )
            )

    return flags
