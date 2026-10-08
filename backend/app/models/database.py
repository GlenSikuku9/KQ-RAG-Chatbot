from datetime import datetime, timezone
import logging
from typing import Annotated, TypeAlias
from urllib.parse import quote

from pydantic import AfterValidator, AwareDatetime


logger = logging.getLogger(__name__)

# Keep native datetimes for Firestore; HTTP responses serialize them as ISO 8601.
UTCDateTime: TypeAlias = Annotated[
    datetime, AwareDatetime(), AfterValidator(lambda value: value.astimezone(timezone.utc))
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def encode_document_id(record_id: str) -> str:
    """Encode one logical ID without allowing nested paths or reserved document names."""

    if not isinstance(record_id, str) or not record_id.strip():
        logger.error("A non-empty string is required for a Firestore record ID.")
        raise ValueError("Invalid Firestore record ID.")
    try:
        encoded = quote(record_id, safe="")
    except UnicodeError as exc:
        logger.error("Firestore record ID contains invalid Unicode.")
        raise ValueError("Invalid Firestore record ID.") from exc
    if encoded in {".", ".."}:
        encoded = encoded.replace(".", "%2E")
    if encoded.startswith("__") and encoded.endswith("__"):
        encoded = encoded.replace("_", "%5F")
    if len(encoded.encode("utf-8")) > 1500:
        logger.error("Encoded Firestore record ID exceeds the document ID limit.")
        raise ValueError("Firestore record ID is too long.")
    return encoded
