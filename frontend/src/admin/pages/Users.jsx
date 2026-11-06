import {
  useEffect,
  useRef,
  useState,
} from "react";
import { EllipsisVertical } from "lucide-react";
import {
  adminDeleteUser,
  adminUpdateDisplayName,
  adminUpdateRole,
  adminUpdateStatus,
  adminUserPage,
} from "../../api";
import {
  Pagination as TablePagination,
  TableLoadingRows,
} from "../components/Pagination";
import { AdminNotice } from "../components/AdminNotice";
import { AdminRefreshIndicator } from "../components/AdminRefreshIndicator";
import { tablePageCache, useAdminTablePage } from "../hooks/useAdminTablePage";
import "../styles/Users.css";

function UserActionDialog({ action, loading, onCancel, onConfirm }) {
  const dialog = useRef(null);
  const isRoleChange = action.type === "role";
  const accountName = action.user.name
    || action.user.email
    || "this user";
  const isDelete = action.type === "delete";
  const title = isRoleChange
    ? "Confirm role change"
    : isDelete
      ? "Delete user account?"
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
          {isDelete
            ? (
              "Are you sure you want to permanently delete this account? "
              + "The user's saved conversations and personal data will also "
              + "be deleted. This action cannot be undone."
            )
            : "Review this administrative action before applying it."}
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
          ) : isDelete ? (
            <div>
              <dt>Email address</dt>
              <dd>{action.user.email || "Not available"}</dd>
            </div>
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
            className={`user-dialog-button ${
              isDelete ? "danger" : "primary"
            }`}
            type="button"
            disabled={loading}
            onClick={onConfirm}
          >
            {loading
              ? isDelete ? "Deleting..." : "Updating..."
              : isDelete ? "Delete account" : "Confirm change"}
          </button>
        </div>
      </section>
    </div>
  );
}

function UserEditDialog({ account, loading, onCancel, onSave }) {
  const [name, setName] = useState(account.name || "");
  // Collapse repeated whitespace before comparing or saving the name.
  const normalized = name.trim().split(/\s+/).join(" ");
  const original = (account.name || "").trim().split(/\s+/).join(" ");
  const valid = normalized.length >= 2;

  useEffect(() => {
    function closeOnEscape(event) {
      if (event.key === "Escape" && !loading) onCancel();
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [loading, onCancel]);

  return (
    <div
      className="user-confirm-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !loading) onCancel();
      }}
    >
      <section
        className="user-confirm-dialog user-edit-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-user-title"
      >
        <p className="eyebrow">ACCOUNT MANAGEMENT</p>
        <h2 id="edit-user-title">Edit User</h2>
        <label>
          Display name
          <input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength="120"
          />
        </label>
        <label>
          Email address
          <input value={account.email || "Not available"} readOnly />
        </label>
        <label>
          Role
          <input value={account.role} readOnly />
        </label>
        <label>
          Account status
          <input value={account.disabled ? "Disabled" : "Active"} readOnly />
        </label>
        {!valid && name.length > 0 && (
          <p className="notice error">Enter at least two characters.</p>
        )}
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
            disabled={loading || !valid || normalized === original}
            onClick={() => onSave(normalized)}
          >
            {loading ? "Saving..." : "Save changes"}
          </button>
        </div>
      </section>
    </div>
  );
}

function UserTable({ user, revision, onProfileChanged }) {
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
  const [editingUser, setEditingUser] = useState(null);
  const [openMenu, setOpenMenu] = useState(null);
  const filters = {
    page,
    page_size: pageSize,
    search,
    role,
    account_status: accountStatus,
    verification,
  };
  const {
    data,
    setData,
    loading,
    refreshing,
    error,
  } = useAdminTablePage(
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

  useEffect(() => {
    function closeMenu(event) {
      if (
        event.key === "Escape"
        || !event.target.closest?.(".user-actions")
      ) {
        setOpenMenu(null);
      }
    }
    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeMenu);
    return () => {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeMenu);
    };
  }, []);

  async function saveDisplayName(displayName) {
    setMutationLoading(true);
    setNotice(null);
    try {
      const updated = await adminUpdateDisplayName(
        user,
        editingUser.user_id,
        displayName,
      );
      replace(updated);
      onProfileChanged?.(updated);
      setEditingUser(null);
      setNotice({ type: "success", text: "Display name updated." });
    } catch (reason) {
      setNotice({ type: "error", text: reason.message });
    } finally {
      setMutationLoading(false);
    }
  }

  async function confirmAction() {
    if (!pendingAction) {
      return;
    }

    setMutationLoading(true);
    setNotice(null);

    try {
      if (pendingAction.type === "delete") {
        await adminDeleteUser(user, pendingAction.user.user_id);
        tablePageCache.clear();
        setRefresh((value) => value + 1);
        setNotice({
          type: "success",
          text: "User account deleted permanently.",
        });
        setPendingAction(null);
        return;
      }
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
    <>
      <AdminRefreshIndicator active={refreshing} />
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
      <AdminNotice type="error" message={error} />
      <AdminNotice
        notice={notice}
        onDismiss={() => setNotice(null)}
      />
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
                    <div className="user-actions">
                      <button
                        className="user-edit-button admin-edit-action"
                        type="button"
                        onClick={() => setEditingUser(item)}
                      >
                        Edit
                      </button>
                      <button
                        className="user-more-button"
                        type="button"
                        aria-label={`More actions for ${item.email || item.user_id}`}
                        aria-expanded={openMenu === item.user_id}
                        onClick={() => setOpenMenu((current) => (
                          current === item.user_id ? null : item.user_id
                        ))}
                      >
                        <EllipsisVertical aria-hidden="true" />
                      </button>
                      {openMenu === item.user_id && (
                        <div className="user-action-menu">
                          <button
                            type="button"
                            disabled={item.user_id === user.uid}
                            onClick={() => {
                              setOpenMenu(null);
                              setPendingAction({
                                type: "status",
                                user: item,
                                nextValue: !item.disabled,
                              });
                            }}
                          >
                            {item.disabled ? "Enable" : "Disable"}
                          </button>
                          <button
                            className="danger"
                            type="button"
                            disabled={item.user_id === user.uid}
                            onClick={() => {
                              setOpenMenu(null);
                              setPendingAction({
                                type: "delete",
                                user: item,
                              });
                            }}
                          >
                            Delete account
                          </button>
                        </div>
                      )}
                    </div>
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
      {editingUser && (
        <UserEditDialog
          account={editingUser}
          loading={mutationLoading}
          onCancel={() => setEditingUser(null)}
          onSave={saveDisplayName}
        />
      )}
      </section>
    </>
  );
}
export default UserTable;
