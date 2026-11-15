"""Allow-list validation for passenger-facing official source URLs."""

from urllib.parse import urlsplit


def official_kenya_airways_url(value: str | None) -> str | None:
    """Return only HTTPS links hosted by the official airline domain."""

    if value is None:
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or hostname not in {
            "kenya-airways.com",
            "www.kenya-airways.com",
        }
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return value.strip()
