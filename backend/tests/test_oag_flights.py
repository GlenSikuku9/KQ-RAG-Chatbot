"""Unit tests for OAG routing and response mapping; no live quota is used."""

import unittest
from datetime import date

import httpx

from app.config import Settings
from app.models.flight import FlightAirport, FlightInformation
from app.models.generation import GroundedAnswerRequest
from app.services.oag_flights import OAGFlightError, OAGFlightService


class FakeClient:
    def __init__(self, response=None, error=None, **_kwargs):
        self.response = response
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.response


class SequenceClient(FakeClient):
    """Return successive mocked responses so retry accounting is testable."""

    def __init__(self, responses, **kwargs):
        super().__init__(**kwargs)
        self.responses = responses

    def get(self, *_args, **_kwargs):
        return self.responses.pop(0)


class RecordingClient(FakeClient):
    """Capture request parameters while returning a mocked OAG response."""

    def __init__(self, response, requests, **kwargs):
        super().__init__(response=response, **kwargs)
        self.requests = requests

    def get(self, url, **kwargs):
        self.requests.append({
            "url": url,
            **kwargs,
        })
        return self.response


def response(status, body):
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("GET", "https://api.oag.com/flight-instances/"),
    )


class OAGFlightServiceTests(unittest.TestCase):
    def service(
        self,
        provider_response,
        *,
        usage_recorder=None,
        **settings,
    ):
        config = Settings(
            oag_api_key="test-key",
            **settings,
        )
        return OAGFlightService(
            config,
            client_factory=lambda **kwargs: FakeClient(
                provider_response,
                **kwargs,
            ),
            sleeper=lambda _seconds: None,
            usage_recorder=usage_recorder,
        )

    def setUp(self):
        OAGFlightService._cache.clear()
        OAGFlightService._route_cache.clear()

    @staticmethod
    def flight_information(
        status,
        *,
        actual_arrival="2026-11-10T18:38:00+03:00",
        names=True,
    ):
        return FlightInformation(
            flight_number="KQ100",
            travel_date=date(2026, 11, 10),
            status=status,
            departure=FlightAirport(
                code="NBO",
                name="Jomo Kenyatta International Airport" if names else None,
            ),
            arrival=FlightAirport(
                code="LHR",
                name="London Heathrow" if names else None,
            ),
            actual_arrival=actual_arrival,
            retrieved_at="2026-11-10T17:38:00+00:00",
        )

    def test_normalizes_supported_flight_variations(self):
        for value in ("KQ100", "KQ 100", "KQ-100", "kq100"):
            with self.subTest(value=value):
                self.assertEqual(
                    OAGFlightService.normalize_flight_number(value),
                    "KQ100",
                )

    def test_english_swahili_and_mixed_queries_route_to_oag(self):
        service = self.service(response(200, {
            "data": [{
                "flightNumber": 100,
                "carrier": {"iata": "KQ"},
                "departure": {"airport": {"iata": "NBO"}},
                "arrival": {"airport": {"iata": "LHR"}},
                "statusDetails": [{"state": "Scheduled"}],
            }],
        }))
        questions = (
            ("Track KQ100 today", "en"),
            ("Nifuatilie ndege KQ100 leo", "sw"),
            ("Nipe status ya KQ100 leo", "mixed"),
        )
        for question, language in questions:
            with self.subTest(language=language):
                result = service.answer_if_flight_query(
                    GroundedAnswerRequest(
                        question=question,
                        language=language,
                    ),
                )
                self.assertEqual(result.decision, "flight_information")
                self.assertEqual(result.flight_info.flight_number, "KQ100")

    def test_equivalent_kenya_airways_flight_references_route_to_oag(self):
        payload = {
            "data": [{
                "flightNumber": 100,
                "carrier": {"iata": "KQ"},
                "departure": {"airport": {"iata": "NBO"}},
                "arrival": {"airport": {"iata": "LHR"}},
            }],
        }
        for reference in (
            "KQ-100",
            "Kenya Airways flight 100",
            "Kenya Airways 100",
            "Flight 100",
        ):
            with self.subTest(reference=reference):
                service = self.service(response(200, payload))
                result = service.answer_if_flight_query(
                    GroundedAnswerRequest(
                        question=f"What is the status of {reference} today?",
                        language="en",
                    ),
                )
                self.assertEqual(result.decision, "flight_information")
                self.assertEqual(result.flight_info.flight_number, "KQ100")

    def test_other_airline_status_lookup_is_declined_without_oag_request(self):
        provider = response(500, {})
        service = self.service(provider)
        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="What is the status of BA100 today?",
                language="en",
            ),
        )
        self.assertEqual(result.decision, "fallback")
        self.assertIn("Kenya Airways flights (KQ) only", result.answer)

    def test_missing_date_returns_clarification_without_provider_call(self):
        service = self.service(response(500, {}))
        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="Track KQ100",
                language="en",
            ),
        )
        self.assertEqual(result.decision, "clarification_required")
        self.assertIn("DD-MM-YYYY", result.answer)

    def test_day_month_year_date_is_accepted_for_passengers(self):
        service = self.service(response(200, {
            "data": [{
                "flightNumber": 100,
                "carrier": {"iata": "KQ"},
                "departure": {"airport": {"iata": "NBO"}},
                "arrival": {"airport": {"iata": "LHR"}},
            }],
        }))
        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="Track KQ100 on 10-11-2026",
                language="en",
            ),
        )
        self.assertEqual(
            result.flight_info.travel_date,
            date(2026, 11, 10),
        )

    def test_policy_question_stays_on_rag_route(self):
        service = self.service(response(500, {}))
        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="Can I get a refund if KQ100 is cancelled?",
                language="en",
            ),
        )
        self.assertIsNone(result)

    def test_status_introductions_are_natural_and_location_aware(self):
        expected_phrases = {
            "InGate": "has arrived at its gate at London Heathrow (LHR)",
            "InFlight": "is currently flying from Jomo Kenyatta International Airport (NBO) to London Heathrow (LHR)",
            "Scheduled": "is scheduled to depart from Jomo Kenyatta International Airport (NBO)",
            "Delayed": "is delayed",
            "Cancelled": "has been cancelled",
            "Landed": "has landed at London Heathrow (LHR)",
            "Departed": "has departed from Jomo Kenyatta International Airport (NBO)",
        }
        for status, phrase in expected_phrases.items():
            with self.subTest(status=status):
                answer = OAGFlightService._format_answer(
                    self.flight_information(status),
                    "en",
                )
                self.assertIn(phrase, answer)
                self.assertTrue(
                    answer.startswith("Kenya Airways flight KQ100"),
                )
                self.assertTrue(answer.endswith("details below."))

    def test_ingate_unknown_location_and_multilingual_introductions(self):
        neutral = self.flight_information(
            "InGate",
            actual_arrival=None,
            names=False,
        )
        english = OAGFlightService._format_answer(neutral, "en")
        self.assertIn("is currently at a gate", english)

        swahili = OAGFlightService._format_answer(
            self.flight_information("InGate"),
            "sw",
        )
        self.assertIn("imefika kwenye lango lake", swahili)
        self.assertIn("London Heathrow (LHR)", swahili)
        self.assertTrue(
            swahili.startswith("Ndege ya Kenya Airways KQ100"),
        )

        mixed = OAGFlightService._format_answer(
            self.flight_information("InFlight"),
            "mixed",
        )
        self.assertIn("Kenya Airways flight KQ100", mixed)
        self.assertIn("Tazama flight details hapa chini.", mixed)

        missing_codes = self.flight_information("Landed", names=False)
        missing_codes.arrival.code = None
        answer = OAGFlightService._format_answer(missing_codes, "en")
        self.assertEqual(
            answer,
            "Kenya Airways flight KQ100 has landed. See the flight details below.",
        )

    def test_maps_only_fields_present_in_provider_response(self):
        service = self.service(response(200, {
            "data": [{
                "flightNumber": 310,
                "carrier": {"iata": "KQ"},
                "departure": {
                    "airport": {"iata": "NBO"},
                    "country": {"code": "KE"},
                    "date": {"local": "2026-11-10"},
                    "time": {"local": "08:00:00"},
                },
                "arrival": {
                    "airport": {"iata": "MBA"},
                    "country": {"code": "KE"},
                },
                "statusDetails": [{
                    "state": "Delayed",
                    "updatedAt": "2026-11-10T05:10:00Z",
                    "departure": {
                        "estimatedTime": {
                            "outGate": {
                                "local": "2026-11-10T08:30:00+03:00",
                                "utc": "2026-11-10T05:30:00+00:00",
                            },
                        },
                        "actualTime": {
                            "outGateVariation": "00:00:00",
                            "outGate": {
                                "local": "2026-11-10T08:00:00+03:00",
                            },
                            "offGround": {
                                "local": "2026-11-10T08:13:00+03:00",
                            },
                        },
                    },
                    "arrival": {
                        "actualTime": {
                            "inGateVariation": "00:08:00",
                            "onGround": {
                                "local": "2026-11-10T09:55:00+03:00",
                            },
                            "inGate": {
                                "local": "2026-11-10T10:08:00+03:00",
                            },
                        },
                    },
                }],
            }],
        }))
        result = service.lookup("KQ310", date(2026, 11, 10))
        self.assertEqual(result.status, "Delayed")
        self.assertEqual(result.delay_minutes, 0)
        self.assertEqual(
            result.estimated_departure,
            "2026-11-10T08:30:00+03:00",
        )
        self.assertEqual(result.departure_delay_minutes, 0)
        self.assertEqual(result.arrival_delay_minutes, 8)
        self.assertEqual(
            result.actual_takeoff,
            "2026-11-10T08:13:00+03:00",
        )
        self.assertEqual(
            result.actual_landing,
            "2026-11-10T09:55:00+03:00",
        )
        self.assertIsNone(result.departure.gate)
        self.assertEqual(result.departure.country_code, "KE")
        self.assertEqual(
            result.departure.name,
            "Jomo Kenyatta International Airport",
        )
        self.assertEqual(result.departure.city, "Nairobi")
        self.assertEqual(result.departure.country, "Kenya")
        self.assertEqual(result.arrival.name, "Moi International Airport")
        self.assertEqual(result.arrival.city, "Mombasa")
        self.assertEqual(
            result.model_dump()["travel_date"],
            "2026-11-10",
        )

    def test_flight_answer_uses_full_verified_airport_locations(self):
        information = FlightInformation(
            flight_number="KQ760",
            travel_date=date(2026, 11, 10),
            status="InFlight",
            departure=FlightAirport(
                code="NBO",
                name="Jomo Kenyatta International Airport",
                city="Nairobi",
                country="Kenya",
            ),
            arrival=FlightAirport(
                code="JNB",
                name="O. R. Tambo International Airport",
                city="Johannesburg",
                country="South Africa",
            ),
            retrieved_at="2026-11-10T17:38:00+00:00",
        )

        english = OAGFlightService._format_answer(information, "en")
        swahili = OAGFlightService._format_answer(information, "sw")

        for answer in (english, swahili):
            self.assertIn(
                "Jomo Kenyatta International Airport (NBO), Nairobi, Kenya",
                answer,
            )
            self.assertIn(
                "O. R. Tambo International Airport (JNB), Johannesburg, "
                "South Africa",
                answer,
            )

    def test_unknown_airport_code_is_preserved_without_guessing(self):
        airport = FlightAirport(code="XYZ")

        self.assertEqual(OAGFlightService._display_airport(airport), "XYZ")

    def test_no_match_and_authentication_failures_are_safe(self):
        missing = self.service(response(200, {"data": []}))
        with self.assertRaises(OAGFlightError) as caught:
            missing.lookup("KQ100", date(2026, 11, 10))
        self.assertEqual(caught.exception.status_code, 404)

        unauthorized = self.service(response(401, {}))
        with self.assertRaises(OAGFlightError) as caught:
            unauthorized.lookup("KQ101", date(2026, 11, 10))
        self.assertEqual(caught.exception.code, "authentication_error")

    def test_rate_limit_and_timeout_are_not_hidden(self):
        limited = self.service(response(429, {}))
        with self.assertRaises(OAGFlightError) as caught:
            limited.lookup("KQ101", date(2026, 11, 10))
        self.assertEqual(caught.exception.status_code, 429)

        config = Settings(oag_api_key="test-key")
        timeout = OAGFlightService(
            config,
            client_factory=lambda **kwargs: FakeClient(
                error=httpx.ReadTimeout("slow"),
                **kwargs,
            ),
            sleeper=lambda _seconds: None,
        )
        with self.assertRaises(OAGFlightError) as caught:
            timeout.lookup("KQ102", date(2026, 11, 10))
        self.assertEqual(caught.exception.status_code, 504)

    def test_provider_validation_error_is_an_upstream_failure(self):
        service = self.service(response(400, {
            "title": "Invalid query parameters",
            "status": 400,
            "errors": {
                "ArrivalAirport": ["Invalid filter combination"],
            },
            "traceId": "safe-provider-trace",
        }))

        with self.assertRaises(OAGFlightError) as caught:
            service.lookup("KQ100", date(2026, 11, 10))

        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(
            caught.exception.code,
            "invalid_provider_filters",
        )

    def test_route_provider_rejection_returns_clarification_not_502(self):
        service = self.service(
            response(400, {
                "title": "Invalid query parameters",
                "status": 400,
            }),
            oag_route_search_enabled=True,
        )

        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question=(
                    "KQ flights from JKIA to Johannesburg tomorrow"
                ),
                language="en",
            ),
        )

        self.assertEqual(result.decision, "clarification_required")
        self.assertIn("KQ flight number", result.answer)

    def test_missing_api_key_fails_without_network_access(self):
        service = OAGFlightService(Settings())
        with self.assertRaises(OAGFlightError) as caught:
            service.lookup("KQ100", date(2026, 11, 10))
        self.assertEqual(caught.exception.code, "configuration_missing")

    def test_utc_times_are_normalized_to_east_africa_time(self):
        self.assertEqual(
            OAGFlightService._utc_to_eat(
                "2026-11-10T15:30:00+00:00",
            ),
            "2026-11-10T18:30:00+03:00",
        )

    def test_one_outbound_attempt_is_recorded_and_cache_hits_are_not(self):
        attempts = []
        provider_response = response(200, {
            "data": [{
                "flightNumber": 100,
                "carrier": {"iata": "KQ"},
                "departure": {"airport": {"iata": "NBO"}},
                "arrival": {"airport": {"iata": "LHR"}},
            }],
        })
        service = self.service(
            provider_response,
            usage_recorder=lambda **values: attempts.append(values),
        )

        service.lookup("KQ100", date(2026, 11, 10))
        cached = service.lookup("KQ100", date(2026, 11, 10))

        self.assertTrue(cached.cached)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["attempt_number"], 1)
        self.assertEqual(attempts[0]["outcome"], "success")
        self.assertEqual(attempts[0]["http_status"], 200)

    def test_each_retry_is_counted_as_an_outbound_attempt(self):
        attempts = []
        responses = [
            response(500, {}),
            response(200, {
                "data": [{
                    "flightNumber": 100,
                    "carrier": {"iata": "KQ"},
                    "departure": {"airport": {"iata": "NBO"}},
                    "arrival": {"airport": {"iata": "LHR"}},
                }],
            }),
        ]
        service = OAGFlightService(
            Settings(oag_api_key="test-key"),
            client_factory=lambda **kwargs: SequenceClient(
                responses,
                **kwargs,
            ),
            sleeper=lambda _seconds: None,
            usage_recorder=lambda **values: attempts.append(values),
        )

        service.lookup("KQ100", date(2026, 11, 10))

        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            [item["attempt_number"] for item in attempts],
            [1, 2],
        )
        self.assertEqual(
            [item["http_status"] for item in attempts],
            [500, 200],
        )
        self.assertEqual(
            attempts[0]["request_id"],
            attempts[1]["request_id"],
        )

    @staticmethod
    def route_record(number, departure, arrival, hour):
        """Create a minimal real-contract OAG record for route tests."""

        return {
            "flightNumber": number,
            "carrier": {"iata": "KQ"},
            "departure": {
                "airport": {"iata": departure},
                "date": {"local": "2026-11-16"},
                "time": {"local": f"{hour}:00:00"},
            },
            "arrival": {
                "airport": {"iata": arrival},
                "date": {"local": "2026-11-16"},
                "time": {"local": "20:00:00"},
            },
            "statusDetails": [{"state": "Scheduled"}],
        }

    def test_destination_only_query_requests_departure_without_calling_oag(self):
        requests = []
        provider_response = response(200, {
            "data": [self.route_record(100, "NBO", "LHR", "09")],
        })
        service = OAGFlightService(
            Settings(
                oag_api_key="test-key",
                oag_route_search_enabled=True,
            ),
            client_factory=lambda **kwargs: RecordingClient(
                provider_response,
                requests,
                **kwargs,
            ),
            sleeper=lambda _seconds: None,
        )

        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="Are there any KQ flights to London today?",
                language="en",
            ),
        )

        self.assertEqual(result.decision, "clarification_required")
        self.assertIn("departing from", result.answer)
        self.assertIn("London", result.answer)
        self.assertEqual(requests, [])

    def test_complete_route_uses_departure_local_date_filter(self):
        requests = []
        provider_response = response(200, {
            "data": [self.route_record(760, "NBO", "JNB", "08")],
        })
        service = OAGFlightService(
            Settings(
                oag_api_key="test-key",
                oag_route_search_enabled=True,
            ),
            client_factory=lambda **kwargs: RecordingClient(
                provider_response,
                requests,
                **kwargs,
            ),
            sleeper=lambda _seconds: None,
        )

        service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="KQ flights from Nairobi to Johannesburg today",
                language="en",
            ),
        )

        self.assertEqual(
            requests[0]["params"]["DepartureDateTime"],
            date.today().isoformat(),
        )
        self.assertNotIn(
            "ArrivalDateTime",
            requests[0]["params"],
        )
        self.assertEqual(requests[0]["params"]["DepartureAirport"], "NBO")
        self.assertEqual(requests[0]["params"]["ArrivalAirport"], "JNB")

    def test_route_query_returns_every_distinct_oag_match(self):
        service = self.service(response(200, {
            "data": [
                self.route_record(760, "NBO", "JNB", "08"),
                self.route_record(762, "NBO", "JNB", "18"),
            ],
        }), oag_route_search_enabled=True)

        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="Show me KQ flights from Nairobi to Johannesburg today.",
                language="en",
            ),
        )

        self.assertIsNone(result.flight_info)
        self.assertEqual(
            [item.flight_number for item in result.flight_options],
            ["KQ760", "KQ762"],
        )
        self.assertIn("2 matching Kenya Airways flights", result.answer)

    def test_airport_codes_and_requested_status_are_applied(self):
        delayed = self.route_record(762, "NBO", "JNB", "18")
        delayed["statusDetails"] = [{"state": "Delayed"}]
        service = self.service(response(200, {
            "data": [
                self.route_record(760, "NBO", "JNB", "08"),
                delayed,
            ],
        }), oag_route_search_enabled=True)

        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="Are there delayed KQ flights from NBO to JNB today?",
                language="en",
            ),
        )

        self.assertEqual(result.flight_info.flight_number, "KQ762")
        self.assertEqual(result.flight_info.status, "Delayed")

    def test_evening_follow_up_filters_cached_route_results(self):
        service = self.service(response(200, {
            "data": [
                self.route_record(760, "NBO", "JNB", "08"),
                self.route_record(762, "NBO", "JNB", "18"),
            ],
        }), oag_route_search_enabled=True)

        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question=(
                    "Show me KQ flights from Nairobi to Johannesburg today. "
                    "Follow-up: What about the evening flight?"
                ),
                language="en",
            ),
        )

        self.assertEqual(result.flight_info.flight_number, "KQ762")

    def test_route_missing_date_and_disabled_subscription_do_not_call_oag(self):
        missing_date = self.service(response(500, {}), oag_route_search_enabled=True)
        clarification = missing_date.answer_if_flight_query(
            GroundedAnswerRequest(
                question="Ndege ya KQ kwenda Johannesburg iko aje?",
                language="sw",
            ),
        )
        self.assertEqual(clarification.decision, "clarification_required")
        self.assertIn("tarehe gani", clarification.answer)

        disabled = self.service(response(500, {}))
        limitation = disabled.answer_if_flight_query(
            GroundedAnswerRequest(
                question="KQ flights from Nairobi to London today",
                language="en",
            ),
        )
        self.assertEqual(limitation.decision, "clarification_required")
        self.assertIn("not enabled", limitation.answer)

    def test_no_route_matches_returns_flight_specific_message(self):
        service = self.service(
            response(200, {"data": []}),
            oag_route_search_enabled=True,
        )
        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="Nipe status ya KQ kutoka Nairobi kwenda London leo",
                language="mixed",
            ),
        )

        self.assertEqual(result.decision, "flight_information")
        self.assertIn("No Kenya Airways flight", result.answer)

    def test_non_kq_route_is_declined_without_provider_request(self):
        service = self.service(
            response(500, {}),
            oag_route_search_enabled=True,
        )
        result = service.answer_if_flight_query(
            GroundedAnswerRequest(
                question="Show me BA flights to London today",
                language="en",
            ),
        )

        self.assertEqual(result.decision, "fallback")
        self.assertIn("Kenya Airways flights (KQ) only", result.answer)


if __name__ == "__main__":
    unittest.main()
