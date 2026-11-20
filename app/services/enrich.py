"""Business checks that run after extraction: duplicates, PO matching, GL coding."""

from sqlalchemy.orm import Session

from app.models import Invoice
from app.schemas import ValidationFlag
from app.services.duplicates import check_duplicate
from app.services.gl_coding import apply_gl
from app.services.matching import check_match


def exception_reasons(flags: list[ValidationFlag]) -> list[str]:
    """Flag messages in order, with repeats removed."""
    return list(dict.fromkeys(f.message for f in flags))


def enrich_invoice(
    session: Session, invoice: Invoice, flags: list[ValidationFlag]
) -> list[ValidationFlag]:
    """Run the checks on a flushed invoice, store results on it, and return all flags."""
    flags = [*flags, *check_duplicate(session, invoice), *check_match(session, invoice)]
    apply_gl(session, invoice)
    invoice.validation_flags = [f.model_dump() for f in flags]
    invoice.exception_reasons = exception_reasons(flags)
    return flags


def mark_not_checked(invoice: Invoice, reason: str) -> None:
    """For unreadable or failed extractions: record that matching was not possible."""
    invoice.match_type = "none"
    invoice.match_status = "not_applicable"
    invoice.match_details = {"notes": [reason]}
