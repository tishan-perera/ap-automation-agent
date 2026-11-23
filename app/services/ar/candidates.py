"""Find candidate invoices for a bank payment with plain rules. No model, no answer key.

- Invoice numbers in the memo: "AR-1006" (clean), "AR1006" / "ar 1006" (loose), and bare
  "1006" (digits) only when it is an open invoice of the customer the payer was identified as.
- Customer from the payer name: compared as word sets, ignoring words like LLC, Group, and.
  A customer counts only if exactly one matches.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_ar import ARInvoice, BankPayment, Customer

STOPWORDS = {"and", "llc", "inc", "co", "company", "group", "grp", "the", "corp", "ltd"}
INVOICE_RE = re.compile(r"\bAR[-\s]?(\d{4})\b", re.IGNORECASE)
DIGITS_RE = re.compile(r"(?<![\d-])(\d{4})(?!\d)")


@dataclass
class MemoReference:
    invoice: ARInvoice
    how: str  # clean | loose | digits


@dataclass
class Candidates:
    customer: Customer | None
    referenced: list[MemoReference] = field(default_factory=list)
    customer_invoices: list[ARInvoice] = field(default_factory=list)

    @property
    def pool(self) -> list[ARInvoice]:
        """Every invoice a matcher may choose from (memo references + payer's open invoices)."""
        seen, pool = set(), []
        for invoice in [r.invoice for r in self.referenced] + self.customer_invoices:
            if invoice.id not in seen:
                seen.add(invoice.id)
                pool.append(invoice)
        return pool


def name_tokens(name: str | None) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    return frozenset(w for w in words if w not in STOPWORDS)


def identify_customer(session: Session, payer: str | None) -> Customer | None:
    tokens = name_tokens(payer)
    if len(tokens) < 2:
        return None
    matches = [
        customer
        for customer in session.scalars(select(Customer))
        if tokens <= name_tokens(customer.name) or name_tokens(customer.name) <= tokens
    ]
    return matches[0] if len(matches) == 1 else None


def open_invoices(session: Session) -> list[ARInvoice]:
    query = (
        select(ARInvoice)
        .where(ARInvoice.status.in_(("open", "partial")), ARInvoice.balance > Decimal(0))
        .order_by(ARInvoice.due_date, ARInvoice.invoice_number)
    )
    return list(session.scalars(query))


def find_candidates(session: Session, payment: BankPayment) -> Candidates:
    customer = identify_customer(session, payment.payer_name_raw)
    opens = open_invoices(session)
    by_number = {invoice.invoice_number: invoice for invoice in opens}
    memo = payment.memo or ""

    referenced: dict[str, MemoReference] = {}
    for match in INVOICE_RE.finditer(memo):
        number = f"AR-{match.group(1)}"
        if number in by_number and number not in referenced:
            how = "clean" if match.group(0) == number else "loose"
            referenced[number] = MemoReference(by_number[number], how)
    for match in DIGITS_RE.finditer(memo):
        number = f"AR-{match.group(1)}"
        invoice = by_number.get(number)
        if invoice and number not in referenced and customer and invoice.customer_id == customer.id:
            referenced[number] = MemoReference(invoice, "digits")

    customer_invoices = [i for i in opens if customer and i.customer_id == customer.id]
    return Candidates(customer, list(referenced.values()), customer_invoices)
