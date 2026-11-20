from decimal import Decimal

import pytest

from app.config import get_settings
from app.schemas import InvoiceExtraction
from app.services.llm.anthropic_provider import AnthropicProvider
from app.services.llm.base import BaseProvider
from app.services.llm.factory import get_provider
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.mock import MockProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.pdf_extract import extract_text


def text_of(fixture_pdf, case_type):
    path, record = fixture_pdf(case_type)
    return extract_text(path.read_bytes()), record


def test_get_provider_defaults_to_mock():
    assert get_settings().llm_provider == "mock"
    assert isinstance(get_provider(), MockProvider)


def test_get_provider_falls_back_to_mock_without_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    get_settings.cache_clear()
    assert isinstance(get_provider(), MockProvider)


def test_mock_extract_returns_invoice_extraction(fixture_pdf):
    text, _ = text_of(fixture_pdf, "happy")
    assert isinstance(MockProvider().extract(text), InvoiceExtraction)


def test_mock_fills_happy_path_fields(fixture_pdf):
    text, expected = text_of(fixture_pdf, "happy")
    extraction = MockProvider().extract(text)

    assert extraction.vendor_name == expected["vendor_name"]
    assert extraction.invoice_number == expected["invoice_number"]
    assert extraction.po_number == expected["po_number"]
    assert extraction.total == Decimal(expected["total"])
    assert [item.amount for item in extraction.line_items] == [
        Decimal(item["amount"]) for item in expected["line_items"]
    ]


def test_mock_missing_total_is_none_not_zero(fixture_pdf):
    text, _ = text_of(fixture_pdf, "missing_total")
    extraction = MockProvider().extract(text)

    assert extraction.total is None
    assert extraction.subtotal is not None


def test_mock_unreadable_text_gives_null_fields(fixture_pdf):
    text, _ = text_of(fixture_pdf, "unreadable")
    extraction = MockProvider().extract(text)

    assert extraction.vendor_name is None
    assert extraction.invoice_number is None
    assert extraction.total is None


@pytest.mark.parametrize("provider_cls", [OpenAIProvider, AnthropicProvider, GeminiProvider])
def test_real_providers_share_the_interface(provider_cls):
    # Constructing with a fake key makes no network call; extract() is never invoked here.
    provider = provider_cls(api_key="fake-key-for-tests")

    assert isinstance(provider, BaseProvider)
    assert provider_cls.extract is not BaseProvider.extract
    assert provider.name in {"openai", "anthropic", "gemini"}
