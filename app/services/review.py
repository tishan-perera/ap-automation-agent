"""Human review actions: approve, reject, and edit. Every action writes an audit event.

Only invoices in needs_review can be changed. Approved invoices are locked in v1.
"""

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import AuditEvent, Invoice
from app.schemas import InvoiceExtraction, LineItem
from app.services.enrich import enrich_invoice, exception_reasons, mark_not_checked
from app.services.validation import validate_extraction

DEFAULT_ACTOR = "ap-specialist"
TEXT_FIELDS = ("vendor_name", "invoice_number", "po_number")
DATE_FIELDS = ("invoice_date", "due_date")
MONEY_FIELDS = ("subtotal", "tax", "total")
EDITABLE_FIELDS = TEXT_FIELDS + DATE_FIELDS + MONEY_FIELDS


class ReviewError(Exception):
    """An action that is not allowed or has bad input. `status_code` is for the web layer."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _require_needs_review(invoice: Invoice, action: str) -> None:
    if invoice.status != "needs_review":
        raise ReviewError(f"This invoice is {invoice.status} and cannot be {action}.")


def _audit(session, invoice, action, before=None, after=None, note=None, actor=DEFAULT_ACTOR):
    session.add(
        AuditEvent(
            invoice_id=invoice.id,
            actor=actor,
            action=action,
            before_json=before,
            after_json=after,
            note=note,
        )
    )


def _jsonable(value):
    """Dates and Decimals as strings, so audit JSON is exact and readable."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def approve(session: Session, invoice: Invoice, actor: str = DEFAULT_ACTOR) -> None:
    _require_needs_review(invoice, "approved")
    if invoice.total is None:
        raise ReviewError("Cannot approve: the total is missing. Edit the invoice first.")
    if not invoice.vendor_name:
        raise ReviewError("Cannot approve: the vendor is missing. Edit the invoice first.")
    invoice.status = "approved"
    invoice.approved_by = actor
    invoice.approved_at = datetime.now(UTC)
    _audit(session, invoice, "approved", {"status": "needs_review"}, {"status": "approved"})
    session.commit()


def reject(session: Session, invoice: Invoice, reason: str, actor: str = DEFAULT_ACTOR) -> None:
    _require_needs_review(invoice, "rejected")
    reason = (reason or "").strip()
    if not reason:
        raise ReviewError("Please give a reason for rejecting this invoice.", status_code=400)
    invoice.status = "rejected"
    _audit(session, invoice, "rejected", {"status": "needs_review"}, {"status": "rejected"}, reason)
    session.commit()


def _parse(field: str, raw: str):
    value = raw.strip()
    if not value:
        return None
    if field in MONEY_FIELDS:
        try:
            return Decimal(value.replace(",", "").replace("$", "")).quantize(Decimal("0.01"))
        except InvalidOperation:
            raise ReviewError(f"{value!r} is not a valid amount for {field}.", 400) from None
    if field in DATE_FIELDS:
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ReviewError(f"{value!r} is not a valid date (use YYYY-MM-DD).", 400) from None
    return value


def recheck(session: Session, invoice: Invoice) -> None:
    """Re-run validation, duplicate, PO, and GL checks on the invoice's current values."""
    extraction = InvoiceExtraction(
        **{field: getattr(invoice, field) for field in EDITABLE_FIELDS},
        line_items=[
            LineItem(
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                amount=line.amount,
            )
            for line in invoice.line_items
        ],
    )
    flags = validate_extraction(extraction)
    invoice.is_duplicate, invoice.duplicate_kind, invoice.duplicate_of_id = False, None, None
    if any(f.code == "UNREADABLE" for f in flags):
        invoice.validation_flags = [f.model_dump() for f in flags]
        invoice.exception_reasons = exception_reasons(flags)
        mark_not_checked(invoice, "Duplicate, PO, and GL checks skipped: unreadable.")
    else:
        enrich_invoice(session, invoice, flags)


def edit(
    session: Session, invoice: Invoice, form: dict[str, str], actor: str = DEFAULT_ACTOR
) -> dict:
    """Apply submitted field values. Only changed fields are saved and audited."""
    _require_needs_review(invoice, "edited")
    changes = {f: _parse(f, form[f]) for f in EDITABLE_FIELDS if f in form}
    changed = {f: v for f, v in changes.items() if getattr(invoice, f) != v}
    if not changed:
        return {}
    before = {f: _jsonable(getattr(invoice, f)) for f in changed}
    for field, value in changed.items():
        setattr(invoice, field, value)
    recheck(session, invoice)
    _audit(session, invoice, "edited", before, {f: _jsonable(v) for f, v in changed.items()})
    session.commit()
    return changed
