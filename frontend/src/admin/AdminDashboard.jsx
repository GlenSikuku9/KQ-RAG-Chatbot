import { useEffect, useState } from "react";

import {
  adminConversation,
  adminConversations,
  adminAnalytics,
  adminDeleteDocument,
  adminDocuments,
  adminProcessDocument,
  adminUpdateDocument,
  adminUpdateRole,
  adminUpdateStatus,
  adminUploadDocument,
  adminUsers,
  getPassengerThemeForAdmin,
  savePassengerTheme,
} from "../api";
import { FormattedAnswer } from "../components/FormattedAnswer";

const pages = ["Overview", "Documents", "Users", "Conversations", "Analytics", "Settings"];

// Use one unambiguous day/month/year format throughout the administrator UI.
function formatDate(value) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(value));
}

function formatChartLabel(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return `${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`;
}

function formatChartTooltip(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function AdminDashboard({ user, profile }) {
  const [page, setPage] = useState("Documents");
  const [documents, setDocuments] = useState([]);
  const [users, setUsers] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [selectedConversation, setSelectedConversation] = useState(null);
  const [notice, setNotice] = useState(null);
  const [loading, setLoading] = useState(true);
  const [analytics, setAnalytics] = useState(null);
  const [periodDays, setPeriodDays] = useState(30);
  const [theme, setTheme] = useState(null);

  async function reload() {
    setLoading(true);
    setNotice(null);
    try {
      if (page === "Documents") setDocuments(await adminDocuments(user));
      if (page === "Users") setUsers(await adminUsers(user));
      if (page === "Conversations") setConversations(await adminConversations(user));
      if (page === "Overview" || page === "Analytics") setAnalytics(await adminAnalytics(user, periodDays));
      if (page === "Settings") setTheme(await getPassengerThemeForAdmin(user));
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); }, [page, periodDays]);

  async function upload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      await adminUploadDocument(user, file);
      setNotice({ type: "success", text: "Document uploaded and processed." });
      await reload();
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    }
    event.target.value = "";
  }

  return (
    <main className="admin-shell">
      <aside className="admin-sidebar">
        <p className="admin-mark"><span>KQ</span> ADMIN</p>
        <p className="admin-caption">Knowledge and access management</p>
        <nav aria-label="Administrator navigation">
          {pages.map((item) => <button className={`admin-nav-item ${page === item ? "active" : ""}`} key={item} type="button" onClick={() => setPage(item)}>{item}</button>)}
        </nav>
        <p className="admin-sidebar-note">Signed in as {profile.name || profile.email || "administrator"}.</p>
      </aside>

      <section className="admin-content">
        <header className="admin-header">
          <div><p className="eyebrow">ADMINISTRATION</p><h1>{page}</h1>{(page === "Overview" || page === "Analytics") && <p className="admin-header-description">{page === "Analytics" ? "Insights into chatbot usage, customer queries and AI performance." : "A concise summary of current chatbot activity."}</p>}</div>
          {(page === "Overview" || page === "Analytics") && <label className="admin-period">Reporting period<select value={periodDays} onChange={(event) => setPeriodDays(Number(event.target.value))}><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></label>}
          {page === "Documents" && <label className="button primary admin-upload">Upload document<input type="file" accept=".docx" onChange={upload} /></label>}
        </header>
        {notice && <p className={`notice ${notice.type === "error" ? "error" : "success"}`} role="status">{notice.text}</p>}
        {loading ? <p className="admin-empty">Loading administrator data…</p> : <>
          {page === "Overview" && <Overview analytics={analytics} />}
          {page === "Analytics" && <Analytics analytics={analytics} />}
          {page === "Settings" && <ThemeSettings user={user} theme={theme} setTheme={setTheme} onSaved={(message) => setNotice({ type: "success", text: message })} />}
          {page === "Documents" && <DocumentTable documents={documents} user={user} onChanged={reload} />}
          {page === "Users" && <UserTable users={users} user={user} onChanged={reload} />}
          {page === "Conversations" && <ConversationTable conversations={conversations} onSelect={async (id) => setSelectedConversation(await adminConversation(user, id))} />}
        </>}
        {selectedConversation && <ConversationModal detail={selectedConversation} onClose={() => setSelectedConversation(null)} />}
      </section>
    </main>
  );
}

function ThemeSettings({ user, theme, setTheme, onSaved }) {
  if (!theme) return <p className="admin-empty">Loading passenger appearance settings…</p>;
  const labels = { primary: "Primary colour", secondary: "Secondary colour", accent: "Accent colour", background: "Background colour", panel: "Chat panel colour", user_message: "Passenger message colour", assistant_message: "Assistant message colour", text: "Text colour", secondary_text: "Secondary text colour", border: "Border colour" };
  async function save() { await savePassengerTheme(user, theme); onSaved("Passenger theme saved and published."); }
  return <div className="theme-settings"><div className="theme-settings-form"><p className="admin-metric-note">These colours affect the passenger chatbot only. Values must be six-digit hexadecimal colours.</p>{Object.entries(labels).map(([key, label]) => <label className="theme-field" key={key}>{label}<span><input type="color" value={theme[key]} onChange={(event) => setTheme({ ...theme, [key]: event.target.value })} /><input value={theme[key]} pattern="#[0-9a-fA-F]{6}" onChange={(event) => setTheme({ ...theme, [key]: event.target.value })} /></span></label>)}<div className="theme-actions"><button className="button primary" type="button" onClick={save}>Save changes</button><button className="button secondary" type="button" onClick={() => setTheme({ primary: "#0b2e6f", secondary: "#16458f", accent: "#f5b335", background: "#f4f7fa", panel: "#ffffff", user_message: "#d71920", assistant_message: "#f1f5f9", text: "#172033", secondary_text: "#5f6b7b", border: "#dbe2ea" })}>Reset to default</button></div></div><div className="theme-preview" style={{ "--preview-primary": theme.primary, "--preview-panel": theme.panel, "--preview-user": theme.user_message, "--preview-assistant": theme.assistant_message, "--preview-text": theme.text }}><strong>KQ Support preview</strong><h3>How can we help?</h3><button type="button">Suggested question</button><p className="preview-user">What is my baggage allowance?</p><p className="preview-assistant">Your baggage allowance depends on your cabin class.</p><div className="preview-input">Enter your inquiry… <span>↑</span></div></div></div>;
}

function MetricCard({ label, value, hint }) {
  return <article className="admin-metric-card"><p>{label}</p><strong>{value}</strong><small>{hint}</small></article>;
}

function Overview({ analytics }) {
  if (!analytics) return <p className="admin-empty">No overview data is available for this period.</p>;
  return <div className="admin-overview"><div className="admin-metric-grid">
    <MetricCard label="Registered users" value={analytics.totals.users} hint="Current Firebase accounts" />
    <MetricCard label="Conversations" value={analytics.totals.conversations} hint={`Started in the last ${analytics.period_days} days`} />
    <MetricCard label="Active documents" value={analytics.totals.active_documents} hint={`${analytics.totals.documents} document records total`} />
    <MetricCard label="Average satisfaction" value={analytics.feedback.count ? `${analytics.feedback.average_rating}/5` : "No ratings"} hint={`${analytics.feedback.count} ratings in period`} />
  </div><AnalyticsPanels analytics={analytics} compact /><div className="admin-overview-lower"><RecentConversations items={analytics.recent_conversations} /><RecentActivity items={analytics.recent_activity} /></div></div>;
}

function Analytics({ analytics }) {
  if (!analytics) return <p className="admin-empty">No analytics data is available for this period.</p>;
  return <AnalyticsPanels analytics={analytics} />;
}

function AnalyticsPanels({ analytics, compact = false }) {
  const maxActivity = Math.max(1, ...analytics.conversation_activity.map((item) => item.count));
  // Keep every daily bar, but reduce only visible labels as the range grows.
  const labelStride = analytics.period_days <= 7 ? 1 : analytics.period_days <= 30 ? 5 : 14;
  return <div className={`admin-analytics-grid ${compact ? "compact" : ""}`}>
    <section className="admin-analytics-card"><h2>Conversation activity</h2><p className="admin-metric-note">Conversations started during the last {analytics.period_days} days. Hover or focus a bar for the exact date and count.</p><div className="activity-chart">{analytics.conversation_activity.length ? analytics.conversation_activity.map((item, index) => <div className="activity-bar" key={item.date} tabIndex="0" aria-label={`${formatChartTooltip(item.date)}: ${item.count} conversation${item.count === 1 ? "" : "s"}`}><span className="activity-bar-fill" style={{ height: `${(item.count / maxActivity) * 100}%` }} /><span className="activity-tooltip"><strong>{formatChartTooltip(item.date)}</strong><br />{item.count} conversation{item.count === 1 ? "" : "s"}</span><small>{index % labelStride === 0 || index === analytics.conversation_activity.length - 1 ? formatChartLabel(item.date) : ""}</small></div>) : <p className="admin-empty">No conversations in this period.</p>}</div></section>
    <section className="admin-analytics-card"><h2>Language distribution</h2><MetricList values={labelLanguages(analytics.languages)} empty="No language data." /><h2>AI model usage</h2><MetricList values={analytics.models} empty="No model usage data." /></section>
    {!compact && <><section className="admin-analytics-card"><h2>Response quality and timing</h2><p>Completed responses: <strong>{analytics.performance.completed_count}</strong></p><p>Average response time: <strong>{analytics.performance.completed_count ? `${(analytics.performance.average_response_ms / 1000).toFixed(2)} seconds` : "No completed responses"}</strong></p><p>Feedback ratings: <strong>{analytics.feedback.count ? `${analytics.feedback.average_rating}/5` : "No ratings"}</strong></p></section><section className="admin-analytics-card"><h2>Fallbacks and failures</h2><MetricList values={analytics.responses} empty="No response outcomes recorded." /><MetricList values={analytics.failures} empty="No failures recorded." /></section></>}
    {!compact && <section className="admin-analytics-card"><h2>User satisfaction</h2><p>Average rating: <strong>{analytics.feedback.count ? `${analytics.feedback.average_rating}/5` : "No ratings"}</strong></p><p>Submitted ratings: <strong>{analytics.feedback.count}</strong></p><RatingDistribution values={analytics.rating_distribution} /></section>}
    {!compact && <section className="admin-analytics-card"><h2>Data availability</h2><p className="admin-metric-note">Query categories, RAG evaluation scores, and CSV export are not recorded by the current backend, so they are intentionally not estimated here.</p></section>}
  </div>;
}

function labelLanguages(values) {
  return Object.fromEntries(Object.entries(values || {}).map(([key, value]) => [languageLabel(key), value]));
}

function languageLabel(value) {
  return { en: "English", sw: "Kiswahili", mixed: "Mixed English/Kiswahili", unknown: "Unknown" }[value] || value;
}

function RecentConversations({ items }) {
  return <section className="admin-analytics-card"><h2>Recent conversations</h2>{items.length ? <div className="admin-recent-list">{items.map((item) => <div key={item.conversation_id}><strong>{item.first_message || "No customer message recorded"}</strong><small>{languageLabel(item.language)} · {item.message_count} messages · {formatDate(item.started_at)}</small></div>)}</div> : <p className="admin-metric-note">No conversations in this period.</p>}</section>;
}

function RecentActivity({ items }) {
  return <section className="admin-analytics-card"><h2>Recent system activity</h2>{items.length ? <div className="admin-recent-list">{items.map((item, index) => <div key={`${item.timestamp}-${index}`}><strong>{item.action}</strong><small>{item.target_type} · {formatDate(item.timestamp)}</small></div>)}</div> : <p className="admin-metric-note">No administrator activity recorded in this period.</p>}</section>;
}

function MetricList({ values, empty }) {
  const entries = Object.entries(values || {});
  return entries.length ? <ul className="admin-metric-list">{entries.map(([name, count]) => <li key={name}><span>{name}</span><strong>{count}</strong></li>)}</ul> : <p className="admin-metric-note">{empty}</p>;
}

function RatingDistribution({ values }) {
  const ratings = [5, 4, 3, 2, 1];
  if (!Object.keys(values || {}).length) return <p className="admin-metric-note">No rating distribution is available.</p>;
  return <div className="rating-distribution">{ratings.map((rating) => <div className="rating-row" key={rating}><span className="rating-stars" aria-label={`${rating} out of 5 stars`}>{"★".repeat(rating)}<span className="rating-stars-muted">{"★".repeat(5 - rating)}</span></span><span className="rating-label">{rating}/5</span><strong>{values[String(rating)] || 0}</strong></div>)}</div>;
}

function DocumentTable({ documents, user, onChanged }) {
  return <div className="admin-table-card"><table className="admin-table"><thead><tr><th>Filename</th><th>Category</th><th>Version</th><th>Uploaded</th><th>Status</th><th>Actions</th></tr></thead><tbody>
    {documents.map((document) => <tr key={document.document_id}><td><strong>{document.filename}</strong><small>{document.title}</small></td><td>{document.category}</td><td className="admin-version">{document.version || "—"}</td><td>{formatDate(document.uploaded_at)}</td><td><span className={`status-badge ${document.status}`}>{document.status}</span></td><td><button className="text-button" type="button" onClick={async () => { const title = window.prompt("Document title", document.title); if (title) { await adminUpdateDocument(user, document.document_id, title, document.category); onChanged(); } }}>Edit</button> <button className="text-button" type="button" onClick={async () => { await adminProcessDocument(user, document.document_id); onChanged(); }}>Process</button> <button className="text-button" type="button" onClick={async () => { if (window.confirm(`Remove ${document.filename}?`)) { await adminDeleteDocument(user, document.document_id); onChanged(); } }}>Delete</button></td></tr>)}
    {!documents.length && <tr><td colSpan="6" className="admin-empty">No documents have been published.</td></tr>}
  </tbody></table></div>;
}

function UserTable({ users, user, onChanged }) {
  return <div className="admin-table-card"><table className="admin-table"><thead><tr><th>User</th><th>Role</th><th>Verified</th><th>Status</th><th>Actions</th></tr></thead><tbody>
    {users.map((item) => <tr key={item.user_id}><td><strong>{item.name || "Unnamed user"}</strong><small>{item.email || item.user_id}</small></td><td><select value={item.role} disabled={item.user_id === user.uid} onChange={async (event) => { await adminUpdateRole(user, item.user_id, event.target.value); onChanged(); }}><option value="passenger">Passenger</option><option value="admin">Administrator</option></select></td><td>{item.email_verified ? "Yes" : "No"}</td><td>{item.disabled ? "Disabled" : "Active"}</td><td><button className="text-button" type="button" disabled={item.user_id === user.uid} onClick={async () => { if (window.confirm(`${item.disabled ? "Enable" : "Disable"} this account?`)) { await adminUpdateStatus(user, item.user_id, !item.disabled); onChanged(); } }}>{item.disabled ? "Enable" : "Disable"}</button></td></tr>)}
    {!users.length && <tr><td colSpan="5" className="admin-empty">No users found.</td></tr>}
  </tbody></table></div>;
}

function ConversationTable({ conversations, onSelect }) {
  return <div className="admin-table-card"><table className="admin-table"><thead><tr><th>Conversation</th><th>User</th><th>Language</th><th>Started</th><th>Status</th><th /></tr></thead><tbody>
    {conversations.map((item) => <tr key={item.conversation_id}><td><strong>{item.title}</strong><small>{item.conversation_id}</small></td><td>{item.user_id}</td><td>{item.language}</td><td>{formatDate(item.started_at)}</td><td><span className={`status-badge ${item.status}`}>{item.status}</span></td><td><button className="text-button" type="button" onClick={() => onSelect(item.conversation_id)}>Inspect</button></td></tr>)}
    {!conversations.length && <tr><td colSpan="6" className="admin-empty">No conversations found.</td></tr>}
  </tbody></table></div>;
}

function ConversationModal({ detail, onClose }) {
  return <div className="admin-modal-backdrop" role="presentation" onClick={onClose}><section className="admin-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}><header><h2>{detail.conversation.title}</h2><button className="icon-button" type="button" onClick={onClose}>×</button></header><div className="admin-transcript">{detail.messages.map((message) => <article className={`admin-transcript-message ${message.sender_type}`} key={message.message_id}><small>{message.sender_type} · {formatDate(message.timestamp)}</small>{message.sender_type === "chatbot" ? <FormattedAnswer text={message.message_text} /> : <p>{message.message_text}</p>}{message.model_id && <small>Model: {message.model_id}</small>}{message.retrieved_chunks?.length > 0 && <div className="admin-source-list"><strong>Sources used</strong>{message.retrieved_chunks.map((source) => <span key={source.message_chunk_id}>{source.citation_id} · {source.source_title || source.chunk_id} ({Math.round(source.similarity_score * 100)}% similar)</span>)}</div>}</article>)}</div></section></div>;
}
