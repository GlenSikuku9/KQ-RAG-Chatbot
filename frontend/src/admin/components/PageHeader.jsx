import { CalendarDays } from "lucide-react";
// Keep non-component module state private so React Fast Refresh sees a
// component-only public API for this file.
const PAGE_DESCRIPTIONS = {
  Overview: "A concise summary of current chatbot activity.",
  Documents: "Manage the published knowledge base and document processing.",
  Users: "Review passenger accounts, roles and access status.",
  Conversations: "Inspect saved conversations and their retrieval evidence.",
  "Human Support": "Manage passenger requests requiring human assistance.",
  Analytics:
    "Explore chatbot usage, response outcomes and service performance.",
  Models:
    "Monitor configured AI providers and compare real evaluation results.",
  Logs: "Review administrative actions and operational diagnostics.",
  Settings: "Configure the passenger-facing chatbot appearance.",
};
export function PageHeader({ page, periodDays, onPeriodChange, onUpload }) {
  return (
    <header
      className={`admin-header ${page === "Overview" ? "overview-header" : ""}`}
    >
      <div>
        <p className="eyebrow">ADMINISTRATION</p>
        <h1>{page}</h1>
        <p className="admin-header-description">{PAGE_DESCRIPTIONS[page]}</p>
        {page === "Overview" && (
          <span className="admin-title-rule" aria-hidden="true" />
        )}
      </div>
      {(page === "Overview" || page === "Analytics") && (
        <label className="admin-period">
          <span>
            <CalendarDays size={16} /> Reporting period
          </span>
          <select
            value={periodDays}
            onChange={(event) => onPeriodChange(Number(event.target.value))}
          >
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="90">Last 90 days</option>
          </select>
        </label>
      )}
      {page === "Documents" && (
        <label className="button primary admin-upload">
          Upload document
          <input type="file" accept=".docx" onChange={onUpload} />
        </label>
      )}
    </header>
  );
}
