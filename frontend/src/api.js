// IPv4 avoids the common Windows localhost (::1) versus Uvicorn (127.0.0.1)
// mismatch that makes a healthy local API appear unreachable in the browser.
// In Vite development, use the same-origin proxy so changing from port 5173
// to 5174 (or another free port) cannot create a browser CORS preflight error.
// A configured absolute URL remains available for deployed environments.
const configuredApiUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const baseUrl = configuredApiUrl ? `${configuredApiUrl}/api/v1` : "/api/v1";
const REQUEST_TIMEOUT_MS = 15000;
const inFlightGetRequests = new Map();

function requestKey(scope, path) {
  return `${scope}:${path}`;
}

function shareInFlightGet(key, operation) {
  const existingRequest = inFlightGetRequests.get(key);

  if (existingRequest) {
    return existingRequest;
  }

  // Coalesce identical GETs only while they are running. Results are not kept,
  // so subsequent navigation still follows each page's normal freshness rules.
  const request = operation().finally(() => {
    inFlightGetRequests.delete(key);
  });

  inFlightGetRequests.set(key, request);
  return request;
}

/** Send the current Firebase ID token with every protected backend request. */
export function authenticatedRequest(
  user,
  path,
  init = {},
  forceRefreshToken = false,
  timeoutMs = REQUEST_TIMEOUT_MS,
) {
  const method = (init.method || "GET").toUpperCase();
  const canShareRequest =
    method === "GET"
    && !init.signal
    && !forceRefreshToken;
  const key = requestKey(user?.uid || "authenticated", path);

  const performRequest = async () => {
    const token = await user.getIdToken(forceRefreshToken);

    const request = async () => {
      // Never leave the authentication screen waiting forever when Uvicorn or
      // Firestore is unavailable; callers can then show a useful retry action.
      const controller = new AbortController();
      const timeout = window.setTimeout(
        () => controller.abort(),
        timeoutMs,
      );
      // Combine the timeout with caller cancellation so obsolete searches stop.
      const signal = init.signal && typeof AbortSignal.any === "function"
        ? AbortSignal.any([
            controller.signal,
            init.signal,
          ])
        : controller.signal;

      try {
        return await fetch(`${baseUrl}${path}`, {
          ...init,
          signal,
          headers: {
            Authorization: `Bearer ${token}`,
            ...(init.body instanceof FormData
              ? {}
              : { "Content-Type": "application/json" }),
            ...init.headers,
          },
        });
      } finally {
        window.clearTimeout(timeout);
      }
    };

    let response;

    try {
      response = await request();
    } catch (error) {
      if (init.signal?.aborted) throw error;
      // Uvicorn can briefly drop a connection while --reload replaces the process.
      await new Promise((resolve) => setTimeout(resolve, 350));

      try {
        response = await request();
      } catch (retryError) {
        if (init.signal?.aborted) throw retryError;
        throw new Error(
          `Cannot reach the KQ Support API at ${baseUrl}. Start the backend and try again.`,
        );
      }
    }

    if (!response.ok) {
      const body = await response.json().catch(() => null);

      throw new Error(
        body?.detail || "The service could not complete that request.",
      );
    }

    return response.json();
  };

  return canShareRequest
    ? shareInFlightGet(key, performRequest)
    : performRequest();
}

export function publicRequest(
  path,
  init = {},
  timeoutMs = REQUEST_TIMEOUT_MS,
) {
  const method = (init.method || "GET").toUpperCase();

  const performRequest = async () => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    let response;

    try {
      response = await fetch(`${baseUrl}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
        },
      });
    } catch {
      throw new Error(`Cannot reach the KQ Support API at ${baseUrl}.`);
    } finally {
      window.clearTimeout(timeout);
    }

    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(
        body?.detail || "The service could not complete that request.",
      );
    }

    return response.json();
  };

  return method === "GET"
    ? shareInFlightGet(requestKey("public", path), performRequest)
    : performRequest();
}

export const getPassengerTheme = () => publicRequest("/theme");
export const getPublicModels = () => publicRequest("/models/public");

/** Guest requests generate an answer but never create conversation records. */
export const sendGuestMessage = (question, language, modelId) => publicRequest(
  "/guest/messages",
  {
    method: "POST",
    body: JSON.stringify({
      question,
      language,
      model_id: modelId,
    }),
  },
  90000,
);
export const getPassengerThemeForAdmin = (user) => authenticatedRequest(user, "/admin/settings/passenger-theme");
export const savePassengerTheme = (user, theme) => authenticatedRequest(user, "/admin/settings/passenger-theme", { method: "PUT", body: JSON.stringify(theme) });

export function syncProfile(user) {
  return authenticatedRequest(user, "/auth/profile/sync", {
    method: "POST",
    body: "{}",
  }, true);
}

/** Create a passenger-owned conversation using the selected language preference. */
export function createConversation(user, language) {
  return authenticatedRequest(user, "/conversations", {
    method: "POST",
    body: JSON.stringify({ language }),
  });
}

/** Persist a conversation only when its first message can be saved with it. */
export function startConversationWithMessage(
  user,
  question,
  language,
  modelId,
) {
  return authenticatedRequest(
    user,
    "/conversations/messages",
    {
      method: "POST",
      body: JSON.stringify({
        question,
        language,
        model_id: modelId,
      }),
    },
    false,
    90000,
  );
}

/** List safe model metadata for the signed-in passenger's model picker. */
export function getAvailableModels(user) {
  return authenticatedRequest(user, "/models");
}

/** Load the caller's saved conversation summaries for the history drawer. */
export function listConversations(user) {
  return authenticatedRequest(user, "/conversations");
}

/** Reopen one caller-owned conversation with its persisted messages and sources. */
export function getConversation(user, conversationId) {
  return authenticatedRequest(user, `/conversations/${conversationId}`);
}

/** Mark the active conversation as ended before a passenger submits feedback. */
export function endConversation(user, conversationId) {
  return authenticatedRequest(user, `/conversations/${conversationId}/end`, {
    method: "POST",
  });
}

export function getConversationFeedback(user, conversationId) {
  return authenticatedRequest(user, `/feedback/conversations/${conversationId}`);
}

export function saveConversationFeedback(user, conversationId, rating, comment) {
  return authenticatedRequest(user, `/feedback/conversations/${conversationId}`, {
    method: "PUT",
    body: JSON.stringify({ rating, comment: comment || null }),
  });
}

/** Submit one question and receive the two persisted messages in sequence order. */
export function sendConversationMessage(
  user,
  conversationId,
  question,
  language,
  modelId,
) {
  return authenticatedRequest(user, `/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      question,
      language,
      model_id: modelId,
    }),
  }, false, 90000);
}

// Administrator requests remain behind the same Firebase bearer-token boundary.
export const adminDocuments = (user) => authenticatedRequest(user, "/admin/documents");
export const adminAnalytics = (user, days = 30) => authenticatedRequest(user, `/admin/analytics?days=${days}`);
export const adminModels = (user) => authenticatedRequest(user, "/admin/models");
export const adminEvaluations = (user) => authenticatedRequest(user, "/admin/evaluations");
export const adminLogs = (user) => authenticatedRequest(user, "/admin/logs");
export const adminSetDefaultModel = (user, modelId) => authenticatedRequest(user, "/admin/models/default", { method: "PUT", body: JSON.stringify({ model_id: modelId }) });
export const adminGenerateModelTest = (user, modelId, prompt) => authenticatedRequest(user, "/models/generate", { method: "POST", body: JSON.stringify({ model_id: modelId, prompt }) });
export const adminUsers = (user) => authenticatedRequest(user, "/admin/users");
export const adminConversations = (user) => authenticatedRequest(user, "/admin/conversations");
export const adminConversation = (user, id) => authenticatedRequest(user, `/admin/conversations/${id}`);
function adminPageRequest(user, path, parameters, signal) {
  const query = new URLSearchParams();
  Object.entries(parameters).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) {
      query.set(key, String(value));
    }
  });
  return authenticatedRequest(user, `${path}?${query.toString()}`, { signal });
}

// Paginated management APIs return only the current table page. Cancellation
// prevents an older debounced search from replacing newer filter results.
export const adminDocumentPage = (user, parameters, signal) => adminPageRequest(
  user,
  "/admin/document-pages",
  parameters,
  signal,
);
export const adminUserPage = (user, parameters, signal) => adminPageRequest(
  user,
  "/admin/user-pages",
  parameters,
  signal,
);
export const adminConversationPage = (user, parameters, signal) => adminPageRequest(
  user,
  "/admin/conversation-pages",
  parameters,
  signal,
);
export const adminSearch = (user, query, signal) => authenticatedRequest(
  user,
  `/admin/search?query=${encodeURIComponent(query)}&limit=6`,
  { signal },
);
export const adminUpdateRole = (user, id, role) => authenticatedRequest(user, `/admin/users/${id}/role`, { method: "PATCH", body: JSON.stringify({ role }) });
export const adminUpdateStatus = (user, id, disabled) => authenticatedRequest(user, `/admin/users/${id}/status`, { method: "PATCH", body: JSON.stringify({ disabled }) });
export const adminDeleteDocument = (user, id) => authenticatedRequest(user, `/admin/documents/${id}`, { method: "DELETE" });
export const adminProcessDocument = (user, id) => authenticatedRequest(user, `/admin/documents/${id}/process`, { method: "POST" });
export const adminUpdateDocument = (user, id, title, category) => authenticatedRequest(user, `/admin/documents/${id}`, { method: "PATCH", body: JSON.stringify({ title, category }) });
export const adminDocumentContent = (user, id) => authenticatedRequest(user, `/admin/documents/${id}/content`);
export const adminSaveDocumentContent = (user, id, content) => authenticatedRequest(user, `/admin/documents/${id}/content`, { method: "PUT", body: JSON.stringify({ content }) });
export const adminDocumentVersions = (user, id) => authenticatedRequest(user, `/admin/documents/${id}/versions`);
export const adminRestoreDocumentVersion = (user, id, version) => authenticatedRequest(user, `/admin/documents/${id}/versions/${version}/restore`, { method: "POST" });
export function adminUploadDocument(user, file) {
  const form = new FormData();
  form.append("file", file);
  return authenticatedRequest(user, "/admin/documents", { method: "POST", body: form, headers: {} });
}
