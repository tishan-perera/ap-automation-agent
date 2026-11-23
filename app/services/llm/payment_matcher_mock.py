"""Offline, deterministic payment matcher (the default). Rules only; it never reads the
answer key and makes no network calls. Unclear payments get no suggestion."""

from decimal import Decimal
from itertools import combinations

from app.models_ar import BankPayment
from app.services.ar.candidates import Candidates
from app.services.llm.payment_matcher import MatchSuggestion, PaymentMatcher

CENT = Decimal("0.01")
CLEAN_CONFIDENCE = 0.97  # every memo reference is a clean "AR-1234"
LOOSE_CONFIDENCE = 0.92  # a typo'd or bare number, confirmed by the payer
UNKNOWN_PAYER_CONFIDENCE = 0.85  # invoice number found, payer not recognized
AMOUNT_CUSTOMER_CONFIDENCE = 0.90  # no number; one invoice of this payer has this amount
SPLIT_BY_AMOUNT_CONFIDENCE = 0.60  # no numbers; amount equals a set of this payer's invoices
CONFLICT_CONFIDENCE = 0.50  # memo invoice belongs to a different customer than the payer


class MockPaymentMatcher(PaymentMatcher):
    name = "mock"

    def match_payment(self, payment: BankPayment, candidates: Candidates) -> MatchSuggestion:
        if candidates.referenced:
            return self._from_memo(candidates)
        if candidates.customer:
            return self._from_amount(payment.amount, candidates)
        return MatchSuggestion([], 0.0, "none", "No invoice number and no known payer.")

    def _from_memo(self, candidates: Candidates) -> MatchSuggestion:
        numbers = [r.invoice.invoice_number for r in candidates.referenced]
        customer = candidates.customer
        if customer and any(r.invoice.customer_id != customer.id for r in candidates.referenced):
            return MatchSuggestion(
                numbers, CONFLICT_CONFIDENCE, "conflict",
                f"The memo names an invoice of a different customer than {customer.name}.",
            )  # fmt: skip
        if customer is None:
            return MatchSuggestion(
                numbers, UNKNOWN_PAYER_CONFIDENCE, "invoice_number",
                "Invoice number found in the memo, but the payer is not recognized.",
            )  # fmt: skip
        clean = all(r.how == "clean" for r in candidates.referenced)
        return MatchSuggestion(
            numbers, CLEAN_CONFIDENCE if clean else LOOSE_CONFIDENCE, "invoice_number",
            f"Memo references {', '.join(numbers)} from {customer.name}.",
        )  # fmt: skip

    def _from_amount(self, amount: Decimal, candidates: Candidates) -> MatchSuggestion:
        invoices = candidates.customer_invoices
        name = candidates.customer.name
        exact = [i for i in invoices if abs(i.balance - amount) <= CENT]
        if len(exact) == 1:
            return MatchSuggestion(
                [exact[0].invoice_number], AMOUNT_CUSTOMER_CONFIDENCE, "amount_and_customer",
                f"Only open invoice of {name} with this exact amount.",
            )  # fmt: skip
        splits = [
            combo
            for size in (2, 3)
            for combo in combinations(invoices, size)
            if abs(sum(i.balance for i in combo) - amount) <= CENT
        ]
        if len(splits) == 1:
            return MatchSuggestion(
                [i.invoice_number for i in splits[0]], SPLIT_BY_AMOUNT_CONFIDENCE,
                "split_by_amount",
                f"No invoice numbers; the amount equals these open invoices of {name}.",
            )  # fmt: skip
        return MatchSuggestion(
            [], 0.0, "none", f"Payer looks like {name}, but no open invoice set matches."
        )
