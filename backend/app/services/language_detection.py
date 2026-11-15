"""Local English, Kiswahili, and code-switch language detection."""

from dataclasses import dataclass
from functools import lru_cache
import math
import re
from threading import Lock

import py3langid as langid


SupportedLanguage = str

_TOKEN_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ']+")
_FLIGHT_DESIGNATOR = re.compile(r"\b[A-Za-z]{2}\s*\d{1,4}\b")
_GREETING_LANGUAGE = {
    "hello": "en",
    "hi": "en",
    "hey": "en",
    "habari": "sw",
    "hujambo": "sw",
    "jambo": "sw",
    "mambo": "sw",
}

# Function words and common passenger phrases provide code-switch boundaries.
# The statistical model remains authoritative for otherwise monolingual text.
_ENGLISH_MARKERS = {
    "allowance", "and", "baggage", "can", "change", "date", "delayed",
    "does", "flight", "for", "from", "hello", "how", "i", "is", "know", "my",
    "please", "status", "the", "to", "today", "tomorrow", "want", "what",
}
_SWAHILI_MARKERS = {
    "asante", "gani", "habari", "hali", "imechelewa", "ina", "jambo",
    "je", "kesho", "kujua", "kwa", "leo", "mizigo", "na", "nataka",
    "ndege", "ni", "ninaweza", "safari", "tafadhali", "tarehe", "ya",
    "yangu", "kwenda", "kutoka",
}


@dataclass(frozen=True)
class LanguageDetection:
    """One explicit detector decision used throughout a chatbot turn."""

    language: SupportedLanguage
    source: str
    confidence: float | None


class LanguageDetector:
    """Combine local statistical detection with conservative code-switch rules."""

    def __init__(self) -> None:
        # py3langid is process-local and performs no network requests.
        langid.set_languages(["en", "sw", "zxx"])
        self._lock = Lock()

    def detect(
        self,
        text: str,
        previous_language: str | None = None,
    ) -> LanguageDetection:
        cleaned = _FLIGHT_DESIGNATOR.sub(" ", text)
        tokens = [
            token.lower()
            for token in _TOKEN_PATTERN.findall(cleaned)
        ]
        previous = (
            previous_language
            if previous_language in {"en", "sw", "mixed"}
            else None
        )

        if not tokens:
            return self._context_or_unknown(previous)

        greeting_languages = {
            _GREETING_LANGUAGE[token]
            for token in tokens
            if token in _GREETING_LANGUAGE
        }
        if len(tokens) <= 2 and greeting_languages:
            language = (
                "mixed"
                if len(greeting_languages) > 1
                else greeting_languages.pop()
            )
            return LanguageDetection(language, "detected", 1.0)

        # A single non-greeting word does not provide enough evidence for a
        # trustworthy statistical decision (for example, "Okay" or "Sure").
        if len(tokens) == 1:
            return self._context_or_unknown(previous)

        english_evidence = sum(
            token in _ENGLISH_MARKERS
            for token in tokens
        )
        swahili_evidence = sum(
            token in _SWAHILI_MARKERS
            for token in tokens
        )

        # Require evidence from both languages. This prevents airline names,
        # flight codes, and a single borrowed technical term from forcing mixed.
        if english_evidence >= 2 and swahili_evidence >= 2:
            return LanguageDetection("mixed", "detected", 1.0)
        if (
            english_evidence >= 1
            and swahili_evidence >= 1
            and len(tokens) >= 2
        ):
            return LanguageDetection("mixed", "detected", 0.9)

        language, confidence = self._statistical_language(text)
        if language in {"en", "sw"} and confidence >= 0.65:
            return LanguageDetection(language, "detected", confidence)

        # A short or statistically uncertain turn inherits only established
        # conversation context. Otherwise uncertainty stays visible in storage.
        return self._context_or_unknown(previous)

    def _statistical_language(self, text: str) -> tuple[str, float]:
        with self._lock:
            ranked = langid.rank(text)
        scores = [score for _, score in ranked]
        maximum = max(scores)
        exponentials = [math.exp(score - maximum) for score in scores]
        total = sum(exponentials)
        probabilities = {
            label: value / total
            for (label, _), value in zip(ranked, exponentials, strict=True)
        }
        language = ranked[0][0]
        return language, probabilities.get(language, 0.0)

    @staticmethod
    def _context_or_unknown(
        previous_language: str | None,
    ) -> LanguageDetection:
        if previous_language:
            return LanguageDetection(
                previous_language,
                "conversation_context",
                None,
            )
        return LanguageDetection("unknown", "undetermined", None)


@lru_cache(maxsize=1)
def get_language_detector() -> LanguageDetector:
    return LanguageDetector()

