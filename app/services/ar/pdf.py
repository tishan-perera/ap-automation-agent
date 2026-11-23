"""Render a customer (AR) invoice PDF with reportlab.

Deterministic output (invariant=1): the same invoice always produces the same bytes.
A missing amount is left off the page; it is never printed as $0.00.
"""

from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.models_ar import ARInvoice

SELLER_LINES = ["Example Co (fictional)", "Synthetic demo seller"]


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"${value:,.2f}"


def _qty(value: Decimal | None) -> str:
    return "" if value is None else format(value.normalize(), "f")


def render_ar_invoice(path: Path | str, invoice: ARInvoice) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)

    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(72, 740, "INVOICE")
    pdf.setFont("Helvetica", 10)
    for i, line in enumerate(SELLER_LINES):
        pdf.drawRightString(540, 744 - i * 13, line)

    pdf.setFont("Helvetica", 11)
    header = [
        ("Invoice Number", invoice.invoice_number),
        ("Invoice Date", invoice.invoice_date.isoformat()),
        ("Due Date", invoice.due_date.isoformat() if invoice.due_date else "To be confirmed"),
        ("Payment Terms", invoice.payment_terms or "To be confirmed"),
        ("Sales Order", invoice.sales_order_number),
    ]
    y = 705
    for label, value in header:
        if value:
            pdf.drawString(72, y, f"{label}: {value}")
            y -= 16

    y -= 8
    customer = invoice.customer
    bill_to = [customer.name, f"Account {customer.account_number}", customer.billing_email]
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(72, y, "Bill To")
    pdf.setFont("Helvetica", 11)
    for line in filter(None, bill_to):
        y -= 15
        pdf.drawString(72, y, line)

    y -= 30
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(72, y, "Description")
    for x, title in ((380, "Qty"), (460, "Unit Price"), (540, "Amount")):
        pdf.drawRightString(x, y, title)
    pdf.setFont("Helvetica", 11)
    for line in invoice.line_items:
        y -= 18
        pdf.drawString(72, y, line.description or "")
        pdf.drawRightString(380, y, _qty(line.quantity))
        pdf.drawRightString(460, y, _money(line.unit_price))
        pdf.drawRightString(540, y, _money(line.amount))

    y -= 30
    for label, value in (
        ("Subtotal", invoice.subtotal),
        ("Tax", invoice.tax),
        ("Total", invoice.total),
    ):
        if value is not None:
            pdf.drawRightString(460, y, f"{label}:")
            pdf.drawRightString(540, y, _money(value))
            y -= 16

    pdf.setFont("Helvetica", 9)
    pdf.drawString(72, 84, f"Please include {invoice.invoice_number} with your payment.")
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(72, 60, "Synthetic invoice for demo purposes. All names are fictional.")
    pdf.save()
