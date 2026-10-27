"""Provider adapters for configurable text-generation models (I13)."""

import logging
from collections.abc import Callable

import httpx
from fastapi import status

from app.config import GenerationModelSettings, Settings, get_settings
from app.models.generation import ConfiguredModel, GenerationResponse

logger = logging.getLogger(__name__)


class GenerationProviderError(Exception):
    """Safe public failure; it never contains credentials or raw provider bodies."""
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


class GenerationService:
    def __init__(self, settings: Settings, client_factory: Callable[..., httpx.Client] = httpx.Client) -> None:
        self.settings, self.client_factory = settings, client_factory
        self.models = {model.model_id: model for model in settings.generation_models}

    def configured_models(self) -> list[ConfiguredModel]:
        return [ConfiguredModel(model_id=model.model_id, provider=model.provider,
            upstream_model=model.upstream_model, configured=bool(model.api_key),
            is_default=model.model_id == self.settings.default_generation_model_id)
            for model in self.models.values()]

    def generate(self, prompt: str, model_id: str | None = None, system_instruction: str | None = None) -> GenerationResponse:
        model = self._select_model(model_id)
        if not model.api_key:
            raise GenerationProviderError(503, "The selected model is not configured with an API key.")
        for attempt in (1, 2):
            try:
                if model.provider == "openrouter":
                    return self._openrouter(model, prompt, system_instruction)
                return self._google_ai_studio(model, prompt, system_instruction)
            except httpx.TimeoutException as error:
                logger.warning("Generation request timed out for %s.", model.model_id)
                raise GenerationProviderError(504, "The selected AI provider timed out. Please try again later.") from error
            except GenerationProviderError as error:
                # Google often returns 503 under load; one retry covers a transient outage.
                if error.status_code != 503 or attempt == 2:
                    raise
                logger.warning("Generation provider unavailable for %s; retrying.", model.model_id)
            except httpx.RequestError as error:
                logger.warning("Generation network failure for %s: %s", model.model_id, type(error).__name__)
                raise GenerationProviderError(503, "The selected AI provider is unavailable. Please try again later.") from error
        raise GenerationProviderError(503, "The selected AI provider is unavailable. Please try again later.")

    def _select_model(self, requested_id: str | None) -> GenerationModelSettings:
        model_id = requested_id or self.settings.default_generation_model_id
        if not model_id:
            raise GenerationProviderError(503, "No default generation model is configured.")
        if model_id not in self.models:
            raise GenerationProviderError(404, "The requested generation model is not configured.")
        return self.models[model_id]

    def _openrouter(self, model: GenerationModelSettings, prompt: str, system: str | None) -> GenerationResponse:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        data = self._post("https://openrouter.ai/api/v1/chat/completions", model,
            {"model": model.upstream_model, "messages": messages, "max_tokens": model.max_output_tokens},
            {"Authorization": f"Bearer {model.api_key}"})
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise GenerationProviderError(502, "The selected AI provider returned an invalid response.") from error
        return self._response(model, self._text(text), data.get("id"))

    def _google_ai_studio(self, model: GenerationModelSettings, prompt: str, system: str | None) -> GenerationResponse:
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": model.max_output_tokens},
        }

        if model.upstream_model.startswith("gemini-3."):
            # Gemini 3.8 defaults to medium thinking and can sit past a 90s client
            # deadline before the first response header. Chat uses the supported
            # low level and skips thought summaries so the answer can return.
            payload["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": "low",
                "includeThoughts": False,
            }

        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        data = self._post(f"https://generativelanguage.googleapis.com/v1beta/models/{model.upstream_model}:generateContent",
            model, payload, {"x-goog-api-key": model.api_key or ""})
        try:
            text = "".join(part["text"] for part in data["candidates"][0]["content"]["parts"] if "text" in part)
        except (KeyError, IndexError, TypeError) as error:
            raise GenerationProviderError(502, "The selected AI provider returned an invalid response.") from error
        return self._response(model, self._text(text), data.get("responseId"))

    def _post(self, url: str, model: GenerationModelSettings, payload: dict, headers: dict[str, str]) -> dict:
        with self.client_factory(timeout=model.timeout_seconds) as client:
            response = client.post(url, json=payload, headers={"Content-Type": "application/json", **headers})
        # Keep useful provider categories distinct. Do not return the raw response:
        # it can contain provider internals and must never include secrets in logs/API output.
        if response.status_code == 400:
            raise GenerationProviderError(400, "The selected AI provider rejected this request as invalid or unsupported.")
        if response.status_code in {401, 403}:
            raise GenerationProviderError(502, "The selected AI provider rejected its configured credentials or access.")
        if response.status_code == 402:
            raise GenerationProviderError(502, "The selected AI provider account has insufficient credits.")
        if response.status_code == 404:
            raise GenerationProviderError(502, "The selected AI model is unavailable from its provider.")
        if response.status_code == 429:
            raise GenerationProviderError(429, "The selected AI provider rate limit was reached. Please try again later.")
        if response.status_code in {500, 502, 503, 504}:
            logger.warning("Generation provider returned status %s for %s.", response.status_code, model.model_id)
            raise GenerationProviderError(503, "The selected AI provider is unavailable. Please try again later.")
        if response.status_code >= 400:
            logger.warning("Generation provider returned status %s for %s.", response.status_code, model.model_id)
            raise GenerationProviderError(502, "The selected AI provider rejected the request.")
        try:
            return response.json()
        except ValueError as error:
            raise GenerationProviderError(502, "The selected AI provider returned an invalid response.") from error

    @staticmethod
    def _text(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise GenerationProviderError(502, "The selected AI provider returned an empty response.")
        return value

    @staticmethod
    def _response(model: GenerationModelSettings, text: str, request_id: object) -> GenerationResponse:
        return GenerationResponse(model_id=model.model_id, provider=model.provider, upstream_model=model.upstream_model,
            text=text, provider_request_id=request_id if isinstance(request_id, str) else None)


def get_generation_service() -> GenerationService:
    return GenerationService(get_settings())
