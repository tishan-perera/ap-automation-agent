"""Combined cash view: money in (AR) and money out (AP), from existing rows only.

Nothing here is a forecast. Every figure is a sum of stored amounts:
- Money in, received: applications of posted bank payments (approved or auto-posted)
  dated on or before as_of.
- Money in, expected: balances of open/partial AR invoices due from as_of to as_of + 30.
- Money out, approved: totals of AP invoices that are approved or exported. (This app does
  not record AP payments, so these are "approved to pay", not "paid".)
- Money out, expected: totals of AP invoices still in needs_review, due in the same window.
Items with no due date are listed as undated, never given a guessed date. Items with no
amount are listed but left out of every sum. Read-only: building the view changes nothing.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Invoice
from app.models_ar import ARInvoice, BankPayment, CollectionReminder, PaymentApplication

WINDOW_DAYS = 30
ZERO = Decimal("0.00")
AR_OPEN = ("open", "partial")


@dataclass
class CashRow:
    day: date | None
    direction: str  # in | out
    reference: str
    party: str
    amount: Decimal | None
    status: str
    needs_human: bool
    link: str


@dataclass
class CashView:
    as_of: date
    window_end: date
    received_count: int = 0
    received_total: Decimal = ZERO
    ar_due_count: int = 0
    ar_due_total: Decimal = ZERO
    ar_past_due_total: Decimal = ZERO
    ap_done_count: int = 0
    ap_done_total: Decimal = ZERO
    ap_due_count: int = 0
    ap_due_total: Decimal = ZERO
    ap_past_due_total: Decimal = ZERO
    missing_amount_count: int = 0
    rows: list[CashRow] = field(default_factory=list)  # dated, inside the window
    undated: list[CashRow] = field(default_factory=list)
    recent_payments: list[BankPayment] = field(default_factory=list)
    needs_human: dict[str, int] = field(default_factory=dict)


def _sum(values) -> Decimal:
    return sum((v for v in values if v is not None), ZERO)


def _money_in(session: Session, view: CashView) -> None:
    # Only payments dated on or before as_of count as received "as of" that date.
    posted = (
        select(PaymentApplication)
        .join(BankPayment, PaymentApplication.payment_id == BankPayment.id)
        .where(PaymentApplication.posted_at.is_not(None), BankPayment.payment_date <= view.as_of)
    )
    applications = list(session.scalars(posted))
    view.received_total = _sum(a.amount_applied for a in applications)
    view.received_count = len({a.payment_id for a in applications})
    view.recent_payments = list(
        session.scalars(
            select(BankPayment)
            .where(BankPayment.status == "posted", BankPayment.payment_date <= view.as_of)
            .order_by(BankPayment.payment_date.desc(), BankPayment.payment_ref.desc())
            .limit(10)
        )
    )

    invoices = session.scalars(
        select(ARInvoice).where(ARInvoice.status.in_(AR_OPEN), ARInvoice.balance > ZERO)
    )
    for inv in invoices:
        row = CashRow(
            inv.due_date, "in", inv.invoice_number, inv.customer_name, inv.balance, inv.status,
            False, f"/api/ar/invoices/{inv.invoice_number}",
        )  # fmt: skip
        if inv.due_date is None:
            view.undated.append(row)
        elif inv.due_date < view.as_of:
            view.ar_past_due_total += inv.balance
        elif inv.due_date <= view.window_end:
            view.rows.append(row)
            view.ar_due_count += 1
            view.ar_due_total += inv.balance


def _money_out(session: Session, view: CashView) -> None:
    done = list(
        session.scalars(select(Invoice).where(Invoice.status.in_(("approved", "exported"))))
    )
    view.ap_done_count = len(done)
    view.ap_done_total = _sum(inv.total for inv in done)

    for inv in session.scalars(select(Invoice).where(Invoice.status == "needs_review")):
        row = CashRow(
            inv.due_date, "out", inv.invoice_number or inv.filename,
            inv.vendor_name or "Unreadable invoice", inv.total, inv.status, True,
            f"/invoices/{inv.id}",
        )  # fmt: skip
        if inv.due_date is None:
            view.undated.append(row)
        elif inv.due_date < view.as_of:
            view.ap_past_due_total += inv.total or ZERO
        elif inv.due_date <= view.window_end:
            view.rows.append(row)
            view.ap_due_count += 1
            view.ap_due_total += inv.total or ZERO


def _count(session: Session, model, *conditions) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def build_cash(session: Session, as_of: date, days: int = WINDOW_DAYS) -> CashView:
    view = CashView(as_of=as_of, window_end=as_of + timedelta(days=days))
    _money_in(session, view)
    _money_out(session, view)
    view.rows.sort(key=lambda r: (r.day, r.direction, r.reference))
    view.undated.sort(key=lambda r: (r.direction, r.reference))
    view.missing_amount_count = sum(r.amount is None for r in view.rows + view.undated)
    view.needs_human = {
        "ap_review": _count(session, Invoice, Invoice.status == "needs_review"),
        "payments_review": _count(session, BankPayment, BankPayment.status == "needs_review"),
        "reminders_review": _count(
            session, CollectionReminder, CollectionReminder.status == "needs_review"
        ),
    }
    return view
