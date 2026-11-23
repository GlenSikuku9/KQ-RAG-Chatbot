"""Verified display metadata for airports supported by route extraction.

OAG Flight Info v2 guarantees airport codes, but display names are not always
included. This search-only metadata enriches presentation without changing the
airport code sent to OAG. Unknown codes deliberately remain unresolved.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AirportMetadata:
    """Passenger-facing airport identity associated with one IATA code."""

    name: str
    city: str
    country: str
    country_code: str


AIRPORT_METADATA: dict[str, AirportMetadata] = {
    "ACC": AirportMetadata(
        "Kotoka International Airport", "Accra", "Ghana", "GH",
    ),
    "AMS": AirportMetadata(
        "Amsterdam Airport Schiphol", "Amsterdam", "Netherlands", "NL",
    ),
    "CDG": AirportMetadata(
        "Paris Charles de Gaulle Airport", "Paris", "France", "FR",
    ),
    "DAR": AirportMetadata(
        "Julius Nyerere International Airport",
        "Dar es Salaam",
        "Tanzania",
        "TZ",
    ),
    "DXB": AirportMetadata(
        "Dubai International Airport",
        "Dubai",
        "United Arab Emirates",
        "AE",
    ),
    "EBB": AirportMetadata(
        "Entebbe International Airport", "Entebbe", "Uganda", "UG",
    ),
    "EDL": AirportMetadata(
        "Eldoret International Airport", "Eldoret", "Kenya", "KE",
    ),
    "HLA": AirportMetadata(
        "Lanseria International Airport",
        "Johannesburg",
        "South Africa",
        "ZA",
    ),
    "JFK": AirportMetadata(
        "John F. Kennedy International Airport",
        "New York",
        "United States",
        "US",
    ),
    "JNB": AirportMetadata(
        "O. R. Tambo International Airport",
        "Johannesburg",
        "South Africa",
        "ZA",
    ),
    "KGL": AirportMetadata(
        "Kigali International Airport", "Kigali", "Rwanda", "RW",
    ),
    "KIS": AirportMetadata(
        "Kisumu International Airport", "Kisumu", "Kenya", "KE",
    ),
    "LGW": AirportMetadata(
        "London Gatwick Airport", "London", "United Kingdom", "GB",
    ),
    "LHR": AirportMetadata(
        "Heathrow Airport", "London", "United Kingdom", "GB",
    ),
    "LOS": AirportMetadata(
        "Murtala Muhammed International Airport",
        "Lagos",
        "Nigeria",
        "NG",
    ),
    "MBA": AirportMetadata(
        "Moi International Airport", "Mombasa", "Kenya", "KE",
    ),
    "NBO": AirportMetadata(
        "Jomo Kenyatta International Airport", "Nairobi", "Kenya", "KE",
    ),
    "WIL": AirportMetadata(
        "Wilson Airport", "Nairobi", "Kenya", "KE",
    ),
    "ZNZ": AirportMetadata(
        "Abeid Amani Karume International Airport",
        "Zanzibar",
        "Tanzania",
        "TZ",
    ),
}


def airport_metadata(code: str | None) -> AirportMetadata | None:
    """Return verified metadata for a known IATA code without guessing."""

    if not code:
        return None
    return AIRPORT_METADATA.get(code.strip().upper())
