"""Sequential AR invoice numbers: AR-1001, AR-1002, ... Numbers are never reused."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_ar import ARInvoice

PREFIX = "AR-"
FIRST_NUMBER = 1001


def next_invoice_number(session: Session) -> str:
    """The highest existing number plus one (void invoices keep their numbers).

    Call inside the transaction that inserts the invoice; the unique constraint on
    invoice_number is the backstop if two writers ever race.
    """
    numbers = [
        int(n[len(PREFIX) :])
        for n in session.scalars(select(ARInvoice.invoice_number))
        if n.startswith(PREFIX) and n[len(PREFIX) :].isdigit()
    ]
    return f"{PREFIX}{max(numbers, default=FIRST_NUMBER - 1) + 1}"
