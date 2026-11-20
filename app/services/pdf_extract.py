"""Extract text from PDF bytes: pdfplumber first, pypdf as a fallback."""

import io
import logging

import pdfplumber
from pypdf import PdfReader

logger = logging.getLogger(__name__)


def _with_pdfplumber(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _with_pypdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_text(data: bytes) -> str:
    """Return the PDF's text, or an empty string if neither library can read it."""
    for name, reader in (("pdfplumber", _with_pdfplumber), ("pypdf", _with_pypdf)):
        try:
            text = reader(data)
        except Exception as exc:  # noqa: BLE001 - a broken PDF must never crash ingestion
            logger.warning("pdf_text_failed", extra={"reader": name, "error": str(exc)})
            continue
        if text.strip():
            return text
    return ""
