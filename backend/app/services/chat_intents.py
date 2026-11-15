"""Conservative deterministic handling for non-policy passenger messages."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class StaticIntentResponse:
    """A local response that does not require retrieval or model generation."""

    intent: str
    messages: dict[str, str]

    def for_language(self, language: str) -> str:
        return self.messages.get(language, self.messages["en"])


_NORMALIZED_SPACE = re.compile(r"\s+")
_GREETING = re.compile(
    r"^(?:hello|hi|hey|good\s+(?:morning|afternoon|evening)|"
    r"habari|hujambo|jambo|mambo|sasa)"
    r"(?:[, ]+(?:hello|hi|hey|habari|hujambo|jambo|mambo|sasa))?"
    r"[!.? ]*$",
    re.IGNORECASE,
)
_THANKS = re.compile(
    r"^(?:thank\s+you|thanks|thank\s+you\s+very\s+much|asante|asante\s+sana)"
    r"[!.? ]*$",
    re.IGNORECASE,
)
_IDENTITY = re.compile(
    r"\b(?:who\s+are\s+you|what(?:'s|\s+is)\s+your\s+name|"
    r"wewe\s+ni\s+nani|jina\s+lako)\b",
    re.IGNORECASE,
)
_LANGUAGES = re.compile(
    r"\b(?:what|which)\s+languages?\b|\b(?:supported|speak)\s+languages?\b|"
    r"\blugha\s+gani\b|\bunaweza\s+kuongea\b",
    re.IGNORECASE,
)
_CAPABILITIES = re.compile(
    r"\b(?:what\s+can\s+you\s+do|what\s+can\s+you\s+help\s+me\s+with|"
    r"how\s+can\s+you\s+help|"
    r"what\s+do\s+you\s+help\s+with|unaweza\s+kusaidia\s+na\s+nini|"
    r"unanisaidia\s+vipi|what\s+questions\s+can\s+you\s+answer|"
    r"which\s+topics\s+do\s+you\s+support|what\s+are\s+your\s+capabilities|"
    r"unajibu\s+maswali\s+gani|unajibu\s+maswali\s+kuhusu\s+nini|"
    r"what\s+services\s+unaweza\s+kusaidia\s+nazo)\b",
    re.IGNORECASE,
)
_UNRELATED = re.compile(
    r"\b(?:do\s+you\s+like|can\s+we\s+be\s+friends|"
    r"who\s+won\s+the\s+world\s+cup|tell\s+me\s+a\s+joke|"
    r"write\s+(?:code|a\s+poem|an\s+essay)|"
    r"your\s+(?:feelings|favourite|favorite)|"
    r"unapenda\s+nini|tunaweza\s+kuwa\s+marafiki)\b",
    re.IGNORECASE,
)
_PROMPT_DISCLOSURE = re.compile(
    r"\b(?:reveal|show|print|repeat|disclose)\b.{0,30}"
    r"\b(?:system\s+prompt|hidden\s+instructions?|api\s+keys?|credentials?)\b",
    re.IGNORECASE,
)


_RESPONSES = {
    "greeting": StaticIntentResponse(
        "greeting",
        {
            "en": "Hello! Welcome to Kenya Airways support. How can I help you today?",
            "sw": "Habari! Karibu kwenye huduma ya Kenya Airways. Ninaweza kukusaidia vipi leo?",
            "mixed": "Hello! Karibu Kenya Airways support. Ninaweza kukusaidia vipi leo?",
        },
    ),
    "acknowledgement": StaticIntentResponse(
        "acknowledgement",
        {
            "en": "You're welcome! Happy to help.",
            "sw": "Karibu! Nimefurahi kukusaidia.",
            "mixed": "You're welcome! Nimefurahi kusaidia.",
        },
    ),
    "identity": StaticIntentResponse(
        "identity",
        {
            "en": "I am the Kenya Airways virtual support assistant.",
            "sw": "Mimi ni msaidizi pepe wa huduma kwa wateja wa Kenya Airways.",
            "mixed": "Mimi ni Kenya Airways virtual support assistant.",
        },
    ),
    "languages": StaticIntentResponse(
        "languages",
        {
            "en": "You can chat with me in English, Kiswahili, or a combination of both. I detect the language automatically.",
            "sw": "Unaweza kuwasiliana nami kwa Kiingereza, Kiswahili, au mchanganyiko wa lugha hizo. Ninatambua lugha kiotomatiki.",
            "mixed": "Unaweza ku-chat nami in English, Kiswahili, au mchanganyiko wa zote mbili. Ninatambua language automatically.",
        },
    ),
    "capabilities": StaticIntentResponse(
        "capabilities",
        {
            "en": "I can explain Kenya Airways booking and payment procedures, baggage, check-in, refunds, cancellations, rebooking, upgrades, special assistance, and other available policies. I can also look up supported KQ flight status and schedule information. I cannot directly complete bookings, payments, refunds, or flight changes.",
            "sw": "Ninaweza kueleza taratibu za Kenya Airways kuhusu nafasi na malipo, mizigo, kuingia, marejesho, kughairi, kubadilisha safari, kuboresha daraja, msaada maalum, na sera nyingine zinazopatikana. Pia ninaweza kuangalia hali na ratiba za ndege za KQ zinazotumika. Siwezi kutekeleza nafasi, malipo, marejesho, au mabadiliko ya safari moja kwa moja.",
            "mixed": "Ninaweza kusaidia na Kenya Airways booking na payment procedures, baggage, check-in, refunds, cancellations, rebooking, upgrades, special assistance, na sera nyingine zinazopatikana. Pia ninaweza ku-check supported KQ flight status na schedules. Siwezi kukamilisha transactions moja kwa moja.",
        },
    ),
    "unrelated": StaticIntentResponse(
        "unrelated",
        {
            "en": "I’m here to help with Kenya Airways services, policies, and flight information. Do you have a Kenya Airways-related question?",
            "sw": "Nipo hapa kusaidia kuhusu huduma, sera, na taarifa za ndege za Kenya Airways. Una swali linalohusu Kenya Airways?",
            "mixed": "Nipo hapa kusaidia na Kenya Airways services, policies, na flight information. Una Kenya Airways-related question?",
        },
    ),
}


def classify_static_intent(question: str) -> StaticIntentResponse | None:
    """Classify only high-confidence local intents; policy ambiguity goes to RAG."""

    normalized = _NORMALIZED_SPACE.sub(" ", question.strip())
    if _GREETING.fullmatch(normalized):
        return _RESPONSES["greeting"]
    if _THANKS.fullmatch(normalized):
        return _RESPONSES["acknowledgement"]
    if _IDENTITY.search(normalized):
        return _RESPONSES["identity"]
    if _LANGUAGES.search(normalized):
        return _RESPONSES["languages"]
    if _CAPABILITIES.search(normalized):
        return _RESPONSES["capabilities"]
    if _UNRELATED.search(normalized) or _PROMPT_DISCLOSURE.search(normalized):
        return _RESPONSES["unrelated"]
    return None


def is_substantive_message(question: str) -> bool:
    """Return false only for standalone greetings and acknowledgements."""

    classified = classify_static_intent(question)
    return classified is None or classified.intent not in {
        "greeting",
        "acknowledgement",
    }


def static_intent_language(
    question: str,
    previous_language: str | None = None,
) -> str:
    """Detect short static-message language without loading statistical models."""

    tokens = set(re.findall(r"[a-z]+", question.lower()))
    english = bool(tokens & {
        "hello",
        "hi",
        "hey",
        "good",
        "morning",
        "afternoon",
        "evening",
        "thank",
        "thanks",
        "you",
    })
    kiswahili = bool(tokens & {
        "habari",
        "hujambo",
        "jambo",
        "mambo",
        "sasa",
        "asante",
        "sana",
    })
    if english and kiswahili:
        return "mixed"
    if kiswahili:
        return "sw"
    if english:
        return "en"
    if previous_language in {"en", "sw", "mixed"}:
        return previous_language
    return "unknown"
