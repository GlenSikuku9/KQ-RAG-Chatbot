const baseUrl = `${import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"}/api/v1`;

/** Send the current Firebase ID token with every protected backend request. */
export async function authenticatedRequest(user, path, init = {}) {
  const token = await user.getIdToken();

  let response;

  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...init.headers,
      },
    });
  } catch {
    // Fetch errors have no HTTP status, which normally means the API is not running.
    throw new Error(
      "Cannot reach the KQ Support API. Start the backend and try again.",
    );
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
