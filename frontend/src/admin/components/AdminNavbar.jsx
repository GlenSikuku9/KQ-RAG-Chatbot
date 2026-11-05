import { useEffect, useRef, useState } from "react";
import {
  Bell,
  ChevronDown,
  FileText,
  MessageSquare,
  Search,
  UsersRound,
} from "lucide-react";
import {
  adminMarkAllNotificationsRead,
  adminMarkNotificationRead,
  adminNotifications,
  adminSearch,
} from "../../api";
import { formatDateTime } from "../utils";
import { subscribeToSupportEvents } from "../../supportRealtime";

function AdminNavbar({
  user,
  profile,
  onNavigate,
  onOpenConversation,
  onOpenLog,
  onOpenSupportRequest,
  onSignOut,
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searchState, setSearchState] = useState("idle");
  const [searchOpen, setSearchOpen] = useState(false);
  const [activeResult, setActiveResult] = useState(-1);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [notificationsState, setNotificationsState] = useState("loading");
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
    let cancelled = false;

    async function loadNotifications() {
      try {
        const items = await adminNotifications(user);
        if (!cancelled) {
          setNotifications(items);
          setNotificationsState("ready");
        }
      } catch {
        if (!cancelled) {
          setNotificationsState("error");
        }
      }
    }

    loadNotifications();
    const stopSupportEvents = subscribeToSupportEvents(
      loadNotifications,
      () => {},
    );
    // A small bounded query keeps the badge current without repeatedly
    // loading the complete Logs collection.
    const interval = window.setInterval(loadNotifications, 30000);
    return () => {
      cancelled = true;
      stopSupportEvents();
      window.clearInterval(interval);
    };
  }, [user]);

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

  async function markRead(notification) {
    if (notification.is_read) return;
    try {
      await adminMarkNotificationRead(user, notification.event_id);
      setNotifications((current) => current.map((item) => (
        item.event_id === notification.event_id
          ? { ...item, is_read: true }
          : item
      )));
    } catch {
      setNotificationsState("error");
    }
  }

  async function markAllRead() {
    try {
      const items = await adminMarkAllNotificationsRead(user);
      setNotifications(items);
      setNotificationsState("ready");
    } catch {
      setNotificationsState("error");
    }
  }

  async function openNotification(notification) {
    await markRead(notification);
    setNotificationsOpen(false);
    if (notification.category === "human_support") {
      onOpenSupportRequest(notification.resource_id);
      return;
    }
    onOpenLog(notification.event_id);
  }

  const groups = [
    { type: "user", label: "Users", icon: UsersRound },
    { type: "conversation", label: "Conversations", icon: MessageSquare },
    { type: "document", label: "Documents", icon: FileText },
  ];
  const unreadCount = notifications.filter((item) => !item.is_read).length;
  const [notificationFilter, setNotificationFilter] = useState("all");
  const visibleNotifications = notifications.filter((item) => (
    notificationFilter === "all"
    || item.category === notificationFilter
  ));

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
            {unreadCount > 0 && (
              <span className="notification-count" aria-hidden="true">
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}
          </button>
          {notificationsOpen && (
            <div
              aria-label="Administrator notifications"
              className="admin-popover notification-popover"
              role="dialog"
            >
              <header>
                <div>
                  <strong>Notifications</strong>
                  <small>{unreadCount} unread</small>
                </div>
                {unreadCount > 0 && (
                  <button
                    className="text-button"
                    type="button"
                    onClick={markAllRead}
                  >
                    Mark all as read
                  </button>
                )}
              </header>
              <div className="notification-filters" aria-label="Notification category">
                {[
                  ["all", "All"],
                  ["system", "System errors"],
                  ["human_support", "Human support"],
                ].map(([value, label]) => (
                  <button
                    className={notificationFilter === value ? "active" : ""}
                    type="button"
                    key={value}
                    onClick={() => setNotificationFilter(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {notificationsState === "loading" && (
                <p className="notification-empty">Loading notifications…</p>
              )}
              {notificationsState === "error" && (
                <p className="notification-error">
                  Notifications could not be refreshed.
                </p>
              )}
              {notificationsState === "ready"
                && !visibleNotifications.length && (
                <div className="notification-empty">
                  <Bell size={22} aria-hidden="true" />
                  <p>No notifications in this category.</p>
                </div>
              )}
              {visibleNotifications.length > 0 && (
                <div className="notification-list">
                  {visibleNotifications.map((notification) => (
                    <article
                      className={notification.is_read ? "read" : "unread"}
                      key={notification.notification_id}
                    >
                      <button
                        className="notification-open"
                        type="button"
                        onClick={() => openNotification(notification)}
                      >
                        <span className="notification-title-line">
                          <strong>{notification.title}</strong>
                          {!notification.is_read && (
                            <i aria-label="Unread notification" />
                          )}
                        </span>
                        <span>{notification.description}</span>
                        <time dateTime={notification.timestamp}>
                          {formatDateTime(notification.timestamp)}
                        </time>
                      </button>
                      {!notification.is_read && (
                        <button
                          className="notification-read"
                          type="button"
                          onClick={() => markRead(notification)}
                        >
                          Mark as read
                        </button>
                      )}
                    </article>
                  ))}
                </div>
              )}
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
