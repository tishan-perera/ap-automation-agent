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

    def structured_completion(self, system: str, user: str, schema):
        message = self._client.messages.parse(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        self.last_token_usage = message.usage.input_tokens + message.usage.output_tokens
        return message.parsed_output

    def extract(self, text: str) -> InvoiceExtraction:
        return to_extraction(
            self.structured_completion(SYSTEM_PROMPT, user_prompt(text), LLMExtraction)
        )
