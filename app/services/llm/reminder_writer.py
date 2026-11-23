"""Wording for collection reminder drafts. Drafts are never sent by this app.

The subject, the past-due invoice table, and the total are written by code. A writer only
supplies the opening and closing sentences. The mock uses a template per tone; a real
provider's sentences are thrown away for the template if they contain any digit or "$",
so a model can never put an amount, date, or invoice number into a reminder.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

SIGN_OFF = "Thank you,\nAccounts Receivable\nExample Co (fictional)"
MAX_SENTENCE_CHARS = 600

SUBJECTS = {
    "polite": "Friendly reminder: past-due invoices for {customer}",
    "firm": "Second notice: past-due balance for {customer}",
    "final": "Final notice: past-due balance for {customer}",
}
TEMPLATES = {
    "polite": (
        "We hope all is well. Our records show the invoices below are now past due.",
        (
            "If payment is already on its way, thank you, and please disregard this note. "
            "Otherwise, we would appreciate payment at your earliest convenience."
        ),
    ),
    "firm": (
        "Our records show the invoices below remain unpaid past their due dates.",
        "Please arrange payment promptly, or reply to let us know when we can expect it.",
    ),
    "final": (
        "Despite earlier reminders, the invoices below are still unpaid.",
        "Please arrange payment right away, or contact us so we can resolve this together.",
    ),
}
REMINDER_SYSTEM_PROMPT = (
    "You write the opening and closing sentences of a professional, courteous collection "
    "reminder for an accounts receivable team. The invoice list, amounts, and dates are "
    "added separately. Do not include any amount, number, date, or invoice number, do not "
    "threaten, do not say the message was sent, and never guess facts."
)


@dataclass
class ReminderLine:
    invoice_number: str
    due_date: date
    balance: Decimal


@dataclass
class ReminderFacts:
    customer_name: str
    tone: str  # polite | firm | final
    oldest_days_past_due: int
    lines: list[ReminderLine]

    @property
    def total(self) -> Decimal:
        return sum((line.balance for line in self.lines), Decimal("0.00"))


class ReminderWording(BaseModel):
    opening: str
    closing: str


def subject_for(facts: ReminderFacts) -> str:
    return SUBJECTS[facts.tone].format(customer=facts.customer_name)


def compose_body(facts: ReminderFacts, wording: ReminderWording) -> str:
    table = "\n".join(
        f"{line.invoice_number}   due {line.due_date.isoformat()}   ${line.balance:,.2f}"
        for line in facts.lines
    )
    return "\n\n".join(
        [
            f"Hello {facts.customer_name} team,",
            wording.opening.strip(),
            table,
            f"Total past due: ${facts.total:,.2f}",
            wording.closing.strip(),
            SIGN_OFF,
        ]
    )


def template_wording(facts: ReminderFacts) -> ReminderWording:
    opening, closing = TEMPLATES[facts.tone]
    return ReminderWording(opening=opening, closing=closing)


def is_safe_sentence(text: str) -> bool:
    text = (text or "").strip()
    return (
        bool(text)
        and len(text) <= MAX_SENTENCE_CHARS
        and not any(ch.isdigit() or ch == "$" for ch in text)
    )


class MockReminderWriter:
    name = "mock"

    def wording(self, facts: ReminderFacts) -> ReminderWording:
        return template_wording(facts)


class LLMReminderWriter:
    """Asks a real provider for the two sentences only, and checks them."""

    def __init__(self, provider) -> None:
        self.provider = provider
        self.name = provider.name

    def wording(self, facts: ReminderFacts) -> ReminderWording:
        prompt = (
            f"Customer: {facts.customer_name}\nTone: {facts.tone}\n"
            f"Number of past-due invoices: {len(facts.lines)}"
        )
        reply = self.provider.structured_completion(REMINDER_SYSTEM_PROMPT, prompt, ReminderWording)
        if is_safe_sentence(reply.opening) and is_safe_sentence(reply.closing):
            return reply
        return template_wording(facts)
