import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  adminAcceptSupportRequest,
  adminConvertSupportRequest,
  adminGetSupportAvailability,
  adminReopenSupportRequest,
  adminResolveSupportRequest,
  adminSendSupportMessage,
  adminSetSupportAvailability,
  adminSupportRequest,
  adminSupportRequests,
  adminSupportSummary,
} from "../../api";
import { Pagination, TableLoadingRows } from "../components/Pagination";
import { AdminNotice } from "../components/AdminNotice";
import { AdminRefreshIndicator } from "../components/AdminRefreshIndicator";
import { formatDateTime } from "../utils";
import {
  subscribeToSupportEvents,
  subscribeToSupportRequest,
} from "../../supportRealtime";
import "../styles/HumanSupport.css";


function statusLabel(value) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => (
    letter.toUpperCase()
  ));
}


function supportMessages(detail) {
  const aiMessages = (detail?.ai_messages || []).map((message) => ({
    ...message,
    kind: message.sender_type === "chatbot" ? "ai" : "passenger",
  }));
  const liveMessages = (detail?.messages || []).map((message) => ({
    ...message,
    kind: message.sender_role,
  }));
  const requestEvent = detail?.request ? [{
    kind: "system",
    message_id: `support-request:${detail.request.request_id}`,
    message_text: "Human support requested.",
    timestamp: detail.request.created_at,
  }] : [];
  const unique = new Map();

  // Collections remain separate in Firestore. This display-only merge removes
  // an exact duplicate if legacy data contains the same recorded message.
  [...aiMessages, ...requestEvent, ...liveMessages].forEach((message) => {
    const key = [
      message.kind,
      message.message_text,
      message.timestamp,
    ].join(":");
    if (!unique.has(key)) unique.set(key, message);
  });

  const order = {
    passenger: 0,
    ai: 1,
    system: 2,
    agent: 3,
  };
  return [...unique.values()].sort((left, right) => (
    new Date(left.timestamp) - new Date(right.timestamp)
    || (order[left.kind] || 0) - (order[right.kind] || 0)
  ));
}


function SupportWorkspace({
  user,
  requestId,
  onClose,
  onChanged,
  onOpenOriginal,
}) {
  const [detail, setDetail] = useState(null);
  const [message, setMessage] = useState("");
  const [state, setState] = useState("loading");
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      setDetail(await adminSupportRequest(user, requestId));
      setState("ready");
    } catch (reason) {
      setError(reason.message);
      setState("error");
    }
  }, [requestId, user]);

  useEffect(() => {
    load();
    const stopRealtime = subscribeToSupportRequest(
      requestId,
      (request) => setDetail((current) => ({
        ...(current || {}),
        request,
        messages: current?.messages || [],
        ai_messages: current?.ai_messages || [],
      })),
      (messages) => setDetail((current) => ({
        ...(current || {}),
        messages,
        ai_messages: current?.ai_messages || [],
      })),
      () => {},
    );
    // Firestore supplies live changes. This low-frequency fallback only
    // repairs state after an extended disconnection without exhausting reads.
    const timer = window.setInterval(load, 60000);
    return () => {
      stopRealtime();
      window.clearInterval(timer);
    };
  }, [load]);

  async function transition(operation) {
    if (state === "saving") return;
    setState("saving");
    setError(null);
    try {
      await operation(user, requestId);
      await load();
      onChanged();
    } catch (reason) {
      setError(reason.message);
      setState("error");
    }
  }

  async function send(event) {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;
    setState("sending");
    setError(null);
    try {
      await adminSendSupportMessage(
        user,
        requestId,
        text,
        crypto.randomUUID(),
      );
      setMessage("");
      await load();
    } catch (reason) {
      setError(reason.message);
      setState("error");
    }
  }

  if (state === "loading") {
    return <div className="support-workspace-loading">Loading case…</div>;
  }
  if (!detail) {
    return (
      <AdminNotice
        type="error"
        message={error || "Case unavailable."}
      />
    );
  }

  const request = detail.request;
  const messages = supportMessages(detail);
  const assignedToCurrentAgent = request.assigned_agent_id === user.uid;

  return (
    <section className="support-workspace" aria-label="Support case workspace">
      <header className="support-workspace-header">
        <div>
          <p className="eyebrow">{request.reference}</p>
          <h2>{request.issue_summary}</h2>
        </div>
        <button className="button secondary" type="button" onClick={onClose}>
          Back to requests
        </button>
      </header>

      <AdminNotice
        type="error"
        message={error}
        onDismiss={() => setError(null)}
      />

      <div className="support-workspace-grid">
        <section className="support-conversation-panel">
          <div className="support-message-list" aria-live="polite">
            {messages.map((item) => (
              <article
                className={`support-message ${item.kind}`}
                key={item.message_id}
              >
                <p className="support-message-meta">
                  <strong>
                    {item.kind === "agent"
                      ? `${item.sender_name || "Agent"} · Support Agent`
                      : item.kind === "passenger"
                        ? request.passenger_name
                          ? `Passenger · ${request.passenger_name}`
                          : "Passenger"
                        : item.kind === "ai"
                          ? "AI Assistant"
                          : "Support update"}
                  </strong>
                  <time dateTime={item.timestamp}>
                    {formatDateTime(item.timestamp)}
                  </time>
                </p>
                <div className="support-message-bubble">
                  {item.message_text}
                </div>
                {item.retrieved_chunks?.length > 0 && (
                  <div className="support-case-sources">
                    {item.retrieved_chunks.map((source) => (
                      source.source_url ? (
                        <a
                          href={source.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          key={source.message_chunk_id}
                        >
                          {source.citation_id} · {source.source_title}
                        </a>
                      ) : (
                        <span key={source.message_chunk_id}>
                          {source.citation_id} · {source.source_title}
                        </span>
                      )
                    ))}
                  </div>
                )}
              </article>
            ))}
          </div>

          <form className="support-agent-composer" onSubmit={send}>
            <label htmlFor="support-agent-message">Reply to passenger</label>
            <textarea
              id="support-agent-message"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              disabled={!assignedToCurrentAgent || request.status !== "in_progress"}
              placeholder={
                assignedToCurrentAgent
                  ? "Write a support reply…"
                  : "Accept this request to reply."
              }
              rows="3"
              maxLength="16000"
            />
            <button
              className="button primary"
              disabled={
                !message.trim()
                || state === "sending"
                || !assignedToCurrentAgent
                || request.status !== "in_progress"
              }
            >
              {state === "sending" ? "Sending…" : "Send reply"}
            </button>
          </form>
        </section>

        <aside className="support-case-details">
          <h3>Case details</h3>
          <dl>
            <div><dt>Reference</dt><dd>{request.reference}</dd></div>
            <div>
              <dt>Passenger</dt>
              <dd>{request.passenger_name || request.passenger_email || "Passenger"}</dd>
            </div>
            <div><dt>Type</dt><dd>{statusLabel(request.request_type)}</dd></div>
            <div><dt>Status</dt><dd>{statusLabel(request.status)}</dd></div>
            <div><dt>Reason</dt><dd>{statusLabel(request.reason)}</dd></div>
            <div>
              <dt>Assigned to</dt>
              <dd>{request.assigned_agent_name || "Unassigned"}</dd>
            </div>
            <div><dt>Requested</dt><dd>{formatDateTime(request.created_at)}</dd></div>
            <div>
              <dt>Accepted</dt>
              <dd>{request.accepted_at ? formatDateTime(request.accepted_at) : "Not accepted"}</dd>
            </div>
          </dl>

          <div className="support-case-actions">
            {request.status === "pending" && (
              <>
                <button
                  className="button primary"
                  type="button"
                  disabled={state === "saving"}
                  onClick={() => transition(adminAcceptSupportRequest)}
                >
                  Accept request
                </button>
                {request.request_type === "live" && (
                  <button
                    className="button secondary"
                    type="button"
                    disabled={state === "saving"}
                    onClick={() => transition(adminConvertSupportRequest)}
                  >
                    Convert to ticket
                  </button>
                )}
              </>
            )}
            {request.status === "in_progress" && (
              <button
                className="button primary"
                type="button"
                disabled={state === "saving"}
                onClick={() => transition(adminResolveSupportRequest)}
              >
                {state === "saving" ? "Resolving..." : "Resolve case"}
              </button>
            )}
            {request.status === "resolved" && (
              <button
                className="button secondary"
                type="button"
                disabled={state === "saving"}
                onClick={() => transition(adminReopenSupportRequest)}
              >
                Reopen case
              </button>
            )}
          </div>

          {detail.feedback && (
            <section className="support-feedback-summary">
              <h3>Passenger feedback</h3>
              <strong>{detail.feedback.rating} out of 5</strong>
              <p>{detail.feedback.comment || "No written comment."}</p>
            </section>
          )}

          <button
            className="support-original-conversation-link"
            type="button"
            onClick={() => onOpenOriginal(request.conversation_id)}
          >
            View original AI conversation
          </button>
        </aside>
      </div>
    </section>
  );
}


export default function HumanSupport({
  user,
  initialRequestId,
  onOpened,
  onOpenConversation,
  revision = 0,
}) {
  const [filters, setFilters] = useState({
    search: "",
    request_type: "",
    case_status: "",
    assigned_agent: "",
    sort_order: "newest",
    page: 1,
    page_size: 10,
  });
  const [data, setData] = useState(null);
  const [summary, setSummary] = useState(null);
  const [selected, setSelected] = useState(initialRequestId || null);
  const [available, setAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const loaded = useRef(false);
  const filterKey = JSON.stringify(filters);

  const load = useCallback(async () => {
    if (loaded.current) setRefreshing(true);
    const current = JSON.parse(filterKey);
    const params = new URLSearchParams();
    Object.entries(current).forEach(([key, value]) => {
      if (value !== "") params.set(key, value);
    });
    try {
      const [page, metrics] = await Promise.all([
        adminSupportRequests(user, params),
        adminSupportSummary(user),
      ]);
      setData(page);
      setSummary(metrics);
      setError(null);
      loaded.current = true;
    } catch (reason) {
      setError(reason.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [filterKey, revision, user]);

  useEffect(() => {
    const timer = window.setTimeout(load, filters.search ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [filters.search, load]);

  useEffect(() => {
    // Availability changes on its own heartbeat cadence, so table refreshes
    // should not spend an additional Firestore read fetching it each time.
    adminGetSupportAvailability(user)
      .then((presence) => setAvailable(presence.available))
      .catch((reason) => setError(reason.message));
  }, [user]);

  useEffect(() => {
    let refreshTimer;
    const stop = subscribeToSupportEvents(
      () => {
        // Status and assignment bursts collapse into one background summary
        // refresh; an open workspace and its draft remain mounted.
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(load, 250);
      },
      () => setError(
        "Live support updates paused. Existing data remains available.",
      ),
    );
    return () => {
      window.clearTimeout(refreshTimer);
      stop();
    };
  }, [load]);

  useEffect(() => {
    if (!initialRequestId) return;
    setSelected(initialRequestId);
    onOpened?.();
  }, [initialRequestId, onOpened]);

  useEffect(() => {
    if (!available) return undefined;
    // Heartbeats prevent a closed dashboard from appearing online forever.
    const heartbeat = () => adminSetSupportAvailability(user, true).catch(() => {
      setAvailable(false);
    });
    heartbeat();
    const timer = window.setInterval(heartbeat, 45000);
    return () => window.clearInterval(timer);
  }, [available, user]);

  async function changeAvailability(nextAvailable) {
    setAvailable(nextAvailable);
    try {
      await adminSetSupportAvailability(user, nextAvailable);
    } catch (reason) {
      setAvailable(!nextAvailable);
      setError(reason.message);
    }
  }

  if (selected) {
    return (
      <SupportWorkspace
        user={user}
        requestId={selected}
        onClose={() => setSelected(null)}
        onChanged={load}
        onOpenOriginal={onOpenConversation}
      />
    );
  }

  const cards = [
    ["Pending requests", summary?.pending || 0],
    ["Active live chats", summary?.active_live_chats || 0],
    ["Open tickets", summary?.open_tickets || 0],
    ["Resolved cases", summary?.resolved || 0],
  ];
  const durationMetric = (seconds) => (
    seconds == null ? "Not available" : `${seconds.toFixed(1)} seconds`
  );
  // Acceptance is easier to scan operationally in minutes, while resolution
  // retains its existing seconds display and backend storage remains unchanged.
  const acceptanceMetric = (seconds) => (
    seconds == null
      ? "Not available"
      : `${(seconds / 60).toFixed(1)} minutes`
  );

  return (
    <section className="human-support-page">
      <AdminRefreshIndicator active={refreshing} />
      <div className="support-availability-row">
        <div>
          <strong>Agent availability</strong>
          <span>
            {available
              ? "Available for live support"
              : "Unavailable"}
          </span>
        </div>
        <label className="availability-toggle">
          <input
            type="checkbox"
            checked={available}
            onChange={(event) => changeAvailability(event.target.checked)}
          />
          <span aria-hidden="true" />
        </label>
      </div>

      <AdminNotice
        type="error"
        message={error}
        onDismiss={() => setError(null)}
      />

      <div className="support-summary-grid">
        {cards.map(([label, value]) => (
          <article key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </div>

      <dl className="support-operational-metrics">
        <div><dt>Total requests</dt><dd>{summary?.total || 0}</dd></div>
        <div><dt>Live / tickets</dt><dd>{summary?.live || 0} / {summary?.tickets || 0}</dd></div>
        <div>
          <dt>Average acceptance</dt>
          <dd>{acceptanceMetric(summary?.average_acceptance_seconds)}</dd>
        </div>
        <div>
          <dt>Average resolution</dt>
          <dd>{durationMetric(summary?.average_resolution_seconds)}</dd>
        </div>
      </dl>

      <div className="support-table-card">
        <div className="support-table-filters">
          <input
            type="search"
            placeholder="Search reference, passenger or summary"
            value={filters.search}
            onChange={(event) => setFilters({
              ...filters,
              search: event.target.value,
              page: 1,
            })}
          />
          <select
            value={filters.request_type}
            onChange={(event) => setFilters({
              ...filters,
              request_type: event.target.value,
              page: 1,
            })}
          >
            <option value="">All types</option>
            <option value="live">Live chat</option>
            <option value="ticket">Ticket</option>
          </select>
          <select
            value={filters.case_status}
            onChange={(event) => setFilters({
              ...filters,
              case_status: event.target.value,
              page: 1,
            })}
          >
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="in_progress">In progress</option>
            <option value="resolved">Resolved</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <select
            value={filters.sort_order}
            onChange={(event) => setFilters({
              ...filters,
              sort_order: event.target.value,
              page: 1,
            })}
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
          </select>
          <select
            value={filters.assigned_agent}
            onChange={(event) => setFilters({
              ...filters,
              assigned_agent: event.target.value,
              page: 1,
            })}
          >
            <option value="">All agents</option>
            {(data?.agents || []).map((agent) => (
              <option value={agent} key={agent}>{agent}</option>
            ))}
          </select>
        </div>

        <div className="admin-table-wrap">
          <table className="admin-table support-table">
            <thead>
              <tr>
                <th>Request</th>
                <th>Passenger</th>
                <th>Type</th>
                <th>Created</th>
                <th>Status</th>
                <th>Assigned to</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading && <TableLoadingRows columns={7} />}
              {!loading && data?.items.map((item) => (
                <tr className={item.status === "pending" ? "pending" : ""} key={item.request_id}>
                  <td>
                    <strong>{item.reference}</strong>
                    <span className="support-summary-text">{item.issue_summary}</span>
                  </td>
                  <td>{item.passenger_name || item.passenger_email || "Passenger"}</td>
                  <td>{item.request_type === "live" ? "Live chat" : "Ticket"}</td>
                  <td>{formatDateTime(item.created_at)}</td>
                  <td>
                    <span className={`support-status ${item.status}`}>
                      {statusLabel(item.status)}
                    </span>
                  </td>
                  <td>{item.assigned_agent_name || "Unassigned"}</td>
                  <td>
                    <button
                      className="button secondary compact"
                      type="button"
                      onClick={() => setSelected(item.request_id)}
                    >
                      {item.status === "pending" ? "Open" : "View"}
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && !data?.items.length && (
                <tr><td colSpan="7" className="empty-table">No support requests found.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {data && (
          <Pagination
            page={filters.page}
            pageSize={filters.page_size}
            total={data.total}
            noun="requests"
            onPage={(page) => setFilters({ ...filters, page })}
            onPageSize={(pageSize) => setFilters({
              ...filters,
              page: 1,
              page_size: pageSize,
            })}
          />
        )}
      </div>
    </section>
  );
}
