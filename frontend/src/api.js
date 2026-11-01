// IPv4 avoids the common Windows localhost (::1) versus Uvicorn (127.0.0.1)
// mismatch that makes a healthy local API appear unreachable in the browser.
const baseUrl = `${import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000"}/api/v1`;

/** Send the current Firebase ID token with every protected backend request. */
export async function authenticatedRequest(user, path, init = {}) {
  const token = await user.getIdToken();

  const request = () => fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  });

  let response;

  try {
    response = await request();
  } catch {
    // Uvicorn can briefly drop a connection while --reload replaces the process.
    await new Promise((resolve) => setTimeout(resolve, 350));

    try {
      response = await request();
    } catch {
      throw new Error(
        `Cannot reach the KQ Support API at ${baseUrl}. Start the backend and try again.`,
      );
    }
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);

    throw new Error(body?.detail || "The service could not complete that request.");
  }

  return response.json();
}

export function syncProfile(user) {
  return authenticatedRequest(user, "/auth/profile/sync", {
    method: "POST",
    body: "{}",
  });
}

/** Create a passenger-owned conversation using the selected language preference. */
export function createConversation(user, language) {
  return authenticatedRequest(user, "/conversations", {
    method: "POST",
    body: JSON.stringify({ language }),
  });
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
  });
}
