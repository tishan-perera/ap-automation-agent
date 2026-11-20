"""Google Gemini provider using a JSON response schema."""

from google import genai
from google.genai import types

from app.schemas import InvoiceExtraction
from app.services.llm.base import BaseProvider
from app.services.llm.prompt import SYSTEM_PROMPT, LLMExtraction, to_extraction, user_prompt


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(
        self, api_key: str, model: str = "gemini-2.5-flash", timeout: float = 30.0
    ) -> None:
        super().__init__()
        self.model = model
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout * 1000))
        )

    def extract(self, text: str) -> InvoiceExtraction:
        response = self._client.models.generate_content(
            model=self.model,
            contents=user_prompt(text),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=LLMExtraction,
            ),
        )
        usage = response.usage_metadata
        self.last_token_usage = (usage.total_token_count or 0) if usage else 0
        return to_extraction(LLMExtraction.model_validate_json(response.text))
