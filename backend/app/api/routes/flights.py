"""Passenger-safe OAG lookup endpoint used by the manual refresh action."""

from fastapi import APIRouter, HTTPException

from app.models.flight import FlightInformation, FlightLookupRequest
from app.services.oag_flights import OAGFlightError, get_oag_flight_service


router = APIRouter()


@router.post("/lookup", response_model=FlightInformation)
def lookup_flight(payload: FlightLookupRequest) -> FlightInformation:
    """Refresh one validated KQ flight without exposing the provider key."""

    try:
        return get_oag_flight_service().lookup(
            payload.flight_number,
            payload.travel_date,
            force_refresh=payload.force_refresh,
        )
    except OAGFlightError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
