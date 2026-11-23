"""Provider interface plus the shared retry policy."""

import logging
import time
from abc import ABC, abstractmethod

from app.schemas import InvoiceExtraction

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = (1, 2, 4)


class ProviderError(Exception):
    """A provider failure with an optional HTTP status code."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BaseProvider(ABC):
    name: str = "base"

    def __init__(self) -> None:
        # Tokens used by the most recent extract() call (always 0 for the mock).
        self.last_token_usage = 0

    @abstractmethod
    def extract(self, text: str) -> InvoiceExtraction:
        """Turn invoice text into structured fields. Unknown values must be None."""

    def structured_completion(self, system: str, user: str, schema):
        """Ask the model for a reply shaped like `schema` (a Pydantic model class).

        Real providers implement this; the offline mock does not call a model at all.
        """
        raise NotImplementedError(f"{self.name} does not call a language model.")


def is_retryable(exc: Exception) -> bool:
    """Retry timeouts, 429, and 5xx. Ordinary 4xx errors are not retried."""
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return True
    # OpenAI/Anthropic errors carry `status_code`; Google GenAI errors carry `code`.
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


def retry_extract(provider: BaseProvider, text: str, attempts: int = 3) -> InvoiceExtraction:
    """Call provider.extract, waiting 1s, 2s, 4s between retryable failures."""
    for attempt in range(1, attempts + 1):
        try:
            return provider.extract(text)
        except Exception as exc:
            if attempt == attempts or not is_retryable(exc):
                raise
            delay = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            logger.warning(
                "llm_retry",
                extra={
                    "provider": provider.name,
                    "attempt": attempt,
                    "delay_s": delay,
                    "error": type(exc).__name__,
                },
            )
            time.sleep(delay)
    raise AssertionError("unreachable")
