import { useEffect, useState } from "react";
import { refreshFlightInformation } from "../api";
import "../styles/FlightInformationCard.css";

// OAG returns local date/time strings without a browser-safe timezone. Keep
// their values deterministic while presenting dates as day-month-year.
function flightDateTime(value, dateOnly = false) {
  if (!value) return null;
  const [datePart, timePart] = value.split("T");
  const [year, month, day] = datePart.split("-");

  if (!year || !month || !day) return value;
  const displayDate = `${day}-${month}-${year}`;
  if (dateOnly || !timePart) return displayDate;
  return `${displayDate} ${timePart.slice(0, 5)}`;
}

function checkedAtDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Africa/Nairobi",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const valueFor = (type) => (
    parts.find((part) => part.type === type)?.value || ""
  );
  const displayDate = [
    valueFor("day"),
    valueFor("month"),
    valueFor("year"),
  ].join("-");
  const displayTime = `${valueFor("hour")}:${valueFor("minute")}`;
  return `${displayDate} ${displayTime} EAT`;
}

function timingLabel(minutes) {
  if (!Number.isInteger(minutes)) return null;
  if (minutes === 0) return "On time";
  return minutes > 0
    ? `${minutes} minutes delayed`
    : `${Math.abs(minutes)} minutes early`;
}

function flightLocation(airport, fallback) {
  const code = airport?.code;
  const countryCode = airport?.country_code;
  let country = countryCode;

  if (countryCode && typeof Intl.DisplayNames === "function") {
    country = new Intl.DisplayNames(["en"], {
      type: "region",
    }).of(countryCode) || countryCode;
  }

  return [code, country].filter(Boolean).join(", ") || fallback;
}

function FlightLocation({ airport, fallback }) {
  const location = flightLocation(airport, fallback);
  const code = airport?.code;
  const remainder = code
    ? location.replace(code, "").replace(/^,\s*/, "")
    : location;

  return (
    <span className="flight-location-display">
      {code && <strong>{code}</strong>}
      {remainder && <span>{remainder}</span>}
    </span>
  );
}

function FlightRoute({ departure, arrival }) {
  return (
    <span className="flight-route-display">
      <strong>{departure || "Unknown"}</strong>
      <span aria-hidden="true">{" \u2192 "}</span>
      <strong>{arrival || "Unknown"}</strong>
    </span>
  );
}

function FlightDetailList({ items, className = "" }) {
  const availableItems = items.filter(([, value]) => value != null);
  if (!availableItems.length) return null;

  return (
    <dl className={className}>
      {availableItems.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function FlightInformationCard({
  initialInformation,
  allowRefresh = true,
}) {
  const [information, setInformation] = useState(initialInformation);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState("");

  // A newly selected saved message should replace the previous snapshot.
  useEffect(() => {
    setInformation(initialInformation);
    setRefreshError("");
  }, [initialInformation]);

  if (!information) return null;

  const departureLocation = flightLocation(
    information.departure,
    "departure airport",
  );
  const arrivalLocation = flightLocation(
    information.arrival,
    "arrival airport",
  );
  const overviewItems = [
    [
      "Route",
      <FlightRoute
        key="flight-route"
        departure={information.departure?.code}
        arrival={information.arrival?.code}
      />,
    ],
    [
      "Travel date",
      flightDateTime(information.travel_date, true),
    ],
  ];
  const departureItems = [
    [
      "Scheduled departure",
      flightDateTime(information.scheduled_departure),
    ],
    [
      "Estimated departure",
      flightDateTime(information.estimated_departure),
    ],
    [
      "Actual gate departure",
      flightDateTime(information.actual_departure),
    ],
    [
      "Actual takeoff",
      flightDateTime(information.actual_takeoff),
    ],
    ["Departure terminal", information.departure?.terminal],
    ["Departure gate", information.departure?.gate],
    [
      "Departure timing",
      timingLabel(information.departure_delay_minutes),
    ],
  ];
  const arrivalItems = [
    [
      "Scheduled arrival",
      flightDateTime(information.scheduled_arrival),
    ],
    [
      "Estimated arrival",
      flightDateTime(information.estimated_arrival),
    ],
    [
      "Actual landing",
      flightDateTime(information.actual_landing),
    ],
    [
      "Actual gate arrival",
      flightDateTime(information.actual_arrival),
    ],
    ["Arrival terminal", information.arrival?.terminal],
    ["Arrival gate", information.arrival?.gate],
    [
      "Arrival timing",
      timingLabel(information.arrival_delay_minutes),
    ],
  ];

  async function refresh() {
    if (!allowRefresh || refreshing) return;
    setRefreshing(true);
    setRefreshError("");

    try {
      setInformation(await refreshFlightInformation(
        information.flight_number,
        information.travel_date,
      ));
    } catch (reason) {
      setRefreshError(
        reason instanceof Error
          ? reason.message
          : "Flight information could not be refreshed.",
      );
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <section className="flight-information-card">
      <header>
        <div>
          <img
            className="flight-card-logo"
            src="/images/kenya-airways-logo-colour.svg"
            alt="Kenya Airways"
          />
          <h3>Flight {information.flight_number}</h3>
        </div>
        {information.status && (
          <span className="flight-status">{information.status}</span>
        )}
      </header>
      <div className="flight-detail-sections">
        <section className="flight-detail-section flight-overview">
          <h4>Flight overview</h4>
          <FlightDetailList
            className="flight-overview-grid"
            items={overviewItems}
          />
        </section>
        <section className="flight-detail-section">
          <h4>Departure</h4>
          <p className="flight-section-location">
            <FlightLocation
              airport={information.departure}
              fallback={departureLocation}
            />
            <span>{" \u00b7 Times shown in EAT"}</span>
          </p>
          <FlightDetailList items={departureItems} />
        </section>
        <section className="flight-detail-section">
          <h4>Arrival</h4>
          <p className="flight-section-location">
            <FlightLocation
              airport={information.arrival}
              fallback={arrivalLocation}
            />
            <span>{" \u00b7 Times shown in EAT"}</span>
          </p>
          <FlightDetailList items={arrivalItems} />
        </section>
      </div>
      <footer className={!allowRefresh ? "read-only" : undefined}>
        <p>
          Source: {information.source}. Last checked{" "}
          {checkedAtDateTime(information.retrieved_at)}.
          {information.cached ? " Cached result." : ""}
        </p>
        {allowRefresh && (
          <button
            className="flight-refresh-button"
            type="button"
            disabled={refreshing}
            onClick={refresh}
          >
            {refreshing ? "Refreshing..." : "Refresh flight status"}
          </button>
        )}
      </footer>
      {refreshError && (
        <p className="flight-refresh-error" role="alert">
          {refreshError}
        </p>
      )}
    </section>
  );
}

export { FlightInformationCard };
