import unittest

import httpx

from app.config import GenerationModelSettings, Settings
from app.rag.generation import GenerationProviderError, GenerationService


class FakeClient:
    def __init__(self, response=None, error=None, **kwargs):
        self.results = list(response) if isinstance(response, list) else [response]
        self.error, self.kwargs = error, kwargs
        self.request = None
        self.calls = 0
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def post(self, url, **kwargs):
        self.request = (url, kwargs)
        self.calls += 1
        if self.error: raise self.error
        return self.results.pop(0)


def response(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://example.test"))


class GenerationServiceTests(unittest.TestCase):
    def service(self, models, default="router", result=None, error=None):
        fake = FakeClient(result, error)
        return GenerationService(Settings(default_generation_model_id=default, generation_models=tuple(models)), lambda **kw: fake), fake

    def test_openrouter_uses_selected_model_and_bearer_key(self):
        model = GenerationModelSettings(model_id="router", provider="openrouter", upstream_model="vendor/gemma", api_key="secret")
        service, fake = self.service([model], result=response(200, {"id": "r1", "choices": [{"message": {"content": "Hello"}}]}))
        result = service.generate("Hi", system_instruction="System")
        self.assertEqual(result.text, "Hello")
        self.assertEqual(fake.request[1]["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(fake.request[1]["json"]["model"], "vendor/gemma")

    def test_google_uses_google_header_and_response_shape(self):
        model = GenerationModelSettings(model_id="google", provider="google_ai_studio", upstream_model="gemini-flash", api_key="secret")
        service, fake = self.service([model], default="google", result=response(200, {"responseId": "g1", "candidates": [{"content": {"parts": [{"text": "Habari"}]}}]}))
        result = service.generate("Jambo")
        self.assertEqual(result.text, "Habari")
        self.assertEqual(fake.request[1]["headers"]["x-goog-api-key"], "secret")
        self.assertIn("gemini-flash:generateContent", fake.request[0])

    def test_gemini_38_uses_low_thinking_without_thought_summaries(self):
        model = GenerationModelSettings(
            model_id="google", provider="google_ai_studio", upstream_model="gemini-3.8-flash", api_key="secret",
        )
        service, fake = self.service(
            [model], default="google",
            result=response(200, {"candidates": [{"content": {"parts": [{"text": "23 kg"}]}}]}),
        )
        service.generate("What is the baggage allowance?")
        thinking = fake.request[1]["json"]["generationConfig"]["thinkingConfig"]
        self.assertEqual(thinking, {"thinkingLevel": "low", "includeThoughts": False})

    def test_errors_do_not_fallback_to_another_model(self):
        models = [GenerationModelSettings(model_id="router", provider="openrouter", upstream_model="one", api_key="key"),
                  GenerationModelSettings(model_id="other", provider="openrouter", upstream_model="two", api_key="key")]
        service, _ = self.service(models, result=response(429, {}))
        with self.assertRaisesRegex(GenerationProviderError, "rate limit") as raised:
            service.generate("Hi")
        self.assertEqual(raised.exception.status_code, 429)

    def test_documented_provider_failure_categories_are_clear(self):
        model = GenerationModelSettings(model_id="router", provider="openrouter", upstream_model="one", api_key="key")
        expected = {400: (400, "invalid"), 401: (502, "credentials"), 402: (502, "credits"), 404: (502, "unavailable")}
        for provider_status, (api_status, text) in expected.items():
            service, _ = self.service([model], result=response(provider_status, {"error": {"message": "private"}}))
            with self.assertRaisesRegex(GenerationProviderError, text) as raised:
                service.generate("Hi")
            self.assertEqual(raised.exception.status_code, api_status)

    def test_timeout_and_missing_key_are_clear(self):
        model = GenerationModelSettings(model_id="router", provider="openrouter", upstream_model="one", api_key="key")
        service, _ = self.service([model], error=httpx.ReadTimeout("slow"))
        with self.assertRaisesRegex(GenerationProviderError, "timed out"):
            service.generate("Hi")
        unconfigured, _ = self.service([model.model_copy(update={"api_key": None})])
        with self.assertRaisesRegex(GenerationProviderError, "API key"):
            unconfigured.generate("Hi")

    def test_google_retries_transient_unavailability(self):
        model = GenerationModelSettings(model_id="google", provider="google_ai_studio", upstream_model="gemini-3.8-flash", api_key="secret")
        service, fake = self.service(
            [model], default="google",
            result=[
                response(503, {}),
                response(200, {"candidates": [{"content": {"parts": [{"text": "23 kg"}]}}]}),
            ],
        )
        result = service.generate("Hi")
        self.assertEqual(result.text, "23 kg")
        self.assertEqual(fake.calls, 2)

    def test_list_metadata_never_contains_secret(self):
        model = GenerationModelSettings(model_id="router", provider="openrouter", upstream_model="one", api_key="secret")
        service, _ = self.service([model])
        self.assertNotIn("secret", str(service.configured_models()))


if __name__ == "__main__":
    unittest.main()
