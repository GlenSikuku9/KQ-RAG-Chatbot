"""Deterministic city and airport resolution for OAG route searches."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class AirportSelection:
    """One passenger location resolved to one or more IATA airports."""

    label: str
    codes: tuple[str, ...]


# Each supported city resolves to one deterministic airport code. The current
# OAG subscription rejects comma-separated city groups. Explicit airport names
# and IATA codes still allow passengers to select another known airport.
_AIRPORT_GROUPS = {
    "nairobi": AirportSelection("Nairobi", ("NBO",)),
    "johannesburg": AirportSelection("Johannesburg", ("JNB",)),
    "london": AirportSelection("London", ("LHR",)),
    "dubai": AirportSelection("Dubai", ("DXB",)),
    "mombasa": AirportSelection("Mombasa", ("MBA",)),
    "kisumu": AirportSelection("Kisumu", ("KIS",)),
    "eldoret": AirportSelection("Eldoret", ("EDL",)),
    "zanzibar": AirportSelection("Zanzibar", ("ZNZ",)),
    "dar es salaam": AirportSelection("Dar es Salaam", ("DAR",)),
    "entebbe": AirportSelection("Entebbe", ("EBB",)),
    "kampala": AirportSelection("Kampala / Entebbe", ("EBB",)),
    "kigali": AirportSelection("Kigali", ("KGL",)),
    "lagos": AirportSelection("Lagos", ("LOS",)),
    "accra": AirportSelection("Accra", ("ACC",)),
    "paris": AirportSelection("Paris", ("CDG",)),
    "amsterdam": AirportSelection("Amsterdam", ("AMS",)),
    "new york": AirportSelection("New York", ("JFK",)),
}

_AIRPORT_NAMES = {
    "jkia": AirportSelection("Jomo Kenyatta International", ("NBO",)),
    "jomo kenyatta": AirportSelection("Jomo Kenyatta International", ("NBO",)),
    "wilson": AirportSelection("Wilson Airport", ("WIL",)),
    "o. r. tambo": AirportSelection("O. R. Tambo International", ("JNB",)),
    "or tambo": AirportSelection("O. R. Tambo International", ("JNB",)),
    "lanseria": AirportSelection("Lanseria International", ("HLA",)),
    "heathrow": AirportSelection("London Heathrow", ("LHR",)),
    "gatwick": AirportSelection("London Gatwick", ("LGW",)),
}

_KNOWN_CODES = {
    code: AirportSelection(code, (code,))
    for selection in (*_AIRPORT_GROUPS.values(), *_AIRPORT_NAMES.values())
    for code in selection.codes
}

_ROUTE = re.compile(
    r"\b(?:from|kutoka)\s+(?P<departure>.+?)\s+"
    r"(?:to|kwenda|hadi)\s+(?P<arrival>.+?)"
    r"(?=$|[?.!,]|\s+\b(?:today|tomorrow|leo|kesho|on|tarehe)\b)",
    re.IGNORECASE,
)
_DEPARTURE = re.compile(
    r"\b(?:from|kutoka)\s+(?P<location>.+?)"
    r"(?=$|[?.!,]|\s+\b(?:to|kwenda|hadi|today|tomorrow|leo|kesho)\b)",
    re.IGNORECASE,
)
_ARRIVAL = re.compile(
    r"\b(?:to|kwenda|hadi)\s+(?P<location>.+?)"
    r"(?=$|[?.!,]|\s+\b(?:today|tomorrow|leo|kesho|on|tarehe)\b)",
    re.IGNORECASE,
)


def resolve_location(value: str) -> AirportSelection | None:
    """Resolve a city, airport name, or known IATA code without guessing."""

    original = re.sub(r"\s+", " ", value).strip(" .?!,")
    if re.fullmatch(r"[A-Z]{3}", original):
        return AirportSelection(original, (original,))

    normalized = original.lower()
    direct_code = re.fullmatch(r"[a-z]{3}", normalized)
    if direct_code:
        return _KNOWN_CODES.get(normalized.upper())

    # Match longer aliases first so "London Heathrow" resolves to LHR rather
    # than expanding to every London airport.
    aliases = {
        **_AIRPORT_GROUPS,
        **_AIRPORT_NAMES,
    }
    for alias in sorted(aliases, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", normalized):
            return aliases[alias]
    return None


def extract_route(
    question: str,
) -> tuple[AirportSelection | None, AirportSelection | None, bool]:
    """Return departure, arrival, and whether route wording was present."""

    route = _ROUTE.search(question)
    if route:
        return (
            resolve_location(route.group("departure")),
            resolve_location(route.group("arrival")),
            True,
        )

    departure_match = _DEPARTURE.search(question)
    arrival_match = _ARRIVAL.search(question)
    departure = (
        resolve_location(departure_match.group("location"))
        if departure_match
        else None
    )
    arrival = (
        resolve_location(arrival_match.group("location"))
        if arrival_match
        else None
    )
    return departure, arrival, bool(departure_match or arrival_match)
