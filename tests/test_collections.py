import re
import socket
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.models_ar import ARInvoice, Customer
from app.services import collections
from app.services.ar.invoicing import create_invoices_from_orders
from app.services.ar.seed import seed_ar_reference
from app.services.cash_application import apply_payments
from app.services.llm.factory import get_reminder_writer
from app.services.llm.payment_matcher_mock import MockPaymentMatcher
from app.services.llm.reminder_writer import LLMReminderWriter, MockReminderWriter, ReminderWording
from app.services.review import ReviewError
from tests.conftest import DATA_DIR

ROOT = Path(__file__).resolve().parents[1]
AR_DIR = DATA_DIR / "ar"
AS_OF = date(2026, 11, 30)  # fixed, so tests never depend on today's date


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Drafting and reviewing reminders must never open a network connection."""

    def refuse(*args, **kwargs):
        raise AssertionError("tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# --- Hand-built customers ---

_seq = iter(range(1, 10_000))


def customer(session, name="Testcase Dental LLC"):
    c = Customer(name=name, account_number=f"T-{next(_seq)}", payment_terms="Net 30")
    session.add(c)
    session.flush()
    return c


def invoice(session, cust, days_late, balance="500.00", status="open"):
    inv = ARInvoice(
        invoice_number=f"AR-{8000 + next(_seq)}",
        customer=cust,
        invoice_date=AS_OF - timedelta(days=days_late + 30),
        due_date=AS_OF - timedelta(days=days_late),
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


def draft_for(session, cust, writer=None):
    created = collections.generate_reminders(session, AS_OF, writer or MockReminderWriter())
    return next(r for r in created if r.customer_id == cust.id)


def audits(session, reminder):
    query = select(AuditEvent).where(AuditEvent.subject_id == reminder.id).order_by(AuditEvent.at)
    return list(session.scalars(query))


# --- Who gets a draft, and in what tone ---


@pytest.mark.parametrize(
    ("oldest_days", "tone"),
    [(1, "polite"), (30, "polite"), (31, "firm"), (60, "firm"), (61, "final"), (200, "final")],
)
def test_tone_comes_from_the_oldest_past_due_invoice(session, oldest_days, tone):
    cust = customer(session)
    invoice(session, cust, days_late=oldest_days)
    invoice(session, cust, days_late=3)  # a younger one does not soften the tone

    assert draft_for(session, cust).tone == tone


def test_customers_with_nothing_past_due_get_no_draft(session):
    on_time = customer(session, "Ontime Testing LLC")
    invoice(session, on_time, days_late=0)  # due today: not past due
    invoice(session, on_time, days_late=-10)
    paid = customer(session, "Paidup Testing LLC")
    invoice(session, paid, days_late=90, status="paid", balance="0.00")

    created = collections.generate_reminders(session, AS_OF, MockReminderWriter())

    assert {r.customer_id for r in created}.isdisjoint({on_time.id, paid.id})


def test_body_lists_past_due_invoices_due_dates_and_balances(session):
    cust = customer(session)
    old = invoice(session, cust, days_late=45, balance="1250.00")
    young = invoice(session, cust, days_late=12, balance="80.50")
    not_due = invoice(session, cust, days_late=-5, balance="999.00")

    reminder = draft_for(session, cust)

    for inv in (old, young):
        assert inv.invoice_number in reminder.body
        assert inv.due_date.isoformat() in reminder.body
    assert "$1,250.00" in reminder.body and "$80.50" in reminder.body
    assert "$1,330.50" in reminder.body  # total past due
    assert not_due.invoice_number not in reminder.body
    assert cust.name in reminder.body and cust.name in reminder.subject
    assert reminder.body.rstrip().endswith("Example Co (fictional)")


def test_new_drafts_wait_for_review_and_are_audited(session):
    cust = customer(session)
    invoice(session, cust, days_late=40)

    reminder = draft_for(session, cust)

    assert reminder.status == "needs_review"
    assert reminder.approved_by is None
    assert reminder.as_of_date == AS_OF
    (event,) = audits(session, reminder)
    assert (event.action, event.subject_type, event.invoice_id) == (
        "reminder_drafted", "collection_reminder", None,
    )  # fmt: skip
    assert reminder in collections.list_reminders(session, status="needs_review")


def test_regenerating_does_not_duplicate_a_waiting_draft(session):
    cust = customer(session)
    invoice(session, cust, days_late=40)
    first = draft_for(session, cust)

    again = collections.generate_reminders(session, AS_OF, MockReminderWriter())
    later = collections.generate_reminders(session, AS_OF + timedelta(days=7), MockReminderWriter())

    assert first.id not in {r.id for r in again + later}
    assert cust.id not in {r.customer_id for r in again + later}


def test_an_approved_draft_does_not_block_next_weeks_draft_but_does_block_today(session):
    cust = customer(session)
    invoice(session, cust, days_late=40)
    collections.approve(session, draft_for(session, cust))

    same_day = collections.generate_reminders(session, AS_OF, MockReminderWriter())
    next_week = collections.generate_reminders(
        session, AS_OF + timedelta(days=7), MockReminderWriter()
    )

    assert cust.id not in {r.customer_id for r in same_day}
    assert cust.id in {r.customer_id for r in next_week}


# --- Human review: approve, edit, reject. Nothing is ever sent. ---


@pytest.fixture
def waiting(session):
    cust = customer(session)
    invoice(session, cust, days_late=40)
    return draft_for(session, cust)


def test_approve_sets_approved_audits_and_sends_nothing(session, waiting):
    collections.approve(session, waiting)

    assert waiting.status == "approved"
    assert waiting.approved_by == "ap-specialist"
    assert audits(session, waiting)[-1].action == "reminder_approved"
    with pytest.raises(ReviewError):
        collections.approve(session, waiting)  # only drafts waiting for review


def test_edit_audits_before_and_after_text(session, waiting):
    old_subject, old_body = waiting.subject, waiting.body

    collections.edit(
        session, waiting, subject="Checking in on open invoices", body="Hello,\n\nUpdated text."
    )

    assert waiting.subject == "Checking in on open invoices"
    assert waiting.status == "needs_review"
    event = audits(session, waiting)[-1]
    assert event.action == "reminder_edited"
    assert event.before_json == {"subject": old_subject, "body": old_body}
    assert event.after_json == {
        "subject": "Checking in on open invoices",
        "body": "Hello,\n\nUpdated text.",
    }


def test_edit_rejects_empty_text_and_locked_drafts(session, waiting):
    with pytest.raises(ReviewError):
        collections.edit(session, waiting, subject="  ", body=waiting.body)
    collections.approve(session, waiting)
    with pytest.raises(ReviewError):
        collections.edit(session, waiting, subject="New", body="New body")


def test_reject_needs_a_reason_audits_and_sends_nothing(session, waiting):
    with pytest.raises(ReviewError):
        collections.reject(session, waiting, " ")

    collections.reject(session, waiting, "Customer already called to arrange payment")

    assert waiting.status == "rejected"
    event = audits(session, waiting)[-1]
    assert (event.action, event.note) == (
        "reminder_rejected",
        "Customer already called to arrange payment",
    )


def test_there_is_no_sent_status(session, waiting):
    assert collections.STATUSES == ("needs_review", "approved", "rejected")
    waiting.status = "sent"
    with pytest.raises(IntegrityError):  # the database itself refuses it
        session.flush()


def test_no_code_can_send_email():
    # A module rule enforced by a test: nothing in the app or scripts may import or call a
    # mail sender. Add a reviewed exception here only if the project rules ever change.
    forbidden = re.compile(
        r"smtplib|sendmail|send_mail|send_email|email\.mime|sendgrid|mailgun|postmark"
        r"|\bboto3\b|ses\.send|aiosmtplib|\b(?:import|from)\s+resend\b|resend\.Emails",
        re.IGNORECASE,
    )
    offenders = [
        f"{path.relative_to(ROOT)}:{n}"
        for folder in ("app", "scripts")
        for path in (ROOT / folder).rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if forbidden.search(line)
    ]
    assert offenders == []
    assert not [name for name in dir(collections) if name.lower().startswith("send")]


# --- A real model may only draft wording ---


class FakeStructuredProvider:
    """Stands in for a real provider. Never touches the network."""

    name = "fake-llm"
    last_token_usage = 0

    def __init__(self, opening, closing):
        self.opening, self.closing, self.calls = opening, closing, 0

    def structured_completion(self, system, user, schema):
        self.calls += 1
        assert "do not" in system.lower() and "amount" in system.lower()
        return schema(opening=self.opening, closing=self.closing)


def test_llm_wording_is_used_but_cannot_mark_anything_sent(session):
    cust = customer(session)
    inv = invoice(session, cust, days_late=40, balance="600.00")
    fake = FakeStructuredProvider(
        "This reminder has been sent to your team about open items.",
        "Please reply once payment is on its way.",
    )

    reminder = draft_for(session, cust, LLMReminderWriter(fake))

    assert fake.calls == 1
    assert "has been sent to your team" in reminder.body  # just words in a draft
    assert reminder.status == "needs_review"
    assert inv.invoice_number in reminder.body and "$600.00" in reminder.body  # table from code


def test_llm_wording_with_numbers_or_amounts_falls_back_to_the_template(session):
    cust = customer(session)
    invoice(session, cust, days_late=40)
    mock_body = draft_for(session, cust).body
    collections.reject(session, collections.list_reminders(session)[0], "redo")

    fake = FakeStructuredProvider("You owe $9,999 on 3 invoices.", "Pay within 5 days.")
    reminder = collections.generate_reminders(
        session, AS_OF + timedelta(days=1), LLMReminderWriter(fake)
    )[0]

    assert "$9,999" not in reminder.body
    assert reminder.body.split("\n\n")[1] == mock_body.split("\n\n")[1]  # template opening


def test_wording_schema_and_default_writer():
    assert set(ReminderWording.model_fields) == {"opening", "closing"}
    assert isinstance(get_reminder_writer(), MockReminderWriter)


# --- The synthetic fixtures and the review routes ---


@pytest.fixture
def fixtures(session, tmp_path):
    seed_ar_reference(session, AR_DIR)
    create_invoices_from_orders(session, tmp_path / "pdfs")
    apply_payments(session, MockPaymentMatcher())
    return session


def test_fixture_run_drafts_one_reminder_per_late_customer(fixtures):
    created = collections.generate_reminders(fixtures, AS_OF, MockReminderWriter())
    names = {r.customer.name: r.tone for r in created}

    assert "Blue Harbor Retail Group" not in names  # everything paid
    assert names["Amberfield Clinics LLC"] == "final"  # oldest 68 days late
    assert names["Cedar & Elm Wholesale"] == "firm"  # oldest 60 days late
    assert len(created) == 7


@pytest.fixture
def loaded(engine, tmp_path):
    with Session(engine) as s:
        seed_ar_reference(s, AR_DIR)
        create_invoices_from_orders(s, tmp_path / "pdfs")
        apply_payments(s, MockPaymentMatcher())


def test_json_routes_generate_list_and_review(client, loaded):
    made = client.post("/api/ar/reminders/generate", params={"as_of": AS_OF.isoformat()}).json()
    assert len(made) == 7 and {r["status"] for r in made} == {"needs_review"}

    first, second, third = (r["id"] for r in made[:3])
    assert client.post(f"/api/ar/reminders/{first}/approve").json()["status"] == "approved"
    assert client.post(f"/api/ar/reminders/{first}/approve").status_code == 409
    rejected = client.post(f"/api/ar/reminders/{second}/reject", json={"reason": "Paid by phone"})
    assert rejected.json()["status"] == "rejected"
    edited = client.post(f"/api/ar/reminders/{third}/edit", json={"subject": "Hi", "body": "Text"})
    assert edited.json()["subject"] == "Hi"
    assert len(client.get("/api/ar/reminders", params={"status": "needs_review"}).json()) == 5


def test_review_page_has_no_send_button_and_queue_still_works(client, loaded):
    client.post("/api/ar/reminders/generate", params={"as_of": AS_OF.isoformat()})

    page = client.get("/receivables/reminders")

    assert page.status_code == 200
    assert "Amberfield Clinics LLC" in page.text
    assert "not emailed" in page.text.lower()
    assert not re.search(r">\s*send\b", page.text, re.IGNORECASE)  # no "Send" button anywhere
    ref = client.get("/api/ar/reminders").json()[0]["id"]
    response = client.post(f"/receivables/reminders/{ref}/approve", follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/queue").status_code == 200
