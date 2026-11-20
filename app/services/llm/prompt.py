"""Shared prompt and response schema for the real LLM providers.

The model returns amounts and dates as strings, which every provider's structured-output
mode supports. We convert them in code; anything that does not parse becomes None.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from app.schemas import InvoiceExtraction, LineItem

SYSTEM_PROMPT = (
    "You extract fields from accounts payable invoice text. "
    "Only return values that are printed in the text. "
    "If a number is missing or unreadable, return null. Never guess, estimate, or calculate "
    "an amount that is not printed. "
    "Write dates as YYYY-MM-DD and amounts as plain decimals like 1234.56, "
    "with no currency symbol or commas."
)


class LLMLineItem(BaseModel):
    description: str | None
    quantity: str | None
    unit_price: str | None
    amount: str | None


class LLMExtraction(BaseModel):
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: str | None
    due_date: str | None
    po_number: str | None
    currency: str | None
    subtotal: str | None
    tax: str | None
    total: str | None
    line_items: list[LLMLineItem]
    extraction_notes: str | None


def user_prompt(text: str) -> str:
    return f"Extract the invoice fields from this text:\n\n<invoice_text>\n{text}\n</invoice_text>"


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value.replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value.strip()) if value else None
    except ValueError:
        return None


def to_extraction(raw: LLMExtraction) -> InvoiceExtraction:
    return InvoiceExtraction(
        vendor_name=raw.vendor_name,
        invoice_number=raw.invoice_number,
        invoice_date=_date(raw.invoice_date),
        due_date=_date(raw.due_date),
        po_number=raw.po_number,
        currency=raw.currency or "USD",
        subtotal=_decimal(raw.subtotal),
        tax=_decimal(raw.tax),
        total=_decimal(raw.total),
        line_items=[
            LineItem(
                description=item.description,
                quantity=_decimal(item.quantity),
                unit_price=_decimal(item.unit_price),
                amount=_decimal(item.amount),
            )
            for item in raw.line_items
        ],
        extraction_notes=raw.extraction_notes,
    )
