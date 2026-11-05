import {
  useEffect,
  useRef,
  useState,
} from "react";
import {
  adminUpdateRole,
  adminUpdateStatus,
  adminUserPage,
} from "../../api";
import {
  Pagination as TablePagination,
  TableLoadingRows,
} from "../components/Pagination";
import { tablePageCache, useAdminTablePage } from "../hooks/useAdminTablePage";
import "../styles/Users.css";

function UserActionDialog({ action, loading, onCancel, onConfirm }) {
  const dialog = useRef(null);
  const isRoleChange = action.type === "role";
  const accountName = action.user.name
    || action.user.email
    || "this user";
  const title = isRoleChange
    ? "Confirm role change"
    : `Confirm account ${action.nextValue ? "disablement" : "enablement"}`;

  useEffect(() => {
    dialog.current?.focus();

    function closeOnEscape(event) {
      if (event.key === "Escape" && !loading) {
        onCancel();
      }
    }

    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [loading, onCancel]);

  return (
    <div
      className="user-confirm-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !loading) {
          onCancel();
        }
      }}
    >
      <section
        className="user-confirm-dialog"
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-confirm-title"
        tabIndex="-1"
      >
        <p className="eyebrow">ACCOUNT MANAGEMENT</p>
        <h2 id="user-confirm-title">{title}</h2>
        <p className="user-confirm-intro">
          Review this administrative action before applying it.
        </p>
        <dl>
          <div>
            <dt>Affected user</dt>
            <dd>{accountName}</dd>
          </div>
          {isRoleChange ? (
            <>
              <div>
                <dt>Previous role</dt>
                <dd>{action.user.role}</dd>
              </div>
              <div>
                <dt>Proposed role</dt>
                <dd>{action.nextValue}</dd>
              </div>
            </>
          ) : (
            <div>
              <dt>New account status</dt>
              <dd>{action.nextValue ? "Disabled" : "Active"}</dd>
            </div>
          )}
        </dl>
        <div className="user-confirm-actions">
          <button
            className="user-dialog-button secondary"
            type="button"
            disabled={loading}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="user-dialog-button primary"
            type="button"
            disabled={loading}
            onClick={onConfirm}
          >
            {loading ? "Updating..." : "Confirm change"}
          </button>
        </div>
      </section>
    </div>
  );
}

function UserTable({ user, revision }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [accountStatus, setAccountStatus] = useState("");
  const [verification, setVerification] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [pendingAction, setPendingAction] = useState(null);
  const [mutationLoading, setMutationLoading] = useState(false);
  const [notice, setNotice] = useState(null);
  const filters = {
    page,
    page_size: pageSize,
    search,
    role,
    account_status: accountStatus,
    verification,
  };
  const { data, setData, loading, error } = useAdminTablePage(
    user,
    "users",
    adminUserPage,
    filters,
    revision + refresh,
  );
  function reset(setter, value) {
    setter(value);
    setPage(1);
  }

  function clearFilters() {
    setSearch("");
    setRole("");
    setAccountStatus("");
    setVerification("");
    setPage(1);
  }

  function replace(updated) {
    // Apply confirmed server data, then refresh the current filtered page in
    // case the changed user no longer belongs in the active filter result.
    setData((current) => ({
      ...current,
      items: current.items.map((item) =>
        item.user_id === updated.user_id ? updated : item,
      ),
    }));
    tablePageCache.clear();
    setRefresh((value) => value + 1);
  }

  async function confirmAction() {
    if (!pendingAction) {
      return;
    }

    setMutationLoading(true);
    setNotice(null);

    try {
      const updated = pendingAction.type === "role"
        ? await adminUpdateRole(
            user,
            pendingAction.user.user_id,
            pendingAction.nextValue,
          )
        : await adminUpdateStatus(
            user,
            pendingAction.user.user_id,
            pendingAction.nextValue,
          );
      replace(updated);
      setNotice({
        type: "success",
        text: pendingAction.type === "role"
          ? "User role updated successfully."
          : "Account status updated successfully.",
      });
      setPendingAction(null);
    } catch (reason) {
      setNotice({
        type: "error",
        text: reason instanceof Error
          ? reason.message
          : "The account update could not be completed.",
      });
    } finally {
      setMutationLoading(false);
    }
  }

  const users = data?.items || [];
  const filtersActive = Boolean(
    search
    || role
    || accountStatus
    || verification,
  );

  return (
    <section className="admin-card table-management">
      <div className="admin-toolbar table-filter-toolbar">
        <label className="admin-search-field">
          <span>Search</span>
          <input
            type="search"
            placeholder="Search users..."
            value={search}
            onChange={(event) => reset(setSearch, event.target.value)}
          />
        </label>
        <label>
          Role
          <select
            value={role}
            onChange={(event) => reset(setRole, event.target.value)}
          >
            <option value="">All roles</option>
            <option value="admin">Administrator</option>
            <option value="passenger">Passenger</option>
          </select>
        </label>
        <label>
          Account status
          <select
            value={accountStatus}
            onChange={(event) => reset(setAccountStatus, event.target.value)}
          >
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="disabled">Disabled</option>
          </select>
        </label>
        <label>
          Email verification
          <select
            value={verification}
            onChange={(event) => reset(setVerification, event.target.value)}
          >
            <option value="">All</option>
            <option value="verified">Verified</option>
            <option value="not_verified">Not verified</option>
          </select>
        </label>
        {filtersActive && (
          <button
            className="users-clear-filters"
            type="button"
            onClick={clearFilters}
          >
            Clear filters
          </button>
        )}
      </div>
      {error && <p className="notice error">{error}</p>}
      {notice && (
        <p className={`notice ${notice.type}`} role="status">
          {notice.text}
        </p>
      )}
      <div className="admin-table-scroll">
        <table className="admin-table users-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>Verified</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <TableLoadingRows columns={5} />
            ) : (
              users.map((item) => (
                <tr key={item.user_id}>
                  <td>
                    <strong>{item.name || "Name not provided"}</strong>
                    <small>{item.email || item.user_id}</small>
                  </td>
                  <td>
                    <select
                      className="user-role-select"
                      value={item.role}
                      aria-label={`Role for ${item.email || item.user_id}`}
                      title={
                        item.user_id === user.uid
                          ? "You cannot change your own administrator role."
                          : "Change user role"
                      }
                      disabled={
                        item.user_id === user.uid
                        || mutationLoading
                      }
                      onChange={(event) =>
                        setPendingAction({
                          type: "role",
                          user: item,
                          nextValue: event.target.value,
                        })}
                    >
                      <option value="passenger">Passenger</option>
                      <option value="admin">Administrator</option>
                    </select>
                  </td>
                  <td>
                    <span
                      className={`user-badge ${
                        item.email_verified ? "verified" : "unverified"
                      }`}
                    >
                      {item.email_verified ? "Verified" : "Not verified"}
                    </span>
                  </td>
                  <td>
                    <span
                      className={`user-badge ${
                        item.disabled ? "disabled" : "active"
                      }`}
                    >
                      {item.disabled ? "Disabled" : "Active"}
                    </span>
                  </td>
                  <td>
                    <button
                      className={`user-status-button ${
                        item.disabled ? "enable" : "disable"
                      }`}
                      type="button"
                      title={
                        item.user_id === user.uid
                          ? "You cannot disable your own account."
                          : `${item.disabled ? "Enable" : "Disable"} account`
                      }
                      disabled={
                        item.user_id === user.uid
                        || mutationLoading
                      }
                      onClick={() =>
                        setPendingAction({
                          type: "status",
                          user: item,
                          nextValue: !item.disabled,
                        })}
                    >
                      {item.disabled ? "Enable" : "Disable"}
                    </button>
                  </td>
                </tr>
              ))
            )}
            {!loading && !users.length && (
              <tr>
                <td colSpan="5" className="admin-empty">
                  No users match the selected filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <TablePagination
        page={page}
        pageSize={pageSize}
        total={data?.total || 0}
        noun="users"
        onPage={setPage}
        onPageSize={(value) => {
          setPageSize(value);
          setPage(1);
        }}
      />
      {pendingAction && (
        <UserActionDialog
          action={pendingAction}
          loading={mutationLoading}
          onCancel={() => setPendingAction(null)}
          onConfirm={confirmAction}
        />
      )}
    </section>
  );
}
export default UserTable;
