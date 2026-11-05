import { useEffect, useRef, useState } from "react";
import {
  Bell,
  ChevronDown,
  FileText,
  MessageSquare,
  Search,
  UsersRound,
} from "lucide-react";
import { adminSearch } from "../../api";

function AdminNavbar({
  user,
  profile,
  onNavigate,
  onOpenConversation,
  onSignOut,
}) {
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
        const payload = await adminSearch(
          user,
          normalizedQuery,
          controller.signal,
        );
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
      setActiveResult(
        (current) => (current + direction + results.length) % results.length,
      );
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
            <div
              className="admin-search-results"
              id="admin-search-results"
              role="listbox"
            >
              {searchState === "loading" && <p>Searching secure records…</p>}
              {searchState === "error" && (
                <p className="search-error">
                  Search could not be completed. Try again.
                </p>
              )}
              {searchState === "ready" && !results.length && (
                <p>No matching records found.</p>
              )}
              {searchState === "ready" &&
                groups.map((group) => {
                  const items = results.filter(
                    (item) => item.result_type === group.type,
                  );
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
                            className={
                              activeResult === resultIndex ? "active" : ""
                            }
                            key={`${item.result_type}-${item.result_id}`}
                            onClick={() => chooseResult(item)}
                            onMouseEnter={() => setActiveResult(resultIndex)}
                            role="option"
                            type="button"
                          >
                            <span>
                              <GroupIcon size={17} aria-hidden="true" />
                            </span>
                            <span>
                              <strong>{item.title}</strong>
                              <small>{item.description}</small>
                            </span>
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
            <span className="admin-account-copy">
              <strong>{displayName}</strong>
              <small>Administrator</small>
            </span>
            <ChevronDown size={17} aria-hidden="true" />
          </button>
          {accountOpen && (
            <div className="admin-popover account-popover">
              <strong>Account information</strong>
              <p>{profile?.email || user?.email || "Email unavailable"}</p>
              <p className="admin-role-label">Role: Administrator</p>
              <button
                className="button secondary"
                type="button"
                onClick={() => {
                  setAccountOpen(false);
                  onSignOut();
                }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

export { AdminNavbar };
