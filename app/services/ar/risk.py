"""Late-payment risk per customer, scored by fixed rules (docs/specs/2026-11-21-ar-design.md §10).

The score and level come only from these rules. A writer (mock or LLM) then explains the
facts in plain English; it never sees or changes the score. Read-only: nothing is saved.

Rules and points:
  OLDEST_PAST_DUE_1_30 / _31_60 / _61_90 / _90_PLUS  oldest open invoice   0 / 20 / 40 / 60
  INVOICES_30_PLUS     each open invoice 30 or more days past due      10 each, capped at 20
  PARTIAL_PAY_PATTERN  2+ posted partial payments in the last 90 days  10
Levels: low under 25, medium 25-49, high 50 or more.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models_ar import ARInvoice, BankPayment, Customer, PaymentApplication
from app.services.ar.aging import days_past_due
from app.services.ar.risk_facts import RiskFacts
from app.services.llm.factory import get_risk_explainer

POINTS = {
    "OLDEST_PAST_DUE_1_30": 0,
    "OLDEST_PAST_DUE_31_60": 20,
    "OLDEST_PAST_DUE_61_90": 40,
    "OLDEST_PAST_DUE_90_PLUS": 60,
    "INVOICES_30_PLUS": 10,
    "PARTIAL_PAY_PATTERN": 10,
}
INVOICES_30_PLUS_CAP = 20
PARTIAL_WINDOW_DAYS = 90
PARTIAL_PAYMENTS_FOR_PATTERN = 2


@dataclass
class RiskAssessment:
    customer_account: str
    customer_name: str
    as_of: date
    score: int
    level: str
    rule_codes: list[str]
    explanation: str
    facts: RiskFacts


def level_for(score: int) -> str:
    if score >= 50:
        return "high"
    return "medium" if score >= 25 else "low"


def _oldest_code(days: int) -> str:
    if days <= 30:
        return "OLDEST_PAST_DUE_1_30"
    if days <= 60:
        return "OLDEST_PAST_DUE_31_60"
    return "OLDEST_PAST_DUE_61_90" if days <= 90 else "OLDEST_PAST_DUE_90_PLUS"


def _past_due_days(session: Session, customer: Customer, as_of: date) -> list[int]:
    invoices = session.scalars(
        select(ARInvoice).where(
            ARInvoice.customer_id == customer.id,
            ARInvoice.status.in_(("open", "partial")),
            ARInvoice.balance > Decimal(0),
            ARInvoice.due_date.is_not(None),
        )
    )
    return [d for d in (days_past_due(i.due_date, as_of) for i in invoices) if d > 0]


def _recent_partial_payments(session: Session, customer: Customer, as_of: date) -> int:
    start = as_of - timedelta(days=PARTIAL_WINDOW_DAYS)
    query = (
        select(func.count(func.distinct(BankPayment.id)))
        .join(PaymentApplication, PaymentApplication.payment_id == BankPayment.id)
        .join(ARInvoice, PaymentApplication.ar_invoice_id == ARInvoice.id)
        .where(
            ARInvoice.customer_id == customer.id,
            PaymentApplication.match_kind == "partial",
            PaymentApplication.posted_at.is_not(None),  # approved/posted only
            BankPayment.payment_date > start,
            BankPayment.payment_date <= as_of,
        )
    )
    return session.scalar(query) or 0


def score_facts(facts: RiskFacts) -> int:
    score = 0
    if facts.oldest_days_past_due:
        score += POINTS[_oldest_code(facts.oldest_days_past_due)]
    score += min(facts.invoices_30_plus * POINTS["INVOICES_30_PLUS"], INVOICES_30_PLUS_CAP)
    if facts.partial_payments_90d >= PARTIAL_PAYMENTS_FOR_PATTERN:
        score += POINTS["PARTIAL_PAY_PATTERN"]
    return score


def gather_facts(session: Session, customer: Customer, as_of: date) -> RiskFacts:
    days = _past_due_days(session, customer, as_of)
    partials = _recent_partial_payments(session, customer, as_of)
    facts = RiskFacts(
        customer_name=customer.name,
        oldest_days_past_due=max(days) if days else None,
        invoices_30_plus=sum(d >= 30 for d in days),
        partial_payments_90d=partials,
    )
    if facts.oldest_days_past_due:
        facts.rule_codes.append(_oldest_code(facts.oldest_days_past_due))
    if facts.invoices_30_plus:
        facts.rule_codes.append("INVOICES_30_PLUS")
    if partials >= PARTIAL_PAYMENTS_FOR_PATTERN:
        facts.rule_codes.append("PARTIAL_PAY_PATTERN")
    return facts


def assess_customer(session: Session, customer: Customer, as_of: date, explainer) -> RiskAssessment:
    facts = gather_facts(session, customer, as_of)
    score = score_facts(facts)  # fixed before any wording is written
    return RiskAssessment(
        customer_account=customer.account_number,
        customer_name=customer.name,
        as_of=as_of,
        score=score,
        level=level_for(score),
        rule_codes=list(facts.rule_codes),
        explanation=explainer.explain(facts),
        facts=facts,
    )


def assess_risk(session: Session, as_of: date, explainer=None) -> list[RiskAssessment]:
    """Every customer, highest score first."""
    explainer = explainer or get_risk_explainer(session)
    customers = session.scalars(select(Customer).order_by(Customer.name))
    results = [assess_customer(session, c, as_of, explainer) for c in customers]
    return sorted(results, key=lambda r: (-r.score, r.customer_name))
