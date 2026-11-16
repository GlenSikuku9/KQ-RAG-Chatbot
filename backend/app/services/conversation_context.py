"""Bounded deterministic conversational query resolution for RAG and OAG."""

from dataclasses import dataclass
import re

from app.models.retrieval import ConversationContextMessage
from app.services.airport_lookup import resolve_location


_TOPICS = {
    "baggage": re.compile(
        r"\b(baggage|luggage|allowance|mizigo)\b",
        re.IGNORECASE,
    ),
    "refund": re.compile(
        r"\b(refund|reimbursement|marejesho)\b",
        re.IGNORECASE,
    ),
    "booking_change": re.compile(
        r"\b(change|modify|reschedule|date change|badilisha|kubadilisha)\b",
        re.IGNORECASE,
    ),
    "check_in": re.compile(
        r"\b(check[ -]?in|kuingia)\b",
        re.IGNORECASE,
    ),
    "payment": re.compile(
        r"\b(payment|pay|cost|fee|charge|malipo|gharama|ada)\b",
        re.IGNORECASE,
    ),
    "special_care": re.compile(
        r"\b(wheelchair|special care|assistance|msaada|magurudumu)\b",
        re.IGNORECASE,
    ),
    "flight_status": re.compile(
        r"\b(KQ\s*\d{1,4}|track|status|delayed|landed|flight|"
        r"ndege|imechelewa|ratiba)\b",
        re.IGNORECASE,
    ),
}
_FOLLOW_UP = re.compile(
    r"^(what about|and |how (?:much|long)|what if|can you explain that|"
    r"na |je\b|kuna |vipi |kwa .* je|what about tomorrow)",
    re.IGNORECASE,
)
_CABIN = re.compile(
    r"\b(economy|business|first|premium economy)(?:\s+class)?\b",
    re.IGNORECASE,
)
_FLIGHT = re.compile(r"\bKQ\s*\d{1,4}[A-Z]?\b", re.IGNORECASE)
_DATE = re.compile(
    r"\b(today|tomorrow|leo|kesho|\d{2}-\d{2}-\d{4}|"
    r"\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
_ROUTE = re.compile(
    r"\b(?:from|kutoka)\s+.+?\s+(?:to|kwenda|hadi)\s+"
    r".+?(?=$|[?.!,]|\s+\b(?:today|tomorrow|leo|kesho|on|tarehe)\b)",
    re.IGNORECASE,
)
_ORIGIN = re.compile(
    r"\b(?:from|kutoka)\s+.+?"
    r"(?=$|[?.!,]|\s+\b(?:to|kwenda|hadi|today|tomorrow|leo|kesho)\b)",
    re.IGNORECASE,
)
_DESTINATION = re.compile(
    r"\b(?:to|kwenda|hadi)\s+.+?"
    r"(?=$|[?.!,]|\s+\b(?:today|tomorrow|leo|kesho|on|tarehe)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConversationResolution:
    """The internal query and history selected for one passenger turn."""

    resolved_question: str
    is_follow_up: bool
    topic: str | None
    context: tuple[ConversationContextMessage, ...]


def _topic(text: str) -> str | None:
    return next(
        (
            name
            for name, pattern in _TOPICS.items()
            if pattern.search(text)
        ),
        None,
    )


def _replace_constraint(
    previous: str,
    current: str,
    pattern: re.Pattern[str],
) -> str:
    replacement = pattern.search(current)
    if replacement is None:
        return previous
    if pattern.search(previous):
        return pattern.sub(
            replacement.group(0),
            previous,
            count=1,
        )
    return f"{previous} {replacement.group(0)}"


def resolve_conversation_query(
    question: str,
    context: list[ConversationContextMessage],
    *,
    history_messages: int = 8,
) -> ConversationResolution:
    """Resolve clear follow-ups without asking another language model."""

    bounded = tuple(context[-history_messages:])
    passenger_messages = [
        item.message_text.strip()
        for item in bounded
        if item.sender_type == "customer"
        and item.message_text.strip()
    ]
    current_topic = _topic(question)
    if not passenger_messages:
        return ConversationResolution(
            question,
            False,
            current_topic,
            bounded,
        )

    previous = passenger_messages[-1]
    if current_topic:
        matching_previous = next(
            (
                message
                for message in reversed(passenger_messages)
                if _topic(message) == current_topic
            ),
            None,
        )
        if matching_previous is not None:
            previous = matching_previous
    elif len(question.split()) <= 8:
        # A short answer to a clarification (for example "tomorrow" followed
        # by "Nairobi") must return to the most recent substantive topic, not
        # treat the previous one-word clarification answer as a new topic.
        topical_previous = next(
            (
                message
                for message in reversed(passenger_messages)
                if _topic(message) is not None
            ),
            None,
        )
        if topical_previous is not None:
            previous = topical_previous
    previous_topic = _topic(previous)
    word_count = len(question.split())
    explicit_follow_up = bool(_FOLLOW_UP.search(question.strip()))
    carries_constraint = bool(
        _CABIN.search(question)
        or _FLIGHT.search(question)
        or _DATE.search(question)
        or _ROUTE.search(question)
        or _ORIGIN.search(question)
        or _DESTINATION.search(question)
    )

    # A complete route is self-contained. Do not inherit a flight number from
    # an earlier lookup in the same conversation, because that would make the
    # flight-number path ignore the passenger's newly requested route.
    if _ROUTE.search(question) and not _FLIGHT.search(question):
        return ConversationResolution(
            question,
            False,
            current_topic,
            bounded,
        )

    # An explicit new policy topic starts a clean retrieval query.
    if (
        current_topic
        and previous_topic
        and current_topic != previous_topic
        and not explicit_follow_up
    ):
        return ConversationResolution(
            question,
            False,
            current_topic,
            bounded,
        )

    is_follow_up = bool(
        explicit_follow_up
        or carries_constraint
        or (
            word_count <= 8
            and current_topic is None
            and previous_topic is not None
        )
    )
    if not is_follow_up:
        return ConversationResolution(
            question,
            False,
            current_topic,
            bounded,
        )

    resolved = previous
    previous_index = passenger_messages.index(previous)
    follow_up_messages = [
        *passenger_messages[previous_index + 1:],
        question,
    ]

    # Apply constraints from every short clarification answer since the base
    # enquiry. This preserves a date supplied on one turn when the passenger
    # supplies the departure city on the next turn.
    for follow_up_message in follow_up_messages:
        for pattern in (_CABIN, _FLIGHT, _DATE):
            resolved = _replace_constraint(
                resolved,
                follow_up_message,
                pattern,
            )

    # A complete new route replaces both prior locations. A partial follow-up
    # changes only its named endpoint and keeps the other route constraint.
    for follow_up_message in follow_up_messages:
        if _ROUTE.search(follow_up_message):
            resolved = _replace_constraint(
                resolved,
                follow_up_message,
                _ROUTE,
            )
            continue
        for pattern in (_ORIGIN, _DESTINATION):
            resolved = _replace_constraint(
                resolved,
                follow_up_message,
                pattern,
            )

    # The chatbot may explicitly ask for a missing origin. Accept a concise
    # city/code reply without requiring the passenger to repeat "from".
    if (
        resolve_location(question) is not None
        and _DESTINATION.search(resolved)
        and not _ORIGIN.search(resolved)
    ):
        resolved = f"from {question.strip()} {resolved}"

    # Retain the passenger's exact follow-up wording so semantic retrieval can
    # represent additions such as costs, children, or international travel.
    resolved = f"{resolved} Follow-up: {question}"
    return ConversationResolution(
        resolved,
        True,
        current_topic or previous_topic,
        bounded,
    )
