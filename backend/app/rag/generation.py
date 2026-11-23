"""Provider adapters for configurable text-generation models (I13)."""

import logging
import re
from collections.abc import Callable
from functools import lru_cache
from time import sleep
from time import perf_counter

import httpx
from fastapi import status

from app.config import GenerationModelSettings, Settings, get_settings
from app.models.generation import ConfiguredModel, GenerationResponse

logger = logging.getLogger(__name__)


class GenerationProviderError(Exception):
    """Safe public failure; it never contains credentials or raw provider bodies."""
    def __init__(
        self,
        status_code: int,
        detail: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.status_code, self.detail = status_code, detail
        self.retry_after_seconds = retry_after_seconds
        super().__init__(detail)


class GenerationService:
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory
        self.sleeper = sleeper
        self.models = {model.model_id: model for model in settings.generation_models}
        # A process-wide service reuses this client so consecutive requests can
        # reuse DNS results and pooled TCP/TLS connections to Groq/OpenRouter.
        self.client = self.client_factory()

    def configured_models(self) -> list[ConfiguredModel]:
        default_model_id = self._effective_default_model_id()
        return [
            ConfiguredModel(
                model_id=model.model_id,
                provider=model.provider,
                upstream_model=model.upstream_model,
                configured=bool(model.api_key),
                is_default=model.model_id == default_model_id,
            )
            for model in self.models.values()
        ]

    def selected_model(self, model_id: str | None = None) -> ConfiguredModel:
        """Return safe metadata for the model selected by the normal rules."""
        model = self._select_model(model_id)
        return ConfiguredModel(
            model_id=model.model_id,
            provider=model.provider,
            upstream_model=model.upstream_model,
            configured=bool(model.api_key),
            is_default=model_id is None,
        )

    def generate(
        self,
        prompt: str,
        model_id: str | None = None,
        system_instruction: str | None = None,
    ) -> GenerationResponse:
        model = self._select_model(model_id)
        if not model.api_key:
            raise GenerationProviderError(
                503,
                "The selected model is not configured with an API key.",
            )
        provider_duration_ms = 0.0
        for attempt in (1, 2, 3):
            attempt_started = perf_counter()
            try:
                if model.provider == "openrouter":
                    response = self._openrouter(
                        model,
                        prompt,
                        system_instruction,
                    )
                elif model.provider == "groq":
                    response = self._groq(
                        model,
                        prompt,
                        system_instruction,
                    )
                else:
                    raise GenerationProviderError(
                        503,
                        "The selected AI provider is not supported.",
                    )
                provider_duration_ms += (
                    perf_counter() - attempt_started
                ) * 1000
                return response.model_copy(
                    update={
                        "provider_duration_ms": round(
                            provider_duration_ms,
                            3,
                        ),
                        "attempt_count": attempt,
                    },
                )
            except httpx.TimeoutException as error:
                provider_duration_ms += (
                    perf_counter() - attempt_started
                ) * 1000
                logger.warning(
                    "Generation request timed out for %s.",
                    model.model_id,
                )
                raise GenerationProviderError(
                    504,
                    "The selected AI provider timed out. Please try again later.",
                ) from error
            except GenerationProviderError as error:
                provider_duration_ms += (
                    perf_counter() - attempt_started
                ) * 1000
                # Retry only transient provider outages. Client errors such as an
                # invalid key or rate limit return immediately to avoid request spam.
                if error.status_code != 503 or attempt == 3:
                    raise
                # Provider capacity spikes are usually short. A small backoff is
                # more useful than immediately repeating the same failed call.
                delay_seconds = 2 ** attempt
                logger.warning(
                    "Generation provider unavailable for %s; retrying in %ss.",
                    model.model_id,
                    delay_seconds,
                )
                self.sleeper(delay_seconds)
            except httpx.RequestError as error:
                provider_duration_ms += (
                    perf_counter() - attempt_started
                ) * 1000
                logger.warning(
                    "Generation network failure for %s: %s",
                    model.model_id,
                    type(error).__name__,
                )
                raise GenerationProviderError(
                    503,
                    "The selected AI provider is unavailable. Please try again later.",
                ) from error
        raise GenerationProviderError(
            503,
            "The selected AI provider is unavailable. Please try again later.",
        )

    def _select_model(self, requested_id: str | None) -> GenerationModelSettings:
        model_id = requested_id or self._effective_default_model_id()
        if not model_id:
            raise GenerationProviderError(503, "No default generation model is configured.")
        if model_id not in self.models:
            raise GenerationProviderError(404, "The requested generation model is not configured.")
        return self.models[model_id]

    def _effective_default_model_id(self) -> str | None:
        """Ignore a persisted default that is no longer in the active registry."""
        stored = self._stored_default_model_id()
        if stored in self.models:
            return stored
        configured_default = self.settings.default_generation_model_id
        if configured_default in self.models:
            return configured_default
        configured = next(
            (
                model.model_id
                for model in self.models.values()
                if model.api_key
            ),
            None,
        )
        return configured or next(iter(self.models), None)

    def repair_stored_default(self) -> None:
        """Persist a valid fallback when an obsolete model was the default."""
        stored = self._stored_default_model_id()
        replacement = self._effective_default_model_id()
        if not stored or stored in self.models or not replacement:
            return
        from app.services.firestore_client import (
            firestore_operation,
            get_firestore_client,
        )
        with firestore_operation():
            get_firestore_client().collection("app_settings").document(
                "generation_config",
            ).set(
                {"default_model_id": replacement},
                merge=True,
            )

    @staticmethod
    def _stored_default_model_id() -> str | None:
        """Use the administrator-selected default when Firestore has one; env config remains the fallback."""
        try:
            from app.services.firestore_client import (
                FIRESTORE_READ_TIMEOUT,
                firestore_operation,
                get_firestore_client,
            )

            with firestore_operation():
                snapshot = (
                    get_firestore_client()
                    .collection("app_settings")
                    .document("generation_config")
                    .get(timeout=FIRESTORE_READ_TIMEOUT)
                )
            value = (
                (snapshot.to_dict() or {}).get("default_model_id")
                if snapshot.exists
                else None
            )
            return (
                value
                if isinstance(value, str) and value.strip()
                else None
            )
        except Exception:
            # Model generation must continue using the environment default if settings storage is unavailable.
            return None

    def _openrouter(
        self,
        model: GenerationModelSettings,
        prompt: str,
        system: str | None,
    ) -> GenerationResponse:
        return self._openai_compatible(
            "https://openrouter.ai/api/v1/chat/completions",
            model,
            prompt,
            system,
            "max_tokens",
        )

    def _groq(
        self,
        model: GenerationModelSettings,
        prompt: str,
        system: str | None,
    ) -> GenerationResponse:
        # Groq exposes an OpenAI-compatible chat-completions contract.
        return self._openai_compatible(
            "https://api.groq.com/openai/v1/chat/completions",
            model,
            prompt,
            system,
            "max_completion_tokens",
        )

    def _openai_compatible(
        self,
        url: str,
        model: GenerationModelSettings,
        prompt: str,
        system: str | None,
        token_field: str,
    ) -> GenerationResponse:
        messages = (
            ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}]
        )
        data = self._post(
            url,
            model,
            {
                "model": model.upstream_model,
                "messages": messages,
                token_field: model.max_output_tokens,
            },
            {"Authorization": f"Bearer {model.api_key}"},
        )
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise GenerationProviderError(502, "The selected AI provider returned an invalid response.") from error
        return self._response(model, self._text(text), data.get("id"))

    def _post(
        self,
        url: str,
        model: GenerationModelSettings,
        payload: dict,
        headers: dict[str, str],
    ) -> dict:
        response = self.client.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                **headers,
            },
            timeout=model.timeout_seconds,
        )
        # Keep useful provider categories distinct. Do not return the raw response:
        # it can contain provider internals and must never include secrets in logs/API output.
        if response.status_code == 400:
            raise GenerationProviderError(
                400,
                "The selected AI provider rejected this request as invalid or unsupported.",
            )
        if response.status_code in {401, 403}:
            raise GenerationProviderError(
                502,
                "The selected AI provider rejected its configured credentials or access.",
            )
        if response.status_code == 402:
            raise GenerationProviderError(502, "The selected AI provider account has insufficient credits.")
        if response.status_code == 404:
            raise GenerationProviderError(502, "The selected AI model is unavailable from its provider.")
        if response.status_code == 429:
            try:
                retry_after = float(response.headers.get("retry-after", ""))
            except ValueError:
                retry_after = None
            raise GenerationProviderError(
                429,
                "The selected AI provider rate limit was reached. Please try again later.",
                retry_after_seconds=retry_after,
            )
        if response.status_code in {500, 502, 503, 504}:
            logger.warning(
                "Generation provider returned status %s for %s.",
                response.status_code,
                model.model_id,
            )
            raise GenerationProviderError(
                503,
                "The selected AI provider is unavailable. Please try again later.",
            )
        if response.status_code >= 400:
            logger.warning(
                "Generation provider returned status %s for %s.",
                response.status_code,
                model.model_id,
            )
            raise GenerationProviderError(502, "The selected AI provider rejected the request.")
        try:
            return response.json()
        except ValueError as error:
            raise GenerationProviderError(
                502,
                "The selected AI provider returned an invalid response.",
            ) from error

    @staticmethod
    def _text(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise GenerationProviderError(
                502,
                "The selected AI provider returned an empty response.",
            )

        # Provider output occasionally contains invisible Unicode spacing in
        # citations or escaped table delimiters. Normalize these presentation
        # artifacts before validation, persistence, and frontend rendering.
        normalized = value.translate({
            ord("\u00a0"): " ",
            ord("\u202f"): " ",
            ord("\u200b"): None,
            ord("\u200c"): None,
            ord("\u200d"): None,
            ord("\ufeff"): None,
        })
        normalized = normalized.replace(r"\|", "|")
        normalized = re.sub(
            r"(?m)(^\s*\|.*\|\s*$)\n\s*\n(?=\s*\|)",
            r"\1\n",
            normalized,
        )
        return normalized.strip()

    @staticmethod
    def _response(
        model: GenerationModelSettings,
        text: str,
        request_id: object,
    ) -> GenerationResponse:
        return GenerationResponse(
            model_id=model.model_id,
            provider=model.provider,
            upstream_model=model.upstream_model,
            text=text,
            provider_request_id=(
                request_id
                if isinstance(request_id, str)
                else None
            ),
        )


@lru_cache(maxsize=1)
def get_generation_service() -> GenerationService:
    return GenerationService(get_settings())
