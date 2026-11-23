"""Write the plain-English reason for a risk flag. The score is already fixed by the rules.

The mock fills a template. A real provider may only rephrase the same facts; if its text
contains any number that is not one of the facts, it is thrown away for the template.
"""

import re

from pydantic import BaseModel

from app.services.ar.risk_facts import RiskFacts

RISK_SYSTEM_PROMPT = (
    "You write one or two plain-English sentences explaining a customer's late-payment risk "
    "for an accounts receivable team. Use only the facts you are given. Do not change the "
    "score or the level, do not add numbers, amounts, or dates that are not in the facts, "
    "and never guess."
)


class RiskExplanation(BaseModel):
    explanation: str


def template_explanation(facts: RiskFacts) -> str:
    if not facts.rule_codes:
        return "No invoices are past due and there is no partial-payment pattern."
    parts = []
    if facts.oldest_days_past_due:
        parts.append(f"the oldest open invoice is {facts.oldest_days_past_due} days past due")
    if facts.invoices_30_plus:
        n = facts.invoices_30_plus
        parts.append(f"{n} invoice{' is' if n == 1 else 's are'} 30 or more days past due")
    if "PARTIAL_PAY_PATTERN" in facts.rule_codes:
        parts.append(f"{facts.partial_payments_90d} recent payments were partial")
    sentence = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]
    return sentence[0].upper() + sentence[1:] + "."


def allowed_numbers(facts: RiskFacts) -> set[str]:
    values = {facts.oldest_days_past_due, facts.invoices_30_plus, facts.partial_payments_90d}
    return {str(v) for v in values if v is not None} | {"30", "90"}  # rule thresholds


def only_known_numbers(text: str, facts: RiskFacts) -> bool:
    found = {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
    return found <= allowed_numbers(facts)


class MockRiskExplainer:
    name = "mock"

    def explain(self, facts: RiskFacts) -> str:
        return template_explanation(facts)


class LLMRiskExplainer:
    """Asks a real provider to phrase the facts, and checks it added nothing."""

    def __init__(self, provider) -> None:
        self.provider = provider
        self.name = provider.name

    def explain(self, facts: RiskFacts) -> str:
        fallback = template_explanation(facts)
        if not facts.rule_codes:
            return fallback  # nothing to explain: no model call
        prompt = (
            f"Customer: {facts.customer_name}\nFacts: {fallback}\nRule codes: {facts.rule_codes}"
        )
        text = self.provider.structured_completion(RISK_SYSTEM_PROMPT, prompt, RiskExplanation)
        wording = (text.explanation or "").strip()
        return wording if wording and only_known_numbers(wording, facts) else fallback
