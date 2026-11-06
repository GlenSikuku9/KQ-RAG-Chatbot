import { useEffect, useRef, useState } from "react";
import {
  adminAnalytics,
  adminConversation,
  adminEvaluations,
  adminLogs,
  adminModels,
  adminUploadDocument,
  getPassengerThemeForAdmin,
} from "../api";
import { AdminLoadingSkeleton } from "./components/AdminLoadingSkeleton";
import { AdminNotice } from "./components/AdminNotice";
import { AdminNavbar } from "./components/AdminNavbar";
import { AdminSidebar } from "./components/AdminSidebar";
import { PageHeader } from "./components/PageHeader";
import Overview from "./pages/Overview";
import Documents from "./pages/Documents";
import Users from "./pages/Users";
import Conversations, { ConversationModal } from "./pages/Conversations";
import HumanSupport from "./pages/HumanSupport";
import Analytics from "./pages/Analytics";
import Models from "./pages/Models";
import Logs from "./pages/Logs";
import Settings from "./pages/Settings";
import "./styles/AdminDashboard.css";

// Operational pages expire sooner than configuration pages. This cache exists
// only for the lifetime of the authenticated dashboard session.
const PAGE_CACHE_TTL = {
  Overview: 30000,
  Documents: 60000,
  Users: 60000,
  Conversations: 15000,
  "Human Support": 5000,
  Analytics: 30000,
  Models: 60000,
  Logs: 15000,
  Settings: 60000,
};

export function AdminDashboard({ user, profile, onSignOut }) {
  const [adminProfile, setAdminProfile] = useState(profile);
  const [page, setPage] = useState("Overview");
  const [selectedConversation, setSelectedConversation] = useState(null);
  const [selectedLogId, setSelectedLogId] = useState(null);
  const [selectedSupportRequestId, setSelectedSupportRequestId] = useState(null);
  const [notice, setNotice] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [analytics, setAnalytics] = useState(null);
  const [periodDays, setPeriodDays] = useState(30);
  const [theme, setTheme] = useState(null);
  const [models, setModels] = useState([]);
  const [evaluations, setEvaluations] = useState([]);
  const [logs, setLogs] = useState([]);
  const [tableRevision, setTableRevision] = useState(0);
  const [pageRevisions, setPageRevisions] = useState({});

  // The shell coordinates shared page requests. Filtered table pages keep
  // their request state in their own components and hook.
  const pageCache = useRef(new Map());
  const inFlightRequests = useRef(new Map());
  const activeRequestKey = useRef(null);
  const loadedPages = useRef(new Set());
  useEffect(() => {
    setAdminProfile(profile);
  }, [profile]);
  function applyPageData(targetPage, data) {
    if (targetPage === "Overview" || targetPage === "Analytics")
      setAnalytics(data);
    if (targetPage === "Settings") setTheme(data);
    if (targetPage === "Models") {
      setModels(data.models);
      setEvaluations(data.evaluations);
    }
    if (targetPage === "Logs") setLogs(data);
  }
  async function fetchPageData(targetPage) {
    // These pages fetch only after mounting so hidden tables never trigger
    // duplicate, unfiltered API requests.
    if ([
      "Documents",
      "Users",
      "Conversations",
      "Human Support",
    ].includes(targetPage))
      return null;
    if (targetPage === "Overview" || targetPage === "Analytics")
      return adminAnalytics(user, periodDays);
    if (targetPage === "Settings") return getPassengerThemeForAdmin(user);
    if (targetPage === "Models") {
      // These resources are independent, so retain concurrent loading.
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
    const isFresh =
      cached && Date.now() - cached.savedAt < PAGE_CACHE_TTL[targetPage];
    if (!force && isFresh) {
      applyPageData(targetPage, cached.data);
      setLoading(false);
      return;
    }
    if (loadedPages.current.has(cacheKey)) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    activeRequestKey.current = cacheKey;
    setNotice(null);
    try {
      // Reuse identical in-flight work during rapid navigation and React's
      // development checks instead of issuing a second request.
      let request = inFlightRequests.current.get(cacheKey);
      if (!request) {
        request = fetchPageData(targetPage);
        inFlightRequests.current.set(cacheKey, request);
      }
      const data = await request;
      pageCache.current.set(cacheKey, { data, savedAt: Date.now() });
      loadedPages.current.add(cacheKey);
      if (activeRequestKey.current === cacheKey)
        applyPageData(targetPage, data);
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      inFlightRequests.current.delete(cacheKey);
      if (activeRequestKey.current === cacheKey) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }
  useEffect(() => {
    // Every navigation selection gets a fresh backend snapshot, including
    // selecting the already-active page. Identical in-flight work is shared.
    reload(true);
  }, [page, periodDays, pageRevisions[page]]);

  function navigate(targetPage) {
    setPage(targetPage);
    setPageRevisions((current) => ({
      ...current,
      [targetPage]: (current[targetPage] || 0) + 1,
    }));
  }
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

  async function openConversation(conversationId) {
    setPage("Conversations");
    setSelectedConversation(
      await adminConversation(
        user,
        conversationId,
      ),
    );
  }

  function openLog(eventId) {
    setSelectedLogId(eventId);
    setPage("Logs");
  }

  function openSupportRequest(requestId) {
    setSelectedSupportRequestId(requestId);
    setPage("Human Support");
  }

  // Each page receives only the data and callbacks it owns.
  const pageProps = {
    Overview: {
      analytics,
      onNavigate: navigate,
      onOpenConversation: openConversation,
      onOpenLog: openLog,
    },
    Analytics: { analytics },
    Settings: {
      user,
      theme,
      setTheme,
      onSaved: (message) => {
        pageCache.current.delete("Settings:default");
        setNotice({ type: "success", text: message });
      },
    },
    Models: { user, models, evaluations, onChanged: () => reload(true) },
    Logs: {
      logs,
      selectedLogId,
      onSelectedLogOpened: () => setSelectedLogId(null),
    },
    Documents: {
      user,
      revision: tableRevision + (pageRevisions.Documents || 0),
    },
    Users: {
      user,
      revision: tableRevision + (pageRevisions.Users || 0),
      onProfileChanged: (updated) => {
        if (updated.user_id === user.uid) {
          setAdminProfile((current) => ({
            ...current,
            name: updated.name,
          }));
        }
      },
    },
    Conversations: {
      user,
      revision: tableRevision + (pageRevisions.Conversations || 0),
      onSelect: openConversation,
    },
    "Human Support": {
      user,
      revision: pageRevisions["Human Support"] || 0,
      initialRequestId: selectedSupportRequestId,
      onOpened: () => setSelectedSupportRequestId(null),
    },
  };
  // Render only the active page to preserve lazy fetching and local state.
  const Page = {
    Overview,
    Documents,
    Users,
    Conversations,
    "Human Support": HumanSupport,
    Analytics,
    Models,
    Logs,
    Settings,
  }[page];
  return (
    <main className="admin-shell">
      <AdminSidebar
        page={page}
        profile={adminProfile}
        onNavigate={navigate}
      />
      <section className="admin-workspace">
        <AdminNavbar
          user={user}
          profile={adminProfile}
          onNavigate={navigate}
          onOpenConversation={openConversation}
          onOpenLog={openLog}
          onOpenSupportRequest={openSupportRequest}
          onSignOut={onSignOut}
        />
        <section className="admin-content">
          <PageHeader
            page={page}
            periodDays={periodDays}
            onPeriodChange={setPeriodDays}
            onUpload={upload}
          />
          {refreshing && (
            <p className="admin-page-refresh" role="status">
              <span aria-hidden="true" />
              Refreshing data...
            </p>
          )}
          <AdminNotice
            notice={notice}
            onDismiss={() => setNotice(null)}
          />
          {loading ? <AdminLoadingSkeleton /> : <Page {...pageProps[page]} />}
          {selectedConversation && (
            <ConversationModal
              detail={selectedConversation}
              onClose={() => setSelectedConversation(null)}
            />
          )}
        </section>
      </section>
    </main>
  );
}
