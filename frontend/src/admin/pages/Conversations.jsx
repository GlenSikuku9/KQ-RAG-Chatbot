import { useEffect, useRef, useState } from "react";
import { EllipsisVertical } from "lucide-react";
import {
  adminConversationPage,
  adminDeleteConversation,
} from "../../api";
import { FormattedAnswer } from "../../components/FormattedAnswer";
import {
  Pagination as TablePagination,
  TableLoadingRows,
} from "../components/Pagination";
import { AdminNotice } from "../components/AdminNotice";
import { AdminRefreshIndicator } from "../components/AdminRefreshIndicator";
import {
  tablePageCache,
  useAdminTablePage,
} from "../hooks/useAdminTablePage";
import {
  formatDateTime,
  languageLabel,
  readableEventName,
} from "../utils";
import { subscribeToAdminConversationChanges } from "../../adminRealtime";
import "../styles/Conversations.css";

function ConversationTable({ user, revision, onSelect }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [search, setSearch] = useState("");
  const [language, setLanguage] = useState("");
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sortOrder, setSortOrder] = useState("newest");
  const [refresh, setRefresh] = useState(0);
  const [openMenu, setOpenMenu] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [mutationLoading, setMutationLoading] = useState(false);
  const [notice, setNotice] = useState(null);
  const filters = {
    page,
    page_size: pageSize,
    search,
    language,
    conversation_status: status,
    started_from: dateFrom ? `${dateFrom}T00:00:00Z` : "",
    started_to: dateTo ? `${dateTo}T23:59:59Z` : "",
    sort_order: sortOrder,
  };
  const { data, loading, refreshing, error } = useAdminTablePage(
    user,
    "conversations",
    adminConversationPage,
    filters,
    revision + refresh,
  );

  useEffect(() => {
    let refreshTimer;
    const stop = subscribeToAdminConversationChanges(
      () => {
        // Collapse a burst of related conversation/message writes into one
        // paginated summary refresh while leaving current rows mounted.
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(() => {
          setRefresh((value) => value + 1);
        }, 250);
      },
      // Real-time hints are optional. The authenticated paginated API remains
      // authoritative and already exposes actionable refresh failures.
      () => {},
    );
    return () => {
      window.clearTimeout(refreshTimer);
      stop();
    };
  }, [user.uid]);

  useEffect(() => {
    function closeMenu(event) {
      if (
        event.key === "Escape"
        || !event.target.closest?.(".conversation-actions")
      ) {
        setOpenMenu(null);
      }
    }
    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeMenu);
    return () => {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeMenu);
    };
  }, []);

  useEffect(() => {
    function closeDialog(event) {
      if (event.key === "Escape" && !mutationLoading) setDeleting(null);
    }
    if (deleting) document.addEventListener("keydown", closeDialog);
    return () => document.removeEventListener("keydown", closeDialog);
  }, [deleting, mutationLoading]);

  async function confirmDelete() {
    if (!deleting || mutationLoading) return;
    setMutationLoading(true);
    setNotice(null);
    try {
      await adminDeleteConversation(user, deleting.conversation_id);
      tablePageCache.clear();
      setRefresh((value) => value + 1);
      setDeleting(null);
      setNotice({
        type: "success",
        text: "Conversation deleted permanently.",
      });
    } catch (reason) {
      setNotice({ type: "error", text: reason.message });
    } finally {
      setMutationLoading(false);
    }
  }
  function reset(setter, value) {
    setter(value);
    setPage(1);
  }
  const conversations = data?.items || [];
  return (
    <>
      <AdminRefreshIndicator active={refreshing} />
      <section className="admin-card table-management">
      <div className="admin-toolbar conversation-filter-toolbar">
        <label className="admin-search-field">
          <span className="sr-only">Search conversations</span>
          <input
            type="search"
            placeholder="Search conversations..."
            value={search}
            onChange={(event) => reset(setSearch, event.target.value)}
          />
        </label>
        <label>
          Language
          <select
            value={language}
            onChange={(event) => reset(setLanguage, event.target.value)}
          >
            <option value="">All languages</option>
            <option value="en">English</option>
            <option value="sw">Kiswahili</option>
            <option value="mixed">Mixed English/Kiswahili</option>
          </select>
        </label>
        <label>
          Status
          <select
            value={status}
            onChange={(event) => reset(setStatus, event.target.value)}
          >
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="ended">Ended</option>
          </select>
        </label>
        <label>
          Start date
          <input
            type="date"
            value={dateFrom}
            onChange={(event) => reset(setDateFrom, event.target.value)}
          />
        </label>
        <label>
          End date
          <input
            type="date"
            value={dateTo}
            onChange={(event) => reset(setDateTo, event.target.value)}
          />
        </label>
        <label>
          Started
          <select
            value={sortOrder}
            onChange={(event) => reset(setSortOrder, event.target.value)}
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
          </select>
        </label>
      </div>
      <AdminNotice type="error" message={error} />
      <AdminNotice
        notice={notice}
        onDismiss={() => setNotice(null)}
      />
      <div className="admin-table-scroll">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Conversation</th>
              <th>User email</th>
              <th>Language</th>
              <th>Started</th>
              <th>Messages</th>
              <th>User messages</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <TableLoadingRows columns={8} />
            ) : (
              conversations.map((item) => (
                <tr key={item.conversation_id}>
                  <td>
                    <strong className="conversation-title">
                      {item.title || "Untitled conversation"}
                    </strong>
                  </td>
                  <td>{item.user_email || "Unavailable"}</td>
                  <td>{languageLabel(item.language)}</td>
                  <td>{formatDateTime(item.started_at)}</td>
                  <td>{item.message_count}</td>
                  <td>{item.customer_message_count}</td>
                  <td>
                    <span className={`status-badge ${item.status}`}>
                      {readableEventName(item.status)}
                    </span>
                  </td>
                  <td>
                    <div className="conversation-actions">
                      <button
                        className="conversation-inspect-button"
                        type="button"
                        onClick={() => onSelect(item.conversation_id)}
                      >
                        Inspect
                      </button>
                      <button
                        className="admin-overflow-action conversation-more-button"
                        type="button"
                        aria-label={`More actions for ${item.title}`}
                        aria-expanded={openMenu === item.conversation_id}
                        onClick={() => setOpenMenu((current) => (
                          current === item.conversation_id
                            ? null
                            : item.conversation_id
                        ))}
                      >
                        <EllipsisVertical aria-hidden="true" />
                      </button>
                      {openMenu === item.conversation_id && (
                        <div className="conversation-action-menu">
                          <button
                            type="button"
                            onClick={() => {
                              setOpenMenu(null);
                              setDeleting(item);
                            }}
                          >
                            Delete conversation
                          </button>
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
            {!loading && !conversations.length && (
              <tr>
                <td colSpan="8" className="admin-empty">
                  No conversations match the selected filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        page={page}
        pageSize={pageSize}
        total={data?.total || 0}
        noun="conversations"
        onPage={setPage}
        onPageSize={(value) => {
          setPageSize(value);
          setPage(1);
        }}
      />
      {deleting && (
        <div
          className="conversation-delete-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (
              event.target === event.currentTarget
              && !mutationLoading
            ) {
              setDeleting(null);
            }
          }}
        >
          <section
            className="conversation-delete-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-conversation-title"
          >
            <h2 id="delete-conversation-title">Delete conversation?</h2>
            <p>
              Are you sure you want to permanently delete this conversation
              and its messages? This action cannot be undone.
            </p>
            <dl>
              <div><dt>Title</dt><dd>{deleting.title}</dd></div>
              <div>
                <dt>Passenger</dt>
                <dd>{deleting.user_email || "Unavailable"}</dd>
              </div>
              <div>
                <dt>Started</dt>
                <dd>{formatDateTime(deleting.started_at)}</dd>
              </div>
              <div><dt>Messages</dt><dd>{deleting.message_count}</dd></div>
            </dl>
            <div className="conversation-delete-actions">
              <button
                className="button secondary"
                type="button"
                disabled={mutationLoading}
                onClick={() => setDeleting(null)}
              >
                Cancel
              </button>
              <button
                className="button danger"
                type="button"
                disabled={mutationLoading}
                onClick={confirmDelete}
              >
                {mutationLoading ? "Deleting..." : "Delete conversation"}
              </button>
            </div>
          </section>
        </div>
      )}
      </section>
    </>
  );
}

function ConversationModal({ detail, onClose }) {
  const closeButtonRef = useRef(null);
  const { conversation, messages } = detail;

  useEffect(() => {
    const previous = document.body.style.overflow;
    const escape = (event) => event.key === "Escape" && onClose();
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", escape);
    closeButtonRef.current?.focus();
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", escape);
    };
  }, [onClose]);

  return (
    <div className="admin-modal-backdrop" role="presentation" onClick={onClose}>
      <section
        aria-labelledby="conversation-details-heading"
        aria-modal="true"
        className="admin-modal conversation-details-modal"
        role="dialog"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="conversation-details-header">
          <div>
            <h2 id="conversation-details-heading">Conversation details</h2>
            <p>{conversation.title}</p>
          </div>
          <button
            ref={closeButtonRef}
            aria-label="Close conversation details"
            className="icon-button"
            type="button"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <dl className="conversation-summary">
          <div>
            <dt>User email</dt>
            <dd>{detail.user_email || "Not available"}</dd>
          </div>
          <div>
            <dt>Language</dt>
            <dd>{languageLabel(conversation.language)}</dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd>{formatDateTime(conversation.started_at)}</dd>
          </div>
          <div>
            <dt>Messages</dt>
            <dd>{messages.length}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>
              <span className={`status-badge ${conversation.status}`}>
                {readableEventName(conversation.status)}
              </span>
            </dd>
          </div>
        </dl>
        <section className="conversation-feedback" aria-labelledby="feedback-title">
          <h3 id="feedback-title">Passenger feedback</h3>
          {detail.feedback ? (
            <div className="conversation-feedback-content">
              <p
                aria-label={`${detail.feedback.rating} out of 5 stars`}
                className="conversation-feedback-rating"
              >
                <span className="selected-stars">
                  {"★".repeat(detail.feedback.rating)}
                </span>
                <span className="unselected-stars">
                  {"★".repeat(5 - detail.feedback.rating)}
                </span>
                <strong>{detail.feedback.rating}/5</strong>
              </p>
              <p className="conversation-feedback-comment">
                {detail.feedback.comment || "No written comment."}
              </p>
              <time dateTime={detail.feedback.updated_at}>
                Updated {formatDateTime(detail.feedback.updated_at)}
              </time>
            </div>
          ) : (
            <p className="conversation-feedback-empty">
              No feedback submitted.
            </p>
          )}
        </section>
        <div className="admin-transcript conversation-message-scroll">
          {messages.map((message) => (
            <article
              className={`admin-transcript-message ${message.sender_type}`}
              key={message.message_id}
            >
              <small>
                {message.sender_type === "customer" ? "Passenger" : "Chatbot"}
                {" · "}{formatDateTime(message.timestamp)}
              </small>
              {message.sender_type === "chatbot" ? (
                <FormattedAnswer text={message.message_text} />
              ) : (
                <p>{message.message_text}</p>
              )}
              {message.model_id && (
                <small className="admin-message-model">
                  Model: {message.model_id}
                </small>
              )}
              {message.retrieved_chunks?.length > 0 && (
                <div className="admin-source-list">
                  <strong>Sources used</strong>
                  {message.retrieved_chunks.map((source) => (
                    <span key={source.message_chunk_id}>
                      {source.citation_id} ·{" "}
                      {source.source_title || source.chunk_id} (
                      {Math.round(source.similarity_score * 100)}% similar)
                    </span>
                  ))}
                </div>
              )}
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
export { ConversationModal };
export default ConversationTable;
