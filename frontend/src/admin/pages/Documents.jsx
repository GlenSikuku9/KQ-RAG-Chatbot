import { useEffect, useRef, useState } from "react";
import { EllipsisVertical } from "lucide-react";
import {
  adminDeleteDocument,
  adminDocumentContent,
  adminDocumentPage,
  adminDocumentVersions,
  adminProcessDocument,
  adminRestoreDocumentVersion,
  adminSaveDocumentContent,
  adminUpdateDocument,
} from "../../api";
import { Pagination, TableLoadingRows } from "../components/Pagination";
import { AdminNotice } from "../components/AdminNotice";
import { AdminRefreshIndicator } from "../components/AdminRefreshIndicator";
import { useAdminTablePage } from "../hooks/useAdminTablePage";
import { formatDateTime, readableEventName } from "../utils";
import "../styles/Documents.css";

function Modal({ children, label, onClose, className = "" }) {
  useEffect(() => {
    const previous = document.body.style.overflow;
    const escape = (event) => event.key === "Escape" && onClose();
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", escape);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", escape);
    };
  }, [onClose]);

  return (
    <div
      className="admin-modal-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section
        aria-label={label}
        aria-modal="true"
        className={`admin-modal ${className}`}
        role="dialog"
      >
        {children}
      </section>
    </div>
  );
}

function Documents({ user, revision }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [sortOrder, setSortOrder] = useState("newest");
  const [refresh, setRefresh] = useState(0);
  const [menu, setMenu] = useState(null);
  const [metadata, setMetadata] = useState(null);
  const [deletion, setDeletion] = useState(null);
  const [editor, setEditor] = useState(null);
  const [discard, setDiscard] = useState(false);
  const [versions, setVersions] = useState(null);
  const [busyIds, setBusyIds] = useState(new Set());
  const [notice, setNotice] = useState(null);
  const textareaRef = useRef(null);
  const menuRef = useRef(null);
  const filters = {
    page,
    page_size: pageSize,
    search,
    category,
    processing_status: status,
    sort_order: sortOrder,
  };
  const { data, loading, refreshing, error } = useAdminTablePage(
    user,
    "documents",
    adminDocumentPage,
    filters,
    revision + refresh,
  );

  useEffect(() => {
    if (!menu) return undefined;
    const outside = (event) => {
      if (!menuRef.current?.contains(event.target)) setMenu(null);
    };
    const escape = (event) => event.key === "Escape" && setMenu(null);
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    window.addEventListener("scroll", outside, true);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("scroll", outside, true);
    };
  }, [menu]);

  function reset(setter, value) {
    setter(value);
    setPage(1);
  }

  function feedback(type, text) {
    setNotice({ type, text });
  }

  async function openEditor(documentRecord) {
    setMenu(null);
    try {
      const result = await adminDocumentContent(
        user,
        documentRecord.document_id,
      );
      setEditor({
        document: documentRecord,
        content: result.content,
        original: result.content,
        warning: result.warning,
        find: "",
        match: -1,
        saving: false,
        message: "",
      });
    } catch (reason) {
      feedback("error", reason.message);
    }
  }

  function closeEditor() {
    if (editor.content !== editor.original) setDiscard(true);
    else setEditor(null);
  }

  function find(direction) {
    const query = editor.find.trim().toLowerCase();
    if (!query) return;
    const haystack = editor.content.toLowerCase();
    const matches = [];
    let offset = 0;
    while ((offset = haystack.indexOf(query, offset)) !== -1) {
      matches.push(offset);
      offset += query.length;
    }
    if (!matches.length) {
      setEditor({ ...editor, message: "No matches found." });
      return;
    }
    const match = (editor.match + direction + matches.length) % matches.length;
    textareaRef.current?.focus();
    textareaRef.current?.setSelectionRange(
      matches[match],
      matches[match] + query.length,
    );
    setEditor({
      ...editor,
      match,
      message: `Match ${match + 1} of ${matches.length}`,
    });
  }

  async function saveContent() {
    if (editor.saving || editor.content === editor.original) return;
    setEditor({ ...editor, saving: true, message: "" });
    try {
      await adminSaveDocumentContent(
        user,
        editor.document.document_id,
        editor.content,
      );
      setEditor(null);
      setRefresh((value) => value + 1);
      feedback("success", "Changes were saved and reprocessed successfully.");
    } catch (reason) {
      // Preserve the administrator's text when indexing or saving fails.
      setEditor((current) => ({
        ...current,
        saving: false,
        message: reason.message,
      }));
    }
  }

  async function saveMetadata() {
    const title = metadata.title.trim();
    const nextCategory = metadata.category.trim();
    if (!title || !nextCategory || metadata.saving) return;
    setMetadata({ ...metadata, saving: true, error: "" });
    try {
      await adminUpdateDocument(
        user,
        metadata.document.document_id,
        title,
        nextCategory,
      );
      setMetadata(null);
      setRefresh((value) => value + 1);
      feedback("success", "Document metadata was updated.");
    } catch (reason) {
      setMetadata((current) => ({
        ...current,
        saving: false,
        error: reason.message,
      }));
    }
  }

  async function processDocument(documentRecord) {
    setMenu(null);
    if (busyIds.has(documentRecord.document_id)) return;
    setBusyIds((current) => new Set(current).add(documentRecord.document_id));
    try {
      await adminProcessDocument(user, documentRecord.document_id);
      setRefresh((value) => value + 1);
      feedback("success", `${documentRecord.filename} was processed.`);
    } catch (reason) {
      feedback("error", reason.message);
    } finally {
      setBusyIds((current) => {
        const next = new Set(current);
        next.delete(documentRecord.document_id);
        return next;
      });
    }
  }

  async function removeDocument() {
    if (deletion.deleting) return;
    setDeletion({ ...deletion, deleting: true, error: "" });
    try {
      await adminDeleteDocument(user, deletion.document.document_id);
      const filename = deletion.document.filename;
      setDeletion(null);
      setRefresh((value) => value + 1);
      feedback("success", `${filename} was removed from the knowledge base.`);
    } catch (reason) {
      setDeletion((current) => ({
        ...current,
        deleting: false,
        error: reason.message,
      }));
    }
  }

  async function openVersions(documentRecord) {
    try {
      setVersions({
        document: documentRecord,
        items: await adminDocumentVersions(user, documentRecord.document_id),
      });
    } catch (reason) {
      feedback("error", reason.message);
    }
  }

  async function restoreVersion(version) {
    if (!window.confirm(`Restore ${version.version}? This creates a new version.`))
      return;
    try {
      await adminRestoreDocumentVersion(
        user,
        versions.document.document_id,
        version.version_number,
      );
      setVersions(null);
      setRefresh((value) => value + 1);
    } catch (reason) {
      feedback("error", reason.message);
    }
  }

  function openMenu(event, documentRecord) {
    const bounds = event.currentTarget.getBoundingClientRect();
    setMenu({
      document: documentRecord,
      top: bounds.bottom + 6,
      right: Math.max(12, window.innerWidth - bounds.right),
    });
  }

  const items = data?.items || [];
  return (
    <>
      <AdminNotice
        notice={notice}
        onDismiss={() => setNotice(null)}
      />
      <AdminRefreshIndicator active={refreshing} />
      <section className="admin-card table-management">
        <div className="admin-toolbar table-filter-toolbar">
          <label className="admin-search-field">
            <span className="sr-only">Search documents</span>
            <input
              type="search"
              placeholder="Search documents..."
              value={search}
              onChange={(event) => reset(setSearch, event.target.value)}
            />
          </label>
          <label>
            Category
            <select
              value={category}
              onChange={(event) => reset(setCategory, event.target.value)}
            >
              <option value="">All categories</option>
              {(data?.categories || []).map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Processing status
            <select
              value={status}
              onChange={(event) => reset(setStatus, event.target.value)}
            >
              <option value="">All statuses</option>
              {(data?.statuses || []).map((value) => (
                <option key={value} value={value}>
                  {readableEventName(value)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Upload date
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
        <div className="admin-table-scroll">
          <table className="admin-table documents-table">
            <thead>
              <tr>
                <th>Filename</th><th>Category</th><th>Version</th>
                <th>Uploaded</th><th>Uploaded by</th><th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <TableLoadingRows columns={7} />
              ) : items.map((item) => {
                const processing = busyIds.has(item.document_id);
                return (
                  <tr key={item.document_id}>
                    <td className="document-name-cell">
                      <strong title={item.filename}>{item.filename}</strong>
                      <small>{item.title}</small>
                    </td>
                    <td>{item.category}</td>
                    <td>
                      <strong>{item.version || "v1"}</strong>
                      <button
                        className="text-button version-history-button"
                        type="button"
                        onClick={() => openVersions(item)}
                      >
                        History
                      </button>
                    </td>
                    <td>{formatDateTime(item.uploaded_at)}</td>
                    <td>{item.uploaded_by || "Not available"}</td>
                    <td>
                      <span className={`status-badge ${
                        processing ? "processing" : item.status
                      }`}>
                        {processing ? "Processing" : readableEventName(item.status)}
                      </span>
                    </td>
                    <td>
                      <button
                        aria-haspopup="menu"
                        aria-label={`Actions for ${item.filename}`}
                        className="admin-overflow-action document-actions-trigger"
                        disabled={processing}
                        type="button"
                        onClick={(event) => openMenu(event, item)}
                      >
                        <EllipsisVertical aria-hidden="true" />
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!loading && !items.length && (
                <tr><td className="admin-empty" colSpan="7">No documents match the selected filters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <Pagination
          noun="documents"
          page={page}
          pageSize={pageSize}
          total={data?.total || 0}
          onPage={setPage}
          onPageSize={(value) => { setPageSize(value); setPage(1); }}
        />
      </section>

      {menu && (
        <div
          ref={menuRef}
          className="document-actions-menu"
          role="menu"
          style={{ top: menu.top, right: menu.right }}
        >
          <button role="menuitem" type="button" onClick={() => openEditor(menu.document)}>Edit content</button>
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              setMetadata({
                document: menu.document,
                title: menu.document.title,
                category: menu.document.category,
                saving: false,
                error: "",
              });
              setMenu(null);
            }}
          >
            Edit metadata
          </button>
          <button role="menuitem" type="button" onClick={() => processDocument(menu.document)}>
            {menu.document.status === "processed" ? "Reprocess" : "Process"}
          </button>
          <button
            className="danger"
            role="menuitem"
            type="button"
            onClick={() => {
              setDeletion({ document: menu.document, deleting: false, error: "" });
              setMenu(null);
            }}
          >
            Delete
          </button>
        </div>
      )}

      {metadata && (
        <Modal label="Edit document metadata" onClose={() => !metadata.saving && setMetadata(null)}>
          <header><div><h2>Edit metadata</h2><p>{metadata.document.filename}</p></div></header>
          <div className="document-form">
            <label>
              Document title
              <input
                autoFocus
                value={metadata.title}
                onChange={(event) => setMetadata({
                  ...metadata,
                  title: event.target.value,
                })}
              />
            </label>
            <label>
              Document category
              <input
                value={metadata.category}
                onChange={(event) => setMetadata({
                  ...metadata,
                  category: event.target.value,
                })}
              />
            </label>
          </div>
          <AdminNotice
            type="error"
            message={metadata.error}
            onDismiss={() => setMetadata((current) => ({
              ...current,
              error: null,
            }))}
          />
          <div className="document-editor-actions">
            <button
              className="button secondary"
              disabled={metadata.saving}
              type="button"
              onClick={() => setMetadata(null)}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={
                metadata.saving
                || !metadata.title.trim()
                || !metadata.category.trim()
              }
              type="button"
              onClick={saveMetadata}
            >
              {metadata.saving ? "Saving…" : "Save changes"}
            </button>
          </div>
        </Modal>
      )}

      {deletion && (
        <Modal label="Delete document" onClose={() => !deletion.deleting && setDeletion(null)}>
          <header><h2>Delete document?</h2></header>
          <p>
            <strong>{deletion.document.filename}</strong>
            {" will be removed from the knowledge base according to the "}
            existing backend deletion and index cleanup process.
          </p>
          <AdminNotice
            type="error"
            message={deletion.error}
            onDismiss={() => setDeletion((current) => ({
              ...current,
              error: null,
            }))}
          />
          <div className="document-editor-actions">
            <button
              className="button secondary"
              disabled={deletion.deleting}
              type="button"
              onClick={() => setDeletion(null)}
            >
              Cancel
            </button>
            <button
              className="button danger-button"
              disabled={deletion.deleting}
              type="button"
              onClick={removeDocument}
            >
              {deletion.deleting ? "Deleting…" : "Delete"}
            </button>
          </div>
        </Modal>
      )}

      {editor && (
        <Modal className="document-editor" label="Edit document content" onClose={closeEditor}>
          <header>
            <div>
              <h2>
                Edit {editor.document.filename}
                <span className="editor-version">
                  {editor.document.version || "v1"}
                </span>
              </h2>
              <p>Edit extracted document content. Changes will be saved and re-indexed for chatbot retrieval.</p>
            </div>
            <button aria-label="Close editor" className="icon-button" type="button" onClick={closeEditor}>×</button>
          </header>
          <AdminNotice
            type="info"
            message={editor.warning}
            onDismiss={() => setEditor((current) => ({
              ...current,
              warning: null,
            }))}
          />
          <div className="document-find">
            <input
              aria-label="Find in document"
              placeholder="Find in document"
              type="search"
              value={editor.find}
              onChange={(event) => setEditor({ ...editor, find: event.target.value, match: -1, message: "" })}
              onKeyDown={(event) => {
                if (event.key === "Enter") { event.preventDefault(); find(event.shiftKey ? -1 : 1); }
              }}
            />
            <button className="button secondary compact" disabled={!editor.find.trim()} type="button" onClick={() => find(-1)}>Previous</button>
            <button className="button secondary compact" disabled={!editor.find.trim()} type="button" onClick={() => find(1)}>Next</button>
          </div>
          <textarea ref={textareaRef} className="document-editor-textarea" value={editor.content} onChange={(event) => setEditor({ ...editor, content: event.target.value, message: "" })} />
          {editor.message && (
            editor.message.startsWith("Match") ? (
              <p className="find-status">{editor.message}</p>
            ) : (
              <AdminNotice
                type="error"
                message={editor.message}
                onDismiss={() => setEditor((current) => ({
                  ...current,
                  message: "",
                }))}
              />
            )
          )}
          <div className="document-editor-actions">
            <button className="button secondary" disabled={editor.saving} type="button" onClick={closeEditor}>Cancel</button>
            <button className="button primary" disabled={editor.saving || !editor.content.trim() || editor.content === editor.original} type="button" onClick={saveContent}>{editor.saving ? "Saving and reprocessing…" : "Save changes & reprocess"}</button>
          </div>
        </Modal>
      )}

      {discard && (
        <Modal label="Discard unsaved changes" onClose={() => setDiscard(false)}>
          <header><h2>Discard unsaved changes?</h2></header>
          <p>Your document edits have not been saved.</p>
          <div className="document-editor-actions">
            <button className="button secondary" type="button" onClick={() => setDiscard(false)}>Keep editing</button>
            <button className="button danger-button" type="button" onClick={() => { setDiscard(false); setEditor(null); }}>Discard changes</button>
          </div>
        </Modal>
      )}

      {versions && (
        <Modal label="Version history" onClose={() => setVersions(null)}>
          <header><div><h2>Version history</h2><p>{versions.document.filename}</p></div></header>
          <div className="version-history-list">
            {versions.items.map((version) => (
              <article className={version.is_current ? "current" : ""} key={version.version_id}>
                <div><strong>{version.version}</strong>{version.is_current && <span className="status-badge active">Current</span>}<small>{formatDateTime(version.uploaded_at)} · {version.uploaded_by || "Not available"}</small></div>
                <button className="text-button" disabled={version.is_current} type="button" onClick={() => restoreVersion(version)}>Restore</button>
              </article>
            ))}
          </div>
        </Modal>
      )}
    </>
  );
}

export default Documents;
