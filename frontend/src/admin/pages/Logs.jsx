import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ArrowRight,
  CircleAlert,
  CircleCheck,
  Eye,
  FileText,
  ListChecks,
  RefreshCcw,
  RefreshCw,
  Search,
  UserRoundCog,
  X,
} from "lucide-react";
import {
  formatDateTime,
  modelDisplayName,
  readableEventName,
  severityLabel,
} from "../utils";
import "../styles/Logs.css";

// Keep event styling in one place so historical and future records always
// receive a restrained fallback without changing their underlying data.
function eventPresentation(eventType, severity) {
  const presentations = {
    default_model_updated: {
      icon: RefreshCw,
      tone: "navy",
    },
    chat_failed: {
      icon: CircleAlert,
      tone: "error",
    },
    document_updated: {
      icon: FileText,
      tone: "document",
    },
    document_reprocessed: {
      icon: RefreshCcw,
      tone: "document",
    },
    user_role_changed: {
      icon: UserRoundCog,
      tone: "navy",
    },
    chat_completed: {
      icon: CircleCheck,
      tone: "success",
    },
  };

  if (presentations[eventType]) {
    return presentations[eventType];
  }

  if (severity === "error") {
    return {
      icon: CircleAlert,
      tone: "error",
    };
  }

  if (eventType.includes("document")) {
    return {
      icon: FileText,
      tone: "document",
    };
  }

  return {
    icon: ListChecks,
    tone: "navy",
  };
}

function LogsTable({
  logs,
  selectedLogId,
  onSelectedLogOpened,
}) {
  const [search, setSearch] = useState("");
  const [eventType, setEventType] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [selectedLog, setSelectedLog] = useState(null);
  const detailsTrigger = useRef(null);
  const pageSize = 12;
  const eventTypes = useMemo(
    () => [...new Set(logs.map((item) => item.event_type))].sort(),
    [logs],
  );
  // Recalculate the potentially large log collection only when its data or a
  // filter changes, not when the drawer and other local UI state changes.
  const filteredLogs = useMemo(() => {
    const term = search.trim().toLowerCase();

    return logs.filter((item) => {
      const timestamp = new Date(item.timestamp);

      return (
        (!term
          || [item.event_type, item.description, item.actor].some((value) =>
            String(value || "")
              .toLowerCase()
              .includes(term),
          ))
        && (eventType === "all" || item.event_type === eventType)
        && (severity === "all" || item.severity === severity)
        && (!dateFrom || timestamp >= new Date(`${dateFrom}T00:00:00`))
        && (!dateTo || timestamp <= new Date(`${dateTo}T23:59:59`))
      );
    });
  }, [logs, search, eventType, severity, dateFrom, dateTo]);
  const pageCount = Math.max(1, Math.ceil(filteredLogs.length / pageSize));
  const safePage = Math.min(currentPage, pageCount);
  const visibleLogs = filteredLogs.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize,
  );
  const filtersActive = Boolean(
    search
    || eventType !== "all"
    || severity !== "all"
    || dateFrom
    || dateTo,
  );

  function updateFilter(setter, value) {
    setter(value);
    setCurrentPage(1);
  }

  function openDetails(item, event) {
    detailsTrigger.current = event.currentTarget;
    setSelectedLog(item);
  }

  function clearFilters() {
    setSearch("");
    setEventType("all");
    setSeverity("all");
    setDateFrom("");
    setDateTo("");
    setCurrentPage(1);
  }

  useEffect(() => {
    if (!selectedLogId) {
      return;
    }

    // Overview deep-links reuse the same drawer and records as the Logs page.
    const matchingLog = logs.find(
      (item) => item.event_id === selectedLogId,
    );

    if (matchingLog) {
      setSelectedLog(matchingLog);
    }
    onSelectedLogOpened();
  }, [logs, selectedLogId, onSelectedLogOpened]);

  return (
    <section className="admin-card logs-panel">
      <div className="admin-toolbar logs-toolbar">
        <label className="admin-search-field logs-search-field">
          <span>Search</span>
          <span className="logs-search-control">
            <Search size={16} aria-hidden="true" />
            <input
              type="search"
              value={search}
              onChange={(event) =>
                updateFilter(
                  setSearch,
                  event.target.value,
                )}
              placeholder="Events, descriptions or actors"
            />
          </span>
        </label>
        <label>
          Event type
          <select
            value={eventType}
            onChange={(event) => updateFilter(setEventType, event.target.value)}
          >
            <option value="all">All events</option>
            {eventTypes.map((value) => (
              <option value={value} key={value}>
                {readableEventName(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Severity
          <select
            value={severity}
            onChange={(event) => updateFilter(setSeverity, event.target.value)}
          >
            <option value="all">All severities</option>
            <option value="info">Information</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
          </select>
        </label>
        <label>
          Date from
          <input
            type="date"
            value={dateFrom}
            onChange={(event) => updateFilter(setDateFrom, event.target.value)}
          />
        </label>
        <label>
          Date to
          <input
            type="date"
            value={dateTo}
            onChange={(event) => updateFilter(setDateTo, event.target.value)}
          />
        </label>
        {filtersActive && (
          <button
            className="logs-clear-filters"
            type="button"
            onClick={clearFilters}
          >
            Clear filters
          </button>
        )}
      </div>
      <div className="admin-table-scroll">
        <table className="admin-table logs-table">
          <colgroup>
            <col className="logs-time-column" />
            <col className="logs-event-column" />
            <col className="logs-description-column" />
            <col className="logs-actor-column" />
            <col className="logs-severity-column" />
            <col className="logs-action-column" />
          </colgroup>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Event</th>
              <th>Description</th>
              <th>Actor</th>
              <th>Severity</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {visibleLogs.map((item, index) => {
              const presentation = eventPresentation(
                item.event_type,
                item.severity,
              );
              const EventIcon = presentation.icon;

              return (
                <tr key={item.event_id || `${item.timestamp}-${index}`}>
                  <td className="log-time">
                    {formatDateTime(item.timestamp)}
                  </td>
                  <td>
                    <div className="log-event-cell">
                      <span
                        className={`log-event-icon ${presentation.tone}`}
                        aria-hidden="true"
                      >
                        <EventIcon size={16} strokeWidth={2} />
                      </span>
                      <span className="log-event-copy">
                        <strong>{readableEventName(item.event_type)}</strong>
                        <small>{item.event_type}</small>
                      </span>
                    </div>
                  </td>
                  <td className="log-description" title={item.description}>
                    <span>{item.description}</span>
                  </td>
                  <td className="log-actor" title={item.actor}>
                    {item.actor}
                  </td>
                  <td>
                    <span className={`severity-badge ${item.severity}`}>
                      {severityLabel(item.severity)}
                    </span>
                  </td>
                  <td>
                    <button
                      className="log-details-button"
                      type="button"
                      onClick={(event) => openDetails(item, event)}
                    >
                      <Eye size={15} aria-hidden="true" />
                      Details
                    </button>
                  </td>
                </tr>
              );
            })}
            {!visibleLogs.length && (
              <tr>
                <td colSpan="6" className="admin-empty">
                  No recorded activity matches these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="admin-pagination">
        <p>
          Showing {visibleLogs.length} of {filteredLogs.length} events
        </p>
        <div>
          <button
            type="button"
            disabled={safePage === 1}
            onClick={() => setCurrentPage(safePage - 1)}
          >
            Previous
          </button>
          <span>
            Page {safePage} of {pageCount}
          </span>
          <button
            type="button"
            disabled={safePage === pageCount}
            onClick={() => setCurrentPage(safePage + 1)}
          >
            Next
          </button>
        </div>
      </div>
      {selectedLog && (
        <LogDetailsDrawer
          log={selectedLog}
          onClose={() => setSelectedLog(null)}
          returnFocusRef={detailsTrigger}
        />
      )}
    </section>
  );
}

function LogDetailsDrawer({ log, onClose, returnFocusRef }) {
  const drawer = useRef(null);
  const details = log.details || {};
  const isModelChange = log.event_type === "default_model_updated";
  const isChatFailure = log.event_type === "chat_failed";
  const isChatEvent = log.event_type.startsWith("chat_");
  const modelName = details.model_name
    || (details.model_id ? modelDisplayName(details.model_id) : null);
  const presentation = eventPresentation(
    log.event_type,
    log.severity,
  );
  const EventIcon = presentation.icon;
  const knownKeys = new Set([
    "previous_model_id",
    "previous_model_name",
    "previous_model_provider",
    "new_model_id",
    "new_model_name",
    "new_model_provider",
    "model_name",
    "model_id",
    "model_selection_status",
    "provider",
    "failure_category",
    "failed_operation",
    "error_code",
    "error_summary",
    "request_id",
    "duration_ms",
    "response_source",
    "administrator_email",
    "target_id",
    "target_type",
  ]);
  const safeMetadata = Object.entries(details).filter(
    ([key, value]) =>
      value !== null
      && value !== undefined
      && value !== ""
      && !knownKeys.has(key)
      && !/(token|secret|api.?key|authorization|message_text|prompt)/i.test(
        key,
      ),
  );

  function detailRow(label, value, formatter = String) {
    if (value === null || value === undefined || value === "") {
      return null;
    }

    return (
      <div key={label}>
        <dt>{label}</dt>
        <dd>{formatter(value)}</dd>
      </div>
    );
  }

  function providerLabel(value) {
    return readableEventName(value).replace("Ai", "AI");
  }

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    drawer.current?.focus();
    function handleKeyDown(event) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !drawer.current) return;
      const focusable = [
        ...drawer.current.querySelectorAll(
          "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
        ),
      ];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
      returnFocusRef.current?.focus();
    };
  }, [onClose, returnFocusRef]);

  return (
    <div
      className="log-drawer-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <aside
        className="log-details-drawer"
        ref={drawer}
        role="dialog"
        aria-modal="true"
        aria-labelledby="log-drawer-title"
        tabIndex="-1"
      >
        <header className="log-drawer-header">
          <div className="log-drawer-heading">
            <span
              className={`log-event-icon ${presentation.tone}`}
              aria-hidden="true"
            >
              <EventIcon size={18} strokeWidth={2} />
            </span>
            <div>
              <p className="eyebrow">LOG DETAILS</p>
              <h2 id="log-drawer-title">
                {readableEventName(log.event_type)}
              </h2>
            </div>
          </div>
          <button
            className="admin-icon-button"
            type="button"
            onClick={onClose}
            aria-label="Close log details"
          >
            <X size={20} />
          </button>
          <p className="log-drawer-description">{log.description}</p>
          <span className={`severity-badge ${log.severity}`}>
            {severityLabel(log.severity)}
          </span>
        </header>
        <section className="log-metadata log-overview-section">
          <h3>Event information</h3>
          <dl className="log-detail-list">
            <div>
              <dt>Event code</dt>
              <dd>
                <code>{log.event_type}</code>
              </dd>
            </div>
            <div>
              <dt>Timestamp</dt>
              <dd>{formatDateTime(log.timestamp)}</dd>
            </div>
            <div>
              <dt>Actor</dt>
              <dd>{log.actor || "System"}</dd>
            </div>
          </dl>
        </section>

        {isModelChange && (
          <section className="log-metadata log-model-change">
            <h3>Model change</h3>
            <div className="log-model-comparison">
              <article>
                <span>Previous Model</span>
                <strong>
                  {details.previous_model_name || "Previous model not recorded"}
                </strong>
                {details.previous_model_id && (
                  <code>{details.previous_model_id}</code>
                )}
                {details.previous_model_provider && (
                  <small>
                    {providerLabel(details.previous_model_provider)}
                  </small>
                )}
              </article>
              <ArrowRight aria-hidden="true" size={20} />
              <article className="current">
                <span>New Default Model</span>
                <strong>
                  {details.new_model_name
                    || modelDisplayName(details.new_model_id)
                    || "New model details not recorded"}
                </strong>
                {details.new_model_id && <code>{details.new_model_id}</code>}
                {details.new_model_provider && (
                  <small>{providerLabel(details.new_model_provider)}</small>
                )}
              </article>
            </div>
          </section>
        )}

        {isChatEvent && (
          <section className="log-metadata">
            <h3>
              {isChatFailure ? "Failure diagnostics" : "Chat diagnostics"}
            </h3>
            <dl>
              {isChatFailure
              && details.model_selection_status === "not_selected"
                ? detailRow("Model", "Model was not selected")
                : detailRow("Model", modelName)}
              {detailRow("Model ID", details.model_id)}
              {detailRow("Provider", details.provider, providerLabel)}
              {isChatFailure
                && detailRow(
                  "Failure Category",
                  details.failure_category,
                  readableEventName,
                )}
              {isChatFailure
                && detailRow(
                  "Failed Operation",
                  details.failed_operation,
                  readableEventName,
                )}
              {isChatFailure
                && detailRow("Error Code", details.error_code)}
              {isChatFailure
                && detailRow("Error Summary", details.error_summary)}
              {isChatFailure
                && detailRow("Request ID", details.request_id)}
              {!isChatFailure
                && detailRow(
                  "Response Source",
                  details.response_source,
                  readableEventName,
                )}
              {detailRow(
                "Duration",
                details.duration_ms,
                (value) => `${Number(value).toFixed(2)} ms`,
              )}
            </dl>
          </section>
        )}

        {safeMetadata.length > 0 && (
          <section className="log-metadata">
            <h3>Additional information</h3>
            <dl>
              {safeMetadata.map(([key, value]) => (
                <div key={key}>
                  <dt>{readableEventName(key)}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}
      </aside>
    </div>
  );
}
export default LogsTable;
