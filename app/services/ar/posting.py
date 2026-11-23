"""Post a payment's applications to AR invoices: amount_paid up, balance down, status set."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.models_ar import BankPayment
from app.services.review import ReviewError

ROUNDING = Decimal("0.01")  # a balance left at or under one cent counts as paid
POSTABLE = {"open", "partial"}


def audit_payment(
    session: Session, payment: BankPayment, action, actor, before=None, after=None, note=None
):
    session.add(
        AuditEvent(
            subject_type="bank_payment",
            subject_id=payment.id,
            actor=actor,
            action=action,
            before_json=before,
            after_json=after,
            note=note,
        )
    )


def applications_summary(payment: BankPayment) -> list[dict]:
    return [
        {"invoice_number": a.invoice.invoice_number, "amount": str(a.amount_applied)}
        for a in payment.applications
    ]


def post_payment(session: Session, payment: BankPayment, approved_by: str | None) -> None:
    """Apply every pending application. Checks first, so nothing is half-posted."""
    for app in payment.applications:
        invoice = app.invoice
        if invoice is None or invoice.status not in POSTABLE:
            raise ReviewError(f"Invoice {getattr(invoice, 'invoice_number', '?')} is not open.")
        if app.amount_applied > invoice.balance + ROUNDING:
            raise ReviewError(
                f"{invoice.invoice_number} has only {invoice.balance} open; "
                f"cannot apply {app.amount_applied}."
            )
    now = datetime.now(UTC)
    for app in payment.applications:
        invoice = app.invoice
        invoice.amount_paid += app.amount_applied
        invoice.balance -= app.amount_applied
        if invoice.balance <= ROUNDING:
            invoice.balance = Decimal("0.00")
            invoice.status = "paid"
        else:
            invoice.status = "partial"
        app.posted_at = now
        app.approved_by = approved_by
    payment.status = "posted"
