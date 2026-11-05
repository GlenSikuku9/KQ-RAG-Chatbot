import { useEffect, useRef, useState } from "react";
import {
  Menu,
  Maximize2,
  Minimize2,
  Minus,
  X,
} from "lucide-react";
import "../styles/PassengerChat.css";

import {
  cancelSupportRequest,
  convertSupportRequest,
  createSupportRequest,
  endConversation,
  getConversation,
  getConversationFeedback,
  getAvailableModels,
  getPublicModels,
  getSupportAvailability,
  getSupportRequest,
  listConversations,
  listSupportRequests,
  saveConversationFeedback,
  saveSupportFeedback,
  sendConversationMessage,
  sendGuestMessage,
  sendSupportMessage,
  startConversationWithMessage,
} from "../../api";
import { FormattedAnswer } from "../../components/FormattedAnswer";
import { KenyaAirwaysLogo } from "../../components/KenyaAirwaysLogo";
import { subscribeToSupportRequest } from "../../supportRealtime";

const LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "sw", label: "Kiswahili" },
  { value: "mixed", label: "English and Kiswahili" },
];

const SUGGESTED_QUESTIONS = [
  "What is the baggage allowance for international flights?",
  "How can I check in online?",
  "What payment methods are available?",
  "How can I request a ticket refund?",
  "What travel documents do I need?",
  "How can I change my booking?",
];

// These phrases intentionally cover clear English, Kiswahili and mixed
// requests. Ambiguous questions continue through normal RAG assistance.
const HUMAN_SUPPORT_PHRASES = [
  "human",
  "live agent",
  "support agent",
  "customer support",
  "speak",
  "talk",
  "connect me",
  "representative",
  "kuongea",
  "ongea",
  "wakala",
  "msaada wa mtu",
];
const UNRESOLVED_PHRASES = [
  "not resolved",
  "did not help",
  "didn't help",
  "still need help",
  "haijatatuliwa",
  "bado nahitaji msaada",
];


function requestsHumanSupport(text) {
  const normalized = text.toLowerCase();
  const explicitRequest = HUMAN_SUPPORT_PHRASES.some((phrase) => (
    normalized.includes(phrase)
  )) && /\b(agent|support|human|mtu|wakala|customer|kuongea|ongea)\b/i.test(
    normalized,
  );
  return explicitRequest || UNRESOLVED_PHRASES.some((phrase) => (
    normalized.includes(phrase)
  ));
}

function responseLabel(responseSource) {
  if (responseSource === "clarification") {
    return "More information needed";
  }

  if (responseSource === "fallback") {
    return "Unable to confirm from KQ information";
  }

  return "KQ Support response";
}

const MODEL_LABELS = {
  openrouter_gemma_26b: "Gemma 4 26B A4B",
  openrouter_gemma_31b: "Gemma 4 31B",
  groq_gpt_oss_120b: "GPT-OSS 120B",
};

function modelLabel(model) {
  if (!model) {
    return "Select model";
  }
  return MODEL_LABELS[model.model_id] || model.upstream_model || model.model_id;
}

function conversationTurns(messages) {
  const turns = [];

  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];

    if (message.sender_type === "customer") {
      const reply = messages[index + 1];

      if (reply?.sender_type === "chatbot") {
        turns.push({ question: message, answer: reply });
        index += 1;
      } else {
        turns.push({ question: message, answer: null });
      }
    } else {
      turns.push({ question: null, answer: message });
    }
  }

  return turns;
}

function Sources({ chunks }) {
  if (!chunks?.length) return null;

  const orderedChunks = [...chunks].sort(
    (left, right) => left.retrieval_rank - right.retrieval_rank,
  );
  return (
    <details className="message-sources">
      <summary>Sources ({orderedChunks.length})</summary>
      <div className="sources-list">
        {orderedChunks.map((chunk, index) => {
          // Keep the persisted citation when available so inline [S1]
          // references continue to match the source row shown to passengers.
          const reference = chunk.citation_id || `S${index + 1}`;
          const title = chunk.source_title || "Kenya Airways support information";
          return (
            <div
              className="source-item"
              key={
                chunk.message_chunk_id
                || `${reference}-${index}`
              }
            >
              <div className="source-row-main">
                <span className="source-reference">{reference}</span>
                <strong>{title}</strong>
              </div>
              {chunk.source_url && (
                <a
                  className="source-link"
                  href={chunk.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  View on Kenya Airways ↗
                </a>
              )}
            </div>
          );
        })}
      </div>
    </details>
  );
}

function ChatMessage({ message }) {
  const isPassenger = message.sender_type === "customer";
  const responseSource = message.response_source;
  const isFallback = responseSource === "fallback";
  const isClarification = responseSource === "clarification";

  return (
    <article className={`chat-message ${isPassenger ? "passenger" : "assistant"}`}>
      <p className="message-sender">
        {isPassenger ? "You" : responseLabel(responseSource)}
      </p>
      <div className={`message-bubble ${isFallback || isClarification ? "message-bubble-notice" : ""}`}>
        {isPassenger ? (
          <p>{message.message_text}</p>
        ) : (
          <FormattedAnswer text={message.message_text} />
        )}
      </div>
      {!isPassenger && <Sources chunks={message.retrieved_chunks} />}
    </article>
  );
}


function AgentMessage({ message }) {
  return (
    <article className="chat-message human-agent">
      <p className="message-sender">
        Support agent{message.sender_name ? ` · ${message.sender_name}` : ""}
      </p>
      <div className="message-bubble">
        <p>{message.message_text}</p>
      </div>
      <time className="support-message-time" dateTime={message.timestamp}>
        {new Date(message.timestamp).toLocaleString()}
      </time>
    </article>
  );
}


function SupportFeedbackRating({ user, request, existing, onSaved }) {
  const [rating, setRating] = useState(existing?.rating || 0);
  const [comment, setComment] = useState(existing?.comment || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onSaved(await saveSupportFeedback(user, request.request_id, {
        rating,
        comment: comment.trim() || null,
      }));
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="feedback-rating support-feedback-rating">
      <h3>How was your human support experience?</h3>
      <form onSubmit={submit}>
        <div className="rating-stars" aria-label="Human support rating">
          {[1, 2, 3, 4, 5].map((value) => (
            <button
              className={value <= rating ? "selected" : ""}
              type="button"
              key={value}
              onClick={() => setRating(value)}
              aria-label={`${value} star${value === 1 ? "" : "s"}`}
            >
              ★
            </button>
          ))}
        </div>
        <textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          placeholder="Optional comment"
          rows="2"
          maxLength="1000"
        />
        {error && <p className="notice error">{error}</p>}
        <button
          className="button feedback-submit"
          disabled={!rating || saving}
        >
          {saving ? "Saving…" : existing ? "Update rating" : "Save rating"}
        </button>
      </form>
    </section>
  );
}

function FeedbackRating({ user, conversation, existingFeedback, onSaved }) {
  const [rating, setRating] = useState(existingFeedback?.rating || 0);
  const [comment, setComment] = useState(existingFeedback?.comment || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [commentVisible, setCommentVisible] = useState(
    Boolean(existingFeedback?.comment) ||
      Boolean(existingFeedback?.rating && existingFeedback.rating <= 3),
  );
  const [confirmation, setConfirmation] = useState("");

  useEffect(() => {
    setRating(existingFeedback?.rating || 0);
    setComment(existingFeedback?.comment || "");
    // Lower ratings invite a comment immediately; positive ratings keep the
    // compact form while still offering the optional comment control.
    setCommentVisible(
      Boolean(existingFeedback?.comment) ||
        Boolean(existingFeedback?.rating && existingFeedback.rating <= 3),
    );
  }, [existingFeedback]);

  async function submit(event) {
    event.preventDefault();

    if (!rating || saving) {
      return;
    }

    setSaving(true);
    setError(null);
    setConfirmation("");

    try {
      const saved = await saveConversationFeedback(
        user,
        conversation.conversation_id,
        rating,
        comment,
      );
      onSaved(saved);
      setConfirmation(existingFeedback ? "Your rating was updated." : "Thank you. Your feedback was saved.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Feedback could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="feedback-card" aria-labelledby="feedback-heading">
      <h4 id="feedback-heading">Conversation feedback</h4>
      <p>How helpful was this conversation?</p>
      <form onSubmit={submit}>
        <div className="feedback-rating-row">
          <div className="star-row" role="radiogroup" aria-label="Rating from one to five stars">
            {[1, 2, 3, 4, 5].map((value) => (
              <button
                className={value <= rating ? "star selected" : "star"}
                key={value}
                type="button"
                role="radio"
                aria-checked={value === rating}
                aria-label={`${value} star${value === 1 ? "" : "s"}`}
                onClick={() => {
                  setRating(value);
                  setCommentVisible(value <= 3 || Boolean(comment));
                }}
              >
                ★
              </button>
            ))}
          </div>
          {rating > 0 && <span className="rating-label">{rating} out of 5</span>}
        </div>
        {!commentVisible && (
          <button
            className="feedback-comment-toggle"
            type="button"
            onClick={() => setCommentVisible(true)}
          >
            Add an optional comment
          </button>
        )}
        {commentVisible && (
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Tell us how we can improve (optional)"
            maxLength="1000"
            rows="2"
          />
        )}
        {error && <p className="notice error" role="alert">{error}</p>}
        {confirmation && <p className="feedback-confirmation" role="status">{confirmation}</p>}
        <button className="button feedback-submit" type="submit" disabled={!rating || saving}>
          {saving ? "Saving…" : existingFeedback ? "Update rating" : "Save rating"}
        </button>
      </form>
    </section>
  );
}

export function PassengerChat({
  user,
  onClose,
  onMinimize,
  onRequestAuth,
  onDrawerToggle,
  initialQuestion = "",
}) {
  const [language, setLanguage] = useState("en");
  const [conversation, setConversation] = useState(null);
  const [messages, setMessages] = useState([]);
  const [history, setHistory] = useState([]);
  const [feedback, setFeedback] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(Boolean(user));
  const [question, setQuestion] = useState("");
  const [models, setModels] = useState([]);
  const [selectedModelId, setSelectedModelId] = useState("");
  const [modelLoading, setModelLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isStarting, setIsStarting] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isEnding, setIsEnding] = useState(false);
  const [confirmEndOpen, setConfirmEndOpen] = useState(false);
  const [supportOffer, setSupportOffer] = useState(false);
  const [supportReason, setSupportReason] = useState("passenger_request");
  const [supportSummary, setSupportSummary] = useState("");
  const [supportAvailability, setSupportAvailability] = useState(null);
  const [supportRequests, setSupportRequests] = useState([]);
  const [activeSupport, setActiveSupport] = useState(null);
  const [supportDetail, setSupportDetail] = useState(null);
  const [supportState, setSupportState] = useState("idle");
  const [pendingEscalation, setPendingEscalation] = useState(null);
  const [unsuccessfulTurns, setUnsuccessfulTurns] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const pendingSubmission = useRef(false);
  const messageListRef = useRef(null);
  const modelDropdownRef = useRef(null);
  const composerFormRef = useRef(null);
  const previouslyAuthenticated = useRef(Boolean(user));

  useEffect(() => {
    // Suggested FAQ questions fill the composer but remain editable before send.
    if (initialQuestion) {
      setQuestion(initialQuestion);
    }
  }, [initialQuestion]);

  useEffect(() => {
    // Escalation timing and repeat-attempt limits remain backend-configurable.
    getSupportAvailability()
      .then(setSupportAvailability)
      .catch(() => {});
  }, []);

  useEffect(() => {
    const isAuthenticated = Boolean(user);

    if (previouslyAuthenticated.current !== isAuthenticated) {
      // Guest messages must never be promoted into a saved account. Likewise,
      // signing out removes protected conversation data from the live screen.
      setConversation(null);
      setMessages([]);
      setFeedback(null);
      setQuestion("");
      setDrawerOpen(false);
    }

    previouslyAuthenticated.current = isAuthenticated;
  }, [user]);

  useEffect(() => {
    let cancelled = false;
    if (!user) {
      setSupportRequests([]);
      setActiveSupport(null);
      setSupportDetail(null);
      return undefined;
    }

    async function loadSupportHistory() {
      try {
        const items = await listSupportRequests(user);
        if (cancelled) return;
        setSupportRequests(items);
        const current = items.find((item) => (
          item.conversation_id === conversation?.conversation_id
          && ["pending", "in_progress"].includes(item.status)
        ));
        if (current) setActiveSupport(current);
        if (pendingEscalation) {
          setSupportSummary(pendingEscalation.issue_summary);
          setSupportReason(pendingEscalation.reason);
          setSupportOffer(true);
        }
      } catch (reason) {
        if (!cancelled) setError(reason.message);
      }
    }

    loadSupportHistory();
    return () => {
      cancelled = true;
    };
  }, [conversation?.conversation_id, pendingEscalation, user]);

  useEffect(() => {
    if (!user || !activeSupport?.request_id) return undefined;
    let cancelled = false;

    async function refreshActiveSupport() {
      try {
        const detail = await getSupportRequest(
          user,
          activeSupport.request_id,
        );
        if (!cancelled) {
          setSupportDetail(detail);
          setActiveSupport(detail.request);
        }
      } catch (reason) {
        if (!cancelled) setError(reason.message);
      }
    }

    refreshActiveSupport();
    const stopRealtime = subscribeToSupportRequest(
      activeSupport.request_id,
      (request) => setActiveSupport(request),
      (nextMessages) => setSupportDetail((current) => ({
        ...(current || {}),
        request: current?.request || activeSupport,
        messages: nextMessages,
      })),
      () => {},
    );
    // A low-frequency scoped refresh repairs state after offline reconnects.
    const timer = window.setInterval(refreshActiveSupport, 15000);
    return () => {
      cancelled = true;
      stopRealtime();
      window.clearInterval(timer);
    };
  }, [activeSupport?.request_id, user]);

  useEffect(() => {
    if (onDrawerToggle) {
      onDrawerToggle(drawerOpen);
    }
  }, [drawerOpen, onDrawerToggle]);

  useEffect(() => {
    function closeDrawerOnEscape(event) {
      if (event.key === "Escape") {
        setDrawerOpen(false);
      }
    }

    if (drawerOpen) {
      document.addEventListener("keydown", closeDrawerOnEscape);
    }

    return () => document.removeEventListener("keydown", closeDrawerOnEscape);
  }, [drawerOpen]);

  useEffect(() => {
    let ignoreResponse = false;

    async function loadHistory() {
      if (!user) {
        setHistory([]);
        setHistoryLoading(false);
        return;
      }

      try {
        const conversations = await listConversations(user);
        if (!ignoreResponse) {
          setHistory(conversations);
        }
      } catch (reason) {
        if (!ignoreResponse) {
          setError(reason instanceof Error ? reason.message : "Conversation history could not be loaded.");
        }
      } finally {
        if (!ignoreResponse) {
          setHistoryLoading(false);
        }
      }
    }

    loadHistory();

    return () => {
      ignoreResponse = true;
    };
  }, [user]);

  useEffect(() => {
    let ignoreResponse = false;

    async function loadModels() {
      try {
        const availableModels = user
          ? await getAvailableModels(user)
          : await getPublicModels();
        const configuredModels = availableModels.filter((model) => model.configured);

        if (ignoreResponse) {
          return;
        }

        setModels(configuredModels);
        setSelectedModelId(
          configuredModels.find((model) => model.is_default)?.model_id
            || configuredModels[0]?.model_id
            || "",
        );
      } catch (reason) {
        if (!ignoreResponse) {
          setError(
            reason instanceof Error
              ? reason.message
              : "We could not load the available AI models.",
          );
        }
      } finally {
        if (!ignoreResponse) {
          setModelLoading(false);
        }
      }
    }

    // The browser only receives safe model names and status, never provider keys.
    loadModels();

    return () => {
      ignoreResponse = true;
    };
  }, [user]);

  useEffect(() => {
    const list = messageListRef.current;

    if (list) {
      list.scrollTop = list.scrollHeight;
    }
  }, [messages, isSending]);

  useEffect(() => {
    function handleClickOutside(event) {
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(event.target)) {
        setModelDropdownOpen(false);
      }
    }

    if (modelDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [modelDropdownOpen]);

  useEffect(() => {
    if (!confirmEndOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === "Escape" && !isEnding) {
        setConfirmEndOpen(false);
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [confirmEndOpen, isEnding]);

  async function startConversation() {
    if (isStarting || isSending || modelLoading || !selectedModelId) {
      return;
    }

    setError(null);
    setIsStarting(true);

    try {
      // New chats remain local until the first successful message exchange, so
      // opening or closing the panel cannot create an empty database record.
      const nextConversation = {
        conversation_id: crypto.randomUUID(),
        user_id: user?.uid || "guest",
        title: "Temporary conversation",
        language,
        status: "active",
        started_at: new Date().toISOString(),
        persisted: false,
      };
      setConversation(nextConversation);
      setMessages([]);
      setFeedback(null);
      setQuestion("");
      setDrawerOpen(false);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "We could not start a conversation. Please try again.",
      );
    } finally {
      setIsStarting(false);
    }
  }

  async function openConversation(conversationId) {
    if (!user) {
      onRequestAuth?.("sign-in");
      return;
    }

    setError(null);

    try {
      const detail = await getConversation(user, conversationId);
      // Render persisted messages immediately. Feedback is supplementary and must
      // never prevent an ended conversation from reopening with its history.
      setConversation(detail.conversation);
      setMessages(detail.messages);
      setDrawerOpen(false);

      try {
        const savedFeedback = await getConversationFeedback(user, conversationId);
        setFeedback(savedFeedback);
      } catch {
        setFeedback(null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That conversation could not be reopened.");
    }
  }

  async function openSupportHistory(item) {
    if (!user) return;
    setError(null);
    try {
      const conversationDetail = await getConversation(
        user,
        item.conversation_id,
      );
      setConversation(conversationDetail.conversation);
      setMessages(conversationDetail.messages);
      setActiveSupport(item);
      setDrawerOpen(false);
    } catch (reason) {
      setError(reason.message);
    }
  }

  async function finishConversation() {
    if (
      !conversation
      || conversation.status !== "active"
      || isSending
      || isEnding
    ) {
      return;
    }

    setIsEnding(true);
    setError(null);
    try {
      const ended = user
        ? await endConversation(user, conversation.conversation_id)
        : {
            ...conversation,
            status: "ended",
            ended_at: new Date().toISOString(),
          };
      setConversation(ended);
      setHistory((current) => current.map((item) => (
        item.conversation_id === ended.conversation_id ? ended : item
      )));
      setConfirmEndOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The conversation could not be ended.");
    } finally {
      setIsEnding(false);
    }
  }

  async function openSupportOffer(
    reason = "passenger_request",
    requestedSummary = "",
  ) {
    const recentPassengerMessage = [...messages]
      .reverse()
      .find((item) => item.sender_type === "customer");
    setSupportReason(reason);
    setSupportSummary(
      requestedSummary
      || recentPassengerMessage?.message_text
      || question.trim()
      || "I need assistance with my Kenya Airways enquiry.",
    );
    setSupportOffer(true);
    setSupportState("checking");
    try {
      setSupportAvailability(await getSupportAvailability());
      setSupportState("idle");
    } catch (reasonCaught) {
      setError(reasonCaught.message);
      setSupportState("error");
    }
  }

  async function submitSupportRequest() {
    const issueSummary = supportSummary.trim();
    if (!issueSummary) return;
    if (!user) {
      setPendingEscalation({
        issue_summary: issueSummary,
        reason: supportReason,
      });
      return;
    }

    const conversationId = conversation?.conversation_id
      || crypto.randomUUID();
    setSupportState("submitting");
    setError(null);
    try {
      const availability = supportAvailability
        || await getSupportAvailability();
      const created = await createSupportRequest(user, {
        conversation_id: conversationId,
        request_type: availability.available ? "live" : "ticket",
        reason: supportReason,
        issue_summary: issueSummary,
      });
      if (!conversation) {
        setConversation({
          conversation_id: conversationId,
          user_id: user.uid,
          title: issueSummary.slice(0, 120),
          language,
          status: "active",
          started_at: created.created_at,
          persisted: true,
        });
      }
      setActiveSupport(created);
      setSupportRequests((current) => [created, ...current]);
      setSupportOffer(false);
      setPendingEscalation(null);
      setSupportState("idle");
    } catch (reasonCaught) {
      setError(reasonCaught.message);
      setSupportState("error");
    }
  }

  async function cancelActiveSupport() {
    if (!user || !activeSupport) return;
    setSupportState("submitting");
    try {
      const cancelled = await cancelSupportRequest(
        user,
        activeSupport.request_id,
      );
      setActiveSupport(cancelled);
      setSupportDetail((current) => current
        ? { ...current, request: cancelled }
        : current);
    } catch (reasonCaught) {
      setError(reasonCaught.message);
    } finally {
      setSupportState("idle");
    }
  }

  async function convertActiveSupport() {
    if (!user || !activeSupport) return;
    setSupportState("submitting");
    try {
      const ticket = await convertSupportRequest(
        user,
        activeSupport.request_id,
      );
      setActiveSupport(ticket);
      setSupportDetail((current) => current
        ? { ...current, request: ticket }
        : current);
    } catch (reasonCaught) {
      setError(reasonCaught.message);
    } finally {
      setSupportState("idle");
    }
  }

  async function submitQuestion(event, submittedQuestion = question) {
    event.preventDefault();

    const trimmedQuestion = submittedQuestion.trim();

    if (activeSupport?.status === "in_progress") {
      event.preventDefault();
      if (!trimmedQuestion || !user || isSending) return;
      setIsSending(true);
      try {
        await sendSupportMessage(
          user,
          activeSupport.request_id,
          trimmedQuestion,
          crypto.randomUUID(),
        );
        setQuestion("");
        setSupportDetail(await getSupportRequest(
          user,
          activeSupport.request_id,
        ));
      } catch (reasonCaught) {
        setError(reasonCaught.message);
      } finally {
        setIsSending(false);
      }
      return;
    }

    if (requestsHumanSupport(trimmedQuestion)) {
      event.preventDefault();
      setQuestion("");
      setSupportSummary(trimmedQuestion);
      const unresolved = UNRESOLVED_PHRASES.some((phrase) => (
        trimmedQuestion.toLowerCase().includes(phrase)
      ));
      const reason = unresolved
        ? "passenger_issue_unresolved"
        : "explicit_request";
      setSupportReason(reason);
      await openSupportOffer(reason, trimmedQuestion);
      return;
    }

    // This closes the small timing window before React disables the send button.
    if (
      !trimmedQuestion ||
      !selectedModelId ||
      modelLoading ||
      isSending ||
      conversation?.status === "ended" ||
      pendingSubmission.current
    ) {
      return;
    }

    setError(null);
    setIsSending(true);
    pendingSubmission.current = true;

    try {
      const activeConversation = conversation || {
        conversation_id: crypto.randomUUID(),
        user_id: user?.uid || "guest",
        title: "Temporary conversation",
        language,
        status: "active",
        started_at: new Date().toISOString(),
        persisted: false,
      };

      if (!conversation) {
        setConversation(activeConversation);
      }

      let response;

      if (user) {
        response = activeConversation.persisted === false
          ? await startConversationWithMessage(
              user,
              trimmedQuestion,
              language,
              selectedModelId,
            )
          : await sendConversationMessage(
              user,
              activeConversation.conversation_id,
              trimmedQuestion,
              language,
              selectedModelId,
            );
      } else {
        const generated = await sendGuestMessage(
          trimmedQuestion,
          language,
          selectedModelId,
        );
        const now = new Date().toISOString();
        const customerId = crypto.randomUUID();
        const chatbotId = crypto.randomUUID();

        // Adapt the non-persistent RAG response to the same message component
        // used by saved conversations, keeping the guest UI fully featured.
        response = {
          conversation: activeConversation,
          customer_message: {
            message_id: customerId,
            conversation_id: activeConversation.conversation_id,
            sender_type: "customer",
            message_text: trimmedQuestion,
            language: generated.language,
            timestamp: now,
          },
          chatbot_message: {
            message_id: chatbotId,
            conversation_id: activeConversation.conversation_id,
            sender_type: "chatbot",
            message_text: generated.answer,
            language: generated.language,
            timestamp: now,
            model_id: generated.model?.model_id || selectedModelId,
            response_source: generated.generation_called
              ? "generated"
              : generated.decision === "clarification_required"
                ? "clarification"
                : "fallback",
            retrieved_chunks: generated.sources.map((source) => ({
              message_chunk_id: `${chatbotId}_${source.chunk.chunk_id}`,
              message_id: chatbotId,
              chunk_id: source.chunk.chunk_id,
              citation_id: source.citation_id,
              similarity_score: source.score,
              retrieval_rank: source.retrieval_rank,
              source_title: source.chunk.source
                .replaceAll("\\", "/")
                .split("/")
                .pop()
                ?.replace(/\.docx$/i, ""),
              source_section: source.chunk.section,
              source_url: source.chunk.source_url,
            })),
          },
        };
      }

      setMessages((currentMessages) => [
        ...currentMessages,
        response.customer_message,
        response.chatbot_message,
      ]);
      setConversation(response.conversation);
      if (["fallback", "clarification"].includes(
        response.chatbot_message.response_source,
      )) {
        const nextCount = unsuccessfulTurns + 1;
        setUnsuccessfulTurns(nextCount);
        if (
          response.chatbot_message.response_source === "fallback"
          || nextCount >= (supportAvailability?.clarification_attempts || 2)
        ) {
          setSupportSummary(trimmedQuestion);
          setSupportReason(
            nextCount >= (supportAvailability?.clarification_attempts || 2)
              ? "repeated_unsuccessful_assistance"
              : "low_retrieval_confidence",
          );
          setSupportOffer(true);
        }
      } else {
        setUnsuccessfulTurns(0);
      }
      if (user) {
        setHistory((current) => {
          const withoutCurrent = current.filter(
            (item) => item.conversation_id !== response.conversation.conversation_id,
          );
          return [response.conversation, ...withoutCurrent];
        });
      }
      setQuestion("");
    } catch (reason) {
      // Retain the text so the passenger can correct or retry the request.
      setError(
        reason instanceof Error
          ? reason.message
          : "We could not send your question. Please try again.",
      );
    } finally {
      pendingSubmission.current = false;
      setIsSending(false);
    }
  }

  function handleComposerKeyDown(event) {
    // Enter sends; Shift+Enter preserves the expected multiline textarea behavior.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();

      if (question.trim() && !isSending) {
        event.currentTarget.form.requestSubmit();
      }
    }
  }

  return (
    <section
      className={`chat-panel ${
        expanded ? "chat-panel-expanded" : ""
      } ${drawerOpen ? "drawer-open" : ""}`}
      id="chat"
      aria-labelledby="chat-heading"
    >
      {/* Default chat uses an overlay drawer; expanded mode gives history its own column. */}
      {drawerOpen && (
        <button
          className="drawer-scrim"
          type="button"
          aria-label="Close conversation history"
          onClick={() => setDrawerOpen(false)}
        />
      )}
      {drawerOpen && <aside className="chat-drawer open" aria-label="Conversation history">
        <div className="drawer-heading">
          <div>
            <KenyaAirwaysLogo className="drawer-airline-logo" variant="colour" />
            <h3>Conversations</h3>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={() => setDrawerOpen(false)}
            aria-label="Close menu"
          >
            <X aria-hidden="true" />
          </button>
        </div>
        <button
          className="button primary drawer-new"
          type="button"
          onClick={startConversation}
          disabled={modelLoading || !selectedModelId}
        >
          + New conversation
        </button>
        {!user && (
          <div className="guest-history-card">
            <strong>Save and revisit your conversations</strong>
            <p>Log in or create an account to access saved conversations and chat history.</p>
            <div className="guest-auth-actions">
              <button
                className="button secondary"
                type="button"
                onClick={() => onRequestAuth?.("sign-in")}
              >
                Log in
              </button>
              <button
                className="link-button"
                type="button"
                onClick={() => onRequestAuth?.("register")}
              >
                Sign up
              </button>
            </div>
          </div>
        )}
        <p className="drawer-section-label">Saved conversations</p>
        {historyLoading ? (
          <p className="muted">Loading history…</p>
        ) : !history.length ? (
          <p className="muted">No saved conversations yet.</p>
        ) : (
          <div className="history-list">
            {history.map((item) => (
              <button
                className={`history-item ${
                  item.conversation_id === conversation?.conversation_id
                    ? "selected"
                    : ""
                }`}
                type="button"
                key={item.conversation_id}
                onClick={() => openConversation(item.conversation_id)}
              >
                <strong>{item.title}</strong>
                <span>{item.status === "ended" ? "Ended" : "Active"}</span>
              </button>
            ))}
          </div>
        )}
        {user && supportRequests.length > 0 && (
          <>
            <p className="drawer-section-label">Human support</p>
            <div className="history-list support-history-list">
              {supportRequests.map((item) => (
                <button
                  className="history-item"
                  type="button"
                  key={item.request_id}
                  onClick={() => openSupportHistory(item)}
                >
                  <strong>{item.reference}</strong>
                  <span>{item.status.replaceAll("_", " ")}</span>
                </button>
              ))}
            </div>
          </>
        )}
        <p className="drawer-note">
          {user
            ? "Your conversation history is tied to your secure passenger account."
            : "Guest messages are temporary and are cleared when this page is refreshed."}
        </p>
      </aside>}
      <div className="chat-main-content">
        <div className="chat-panel-heading">
        <div className="chat-header-left">
          <button
            className="menu-button"
            type="button"
            onClick={() => setDrawerOpen((current) => !current)}
            aria-label={
              drawerOpen
                ? "Close conversation history"
                : "Open conversation history"
            }
          >
            <Menu aria-hidden="true" />
          </button>
          <div className="chat-brand">
            <KenyaAirwaysLogo className="chat-airline-logo" variant="colour" />
            <span className="chat-brand-divider" aria-hidden="true" />
            <p className="chat-brand-name">KQ Support</p>
          </div>
        </div>
        <div className="chat-header-right">
          <div className="chat-window-actions">
            <button
              className="icon-button"
              type="button"
              onClick={onMinimize}
              aria-label="Minimize chat"
            >
              <Minus aria-hidden="true" />
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={() => setExpanded((current) => !current)}
              aria-label={expanded ? "Restore chat size" : "Expand chat"}
            >
              {expanded
                ? <Minimize2 aria-hidden="true" />
                : <Maximize2 aria-hidden="true" />}
            </button>
            <button className="icon-button" type="button" onClick={onClose} aria-label="Close chat">
              <X aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      <div className="chat-controls">
        <div className="control-field">
          <label htmlFor="language-preference">Response language</label>
          <select
            id="language-preference"
            value={language}
            onChange={(event) => setLanguage(event.target.value)}
            disabled={isStarting || isSending}
          >
            {LANGUAGE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <button
          className="button secondary"
          type="button"
          onClick={startConversation}
          disabled={isStarting || isSending || modelLoading || !selectedModelId}
        >
          {isStarting ? "Starting…" : "New conversation"}
        </button>
        {conversation?.status === "active" && messages.length > 0 && (
          <button
            className="end-conversation-button"
            type="button"
            onClick={() => setConfirmEndOpen(true)}
            disabled={isSending || isEnding}
          >
            End conversation
          </button>
        )}
        {!activeSupport || ["resolved", "cancelled"].includes(
          activeSupport.status,
        ) ? (
          <button
            className="request-support-action"
            type="button"
            onClick={() => openSupportOffer("passenger_request")}
          >
            Request human support
          </button>
        ) : null}
      </div>

      {confirmEndOpen && (
        <div
          className="chat-confirm-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !isEnding) {
              setConfirmEndOpen(false);
            }
          }}
        >
          <section
            aria-labelledby="end-conversation-title"
            aria-modal="true"
            className="chat-confirm-dialog"
            role="dialog"
          >
            <h3 id="end-conversation-title">End conversation?</h3>
            <p>
              You can still view this conversation in your saved history,
              but you will not be able to send more messages to it.
            </p>
            <div className="chat-confirm-actions">
              <button
                className="button secondary"
                type="button"
                disabled={isEnding}
                onClick={() => setConfirmEndOpen(false)}
              >
                Cancel
              </button>
              <button
                className="button primary"
                type="button"
                disabled={isEnding}
                onClick={finishConversation}
              >
                {isEnding ? "Ending…" : "End conversation"}
              </button>
            </div>
          </section>
        </div>
      )}

      {error && (
        <div className="notice error chat-error" role="alert">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => {
              setError(null);
              document.getElementById("chat-question")?.focus();
            }}
          >
            Try again
          </button>
        </div>
      )}

      {activeSupport && (
        <section className={`support-status-banner ${activeSupport.status}`}>
          <div>
            <strong>
              {activeSupport.status === "pending"
                ? activeSupport.request_type === "live"
                  ? "Waiting for agent"
                  : "Ticket pending"
                : activeSupport.status === "in_progress"
                  ? "Connected to a support agent"
                  : activeSupport.status === "resolved"
                    ? "Support case resolved"
                    : "Support request cancelled"}
            </strong>
            <span>
              {activeSupport.assigned_agent_name
                ? `Agent: ${activeSupport.assigned_agent_name}`
                : activeSupport.reference}
            </span>
          </div>
          {activeSupport.status === "pending" && (
            <div className="support-banner-actions">
              {activeSupport.request_type === "live"
                && supportAvailability
                && Date.now() - new Date(activeSupport.created_at).getTime()
                  >= supportAvailability.wait_seconds * 1000 && (
                <button type="button" onClick={convertActiveSupport}>
                  Convert to ticket
                </button>
              )}
              <button type="button" onClick={cancelActiveSupport}>
                Cancel request
              </button>
            </div>
          )}
        </section>
      )}

      <div
        className="message-list"
        ref={messageListRef}
        aria-live="polite"
        aria-relevant="additions"
      >
        {modelLoading && !error && (
          <div className="welcome-screen">
            <h3>Loading...</h3>
            <p className="empty-chat">Please wait while we connect to the support system.</p>
          </div>
        )}
        {!messages.length && !isSending && (
          <div className="welcome-screen">
            <KenyaAirwaysLogo className="welcome-airline-logo" variant="colour" />
            <h3>What can we help you with?</h3>
            <p className="empty-chat">Ask about your Kenya Airways journey or choose a common topic.</p>
            <div className="suggested-questions">
              {SUGGESTED_QUESTIONS.map((suggestion) => (
                <button
                  className="suggestion-chip"
                  type="button"
                  key={suggestion}
                  onClick={(event) => submitQuestion(event, suggestion)}
                  title={suggestion}
                  disabled={modelLoading || isSending || !selectedModelId}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        {conversationTurns(messages).map((turn) => (
          <div
            className="message-turn"
            key={turn.question?.message_id || turn.answer?.message_id}
          >
            {turn.question && <ChatMessage message={turn.question} />}
            {turn.answer && <ChatMessage message={turn.answer} />}
          </div>
        ))}
        {supportDetail?.messages.map((message) => (
          message.sender_role === "agent" ? (
            <AgentMessage message={message} key={message.message_id} />
          ) : (
            <ChatMessage
              key={message.message_id}
              message={{
                ...message,
                sender_type: "customer",
              }}
            />
          )
        ))}
        {user && activeSupport?.status === "resolved" && (
          <SupportFeedbackRating
            user={user}
            request={activeSupport}
            existing={supportDetail?.feedback}
            onSaved={(saved) => setSupportDetail((current) => ({
              ...current,
              feedback: saved,
            }))}
          />
        )}
        {supportOffer && (
          <section className="human-support-card">
            <h3>Need additional assistance?</h3>
            <p>
              I couldn&apos;t fully resolve your question using the available
              Kenya Airways information. Would you like assistance from a
              support agent?
            </p>
            <label htmlFor="support-issue-summary">Issue summary</label>
            <textarea
              id="support-issue-summary"
              value={supportSummary}
              onChange={(event) => setSupportSummary(event.target.value)}
              maxLength="1000"
              rows="3"
            />
            {!user && (
              <div className="support-auth-required">
                <p>
                  Sign in to track this support request and receive replies.
                </p>
                <div>
                  <button
                    className="button primary"
                    type="button"
                    onClick={() => {
                      setPendingEscalation({
                        issue_summary: supportSummary,
                        reason: supportReason,
                      });
                      onRequestAuth?.("sign-in");
                    }}
                  >
                    Log in
                  </button>
                  <button
                    className="button secondary"
                    type="button"
                    onClick={() => {
                      setPendingEscalation({
                        issue_summary: supportSummary,
                        reason: supportReason,
                      });
                      onRequestAuth?.("register");
                    }}
                  >
                    Sign up
                  </button>
                </div>
              </div>
            )}
            {user && supportAvailability && (
              <p className="support-availability-copy">
                {supportAvailability.available
                  ? "An agent is currently available for live support."
                  : "No agent is online. This will be saved as a support ticket."}
              </p>
            )}
            <div className="human-support-card-actions">
              {user && (
                <button
                  className="button primary"
                  type="button"
                  disabled={
                    supportState === "submitting"
                    || !supportSummary.trim()
                  }
                  onClick={submitSupportRequest}
                >
                  {supportState === "submitting"
                    ? "Submitting…"
                    : "Request human support"}
                </button>
              )}
              <button
                className="button secondary"
                type="button"
                onClick={() => setSupportOffer(false)}
              >
                Continue with AI
              </button>
            </div>
          </section>
        )}
        {isSending && (
          <div className="chat-message assistant loading-message">
            <p className="message-sender">KQ Support</p>
            <div className="message-bubble">
              <span
                className="typing-indicator"
                aria-label="KQ Support is preparing a response"
              >
                <i />
                <i />
                <i />
              </span>
            </div>
          </div>
        )}
        {user && conversation?.status === "ended" && (
          <FeedbackRating
            user={user}
            conversation={conversation}
            existingFeedback={feedback}
            onSaved={setFeedback}
          />
        )}
      </div>

      {/* Keep the composer separate from the scrolling transcript. */}
      <form className="chat-composer" ref={composerFormRef} onSubmit={submitQuestion}>
        <label className="sr-only" htmlFor="chat-question">Your question</label>
        <textarea
          id="chat-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={handleComposerKeyDown}
          placeholder={
            activeSupport?.status === "in_progress"
              ? "Message your support agent..."
              : "Enter your inquiry..."
          }
          rows="3"
          maxLength="4000"
          disabled={
            isSending
            || isStarting
            || conversation?.status === "ended"
            || (
              activeSupport?.status !== "in_progress"
              && (modelLoading || !selectedModelId)
            )
          }
          required
        />
        <div className="composer-actions">
          <div className="composer-meta">
            <div className="model-selector" ref={modelDropdownRef}>
              <button
                className="model-selector-button"
                type="button"
                onClick={() => setModelDropdownOpen((current) => !current)}
                disabled={modelLoading || isSending || !models.length}
                aria-label="Select AI model"
              >
                <span className="model-icon" aria-hidden="true">AI</span>
                <span className="model-label">
                  {modelLabel(models.find((model) => (
                    model.model_id === selectedModelId
                  )))}
                </span>
                <span className="model-dropdown-arrow">{modelDropdownOpen ? "▲" : "▼"}</span>
              </button>
              {modelDropdownOpen && (
                <div className="model-dropdown">
                  {models.map((model) => (
                    <button
                      key={model.model_id}
                      className={`model-option ${model.model_id === selectedModelId ? "selected" : ""}`}
                      type="button"
                      onClick={() => {
                        setSelectedModelId(model.model_id);
                        setModelDropdownOpen(false);
                      }}
                    >
                      {modelLabel(model)}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <p>{question.length}/4000 · Enter to send, Shift+Enter for a new line</p>
          </div>
          <button
            className="button primary"
            type="submit"
            disabled={
              !question.trim()
              || isSending
              || isStarting
              || (
                activeSupport?.status !== "in_progress"
                && (modelLoading || !selectedModelId)
              )
              || conversation?.status === "ended"
            }
          >
            {isSending ? "Sending…" : "Send"}
          </button>
        </div>
        <p className="chat-disclaimer">AI can make mistakes. Please verify important travel information.</p>
      </form>
      </div>
    </section>
  );
}
