"""Human review of payment matches: approve, reject, or edit. Every action is audited.

Approving posts the proposed applications. Rejecting removes them and leaves invoices
unchanged. Editing replaces them with the reviewer's own, after checking every invoice
exists, is open, and has enough balance, and that the total fits inside the payment.
"""

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_ar import ARInvoice, BankPayment, PaymentApplication
from app.services.ar.posting import (
    POSTABLE,
    ROUNDING,
    applications_summary,
    audit_payment,
    post_payment,
)
from app.services.review import DEFAULT_ACTOR, ReviewError

REVIEWABLE = {"needs_review"}
EDITABLE = {"needs_review", "unidentified"}


def _require(payment: BankPayment, allowed: set[str], action: str) -> None:
    if payment.status not in allowed:
        raise ReviewError(
            f"Payment {payment.payment_ref} is {payment.status} and cannot be {action}."
        )


def approve(session: Session, payment: BankPayment, actor: str = DEFAULT_ACTOR) -> None:
    _require(payment, REVIEWABLE, "approved")
    if not payment.applications:
        raise ReviewError("Nothing to post. Edit the payment to choose invoices first.")
    post_payment(session, payment, approved_by=actor)
    audit_payment(
        session, payment, "payment_application_approved", actor,
        before={"status": "needs_review"},
        after={"status": "posted", "applications": applications_summary(payment)},
    )  # fmt: skip
    session.commit()


def reject(session: Session, payment: BankPayment, reason: str, actor: str = DEFAULT_ACTOR) -> None:
    _require(payment, REVIEWABLE, "rejected")
    reason = (reason or "").strip()
    if not reason:
        raise ReviewError("Please give a reason for rejecting this match.", status_code=400)
    before = applications_summary(payment)
    payment.applications.clear()
    payment.status = "unidentified"
    payment.match_details = {
        **(payment.match_details or {}),
        "unapplied_amount": str(payment.amount),
        "rejected_reason": reason,
    }
    audit_payment(
        session, payment, "payment_application_rejected", actor,
        before={"status": "needs_review", "applications": before},
        after={"status": "unidentified"}, note=reason,
    )  # fmt: skip
    session.commit()


def _parse_amount(raw) -> Decimal:
    try:
        amount = Decimal(str(raw).replace(",", "").replace("$", "").strip()).quantize(ROUNDING)
    except InvalidOperation:
        raise ReviewError(f"{raw!r} is not a valid amount.", status_code=400) from None
    if amount <= 0:
        raise ReviewError("Applied amounts must be greater than zero.", status_code=400)
    return amount


def _validated(session: Session, payment: BankPayment, rows) -> list[tuple[ARInvoice, Decimal]]:
    rows = list(rows)
    if not rows:
        raise ReviewError("Add at least one invoice and amount.", status_code=400)
    numbers = [str(number).strip().upper() for number, _ in rows]
    if len(set(numbers)) != len(numbers):
        raise ReviewError("Each invoice can appear only once.", status_code=400)
    result = []
    for number, (_, raw_amount) in zip(numbers, rows, strict=True):
        invoice = session.scalars(
            select(ARInvoice).where(ARInvoice.invoice_number == number)
        ).first()
        if invoice is None:
            raise ReviewError(f"Invoice {number} does not exist.", status_code=400)
        if invoice.status not in POSTABLE:
            raise ReviewError(f"Invoice {number} is {invoice.status}, not open.", status_code=400)
        amount = _parse_amount(raw_amount)
        if amount > invoice.balance:
            raise ReviewError(f"{number} has only {invoice.balance} open.", status_code=400)
        result.append((invoice, amount))
    if sum(amount for _, amount in result) > payment.amount:
        raise ReviewError(
            f"The total is more than the payment ({payment.amount}).", status_code=400
        )
    return result


def edit(session: Session, payment: BankPayment, rows, actor: str = DEFAULT_ACTOR) -> None:
    """rows: [(invoice_number, amount), ...] chosen by the reviewer."""
    _require(payment, EDITABLE, "edited")
    chosen = _validated(session, payment, rows)
    before = applications_summary(payment)

    def kind(invoice, amount):
        if len(chosen) > 1:
            return "split"
        return "partial" if amount < invoice.balance else "invoice_number"

    payment.applications = [
        PaymentApplication(invoice=i, amount_applied=a, confidence=None, match_kind=kind(i, a))
        for i, a in chosen
    ]
    applied = sum(a for _, a in chosen)
    payment.status = "needs_review"
    payment.match_details = {
        **(payment.match_details or {}),
        "unapplied_amount": str(payment.amount - applied),
        "edited_by": actor,
    }
    session.flush()
    audit_payment(
        session, payment, "payment_application_edited", actor,
        before={"applications": before}, after={"applications": applications_summary(payment)},
    )  # fmt: skip
    session.commit()
