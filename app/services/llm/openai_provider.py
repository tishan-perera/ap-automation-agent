"""OpenAI provider using the Responses API with structured output."""

from openai import OpenAI

from app.schemas import InvoiceExtraction
from app.services.llm.base import BaseProvider
from app.services.llm.prompt import SYSTEM_PROMPT, LLMExtraction, to_extraction, user_prompt


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-5-mini", timeout: float = 30.0) -> None:
        super().__init__()
        self.model = model
        # max_retries=0: retry_extract owns the retry policy, so the SDK must not retry too.
        self._client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)

    def structured_completion(self, system: str, user: str, schema):
        response = self._client.responses.parse(
            model=self.model, instructions=system, input=user, text_format=schema
        )
        self.last_token_usage = response.usage.total_tokens if response.usage else 0
        return response.output_parsed

    def extract(self, text: str) -> InvoiceExtraction:
        return to_extraction(
            self.structured_completion(SYSTEM_PROMPT, user_prompt(text), LLMExtraction)
        )
