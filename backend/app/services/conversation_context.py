"""Bounded deterministic conversational query resolution for RAG and OAG."""

from dataclasses import dataclass
import re

from app.models.retrieval import ConversationContextMessage


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
    previous_topic = _topic(previous)
    word_count = len(question.split())
    explicit_follow_up = bool(_FOLLOW_UP.search(question.strip()))
    carries_constraint = bool(
        _CABIN.search(question)
        or _FLIGHT.search(question)
        or _DATE.search(question)
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
    for pattern in (_CABIN, _FLIGHT, _DATE):
        resolved = _replace_constraint(
            resolved,
            question,
            pattern,
        )

    # Retain the passenger's exact follow-up wording so semantic retrieval can
    # represent additions such as costs, children, or international travel.
    resolved = f"{resolved} Follow-up: {question}"
    return ConversationResolution(
        resolved,
        True,
        current_topic or previous_topic,
        bounded,
    )
