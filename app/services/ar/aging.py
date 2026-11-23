"""AR aging: open balances by how far past due they are, as of a given date.

Read-only. Building the report or its CSV never changes an invoice or writes an audit row.

Buckets by days past due (report_as_of - due_date):
  current  = not yet past due (due_date on or after report_as_of)
  1-30, 31-60, 61-90 = that many days past due (90 days late is still 61-90)
  90+      = 91 or more days past due
Only open and partial invoices with a balance above zero are aged.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models_ar import ARInvoice
from app.services.export import safe_text

BUCKETS = ["current", "1-30", "31-60", "61-90", "90+"]
AGED_STATUSES = ("open", "partial")
CSV_HEADER = [
    "AsOf",
    "CustomerAccount",
    "Customer",
    "InvoiceNumber",
    "InvoiceDate",
    "DueDate",
    "OriginalTotal",
    "Balance",
    "DaysPastDue",
    "Bucket",
]


def resolve_as_of(as_of: date | None = None) -> date:
    """The given date, else AR_REPORT_AS_OF from settings, else today in UTC."""
    return as_of or get_settings().ar_report_as_of or datetime.now(UTC).date()


def days_past_due(due_date: date, as_of: date) -> int:
    return max((as_of - due_date).days, 0)


def bucket_for(days: int) -> str:
    if days <= 0:
        return "current"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


@dataclass
class AgingRow:
    customer_account: str
    customer_name: str
    invoice_number: str
    invoice_date: date
    due_date: date
    original_total: Decimal
    balance: Decimal
    days_past_due: int
    bucket: str


@dataclass
class BucketTotal:
    count: int = 0
    balance: Decimal = Decimal("0.00")


@dataclass
class CustomerAging:
    customer_account: str
    customer_name: str
    buckets: dict[str, Decimal] = field(
        default_factory=lambda: dict.fromkeys(BUCKETS, Decimal("0.00"))
    )
    total: Decimal = Decimal("0.00")


@dataclass
class AgingReport:
    as_of: date
    rows: list[AgingRow]
    bucket_totals: dict[str, BucketTotal]
    customers: list[CustomerAging]
    total: Decimal


def _aged_invoices(session: Session) -> list[ARInvoice]:
    query = (
        select(ARInvoice)
        .where(
            ARInvoice.status.in_(AGED_STATUSES),
            ARInvoice.balance > Decimal(0),
            ARInvoice.due_date.is_not(None),  # open invoices always have one; never guess it
        )
        .order_by(ARInvoice.due_date, ARInvoice.invoice_number)
    )
    return list(session.scalars(query))


def build_aging(session: Session, as_of: date) -> AgingReport:
    rows = []
    for invoice in _aged_invoices(session):
        days = days_past_due(invoice.due_date, as_of)
        rows.append(
            AgingRow(
                customer_account=invoice.customer_account,
                customer_name=invoice.customer_name,
                invoice_number=invoice.invoice_number,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                original_total=invoice.total,
                balance=invoice.balance,
                days_past_due=days,
                bucket=bucket_for(days),
            )
        )

    bucket_totals = {name: BucketTotal() for name in BUCKETS}
    customers: dict[str, CustomerAging] = {}
    for row in rows:
        bucket_totals[row.bucket].count += 1
        bucket_totals[row.bucket].balance += row.balance
        customer = customers.setdefault(
            row.customer_account, CustomerAging(row.customer_account, row.customer_name)
        )
        customer.buckets[row.bucket] += row.balance
        customer.total += row.balance

    return AgingReport(
        as_of=as_of,
        rows=rows,
        bucket_totals=bucket_totals,
        customers=sorted(customers.values(), key=lambda c: (-c.total, c.customer_name)),
        total=sum((row.balance for row in rows), Decimal("0.00")),
    )


def aging_csv(report: AgingReport) -> str:
    """One row per aged invoice, no totals row, so the file imports cleanly."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for row in report.rows:
        writer.writerow(
            [
                report.as_of.isoformat(),
                safe_text(row.customer_account),
                safe_text(row.customer_name),
                safe_text(row.invoice_number),
                row.invoice_date.isoformat(),
                row.due_date.isoformat(),
                f"{row.original_total:.2f}",
                f"{row.balance:.2f}",
                row.days_past_due,
                row.bucket,
            ]
        )
    return buffer.getvalue()
