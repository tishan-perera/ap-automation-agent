from datetime import date
from decimal import Decimal

from app.models import Invoice
from app.services.duplicates import check_duplicate
from app.services.ingest import process_invoice

DATA_INVOICES = "data/invoices"


def add_invoice(session, file_hash, **fields):
    defaults = {
        "filename": f"{file_hash}.pdf",
        "vendor_name": "Northwind Office Supply LLC",
        "invoice_number": "NWO-2001",
        "invoice_date": date(2026, 9, 3),
        "total": Decimal("45.90"),
    }
    invoice = Invoice(file_hash=file_hash, **{**defaults, **fields})
    session.add(invoice)
    session.flush()
    return invoice


def codes(flags):
    return [f.code for f in flags]


def test_exact_duplicate_after_normalization(session):
    original = add_invoice(session, "h1")
    new = add_invoice(
        session, "h2", vendor_name="  northwind office supply, llc. ", invoice_number="nwo 2001"
    )

    flags = check_duplicate(session, new)

    assert codes(flags) == ["DUPLICATE_EXACT"]
    assert new.is_duplicate is True
    assert new.duplicate_kind == "exact"
    assert new.duplicate_of_id == original.id


def test_soft_duplicate_same_vendor_total_close_date(session):
    original = add_invoice(session, "h1")
    new = add_invoice(session, "h2", invoice_number="NWO-2999", invoice_date=date(2026, 9, 6))

    flags = check_duplicate(session, new)

    assert codes(flags) == ["DUPLICATE_SOFT"]
    assert new.duplicate_kind == "soft"
    assert new.duplicate_of_id == original.id


def test_soft_duplicate_needs_date_within_three_days(session):
    add_invoice(session, "h1")
    new = add_invoice(session, "h2", invoice_number="NWO-2999", invoice_date=date(2026, 9, 7))

    assert check_duplicate(session, new) == []
    assert new.is_duplicate is False


def test_unique_invoice_is_not_a_duplicate(session):
    add_invoice(session, "h1")
    new = add_invoice(
        session,
        "h2",
        vendor_name="Cedar Utilities Inc",
        invoice_number="CUI-2006",
        total=Decimal("1825.10"),
    )

    assert check_duplicate(session, new) == []
    assert new.is_duplicate is False
    assert new.duplicate_kind is None
    assert new.duplicate_of_id is None


def test_missing_total_never_soft_matches(session):
    add_invoice(session, "h1", total=None)
    new = add_invoice(session, "h2", invoice_number="NWO-2999", total=None)

    assert check_duplicate(session, new) == []


def ingest(session, filename):
    return process_invoice(f"{DATA_INVOICES}/{filename}", session=session)


def test_ingest_flags_exact_duplicate_fixture(session, fixture_pdf):
    path, record = fixture_pdf("duplicate_exact")
    original = ingest(session, record["duplicate_of"])
    duplicate = process_invoice(path, session=session)

    assert duplicate.id != original.id  # saved, never silently dropped
    assert duplicate.is_duplicate is True
    assert duplicate.duplicate_kind == "exact"
    assert duplicate.duplicate_of_id == original.id
    assert "DUPLICATE_EXACT" in [f["code"] for f in duplicate.validation_flags]
    assert duplicate.status == "needs_review"
    assert original.is_duplicate is False


def test_ingest_flags_soft_duplicate_fixture(session, fixture_pdf):
    path, record = fixture_pdf("duplicate_soft")
    original = ingest(session, record["duplicate_of"])
    duplicate = process_invoice(path, session=session)

    assert duplicate.duplicate_kind == "soft"
    assert duplicate.duplicate_of_id == original.id
    assert "DUPLICATE_SOFT" in [f["code"] for f in duplicate.validation_flags]
