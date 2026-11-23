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
        # AR (docs/specs/2026-11-21-ar-design.md)
        "customers",
        "sales_orders",
        "sales_order_line_items",
        "ar_invoices",
        "ar_invoice_line_items",
    }
    assert expected <= set(inspect(engine).get_table_names())


def test_audit_events_gain_nullable_subject_columns(engine):
    columns = {c["name"]: c for c in inspect(engine).get_columns("audit_events")}
    assert "invoice_id" in columns  # AP keeps using this
    assert columns["subject_type"]["nullable"] is True
    assert columns["subject_id"]["nullable"] is True


def test_init_db_warns_when_local_database_is_outdated(tmp_path, caplog):
    import logging

    from sqlalchemy import text

    from app.db import init_db, make_engine

    old = make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with old.begin() as conn:  # an audit_events table from before the AR columns existed
        conn.execute(text("CREATE TABLE audit_events (id VARCHAR(36) PRIMARY KEY)"))

    with caplog.at_level(logging.WARNING, logger="app.db"):
        init_db(old)

    assert any(r.getMessage() == "db_schema_outdated" for r in caplog.records)
    old.dispose()
