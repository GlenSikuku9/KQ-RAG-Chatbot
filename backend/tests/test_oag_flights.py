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
        for value in ("KQ100", "KQ 100", "kq100"):
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
            result.model_dump()["travel_date"],
            "2026-11-10",
        )

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


if __name__ == "__main__":
    unittest.main()
