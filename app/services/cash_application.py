"""AI cash application: match bank payments to open AR invoices.

1. Code finds candidates (memo invoice numbers, payer -> customer).
2. A matcher (mock by default, or a real LLM) picks invoices from those candidates only.
3. Code decides amounts, match kind, and status. A payment auto-posts only when the
   confidence is at least 0.90 AND the application is complete and balanced. Partial
   payments, unclear splits, overpayments, and unidentified payments go to a person.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_ar import ARInvoice, BankPayment, PaymentApplication
from app.services.ar.candidates import Candidates, find_candidates
from app.services.ar.posting import applications_summary, audit_payment, post_payment
from app.services.llm.factory import get_payment_matcher
from app.services.llm.payment_matcher import MatchSuggestion, PaymentMatcher

AUTO_POST_CONFIDENCE = 0.90
CENT = Decimal("0.01")


@dataclass
class Proposal:
    status: str  # posted | needs_review | unidentified
    confidence: float
    kind: str
    applications: list[tuple[ARInvoice, Decimal]] = field(default_factory=list)
    complete: bool = False


def _allocate(amount: Decimal, invoices: list[ARInvoice], basis: str) -> tuple[str, list, bool]:
    """Return (match_kind, [(invoice, amount)], complete_and_balanced). Never over-applies."""
    open_total = sum((i.balance for i in invoices), Decimal(0))
    if amount > open_total + CENT:
        return "overpayment", [(i, i.balance) for i in invoices], False
    if amount < open_total - CENT:
        if len(invoices) == 1:
            return "partial", [(invoices[0], amount)], False
        left, allocations = amount, []
        for invoice in invoices:  # oldest due date first
            if left <= 0:
                break
            portion = min(invoice.balance, left)
            allocations.append((invoice, portion))
            left -= portion
        return "split", allocations, False
    # Balanced (within one cent): the last invoice absorbs the rounding, so the sum of
    # applications never exceeds the payment.
    allocations = [(i, i.balance) for i in invoices[:-1]]
    allocations.append((invoices[-1], amount - sum((a for _, a in allocations), Decimal(0))))
    if len(invoices) > 1:
        kind = "split"
    elif basis == "amount_and_customer":
        kind = "amount_and_customer"
    else:
        kind = "invoice_number"
    return kind, allocations, True


def build_proposal(payment: BankPayment, candidates: Candidates, s: MatchSuggestion) -> Proposal:
    pool = {invoice.invoice_number: invoice for invoice in candidates.pool}
    invoices = sorted(
        (pool[n] for n in s.invoice_numbers if n in pool),
        key=lambda i: (i.due_date, i.invoice_number),
    )
    if not invoices:
        status = "needs_review" if candidates.customer else "unidentified"
        return Proposal(status, s.confidence, "unidentified")
    kind, allocations, complete = _allocate(payment.amount, invoices, s.basis)
    status = "posted" if complete and s.confidence >= AUTO_POST_CONFIDENCE else "needs_review"
    return Proposal(status, s.confidence, kind, allocations, complete)


def review_reason(
    proposal: Proposal, suggestion: MatchSuggestion, unapplied: Decimal
) -> str | None:
    """Plain-English reason a person must look at this payment (None if it auto-posted)."""
    if proposal.status == "posted":
        return None
    if proposal.status == "unidentified" or not proposal.applications:
        return suggestion.explanation or "No invoice number and no known payer."
    if suggestion.basis == "conflict":
        return "The memo names an invoice that belongs to a different customer than the payer."
    if proposal.kind == "overpayment":
        return f"The payment is more than the open balance; ${unapplied} has nowhere to go."
    if proposal.kind == "partial":
        return "The payment covers only part of the invoice."
    if proposal.kind == "split" and not proposal.complete:
        return "The payment does not add up exactly to the invoices it names."
    return (
        f"Match confidence {proposal.confidence:.0%} is below the "
        f"{AUTO_POST_CONFIDENCE:.0%} auto-post threshold."
    )


def apply_payment(
    session: Session, payment: BankPayment, matcher: PaymentMatcher | None = None, actor="system"
) -> BankPayment:
    matcher = matcher or get_payment_matcher(session)
    candidates = find_candidates(session, payment)
    suggestion = matcher.match_payment(payment, candidates)
    proposal = build_proposal(payment, candidates, suggestion)

    applied = sum((amount for _, amount in proposal.applications), Decimal(0))
    payment.applications = [
        PaymentApplication(
            invoice=invoice,
            amount_applied=amount,
            confidence=proposal.confidence,
            match_kind=proposal.kind,
        )
        for invoice, amount in proposal.applications
    ]
    payment.status = proposal.status
    payment.confidence = proposal.confidence
    payment.match_details = {
        "matcher": matcher.name,
        "customer_account": candidates.customer.account_number if candidates.customer else None,
        "basis": suggestion.basis,
        "explanation": suggestion.explanation,
        "match_kind": proposal.kind,
        "candidates": [i.invoice_number for i in candidates.pool],
        "complete_and_balanced": proposal.complete,
        "unapplied_amount": str(payment.amount - applied),
        "review_reason": review_reason(proposal, suggestion, payment.amount - applied),
    }
    session.flush()

    if proposal.status == "posted":
        post_payment(session, payment, approved_by=None)
        action = "payment_auto_posted"
    else:
        action = f"payment_{proposal.status}"  # payment_needs_review | payment_unidentified
    audit_payment(
        session, payment, action, actor,
        after={"status": payment.status, "applications": applications_summary(payment)},
        note=suggestion.explanation,
    )  # fmt: skip
    return payment


def apply_payments(session: Session, matcher: PaymentMatcher | None = None) -> list[BankPayment]:
    """Match every unmatched payment, oldest first, so balances evolve in order."""
    matcher = matcher or get_payment_matcher(session)
    query = (
        select(BankPayment)
        .where(BankPayment.status == "unmatched")
        .order_by(BankPayment.payment_date, BankPayment.payment_ref)
    )
    results = [apply_payment(session, payment, matcher) for payment in session.scalars(query)]
    session.commit()
    return results
