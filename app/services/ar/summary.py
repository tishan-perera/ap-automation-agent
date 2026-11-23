"""Counts for the Receivables home page. Read-only sums of stored rows; nothing is invented."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models_ar import ARInvoice, BankPayment, CollectionReminder
from app.services.ar.risk import assess_risk


@dataclass
class ReceivablesSummary:
    as_of: date
    open_count: int
    open_balance: Decimal
    payments_review: int
    payments_unidentified: int
    reminders_review: int
    high_risk: int


def _count(session: Session, model, *conditions) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def receivables_summary(session: Session, as_of: date) -> ReceivablesSummary:
    open_rows = session.execute(
        select(func.count(), func.coalesce(func.sum(ARInvoice.balance), 0)).where(
            ARInvoice.status.in_(("open", "partial")), ARInvoice.balance > 0
        )
    ).one()
    return ReceivablesSummary(
        as_of=as_of,
        open_count=open_rows[0],
        open_balance=Decimal(str(open_rows[1])).quantize(Decimal("0.01")),
        payments_review=_count(session, BankPayment, BankPayment.status == "needs_review"),
        payments_unidentified=_count(session, BankPayment, BankPayment.status == "unidentified"),
        reminders_review=_count(
            session, CollectionReminder, CollectionReminder.status == "needs_review"
        ),
        high_risk=sum(r.level == "high" for r in assess_risk(session, as_of)),
    )
