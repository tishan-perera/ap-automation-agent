from decimal import Decimal

import pytest

from app.models import (
    Invoice,
    InvoiceLineItem,
    POLineItem,
    PurchaseOrder,
    ReceivingLineItem,
    ReceivingRecord,
)
from app.services.ingest import process_invoice
from app.services.matching import check_match
from app.services.seed import seed_reference_data
from tests.conftest import DATA_DIR

VENDOR = "Northwind Office Supply LLC"


def add_po(session, expected_total, qty=4, po_number="PO-9001", vendor=VENDOR):
    po = PurchaseOrder(po_number=po_number, vendor_name=vendor, expected_total=expected_total)
    po.line_items = [POLineItem(description="Copy paper", quantity=qty)]
    session.add(po)
    session.flush()
    return po


def add_receipt(session, qty_received, po_number="PO-9001"):
    receipt = ReceivingRecord(po_number=po_number, packing_slip_number="PS-9001")
    receipt.line_items = [
        ReceivingLineItem(description="Copy paper", quantity_received=qty_received)
    ]
    session.add(receipt)
    session.flush()


def make_invoice(total, qty=4, po_number="PO-9001", vendor=VENDOR):
    invoice = Invoice(filename="x.pdf", file_hash="x", vendor_name=vendor, total=total)
    invoice.po_number = po_number
    invoice.line_items = [InvoiceLineItem(description="Copy paper", quantity=qty)]
    return invoice


def codes(flags):
    return [f.code for f in flags]


@pytest.mark.parametrize(
    ("po_total", "invoice_total", "expected_status"),
    [
        ("100.00", "100.99", "matched"),  # within the $1.00 floor
        ("100.00", "101.50", "variance"),  # beyond $1.00 (1% of 100 is only $1.00)
        ("1000.00", "1009.00", "matched"),  # within 1% = $10.00
        ("1000.00", "1010.01", "variance"),  # just beyond 1%
    ],
)
def test_two_way_tolerance(session, po_total, invoice_total, expected_status):
    add_po(session, Decimal(po_total))
    invoice = make_invoice(Decimal(invoice_total))

    flags = check_match(session, invoice)

    assert invoice.match_status == expected_status
    assert ("PRICE_VARIANCE" in codes(flags)) == (expected_status == "variance")


def test_price_variance_details_show_expected_and_actual(session):
    add_po(session, Decimal("100.00"))
    invoice = make_invoice(Decimal("115.00"))

    check_match(session, invoice)

    assert invoice.match_details["expected_total"] == "100.00"
    assert invoice.match_details["actual_total"] == "115.00"
    assert invoice.match_details["difference"] == "15.00"


def test_vendor_compare_ignores_case_and_punctuation(session):
    add_po(session, Decimal("100.00"))
    invoice = make_invoice(Decimal("100.00"), vendor="NORTHWIND office supply, L.L.C.")

    assert check_match(session, invoice) == []
    assert invoice.match_status == "matched"


def test_missing_and_unknown_po(session):
    for po_number in (None, "PO-0000"):
        invoice = make_invoice(Decimal("10.00"), po_number=po_number)
        flags = check_match(session, invoice)
        assert invoice.match_status == "missing_po"
        assert codes(flags) == ["MISSING_PO"]


def test_three_way_qty_variance(session):
    add_po(session, Decimal("100.00"), qty=10)
    add_receipt(session, qty_received=8)
    invoice = make_invoice(Decimal("100.00"), qty=10)

    flags = check_match(session, invoice)

    assert invoice.match_type == "three_way"
    assert invoice.match_status == "variance"
    assert codes(flags) == ["QTY_VARIANCE"]
    assert invoice.match_details["qty_variances"] == [
        {"description": "Copy paper", "invoiced": "10", "received": "8"}
    ]


def test_three_way_matched_when_quantities_agree(session):
    add_po(session, Decimal("100.00"), qty=10)
    add_receipt(session, qty_received=10)
    invoice = make_invoice(Decimal("100.00"), qty=10)

    assert check_match(session, invoice) == []
    assert invoice.match_type == "three_way"
    assert invoice.match_status == "matched"


def test_no_receipt_is_two_way_and_still_matched(session):
    add_po(session, Decimal("100.00"))
    invoice = make_invoice(Decimal("100.00"))

    assert check_match(session, invoice) == []
    assert invoice.match_type == "two_way"
    assert invoice.match_status == "matched"
    assert "3-way match not possible" in " ".join(invoice.match_details["notes"])


# --- The synthetic fixtures, end to end, against seeded POs and receipts ---


@pytest.fixture
def seeded(session):
    seed_reference_data(session, DATA_DIR)
    return session


@pytest.mark.parametrize(
    ("case_type", "match_status", "flag_code"),
    [
        ("happy", "matched", None),
        ("price_variance", "variance", "PRICE_VARIANCE"),
        ("missing_po", "missing_po", "MISSING_PO"),
        ("wrong_vendor", "vendor_mismatch", "VENDOR_MISMATCH"),
        ("qty_variance", "variance", "QTY_VARIANCE"),
    ],
)
def test_fixture_cases(seeded, fixture_pdf, case_type, match_status, flag_code):
    path, _ = fixture_pdf(case_type)
    invoice = process_invoice(path, session=seeded)
    invoice_codes = [f["code"] for f in invoice.validation_flags]

    assert invoice.match_status == match_status
    if flag_code:
        assert flag_code in invoice_codes
    else:
        assert invoice_codes == []
