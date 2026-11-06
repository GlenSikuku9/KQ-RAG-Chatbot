export function AdminRefreshIndicator({ active }) {
  return (
    <div className="admin-refresh-slot">
      <p
        className={`admin-page-refresh admin-section-refresh ${
          active ? "visible" : ""
        }`}
        role="status"
        aria-hidden={!active}
      >
        <span aria-hidden="true" />
        Refreshing data...
      </p>
    </div>
  );
}
