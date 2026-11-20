"""Deterministic checks on extracted fields. Missing values are flagged, never filled in."""

from decimal import Decimal

from app.schemas import InvoiceExtraction, ValidationFlag

LINE_MATH_TOLERANCE = Decimal("0.01")


def flag(code: str, message: str, field: str | None = None) -> ValidationFlag:
    return ValidationFlag(code=code, message=message, field=field)


def _line_math_flag(extraction: InvoiceExtraction) -> ValidationFlag | None:
    amounts = [item.amount for item in extraction.line_items]
    if extraction.total is None or not amounts or any(a is None for a in amounts):
        return None
    expected = sum(amounts, Decimal(0)) + (extraction.tax or Decimal(0))
    if abs(expected - extraction.total) <= LINE_MATH_TOLERANCE:
        return None
    return flag(
        "LINE_MATH_MISMATCH",
        f"Line items plus tax ({expected}) do not match total ({extraction.total}).",
        "total",
    )


def validate_extraction(extraction: InvoiceExtraction) -> list[ValidationFlag]:
    """Return every validation flag that applies to this extraction."""
    nothing_read = (
        extraction.vendor_name is None
        and extraction.invoice_number is None
        and extraction.total is None
    )
    if nothing_read:
        return [flag("UNREADABLE", "No vendor, invoice number, or total could be read.")]

    flags: list[ValidationFlag] = []
    if extraction.vendor_name is None:
        flags.append(flag("MISSING_VENDOR", "Vendor name is missing.", "vendor_name"))
    if extraction.invoice_number is None:
        flags.append(flag("MISSING_INVOICE_NUMBER", "Invoice number is missing.", "invoice_number"))
    if extraction.invoice_date is None:
        flags.append(flag("MISSING_DATE", "Invoice date is missing.", "invoice_date"))
    if extraction.total is None:
        flags.append(flag("MISSING_TOTAL", "Invoice total is missing or unreadable.", "total"))
    line_math = _line_math_flag(extraction)
    if line_math:
        flags.append(line_math)
    return flags
