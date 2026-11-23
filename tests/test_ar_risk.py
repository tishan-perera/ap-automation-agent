from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.models_ar import ARInvoice, BankPayment, Customer, PaymentApplication
from app.services.ar import payment_review
from app.services.ar.invoicing import create_invoices_from_orders
from app.services.ar.risk import POINTS, assess_customer, assess_risk, level_for
from app.services.ar.seed import seed_ar_reference
from app.services.cash_application import apply_payments
from app.services.llm.factory import get_risk_explainer
from app.services.llm.payment_matcher_mock import MockPaymentMatcher
from app.services.llm.risk_explainer import LLMRiskExplainer, MockRiskExplainer, RiskExplanation
from tests.conftest import DATA_DIR

AR_DIR = DATA_DIR / "ar"
AS_OF = date(2026, 11, 30)  # fixed, so tests never depend on today's date


# --- Hand-built customers, so each rule can be tested on its own ---

_seq = iter(range(1, 10_000))


def customer(session, name="Testcase Studio LLC"):
    c = Customer(name=name, account_number=f"T-{next(_seq)}", payment_terms="Net 30")
    session.add(c)
    session.flush()
    return c


def invoice(session, cust, days_late, balance="1000.00", status="open"):
    n = next(_seq)
    inv = ARInvoice(
        invoice_number=f"AR-{8000 + n}",
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


def partial_payment(session, inv, days_ago, posted=True):
    payment = BankPayment(
        payment_ref=f"T-BP-{next(_seq)}",
        payment_date=AS_OF - timedelta(days=days_ago),
        amount=Decimal("100.00"),
        status="posted" if posted else "needs_review",
    )
    payment.applications = [
        PaymentApplication(
            invoice=inv,
            amount_applied=Decimal("100.00"),
            match_kind="partial",
            posted_at=datetime.now(UTC) if posted else None,
        )
    ]
    session.add(payment)
    session.flush()


def assess(session, cust):
    return assess_customer(session, cust, AS_OF, MockRiskExplainer())


def test_points_and_levels_are_documented_constants():
    assert POINTS == {
        "OLDEST_PAST_DUE_1_30": 0,
        "OLDEST_PAST_DUE_31_60": 20,
        "OLDEST_PAST_DUE_61_90": 40,
        "OLDEST_PAST_DUE_90_PLUS": 60,
        "INVOICES_30_PLUS": 15,  # per invoice, capped at 30
        "PARTIAL_PAY_PATTERN": 10,
    }
    assert [level_for(s) for s in (0, 24, 25, 49, 50, 100)] == [
        "low", "low", "medium", "medium", "high", "high",
    ]  # fmt: skip


def test_customer_with_nothing_past_due_is_low_with_no_rules(session):
    cust = customer(session)
    invoice(session, cust, days_late=-5)  # not due yet

    risk = assess(session, cust)

    assert (risk.score, risk.level, risk.rule_codes) == (0, "low", [])
    assert "no invoices are past due" in risk.explanation.lower()


def test_slightly_late_invoice_is_noted_but_scores_zero(session):
    cust = customer(session)
    invoice(session, cust, days_late=10)

    risk = assess(session, cust)

    assert risk.rule_codes == ["OLDEST_PAST_DUE_1_30"]
    assert (risk.score, risk.level) == (0, "low")


def test_one_invoice_35_days_late_is_medium(session):
    cust = customer(session)
    invoice(session, cust, days_late=35)

    risk = assess(session, cust)

    assert risk.rule_codes == ["OLDEST_PAST_DUE_31_60", "INVOICES_30_PLUS"]
    assert (risk.score, risk.level) == (20 + 15, "medium")


def test_thirty_days_late_counts_toward_invoices_30_plus(session):
    cust = customer(session)
    invoice(session, cust, days_late=30)

    risk = assess(session, cust)

    assert risk.rule_codes == ["OLDEST_PAST_DUE_1_30", "INVOICES_30_PLUS"]
    assert risk.score == 15


def test_invoices_30_plus_is_capped_at_30_points(session):
    cust = customer(session)
    for days in (95, 70, 45, 33):
        invoice(session, cust, days_late=days)

    risk = assess(session, cust)

    assert risk.score == 60 + 30  # oldest 90+ plus the capped count
    assert risk.facts.invoices_30_plus == 4
    assert risk.level == "high"


def test_paid_void_and_draft_invoices_are_ignored(session):
    cust = customer(session)
    invoice(session, cust, days_late=200, status="paid", balance="0.00")
    invoice(session, cust, days_late=200, status="void")
    invoice(session, cust, days_late=200, status="draft")

    assert assess(session, cust).score == 0


def test_partial_pay_pattern_needs_two_posted_partials_in_90_days(session):
    cust = customer(session)
    inv = invoice(session, cust, days_late=-1)  # nothing past due, so only the pattern counts

    partial_payment(session, inv, days_ago=10)
    partial_payment(session, inv, days_ago=100)  # too old
    partial_payment(session, inv, days_ago=5, posted=False)  # not approved yet
    assert assess(session, cust).rule_codes == []

    partial_payment(session, inv, days_ago=40)
    risk = assess(session, cust)
    assert risk.rule_codes == ["PARTIAL_PAY_PATTERN"]
    assert risk.facts.partial_payments_90d == 2
    assert (risk.score, risk.level) == (10, "low")


def test_mock_explanation_states_the_facts(session):
    cust = customer(session)
    inv = invoice(session, cust, days_late=52)
    invoice(session, cust, days_late=33)
    partial_payment(session, inv, days_ago=20)
    partial_payment(session, inv, days_ago=12)

    text = assess(session, cust).explanation

    assert "52 days past due" in text
    assert "2 invoices are 30 or more days past due" in text
    assert "2 recent payments were partial" in text
    assert text.endswith(".")


# --- The model may only phrase the facts; it never changes the score ---


class FakeStructuredProvider:
    """Stands in for a real provider. Never touches the network."""

    name = "fake-llm"
    last_token_usage = 0

    def __init__(self, text):
        self.text = text
        self.calls = 0

    def structured_completion(self, system, user, schema):
        self.calls += 1
        assert "do not change" in system.lower() and "only" in system.lower()
        return schema(explanation=self.text)


def late_customer(session):
    cust = customer(session)
    invoice(session, cust, days_late=52)
    invoice(session, cust, days_late=33)
    return cust


def test_llm_wording_is_used_but_cannot_change_score_or_level(session):
    cust = late_customer(session)
    expected = assess(session, cust)
    fake = FakeStructuredProvider(
        "This customer is very low risk. The oldest invoice is 52 days past due and 2 invoices are late."
    )

    risk = assess_customer(session, cust, AS_OF, LLMRiskExplainer(fake))

    assert fake.calls == 1
    assert risk.explanation.startswith("This customer")
    assert (risk.score, risk.level, risk.rule_codes) == (
        expected.score, expected.level, expected.rule_codes,
    )  # fmt: skip


def test_llm_text_with_an_invented_number_falls_back_to_the_template(session):
    cust = late_customer(session)
    fake = FakeStructuredProvider("The oldest invoice is 90 days late and $12,000 is overdue.")

    risk = assess_customer(session, cust, AS_OF, LLMRiskExplainer(fake))

    assert risk.explanation == assess(session, cust).explanation  # the template, not the guess


def test_llm_is_not_called_when_there_is_nothing_to_explain(session):
    cust = customer(session)
    fake = FakeStructuredProvider("anything")

    assess_customer(session, cust, AS_OF, LLMRiskExplainer(fake))

    assert fake.calls == 0


def test_explanation_schema_and_default_explainer():
    assert set(RiskExplanation.model_fields) == {"explanation"}
    assert isinstance(get_risk_explainer(), MockRiskExplainer)


# --- The synthetic fixtures ---


@pytest.fixture
def fixtures(session, tmp_path):
    seed_ar_reference(session, AR_DIR)
    create_invoices_from_orders(session, tmp_path / "pdfs")
    apply_payments(session, MockPaymentMatcher())  # auto-posts confident payments only
    return session


def by_name(results):
    return {r.customer_name: r for r in results}


def test_fixture_customers_land_in_the_expected_levels(fixtures):
    results = by_name(assess_risk(fixtures, AS_OF, MockRiskExplainer()))

    amberfield = results["Amberfield Clinics LLC"]  # oldest 68 days late, two invoices 30+ late
    assert amberfield.rule_codes == ["OLDEST_PAST_DUE_61_90", "INVOICES_30_PLUS"]
    assert (amberfield.score, amberfield.level) == (40 + 30, "high")

    blue_harbor = results["Blue Harbor Retail Group"]  # everything paid
    assert (blue_harbor.score, blue_harbor.level, blue_harbor.rule_codes) == (0, "low", [])
    assert len(results) == 8  # every customer is assessed, flagged or not


def test_approving_two_partials_adds_the_partial_pay_pattern(fixtures):
    for payment in fixtures.scalars(
        select(BankPayment).where(BankPayment.status == "needs_review")
    ):
        if payment.applications and payment.applications[0].match_kind == "partial":
            payment_review.approve(fixtures, payment)

    northlake = by_name(assess_risk(fixtures, AS_OF, MockRiskExplainer()))["Northlake Studio LLC"]

    assert "PARTIAL_PAY_PATTERN" in northlake.rule_codes
    assert northlake.score == 20 + 30 + 10


def test_assessing_risk_changes_nothing(fixtures):
    def state():
        invoices = {
            i.invoice_number: (i.status, i.balance) for i in fixtures.scalars(select(ARInvoice))
        }
        return invoices, fixtures.scalar(select(func.count()).select_from(AuditEvent))

    before = state()
    assess_risk(fixtures, AS_OF, MockRiskExplainer())
    assert state() == before


# --- Routes ---


@pytest.fixture
def loaded(engine, tmp_path):
    with Session(engine) as s:
        seed_ar_reference(s, AR_DIR)
        create_invoices_from_orders(s, tmp_path / "pdfs")
        apply_payments(s, MockPaymentMatcher())


def test_json_endpoint_returns_scores_levels_and_reasons(client, loaded):
    rows = client.get("/api/ar/risk", params={"as_of": AS_OF.isoformat()}).json()

    assert len(rows) == 8
    first = rows[0]
    assert {"customer_name", "score", "level", "rule_codes", "explanation", "as_of"} <= set(first)
    assert [r["score"] for r in rows] == sorted((r["score"] for r in rows), reverse=True)


def test_risk_page_shows_levels_and_does_not_break_the_queue(client, loaded):
    page = client.get("/receivables/risk", params={"as_of": AS_OF.isoformat()})

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "Amberfield Clinics LLC" in page.text and "high" in page.text
    assert "OLDEST_PAST_DUE_61_90" in page.text
    assert client.get("/queue").status_code == 200
