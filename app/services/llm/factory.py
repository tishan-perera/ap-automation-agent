"""Pick the LLM provider from app_settings (if set in the UI) or LLM_PROVIDER, default mock."""

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppSetting
from app.services.llm.anthropic_provider import AnthropicProvider
from app.services.llm.base import BaseProvider
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.mock import MockProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.payment_matcher import LLMPaymentMatcher, PaymentMatcher
from app.services.llm.payment_matcher_mock import MockPaymentMatcher
from app.services.llm.risk_explainer import LLMRiskExplainer, MockRiskExplainer

logger = logging.getLogger(__name__)

# provider name -> (class, settings field holding its key, settings field holding its model)
REAL_PROVIDERS = {
    "openai": (OpenAIProvider, "openai_api_key", "openai_model"),
    "anthropic": (AnthropicProvider, "anthropic_api_key", "anthropic_model"),
    "gemini": (GeminiProvider, "gemini_api_key", "gemini_model"),
}


def provider_name(session: Session | None = None) -> str:
    if session is not None:
        row = session.get(AppSetting, "llm_provider")
        if row and row.value:
            return row.value.strip().lower()
    return get_settings().llm_provider.strip().lower()


def get_provider(session: Session | None = None) -> BaseProvider:
    name = provider_name(session)
    if name not in REAL_PROVIDERS:
        if name != "mock":
            logger.warning("unknown_llm_provider", extra={"requested": name, "using": "mock"})
        return MockProvider()

    settings = get_settings()
    provider_cls, key_field, model_field = REAL_PROVIDERS[name]
    api_key = getattr(settings, key_field)
    if not api_key:
        # Real calls only happen when a key is present. The key itself is never logged.
        logger.warning("llm_api_key_missing", extra={"requested": name, "using": "mock"})
        return MockProvider()
    return provider_cls(
        api_key=api_key,
        model=getattr(settings, model_field),
        timeout=settings.llm_timeout_seconds,
    )


def get_payment_matcher(session: Session | None = None) -> PaymentMatcher:
    """Same provider choice as get_provider(): the mock unless a real provider has a key."""
    provider = get_provider(session)
    if isinstance(provider, MockProvider):
        return MockPaymentMatcher()
    return LLMPaymentMatcher(provider)


def get_risk_explainer(session: Session | None = None):
    """Same provider choice as get_provider(): the mock template unless a real key is set."""
    provider = get_provider(session)
    if isinstance(provider, MockProvider):
        return MockRiskExplainer()
    return LLMRiskExplainer(provider)
