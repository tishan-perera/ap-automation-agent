import builtins
import json
import pathlib
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.models_ar import ARInvoice, BankPayment
from app.services.ar import payment_review
from app.services.ar.invoicing import create_invoices_from_orders
from app.services.ar.seed import seed_ar_reference
from app.services.cash_application import apply_payment, apply_payments
from app.services.llm.anthropic_provider import AnthropicProvider
from app.services.llm.factory import get_payment_matcher
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.payment_matcher import LLMMatchOutput, LLMPaymentMatcher
from app.services.llm.payment_matcher_mock import MockPaymentMatcher
from app.services.review import ReviewError
from tests.conftest import DATA_DIR

AR_DIR = DATA_DIR / "ar"


@pytest.fixture
def ar(session, tmp_path):
    """Seeded customers, orders, fixture bank payments (unprocessed), and 24 open invoices."""
    seed_ar_reference(session, AR_DIR)
    create_invoices_from_orders(session, tmp_path / "pdfs")
    return session


def inv(session, number) -> ARInvoice:
    return session.scalars(select(ARInvoice).where(ARInvoice.invoice_number == number)).one()


def payer_for(session, number) -> str:
    return inv(session, number).customer.name.upper()


_refs = iter(range(1, 10_000))


def add_payment(session, amount, memo, payer, paid_on=date(2026, 11, 2)) -> BankPayment:
    payment = BankPayment(
        payment_ref=f"TEST-{next(_refs):04d}",
        payment_date=paid_on,
        amount=Decimal(amount),
        currency="USD",
        memo=memo,
        payer_name_raw=payer,
        status="unmatched",
    )
    session.add(payment)
    session.flush()
    return payment


def apps(payment):
    return [(a.invoice.invoice_number, a.amount_applied) for a in payment.applications]


def audits(session, payment):
    query = select(AuditEvent).where(AuditEvent.subject_id == payment.id).order_by(AuditEvent.at)
    return list(session.scalars(query))


# --- Matching rules ---


def test_clear_invoice_number_and_exact_amount_auto_posts(ar):
    invoice = inv(ar, "AR-1016")
    payment = add_payment(ar, invoice.balance, "Payment for AR-1016", payer_for(ar, "AR-1016"))

    apply_payment(ar, payment, MockPaymentMatcher())

    assert payment.status == "posted"
    assert payment.confidence >= 0.90
    assert apps(payment) == [("AR-1016", invoice.total)]
    assert payment.applications[0].posted_at is not None
    assert payment.applications[0].match_kind == "invoice_number"
    assert invoice.balance == Decimal("0.00")
    assert invoice.amount_paid == invoice.total
    assert invoice.status == "paid"
    assert audits(ar, payment)[-1].action == "payment_auto_posted"


def test_amount_within_one_cent_still_counts_as_exact(ar):
    invoice = inv(ar, "AR-1016")
    payment = add_payment(
        ar, invoice.balance - Decimal("0.01"), "AR-1016", payer_for(ar, "AR-1016")
    )

    apply_payment(ar, payment, MockPaymentMatcher())

    assert payment.status == "posted"
    assert invoice.status == "paid"
    assert invoice.balance == Decimal("0.00")


def test_partial_payment_waits_for_review_and_invoice_stays_open(ar):
    invoice = inv(ar, "AR-1013")
    half = (invoice.balance / 2).quantize(Decimal("0.01"))
    payment = add_payment(ar, half, "Partial payment AR-1013", payer_for(ar, "AR-1013"))

    apply_payment(ar, payment, MockPaymentMatcher())

    assert payment.status == "needs_review"
    assert payment.applications[0].match_kind == "partial"
    assert apps(payment) == [("AR-1013", half)]
    assert payment.applications[0].posted_at is None  # proposed, not posted
    assert invoice.status == "open"
    assert invoice.balance == invoice.total


def test_split_with_numbers_and_exact_sum_auto_posts(ar):
    a, b = inv(ar, "AR-1009"), inv(ar, "AR-1017")  # both Amberfield Clinics
    total = a.balance + b.balance
    payment = add_payment(ar, total, "AR-1009, AR-1017", payer_for(ar, "AR-1009"))

    apply_payment(ar, payment, MockPaymentMatcher())

    assert payment.status == "posted"
    assert {kind for kind in (x.match_kind for x in payment.applications)} == {"split"}
    assert sorted(apps(payment)) == [("AR-1009", a.total), ("AR-1017", b.total)]
    assert a.status == b.status == "paid"


def test_split_that_does_not_add_up_needs_review(ar):
    a, b = inv(ar, "AR-1009"), inv(ar, "AR-1017")
    short = a.balance + b.balance - Decimal("100.00")
    payment = add_payment(ar, short, "AR-1009 AR-1017", payer_for(ar, "AR-1009"))

    apply_payment(ar, payment, MockPaymentMatcher())

    assert payment.status == "needs_review"
    assert a.status == b.status == "open"


def test_overpayment_needs_review_and_records_leftover(ar):
    invoice = inv(ar, "AR-1015")
    payment = add_payment(
        ar, invoice.balance + Decimal("40.00"), "Payment AR-1015", payer_for(ar, "AR-1015")
    )

    apply_payment(ar, payment, MockPaymentMatcher())

    assert payment.status == "needs_review"
    assert payment.applications[0].match_kind == "overpayment"
    assert apps(payment) == [("AR-1015", invoice.total)]  # never "fully applied"
    assert payment.match_details["unapplied_amount"] == "40.00"
    assert invoice.status == "open"


@pytest.mark.parametrize(
    ("memo", "payer"),
    [("", ""), ("ONLINE TRANSFER", "ACH CREDIT"), ("#%&*?~ 0x9 ^^", "")],
)
def test_unidentified_payment_invents_nothing(ar, memo, payer):
    payment = add_payment(ar, "777.77", memo, payer)

    apply_payment(ar, payment, MockPaymentMatcher())

    assert payment.status == "unidentified"
    assert payment.applications == []
    assert payment.match_details["customer_account"] is None


def test_memo_invoice_from_a_different_customer_needs_review(ar):
    invoice = inv(ar, "AR-1020")  # Riverstone, but paid by Amberfield
    payment = add_payment(ar, invoice.balance, "AR-1020", payer_for(ar, "AR-1009"))

    apply_payment(ar, payment, MockPaymentMatcher())

    assert payment.status == "needs_review"
    assert payment.confidence < 0.90
    assert invoice.status == "open"


def test_bare_digits_count_only_for_the_payers_own_invoices(ar):
    invoice = inv(ar, "AR-1016")  # Maple & Pine
    payment = add_payment(ar, invoice.balance, "ref 1016", "ACH CREDIT")  # unknown payer

    apply_payment(ar, payment, MockPaymentMatcher())

    assert payment.status == "unidentified"


def test_mock_never_reads_the_answer_key(ar, monkeypatch):
    real_open, real_read = builtins.open, pathlib.Path.read_text

    def guarded_open(file, *args, **kwargs):
        assert "ground_truth" not in str(file), "matcher read the answer key"
        return real_open(file, *args, **kwargs)

    def guarded_read(self, *args, **kwargs):
        assert "ground_truth" not in str(self), "matcher read the answer key"
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(pathlib.Path, "read_text", guarded_read)

    assert apply_payments(ar, MockPaymentMatcher())


def test_fixture_feed_matches_the_answer_key(ar):
    truth = json.loads((AR_DIR / "ground_truth.json").read_text())

    for payment in apply_payments(ar, MockPaymentMatcher()):
        expected = truth[payment.payment_ref]
        assert payment.status == expected["expected_status"], payment.payment_ref
        got = sorted((n, str(a)) for n, a in apps(payment))
        want = sorted((a["invoice_number"], a["amount"]) for a in expected["applications"])
        assert got == want, payment.payment_ref


# --- Human review: approve, reject, edit ---


def needs_review_partial(ar):
    invoice = inv(ar, "AR-1013")
    payment = add_payment(ar, "500.00", "Partial payment AR-1013", payer_for(ar, "AR-1013"))
    apply_payment(ar, payment, MockPaymentMatcher())
    return payment, invoice


def test_approve_posts_audits_and_updates_balances(ar):
    payment, invoice = needs_review_partial(ar)
    total = invoice.total

    payment_review.approve(ar, payment)

    assert payment.status == "posted"
    assert payment.applications[0].approved_by == "ap-specialist"
    assert invoice.amount_paid == Decimal("500.00")
    assert invoice.balance == total - Decimal("500.00")
    assert invoice.status == "partial"
    event = audits(ar, payment)[-1]
    assert event.action == "payment_application_approved"
    assert event.subject_type == "bank_payment"
    assert event.invoice_id is None  # the AP column is untouched
    with pytest.raises(ReviewError):
        payment_review.approve(ar, payment)  # cannot post twice


def test_reject_leaves_invoices_unchanged_and_audits(ar):
    payment, invoice = needs_review_partial(ar)

    payment_review.reject(ar, payment, "Customer says this was a deposit")

    assert payment.status == "unidentified"
    assert payment.applications == []
    assert invoice.balance == invoice.total and invoice.status == "open"
    event = audits(ar, payment)[-1]
    assert event.action == "payment_application_rejected"
    assert event.note == "Customer says this was a deposit"


def test_reject_requires_a_reason(ar):
    payment, _ = needs_review_partial(ar)
    with pytest.raises(ReviewError):
        payment_review.reject(ar, payment, "  ")


def test_edit_validates_then_replaces_the_proposal(ar):
    payment, invoice = needs_review_partial(ar)

    for bad in (
        [("AR-9999", "100.00")],  # unknown invoice: never invented
        [("AR-1013", "0.00")],  # amounts must be positive
        [("AR-1013", "600.00")],  # more than the payment
        [("AR-1019", "300.00")],  # more than that invoice's open balance ($95.00)
    ):
        with pytest.raises(ReviewError):
            payment_review.edit(ar, payment, bad)

    payment_review.edit(ar, payment, [("AR-1013", "300.00")])

    assert apps(payment) == [("AR-1013", Decimal("300.00"))]
    assert payment.status == "needs_review"
    assert payment.match_details["unapplied_amount"] == "200.00"
    assert audits(ar, payment)[-1].action == "payment_application_edited"
    assert invoice.balance == invoice.total  # nothing posted until approval

    payment_review.approve(ar, payment)
    assert invoice.amount_paid == Decimal("300.00")


def test_unidentified_payment_can_be_assigned_by_a_person(ar):
    invoice = inv(ar, "AR-1016")
    payment = add_payment(ar, invoice.balance, "", "")
    apply_payment(ar, payment, MockPaymentMatcher())

    payment_review.edit(ar, payment, [("AR-1016", str(invoice.balance))])
    payment_review.approve(ar, payment)

    assert invoice.status == "paid"


# --- Real LLM matchers: told never to guess, and validated anyway ---


class FakeStructuredProvider:
    """Stands in for a real provider. Never touches the network."""

    name = "fake-llm"

    def __init__(self, output):
        self.output = output
        self.calls = 0
        self.last_token_usage = 0

    def structured_completion(self, system, user, schema):
        self.calls += 1
        assert "never guess" in system.lower()
        return schema.model_validate(self.output)


def test_llm_suggesting_an_unknown_invoice_is_never_posted(ar):
    invoice = inv(ar, "AR-1016")
    payment = add_payment(ar, invoice.balance, "AR-1016", payer_for(ar, "AR-1016"))
    fake = FakeStructuredProvider(
        {"invoice_numbers": ["AR-7777"], "confidence": 0.99, "explanation": "looks right"}
    )

    apply_payment(ar, payment, LLMPaymentMatcher(fake))

    assert fake.calls == 1
    assert payment.status != "posted"
    assert all(a.invoice.invoice_number != "AR-7777" for a in payment.applications)
    assert invoice.status == "open"


def test_llm_confidence_cannot_post_an_incomplete_match(ar):
    invoice = inv(ar, "AR-1013")
    payment = add_payment(ar, "100.00", "AR-1013", payer_for(ar, "AR-1013"))
    fake = FakeStructuredProvider(
        {"invoice_numbers": ["AR-1013"], "confidence": 0.99, "explanation": "partial"}
    )

    apply_payment(ar, payment, LLMPaymentMatcher(fake))

    assert payment.status == "needs_review"
    assert invoice.status == "open"


def test_llm_is_not_asked_about_payments_with_no_candidates(ar):
    payment = add_payment(ar, "777.77", "", "")
    fake = FakeStructuredProvider({"invoice_numbers": [], "confidence": 0.0, "explanation": ""})

    apply_payment(ar, payment, LLMPaymentMatcher(fake))

    assert fake.calls == 0
    assert payment.status == "unidentified"


def test_llm_output_schema_and_real_providers_support_structured_calls():
    assert set(LLMMatchOutput.model_fields) == {"invoice_numbers", "confidence", "explanation"}
    for cls in (OpenAIProvider, AnthropicProvider, GeminiProvider):
        assert callable(cls(api_key="fake-key-for-tests").structured_completion)


def test_default_payment_matcher_is_the_mock():
    assert isinstance(get_payment_matcher(), MockPaymentMatcher)


# --- Review routes ---


@pytest.fixture
def review_ready(engine, tmp_path):
    with Session(engine) as session:
        seed_ar_reference(session, AR_DIR)
        create_invoices_from_orders(session, tmp_path / "pdfs")
        apply_payments(session, MockPaymentMatcher())


def test_api_lists_payments_needing_review(client, review_ready):
    rows = client.get("/api/ar/payments", params={"status": "needs_review"}).json()

    assert rows and {r["status"] for r in rows} == {"needs_review"}
    assert {"payment_ref", "amount", "memo", "applications", "confidence"} <= set(rows[0])


def test_api_approve_reject_and_edit(client, review_ready):
    rows = client.get("/api/ar/payments", params={"status": "needs_review"}).json()
    first, second = rows[0]["payment_ref"], rows[1]["payment_ref"]

    assert client.post(f"/api/ar/payments/{first}/approve").json()["status"] == "posted"
    assert client.post(f"/api/ar/payments/{first}/approve").status_code == 409
    rejected = client.post(f"/api/ar/payments/{second}/reject", json={"reason": "Wrong customer"})
    assert rejected.json()["status"] == "unidentified"
    bad_edit = client.post(
        f"/api/ar/payments/{second}/edit",
        json={"applications": [{"invoice_number": "AR-9999", "amount": "1.00"}]},
    )
    assert bad_edit.status_code == 400


def test_html_review_page_shows_queue_and_approves(client, review_ready):
    page = client.get("/receivables/payments")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    ref = client.get("/api/ar/payments", params={"status": "needs_review"}).json()[0]["payment_ref"]
    assert ref in page.text
    response = client.post(f"/receivables/payments/{ref}/approve", follow_redirects=False)
    assert response.status_code == 303
    assert client.get(f"/api/ar/payments/{ref}").json()["status"] == "posted"


def test_each_held_payment_says_why_it_needs_a_person(ar):
    cases = {
        "overpayment": ("AR-1015", Decimal("40.00"), "more than"),
        "partial": ("AR-1013", Decimal("-100.00"), "part of"),
    }
    for number, delta, phrase in cases.values():
        invoice = inv(ar, number)
        payment = add_payment(ar, invoice.balance + delta, number, payer_for(ar, number))
        apply_payment(ar, payment, MockPaymentMatcher())
        assert phrase in payment.match_details["review_reason"].lower()

    unknown = add_payment(ar, "777.77", "", "")
    apply_payment(ar, unknown, MockPaymentMatcher())
    assert "no invoice number" in unknown.match_details["review_reason"].lower()

    clean = add_payment(ar, inv(ar, "AR-1016").balance, "AR-1016", payer_for(ar, "AR-1016"))
    apply_payment(ar, clean, MockPaymentMatcher())
    assert clean.match_details["review_reason"] is None  # auto-posted: nothing to explain
