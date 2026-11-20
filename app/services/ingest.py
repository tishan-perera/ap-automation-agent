"""Ingest one invoice PDF: hash, skip if seen, extract, validate, persist, audit."""

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import AuditEvent, Invoice, InvoiceLineItem, ProcessedFile
from app.schemas import InvoiceExtraction, ValidationFlag
from app.services.llm.base import BaseProvider, retry_extract
from app.services.llm.factory import get_provider
from app.services.pdf_extract import extract_text
from app.services.validation import flag, validate_extraction

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    invoice: Invoice
    created: bool  # False when the same file bytes were already processed


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_invoice(
    filename: str,
    file_hash: str,
    text: str,
    extraction: InvoiceExtraction,
    flags: list[ValidationFlag],
) -> Invoice:
    invoice = Invoice(
        filename=filename,
        file_hash=file_hash,
        # v1: every invoice waits for a human, even a clean one.
        status="needs_review",
        raw_text=text,
        extracted_json=extraction.model_dump(mode="json"),
        validation_flags=[f.model_dump() for f in flags],
        exception_reasons=[f.message for f in flags],
        **extraction.model_dump(
            include={
                "vendor_name",
                "invoice_number",
                "invoice_date",
                "due_date",
                "po_number",
                "currency",
                "subtotal",
                "tax",
                "total",
            }
        ),
    )
    invoice.line_items = [InvoiceLineItem(**item.model_dump()) for item in extraction.line_items]
    return invoice


def _extract(provider: BaseProvider, text: str) -> tuple[InvoiceExtraction, list, str]:
    """Return (extraction, flags, outcome). Failures become nulls plus a flag, never guesses."""
    if not text.strip():
        extraction = InvoiceExtraction(extraction_notes="PDF contained no extractable text.")
        return extraction, validate_extraction(extraction), "unreadable"
    try:
        extraction = retry_extract(provider, text)
    except Exception as exc:  # noqa: BLE001 - any provider failure becomes EXTRACTION_FAILED
        logger.warning(
            "llm_extraction_failed", extra={"provider": provider.name, "error": repr(exc)}
        )
        failed = [flag("EXTRACTION_FAILED", f"Extraction failed: {type(exc).__name__}.")]
        return InvoiceExtraction(), failed, "extraction_failed"
    flags = validate_extraction(extraction)
    outcome = "unreadable" if any(f.code == "UNREADABLE" for f in flags) else "extracted"
    return extraction, flags, outcome


def _log(invoice_id, file_hash, provider_name, started, token_usage, outcome) -> None:
    logger.info(
        "invoice_ingested",
        extra={
            "invoice_id": invoice_id,
            "file_hash": file_hash,
            "provider": provider_name,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "token_usage": token_usage,
            "outcome": outcome,
        },
    )


def ingest_bytes(
    session: Session, filename: str, data: bytes, provider: BaseProvider | None = None
) -> IngestResult:
    started = time.perf_counter()
    file_hash = sha256_hex(data)
    seen = session.get(ProcessedFile, file_hash)
    if seen:
        _log(seen.invoice_id, file_hash, None, started, 0, "already_processed")
        return IngestResult(invoice=session.get(Invoice, seen.invoice_id), created=False)

    provider = provider or get_provider(session)
    text = extract_text(data)
    extraction, flags, outcome = _extract(provider, text)

    invoice = _build_invoice(filename, file_hash, text, extraction, flags)
    session.add(invoice)
    session.flush()  # assigns invoice.id
    session.add(ProcessedFile(file_hash=file_hash, invoice_id=invoice.id))
    session.add(
        AuditEvent(
            invoice_id=invoice.id,
            actor="system",
            action="ingested",
            after_json={
                "provider": provider.name,
                "outcome": outcome,
                "flags": [f.code for f in flags],
            },
        )
    )
    session.commit()
    _log(invoice.id, file_hash, provider.name, started, provider.last_token_usage, outcome)
    return IngestResult(invoice=invoice, created=True)


def process_invoice(
    path: Path | str, session: Session, provider: BaseProvider | None = None
) -> Invoice:
    path = Path(path)
    return ingest_bytes(session, path.name, path.read_bytes(), provider).invoice
