from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models import Invoice
from app.schemas import InvoiceExtraction, LineItem
from app.services.validation import validate_extraction


def flag_codes(flags):
    return [flag.code for flag in flags]


def test_missing_total_is_none_not_zero():
    extraction = InvoiceExtraction(vendor_name="Acme Paper Co", total=None)
    assert extraction.total is None
    assert extraction.total != 0


def test_validate_extraction_flags_missing_total():
    extraction = InvoiceExtraction(vendor_name="Acme Paper Co", total=None)
    assert "MISSING_TOTAL" in flag_codes(validate_extraction(extraction))


def test_line_math_mismatch_is_flagged():
    extraction = InvoiceExtraction(
        vendor_name="Acme Paper Co",
        total=Decimal("30.00"),
        line_items=[LineItem(amount=Decimal("10.00")), LineItem(amount=Decimal("15.00"))],
    )
    codes = flag_codes(validate_extraction(extraction))
    assert "LINE_MATH_MISMATCH" in codes
    assert "MISSING_TOTAL" not in codes


def test_line_math_that_adds_up_is_not_flagged():
    extraction = InvoiceExtraction(
        vendor_name="Acme Paper Co",
        total=Decimal("25.00"),
        line_items=[LineItem(amount=Decimal("10.00")), LineItem(amount=Decimal("15.00"))],
    )
    assert "LINE_MATH_MISMATCH" not in flag_codes(validate_extraction(extraction))


def test_invoice_file_hash_is_unique(session):
    session.add(Invoice(filename="a.pdf", file_hash="abc"))
    session.commit()

    session.add(Invoice(filename="b.pdf", file_hash="abc"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_init_db_creates_all_tables(engine):
    expected = {
        "invoices",
        "invoice_line_items",
        "purchase_orders",
        "po_line_items",
        "receiving_records",
        "receiving_line_items",
        "audit_events",
        "processed_files",
        "app_settings",
    }
    assert expected <= set(inspect(engine).get_table_names())
