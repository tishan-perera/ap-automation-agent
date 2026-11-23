import json
from datetime import date, timedelta
from decimal import Decimal

import pdfplumber
import pytest
from sqlalchemy import func, select

from app.models import AuditEvent
from app.models_ar import ARInvoice, Customer, SalesOrder, SalesOrderLineItem
from app.services.ar.invoicing import create_invoices_from_orders
from app.services.ar.seed import seed_ar_reference
from scripts.generate_ar_invoices import generate_ar_invoice_files
from tests.conftest import DATA_DIR

AR_DIR = DATA_DIR / "ar"


def load(name):
    return json.loads((AR_DIR / name).read_text())


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def pdf_text(path):
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


@pytest.fixture
def seeded(session):
    seed_ar_reference(session, AR_DIR)
    return session


@pytest.fixture
def pdf_dir(tmp_path):
    return tmp_path / "invoices"


@pytest.fixture
def invoiced(seeded, pdf_dir):
    created = create_invoices_from_orders(seeded, pdf_dir)
    return {inv.invoice_number: inv for inv in created}


def add_order(session, order_number, lines, *, status="fulfilled", terms="Net 30", total=None):
    """A hand-built order for edge cases, under its own fictional customer."""
    customer = Customer(
        name=f"Edgecase Test Customer {order_number}",
        account_number=f"T-{order_number}",
        payment_terms=terms,
    )
    order = SalesOrder(
        order_number=order_number,
        customer=customer,
        order_date=date(2026, 12, 1),
        status=status,
        expected_total=total,
    )
    order.line_items = [
        SalesOrderLineItem(description=d, quantity=q, unit_price=p) for d, q, p in lines
    ]
    session.add(order)
    session.flush()
    return order


# --- Creating invoices from the synthetic orders ---


def test_one_invoice_per_order_numbered_from_ar_1001(invoiced):
    orders = load("sales_orders.json")
    assert sorted(invoiced, key=lambda n: int(n[3:])) == [
        f"AR-{1001 + i}" for i in range(len(orders))
    ]


def test_numbers_match_the_phase_9_plan(invoiced):
    # Bank payments in data/ar/ground_truth.json refer to these exact numbers.
    for planned in load("planned_invoices.json"):
        invoice = invoiced[planned["invoice_number"]]
        assert invoice.sales_order.order_number == planned["sales_order_number"]
        assert invoice.customer.account_number == planned["customer_account"]
        assert str(invoice.total) == planned["total"]


def test_net_30_due_dates_and_open_balances(invoiced):
    for invoice in invoiced.values():
        assert invoice.payment_terms == "Net 30"
        assert invoice.due_date == invoice.invoice_date + timedelta(days=30)
        assert invoice.status == "open"
        assert invoice.amount_paid == Decimal("0.00")
        assert invoice.balance == invoice.total


def test_line_amounts_add_up_and_money_is_decimal(invoiced):
    for invoice in invoiced.values():
        assert all(isinstance(v, Decimal) for v in (invoice.subtotal, invoice.tax, invoice.total))
        assert sum(line.amount for line in invoice.line_items) == invoice.subtotal
        assert invoice.subtotal + invoice.tax == invoice.total
        for line in invoice.line_items:
            assert line.amount == (line.quantity * line.unit_price).quantize(Decimal("0.01"))


def test_rerunning_creates_no_duplicates(seeded, invoiced, pdf_dir):
    again = create_invoices_from_orders(seeded, pdf_dir)
    seed_ar_reference(seeded, AR_DIR)

    assert again == []
    assert count(seeded, ARInvoice) == len(invoiced)
    assert len(list(pdf_dir.glob("*.pdf"))) == len(invoiced)


def test_each_invoice_creation_is_audited_as_an_ar_subject(seeded, invoiced):
    events = seeded.scalars(select(AuditEvent).where(AuditEvent.action == "ar_invoice_created"))
    events = list(events)
    assert len(events) == len(invoiced)
    assert {e.subject_type for e in events} == {"ar_invoice"}
    assert all(e.invoice_id is None for e in events)  # AP column stays untouched
    assert {e.subject_id for e in events} == {inv.id for inv in invoiced.values()}


# --- Edge cases: never invent terms, amounts, or totals ---


def test_cancelled_orders_are_not_invoiced(seeded, pdf_dir):
    add_order(
        seeded, "SO-9001", [("Onsite training session", 1, Decimal("1200.00"))], status="cancelled"
    )

    created = create_invoices_from_orders(seeded, pdf_dir)

    assert "SO-9001" not in {inv.sales_order.order_number for inv in created}


def test_unknown_payment_terms_leave_due_date_empty(seeded, pdf_dir):
    add_order(
        seeded,
        "SO-9002",
        [("Cloud backup, monthly", 2, Decimal("180.00"))],
        terms="Upon receipt-ish",
    )

    (invoice,) = [
        i for i in create_invoices_from_orders(seeded, pdf_dir) if i.invoice_number == "AR-1025"
    ]

    assert invoice.due_date is None  # never invent a term
    assert invoice.status == "draft"


def test_missing_unit_price_keeps_amounts_null_not_zero(seeded, pdf_dir):
    add_order(seeded, "SO-9003", [("Hardware maintenance visit", 1, None)])

    (invoice,) = [
        i for i in create_invoices_from_orders(seeded, pdf_dir) if i.invoice_number == "AR-1025"
    ]

    assert invoice.line_items[0].amount is None
    assert invoice.subtotal is None and invoice.total is None and invoice.balance is None
    assert invoice.status == "draft"
    assert "Total:" not in pdf_text(pdf_dir / "AR-1025.pdf")


def test_total_that_disagrees_with_the_order_becomes_a_draft(seeded, pdf_dir):
    add_order(
        seeded,
        "SO-9004",
        [("Laptop setup and imaging", 2, Decimal("95.00"))],
        total=Decimal("999.00"),
    )

    (invoice,) = [
        i for i in create_invoices_from_orders(seeded, pdf_dir) if i.invoice_number == "AR-1025"
    ]

    assert invoice.total == Decimal("190.00")  # computed from lines, not forced to agree
    assert invoice.status == "draft"
    event = seeded.scalars(select(AuditEvent).where(AuditEvent.subject_id == invoice.id)).one()
    assert "999.00" in event.note


# --- PDF and JSON outputs ---


def test_pdf_shows_customer_dates_terms_lines_and_total(invoiced, pdf_dir):
    invoice = invoiced["AR-1001"]

    text = pdf_text(pdf_dir / "AR-1001.pdf")

    assert invoice.pdf_path.endswith("AR-1001.pdf")
    for expected in (
        invoice.customer.name,
        "AR-1001",
        invoice.invoice_date.isoformat(),
        invoice.due_date.isoformat(),
        "Net 30",
        f"${invoice.total:,.2f}",
    ):
        assert expected in text, expected
    for line in invoice.line_items:
        assert line.description in text


def test_api_lists_and_shows_invoices_as_json(client, engine, pdf_dir):
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        seed_ar_reference(session, AR_DIR)
        create_invoices_from_orders(session, pdf_dir)

    listing = client.get("/api/ar/invoices").json()
    detail = client.get("/api/ar/invoices/AR-1001").json()

    assert len(listing) == len(load("sales_orders.json"))
    for key in (
        "invoice_number",
        "customer_name",
        "invoice_date",
        "due_date",
        "total",
        "balance",
        "status",
    ):
        assert key in listing[0]
    assert detail["invoice_number"] == "AR-1001"
    assert detail["payment_terms"] == "Net 30"
    assert detail["line_items"]
    assert Decimal(detail["subtotal"]) + Decimal(detail["tax"]) == Decimal(detail["total"])
    assert client.get("/api/ar/invoices/AR-9999").status_code == 404


def test_api_serves_the_invoice_pdf(client, engine, pdf_dir):
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        seed_ar_reference(session, AR_DIR)
        create_invoices_from_orders(session, pdf_dir)

    response = client.get("/api/ar/invoices/AR-1001/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


# --- The generator script and the committed fixtures ---


def test_generator_writes_pdfs_and_index_and_is_repeatable(tmp_path):
    out_dir = tmp_path / "invoices"

    first = generate_ar_invoice_files(AR_DIR, out_dir)
    snapshot = {p.name: p.read_bytes() for p in out_dir.iterdir()}
    generate_ar_invoice_files(AR_DIR, out_dir)

    assert len(first) == len(load("sales_orders.json"))
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} == snapshot  # same files, same bytes
    index = json.loads((out_dir / "index.json").read_text())
    entry = index[0]
    for key in ("filename", "invoice_number", "customer_account", "customer_name",
                "subtotal", "tax", "total", "invoice_date", "due_date"):  # fmt: skip
        assert key in entry
    assert {e["filename"] for e in index} == {p.name for p in out_dir.glob("*.pdf")}


def test_committed_invoice_fixtures_match_the_generator(tmp_path):
    fresh = tmp_path / "invoices"
    generate_ar_invoice_files(AR_DIR, fresh)
    committed = AR_DIR / "invoices"

    assert {p.name for p in committed.iterdir()} == {p.name for p in fresh.iterdir()}
    for path in fresh.iterdir():
        assert (committed / path.name).read_bytes() == path.read_bytes(), path.name


def test_ar_bootstrap_fills_an_empty_database_once(session, pdf_dir):
    from app.services.demo import bootstrap_ar_demo

    first = bootstrap_ar_demo(session, AR_DIR, pdf_dir)
    second = bootstrap_ar_demo(session, AR_DIR, pdf_dir)

    assert first == len(load("sales_orders.json"))
    assert second == 0
    assert count(session, Customer) == len(load("customers.json"))
