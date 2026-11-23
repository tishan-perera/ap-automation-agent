"""Payment matchers: choose which candidate invoices a bank payment pays.

A matcher only picks invoice numbers and a confidence. Amounts, match kind, and whether
anything is posted are decided in code (app/services/cash_application.py). Real LLMs may
only choose from the candidates the code found; anything else is thrown away.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel

from app.models_ar import BankPayment
from app.services.ar.candidates import Candidates


@dataclass
class MatchSuggestion:
    invoice_numbers: list[str]
    confidence: float
    # invoice_number | amount_and_customer | split_by_amount | conflict | none | rejected
    basis: str
    explanation: str


class PaymentMatcher(ABC):
    name = "base"

    @abstractmethod
    def match_payment(self, payment: BankPayment, candidates: Candidates) -> MatchSuggestion:
        """Pick invoices from candidates.pool. Unclear -> empty list and low confidence."""


MATCH_SYSTEM_PROMPT = (
    "You match one incoming bank payment to open customer invoices for an accounts "
    "receivable team. Choose only invoice numbers from the candidate list you are given. "
    "If the match is unclear, return an empty list and a confidence below 0.5. "
    "Never guess, never invent an invoice number or a customer, and never change amounts."
)


class LLMMatchOutput(BaseModel):
    invoice_numbers: list[str]
    confidence: float
    explanation: str


def match_prompt(payment: BankPayment, candidates: Candidates) -> str:
    lines = [
        (
            f"Payment: amount {payment.amount} {payment.currency}, date {payment.payment_date}, "
            f"memo {payment.memo!r}, payer {payment.payer_name_raw!r}."
        ),
        "Candidate open invoices (number, customer, open balance, due date):",
    ]
    for invoice in candidates.pool:
        lines.append(
            f"- {invoice.invoice_number}, {invoice.customer.name}, {invoice.balance}, "
            f"{invoice.due_date}"
        )
    return "\n".join(lines)


class LLMPaymentMatcher(PaymentMatcher):
    """Asks a real provider to choose among candidates, then validates its answer."""

    def __init__(self, provider) -> None:
        self.provider = provider
        self.name = provider.name

    def match_payment(self, payment: BankPayment, candidates: Candidates) -> MatchSuggestion:
        pool = {invoice.invoice_number for invoice in candidates.pool}
        if not pool:  # nothing to choose from: never ask a model to guess
            return MatchSuggestion([], 0.0, "none", "No invoice number and no known payer.")
        output = self.provider.structured_completion(
            MATCH_SYSTEM_PROMPT, match_prompt(payment, candidates), LLMMatchOutput
        )
        chosen = list(dict.fromkeys(output.invoice_numbers))
        if any(number not in pool for number in chosen):
            return MatchSuggestion(
                [], 0.0, "rejected", "The model named an invoice outside the candidates."
            )
        referenced = {r.invoice.invoice_number for r in candidates.referenced}
        basis = "invoice_number" if set(chosen) & referenced else "amount_and_customer"
        confidence = min(max(float(output.confidence), 0.0), 1.0)
        return MatchSuggestion(chosen, confidence, basis if chosen else "none", output.explanation)
