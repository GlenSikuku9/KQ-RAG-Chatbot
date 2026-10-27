const baseUrl = `${import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"}/api/v1`;

/** Send the current Firebase ID token with every protected backend request. */
export async function authenticatedRequest(user, path, init = {}) {
  const token = await user.getIdToken();
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...init.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || "The service could not complete that request.");
  }
  return response.json();
}

export function syncProfile(user) {
  return authenticatedRequest(user, "/auth/profile/sync", { method: "POST", body: "{}" });
}
