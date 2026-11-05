import {
  Bot,
  ChartNoAxesCombined,
  FileText,
  LayoutDashboard,
  ListChecks,
  MessagesSquare,
  Settings,
  UsersRound,
} from "lucide-react";
import { KenyaAirwaysLogo } from "../../components/KenyaAirwaysLogo";

// This navigation definition is private to the sidebar so the module exports
// React components only, which keeps Vite Fast Refresh compatible.
const ADMIN_PAGES = [
  { name: "Overview", icon: LayoutDashboard },
  { name: "Documents", icon: FileText },
  { name: "Users", icon: UsersRound },
  { name: "Conversations", icon: MessagesSquare },
  { name: "Analytics", icon: ChartNoAxesCombined },
  { name: "Models", icon: Bot },
  { name: "Logs", icon: ListChecks },
  { name: "Settings", icon: Settings },
];

export function AdminSidebar({ page, profile, onNavigate }) {
  function refreshDashboard() {
    // A full reload also refreshes authentication and all cached page data.
    window.location.reload();
  }

  return (
    <aside className="admin-sidebar">
      <div className="admin-brand">
        <button
          className="admin-brand-refresh"
          type="button"
          onClick={refreshDashboard}
          aria-label="Refresh the administration dashboard"
          title="Refresh dashboard"
        >
          <KenyaAirwaysLogo className="admin-airline-logo" />
        </button>
      </div>
      <div className="admin-sidebar-heading">
        <p className="admin-mark">ADMINISTRATION</p>
        <p className="admin-caption">Knowledge and access management</p>
      </div>
      <nav aria-label="Administrator navigation">
        {ADMIN_PAGES.map((item) => {
          const Icon = item.icon;
          return (
            <button
              className={`admin-nav-item ${page === item.name ? "active" : ""}`}
              key={item.name}
              type="button"
              onClick={() => onNavigate(item.name)}
            >
              <Icon
                className="admin-nav-icon"
                size={19}
                strokeWidth={1.9}
                aria-hidden="true"
              />
              <span>{item.name}</span>
            </button>
          );
        })}
      </nav>
      <p className="admin-sidebar-note">
        <UsersRound size={18} aria-hidden="true" />{" "}
        <span>
          Signed in as{" "}
          <strong>{profile.name || profile.email || "administrator"}</strong>.
        </span>
      </p>
    </aside>
  );
}
