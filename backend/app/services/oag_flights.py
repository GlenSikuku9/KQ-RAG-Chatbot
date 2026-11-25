"""Deterministic Kenya Airways flight lookups through OAG Flight Info v2."""

import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from time import monotonic, sleep
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings, get_settings
from app.models.flight import FlightAirport, FlightInformation
from app.models.generation import GroundedAnswerRequest, GroundedAnswerResponse
from app.services.airport_lookup import AirportSelection, extract_route
from app.services.airport_metadata import airport_metadata
from app.services.chat_intents import is_booking_action_request
from app.services.operational_logging import get_operational_logger


logger = logging.getLogger(__name__)
NAIROBI_TIMEZONE = ZoneInfo("Africa/Nairobi")
OAG_SUBSCRIPTION_HEADER = "Subscription-Key"
FLIGHT_NUMBER = re.compile(
    r"\b(?:KQ\s*-?\s*|Kenya\s+Airways(?:\s+flight)?\s+)"
    r"([0-9]{1,4}[A-Z]?)\b",
    re.IGNORECASE,
)
BARE_FLIGHT_NUMBER = re.compile(
    r"\bflight\s+([0-9]{1,4}[A-Z]?)\b",
    re.IGNORECASE,
)
OTHER_AIRLINE_FLIGHT = re.compile(
    r"\b([A-Z][A-Z0-9])\s*-?\s*([0-9]{1,4}[A-Z]?)\b",
    re.IGNORECASE,
)
OTHER_AIRLINE_ROUTE = re.compile(r"\b([A-Z0-9]{2})\s+flights?\b")
ISO_DATE = re.compile(r"\b(20[0-9]{2}-[01][0-9]-[0-3][0-9])\b")
DISPLAY_DATE = re.compile(r"\b([0-3][0-9]-[01][0-9]-20[0-9]{2})\b")
FLIGHT_INTENT = re.compile(
    r"\b(track|tracking|status|delayed|delay|landed|depart|departure|"
    r"arrive|arrival|in[- ]?flight|cancelled|canceled|schedule|time|"
    r"fuatilia|nifuatilie|iko wapi|imechelewa|itaondoka|inafika|"
    r"imefika|ratiba|hali|flights?|ndege)\b",
    re.IGNORECASE,
)
POLICY_INTENT = re.compile(
    r"\b(what happens|policy|refund|compensation|rebook|rights|"
    r"sera|marejesho|fidia)\b",
    re.IGNORECASE,
)
TIME_WINDOW = re.compile(
    r"\b(morning|asubuhi|afternoon|mchana|evening|jioni)\b",
    re.IGNORECASE,
)


class OAGFlightError(Exception):
    """A credential-safe provider failure suitable for an API response."""

    def __init__(self, status_code: int, detail: str, code: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.code = code
        super().__init__(detail)


class OAGFlightService:
    """Validate, cache and map OAG responses without using an LLM."""

    _cache: dict[str, tuple[float, FlightInformation]] = {}
    _route_cache: dict[str, tuple[float, list[FlightInformation]]] = {}
    _last_provider_request: dict[str, float] = {}
    _cache_lock = Lock()

    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        sleeper: Callable[[float], None] = sleep,
        usage_recorder: Callable[..., None] | None = None,
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory
        self.sleeper = sleeper
        self.usage_recorder = usage_recorder

    def answer_if_flight_query(
        self,
        request: GroundedAnswerRequest,
        *,
        force_refresh: bool = False,
    ) -> GroundedAnswerResponse | None:
        """Return None for policy queries so the existing RAG path remains authoritative."""

        # A resolved follow-up may inherit the flight number or date, while the
        # original passenger wording remains unchanged in conversation storage.
        question = (
            request.resolved_question
            or request.question
        ).strip()
        # Booking transactions are not operational flight-information lookups.
        # The conversation router supplies deterministic booking guidance.
        if is_booking_action_request(request.question):
            return None
        match = FLIGHT_NUMBER.search(question)
        has_flight_intent = bool(FLIGHT_INTENT.search(question))

        # Conditional refund and compensation questions remain policy questions.
        if POLICY_INTENT.search(question):
            return None
        unsupported_match = OTHER_AIRLINE_FLIGHT.search(question)
        unsupported_route = OTHER_AIRLINE_ROUTE.search(question)
        if (
            (
                unsupported_match
                and unsupported_match.group(1).upper() != "KQ"
            )
            or (
                unsupported_route
                and unsupported_route.group(1).upper() != "KQ"
            )
        ) and has_flight_intent:
            return self._unsupported_airline(request)
        # A bare "flight 100" is interpreted as KQ only because this is the
        # Kenya Airways assistant and an operational flight intent is explicit.
        if match is None and has_flight_intent:
            match = BARE_FLIGHT_NUMBER.search(question)
        if not has_flight_intent:
            return None

        # Route and destination searches use the same Flight Info v2 endpoint
        # as numbered lookups. Extraction remains deterministic and therefore
        # adds no model call, latency, or hallucinated airport information.
        if match is None:
            departure, arrival, route_wording = extract_route(question)
            if not route_wording:
                return None
            if departure is None and arrival is None:
                return self._location_clarification(request)

            travel_date = self._travel_date(question)
            if travel_date is None:
                return self._route_date_clarification(
                    request,
                    departure,
                    arrival,
                )
            # The current subscription rejects arrival-only Flight Info
            # searches. Ask for the missing origin instead of forwarding a
            # provider-invalid filter combination or guessing Nairobi.
            if departure is None and arrival is not None:
                return self._departure_clarification(
                    request,
                    arrival,
                    travel_date,
                )
            if not self.settings.oag_route_search_enabled:
                return self._route_subscription_limitation(request)

            try:
                options = self.lookup_route(
                    travel_date,
                    departure=departure,
                    arrival=arrival,
                )
            except OAGFlightError as error:
                # Some OAG subscriptions accept numbered-flight filters but
                # reject route filters. This is a capability limitation, not a
                # chatbot failure requiring a support ticket.
                if error.code == "invalid_provider_filters":
                    return self._route_provider_limitation(request)
                raise self._localized_error(
                    error,
                    request.language or "unknown",
                ) from error

            options = self._filter_time_window(options, question)
            options = self._filter_requested_status(options, question)
            return self._route_answer(
                request,
                options,
                departure,
                arrival,
                travel_date,
            )

        flight_number = f"KQ{match.group(1).upper()}"
        travel_date = self._travel_date(question)
        if travel_date is None:
            return self._date_clarification(request, flight_number)

        try:
            information = self.lookup(
                flight_number,
                travel_date,
                force_refresh=force_refresh,
            )
        except OAGFlightError as error:
            raise self._localized_error(
                error,
                request.language or "unknown",
            ) from error
        language = request.language or "unknown"
        return GroundedAnswerResponse(
            answer=self._format_answer(information, language),
            language=language,
            decision="flight_information",
            generation_called=False,
            sources=[],
            flight_info=information,
            flight_options=[],
        )

    @staticmethod
    def _unsupported_airline(
        request: GroundedAnswerRequest,
    ) -> GroundedAnswerResponse:
        """Decline non-KQ operational lookups without retrieval or an OAG call."""

        language = request.language or "unknown"
        messages = {
            "en": "I can currently look up flight information for Kenya Airways flights (KQ) only.",
            "sw": "Kwa sasa ninaweza kuangalia taarifa za ndege za Kenya Airways (KQ) pekee.",
            "mixed": "Kwa sasa ninaweza ku-check flight information ya Kenya Airways flights (KQ) pekee.",
            "unknown": "I can currently look up flight information for Kenya Airways flights (KQ) only.",
        }
        return GroundedAnswerResponse(
            answer=messages[language],
            language=language,
            decision="fallback",
            generation_called=False,
            sources=[],
        )

    def lookup(
        self,
        flight_number: str,
        travel_date: date,
        *,
        force_refresh: bool = False,
    ) -> FlightInformation:
        """Look up one KQ flight and return the first unambiguous matching instance."""

        normalized = self.normalize_flight_number(flight_number)
        cache_key = f"{normalized}:{travel_date.isoformat()}"
        if not force_refresh:
            cached = self._cached(cache_key)
            if cached is not None:
                return cached
        else:
            # A manual refresh bypasses normal caching, but repeated clicks within
            # the cooldown reuse the last value to protect limited OAG quotas.
            last_request = self._last_provider_request.get(cache_key)
            if (
                last_request is not None
                and monotonic() - last_request
                < self.settings.oag_refresh_cooldown_seconds
            ):
                cached = self._cached(cache_key)
                if cached is not None:
                    return cached

        if not self.settings.oag_api_key:
            raise OAGFlightError(
                503,
                "Flight information is not configured. Please try Kenya Airways' official flight information channels.",
                "configuration_missing",
            )

        self._last_provider_request[cache_key] = monotonic()
        payload = self._request({
            "CarrierCode": "KQ",
            # OAG expects only the numeric part of the designator here.
            "FlightNumber": re.match(
                r"KQ([0-9]{1,4})",
                normalized,
            ).group(1),
            "DepartureDateTime": travel_date.isoformat(),
            "CodeType": "IATA",
            "Content": "Status",
            "FlightType": "Scheduled",
            "Limit": "10",
            "version": "v2",
        })
        records = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise OAGFlightError(
                502,
                "OAG returned an unexpected flight-information response.",
                "malformed_response",
            )
        matches = [record for record in records if self._matches(record, normalized)]
        if not matches:
            raise OAGFlightError(
                404,
                f"No OAG flight information was found for {normalized} on {travel_date.isoformat()}.",
                "not_found",
            )
        if len(matches) > 1:
            raise OAGFlightError(
                409,
                "More than one flight instance matched. Please include the departure airport or time.",
                "ambiguous",
            )

        information = self._map(matches[0], normalized, travel_date)
        with self._cache_lock:
            self._cache[cache_key] = (
                monotonic() + self.settings.oag_status_cache_seconds,
                information,
            )
        return information

    def lookup_route(
        self,
        travel_date: date,
        *,
        departure: AirportSelection | None = None,
        arrival: AirportSelection | None = None,
    ) -> list[FlightInformation]:
        """Return every KQ flight matching supported airport/date filters."""

        if departure is None and arrival is None:
            raise OAGFlightError(
                400,
                "Enter a departure airport, destination, or route.",
                "invalid_route",
            )
        if not self.settings.oag_api_key:
            raise OAGFlightError(
                503,
                "Flight information is not configured. Please try Kenya Airways' official flight information channels.",
                "configuration_missing",
            )

        departure_codes = ",".join(departure.codes) if departure else ""
        arrival_codes = ",".join(arrival.codes) if arrival else ""
        cache_key = ":".join((
            "route",
            travel_date.isoformat(),
            departure_codes,
            arrival_codes,
        ))
        cached = self._cached_route(cache_key)
        if cached is not None:
            return cached

        params = {
            "CarrierCode": "KQ",
            "CodeType": "IATA",
            "Content": "Status",
            "FlightType": "Scheduled",
            # One bounded provider page avoids quota-heavy automatic paging.
            "Limit": "100",
            "version": "v2",
        }
        # OAG applies airport-local dates at the corresponding end of a flight.
        # A destination-only enquiry therefore needs ArrivalDateTime; otherwise
        # a passenger asking for tomorrow's arrivals can receive a rejected or
        # semantically incorrect departure-date query.
        date_filter = (
            "ArrivalDateTime"
            if departure is None and arrival is not None
            else "DepartureDateTime"
        )
        params[date_filter] = travel_date.isoformat()
        if departure_codes:
            params["DepartureAirport"] = departure_codes
        if arrival_codes:
            params["ArrivalAirport"] = arrival_codes

        self._last_provider_request[cache_key] = monotonic()
        payload = self._request(params)
        records = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise OAGFlightError(
                502,
                "OAG returned an unexpected flight-information response.",
                "malformed_response",
            )

        information: list[FlightInformation] = []
        seen: set[tuple[str, str | None, str | None, str | None]] = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            carrier = record.get("carrier") or {}
            carrier_code = str(carrier.get("iata") or "").upper()
            number = str(record.get("flightNumber") or "").strip()
            if carrier_code not in {"", "KQ"} or not number:
                continue

            mapped = self._map(
                record,
                f"KQ{number}",
                travel_date,
            )
            identity = (
                mapped.flight_number,
                mapped.departure.code,
                mapped.arrival.code,
                mapped.scheduled_departure,
            )
            if identity not in seen:
                seen.add(identity)
                information.append(mapped)

        information.sort(
            key=lambda item: (
                item.scheduled_departure or "",
                item.flight_number,
            ),
        )
        with self._cache_lock:
            self._route_cache[cache_key] = (
                monotonic() + self.settings.oag_status_cache_seconds,
                information,
            )
        return information

    def _request(self, params: dict[str, str]) -> dict:
        headers = {
            OAG_SUBSCRIPTION_HEADER: self.settings.oag_api_key,
        }
        url = f"{self.settings.oag_base_url.rstrip('/')}/flight-instances/"
        request_id = uuid4().hex

        for attempt in range(2):
            attempt_number = attempt + 1
            try:
                with self.client_factory(
                    timeout=self.settings.oag_timeout_seconds,
                ) as client:
                    response = client.get(url, params=params, headers=headers)
            except httpx.TimeoutException as error:
                self._record_attempt(
                    request_id,
                    attempt_number,
                    "timeout",
                )
                if attempt == 0:
                    self.sleeper(0.5)
                    continue
                raise OAGFlightError(
                    504,
                    "Flight information timed out. Please try again later.",
                    "timeout",
                ) from error
            except httpx.RequestError as error:
                self._record_attempt(
                    request_id,
                    attempt_number,
                    "network_error",
                )
                raise OAGFlightError(
                    503,
                    "Flight information is temporarily unavailable. Please try again later.",
                    "network_error",
                ) from error

            self._record_attempt(
                request_id,
                attempt_number,
                "success" if response.status_code == 200 else "http_error",
                response.status_code,
            )
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as error:
                    raise OAGFlightError(
                        502,
                        "OAG returned unreadable flight information.",
                        "malformed_response",
                    ) from error
            if response.status_code in {401, 403}:
                raise OAGFlightError(
                    503,
                    "Flight information authentication is unavailable.",
                    "authentication_error",
                )
            if response.status_code == 429:
                raise OAGFlightError(
                    429,
                    "The flight-information request limit has been reached. Please try again later.",
                    "rate_limited",
                )
            if response.status_code >= 500 and attempt == 0:
                self.sleeper(0.5)
                continue
            if response.status_code == 400:
                logger.warning(
                    "OAG rejected validated flight filters: %s",
                    self._provider_error_summary(response),
                )
                raise OAGFlightError(
                    502,
                    "OAG rejected the flight-search filters. Please try a specific departure airport or KQ flight number.",
                    "invalid_provider_filters",
                )
            raise OAGFlightError(
                503 if response.status_code >= 500 else 400,
                "OAG could not complete the flight-information request.",
                "provider_error",
            )
        raise OAGFlightError(503, "Flight information is unavailable.", "provider_error")

    @staticmethod
    def _provider_error_summary(response: httpx.Response) -> str:
        """Log OAG validation fields without headers, credentials, or request URLs."""

        try:
            payload = response.json()
        except ValueError:
            return f"HTTP {response.status_code}; unreadable response body"
        if not isinstance(payload, dict):
            return f"HTTP {response.status_code}; unexpected response body"

        safe = {
            key: payload.get(key)
            for key in ("title", "status", "errors", "type", "traceId")
            if payload.get(key) is not None
        }
        # Provider messages can be unexpectedly verbose. Keep logs useful and
        # bounded while never including outbound headers or the subscription key.
        return f"HTTP {response.status_code}; {str(safe)[:1000]}"

    def _record_attempt(
        self,
        request_id: str,
        attempt_number: int,
        outcome: str,
        http_status: int | None = None,
    ) -> None:
        if self.usage_recorder is None:
            return
        try:
            self.usage_recorder(
                request_id=request_id,
                attempt_number=attempt_number,
                outcome=outcome,
                http_status=http_status,
            )
        except Exception:
            # Usage telemetry must never turn a valid provider response into a
            # passenger-facing failure.
            logger.warning("Could not persist OAG API usage telemetry.")

    def _cached(self, key: str) -> FlightInformation | None:
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            expires_at, information = cached
            if expires_at <= monotonic():
                self._cache.pop(key, None)
                return None
            return information.model_copy(update={"cached": True})

    def _cached_route(self, key: str) -> list[FlightInformation] | None:
        """Return copied route results so cache metadata cannot mutate storage."""

        with self._cache_lock:
            cached = self._route_cache.get(key)
            if cached is None:
                return None
            expires_at, information = cached
            if expires_at <= monotonic():
                self._route_cache.pop(key, None)
                return None
            return [
                item.model_copy(update={"cached": True})
                for item in information
            ]

    @staticmethod
    def normalize_flight_number(value: str) -> str:
        match = re.fullmatch(
            r"\s*KQ\s*-?\s*([0-9]{1,4}[A-Z]?)\s*",
            value,
            re.IGNORECASE,
        )
        if match is None:
            raise OAGFlightError(400, "Enter a Kenya Airways flight number such as KQ100.", "invalid_flight")
        return f"KQ{match.group(1).upper()}"

    @staticmethod
    def _travel_date(question: str) -> date | None:
        today = datetime.now(NAIROBI_TIMEZONE).date()
        lowered = question.lower()
        if re.search(r"\b(today|leo)\b", lowered):
            return today
        if re.search(r"\b(tomorrow|kesho)\b", lowered):
            return today + timedelta(days=1)
        match = ISO_DATE.search(question)
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                return None
        display_match = DISPLAY_DATE.search(question)
        if display_match:
            try:
                return datetime.strptime(
                    display_match.group(1),
                    "%d-%m-%Y",
                ).date()
            except ValueError:
                return None
        return None

    @staticmethod
    def _matches(record: object, flight_number: str) -> bool:
        if not isinstance(record, dict):
            return False
        actual_number = str(
            record.get("flightNumber")
            or record.get("FlightNumber")
            or ""
        ).strip()
        carrier = record.get("carrier") or {}
        actual_carrier = str(
            carrier.get("iata")
            or record.get("AirlineIata")
            or ""
        ).upper()
        return (
            actual_number == flight_number.removeprefix("KQ")
            and actual_carrier in {"", "KQ"}
        )

    @classmethod
    def _map(cls, record: dict, flight_number: str, travel_date: date) -> FlightInformation:
        departure_record = record.get("departure") or {}
        arrival_record = record.get("arrival") or {}
        latest_status = cls._latest_status(record)
        status_departure = latest_status.get("departure") or {}
        status_arrival = latest_status.get("arrival") or {}
        operating_flight = (
            (record.get("codeshare") or {}).get("operatingFlight")
            or {}
        )
        carrier = record.get("carrier") or {}
        departure_code = cls._airport_code(departure_record)
        departure_metadata = airport_metadata(departure_code)
        departure = FlightAirport(
            code=departure_code,
            country_code=(
                cls._country_code(departure_record)
                or getattr(departure_metadata, "country_code", None)
            ),
            name=(
                cls._airport_name(departure_record)
                or getattr(departure_metadata, "name", None)
            ),
            city=(
                cls._city_name(departure_record)
                or getattr(departure_metadata, "city", None)
            ),
            country=(
                cls._country_name(departure_record)
                or getattr(departure_metadata, "country", None)
            ),
            terminal=(
                cls._nested_text(status_departure, "actualTerminal")
                or cls._nested_text(departure_record, "terminal")
            ),
            gate=cls._nested_text(status_departure, "gate"),
        )
        arrival_code = cls._airport_code(arrival_record)
        arrival_metadata = airport_metadata(arrival_code)
        arrival = FlightAirport(
            code=arrival_code,
            country_code=(
                cls._country_code(arrival_record)
                or getattr(arrival_metadata, "country_code", None)
            ),
            name=(
                cls._airport_name(arrival_record)
                or getattr(arrival_metadata, "name", None)
            ),
            city=(
                cls._city_name(arrival_record)
                or getattr(arrival_metadata, "city", None)
            ),
            country=(
                cls._country_name(arrival_record)
                or getattr(arrival_metadata, "country", None)
            ),
            terminal=(
                cls._nested_text(status_arrival, "actualTerminal")
                or cls._nested_text(arrival_record, "terminal")
            ),
            gate=cls._nested_text(status_arrival, "gate"),
        )
        estimated_departure = cls._status_time(
            status_departure,
            "estimatedTime",
            "outGate",
            "offGround",
        )
        estimated_arrival = cls._status_time(
            status_arrival,
            "estimatedTime",
            "inGate",
            "onGround",
        )
        actual_departure = cls._status_time(
            status_departure,
            "actualTime",
            "outGate",
            "offGround",
        )
        actual_arrival = cls._status_time(
            status_arrival,
            "actualTime",
            "inGate",
            "onGround",
        )
        actual_takeoff = cls._status_time(
            status_departure,
            "actualTime",
            "offGround",
            "outGate",
        )
        actual_landing = cls._status_time(
            status_arrival,
            "actualTime",
            "onGround",
            "inGate",
        )
        departure_actual = status_departure.get("actualTime") or {}
        arrival_actual = status_arrival.get("actualTime") or {}
        departure_delay = cls._variation_minutes(
            departure_actual.get("outGateVariation"),
        )
        arrival_delay = cls._variation_minutes(
            arrival_actual.get("inGateVariation"),
        )
        return FlightInformation(
            flight_number=flight_number,
            travel_date=travel_date,
            status=cls._nested_text(latest_status, "state"),
            operating_carrier=cls._nested_text(
                operating_flight,
                "code",
            ) or cls._nested_text(carrier, "iata"),
            marketing_carrier=cls._nested_text(carrier, "iata"),
            departure=departure,
            arrival=arrival,
            scheduled_departure=cls._scheduled_time(departure_record),
            scheduled_arrival=cls._scheduled_time(arrival_record),
            estimated_departure=estimated_departure,
            estimated_arrival=estimated_arrival,
            actual_departure=actual_departure,
            actual_arrival=actual_arrival,
            actual_takeoff=actual_takeoff,
            actual_landing=actual_landing,
            # Retain the legacy field for existing clients while exposing the
            # two operationally distinct values to the updated passenger card.
            delay_minutes=departure_delay,
            departure_delay_minutes=departure_delay,
            arrival_delay_minutes=arrival_delay,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _text(record: dict, key: str) -> str | None:
        value = record.get(key)
        return str(value).strip() if value is not None and str(value).strip() else None

    @staticmethod
    def _nested_text(record: object, key: str) -> str | None:
        if not isinstance(record, dict):
            return None
        value = record.get(key)
        return str(value).strip() if value is not None and str(value).strip() else None

    @classmethod
    def _airport_code(cls, movement: dict) -> str | None:
        airport = movement.get("airport") or {}
        return (
            cls._nested_text(airport, "iata")
            or cls._nested_text(airport, "icao")
            or cls._nested_text(airport, "faa")
        )

    @classmethod
    def _country_code(cls, movement: dict) -> str | None:
        country = movement.get("country") or {}
        return cls._nested_text(country, "code")

    @classmethod
    def _airport_name(cls, movement: dict) -> str | None:
        airport = movement.get("airport") or {}
        return (
            cls._nested_text(airport, "name")
            or cls._nested_text(movement, "airportName")
        )

    @classmethod
    def _city_name(cls, movement: dict) -> str | None:
        city = movement.get("city") or {}
        return (
            cls._nested_text(city, "name")
            or cls._nested_text(movement, "cityName")
        )

    @classmethod
    def _country_name(cls, movement: dict) -> str | None:
        country = movement.get("country") or {}
        return (
            cls._nested_text(country, "name")
            or cls._nested_text(movement, "countryName")
        )

    @staticmethod
    def _latest_status(record: dict) -> dict:
        details = record.get("statusDetails")
        if not isinstance(details, list):
            return {}
        valid = [item for item in details if isinstance(item, dict)]
        return max(
            valid,
            key=lambda item: str(item.get("updatedAt") or ""),
            default={},
        )

    @classmethod
    def _scheduled_time(cls, movement: dict) -> str | None:
        date_value = movement.get("date") or {}
        time_value = movement.get("time") or {}
        utc_date = cls._nested_text(date_value, "utc")
        utc_time = cls._nested_text(time_value, "utc")
        if utc_date and utc_time:
            return cls._utc_to_eat(f"{utc_date}T{utc_time}")

        local_date = cls._nested_text(date_value, "local")
        local_time = cls._nested_text(time_value, "local")
        if local_date and local_time:
            return f"{local_date}T{local_time}"
        return local_date or local_time

    @classmethod
    def _status_time(
        cls,
        movement: dict,
        group: str,
        primary: str,
        secondary: str,
    ) -> str | None:
        values = movement.get(group) or {}
        for key in (primary, secondary):
            value = values.get(key)
            if isinstance(value, dict):
                # Normalize both airports through OAG's UTC timestamp so every
                # displayed value uses the same East Africa Time reference.
                utc_value = cls._nested_text(value, "utc")
                if utc_value:
                    return cls._utc_to_eat(utc_value)
                local_value = cls._nested_text(value, "local")
                if local_value:
                    return cls._offset_time_to_eat(local_value)
            elif value is not None and str(value).strip():
                return str(value).strip()
        return None

    @staticmethod
    def _utc_to_eat(value: str) -> str | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(
                NAIROBI_TIMEZONE,
            ).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _offset_time_to_eat(value: str) -> str | None:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return None
            return parsed.astimezone(
                NAIROBI_TIMEZONE,
            ).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _delay_minutes(scheduled: str | None, current: str | None) -> int | None:
        if not scheduled or not current:
            return None
        try:
            # Both values are airport-local. Remove any explicit offset before
            # comparing their wall-clock times to avoid silent zone conversion.
            scheduled_time = datetime.fromisoformat(scheduled).replace(
                tzinfo=None,
            )
            current_time = datetime.fromisoformat(current).replace(
                tzinfo=None,
            )
        except ValueError:
            return None
        return max(0, round((current_time - scheduled_time).total_seconds() / 60))

    @staticmethod
    def _variation_minutes(value: object) -> int | None:
        """Convert OAG's signed HH:MM:SS variation without guessing a delay."""

        if not isinstance(value, str):
            return None
        match = re.fullmatch(
            r"([+-]?)([0-9]{2}):([0-9]{2}):([0-9]{2})",
            value.strip(),
        )
        if match is None:
            return None
        sign = -1 if match.group(1) == "-" else 1
        minutes = (
            int(match.group(2)) * 60
            + int(match.group(3))
            + round(int(match.group(4)) / 60)
        )
        return sign * minutes

    @staticmethod
    def _format_answer(information: FlightInformation, language: str) -> str:
        status = re.sub(
            r"[^a-z]",
            "",
            (information.status or "").lower(),
        )
        departure = OAGFlightService._display_airport(
            information.departure,
        )
        arrival = OAGFlightService._display_airport(
            information.arrival,
        )
        arrived_at_gate = bool(
            information.actual_arrival
            and status == "ingate"
        )

        if language == "sw":
            return OAGFlightService._swahili_status_answer(
                information.flight_number,
                status,
                departure,
                arrival,
                arrived_at_gate,
            )
        if language == "mixed":
            return OAGFlightService._mixed_status_answer(
                information.flight_number,
                status,
                departure,
                arrival,
                arrived_at_gate,
            )
        return OAGFlightService._english_status_answer(
            information.flight_number,
            status,
            departure,
            arrival,
            arrived_at_gate,
        )

    @staticmethod
    def _display_airport(airport: FlightAirport) -> str | None:
        if airport.name and airport.code:
            identity = f"{airport.name} ({airport.code})"
        else:
            identity = airport.name or airport.code
        if not identity:
            return None
        return ", ".join(
            value
            for value in (identity, airport.city, airport.country)
            if value
        )

    @staticmethod
    def _english_status_answer(
        flight_number: str,
        status: str,
        departure: str | None,
        arrival: str | None,
        arrived_at_gate: bool,
    ) -> str:
        suffix = "See the flight details below."
        if status == "ingate":
            if arrived_at_gate and arrival:
                lead = f"has arrived at its gate at {arrival}"
            else:
                lead = "is currently at a gate"
        elif status in {"inflight", "enroute"}:
            if departure and arrival:
                lead = f"is currently flying from {departure} to {arrival}"
            else:
                lead = "is currently in flight"
        elif status == "scheduled":
            lead = (
                f"is scheduled to depart from {departure}"
                if departure
                else "is scheduled"
            )
        elif status == "delayed":
            return (
                f"Kenya Airways flight {flight_number} is delayed. "
                "See the updated flight details below."
            )
        elif status in {"cancelled", "canceled"}:
            lead = "has been cancelled"
        elif status == "landed":
            lead = f"has landed at {arrival}" if arrival else "has landed"
        elif status == "departed":
            lead = (
                f"has departed from {departure}"
                if departure
                else "has departed"
            )
        else:
            lead = "has updated flight information available"
        return (
            f"Kenya Airways flight {flight_number} "
            f"{lead}. {suffix}"
        )

    @staticmethod
    def _swahili_status_answer(
        flight_number: str,
        status: str,
        departure: str | None,
        arrival: str | None,
        arrived_at_gate: bool,
    ) -> str:
        suffix = "Tazama maelezo ya safari hapa chini."
        if status == "ingate":
            if arrived_at_gate and arrival:
                lead = (
                    "imefika kwenye lango lake katika uwanja wa ndege wa "
                    f"{arrival}"
                )
            else:
                lead = "iko kwenye lango kwa sasa"
        elif status in {"inflight", "enroute"}:
            if departure and arrival:
                lead = f"iko safarini kutoka {departure} kwenda {arrival}"
            else:
                lead = "iko angani kwa sasa"
        elif status == "scheduled":
            lead = (
                f"imepangwa kuondoka {departure}"
                if departure
                else "imepangwa kuondoka"
            )
        elif status == "delayed":
            lead = "imechelewa"
        elif status in {"cancelled", "canceled"}:
            lead = "imefutwa"
        elif status == "landed":
            lead = f"imetua {arrival}" if arrival else "imetua"
        elif status == "departed":
            lead = (
                f"imeondoka {departure}"
                if departure
                else "imeondoka"
            )
        else:
            lead = "ina taarifa mpya za safari"
        return (
            f"Ndege ya Kenya Airways {flight_number} "
            f"{lead}. {suffix}"
        )

    @staticmethod
    def _mixed_status_answer(
        flight_number: str,
        status: str,
        departure: str | None,
        arrival: str | None,
        arrived_at_gate: bool,
    ) -> str:
        english = OAGFlightService._english_status_answer(
            flight_number,
            status,
            departure,
            arrival,
            arrived_at_gate,
        )
        return english.replace(
            "See the flight details below.",
            "Tazama flight details hapa chini.",
        ).replace(
            "See the updated flight details below.",
            "Tazama updated flight details hapa chini.",
        )

    def _date_clarification(
        self,
        request: GroundedAnswerRequest,
        flight_number: str,
    ) -> GroundedAnswerResponse:
        language = request.language or "unknown"
        answer = {
            "sw": f"Ungependa kuangalia {flight_number} kwa tarehe gani? Taja leo, kesho, au tarehe kama 10-11-2026.",
            "mixed": f"Unataka status ya {flight_number} ya tarehe gani? Use leo, kesho, or DD-MM-YYYY.",
        }.get(language, f"Which travel date should I use for {flight_number}? Enter today, tomorrow, or DD-MM-YYYY.")
        return GroundedAnswerResponse(
            answer=answer,
            language=language,
            decision="clarification_required",
            generation_called=False,
            sources=[],
        )

    @staticmethod
    def _location_clarification(
        request: GroundedAnswerRequest,
    ) -> GroundedAnswerResponse:
        """Ask only for a location that the deterministic catalogue could not resolve."""

        language = request.language or "unknown"
        message = {
            "sw": "Tafadhali taja mji au msimbo wa uwanja wa ndege wa kuondoka na unakoenda.",
            "mixed": "Please taja departure na destination city au airport code.",
        }.get(
            language,
            "Please provide the departure or destination city, or its three-letter airport code.",
        )
        return GroundedAnswerResponse(
            answer=message,
            language=language,
            decision="clarification_required",
            generation_called=False,
            sources=[],
        )

    @staticmethod
    def _route_date_clarification(
        request: GroundedAnswerRequest,
        departure: AirportSelection | None,
        arrival: AirportSelection | None,
    ) -> GroundedAnswerResponse:
        """Retain recognized locations and request only the missing date."""

        language = request.language or "unknown"
        route = " to ".join(
            selection.label
            for selection in (departure, arrival)
            if selection is not None
        )
        message = {
            "sw": f"Ungependa kuangalia ndege za {route} kwa tarehe gani? Taja leo, kesho, au DD-MM-YYYY.",
            "mixed": f"Unataka KQ flights za {route} tarehe gani? Use leo, kesho, or DD-MM-YYYY.",
        }.get(
            language,
            f"Which travel date should I use for Kenya Airways flights {route}? Enter today, tomorrow, or DD-MM-YYYY.",
        )
        return GroundedAnswerResponse(
            answer=message,
            language=language,
            decision="clarification_required",
            generation_called=False,
            sources=[],
        )

    @staticmethod
    def _route_subscription_limitation(
        request: GroundedAnswerRequest,
    ) -> GroundedAnswerResponse:
        """Do not spend quota until route search is confirmed for this key."""

        language = request.language or "unknown"
        message = {
            "sw": (
                "Utafutaji wa ndege kwa njia au unakoenda haujawezeshwa kwa usajili huu wa OAG. "
                "Tafadhali taja nambari ya ndege ya KQ na tarehe."
            ),
            "mixed": (
                "Route search haija-enable kwa OAG subscription hii. "
                "Please provide KQ flight number na date."
            ),
        }.get(
            language,
            "Route and destination search is not enabled for this OAG subscription. Please provide a KQ flight number and date.",
        )
        return GroundedAnswerResponse(
            answer=message,
            language=language,
            decision="clarification_required",
            generation_called=False,
            sources=[],
        )

    @staticmethod
    def _route_provider_limitation(
        request: GroundedAnswerRequest,
    ) -> GroundedAnswerResponse:
        """Explain a provider route-filter rejection without returning 502."""

        language = request.language or "unknown"
        message = {
            "sw": (
                "OAG haikuruhusu utafutaji huu kwa njia ya safari. "
                "Tafadhali taja nambari ya ndege ya KQ, kwa mfano KQ760, "
                "pamoja na tarehe."
            ),
            "mixed": (
                "OAG haija-allow route search hii. Please provide a KQ "
                "flight number, for example KQ760, pamoja na date."
            ),
        }.get(
            language,
            (
                "OAG did not allow this route search for the current "
                "subscription. Please provide a KQ flight number, for "
                "example KQ760, and the travel date."
            ),
        )
        return GroundedAnswerResponse(
            answer=message,
            language=language,
            decision="clarification_required",
            generation_called=False,
            sources=[],
        )

    @staticmethod
    def _departure_clarification(
        request: GroundedAnswerRequest,
        arrival: AirportSelection,
        travel_date: date,
    ) -> GroundedAnswerResponse:
        """Ask for the origin required by this OAG subscription."""

        language = request.language or "unknown"
        display_date = travel_date.strftime("%d-%m-%Y")
        message = {
            "sw": (
                f"Ndege ya kwenda {arrival.label} tarehe {display_date} "
                "itaondoka mji au uwanja gani wa ndege?"
            ),
            "mixed": (
                f"Flight ya kwenda {arrival.label} tarehe {display_date} "
                "ina-depart from which city au airport?"
            ),
        }.get(
            language,
            (
                f"Which city or airport is the flight to {arrival.label} "
                f"departing from on {display_date}?"
            ),
        )
        return GroundedAnswerResponse(
            answer=message,
            language=language,
            decision="clarification_required",
            generation_called=False,
            sources=[],
        )

    @staticmethod
    def _filter_time_window(
        options: list[FlightInformation],
        question: str,
    ) -> list[FlightInformation]:
        """Apply an explicit morning/afternoon/evening follow-up locally."""

        match = TIME_WINDOW.search(question)
        if match is None:
            return options
        requested = match.group(1).lower()
        window = {
            "morning": (0, 12),
            "asubuhi": (0, 12),
            "afternoon": (12, 17),
            "mchana": (12, 17),
            "evening": (17, 24),
            "jioni": (17, 24),
        }[requested]

        filtered: list[FlightInformation] = []
        for option in options:
            try:
                hour = datetime.fromisoformat(
                    option.scheduled_departure or "",
                ).hour
            except ValueError:
                continue
            if window[0] <= hour < window[1]:
                filtered.append(option)
        return filtered

    @staticmethod
    def _filter_requested_status(
        options: list[FlightInformation],
        question: str,
    ) -> list[FlightInformation]:
        """Filter only when the passenger explicitly names a flight state."""

        requested: set[str] = set()
        lowered = question.lower()
        status_terms = {
            "delayed": ("delayed", "imechelewa"),
            "cancelled": ("cancelled", "canceled", "imefutwa"),
            "landed": ("landed", "imetua"),
            "inflight": ("in flight", "in-flight", "iko angani"),
            "departed": ("departed", "imeondoka"),
        }
        for status_name, terms in status_terms.items():
            if any(term in lowered for term in terms):
                requested.add(status_name)
        if not requested:
            return options

        return [
            option
            for option in options
            if re.sub(
                r"[^a-z]",
                "",
                (option.status or "").lower(),
            ) in requested
        ]

    @staticmethod
    def _route_answer(
        request: GroundedAnswerRequest,
        options: list[FlightInformation],
        departure: AirportSelection | None,
        arrival: AirportSelection | None,
        travel_date: date,
    ) -> GroundedAnswerResponse:
        """Present one verified card or every distinct OAG match."""

        language = request.language or "unknown"
        route = " to ".join(
            selection.label
            for selection in (departure, arrival)
            if selection is not None
        )
        display_date = travel_date.strftime("%d-%m-%Y")

        if not options:
            answer = {
                "sw": f"Hakuna ndege ya Kenya Airways iliyopatikana kwa {route} tarehe {display_date}.",
                "mixed": f"No Kenya Airways flight imepatikana kwa {route} tarehe {display_date}.",
            }.get(
                language,
                f"No matching Kenya Airways flight was found for {route} on {display_date}.",
            )
            return GroundedAnswerResponse(
                answer=answer,
                language=language,
                decision="flight_information",
                generation_called=False,
                sources=[],
            )

        if len(options) == 1:
            information = options[0]
            return GroundedAnswerResponse(
                answer=OAGFlightService._format_answer(
                    information,
                    language,
                ),
                language=language,
                decision="flight_information",
                generation_called=False,
                sources=[],
                flight_info=information,
            )

        answer = {
            "sw": (
                f"Nimepata ndege {len(options)} za Kenya Airways kwa {route} tarehe {display_date}. "
                "Chagua nambari ya ndege au muda unaotaka kutoka kwenye maelezo hapa chini."
            ),
            "mixed": (
                f"Nimepata Kenya Airways flights {len(options)} kwa {route} tarehe {display_date}. "
                "Choose flight number au departure time kutoka details hapa chini."
            ),
        }.get(
            language,
            (
                f"I found {len(options)} matching Kenya Airways flights for {route} on {display_date}. "
                "Choose the flight number or departure time you want from the details below."
            ),
        )
        return GroundedAnswerResponse(
            answer=answer,
            language=language,
            decision="flight_information",
            generation_called=False,
            sources=[],
            flight_options=options,
        )

    @staticmethod
    def _localized_error(error: OAGFlightError, language: str) -> OAGFlightError:
        if language not in {"sw", "mixed"}:
            return error
        detail = (
            "Taarifa za safari za ndege hazipatikani kwa sasa. "
            "Tafadhali jaribu tena baadaye au angalia taarifa rasmi za Kenya Airways."
        )
        return OAGFlightError(
            error.status_code,
            detail,
            error.code,
        )


def get_oag_flight_service() -> OAGFlightService:
    return OAGFlightService(
        get_settings(),
        usage_recorder=get_operational_logger().oag_api_request,
    )
