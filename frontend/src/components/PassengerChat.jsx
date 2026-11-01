import { useEffect, useRef, useState } from "react";

import {
  createConversation,
  endConversation,
  getConversation,
  getConversationFeedback,
  getAvailableModels,
  listConversations,
  saveConversationFeedback,
  sendConversationMessage,
} from "../api";
import { FormattedAnswer } from "./FormattedAnswer";

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
  return `${(Number(score) * 100).toFixed(1)}% similar`;
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
  if (!chunks?.length) {
    return null;
  }

  const orderedChunks = [...chunks].sort(
    (left, right) => left.retrieval_rank - right.retrieval_rank,
  );

  return (
    <details className="message-sources">
      <summary>Sources ({orderedChunks.length})</summary>
      <div className="sources-list">
        {orderedChunks.map((chunk) => (
          <div key={chunk.message_chunk_id} className="source-item">
            <div className="source-header">
              <strong>{chunk.source_title || `Source ${chunk.retrieval_rank}`}</strong>
              {chunk.source_section && <span className="source-section">{chunk.source_section}</span>}
            </div>
            <p className="source-description">
              {chunk.citation_id}
              {chunk.similarity_score && <span className="source-similarity"> · {similarityPercent(chunk.similarity_score)}</span>}
            </p>
            {chunk.source_url && (
              <a
                href={chunk.source_url}
                target="_blank"
                rel="noreferrer noopener"
                className="source-link"
              >
                View on Kenya Airways website →
              </a>
            )}
          </div>
        ))}
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

  useEffect(() => {
    setRating(existingFeedback?.rating || 0);
    setComment(existingFeedback?.comment || "");
  }, [existingFeedback]);

  async function submit(event) {
    event.preventDefault();

    if (!rating || saving) {
      return;
    }

    setSaving(true);
    setError(null);

    try {
      const saved = await saveConversationFeedback(
        user,
        conversation.conversation_id,
        rating,
        comment,
      );
      onSaved(saved);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Feedback could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="feedback-card" aria-labelledby="feedback-heading">
      <p className="eyebrow" id="feedback-heading">CONVERSATION FEEDBACK</p>
      <p>How helpful was this conversation?</p>
      <form onSubmit={submit}>
        <div className="star-row" role="radiogroup" aria-label="Rating from one to five stars">
          {[1, 2, 3, 4, 5].map((value) => (
            <button
              className={value <= rating ? "star selected" : "star"}
              key={value}
              type="button"
              role="radio"
              aria-checked={value === rating}
              aria-label={`${value} star${value === 1 ? "" : "s"}`}
              onClick={() => setRating(value)}
            >
              ★
            </button>
          ))}
        </div>
        <textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          placeholder="Tell us how we can improve (optional)"
          maxLength="1000"
          rows="2"
        />
        {error && <p className="notice error" role="alert">{error}</p>}
        <button className="button secondary" type="submit" disabled={!rating || saving}>
          {saving ? "Saving…" : existingFeedback ? "Update rating" : "Save rating"}
        </button>
      </form>
    </section>
  );
}

export function PassengerChat({ user, onClose, onDrawerToggle }) {
  const [language, setLanguage] = useState("en");
  const [conversation, setConversation] = useState(null);
  const [messages, setMessages] = useState([]);
  const [history, setHistory] = useState([]);
  const [feedback, setFeedback] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
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

  useEffect(() => {
    if (onDrawerToggle) {
      onDrawerToggle(drawerOpen);
    }
  }, [drawerOpen, onDrawerToggle]);

  useEffect(() => {
    let ignoreResponse = false;

    async function loadHistory() {
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
        const availableModels = await getAvailableModels(user);
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
      const nextConversation = await createConversation(user, language);
      setConversation(nextConversation);
      setMessages([]);
      setFeedback(null);
      setQuestion("");
      setDrawerOpen(false);
      setHistory((current) => [nextConversation, ...current]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "We could not start a conversation. Please try again.");
    } finally {
      setIsStarting(false);
    }
  }

  async function openConversation(conversationId) {
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
      const ended = await endConversation(user, conversation.conversation_id);
      setConversation(ended);
      setHistory((current) => current.map((item) => (
        item.conversation_id === ended.conversation_id ? ended : item
      )));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The conversation could not be ended.");
    }
  }

  async function submitQuestion(event) {
    event.preventDefault();

    const trimmedQuestion = question.trim();

    // This closes the small timing window before React disables the send button.
    if (!trimmedQuestion || isSending || pendingSubmission.current) {
      return;
    }

    setError(null);
    setIsSending(true);
    pendingSubmission.current = true;

    try {
      const activeConversation = conversation || (await createConversation(user, language));

      if (!conversation) {
        setConversation(activeConversation);
      }

      const response = await sendConversationMessage(
        user,
        activeConversation.conversation_id,
        trimmedQuestion,
        language,
        selectedModelId,
      );

      setMessages((currentMessages) => [
        ...currentMessages,
        response.customer_message,
        response.chatbot_message,
      ]);
      setConversation(response.conversation);
      setHistory((current) => {
        const withoutCurrent = current.filter(
          (item) => item.conversation_id !== response.conversation.conversation_id,
        );
        return [response.conversation, ...withoutCurrent];
      });
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
      {drawerOpen && <aside className="chat-drawer open" aria-label="Conversation history">
        <div className="drawer-heading">
          <div>
            <p className="eyebrow">KENYA AIRWAYS</p>
            <h3>Conversations</h3>
          </div>
          <button className="icon-button" type="button" onClick={() => setDrawerOpen(false)} aria-label="Close menu">×</button>
        </div>
        <button className="button primary drawer-new" type="button" onClick={startConversation} disabled={modelLoading || !selectedModelId}>+ New conversation</button>
        {historyLoading ? <p className="muted">Loading history…</p> : !history.length ? <p className="muted">No saved conversations yet.</p> : (
          <div className="history-list">
            {history.map((item) => (
              <button className="history-item" type="button" key={item.conversation_id} onClick={() => openConversation(item.conversation_id)}>
                <strong>{item.title}</strong>
                <span>{item.status === "ended" ? "Ended" : "Active"}</span>
              </button>
            ))}
          </div>
        )}
        <p className="drawer-note">Your conversation history is tied to your secure passenger account.</p>
      </aside>}
      <div className="chat-main-content">
        <div className="chat-panel-heading">
        <div className="chat-header-left">
          <button className="menu-button" type="button" onClick={() => setDrawerOpen((current) => !current)} aria-label={drawerOpen ? "Close conversation history" : "Open conversation history"}>☰</button>
          <div className="chat-brand">
            <span className="chat-brand-logo">KQ</span>
            <p className="chat-brand-name">KENYA AIRWAYS</p>
          </div>
        </div>
        <div className="chat-header-right">
          <div className="chat-window-actions">
            <button className="icon-button" type="button" onClick={() => setExpanded((current) => !current)} aria-label={expanded ? "Restore chat size" : "Expand chat"}>
              {expanded ? "−" : "□"}
            </button>
            <button className="icon-button" type="button" onClick={onClose} aria-label="Close chat">×</button>
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
          {isStarting ? "Starting…" : "Start new conversation"}
        </button>
        {conversation?.status === "active" && messages.length > 0 && (
          <button className="button text-button" type="button" onClick={finishConversation} disabled={isSending}>
            End conversation
          </button>
        )}
      </div>

      {error && <p className="notice error chat-error" role="alert">{error}</p>}

      <div className="message-list" ref={messageListRef} aria-live="polite" aria-relevant="additions">
        {modelLoading && !error && (
          <div className="welcome-screen">
            <p className="welcome-mark">KQ</p>
            <h3>Loading...</h3>
            <p className="empty-chat">Please wait while we connect to the support system.</p>
          </div>
        )}
        {!messages.length && !isSending && (
          <div className="welcome-screen">
            <p className="welcome-mark">KQ</p>
            <h3>Hello! How can we help you today?</h3>
            <p className="empty-chat">Choose a common question or enter your inquiry below.</p>
            <div className="suggested-questions">
              {SUGGESTED_QUESTIONS.map((suggestion) => (
                <button
                  className="suggestion-chip"
                  type="button"
                  key={suggestion}
                  onClick={() => setQuestion(suggestion)}
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
              <p>Finding an answer from KQ support information…</p>
            </div>
          </div>
        )}
      </div>

      <form className="chat-composer" onSubmit={submitQuestion}>
        <label htmlFor="chat-question">Your question</label>
        <textarea
          id="chat-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={handleComposerKeyDown}
          placeholder="Enter your inquiry..."
          rows="4"
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
                <span className="model-icon">🤖</span>
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
      </form>
      {conversation?.status === "ended" && (
        <FeedbackRating
          user={user}
          conversation={conversation}
          existingFeedback={feedback}
          onSaved={setFeedback}
        />
      )}
      </div>
    </section>
  );
}
