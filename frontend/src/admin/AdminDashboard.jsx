import { useEffect, useState } from "react";

import {
  adminConversation,
  adminConversations,
  adminDeleteDocument,
  adminDocuments,
  adminProcessDocument,
  adminUpdateDocument,
  adminUpdateRole,
  adminUpdateStatus,
  adminUploadDocument,
  adminUsers,
} from "../api";
import { FormattedAnswer } from "../components/FormattedAnswer";

const pages = ["Documents", "Users", "Conversations"];

export function AdminDashboard({ user, profile }) {
  const [page, setPage] = useState("Documents");
  const [documents, setDocuments] = useState([]);
  const [users, setUsers] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [selectedConversation, setSelectedConversation] = useState(null);
  const [notice, setNotice] = useState(null);
  const [loading, setLoading] = useState(true);

  async function reload() {
    setLoading(true);
    setNotice(null);
    try {
      if (page === "Documents") setDocuments(await adminDocuments(user));
      if (page === "Users") setUsers(await adminUsers(user));
      if (page === "Conversations") setConversations(await adminConversations(user));
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); }, [page]);

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
          <div><p className="eyebrow">ADMINISTRATION</p><h1>{page}</h1></div>
          {page === "Documents" && <label className="button primary admin-upload">Upload document<input type="file" accept=".docx" onChange={upload} /></label>}
        </header>
        {notice && <p className={`notice ${notice.type === "error" ? "error" : "success"}`} role="status">{notice.text}</p>}
        {loading ? <p className="admin-empty">Loading administrator data…</p> : <>
          {page === "Documents" && <DocumentTable documents={documents} user={user} onChanged={reload} />}
          {page === "Users" && <UserTable users={users} user={user} onChanged={reload} />}
          {page === "Conversations" && <ConversationTable conversations={conversations} onSelect={async (id) => setSelectedConversation(await adminConversation(user, id))} />}
        </>}
        {selectedConversation && <ConversationModal detail={selectedConversation} onClose={() => setSelectedConversation(null)} />}
      </section>
    </main>
  );
}

function DocumentTable({ documents, user, onChanged }) {
  return <div className="admin-table-card"><table className="admin-table"><thead><tr><th>Filename</th><th>Category</th><th>Version</th><th>Uploaded</th><th>Status</th><th>Actions</th></tr></thead><tbody>
    {documents.map((document) => <tr key={document.document_id}><td><strong>{document.filename}</strong><small>{document.title}</small></td><td>{document.category}</td><td className="admin-version">{document.version || "—"}</td><td>{new Date(document.uploaded_at).toLocaleString()}</td><td><span className={`status-badge ${document.status}`}>{document.status}</span></td><td><button className="text-button" type="button" onClick={async () => { const title = window.prompt("Document title", document.title); if (title) { await adminUpdateDocument(user, document.document_id, title, document.category); onChanged(); } }}>Edit</button> <button className="text-button" type="button" onClick={async () => { await adminProcessDocument(user, document.document_id); onChanged(); }}>Process</button> <button className="text-button" type="button" onClick={async () => { if (window.confirm(`Remove ${document.filename}?`)) { await adminDeleteDocument(user, document.document_id); onChanged(); } }}>Delete</button></td></tr>)}
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
    {conversations.map((item) => <tr key={item.conversation_id}><td><strong>{item.title}</strong><small>{item.conversation_id}</small></td><td>{item.user_id}</td><td>{item.language}</td><td>{new Date(item.started_at).toLocaleString()}</td><td><span className={`status-badge ${item.status}`}>{item.status}</span></td><td><button className="text-button" type="button" onClick={() => onSelect(item.conversation_id)}>Inspect</button></td></tr>)}
    {!conversations.length && <tr><td colSpan="6" className="admin-empty">No conversations found.</td></tr>}
  </tbody></table></div>;
}

function ConversationModal({ detail, onClose }) {
  return <div className="admin-modal-backdrop" role="presentation" onClick={onClose}><section className="admin-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}><header><h2>{detail.conversation.title}</h2><button className="icon-button" type="button" onClick={onClose}>×</button></header><div className="admin-transcript">{detail.messages.map((message) => <article className={`admin-transcript-message ${message.sender_type}`} key={message.message_id}><small>{message.sender_type} · {new Date(message.timestamp).toLocaleString()}</small>{message.sender_type === "chatbot" ? <FormattedAnswer text={message.message_text} /> : <p>{message.message_text}</p>}{message.model_id && <small>Model: {message.model_id}</small>}{message.retrieved_chunks?.length > 0 && <div className="admin-source-list"><strong>Sources used</strong>{message.retrieved_chunks.map((source) => <span key={source.message_chunk_id}>{source.citation_id} · {source.source_title || source.chunk_id} ({Math.round(source.similarity_score * 100)}% similar)</span>)}</div>}</article>)}</div></section></div>;
}
