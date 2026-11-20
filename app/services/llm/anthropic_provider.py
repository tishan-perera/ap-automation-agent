"""Anthropic (Claude) provider using structured output."""

from anthropic import Anthropic

from app.schemas import InvoiceExtraction
from app.services.llm.base import BaseProvider
from app.services.llm.prompt import SYSTEM_PROMPT, LLMExtraction, to_extraction, user_prompt


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(
        self, api_key: str, model: str = "claude-haiku-5-5", timeout: float = 30.0
    ) -> None:
        super().__init__()
        self.model = model
        # max_retries=0: retry_extract owns the retry policy, so the SDK must not retry too.
        self._client = Anthropic(api_key=api_key, timeout=timeout, max_retries=0)

    def extract(self, text: str) -> InvoiceExtraction:
        message = self._client.messages.parse(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt(text)}],
            output_format=LLMExtraction,
        )
        self.last_token_usage = message.usage.input_tokens + message.usage.output_tokens
        return to_extraction(message.parsed_output)
