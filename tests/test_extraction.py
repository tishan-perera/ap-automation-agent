import logging
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AuditEvent
from app.services.ingest import process_invoice
from app.services.llm.base import BaseProvider, ProviderError
from app.services.llm.factory import get_provider
from app.services.llm.mock import MockProvider


class FlakyProvider(BaseProvider):
    """Test double: raises `error` for the first `failures` calls, then acts like the mock."""

    name = "test-double"

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        super().__init__()
        self.failures = failures
        self.error = error or TimeoutError("fake timeout")
        self.calls = 0

    def extract(self, text):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return MockProvider().extract(text)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Record backoff delays instead of actually sleeping."""
    delays = []
    monkeypatch.setattr("app.services.llm.base.time.sleep", delays.append)
    return delays


def codes(invoice):
    return [flag["code"] for flag in invoice.validation_flags]


def audit_for(session, invoice):
    return session.scalars(select(AuditEvent).where(AuditEvent.invoice_id == invoice.id)).one()


def test_default_provider_in_tests_is_mock():
    assert isinstance(get_provider(), MockProvider)


def test_happy_path_persists_fields(session, fixture_pdf):
    path, expected = fixture_pdf("happy")
    invoice = process_invoice(path, session=session)
    session.expire_all()  # re-read from the database, not from memory

    assert invoice.vendor_name == expected["vendor_name"]
    assert invoice.invoice_number == expected["invoice_number"]
    assert invoice.total == Decimal(expected["total"])
    assert len(invoice.line_items) == len(expected["line_items"])
    assert "MISSING_TOTAL" not in codes(invoice)
    assert "UNREADABLE" not in codes(invoice)
    event = audit_for(session, invoice)
    assert event.action == "ingested"
    assert event.after_json["provider"] == "mock"


def test_missing_total_stays_null_and_needs_review(session, fixture_pdf):
    path, _ = fixture_pdf("missing_total")
    invoice = process_invoice(path, session=session)

    assert invoice.total is None
    assert "MISSING_TOTAL" in codes(invoice)
    assert invoice.status == "needs_review"


def test_line_math_mismatch_is_flagged(session, fixture_pdf):
    path, _ = fixture_pdf("line_math_mismatch")
    invoice = process_invoice(path, session=session)

    assert "LINE_MATH_MISMATCH" in codes(invoice)
    assert invoice.status == "needs_review"


def test_unreadable_pdf_is_flagged(session, fixture_pdf):
    path, _ = fixture_pdf("unreadable")
    invoice = process_invoice(path, session=session)

    assert codes(invoice) == ["UNREADABLE"]
    assert invoice.total is None
    assert invoice.status == "needs_review"


def test_retries_timeouts_then_succeeds(session, fixture_pdf, no_real_sleep):
    path, expected = fixture_pdf("happy")
    provider = FlakyProvider(failures=2)

    invoice = process_invoice(path, session=session, provider=provider)

    assert provider.calls == 3
    assert no_real_sleep == [1, 2]
    assert invoice.invoice_number == expected["invoice_number"]
    assert "EXTRACTION_FAILED" not in codes(invoice)


def test_three_failures_save_invoice_as_extraction_failed(session, fixture_pdf):
    path, _ = fixture_pdf("happy")
    provider = FlakyProvider(failures=3)

    invoice = process_invoice(path, session=session, provider=provider)

    assert provider.calls == 3
    assert invoice.id is not None
    assert invoice.status == "needs_review"
    assert codes(invoice) == ["EXTRACTION_FAILED"]
    assert invoice.total is None


def test_ordinary_4xx_is_not_retried(session, fixture_pdf, no_real_sleep):
    path, _ = fixture_pdf("happy")
    provider = FlakyProvider(failures=5, error=ProviderError("bad request", status_code=400))

    invoice = process_invoice(path, session=session, provider=provider)

    assert provider.calls == 1
    assert no_real_sleep == []
    assert codes(invoice) == ["EXTRACTION_FAILED"]


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_rate_limit_and_server_errors_are_retried(session, fixture_pdf, status_code):
    path, _ = fixture_pdf("happy")
    provider = FlakyProvider(failures=1, error=ProviderError("busy", status_code=status_code))

    invoice = process_invoice(path, session=session, provider=provider)

    assert provider.calls == 2
    assert "EXTRACTION_FAILED" not in codes(invoice)


def test_structured_log_has_required_fields(session, fixture_pdf, caplog):
    path, _ = fixture_pdf("happy")
    with caplog.at_level(logging.INFO, logger="app.services.ingest"):
        invoice = process_invoice(path, session=session)

    record = next(r for r in caplog.records if r.getMessage() == "invoice_ingested")
    assert record.invoice_id == invoice.id
    assert record.file_hash == invoice.file_hash
    assert record.provider == "mock"
    assert record.token_usage == 0
    assert record.outcome == "extracted"
    assert isinstance(record.elapsed_ms, int)
