"""The facts a risk score is built from. Shared by the scorer and the explanation writers."""

from dataclasses import dataclass, field


@dataclass
class RiskFacts:
    customer_name: str
    oldest_days_past_due: int | None  # None when nothing is past due
    invoices_30_plus: int
    partial_payments_90d: int
    rule_codes: list[str] = field(default_factory=list)
