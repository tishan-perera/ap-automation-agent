"""Deterministic, offline parser for the synthetic reportlab invoice template.

It reads labels like "Invoice Number:" with regexes. If a label is missing or the text is
garbled, the field stays None. It never guesses and never makes network calls.
"""

import re
from datetime import date
from decimal import Decimal

from app.schemas import InvoiceExtraction, LineItem
from app.services.llm.base import BaseProvider

MONEY = r"\$([\d,]+\.\d{2})"
LINE_ITEM = re.compile(rf"^(.+?) (\d+(?:\.\d+)?) {MONEY} {MONEY}$")


def _field(text: str, label: str) -> str | None:
    match = re.search(rf"^{label}:\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def _money(text: str, label: str) -> Decimal | None:
    match = re.search(rf"^{label}:\s*{MONEY}\s*$", text, re.MULTILINE)
    return Decimal(match.group(1).replace(",", "")) if match else None


def _date(text: str, label: str) -> date | None:
    value = _field(text, label)
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _line_items(text: str) -> list[LineItem]:
    items = []
    for line in text.splitlines():
        match = LINE_ITEM.match(line.strip())
        if match:
            description, qty, unit_price, amount = match.groups()
            items.append(
                LineItem(
                    description=description,
                    quantity=Decimal(qty),
                    unit_price=Decimal(unit_price.replace(",", "")),
                    amount=Decimal(amount.replace(",", "")),
                )
            )
    return items


class MockProvider(BaseProvider):
    name = "mock"

    def extract(self, text: str) -> InvoiceExtraction:
        self.last_token_usage = 0
        extraction = InvoiceExtraction(
            vendor_name=_field(text, "Vendor"),
            invoice_number=_field(text, "Invoice Number"),
            invoice_date=_date(text, "Invoice Date"),
            due_date=_date(text, "Due Date"),
            po_number=_field(text, "PO Number"),
            subtotal=_money(text, "Subtotal"),
            tax=_money(text, "Tax"),
            total=_money(text, "Total"),
            line_items=_line_items(text),
        )
        if extraction.vendor_name is None and extraction.invoice_number is None:
            extraction.extraction_notes = "No invoice labels found; text looks unreadable."
        return extraction
