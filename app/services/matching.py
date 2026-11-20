"""2-way (invoice vs PO) and 3-way (plus receiving record) matching.

- Amounts match within the greater of AMOUNT_TOLERANCE_USD or AMOUNT_TOLERANCE_PCT of the PO.
- Vendors are compared after normalization.
- With a receiving record, invoiced quantities must equal quantities received.
- Without one, the match is 2-way and a missing receipt alone is not a failure.
"""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Invoice, PurchaseOrder, ReceivingRecord
from app.schemas import ValidationFlag
from app.services.normalize import normalize
from app.services.validation import flag

CENTS = Decimal("0.01")


def _money(value: Decimal) -> str:
    return str(value.quantize(CENTS))


def _qty(value) -> str:
    return format(Decimal(value).normalize(), "f")


def tolerance_for(po_total: Decimal) -> Decimal:
    settings = get_settings()
    return max(settings.amount_tolerance_usd, po_total * settings.amount_tolerance_pct)


def _received_quantities(session: Session, po_number: str) -> dict[str, Decimal] | None:
    receipts = session.scalars(
        select(ReceivingRecord).where(ReceivingRecord.po_number == po_number)
    ).all()
    if not receipts:
        return None
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for receipt in receipts:
        for line in receipt.line_items:
            totals[normalize(line.description)] += line.quantity_received or Decimal(0)
    return totals


def _price_flags(invoice: Invoice, po: PurchaseOrder, details: dict) -> list[ValidationFlag]:
    if invoice.total is None or po.expected_total is None:
        details["notes"].append("Invoice or PO total is missing; amounts were not compared.")
        return []
    difference = invoice.total - po.expected_total
    tolerance = tolerance_for(po.expected_total)
    details.update(
        expected_total=_money(po.expected_total),
        actual_total=_money(invoice.total),
        difference=_money(difference),
        tolerance=_money(tolerance),
    )
    if abs(difference) <= tolerance:
        return []
    return [
        flag(
            "PRICE_VARIANCE",
            f"Invoice total {_money(invoice.total)} differs from PO total "
            f"{_money(po.expected_total)} by more than {_money(tolerance)}.",
            "total",
        )
    ]


def _qty_flags(invoice: Invoice, received: dict[str, Decimal], details: dict) -> list:
    variances = []
    for line in invoice.line_items:
        if line.quantity is None:
            continue
        got = received.get(normalize(line.description), Decimal(0))
        if line.quantity != got:
            variances.append(
                {
                    "description": line.description,
                    "invoiced": _qty(line.quantity),
                    "received": _qty(got),
                }
            )
    details["qty_variances"] = variances
    if not variances:
        return []
    return [flag("QTY_VARIANCE", "Invoiced quantity does not match quantity received.")]


def _set(invoice, match_type, status, details, flags) -> list[ValidationFlag]:
    invoice.match_type, invoice.match_status, invoice.match_details = match_type, status, details
    return flags


def check_match(session: Session, invoice: Invoice) -> list[ValidationFlag]:
    """Match the invoice to its PO (and receipt); set match fields and return any flags."""
    details: dict = {"po_number": invoice.po_number, "notes": []}
    po = None
    if invoice.po_number:
        po = session.scalars(
            select(PurchaseOrder).where(PurchaseOrder.po_number == invoice.po_number.strip())
        ).first()
    if po is None:
        message = f"PO {invoice.po_number} was not found." if invoice.po_number else "No PO number."
        return _set(
            invoice, "none", "missing_po", details, [flag("MISSING_PO", message, "po_number")]
        )

    if normalize(invoice.vendor_name) != normalize(po.vendor_name):
        details.update(po_vendor=po.vendor_name, invoice_vendor=invoice.vendor_name)
        message = f"Invoice vendor does not match PO {po.po_number} vendor ({po.vendor_name})."
        return _set(
            invoice, "two_way", "vendor_mismatch", details, [flag("VENDOR_MISMATCH", message)]
        )

    flags = _price_flags(invoice, po, details)
    received = _received_quantities(session, po.po_number)
    if received is None:
        match_type = "two_way"
        details["notes"].append(
            f"No receiving record for {po.po_number}; 3-way match not possible."
        )
    else:
        match_type = "three_way"
        flags += _qty_flags(invoice, received, details)

    if flags:
        status = "variance"
    elif invoice.total is None:
        status = "not_applicable"
    else:
        status = "matched"
    return _set(invoice, match_type, status, details, flags)
