import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditEvent
from app.models_ar import ARInvoice
from app.services.ar.aging import (
    BUCKETS,
    CSV_HEADER,
    aging_csv,
    bucket_for,
    build_aging,
    days_past_due,
)
from app.services.ar.invoicing import create_invoices_from_orders
from app.services.ar.seed import seed_ar_reference
from app.services.cash_application import apply_payments
from app.services.llm.payment_matcher_mock import MockPaymentMatcher
from tests.conftest import DATA_DIR

AR_DIR = DATA_DIR / "ar"
AS_OF = date(2026, 11, 30)  # fixed, so tests never depend on today's date


@pytest.fixture
def ar(session, tmp_path):
    seed_ar_reference(session, AR_DIR)
    create_invoices_from_orders(session, tmp_path / "pdfs")
    return session


def inv(session, number) -> ARInvoice:
    return session.scalars(select(ARInvoice).where(ARInvoice.invoice_number == number)).one()


def snapshot(session):
    return {
        i.invoice_number: (i.status, i.balance, i.amount_paid, i.updated_at)
        for i in session.scalars(select(ARInvoice))
    }


def audit_count(session):
    return session.scalar(select(func.count()).select_from(AuditEvent))


# --- Buckets and boundaries ---


def test_bucket_names_are_fixed():
    assert BUCKETS == ["current", "1-30", "31-60", "61-90", "90+"]


@pytest.mark.parametrize(
    ("days_late", "bucket"),
    [
        (-10, "current"),
        (0, "current"),  # due today is not past due
        (1, "1-30"),
        (30, "1-30"),
        (31, "31-60"),
        (60, "31-60"),
        (61, "61-90"),
        (90, "61-90"),  # 90 days late is still 61-90
        (91, "90+"),  # 90+ starts at 91 days
        (400, "90+"),
    ],
)
def test_bucket_boundaries(days_late, bucket):
    due = date(2026, 9, 1)
    as_of = due + timedelta(days=days_late)
    assert days_past_due(due, as_of) == max(days_late, 0)
    assert bucket_for(days_past_due(due, as_of)) == bucket


# --- The report ---


def test_only_open_and_partial_invoices_with_a_balance_are_aged(ar):
    inv(ar, "AR-1001").status, inv(ar, "AR-1001").balance = "paid", Decimal("0.00")
    inv(ar, "AR-1002").status = "void"
    inv(ar, "AR-1003").status = "draft"
    partial = inv(ar, "AR-1004")
    partial.status, partial.amount_paid = "partial", Decimal("100.00")
    partial.balance = partial.total - Decimal("100.00")
    ar.flush()

    report = build_aging(ar, AS_OF)
    numbers = {row.invoice_number for row in report.rows}

    assert {"AR-1001", "AR-1002", "AR-1003"}.isdisjoint(numbers)
    assert "AR-1004" in numbers
    row = next(r for r in report.rows if r.invoice_number == "AR-1004")
    assert row.balance == partial.total - Decimal("100.00")  # the open balance, not the total
    assert row.original_total == partial.total


def test_rows_carry_every_required_field(ar):
    row = build_aging(ar, AS_OF).rows[0]
    invoice = inv(ar, row.invoice_number)

    assert row.customer_account == invoice.customer.account_number
    assert row.customer_name == invoice.customer.name
    assert row.invoice_date == invoice.invoice_date
    assert row.due_date == invoice.due_date
    assert isinstance(row.original_total, Decimal) and isinstance(row.balance, Decimal)
    assert row.days_past_due == days_past_due(invoice.due_date, AS_OF)
    assert row.bucket == bucket_for(row.days_past_due)


def test_bucket_and_customer_totals_add_up_to_the_grand_total(ar):
    report = build_aging(ar, AS_OF)

    assert report.as_of == AS_OF
    assert set(report.bucket_totals) == set(BUCKETS)
    assert sum(b.count for b in report.bucket_totals.values()) == len(report.rows)
    assert sum((b.balance for b in report.bucket_totals.values()), Decimal(0)) == report.total
    assert sum((c.total for c in report.customers), Decimal(0)) == report.total
    assert report.total == sum((r.balance for r in report.rows), Decimal(0))
    for customer in report.customers:
        assert sum(customer.buckets.values(), Decimal(0)) == customer.total


def test_aging_reflects_posted_payments(ar):
    apply_payments(ar, MockPaymentMatcher())  # posts the confident fixture payments

    numbers = {row.invoice_number for row in build_aging(ar, AS_OF).rows}

    assert "AR-1001" not in numbers  # paid in full by BP-0001
    paid = {
        i.invoice_number for i in ar.scalars(select(ARInvoice).where(ARInvoice.status == "paid"))
    }
    assert paid and paid.isdisjoint(numbers)


def test_an_earlier_as_of_date_moves_invoices_into_current(ar):
    early = build_aging(ar, date(2026, 8, 1))  # before any invoice was due
    assert {row.bucket for row in early.rows} == {"current"}


def test_building_the_report_changes_nothing(ar):
    before, audits = snapshot(ar), audit_count(ar)

    build_aging(ar, AS_OF)
    aging_csv(build_aging(ar, AS_OF))

    assert snapshot(ar) == before
    assert audit_count(ar) == audits


# --- CSV ---


def test_csv_has_a_stable_header_and_one_row_per_open_invoice(ar):
    report = build_aging(ar, AS_OF)

    rows = list(csv.reader(io.StringIO(aging_csv(report))))

    assert (
        rows[0]
        == CSV_HEADER
        == [
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
    )
    assert len(rows) - 1 == len(report.rows)  # no totals row: easy to import
    first = dict(zip(rows[0], rows[1], strict=True))
    assert first["AsOf"] == AS_OF.isoformat()
    assert first["InvoiceNumber"] == report.rows[0].invoice_number
    assert Decimal(first["Balance"]) == report.rows[0].balance


# --- Routes: read-only GETs ---


@pytest.fixture
def loaded(engine, tmp_path):
    with Session(engine) as session:
        seed_ar_reference(session, AR_DIR)
        create_invoices_from_orders(session, tmp_path / "pdfs")


def db_state(engine):
    with Session(engine) as session:
        return snapshot(session), audit_count(session)


def test_json_endpoint_returns_the_report(client, engine, loaded):
    body = client.get("/api/ar/aging", params={"as_of": AS_OF.isoformat()}).json()

    assert body["as_of"] == AS_OF.isoformat()
    assert set(body["bucket_totals"]) == set(BUCKETS)
    assert body["rows"] and {"invoice_number", "bucket", "days_past_due", "balance"} <= set(
        body["rows"][0]
    )
    assert Decimal(body["total"]) == sum(Decimal(r["balance"]) for r in body["rows"])


def test_csv_download_is_a_read_only_get(client, engine, loaded):
    before = db_state(engine)

    response = client.get("/api/ar/aging.csv", params={"as_of": AS_OF.isoformat()})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "2026-11-30" in response.headers["content-disposition"]
    assert response.text.splitlines()[0] == ",".join(CSV_HEADER)
    assert db_state(engine) == before  # no status change, no audit row


def test_html_page_shows_buckets_and_rows_without_side_effects(client, engine, loaded):
    before = db_state(engine)

    page = client.get("/receivables/aging", params={"as_of": AS_OF.isoformat()})

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    for bucket in BUCKETS:
        assert bucket in page.text
    assert "AR-1009" in page.text
    # The download link opts out of htmx boosting, so the CSV downloads instead of swapping in.
    assert 'href="/api/ar/aging.csv?as_of=2026-11-30"' in page.text
    assert 'hx-boost="false"' in page.text
    assert db_state(engine) == before
    assert client.get("/queue").status_code == 200


def test_default_as_of_comes_from_settings_when_set(client, loaded, monkeypatch):
    monkeypatch.setenv("AR_REPORT_AS_OF", "2026-10-15")
    get_settings.cache_clear()

    assert client.get("/api/ar/aging").json()["as_of"] == "2026-10-15"
