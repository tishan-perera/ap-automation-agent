"""Turn fulfilled sales orders into customer (AR) invoices, as database rows and PDFs.

- One invoice per order (re-running skips orders that already have one).
- Amounts are computed in code: amount = quantity x unit_price, rounded to cents.
- Tax is 0.00 in v1 (synthetic orders carry no tax rate). total = subtotal + tax.
- A missing price, unknown payment terms, or a total that disagrees with the order makes
  the invoice a draft with a note. Nothing is guessed to make the numbers agree.
"""

import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.models_ar import ARInvoice, ARInvoiceLineItem, SalesOrder
from app.services.ar.numbering import next_invoice_number
from app.services.ar.pdf import render_ar_invoice

CENTS = Decimal("0.01")
ELIGIBLE_STATUSES = {"fulfilled"}
TAX_V1 = Decimal("0.00")


def terms_days(terms: str | None) -> int | None:
    """'Net 30' -> 30. Anything else -> None, so a due date is never invented."""
    match = re.fullmatch(r"\s*net\s+(\d+)\s*", terms or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def _invoice_line(line) -> ARInvoiceLineItem:
    amount = None
    if line.quantity is not None and line.unit_price is not None:
        amount = (line.quantity * line.unit_price).quantize(CENTS)
    return ARInvoiceLineItem(
        description=line.description,
        quantity=line.quantity,
        unit_price=line.unit_price,
        amount=amount,
    )


def build_invoice(order: SalesOrder, invoice_number: str) -> tuple[ARInvoice, list[str]]:
    """Build (not save) the invoice for an order. Returns it with any problems found."""
    problems: list[str] = []
    lines = [_invoice_line(line) for line in order.line_items]

    subtotal = None
    if lines and all(line.amount is not None for line in lines):
        subtotal = sum((line.amount for line in lines), Decimal(0)).quantize(CENTS)
    else:
        problems.append("A line amount is missing, so the total is unknown.")
    total = None if subtotal is None else subtotal + TAX_V1

    if total is not None and order.expected_total is not None and total != order.expected_total:
        problems.append(
            f"Computed total {total} does not match the order's expected total "
            f"{order.expected_total}."
        )

    terms = order.customer.payment_terms
    days = terms_days(terms)
    if days is None:
        problems.append(f"Payment terms {terms!r} are not understood, so there is no due date.")

    invoice = ARInvoice(
        invoice_number=invoice_number,
        customer=order.customer,
        sales_order=order,
        invoice_date=order.order_date,
        due_date=order.order_date + timedelta(days=days) if days is not None else None,
        payment_terms=terms,
        currency=order.currency,
        subtotal=subtotal,
        tax=TAX_V1,
        total=total,
        amount_paid=Decimal("0.00"),
        balance=total,
        status="draft" if problems else "open",
    )
    invoice.line_items = lines
    return invoice, problems


def _orders_to_invoice(session: Session) -> list[SalesOrder]:
    invoiced = set(session.scalars(select(ARInvoice.sales_order_id)))
    orders = session.scalars(
        select(SalesOrder)
        .where(SalesOrder.status.in_(ELIGIBLE_STATUSES))
        .order_by(SalesOrder.order_date, SalesOrder.order_number)
    )
    return [order for order in orders if order.id not in invoiced]


def create_invoices_from_orders(
    session: Session, pdf_dir: Path | str, actor: str = "system"
) -> list[ARInvoice]:
    """Invoice every eligible order that has no invoice yet, oldest order first."""
    pdf_dir = Path(pdf_dir)
    created = []
    for order in _orders_to_invoice(session):
        invoice, problems = build_invoice(order, next_invoice_number(session))
        session.add(invoice)
        session.flush()  # assigns invoice.id and reserves the number

        pdf_path = pdf_dir / f"{invoice.invoice_number}.pdf"
        render_ar_invoice(pdf_path, invoice)
        invoice.pdf_path = str(pdf_path)

        session.add(
            AuditEvent(
                subject_type="ar_invoice",
                subject_id=invoice.id,
                actor=actor,
                action="ar_invoice_created",
                after_json={
                    "invoice_number": invoice.invoice_number,
                    "sales_order": order.order_number,
                    "total": None if invoice.total is None else str(invoice.total),
                    "status": invoice.status,
                },
                note=" ".join(problems) or None,
            )
        )
        created.append(invoice)
    session.commit()
    return created
