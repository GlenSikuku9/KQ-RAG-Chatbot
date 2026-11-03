import { Fragment, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  Bell,
  Bot,
  CalendarDays,
  ChartColumn,
  ChartNoAxesCombined,
  ChartPie,
  CircleAlert,
  CircleCheck,
  ChevronDown,
  Clock3,
  Cpu,
  Ellipsis,
  FileText,
  LayoutDashboard,
  ListChecks,
  MessageSquare,
  MessageCircleMore,
  MessagesSquare,
  RefreshCcw,
  Search,
  Settings,
  Star,
  UserPlus,
  UsersRound,
  X,
} from "lucide-react";

import {
  adminConversation,
  adminConversationPage,
  adminAnalytics,
  adminDeleteDocument,
  adminDocumentPage,
  adminProcessDocument,
  adminUpdateDocument,
  adminDocumentContent,
  adminSaveDocumentContent,
  adminDocumentVersions,
  adminRestoreDocumentVersion,
  adminSearch,
  adminUpdateRole,
  adminUpdateStatus,
  adminUploadDocument,
  adminModels,
  adminEvaluations,
  adminLogs,
  adminSetDefaultModel,
  adminGenerateModelTest,
  adminUserPage,
  getPassengerThemeForAdmin,
  savePassengerTheme,
} from "../api";
import { FormattedAnswer } from "../components/FormattedAnswer";
import { KenyaAirwaysLogo } from "../components/KenyaAirwaysLogo";
import "./AdminDashboard.css";

const pages = [
  { name: "Overview", icon: LayoutDashboard },
  { name: "Documents", icon: FileText },
  { name: "Users", icon: UsersRound },
  { name: "Conversations", icon: MessagesSquare },
  { name: "Analytics", icon: ChartNoAxesCombined },
  { name: "Models", icon: Bot },
  { name: "Logs", icon: ListChecks },
  { name: "Settings", icon: Settings },
];

const PAGE_DESCRIPTIONS = {
  Overview: "A concise summary of current chatbot activity.",
  Documents: "Manage the published knowledge base and document processing.",
  Users: "Review passenger accounts, roles and access status.",
  Conversations: "Inspect saved conversations and their retrieval evidence.",
  Analytics: "Explore chatbot usage, response outcomes and service performance.",
  Models: "Monitor configured AI providers and compare real evaluation results.",
  Logs: "Review administrative actions and operational diagnostics.",
  Settings: "Configure the passenger-facing chatbot appearance.",
};

// Stable configuration pages remain fresh longer than frequently changing
// operational pages. Cached entries are scoped to this signed-in dashboard.
const PAGE_CACHE_TTL = {
  Overview: 30_000,
  Documents: 60_000,
  Users: 60_000,
  Conversations: 15_000,
  Analytics: 30_000,
  Models: 60_000,
  Logs: 15_000,
  Settings: 60_000,
};

// Use one unambiguous day/month/year format throughout the administrator UI.
function formatDate(value) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(value));
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

function formatChartLabel(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return `${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`;
}

function formatChartTooltip(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function AdminDashboard({ user, profile, onSignOut }) {
  const [page, setPage] = useState("Overview");
  const [selectedConversation, setSelectedConversation] = useState(null);
  const [notice, setNotice] = useState(null);
  const [loading, setLoading] = useState(true);
  const [analytics, setAnalytics] = useState(null);
  const [periodDays, setPeriodDays] = useState(30);
  const [theme, setTheme] = useState(null);
  const [models, setModels] = useState([]);
  const [evaluations, setEvaluations] = useState([]);
  const [logs, setLogs] = useState([]);
  const [tableRevision, setTableRevision] = useState(0);
  const pageCache = useRef(new Map());
  const inFlightRequests = useRef(new Map());
  const activeRequestKey = useRef(null);

  function applyPageData(targetPage, data) {
    if (targetPage === "Overview" || targetPage === "Analytics") setAnalytics(data);
    if (targetPage === "Settings") setTheme(data);
    if (targetPage === "Models") {
      setModels(data.models);
      setEvaluations(data.evaluations);
    }
    if (targetPage === "Logs") setLogs(data);
  }

  async function fetchPageData(targetPage) {
    // Paginated table pages own their request lifecycle so filters do not make
    // the dashboard shell download an additional unfiltered collection.
    if (["Documents", "Users", "Conversations"].includes(targetPage)) return null;
    if (targetPage === "Overview" || targetPage === "Analytics") {
      return adminAnalytics(user, periodDays);
    }
    if (targetPage === "Settings") return getPassengerThemeForAdmin(user);
    if (targetPage === "Models") {
      // These endpoints are independent, so concurrent loading removes one
      // complete network wait without altering their data or permissions.
      const [nextModels, nextEvaluations] = await Promise.all([
        adminModels(user),
        adminEvaluations(user),
      ]);
      return { models: nextModels, evaluations: nextEvaluations };
    }
    if (targetPage === "Logs") return adminLogs(user);
    return null;
  }

  async function reload(force = false) {
    const targetPage = page;
    const periodKey = ["Overview", "Analytics"].includes(targetPage)
      ? periodDays
      : "default";
    const cacheKey = `${targetPage}:${periodKey}`;
    const cached = pageCache.current.get(cacheKey);
    const isFresh = cached
      && Date.now() - cached.savedAt < PAGE_CACHE_TTL[targetPage];

    if (!force && isFresh) {
      applyPageData(targetPage, cached.data);
      setLoading(false);
      return;
    }

    setLoading(true);
    activeRequestKey.current = cacheKey;
    setNotice(null);
    try {
      // Reuse an identical in-flight request when rapid navigation or React's
      // development checks request the same page more than once.
      let request = inFlightRequests.current.get(cacheKey);
      if (!request || force) {
        request = fetchPageData(targetPage);
        inFlightRequests.current.set(cacheKey, request);
      }
      const data = await request;
      pageCache.current.set(cacheKey, { data, savedAt: Date.now() });
      if (activeRequestKey.current === cacheKey) applyPageData(targetPage, data);
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      inFlightRequests.current.delete(cacheKey);
      if (activeRequestKey.current === cacheKey) setLoading(false);
    }
  }

  useEffect(() => { reload(); }, [page, periodDays]);

  async function upload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      await adminUploadDocument(user, file);
      setNotice({ type: "success", text: "Document uploaded and processed." });
      setTableRevision((value) => value + 1);
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    }
    event.target.value = "";
  }

  return (
    <main className="admin-shell">
      <aside className="admin-sidebar">
        <div className="admin-brand">
          <KenyaAirwaysLogo className="admin-airline-logo" />
        </div>
        <div className="admin-sidebar-heading">
          <p className="admin-mark">ADMINISTRATION</p>
          <p className="admin-caption">Knowledge and access management</p>
        </div>
        <nav aria-label="Administrator navigation">
          {pages.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={`admin-nav-item ${page === item.name ? "active" : ""}`}
                key={item.name}
                type="button"
                onClick={() => setPage(item.name)}
              >
                <Icon className="admin-nav-icon" size={19} strokeWidth={1.9} aria-hidden="true" />
                <span>{item.name}</span>
              </button>
            );
          })}
        </nav>
        <p className="admin-sidebar-note"><UsersRound size={18} aria-hidden="true" /> <span>Signed in as <strong>{profile.name || profile.email || "administrator"}</strong>.</span></p>
      </aside>

      <section className="admin-workspace">
        <AdminTopbar
          user={user}
          profile={profile}
          onNavigate={setPage}
          onOpenConversation={async (id) => {
            setPage("Conversations");
            setSelectedConversation(await adminConversation(user, id));
          }}
          onSignOut={onSignOut}
        />
        <section className="admin-content">
        <header className={`admin-header ${page === "Overview" ? "overview-header" : ""}`}>
          <div>
            <p className="eyebrow">ADMINISTRATION</p>
            <h1>{page}</h1>
            <p className="admin-header-description">{PAGE_DESCRIPTIONS[page]}</p>
            {page === "Overview" && <span className="admin-title-rule" aria-hidden="true" />}
          </div>
          {(page === "Overview" || page === "Analytics") && <label className="admin-period"><span><CalendarDays size={16} /> Reporting period</span><select value={periodDays} onChange={(event) => setPeriodDays(Number(event.target.value))}><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></label>}
          {page === "Documents" && <label className="button primary admin-upload">Upload document<input type="file" accept=".docx" onChange={upload} /></label>}
        </header>
        {notice && <div className={`notice ${notice.type === "error" ? "error" : "success"}`} role="status"><span>{notice.text}</span>{notice.type === "error" && <button className="text-button" type="button" onClick={() => reload(true)}>Retry</button>}</div>}
        {loading ? <AdminLoadingSkeleton /> : <>
          {page === "Overview" && <Overview analytics={analytics} onNavigate={setPage} />}
          {page === "Analytics" && <Analytics analytics={analytics} />}
          {page === "Settings" && <ThemeSettings user={user} theme={theme} setTheme={setTheme} onSaved={(message) => {
            // Published settings changed, so a later visit must not restore the
            // pre-save value from the stable-page cache.
            pageCache.current.delete("Settings:default");
            setNotice({ type: "success", text: message });
          }} />}
          {page === "Models" && <ModelManagement user={user} models={models} evaluations={evaluations} onChanged={() => reload(true)} />}
          {page === "Logs" && <LogsTable logs={logs} />}
          {page === "Documents" && <DocumentTable user={user} revision={tableRevision} />}
          {page === "Users" && <UserTable user={user} revision={tableRevision} />}
          {page === "Conversations" && <ConversationTable user={user} revision={tableRevision} onSelect={async (id) => setSelectedConversation(await adminConversation(user, id))} />}
        </>}
        {selectedConversation && <ConversationModal detail={selectedConversation} onClose={() => setSelectedConversation(null)} />}
        </section>
      </section>
    </main>
  );
}

function AdminTopbar({ user, profile, onNavigate, onOpenConversation, onSignOut }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searchState, setSearchState] = useState("idle");
  const [searchOpen, setSearchOpen] = useState(false);
  const [activeResult, setActiveResult] = useState(-1);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const searchRef = useRef(null);
  const notificationsRef = useRef(null);
  const accountRef = useRef(null);
  const normalizedQuery = query.trim();
  const displayName = profile?.name || profile?.email || "Administrator";
  const initials = displayName
    .split(/\s+|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");

  useEffect(() => {
    if (normalizedQuery.length < 2) {
      setResults([]);
      setSearchState("idle");
      setActiveResult(-1);
      return undefined;
    }

    // Debouncing and cancellation prevent stale keystrokes from producing
    // unnecessary reads or replacing a newer result set.
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setSearchState("loading");
      setSearchOpen(true);
      try {
        const payload = await adminSearch(user, normalizedQuery, controller.signal);
        setResults(payload.results);
        setSearchState("ready");
        setActiveResult(payload.results.length ? 0 : -1);
      } catch (error) {
        if (controller.signal.aborted) return;
        setSearchState("error");
        setResults([]);
      }
    }, 300);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [normalizedQuery, user]);

  useEffect(() => {
    // Both navbar popovers share one outside-click and Escape lifecycle so
    // opening one cannot leave the other active behind it.
    function closeOpenPopovers(event) {
      if (event.type === "keydown" && event.key !== "Escape") return;
      if (event.type === "pointerdown") {
        if (notificationsRef.current?.contains(event.target)) return;
        if (accountRef.current?.contains(event.target)) return;
        if (searchRef.current?.contains(event.target)) return;
      }
      setNotificationsOpen(false);
      setAccountOpen(false);
      if (event.type === "keydown") setSearchOpen(false);
    }
    document.addEventListener("pointerdown", closeOpenPopovers);
    document.addEventListener("keydown", closeOpenPopovers);
    return () => {
      document.removeEventListener("pointerdown", closeOpenPopovers);
      document.removeEventListener("keydown", closeOpenPopovers);
    };
  }, []);

  function chooseResult(result) {
    setSearchOpen(false);
    setQuery("");
    if (result.result_type === "conversation") {
      onOpenConversation(result.result_id);
      return;
    }
    onNavigate(result.result_type === "user" ? "Users" : "Documents");
  }

  function handleSearchKeyDown(event) {
    if (event.key === "Escape") {
      setSearchOpen(false);
      return;
    }
    if (!results.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveResult((current) => (
        (current + direction + results.length) % results.length
      ));
    }
    if (event.key === "Enter" && activeResult >= 0) {
      event.preventDefault();
      chooseResult(results[activeResult]);
    }
  }

  const groups = [
    { type: "user", label: "Users", icon: UsersRound },
    { type: "conversation", label: "Conversations", icon: MessageSquare },
    { type: "document", label: "Documents", icon: FileText },
  ];

  return (
    <header className="admin-topbar">
      <div className="admin-topbar-left">
        <strong>Administration</strong>
        <span className="admin-topbar-divider" aria-hidden="true" />
        <div className="admin-global-search" ref={searchRef}>
          <Search size={18} aria-hidden="true" />
          <input
            aria-autocomplete="list"
            aria-controls="admin-search-results"
            aria-expanded={searchOpen}
            aria-label="Search administrator records"
            onChange={(event) => setQuery(event.target.value)}
            onFocus={() => normalizedQuery.length >= 2 && setSearchOpen(true)}
            onKeyDown={handleSearchKeyDown}
            placeholder="Search users, conversations, documents..."
            type="search"
            value={query}
          />
          {searchOpen && (
            <div className="admin-search-results" id="admin-search-results" role="listbox">
              {searchState === "loading" && <p>Searching secure records…</p>}
              {searchState === "error" && <p className="search-error">Search could not be completed. Try again.</p>}
              {searchState === "ready" && !results.length && <p>No matching records found.</p>}
              {searchState === "ready" && groups.map((group) => {
                const items = results.filter((item) => item.result_type === group.type);
                if (!items.length) return null;
                const GroupIcon = group.icon;
                return (
                  <section className="admin-search-group" key={group.type}>
                    <h3>{group.label}</h3>
                    {items.map((item) => {
                      const resultIndex = results.indexOf(item);
                      return (
                        <button
                          aria-selected={activeResult === resultIndex}
                          className={activeResult === resultIndex ? "active" : ""}
                          key={`${item.result_type}-${item.result_id}`}
                          onClick={() => chooseResult(item)}
                          onMouseEnter={() => setActiveResult(resultIndex)}
                          role="option"
                          type="button"
                        >
                          <span><GroupIcon size={17} aria-hidden="true" /></span>
                          <span><strong>{item.title}</strong><small>{item.description}</small></span>
                        </button>
                      );
                    })}
                  </section>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="admin-topbar-actions">
        <div className="admin-menu-anchor" ref={notificationsRef}>
          <button
            aria-expanded={notificationsOpen}
            aria-label="Open notifications"
            className="admin-icon-button"
            onClick={() => {
              setNotificationsOpen((current) => !current);
              setAccountOpen(false);
            }}
            type="button"
          >
            <Bell size={20} aria-hidden="true" />
          </button>
          {notificationsOpen && (
            <div className="admin-popover notification-popover" role="status">
              <strong>Notifications</strong>
              <Bell size={24} aria-hidden="true" />
              <p>No notifications available</p>
            </div>
          )}
        </div>

        <div className="admin-menu-anchor" ref={accountRef}>
          <button
            aria-expanded={accountOpen}
            className="admin-account-button"
            onClick={() => {
              setAccountOpen((current) => !current);
              setNotificationsOpen(false);
            }}
            type="button"
          >
            <span className="admin-avatar">{initials || "A"}</span>
            <span className="admin-account-copy"><strong>{displayName}</strong><small>Administrator</small></span>
            <ChevronDown size={17} aria-hidden="true" />
          </button>
          {accountOpen && (
            <div className="admin-popover account-popover">
              <strong>Account information</strong>
              <p>{profile?.email || user?.email || "Email unavailable"}</p>
              <p className="admin-role-label">Role: Administrator</p>
              <button className="button secondary" type="button" onClick={() => { setAccountOpen(false); onSignOut(); }}>Sign out</button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

function AdminLoadingSkeleton() {
  return (
    <div className="admin-loading-skeleton" aria-busy="true" aria-label="Loading administrator data">
      <span /><span /><span /><span />
    </div>
  );
}

function LegacyModelManagement({ user, models, evaluations, onChanged }) {
  const [prompt, setPrompt] = useState("");
  const [testResult, setTestResult] = useState(null);
  async function testModel(modelId) { setTestResult({ loading: true }); try { setTestResult(await adminGenerateModelTest(user, modelId, prompt)); } catch (error) { setTestResult({ error: error.message }); } }
  return <div className="model-management"><section className="admin-analytics-card model-test-panel"><h2>Model testing</h2><p className="admin-metric-note">Test a configured provider without creating a passenger conversation.</p><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Enter a test prompt…" /><div className="model-test-buttons">{models.map((model) => <button className="button secondary" key={model.model_id} type="button" disabled={!model.configured || !prompt.trim()} onClick={() => testModel(model.model_id)}>Test {modelDisplayName(model.model_id)}</button>)}</div>{testResult?.loading && <p>Testing provider…</p>}{testResult?.error && <p className="notice error">{testResult.error}</p>}{testResult?.text && <div className="model-test-result"><strong>{modelDisplayName(testResult.model_id)}</strong><p>{testResult.text}</p></div>}</section><div className="model-card-grid">{models.map((model) => <article className={`model-card ${model.is_default ? "default" : ""}`} key={model.model_id}><div className="model-card-heading"><div><h2>{modelDisplayName(model.model_id)}</h2><small>{model.model_id}</small></div>{model.is_default && <span className="status-badge active">Default</span>}</div><p>{readableEventName(model.provider)} · {model.upstream_model}</p><p>Languages: {model.language_support.map(languageLabel).join(", ")}</p><p className="admin-metric-note">{model.configured ? "Available and configured" : "Unavailable"}</p><p>Usage: <strong>{model.usage_count}</strong> ({model.usage_percentage}%)</p><p>Average response: <strong>{model.average_response_ms == null ? "Unavailable" : `${(model.average_response_ms / 1000).toFixed(2)} seconds`}</strong></p><p>Successful: {model.successful_requests} · Failed: {model.failed_requests}</p><button className="button secondary" type="button" disabled={model.is_default || !model.configured} onClick={async () => { if (window.confirm(`Set ${modelDisplayName(model.model_id)} as the default model?`)) { await adminSetDefaultModel(user, model.model_id); onChanged(); } }}>Set as default</button></article>)}</div><section className="admin-table-card evaluation-card"><h2>Evaluation comparison</h2>{evaluations.length ? <table className="admin-table"><thead><tr><th>Model</th><th>Language</th><th>Faithfulness</th><th>Answer relevance</th><th>Context precision</th><th>Context recall</th><th>Sample</th><th>Run</th></tr></thead><tbody>{evaluations.map((item) => <tr key={`${item.run_id}-${item.model_id}-${item.language}`}><td>{modelDisplayName(item.model_id)}</td><td>{languageLabel(item.language)}</td><td>{metricValue(item.faithfulness)}</td><td>{metricValue(item.answer_relevance)}</td><td>{metricValue(item.context_precision)}</td><td>{metricValue(item.context_recall)}</td><td>{item.sample_size}</td><td><small>{item.dataset_id}<br />{formatDate(item.evaluated_at)}</small></td></tr>)}</tbody></table> : <div className="admin-empty-state evaluation-empty"><span aria-hidden="true">◇</span><h3>No evaluation results yet</h3><p>Complete and persist an evaluation run before comparing quality metrics.</p></div>}</section></div>;
}

function ModelManagement({ user, models, evaluations, onChanged }) {
  const [prompt, setPrompt] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [defaultUpdate, setDefaultUpdate] = useState(null);

  useEffect(() => {
    if (!selectedModel && models.length) {
      setSelectedModel(models.find((model) => model.is_default)?.model_id || models[0].model_id);
    }
  }, [models, selectedModel]);

  async function testModel() {
    const startedAt = performance.now();
    setTestResult({ loading: true, model_id: selectedModel });
    try {
      const result = await adminGenerateModelTest(user, selectedModel, prompt);
      setTestResult({ ...result, elapsed_ms: performance.now() - startedAt });
    } catch (error) {
      setTestResult({
        error: error.message,
        model_id: selectedModel,
        elapsed_ms: performance.now() - startedAt,
      });
    }
  }

  async function setDefault(model) {
    if (!window.confirm(`Set ${modelDisplayName(model.model_id)} as the default model?`)) return;
    setDefaultUpdate({ modelId: model.model_id, status: "loading" });
    try {
      await adminSetDefaultModel(user, model.model_id);
      await onChanged();
      setDefaultUpdate({ modelId: model.model_id, status: "success" });
    } catch (error) {
      setDefaultUpdate({ modelId: model.model_id, status: "error", message: error.message });
    }
  }

  return (
    <div className="model-management">
      <section className="admin-analytics-card model-test-panel">
        <div className="admin-section-heading">
          <div><h2>Model testing</h2><p>Test a provider without creating a passenger conversation.</p></div>
        </div>
        <label>Test prompt<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Enter a test prompt..." /></label>
        <div className="model-test-controls">
          <label>AI model<select value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)}>{models.map((model) => <option disabled={!model.configured} key={model.model_id} value={model.model_id}>{modelDisplayName(model.model_id)}</option>)}</select></label>
          <button className="button primary" disabled={!prompt.trim() || !selectedModel || testResult?.loading} onClick={testModel} type="button">{testResult?.loading ? "Testing model..." : "Test model"}</button>
        </div>
        {testResult && !testResult.loading && <div className={`model-test-result ${testResult.error ? "error" : ""}`}><dl><div><dt>Selected model</dt><dd>{modelDisplayName(testResult.model_id)}</dd></div><div><dt>Response time</dt><dd>{(testResult.elapsed_ms / 1000).toFixed(2)} seconds</dd></div></dl>{testResult.error ? <p className="notice error">{testResult.error}</p> : <div><strong>Generated response</strong><p>{testResult.text}</p></div>}</div>}
      </section>

      {defaultUpdate?.status === "success" && <p className="notice success">Default model updated successfully.</p>}
      {defaultUpdate?.status === "error" && <p className="notice error">{defaultUpdate.message}</p>}
      <div className="model-card-grid">
        {models.map((model) => <article className={`model-card ${model.is_default ? "default" : ""}`} key={model.model_id}><div className="model-card-heading"><div><h2>{modelDisplayName(model.model_id)}</h2><small>{readableEventName(model.provider)}</small></div>{model.is_default && <span className="status-badge active">Default</span>}</div><p className="model-technical-id">{model.upstream_model}</p><dl className="model-detail-list"><div><dt>Supported languages</dt><dd>{model.language_support.map(languageLabel).join(", ")}</dd></div><div><dt>Availability</dt><dd><span className={`status-badge ${model.configured ? "active" : "failed"}`}>{model.configured ? "Configured" : "Unavailable"}</span></dd></div><div><dt>Usage</dt><dd>{model.usage_count} ({model.usage_percentage}%)</dd></div><div><dt>Average response</dt><dd>{model.average_response_ms == null ? "Unavailable" : `${(model.average_response_ms / 1000).toFixed(2)} seconds`}</dd></div><div><dt>Successful requests</dt><dd>{model.successful_requests}</dd></div><div><dt>Failed requests</dt><dd>{model.failed_requests}</dd></div></dl>{!model.is_default && <button className="button secondary" disabled={!model.configured || defaultUpdate?.status === "loading"} onClick={() => setDefault(model)} type="button">{defaultUpdate?.status === "loading" && defaultUpdate.modelId === model.model_id ? "Updating..." : "Set as default"}</button>}</article>)}
      </div>

      <section className="admin-table-card evaluation-card">
        <div className="admin-card-heading"><h2>Evaluation comparison</h2></div>
        {evaluations.length ? <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>Model</th><th>Language</th><th>Faithfulness</th><th>Answer relevance</th><th>Context precision</th><th>Context recall</th><th>Sample</th><th>Run</th></tr></thead><tbody>{evaluations.map((item) => <tr key={`${item.run_id}-${item.model_id}-${item.language}`}><td>{modelDisplayName(item.model_id)}</td><td>{languageLabel(item.language)}</td><td>{metricValue(item.faithfulness)}</td><td>{metricValue(item.answer_relevance)}</td><td>{metricValue(item.context_precision)}</td><td>{metricValue(item.context_recall)}</td><td>{item.sample_size}</td><td><small>{item.dataset_id}<br />{formatDate(item.evaluated_at)}</small></td></tr>)}</tbody></table></div> : <div className="admin-empty-state evaluation-empty"><h3>No evaluation results yet</h3><p>Persist a real evaluation run before comparing quality metrics.</p></div>}
      </section>
    </div>
  );
}

function metricValue(value) { return value == null ? "Unavailable" : `${(value * 100).toFixed(1)}%`; }

function modelDisplayName(modelId) {
  return {
    openrouter_gemma_26b: "Gemma 4 26B A4B",
    openrouter_gemma_31b: "Gemma 4 31B",
    google_gemini_flash: "Gemini 3.8 Flash",
  }[modelId] || modelId;
}

function LegacyLogsTable({ logs }) {
  const [search, setSearch] = useState("");
  const [eventType, setEventType] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [expandedRow, setExpandedRow] = useState(null);
  const pageSize = 12;

  const eventTypes = [...new Set(logs.map((item) => item.event_type))].sort();
  // Filtering stays client-side because the existing logs API returns the
  // administrator-safe result set and does not expose query parameters.
  const filteredLogs = logs.filter((item) => {
    const normalizedSearch = search.trim().toLowerCase();
    const timestamp = new Date(item.timestamp);
    const matchesSearch = !normalizedSearch || [
      item.event_type,
      item.description,
      item.actor,
    ].some((value) => String(value || "").toLowerCase().includes(normalizedSearch));
    const matchesEvent = eventType === "all" || item.event_type === eventType;
    const matchesSeverity = severity === "all" || item.severity === severity;
    const matchesStart = !dateFrom || timestamp >= new Date(`${dateFrom}T00:00:00`);
    const matchesEnd = !dateTo || timestamp <= new Date(`${dateTo}T23:59:59`);

    return matchesSearch
      && matchesEvent
      && matchesSeverity
      && matchesStart
      && matchesEnd;
  });
  const pageCount = Math.max(1, Math.ceil(filteredLogs.length / pageSize));
  const safePage = Math.min(currentPage, pageCount);
  const visibleLogs = filteredLogs.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize,
  );

  function updateFilter(setter, value) {
    setter(value);
    setCurrentPage(1);
  }

  return (
    <section className="admin-card logs-panel">
      <div className="admin-toolbar logs-toolbar">
        <label className="admin-search-field">
          <span className="sr-only">Search logs</span>
          <input
            type="search"
            value={search}
            onChange={(event) => updateFilter(setSearch, event.target.value)}
            placeholder="Search events, descriptions or actors"
          />
        </label>
        <label>
          Event type
          <select
            value={eventType}
            onChange={(event) => updateFilter(setEventType, event.target.value)}
          >
            <option value="all">All events</option>
            {eventTypes.map((value) => (
              <option value={value} key={value}>{readableEventName(value)}</option>
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
        <label>Date from<input type="date" value={dateFrom} onChange={(event) => updateFilter(setDateFrom, event.target.value)} /></label>
        <label>Date to<input type="date" value={dateTo} onChange={(event) => updateFilter(setDateTo, event.target.value)} /></label>
      </div>

      <div className="admin-table-scroll">
        <table className="admin-table logs-table">
          <thead><tr><th>Timestamp</th><th>Event</th><th>Description</th><th>Actor</th><th>Severity</th><th /></tr></thead>
          <tbody>
            {visibleLogs.map((item, index) => {
              const rowKey = `${item.timestamp}-${index}`;
              return (
                <Fragment key={rowKey}>
                  <tr>
                    <td className="log-time">{formatDateTime(item.timestamp)}</td>
                    <td><strong>{readableEventName(item.event_type)}</strong><small>{item.event_type}</small></td>
                    <td>{item.description}</td>
                    <td>{item.actor}</td>
                    <td><span className={`severity-badge ${item.severity}`}>{severityLabel(item.severity)}</span></td>
                    <td><button className="text-button" type="button" onClick={() => setExpandedRow(expandedRow === rowKey ? null : rowKey)}>{expandedRow === rowKey ? "Hide" : "Details"}</button></td>
                  </tr>
                  {expandedRow === rowKey && (
                    <tr className="log-detail-row" key={`${rowKey}-details`}>
                      <td colSpan="6">
                        <dl><div><dt>Original event</dt><dd>{item.event_type}</dd></div><div><dt>Recorded at</dt><dd>{formatDateTime(item.timestamp)}</dd></div><div><dt>Actor</dt><dd>{item.actor || "System"}</dd></div></dl>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {!visibleLogs.length && <tr><td colSpan="6" className="admin-empty">No recorded activity matches these filters.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="admin-pagination">
        <p>Showing {visibleLogs.length} of {filteredLogs.length} events</p>
        <div><button type="button" disabled={safePage === 1} onClick={() => setCurrentPage(safePage - 1)}>Previous</button><span>Page {safePage} of {pageCount}</span><button type="button" disabled={safePage === pageCount} onClick={() => setCurrentPage(safePage + 1)}>Next</button></div>
      </div>
    </section>
  );
}

function LogsTable({ logs }) {
  const [search, setSearch] = useState("");
  const [eventType, setEventType] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [selectedLog, setSelectedLog] = useState(null);
  const detailsTrigger = useRef(null);
  const pageSize = 12;
  const eventTypes = [...new Set(logs.map((item) => item.event_type))].sort();
  const filteredLogs = logs.filter((item) => {
    const term = search.trim().toLowerCase();
    const timestamp = new Date(item.timestamp);
    return (!term || [item.event_type, item.description, item.actor].some((value) => String(value || "").toLowerCase().includes(term)))
      && (eventType === "all" || item.event_type === eventType)
      && (severity === "all" || item.severity === severity)
      && (!dateFrom || timestamp >= new Date(`${dateFrom}T00:00:00`))
      && (!dateTo || timestamp <= new Date(`${dateTo}T23:59:59`));
  });
  const pageCount = Math.max(1, Math.ceil(filteredLogs.length / pageSize));
  const safePage = Math.min(currentPage, pageCount);
  const visibleLogs = filteredLogs.slice((safePage - 1) * pageSize, safePage * pageSize);

  function updateFilter(setter, value) {
    setter(value);
    setCurrentPage(1);
  }

  function openDetails(item, event) {
    detailsTrigger.current = event.currentTarget;
    setSelectedLog(item);
  }

  return (
    <section className="admin-card logs-panel">
      <div className="admin-toolbar logs-toolbar">
        <label className="admin-search-field"><span className="sr-only">Search logs</span><input type="search" value={search} onChange={(event) => updateFilter(setSearch, event.target.value)} placeholder="Search events, descriptions or actors" /></label>
        <label>Event type<select value={eventType} onChange={(event) => updateFilter(setEventType, event.target.value)}><option value="all">All events</option>{eventTypes.map((value) => <option value={value} key={value}>{readableEventName(value)}</option>)}</select></label>
        <label>Severity<select value={severity} onChange={(event) => updateFilter(setSeverity, event.target.value)}><option value="all">All severities</option><option value="info">Information</option><option value="warning">Warning</option><option value="error">Error</option></select></label>
        <label>Date from<input type="date" value={dateFrom} onChange={(event) => updateFilter(setDateFrom, event.target.value)} /></label>
        <label>Date to<input type="date" value={dateTo} onChange={(event) => updateFilter(setDateTo, event.target.value)} /></label>
      </div>
      <div className="admin-table-scroll"><table className="admin-table logs-table"><thead><tr><th>Timestamp</th><th>Event</th><th>Description</th><th>Actor</th><th>Severity</th><th /></tr></thead><tbody>{visibleLogs.map((item, index) => <tr key={`${item.timestamp}-${index}`}><td className="log-time">{formatDateTime(item.timestamp)}</td><td><strong>{readableEventName(item.event_type)}</strong><small>{item.event_type}</small></td><td>{item.description}</td><td>{item.actor}</td><td><span className={`severity-badge ${item.severity}`}>{severityLabel(item.severity)}</span></td><td><button className="text-button" type="button" onClick={(event) => openDetails(item, event)}>Details</button></td></tr>)}{!visibleLogs.length && <tr><td colSpan="6" className="admin-empty">No recorded activity matches these filters.</td></tr>}</tbody></table></div>
      <div className="admin-pagination"><p>Showing {visibleLogs.length} of {filteredLogs.length} events</p><div><button type="button" disabled={safePage === 1} onClick={() => setCurrentPage(safePage - 1)}>Previous</button><span>Page {safePage} of {pageCount}</span><button type="button" disabled={safePage === pageCount} onClick={() => setCurrentPage(safePage + 1)}>Next</button></div></div>
      {selectedLog && <LogDetailsDrawer log={selectedLog} onClose={() => setSelectedLog(null)} returnFocusRef={detailsTrigger} />}
    </section>
  );
}

function LogDetailsDrawer({ log, onClose, returnFocusRef }) {
  const drawer = useRef(null);
  const safeMetadata = Object.entries(log.details || {}).filter(([key]) => (
    !/(token|secret|api.?key|authorization|message_text|prompt|target_id)/i.test(key)
  ));

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
      const focusable = [...drawer.current.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])")];
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
    <div className="log-drawer-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="log-details-drawer" ref={drawer} role="dialog" aria-modal="true" aria-labelledby="log-drawer-title" tabIndex="-1">
        <header><div><p className="eyebrow">EVENT DIAGNOSTICS</p><h2 id="log-drawer-title">Log details</h2></div><button className="admin-icon-button" type="button" onClick={onClose} aria-label="Close log details"><X size={20} /></button></header>
        <dl className="log-detail-list"><div><dt>Event</dt><dd>{readableEventName(log.event_type)}<small>{log.event_type}</small></dd></div><div><dt>Severity</dt><dd><span className={`severity-badge ${log.severity}`}>{severityLabel(log.severity)}</span></dd></div><div><dt>Timestamp</dt><dd>{formatDateTime(log.timestamp)}</dd></div><div><dt>Actor</dt><dd>{log.actor || "System"}</dd></div><div><dt>Description</dt><dd>{log.description}</dd></div></dl>
        <section className="log-metadata"><h3>Metadata</h3>{safeMetadata.length ? <dl>{safeMetadata.map(([key, value]) => <div key={key}><dt>{readableEventName(key)}</dt><dd>{value == null ? "Unavailable" : String(value)}</dd></div>)}</dl> : <p>No additional non-sensitive diagnostic metadata is available.</p>}</section>
      </aside>
    </div>
  );
}

function readableEventName(value) {
  return String(value || "System event")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function severityLabel(value) {
  return {
    info: "Information",
    warning: "Warning",
    error: "Error",
  }[value] || readableEventName(value);
}

function ThemeSettings({ user, theme, setTheme, onSaved }) {
  if (!theme) {
    return <p className="admin-empty">Loading passenger appearance settings...</p>;
  }

  // Group the existing persisted tokens for usability; storage keys and the
  // passenger-only settings API remain unchanged.
  const groups = [
    {
      title: "Branding",
      description: "Core navigation, headings and accent actions.",
      fields: [["primary", "Primary navy"], ["secondary", "Secondary navy"], ["accent", "Accent colour"]],
    },
    {
      title: "Backgrounds",
      description: "Passenger page, panels and structural borders.",
      fields: [["background", "Page background"], ["panel", "Panel surface"], ["border", "Border colour"]],
    },
    {
      title: "Message colours",
      description: "Passenger and assistant conversation bubbles.",
      fields: [["user_message", "Passenger message"], ["assistant_message", "Assistant message"]],
    },
    {
      title: "Text and controls",
      description: "Primary and supporting text used by the chatbot.",
      fields: [["text", "Primary text"], ["secondary_text", "Secondary text"]],
    },
  ];
  const defaults = {
    primary: "#102f70",
    secondary: "#102f70",
    accent: "#d71920",
    background: "#f7f9fc",
    panel: "#ffffff",
    user_message: "#d71920",
    assistant_message: "#eaf2fc",
    text: "#172033",
    secondary_text: "#64748b",
    border: "#dce4ef",
  };

  async function save() {
    await savePassengerTheme(user, theme);
    onSaved("Passenger theme saved and published.");
  }

  return (
    <div className="theme-settings">
      <div className="theme-settings-form">
        <div className="settings-intro">
          <h2>Passenger chatbot colours</h2>
          <p>These settings affect only passenger-facing screens. The administrator dashboard always keeps its own theme.</p>
        </div>
        {groups.map((group) => (
          <section className="theme-group" key={group.title}>
            <div className="theme-group-heading">
              <h3>{group.title}</h3>
              <p>{group.description}</p>
            </div>
            <div className="theme-field-grid">
              {group.fields.map(([key, label]) => (
                <label className="theme-field" key={key}>
                  {label}
                  <span>
                    <input
                      type="color"
                      value={theme[key]}
                      onChange={(event) => setTheme({ ...theme, [key]: event.target.value })}
                      aria-label={`${label} colour picker`}
                    />
                    <input
                      value={theme[key]}
                      pattern="#[0-9a-fA-F]{6}"
                      onChange={(event) => setTheme({ ...theme, [key]: event.target.value })}
                      aria-label={`${label} hexadecimal value`}
                    />
                  </span>
                </label>
              ))}
            </div>
          </section>
        ))}
        <div className="theme-actions">
          <button className="button admin-primary-button" type="button" onClick={save}>Save changes</button>
          <button className="button secondary" type="button" onClick={() => { if (window.confirm("Reset all passenger colours to the Kenya Airways defaults?")) setTheme(defaults); }}>Reset to default</button>
        </div>
      </div>
      <div className="theme-preview-wrap">
        <p className="eyebrow">LIVE PREVIEW</p>
        <div
          className="theme-preview"
          style={{
            "--preview-primary": theme.primary,
            "--preview-panel": theme.panel,
            "--preview-user": theme.user_message,
            "--preview-assistant": theme.assistant_message,
            "--preview-text": theme.text,
            "--preview-background": theme.background,
            "--preview-border": theme.border,
          }}
        >
          <header><KenyaAirwaysLogo className="preview-airline-logo" variant="colour" /><strong>KQ Support</strong><i>−</i></header>
          <div className="theme-preview-messages">
            <p className="preview-user">What is my baggage allowance?</p>
            <div><small>KQ Support</small><p className="preview-assistant">Your baggage allowance depends on your route, cabin and fare.</p></div>
          </div>
          <div className="preview-input">Enter your inquiry... <span>Send</span></div>
        </div>
      </div>
    </div>
  );
}

function MetricCard({ label, value, hint, icon: Icon, accent = "navy" }) {
  return <article className={`admin-metric-card accent-${accent}`}><div className="admin-metric-heading"><span aria-hidden="true">{Icon && <Icon size={21} strokeWidth={1.9} />}</span><p>{label}</p></div><strong>{value}</strong><small>{hint}</small></article>;
}

function Overview({ analytics, onNavigate }) {
  if (!analytics) return <p className="admin-empty">No overview data is available for this period.</p>;
  return <div className="admin-overview"><div className="admin-metric-grid">
    <MetricCard label="Registered users" value={analytics.totals.users} hint="Current Firebase accounts" icon={UsersRound} accent="red" />
    <MetricCard label="Conversations" value={analytics.totals.conversations} hint={`Started in the last ${analytics.period_days} days`} icon={MessageSquare} accent="navy" />
    <MetricCard label="Active documents" value={analytics.totals.active_documents} hint={`${analytics.totals.documents} document records total`} icon={FileText} accent="amber" />
    <MetricCard label="Average satisfaction" value={analytics.feedback.count ? `${analytics.feedback.average_rating}/5` : "No ratings"} hint={`${analytics.feedback.count} ratings in period`} icon={Star} accent="green" />
  </div><AnalyticsPanels analytics={analytics} compact /><div className="admin-overview-lower"><RecentConversations items={analytics.recent_conversations} onViewAll={() => onNavigate("Conversations")} /><RecentActivity items={analytics.recent_activity} onViewAll={() => onNavigate("Logs")} /></div></div>;
}

function Analytics({ analytics }) {
  if (!analytics) return <p className="admin-empty">No analytics data is available for this period.</p>;
  return (
    <div className="analytics-page">
      <div className="admin-metric-grid analytics-kpis">
        <MetricCard label="Conversations" value={analytics.totals.conversations} hint={`Started in the last ${analytics.period_days} days`} icon={MessageSquare} accent="navy" />
        <MetricCard label="Completed responses" value={analytics.performance.completed_count} hint="Successfully completed generations" icon={CircleCheck} accent="green" />
        <MetricCard label="Average response" value={analytics.performance.completed_count ? `${(analytics.performance.average_response_ms / 1000).toFixed(2)} s` : "Unavailable"} hint="Across completed responses" icon={Clock3} accent="amber" />
        <MetricCard label="Satisfaction" value={analytics.feedback.count ? `${analytics.feedback.average_rating}/5 ★` : "No ratings"} hint={`${analytics.feedback.count} submitted ratings`} icon={Star} accent="purple" />
      </div>
      <AnalyticsPanels analytics={analytics} />
    </div>
  );
}

function AnalyticsPanels({ analytics, compact = false }) {
  const activitySeries = buildActivitySeries(
    analytics.conversation_activity,
    analytics.period_days,
  );
  // Seven and thirty-day reports retain daily data. The ninety-day view uses
  // weekly sums so totals are preserved without squeezing ninety bars.
  const labelStride = analytics.period_days <= 7 ? 1 : analytics.period_days <= 30 ? 5 : 1;
  return <div className={`admin-analytics-grid ${compact ? "compact" : ""}`}>
    <section className="admin-analytics-card activity-card"><h2><ChartColumn size={21} /> Conversation activity</h2><p className="admin-metric-note">{analytics.period_days === 90 ? "Weekly conversation totals for the last 90 days." : `Daily conversations started during the last ${analytics.period_days} days.`} Hover or focus a bar for exact details.</p><ConversationActivityChart items={activitySeries} labelStride={labelStride} /></section>
    <section className="admin-analytics-card distribution-card"><h2><ChartPie size={21} /> Language distribution</h2><LanguagePie values={analytics.languages} /><h2><Cpu size={20} /> AI model usage</h2><MetricList values={analytics.models} empty="No model usage data." progress /></section>
    {!compact && <><section className="admin-analytics-card quality-card"><h2><CircleCheck size={20} /> Response quality and timing</h2><div className="quality-metric-grid"><div><CircleCheck size={18} /><span>Completed responses<strong>{analytics.performance.completed_count}</strong></span></div><div><Clock3 size={18} /><span>Average response<strong>{analytics.performance.completed_count ? `${(analytics.performance.average_response_ms / 1000).toFixed(2)} seconds` : "Unavailable"}</strong></span></div><div><Star size={18} /><span>Feedback rating<strong>{analytics.feedback.count ? `${analytics.feedback.average_rating}/5` : "No ratings"}</strong></span></div></div></section><section className="admin-analytics-card outcomes-card"><h2>Fallbacks and failures</h2><MetricList values={analytics.responses} empty="No response outcomes recorded." /><MetricList values={analytics.failures} empty="No failures recorded." /></section></>}
    {!compact && <section className="admin-analytics-card satisfaction-card"><h2><Star size={20} /> User satisfaction</h2><div className="satisfaction-summary"><strong>{analytics.feedback.count ? `${analytics.feedback.average_rating}/5` : "No ratings"}</strong><span>{analytics.feedback.count} submitted ratings</span></div><RatingDistribution values={analytics.rating_distribution} /></section>}
    {!compact && <section className="admin-data-notice"><CircleAlert size={19} /><div><strong>Data availability</strong><p>Query categories, RAG evaluation scores, and CSV export are not recorded by the current backend, so they are intentionally not estimated here.</p></div></section>}
  </div>;
}

function ConversationActivityChart({ items, labelStride }) {
  const [activeItem, setActiveItem] = useState(null);
  const chartWrapRef = useRef(null);

  if (!items.length) {
    return <p className="admin-empty">No conversations in this period.</p>;
  }

  const width = 960;
  const height = 360;
  const plot = { left: 58, right: 16, top: 14, bottom: 52 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const highestCount = Math.max(1, ...items.map((item) => item.count));
  const tickCount = Math.min(4, highestCount);
  const tickStep = Math.max(1, Math.ceil(highestCount / tickCount));
  const axisMaximum = tickStep * tickCount;
  const ticks = Array.from(
    { length: tickCount + 1 },
    (_, index) => axisMaximum - index * tickStep,
  );
  const slotWidth = plotWidth / items.length;
  const barWidth = Math.max(4, Math.min(54, slotWidth * 0.72));

  // Bars, grid lines, and labels use this one coordinate system so a value can
  // never visually disagree with the y-axis scale.
  const yForValue = (value) => (
    plot.top + plotHeight - (value / axisMaximum) * plotHeight
  );

  const showTooltip = (item, event) => {
    const wrapperBounds = chartWrapRef.current?.getBoundingClientRect();
    const targetBounds = event.currentTarget.getBoundingClientRect();

    if (!wrapperBounds) return;

    // Use rendered browser coordinates rather than raw SVG coordinates. This
    // accounts for responsive SVG letterboxing and keeps the tooltip beside
    // the focused bar at every card width.
    setActiveItem({
      ...item,
      left: Math.min(
        wrapperBounds.width - 96,
        Math.max(96, targetBounds.left - wrapperBounds.left + targetBounds.width / 2),
      ),
      top: Math.max(72, targetBounds.top - wrapperBounds.top),
    });
  };

  return (
    <div className="activity-chart-svg-wrap" ref={chartWrapRef}>
      <svg
        className="activity-chart-svg"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Conversation activity bar chart"
        preserveAspectRatio="xMidYMax meet"
      >
        {ticks.map((tick) => {
          const y = yForValue(tick);
          return (
            <g key={tick}>
              <line className="activity-grid-line" x1={plot.left} x2={width - plot.right} y1={y} y2={y} />
              <text className="activity-axis-label" x={plot.left - 9} y={y + 4} textAnchor="end">{tick}</text>
            </g>
          );
        })}
        {items.map((item, index) => {
          const x = plot.left + index * slotWidth + (slotWidth - barWidth) / 2;
          const y = yForValue(item.count);
          const barHeight = Math.max(0, plot.top + plotHeight - y);
          const showLabel = index % labelStride === 0 || index === items.length - 1;
          return (
            <g
              className="activity-svg-bar"
              key={item.key}
              tabIndex="0"
              aria-label={`${item.tooltip}: ${item.count} conversation${item.count === 1 ? "" : "s"}`}
              onMouseEnter={(event) => showTooltip(item, event)}
              onMouseLeave={() => setActiveItem(null)}
              onFocus={(event) => showTooltip(item, event)}
              onBlur={() => setActiveItem(null)}
            >
              <rect x={x} y={y} width={barWidth} height={barHeight} rx="5" />
              {/* A transparent target keeps zero-count dates interactive. */}
              {item.count === 0 && (
                <rect className="activity-zero-target" x={x} y={plot.top + plotHeight - 12} width={barWidth} height="12" rx="4" />
              )}
              {showLabel && (
                <text className="activity-x-label" x={x + barWidth / 2} y={height - 15} textAnchor="middle">{item.label}</text>
              )}
            </g>
          );
        })}
      </svg>
      {activeItem && (
        <div
          className="activity-chart-tooltip"
          role="status"
          style={{ left: `${activeItem.left}px`, top: `${activeItem.top}px` }}
        >
          <span>{activeItem.endDate ? "Week" : "Date"}</span>
          <strong>{activeItem.tooltip}</strong>
          <span>Conversations</span>
          <strong>{activeItem.count}</strong>
        </div>
      )}
    </div>
  );
}

function buildActivitySeries(items, periodDays) {
  if (periodDays !== 90) {
    return items.map((item) => ({
      ...item,
      key: item.date,
      label: formatChartLabel(item.date),
      tooltip: formatChartTooltip(item.date),
      startDate: item.date,
    }));
  }

  const weeks = [];

  for (let index = 0; index < items.length; index += 7) {
    const days = items.slice(index, index + 7);
    const first = days[0];
    const last = days[days.length - 1];

    if (!first || !last) {
      continue;
    }

    weeks.push({
      key: `${first.date}-${last.date}`,
      label: formatChartLabel(first.date),
      tooltip: `${formatChartTooltip(first.date)} – ${formatChartTooltip(last.date)}`,
      startDate: first.date,
      endDate: last.date,
      count: days.reduce((sum, day) => sum + day.count, 0),
    });
  }

  return weeks;
}

function labelLanguages(values) {
  return Object.fromEntries(Object.entries(values || {}).map(([key, value]) => [languageLabel(key), value]));
}

function languageLabel(value) {
  return { en: "English", sw: "Kiswahili", mixed: "Mixed English/Kiswahili", unknown: "Unknown" }[value] || value;
}

function RecentConversations({ items, onViewAll }) {
  const recentItems = [...items]
    .sort((left, right) => new Date(right.started_at) - new Date(left.started_at))
    .slice(0, 6);
  return (
    <section className="admin-analytics-card recent-card">
      <div className="admin-card-heading">
        <h2><MessageCircleMore size={20} /> Recent conversations</h2>
        <button className="text-button" type="button" onClick={onViewAll}>
          View all <ArrowRight size={15} />
        </button>
      </div>
      {recentItems.length ? (
        <div className="admin-recent-list conversation-list">
          {recentItems.map((item) => (
            <div className="recent-conversation-row" key={item.conversation_id}>
              <span className="recent-indicator conversation" aria-hidden="true">
                <MessageSquare size={17} />
              </span>
              <div className="recent-row-copy">
                <strong>{item.title || item.first_message || "No customer message recorded"}</strong>
                <small>{languageLabel(item.language)} · {item.message_count} messages</small>
              </div>
              <time dateTime={item.started_at}>{formatDateTime(item.started_at)}</time>
              <span className="recent-more" aria-hidden="true"><Ellipsis size={18} /></span>
            </div>
          ))}
        </div>
      ) : (
        <p className="admin-metric-note">No conversations in this period.</p>
      )}
    </section>
  );
}

function RecentActivity({ items, onViewAll }) {
  const recentItems = [...items]
    .sort((left, right) => new Date(right.timestamp) - new Date(left.timestamp))
    .slice(0, 6);
  return (
    <section className="admin-analytics-card recent-card">
      <div className="admin-card-heading">
        <h2><Activity size={20} /> Recent system activity</h2>
        <button className="text-button" type="button" onClick={onViewAll}>
          View all <ArrowRight size={15} />
        </button>
      </div>
      {recentItems.length ? (
        <div className="admin-recent-list activity-list">
          {recentItems.map((item, index) => {
            const presentation = activityPresentation(item.action);
            const ActivityIcon = presentation.icon;
            return (
              <div className="recent-activity-row" key={`${item.timestamp}-${index}`}>
                <span className={`recent-indicator ${presentation.tone}`} aria-hidden="true">
                  <ActivityIcon size={17} />
                </span>
                <div className="recent-row-copy">
                  <strong>{readableEventName(item.action)}</strong>
                  <small>{readableEventName(item.target_type)}</small>
                </div>
                <time dateTime={item.timestamp}>{formatDateTime(item.timestamp)}</time>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="admin-metric-note">No administrator activity recorded in this period.</p>
      )}
    </section>
  );
}

function activityPresentation(action) {
  const value = String(action || "").toLowerCase();

  if (value.includes("user")) return { icon: UserPlus, tone: "user" };
  if (value.includes("document") && value.includes("process")) return { icon: RefreshCcw, tone: "document" };
  if (value.includes("document")) return { icon: FileText, tone: "document" };
  if (value.includes("model") || value.includes("generation")) return { icon: Bot, tone: "model" };
  return { icon: CircleCheck, tone: "success" };
}

function MetricList({ values, empty, progress = false }) {
  const entries = Object.entries(values || {});
  const maximum = Math.max(1, ...entries.map(([, count]) => count));
  return entries.length ? <ul className={`admin-metric-list ${progress ? "with-progress" : ""}`}>{entries.map(([name, count]) => <li key={name}><span>{progress ? modelDisplayName(name) : readableMetricName(name)}</span><strong>{count}</strong>{progress && <i aria-hidden="true"><span style={{ width: `${(count / maximum) * 100}%` }} /></i>}</li>)}</ul> : <p className="admin-metric-note">{empty}</p>;
}

function readableMetricName(value) {
  const labels = {
    generated: "Generated answer",
    fallback: "Fallback response",
    clarification: "Clarification requested",
    clarification_required: "Clarification requested",
    provider_error: "AI provider error",
    retrieval_error: "Retrieval error",
    timeout: "Request timeout",
  };

  return labels[value] || readableEventName(value);
}

function LanguagePie({ values }) {
  const labels = Object.entries(values || {}).map(([key, value]) => [languageLabel(key), value]);
  const total = labels.reduce((sum, [, value]) => sum + value, 0);
  if (!total) return <p className="admin-metric-note">No language data.</p>;
  // Complementary colours distinguish languages without changing the counts
  // or implying that one language has a higher quality ranking.
  const colors = ["#0b2347", "#d71920", "#a9b8cc", "#6f86a6"];
  let start = 0;
  const stops = labels.map(([label, value], index) => {
    const end = start + (value / total) * 360;
    const stop = `${colors[index % colors.length]} ${start}deg ${end}deg`;
    start = end;
    return { label, value, stop, color: colors[index % colors.length] };
  });
  return <div className="language-pie-layout"><div className="language-pie" style={{ background: `conic-gradient(${stops.map((item) => item.stop).join(", ")})` }} aria-label={`Language distribution chart, ${total} total conversations`}><span><strong>{total}</strong><small>Total</small></span></div><ul className="language-pie-key">{stops.map((item) => <li key={item.label}><span style={{ background: item.color }} />{item.label}<strong>{item.value} ({Math.round(item.value / total * 100)}%)</strong></li>)}</ul></div>;
}

function RatingDistribution({ values }) {
  const ratings = [5, 4, 3, 2, 1];
  if (!Object.keys(values || {}).length) return <p className="admin-metric-note">No rating distribution is available.</p>;
  return <div className="rating-distribution">{ratings.map((rating) => <div className="rating-row" key={rating}><span className="rating-stars" aria-label={`${rating} out of 5 stars`}>{"★".repeat(rating)}<span className="rating-stars-muted">{"★".repeat(5 - rating)}</span></span><span className="rating-label">{rating}/5</span><strong>{values[String(rating)] || 0}</strong></div>)}</div>;
}

function LegacyDocumentTable({ documents, user, onChanged }) {
  const [editor, setEditor] = useState(null);
  const [versionPanel, setVersionPanel] = useState(null);
  async function openEditor(document) {
    try {
      const content = await adminDocumentContent(user, document.document_id);
      setEditor({ document, content: content.content, warning: content.warning, saving: false });
    } catch (error) {
      window.alert(error.message);
    }
  }
  async function saveEditor() {
    setEditor((value) => ({ ...value, saving: true }));
    try {
      await adminSaveDocumentContent(user, editor.document.document_id, editor.content);
      setEditor(null);
      onChanged();
    } catch (error) {
      setEditor((value) => ({ ...value, saving: false }));
      window.alert(error.message);
    }
  }
  async function openVersions(document) {
    try {
      setVersionPanel({ document, versions: await adminDocumentVersions(user, document.document_id) });
    } catch (error) {
      window.alert(error.message);
    }
  }
  async function restoreVersion(version) {
    if (!window.confirm(`Restore ${version.version}? This creates a new version.`)) return;
    try {
      await adminRestoreDocumentVersion(user, versionPanel.document.document_id, version.version_number);
      setVersionPanel(null);
      onChanged();
    } catch (error) {
      window.alert(error.message);
    }
  }
  return <><div className="admin-table-card"><table className="admin-table"><thead><tr><th>Filename</th><th>Category</th><th>Version</th><th>Uploaded</th><th>Uploaded by</th><th>Status</th><th>Actions</th></tr></thead><tbody>
    {documents.map((document) => <tr key={document.document_id}><td><strong>{document.filename}</strong><small>{document.title}</small></td><td>{document.category}</td><td className="admin-version"><strong>{document.version || "v1"}</strong><button className="text-button version-history-button" type="button" onClick={() => openVersions(document)}>History</button></td><td>{formatDateTime(document.uploaded_at)}</td><td>{document.uploaded_by || "Unknown"}</td><td><span className={`status-badge ${document.status}`}>{document.status}</span></td><td><button className="text-button" type="button" onClick={() => openEditor(document)}>Edit content</button> <button className="text-button" type="button" onClick={async () => { const title = window.prompt("Document title", document.title); if (title) { await adminUpdateDocument(user, document.document_id, title, document.category); onChanged(); } }}>Metadata</button> <button className="text-button" type="button" onClick={async () => { await adminProcessDocument(user, document.document_id); onChanged(); }}>Process</button> <button className="text-button" type="button" onClick={async () => { if (window.confirm(`Remove ${document.filename}?`)) { await adminDeleteDocument(user, document.document_id); onChanged(); } }}>Delete</button></td></tr>)}
    {!documents.length && <tr><td colSpan="7" className="admin-empty">No documents have been published.</td></tr>}
  </tbody></table></div>{editor && <div className="admin-modal-backdrop" role="presentation"><section className="admin-modal document-editor" role="dialog" aria-modal="true"><header><div><h2>Edit {editor.document.filename}</h2><p className="admin-metric-note">Edit extracted text, then save to republish and re-index this document.</p></div><button className="icon-button" type="button" onClick={() => setEditor(null)}>×</button></header>{editor.warning && <p className="notice warning">{editor.warning}</p>}<textarea className="document-editor-textarea" value={editor.content} onChange={(event) => setEditor({ ...editor, content: event.target.value })} spellCheck="false" /><div className="document-editor-actions"><button className="button secondary" type="button" onClick={() => setEditor(null)}>Cancel</button><button className="button primary" type="button" disabled={editor.saving || !editor.content.trim()} onClick={saveEditor}>{editor.saving ? "Saving and processing…" : "Save and process"}</button></div></section></div>}{versionPanel && <div className="admin-modal-backdrop" role="presentation"><section className="admin-modal version-history-modal" role="dialog" aria-modal="true"><header><div><h2>Version history</h2><p className="admin-metric-note">{versionPanel.document.filename}</p></div><button className="icon-button" type="button" onClick={() => setVersionPanel(null)}>×</button></header><div className="version-history-list">{versionPanel.versions.map((version) => <article className={version.is_current ? "current" : ""} key={version.version_id}><div><strong>{version.version}</strong>{version.is_current && <span className="status-badge active">Current</span>}<small>{formatDateTime(version.uploaded_at)} · {version.uploaded_by || "Unknown"}</small></div><button className="text-button" type="button" disabled={version.is_current} onClick={() => restoreVersion(version)}>Restore</button></article>)}</div></section></div>}</>;
}

function LegacyUserTable({ users, user, onChanged }) {
  return <div className="admin-table-card"><table className="admin-table"><thead><tr><th>User</th><th>Role</th><th>Verified</th><th>Status</th><th>Actions</th></tr></thead><tbody>
    {users.map((item) => <tr key={item.user_id}><td><strong>{item.name || "Unnamed user"}</strong><small>{item.email || item.user_id}</small></td><td><select value={item.role} disabled={item.user_id === user.uid} onChange={async (event) => { await adminUpdateRole(user, item.user_id, event.target.value); onChanged(); }}><option value="passenger">Passenger</option><option value="admin">Administrator</option></select></td><td>{item.email_verified ? "Yes" : "No"}</td><td>{item.disabled ? "Disabled" : "Active"}</td><td><button className="text-button" type="button" disabled={item.user_id === user.uid} onClick={async () => { if (window.confirm(`${item.disabled ? "Enable" : "Disable"} this account?`)) { await adminUpdateStatus(user, item.user_id, !item.disabled); onChanged(); } }}>{item.disabled ? "Enable" : "Disable"}</button></td></tr>)}
    {!users.length && <tr><td colSpan="5" className="admin-empty">No users found.</td></tr>}
  </tbody></table></div>;
}

function LegacyConversationTable({ conversations, onSelect }) {
  return <div className="admin-table-card"><table className="admin-table"><thead><tr><th>Conversation</th><th>User email</th><th>Language</th><th>Started</th><th>Messages</th><th>User messages</th><th>Status</th><th /></tr></thead><tbody>
    {conversations.map((item) => <tr key={item.conversation_id}><td><strong className="conversation-title">{item.title || item.first_customer_message || "Untitled conversation"}</strong></td><td>{item.user_email || "Unavailable"}</td><td>{languageLabel(item.language)}</td><td>{formatDateTime(item.started_at)}</td><td>{item.message_count}</td><td>{item.customer_message_count}</td><td><span className={`status-badge ${item.status}`}>{item.status}</span></td><td><button className="text-button" type="button" onClick={() => onSelect(item.conversation_id)}>Inspect</button></td></tr>)}
    {!conversations.length && <tr><td colSpan="8" className="admin-empty">No conversations found.</td></tr>}
  </tbody></table></div>;
}

const tablePageCache = new Map();

function useAdminTablePage(user, namespace, loader, filters, revision) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const filterKey = JSON.stringify(filters);

  useEffect(() => {
    const controller = new AbortController();
    const cacheKey = `${namespace}:${filterKey}:${revision}`;
    const cached = tablePageCache.get(cacheKey);
    if (cached && Date.now() - cached.savedAt < 30_000) {
      setData(cached.data);
      setLoading(false);
      setError(null);
      return () => controller.abort();
    }
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(async () => {
      try {
        const result = await loader(user, JSON.parse(filterKey), controller.signal);
        tablePageCache.set(cacheKey, { data: result, savedAt: Date.now() });
        setData(result);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason.message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, filters.search ? 300 : 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [user, namespace, loader, filterKey, revision, filters.search]);

  return { data, setData, loading, error };
}

function TablePagination({ page, pageSize, total, noun, onPage, onPageSize }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const start = total ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(page * pageSize, total);
  const firstPage = Math.max(1, Math.min(page - 2, pageCount - 4));
  const visiblePages = Array.from(
    { length: Math.min(5, pageCount) },
    (_, index) => firstPage + index,
  );
  return <div className="admin-pagination table-pagination"><p>Showing {start}–{end} of {total} {noun}</p><label>Rows per page<select value={pageSize} onChange={(event) => onPageSize(Number(event.target.value))}><option value="10">10</option><option value="20">20</option><option value="50">50</option></select></label><div><button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>{visiblePages.map((number) => <button className={number === page ? "selected" : ""} type="button" key={number} onClick={() => onPage(number)} aria-current={number === page ? "page" : undefined}>{number}</button>)}<button type="button" disabled={page >= pageCount} onClick={() => onPage(page + 1)}>Next</button></div></div>;
}

function TableLoadingRows({ columns }) {
  return <tr className="table-loading-row"><td colSpan={columns}><span /><span /><span /></td></tr>;
}

function DocumentTable({ user, revision }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [processingStatus, setProcessingStatus] = useState("");
  const [sortOrder, setSortOrder] = useState("newest");
  const [refresh, setRefresh] = useState(0);
  const [editor, setEditor] = useState(null);
  const [versionPanel, setVersionPanel] = useState(null);
  const filters = { page, page_size: pageSize, search, category, processing_status: processingStatus, sort_order: sortOrder };
  const { data, loading, error } = useAdminTablePage(user, "documents", adminDocumentPage, filters, revision + refresh);
  function reset(setter, value) { setter(value); setPage(1); }
  async function openEditor(document) { try { const content = await adminDocumentContent(user, document.document_id); setEditor({ document, content: content.content, warning: content.warning, saving: false }); } catch (reason) { window.alert(reason.message); } }
  async function saveEditor() { setEditor((value) => ({ ...value, saving: true })); try { await adminSaveDocumentContent(user, editor.document.document_id, editor.content); setEditor(null); setRefresh((value) => value + 1); } catch (reason) { setEditor((value) => ({ ...value, saving: false })); window.alert(reason.message); } }
  async function openVersions(document) { try { setVersionPanel({ document, versions: await adminDocumentVersions(user, document.document_id) }); } catch (reason) { window.alert(reason.message); } }
  async function restoreVersion(version) { if (!window.confirm(`Restore ${version.version}? This creates a new version.`)) return; try { await adminRestoreDocumentVersion(user, versionPanel.document.document_id, version.version_number); setVersionPanel(null); setRefresh((value) => value + 1); } catch (reason) { window.alert(reason.message); } }
  const documents = data?.items || [];
  return <><section className="admin-card table-management"><div className="admin-toolbar table-filter-toolbar"><label className="admin-search-field"><span className="sr-only">Search documents</span><input type="search" placeholder="Search documents..." value={search} onChange={(event) => reset(setSearch, event.target.value)} /></label><label>Category<select value={category} onChange={(event) => reset(setCategory, event.target.value)}><option value="">All categories</option>{(data?.categories || []).map((value) => <option key={value} value={value}>{value}</option>)}</select></label><label>Processing status<select value={processingStatus} onChange={(event) => reset(setProcessingStatus, event.target.value)}><option value="">All statuses</option>{(data?.statuses || []).map((value) => <option key={value} value={value}>{readableEventName(value)}</option>)}</select></label><label>Upload date<select value={sortOrder} onChange={(event) => reset(setSortOrder, event.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></label></div>{error && <p className="notice error">{error}</p>}<div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>Filename</th><th>Category</th><th>Version</th><th>Uploaded</th><th>Uploaded by</th><th>Status</th><th>Actions</th></tr></thead><tbody>{loading ? <TableLoadingRows columns={7} /> : documents.map((document) => <tr key={document.document_id}><td><strong>{document.filename}</strong><small>{document.title}</small></td><td>{document.category}</td><td><strong>{document.version || "v1"}</strong><button className="text-button" type="button" onClick={() => openVersions(document)}>History</button></td><td>{formatDateTime(document.uploaded_at)}</td><td>{document.uploaded_by || "Unknown"}</td><td><span className={`status-badge ${document.status}`}>{readableEventName(document.status)}</span></td><td><button className="text-button" type="button" onClick={() => openEditor(document)}>Edit content</button> <button className="text-button" type="button" onClick={async () => { const title = window.prompt("Document title", document.title); if (title) { await adminUpdateDocument(user, document.document_id, title, document.category); setRefresh((value) => value + 1); } }}>Metadata</button> <button className="text-button" type="button" onClick={async () => { await adminProcessDocument(user, document.document_id); setRefresh((value) => value + 1); }}>Process</button> <button className="text-button" type="button" onClick={async () => { if (window.confirm(`Remove ${document.filename}?`)) { await adminDeleteDocument(user, document.document_id); setRefresh((value) => value + 1); } }}>Delete</button></td></tr>)}{!loading && !documents.length && <tr><td colSpan="7" className="admin-empty">No documents match the selected filters.</td></tr>}</tbody></table></div><TablePagination page={page} pageSize={pageSize} total={data?.total || 0} noun="documents" onPage={setPage} onPageSize={(value) => { setPageSize(value); setPage(1); }} /></section>{editor && <div className="admin-modal-backdrop"><section className="admin-modal document-editor" role="dialog" aria-modal="true"><header><div><h2>Edit {editor.document.filename}</h2><p>Edit extracted text, then save to republish and re-index.</p></div><button className="icon-button" type="button" onClick={() => setEditor(null)}>×</button></header>{editor.warning && <p className="notice warning">{editor.warning}</p>}<textarea className="document-editor-textarea" value={editor.content} onChange={(event) => setEditor({ ...editor, content: event.target.value })} /><div className="document-editor-actions"><button className="button secondary" type="button" onClick={() => setEditor(null)}>Cancel</button><button className="button primary" type="button" disabled={editor.saving || !editor.content.trim()} onClick={saveEditor}>{editor.saving ? "Saving and processing..." : "Save and process"}</button></div></section></div>}{versionPanel && <div className="admin-modal-backdrop"><section className="admin-modal" role="dialog" aria-modal="true"><header><div><h2>Version history</h2><p>{versionPanel.document.filename}</p></div><button className="icon-button" type="button" onClick={() => setVersionPanel(null)}>×</button></header><div className="version-history-list">{versionPanel.versions.map((version) => <article className={version.is_current ? "current" : ""} key={version.version_id}><div><strong>{version.version}</strong>{version.is_current && <span className="status-badge active">Current</span>}<small>{formatDateTime(version.uploaded_at)} · {version.uploaded_by || "Unknown"}</small></div><button className="text-button" type="button" disabled={version.is_current} onClick={() => restoreVersion(version)}>Restore</button></article>)}</div></section></div>}</>;
}

function UserTable({ user, revision }) {
  const [page, setPage] = useState(1); const [pageSize, setPageSize] = useState(10); const [search, setSearch] = useState(""); const [role, setRole] = useState(""); const [accountStatus, setAccountStatus] = useState(""); const [verification, setVerification] = useState(""); const [refresh, setRefresh] = useState(0);
  const filters = { page, page_size: pageSize, search, role, account_status: accountStatus, verification };
  const { data, setData, loading, error } = useAdminTablePage(user, "users", adminUserPage, filters, revision + refresh);
  function reset(setter, value) { setter(value); setPage(1); }
  function replace(updated) {
    // Update immediately, then refresh only the current filtered page so a
    // role/status change can remove a row that no longer matches its filters.
    setData((current) => ({
      ...current,
      items: current.items.map((item) => (
        item.user_id === updated.user_id ? updated : item
      )),
    }));
    tablePageCache.clear();
    setRefresh((value) => value + 1);
  }
  const users = data?.items || [];
  return <section className="admin-card table-management"><div className="admin-toolbar table-filter-toolbar"><label className="admin-search-field"><span className="sr-only">Search users</span><input type="search" placeholder="Search users..." value={search} onChange={(event) => reset(setSearch, event.target.value)} /></label><label>Role<select value={role} onChange={(event) => reset(setRole, event.target.value)}><option value="">All roles</option><option value="admin">Administrator</option><option value="passenger">Passenger</option></select></label><label>Account status<select value={accountStatus} onChange={(event) => reset(setAccountStatus, event.target.value)}><option value="">All statuses</option><option value="active">Active</option><option value="disabled">Disabled</option></select></label><label>Email verification<select value={verification} onChange={(event) => reset(setVerification, event.target.value)}><option value="">All</option><option value="verified">Verified</option><option value="not_verified">Not verified</option></select></label></div>{error && <p className="notice error">{error}</p>}<div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>User</th><th>Role</th><th>Verified</th><th>Status</th><th>Actions</th></tr></thead><tbody>{loading ? <TableLoadingRows columns={5} /> : users.map((item) => <tr key={item.user_id}><td><strong>{item.name || "Unnamed user"}</strong><small>{item.email || item.user_id}</small></td><td><select value={item.role} disabled={item.user_id === user.uid} onChange={async (event) => replace(await adminUpdateRole(user, item.user_id, event.target.value))}><option value="passenger">Passenger</option><option value="admin">Administrator</option></select></td><td>{item.email_verified ? "Yes" : "No"}</td><td>{item.disabled ? "Disabled" : "Active"}</td><td><button className="text-button" type="button" disabled={item.user_id === user.uid} onClick={async () => { if (window.confirm(`${item.disabled ? "Enable" : "Disable"} this account?`)) replace(await adminUpdateStatus(user, item.user_id, !item.disabled)); }}>{item.disabled ? "Enable" : "Disable"}</button></td></tr>)}{!loading && !users.length && <tr><td colSpan="5" className="admin-empty">No users match the selected filters.</td></tr>}</tbody></table></div><TablePagination page={page} pageSize={pageSize} total={data?.total || 0} noun="users" onPage={setPage} onPageSize={(value) => { setPageSize(value); setPage(1); }} /></section>;
}

function ConversationTable({ user, revision, onSelect }) {
  const [page, setPage] = useState(1); const [pageSize, setPageSize] = useState(10); const [search, setSearch] = useState(""); const [language, setLanguage] = useState(""); const [status, setStatus] = useState(""); const [dateFrom, setDateFrom] = useState(""); const [dateTo, setDateTo] = useState(""); const [sortOrder, setSortOrder] = useState("newest");
  const filters = { page, page_size: pageSize, search, language, conversation_status: status, started_from: dateFrom ? `${dateFrom}T00:00:00Z` : "", started_to: dateTo ? `${dateTo}T23:59:59Z` : "", sort_order: sortOrder };
  const { data, loading, error } = useAdminTablePage(user, "conversations", adminConversationPage, filters, revision);
  function reset(setter, value) { setter(value); setPage(1); }
  const conversations = data?.items || [];
  return <section className="admin-card table-management"><div className="admin-toolbar conversation-filter-toolbar"><label className="admin-search-field"><span className="sr-only">Search conversations</span><input type="search" placeholder="Search conversations..." value={search} onChange={(event) => reset(setSearch, event.target.value)} /></label><label>Language<select value={language} onChange={(event) => reset(setLanguage, event.target.value)}><option value="">All languages</option><option value="en">English</option><option value="sw">Kiswahili</option><option value="mixed">Mixed English/Kiswahili</option></select></label><label>Status<select value={status} onChange={(event) => reset(setStatus, event.target.value)}><option value="">All statuses</option><option value="active">Active</option><option value="ended">Ended</option></select></label><label>Start date<input type="date" value={dateFrom} onChange={(event) => reset(setDateFrom, event.target.value)} /></label><label>End date<input type="date" value={dateTo} onChange={(event) => reset(setDateTo, event.target.value)} /></label><label>Started<select value={sortOrder} onChange={(event) => reset(setSortOrder, event.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></label></div>{error && <p className="notice error">{error}</p>}<div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>Conversation</th><th>User email</th><th>Language</th><th>Started</th><th>Messages</th><th>User messages</th><th>Status</th><th /></tr></thead><tbody>{loading ? <TableLoadingRows columns={8} /> : conversations.map((item) => <tr key={item.conversation_id}><td><strong className="conversation-title">{item.title || "Untitled conversation"}</strong></td><td>{item.user_email || "Unavailable"}</td><td>{languageLabel(item.language)}</td><td>{formatDateTime(item.started_at)}</td><td>{item.message_count}</td><td>{item.customer_message_count}</td><td><span className={`status-badge ${item.status}`}>{readableEventName(item.status)}</span></td><td><button className="text-button" type="button" onClick={() => onSelect(item.conversation_id)}>Inspect</button></td></tr>)}{!loading && !conversations.length && <tr><td colSpan="8" className="admin-empty">No conversations match the selected filters.</td></tr>}</tbody></table></div><TablePagination page={page} pageSize={pageSize} total={data?.total || 0} noun="conversations" onPage={setPage} onPageSize={(value) => { setPageSize(value); setPage(1); }} /></section>;
}

function ConversationModal({ detail, onClose }) {
  return <div className="admin-modal-backdrop" role="presentation" onClick={onClose}><section className="admin-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}><header><h2>{detail.conversation.title}</h2><button className="icon-button" type="button" onClick={onClose}>×</button></header><div className="admin-transcript">{detail.messages.map((message) => <article className={`admin-transcript-message ${message.sender_type}`} key={message.message_id}><small>{message.sender_type} · {formatDate(message.timestamp)}</small>{message.sender_type === "chatbot" ? <FormattedAnswer text={message.message_text} /> : <p>{message.message_text}</p>}{message.model_id && <small>Model: {message.model_id}</small>}{message.retrieved_chunks?.length > 0 && <div className="admin-source-list"><strong>Sources used</strong>{message.retrieved_chunks.map((source) => <span key={source.message_chunk_id}>{source.citation_id} · {source.source_title || source.chunk_id} ({Math.round(source.similarity_score * 100)}% similar)</span>)}</div>}</article>)}</div></section></div>;
}
