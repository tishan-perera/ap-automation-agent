import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models import Invoice
from app.models_ar import ARInvoice, BankPayment, Customer, PaymentApplication
from app.services.ar.invoicing import create_invoices_from_orders
from app.services.ar.seed import seed_ar_reference
from app.services.cash_application import apply_payments
from app.services.cash_dashboard import build_cash
from app.services.llm.payment_matcher_mock import MockPaymentMatcher
from tests.conftest import DATA_DIR

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "web" / "static"
TEMPLATES = ROOT / "app" / "web" / "templates"
AS_OF = date(2026, 10, 15)  # fixed; the synthetic invoices fall due around this date

_seq = iter(range(1, 10_000))


# --- Hand-built rows, so every total can be checked exactly ---


def ap_invoice(session, status, due_in_days, total="100.00"):
    n = next(_seq)
    inv = Invoice(
        filename=f"t{n}.pdf",
        file_hash=f"hash-{n}",
        status=status,
        vendor_name="Testcase Vendor LLC",
        invoice_number=f"TV-{n}",
        due_date=None if due_in_days is None else AS_OF + timedelta(days=due_in_days),
        total=None if total is None else Decimal(total),
    )
    session.add(inv)
    session.flush()
    return inv


def ar_invoice(session, due_in_days, balance="200.00", status="open"):
    n = next(_seq)
    cust = Customer(name=f"Testcase Customer {n}", account_number=f"T-{n}", payment_terms="Net 30")
    inv = ARInvoice(
        invoice_number=f"AR-{8000 + n}",
        customer=cust,
        invoice_date=AS_OF - timedelta(days=30),
        due_date=None if due_in_days is None else AS_OF + timedelta(days=due_in_days),
        payment_terms="Net 30",
        subtotal=Decimal(balance),
        tax=Decimal("0.00"),
        total=Decimal(balance),
        amount_paid=Decimal("0.00"),
        balance=Decimal(balance),
        status=status,
    )
    session.add(inv)
    session.flush()
    return inv


def posted_payment(session, inv, amount, posted=True):
    payment = BankPayment(
        payment_ref=f"T-BP-{next(_seq)}",
        payment_date=AS_OF - timedelta(days=3),
        amount=Decimal(amount),
        status="posted" if posted else "needs_review",
    )
    payment.applications = [
        PaymentApplication(
            invoice=inv,
            amount_applied=Decimal(amount),
            match_kind="invoice_number",
            posted_at=datetime.now(UTC) if posted else None,
        )
    ]
    session.add(payment)
    session.flush()


def test_money_in_counts_posted_payments_and_ar_due_in_the_window(session):
    ar_invoice(session, due_in_days=0, balance="200.00")  # due today: in
    ar_invoice(session, due_in_days=30, balance="300.00")  # last day of the window: in
    ar_invoice(session, due_in_days=31, balance="999.00")  # after the window: out
    ar_invoice(session, due_in_days=-5, balance="50.00")  # already past due: separate total
    ar_invoice(session, due_in_days=10, balance="0.00", status="paid")  # paid: out
    paid_by = ar_invoice(session, due_in_days=-40, balance="0.00", status="paid")
    posted_payment(session, paid_by, "400.00")
    posted_payment(session, paid_by, "75.00", posted=False)  # waiting for review: not cash in

    cash = build_cash(session, AS_OF)

    assert cash.window_end == AS_OF + timedelta(days=30)
    assert (cash.received_count, cash.received_total) == (1, Decimal("400.00"))
    assert (cash.ar_due_count, cash.ar_due_total) == (2, Decimal("500.00"))
    assert cash.ar_past_due_total == Decimal("50.00")


def test_money_out_uses_approved_exported_and_needs_review_ap_only(session):
    ap_invoice(session, "approved", due_in_days=50, total="120.00")
    ap_invoice(session, "exported", due_in_days=-10, total="80.00")
    ap_invoice(session, "needs_review", due_in_days=5, total="45.90")  # in the window
    ap_invoice(session, "needs_review", due_in_days=45, total="999.00")  # after the window
    ap_invoice(session, "needs_review", due_in_days=-3, total="10.00")  # already past due
    ap_invoice(session, "rejected", due_in_days=5, total="777.00")  # rejected: never counted

    cash = build_cash(session, AS_OF)

    assert (cash.ap_done_count, cash.ap_done_total) == (2, Decimal("200.00"))
    assert (cash.ap_due_count, cash.ap_due_total) == (1, Decimal("45.90"))
    assert cash.ap_past_due_total == Decimal("10.00")


def test_missing_due_dates_are_never_invented(session):
    undated = ap_invoice(session, "needs_review", due_in_days=None, total="60.00")
    ar_invoice(session, due_in_days=None, balance="70.00")

    cash = build_cash(session, AS_OF)

    assert undated.invoice_number not in {row.reference for row in cash.rows}
    assert undated.invoice_number in {row.reference for row in cash.undated}
    assert all(row.day is None for row in cash.undated)
    assert cash.ap_due_total == Decimal("0.00")  # not guessed into the window


def test_missing_amounts_are_listed_but_not_summed(session):
    ap_invoice(session, "needs_review", due_in_days=7, total=None)
    ap_invoice(session, "needs_review", due_in_days=8, total="30.00")

    cash = build_cash(session, AS_OF)

    missing = [row for row in cash.rows if row.amount is None]
    assert len(missing) == 1 and cash.missing_amount_count == 1
    assert cash.ap_due_total == Decimal("30.00")  # never treated as 0 or guessed


def test_dated_list_is_sorted_by_date_and_marks_only_human_items(session):
    ap_invoice(session, "needs_review", due_in_days=9, total="40.00")
    ar_invoice(session, due_in_days=2, balance="20.00")

    rows = build_cash(session, AS_OF).rows

    assert [row.day for row in rows] == sorted(row.day for row in rows)
    assert {(row.direction, row.needs_human) for row in rows} == {("out", True), ("in", False)}


def test_needs_human_counts_come_from_existing_queues(session):
    ap_invoice(session, "needs_review", due_in_days=1)
    ap_invoice(session, "approved", due_in_days=1)
    inv = ar_invoice(session, due_in_days=1)
    posted_payment(session, inv, "10.00", posted=False)

    counts = build_cash(session, AS_OF).needs_human

    assert counts == {"ap_review": 1, "payments_review": 1, "reminders_review": 0}


# --- Pages, tabs, and the brand rules ---


@pytest.fixture
def loaded(engine, tmp_path):
    from app.services.demo import bootstrap_demo

    with Session(engine) as s:
        bootstrap_demo(s, DATA_DIR, DATA_DIR / "invoices")
        seed_ar_reference(s, DATA_DIR / "ar")
        create_invoices_from_orders(s, tmp_path / "pdfs")
        apply_payments(s, MockPaymentMatcher())


def nav(html):
    return html.split("<nav", 1)[1].split("</nav>", 1)[0]


def test_top_navigation_has_payables_receivables_and_cash_tabs(client, loaded):
    for path, active in (
        ("/queue", "Payables"),
        ("/receivables", "Receivables"),
        ("/cash", "Cash"),
    ):
        top = nav(client.get(path).text)
        for href, label in (
            ("/queue", "Payables"),
            ("/receivables", "Receivables"),
            ("/cash", "Cash"),
        ):
            assert f'href="{href}"' in top and label in top
        assert re.search(rf'aria-current="page"[^>]*>\s*{active}', top), path


def test_root_and_queue_still_lead_to_the_ap_queue(client, loaded):
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/queue"
    queue = client.get("/queue")
    assert queue.status_code == 200 and "Approval queue" in queue.text


def test_receivables_home_reaches_every_ar_page(client, loaded):
    page = client.get("/receivables")

    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    for href in (
        "/receivables/invoices",
        "/receivables/payments",
        "/receivables/aging",
        "/receivables/risk",
        "/receivables/reminders",
    ):
        assert f'href="{href}' in page.text, href
        assert client.get(href).status_code == 200, href


def test_customer_invoices_page_lists_invoices_with_pdf_links(client, loaded):
    page = client.get("/receivables/invoices").text

    assert "AR-1001" in page and "Amberfield Clinics LLC" in page
    assert 'href="/api/ar/invoices/AR-1001/pdf"' in page


def test_cash_page_shows_money_in_out_and_the_dated_list(client, loaded):
    page = client.get("/cash", params={"as_of": AS_OF.isoformat()})
    text = page.text

    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    for heading in ("Money in", "Money out", "Next 30 days"):
        assert heading in text
    assert "2026-11-14" in text  # the window end, from the as-of date
    assert "Undated" in text
    assert re.search(r'class="[^"]*needs-human[^"]*"', text)  # AP items waiting on a person


def test_cash_figures_match_the_aggregation(client, engine, loaded):
    with Session(engine) as s:
        cash = build_cash(s, AS_OF)

    text = client.get("/cash", params={"as_of": AS_OF.isoformat()}).text

    for amount in (cash.received_total, cash.ar_due_total, cash.ap_done_total, cash.ap_due_total):
        assert f"${amount:,.2f}" in text


def test_settings_still_shows_key_presence_not_values(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key-value-999")
    get_settings.cache_clear()
    page = client.get("/settings").text

    assert 'data-key="gemini">yes' in page
    assert "fake-gemini-key-value-999" not in page


# --- Brand rules, checked in the files themselves ---


def css():
    return (STATIC / "styles.css").read_text()


def test_brand_palette_and_self_hosted_fonts():
    sheet = css()
    for token in ("--navy: #0B1F33", "--teal: #14B8A6", "--amber: #F59E0B", "--paper: #F8FAFC"):
        assert token in sheet, token
    for font in (
        "Inter-Regular.ttf",
        "Inter-Medium.ttf",
        "Inter-SemiBold.ttf",
        "JetBrainsMono-Regular.ttf",
    ):
        assert f"/static/fonts/{font}" in sheet
        assert (STATIC / "fonts" / font).is_file()
    assert '"Inter"' in sheet and '"JetBrains Mono"' in sheet
    assert "tabular-nums" in sheet


def test_amber_is_only_used_for_items_that_need_a_human():
    allowed = {
        ":root",
        ".status-needs_review",
        ".needs-human",
        ".card-waiting",
        ".card-warn",
        ".chip-needs-human",
    }
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css()):
        if re.search(r"amber|#F59E0B", body, re.IGNORECASE):
            for selector in (s.strip() for s in selectors.split(",")):
                assert any(selector.startswith(a) for a in allowed), selector
    for template in TEMPLATES.glob("*.html"):
        assert "#F59E0B" not in template.read_text().upper(), template.name


def test_no_teal_text_on_light_backgrounds_and_no_paths_outside_the_repo():
    assert not re.search(r"(?<![-\w])color:\s*var\(--teal\)", css())
    for path in [*TEMPLATES.glob("*.html"), STATIC / "styles.css"]:
        assert "~/.claude" not in path.read_text() and ".claude/brand" not in path.read_text()


def test_payments_dated_after_the_as_of_date_are_not_counted_as_received(session):
    inv = ar_invoice(session, due_in_days=-40, balance="0.00", status="paid")
    posted_payment(session, inv, "100.00")  # dated 3 days before as_of: counted
    later = BankPayment(
        payment_ref=f"T-BP-{next(_seq)}",
        payment_date=AS_OF + timedelta(days=5),  # after as_of: not received yet as of that date
        amount=Decimal("900.00"),
        status="posted",
    )
    later.applications = [
        PaymentApplication(
            invoice=inv, amount_applied=Decimal("900.00"), match_kind="invoice_number",
            posted_at=datetime.now(UTC),
        )
    ]  # fmt: skip
    session.add(later)
    session.flush()

    cash = build_cash(session, AS_OF)

    assert (cash.received_count, cash.received_total) == (1, Decimal("100.00"))
    assert later not in cash.recent_payments
