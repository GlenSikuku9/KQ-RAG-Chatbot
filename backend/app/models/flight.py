"""Typed, provider-neutral flight information exposed to chatbot clients."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_serializer


class FlightAirport(BaseModel):
    """Airport fields are optional because OAG entitlements can differ."""

    code: str | None = None
    country_code: str | None = None
    name: str | None = None
    terminal: str | None = None
    gate: str | None = None


class FlightInformation(BaseModel):
    """A safe subset of fields copied from one OAG flight instance."""

    flight_number: str
    travel_date: date
    status: str | None = None
    operating_carrier: str | None = None
    marketing_carrier: str | None = None
    departure: FlightAirport
    arrival: FlightAirport
    scheduled_departure: str | None = None
    scheduled_arrival: str | None = None
    estimated_departure: str | None = None
    estimated_arrival: str | None = None
    actual_departure: str | None = None
    actual_arrival: str | None = None
    actual_takeoff: str | None = None
    actual_landing: str | None = None
    delay_minutes: int | None = None
    departure_delay_minutes: int | None = None
    arrival_delay_minutes: int | None = None
    retrieved_at: str
    source: Literal["OAG Flight Information"] = "OAG Flight Information"
    cached: bool = False

    @field_serializer("travel_date")
    def serialize_travel_date(self, value: date) -> str:
        """Firestore cannot encode a date without a time; retain ISO date text."""

        return value.isoformat()


class FlightLookupRequest(BaseModel):
    """An explicit refresh request never accepts arbitrary carrier codes."""

    flight_number: str = Field(min_length=3, max_length=12)
    travel_date: date
    force_refresh: bool = False
