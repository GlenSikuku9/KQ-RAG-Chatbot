import { useEffect, useRef, useState } from "react";
import {
  Menu,
  Maximize2,
  Minimize2,
  Minus,
  X,
} from "lucide-react";
import "./PassengerChat.css";

import {
  createConversation,
  endConversation,
  getConversation,
  getConversationFeedback,
  getAvailableModels,
  getPublicModels,
  listConversations,
  saveConversationFeedback,
  sendConversationMessage,
  sendGuestMessage,
} from "../api";
import { FormattedAnswer } from "./FormattedAnswer";
import { KenyaAirwaysLogo } from "./KenyaAirwaysLogo";

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
  google_gemini_flash: "Gemini 3.8 Flash",
};

function modelLabel(model) {
  if (!model) {
    return "Select model";
  }
  return MODEL_LABELS[model.model_id] || model.upstream_model || model.model_id;
}

function answerModelLabel(modelId) {
  return MODEL_LABELS[modelId] || modelId;
}

function similarityPercent(score) {
  return `${(Number(score) * 100).toFixed(1)}% match`;
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
          const hasScore = chunk.similarity_score !== null &&
            chunk.similarity_score !== undefined;

          return (
            <div className="source-item" key={chunk.message_chunk_id || `${reference}-${index}`}>
              <div className="source-row-main">
                <span className="source-reference">{reference}</span>
                <strong>{title}</strong>
                {hasScore && (
                  <span
                    className="source-similarity"
                    title="Retrieval similarity score; this is not answer accuracy or confidence"
                  >
                    {similarityPercent(chunk.similarity_score)}
                  </span>
                )}
              </div>
              {chunk.source_url && (
                <a className="source-link" href={chunk.source_url} target="_blank" rel="noreferrer noopener">
                  View verified Kenya Airways source →
                </a>
              )}
            </div>
          );
        })}
      </div>
      <p className="source-score-note">Percentages are retrieval similarity scores, not answer accuracy.</p>
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
      {!isPassenger && message.model_id && (
        <p className="message-model">
          Answered by {answerModelLabel(message.model_id)}
        </p>
      )}
      {!isPassenger && <Sources chunks={message.retrieved_chunks} />}
    </article>
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
        {!commentVisible && <button className="feedback-comment-toggle" type="button" onClick={() => setCommentVisible(true)}>Add an optional comment</button>}
        {commentVisible && <textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Tell us how we can improve (optional)" maxLength="1000" rows="2" />}
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

  async function startConversation() {
    if (isStarting || isSending || modelLoading || !selectedModelId) {
      return;
    }

    setError(null);
    setIsStarting(true);

    try {
      // A new chat clears only this screen; persisted history is delivered in I19.
      // Guest conversation metadata exists only in component memory. Signed-in
      // passengers continue through the protected persistence endpoint.
      const nextConversation = user
        ? await createConversation(user, language)
        : {
            conversation_id: crypto.randomUUID(),
            user_id: "guest",
            title: "Temporary conversation",
            language,
            status: "active",
            started_at: new Date().toISOString(),
          };
      setConversation(nextConversation);
      setMessages([]);
      setFeedback(null);
      setQuestion("");
      setDrawerOpen(false);
      if (user) {
        setHistory((current) => [nextConversation, ...current]);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "We could not start a conversation. Please try again.");
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

  async function finishConversation() {
    if (!conversation || conversation.status !== "active" || isSending) {
      return;
    }

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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The conversation could not be ended.");
    }
  }

  async function submitQuestion(event, submittedQuestion = question) {
    event.preventDefault();

    const trimmedQuestion = submittedQuestion.trim();

    // This closes the small timing window before React disables the send button.
    if (
      !trimmedQuestion ||
      !selectedModelId ||
      modelLoading ||
      isSending ||
      pendingSubmission.current
    ) {
      return;
    }

    setError(null);
    setIsSending(true);
    pendingSubmission.current = true;

    try {
      const activeConversation = conversation || (
        user
          ? await createConversation(user, language)
          : {
              conversation_id: crypto.randomUUID(),
              user_id: "guest",
              title: "Temporary conversation",
              language,
              status: "active",
              started_at: new Date().toISOString(),
            }
      );

      if (!conversation) {
        setConversation(activeConversation);
      }

      let response;

      if (user) {
        response = await sendConversationMessage(
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
      setError(reason instanceof Error ? reason.message : "We could not send your question. Please try again.");
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
      className={`chat-panel ${expanded ? "chat-panel-expanded" : ""} ${drawerOpen ? "drawer-open" : ""}`}
      id="chat"
      aria-labelledby="chat-heading"
    >
      {/* Default chat uses an overlay drawer; expanded mode gives history its own column. */}
      {drawerOpen && <button className="drawer-scrim" type="button" aria-label="Close conversation history" onClick={() => setDrawerOpen(false)} />}
      {drawerOpen && <aside className="chat-drawer open" aria-label="Conversation history">
        <div className="drawer-heading">
          <div>
            <KenyaAirwaysLogo className="drawer-airline-logo" variant="colour" />
            <h3>Conversations</h3>
          </div>
          <button className="icon-button" type="button" onClick={() => setDrawerOpen(false)} aria-label="Close menu">
            <X aria-hidden="true" />
          </button>
        </div>
        <button className="button primary drawer-new" type="button" onClick={startConversation} disabled={modelLoading || !selectedModelId}>+ New conversation</button>
        {!user && (
          <div className="guest-history-card">
            <strong>Save and revisit your conversations</strong>
            <p>Log in or create an account to access saved conversations and chat history.</p>
            <div className="guest-auth-actions">
              <button className="button secondary" type="button" onClick={() => onRequestAuth?.("sign-in")}>Log in</button>
              <button className="link-button" type="button" onClick={() => onRequestAuth?.("register")}>Sign up</button>
            </div>
          </div>
        )}
        <p className="drawer-section-label">Saved conversations</p>
        {historyLoading ? <p className="muted">Loading history…</p> : !history.length ? <p className="muted">No saved conversations yet.</p> : (
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
        <p className="drawer-note">
          {user
            ? "Your conversation history is tied to your secure passenger account."
            : "Guest messages are temporary and are cleared when this page is refreshed."}
        </p>
      </aside>}
      <div className="chat-main-content">
        <div className="chat-panel-heading">
        <div className="chat-header-left">
          <button className="menu-button" type="button" onClick={() => setDrawerOpen((current) => !current)} aria-label={drawerOpen ? "Close conversation history" : "Open conversation history"}>
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
            <button className="icon-button" type="button" onClick={() => setExpanded((current) => !current)} aria-label={expanded ? "Restore chat size" : "Expand chat"}>
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
          <button className="button text-button" type="button" onClick={finishConversation} disabled={isSending}>
            End conversation
          </button>
        )}
      </div>

      {error && <div className="notice error chat-error" role="alert"><span>{error}</span><button type="button" onClick={() => { setError(null); document.getElementById("chat-question")?.focus(); }}>Try again</button></div>}

      <div className="message-list" ref={messageListRef} aria-live="polite" aria-relevant="additions">
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
        {isSending && (
          <div className="chat-message assistant loading-message">
            <p className="message-sender">KQ Support</p>
            <div className="message-bubble">
              <span className="typing-indicator" aria-label="KQ Support is preparing a response"><i /><i /><i /></span>
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
          placeholder="Enter your inquiry..."
          rows="3"
          maxLength="4000"
          disabled={isSending || isStarting || modelLoading || !selectedModelId || conversation?.status === "ended"}
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
                <span className="model-label">{modelLabel(models.find((m) => m.model_id === selectedModelId))}</span>
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
            disabled={!question.trim() || isSending || isStarting || modelLoading || !selectedModelId || conversation?.status === "ended"}
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
