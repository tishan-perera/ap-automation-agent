"""Export approved invoices to a QuickBooks-style bills CSV.

One row per line item (or one row with the invoice total when there are no lines).
Each approved invoice is exported once: it becomes "exported" and gets an audit event,
in the same database commit that builds the file.
"""

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Invoice

HEADER = [
    "BillNo",
    "Vendor",
    "InvoiceDate",
    "DueDate",
    "Amount",
    "Account",
    "LineDescription",
    "LineAmount",
    "Currency",
    "PONumber",
    "Memo",
]
TEXT_COLUMNS = {"BillNo", "Vendor", "Account", "LineDescription", "PONumber", "Memo"}
# A cell starting with one of these could run as a formula when opened in a spreadsheet.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
DEFAULT_ACTOR = "ap-specialist"


def _safe_text(value: str | None) -> str:
    text = value or ""
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def _date(value: date | None) -> str:
    return value.strftime("%m/%d/%Y") if value else ""


def _amount(value: Decimal | None) -> str:
    return "" if value is None else str(value.quantize(Decimal("0.01")))


def _memo(invoice: Invoice) -> str:
    memo = f"Approved by {invoice.approved_by or DEFAULT_ACTOR}. Credit 2000 Accounts Payable."
    if not invoice.gl_account:
        memo += " GL account needs coding."
    return memo


def invoice_rows(invoice: Invoice) -> list[dict[str, str]]:
    base = {
        "BillNo": invoice.invoice_number,
        "Vendor": invoice.vendor_name,
        "InvoiceDate": _date(invoice.invoice_date),
        "DueDate": _date(invoice.due_date),
        "Amount": _amount(invoice.total),
        "Account": invoice.gl_account,
        "Currency": invoice.currency or "USD",
        "PONumber": invoice.po_number,
        "Memo": _memo(invoice),
    }
    lines = [(line.description, line.amount) for line in invoice.line_items] or [
        ("Invoice total", invoice.total)
    ]
    rows = []
    for description, amount in lines:
        row = {**base, "LineDescription": description, "LineAmount": _amount(amount)}
        rows.append(
            {col: _safe_text(row[col]) if col in TEXT_COLUMNS else row[col] for col in HEADER}
        )
    return rows


def build_csv(invoices: list[Invoice]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=HEADER, lineterminator="\n")
    writer.writeheader()
    for invoice in invoices:
        writer.writerows(invoice_rows(invoice))
    return buffer.getvalue()


def approved_invoices(session: Session) -> list[Invoice]:
    query = select(Invoice).where(Invoice.status == "approved")
    return list(session.scalars(query.order_by(Invoice.approved_at, Invoice.filename)))


def preview_csv(session: Session) -> str:
    """CSV of the currently approved invoices. Read-only: nothing is marked exported."""
    return build_csv(approved_invoices(session))


def export_approved(session: Session, actor: str = DEFAULT_ACTOR) -> tuple[str, list[str]]:
    """Return (csv_text, exported invoice ids). Marks those invoices exported."""
    invoices = approved_invoices(session)
    csv_text = build_csv(invoices)
    now = datetime.now(UTC)
    for invoice in invoices:
        invoice.status = "exported"
        invoice.exported_at = now
        session.add(
            AuditEvent(
                invoice_id=invoice.id,
                actor=actor,
                action="exported",
                before_json={"status": "approved"},
                after_json={"status": "exported"},
                note="QuickBooks-style CSV export",
            )
        )
    session.commit()
    return csv_text, [invoice.id for invoice in invoices]
