"""Duplicate detection. Duplicates are saved and flagged, never silently dropped.

Exact: same normalized vendor + same normalized invoice number.
Soft:  same normalized vendor + same total + invoice_date within 3 days
       + different invoice number.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invoice
from app.schemas import ValidationFlag
from app.services.normalize import normalize
from app.services.validation import flag

SOFT_DUPLICATE_DAYS = 3


def _is_exact(a: Invoice, b: Invoice) -> bool:
    return bool(normalize(a.invoice_number)) and normalize(a.invoice_number) == normalize(
        b.invoice_number
    )


def _is_soft(a: Invoice, b: Invoice) -> bool:
    if a.total is None or b.total is None or not a.invoice_date or not b.invoice_date:
        return False
    close_dates = abs((a.invoice_date - b.invoice_date).days) <= SOFT_DUPLICATE_DAYS
    return a.total == b.total and close_dates and not _is_exact(a, b)


def check_duplicate(session: Session, invoice: Invoice) -> list[ValidationFlag]:
    """Compare against earlier invoices; set duplicate fields and return any flag."""
    vendor = normalize(invoice.vendor_name)
    if not vendor:
        return []

    earlier = session.scalars(
        select(Invoice)
        .where(Invoice.id != invoice.id, Invoice.vendor_name.is_not(None))
        .order_by(Invoice.created_at)
    ).all()
    same_vendor = [other for other in earlier if normalize(other.vendor_name) == vendor]

    for kind, matches in (("exact", _is_exact), ("soft", _is_soft)):
        original = next((other for other in same_vendor if matches(invoice, other)), None)
        if original:
            invoice.is_duplicate = True
            invoice.duplicate_kind = kind
            invoice.duplicate_of_id = original.id
            return [
                flag(
                    f"DUPLICATE_{kind.upper()}",
                    f"Possible {kind} duplicate of invoice {original.invoice_number} "
                    f"({original.filename}).",
                    "invoice_number",
                )
            ]
    return []
