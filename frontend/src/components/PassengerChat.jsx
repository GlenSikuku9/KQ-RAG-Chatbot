import { useEffect, useRef, useState } from "react";

import {
  createConversation,
  getAvailableModels,
  sendConversationMessage,
} from "../api";
import { FormattedAnswer } from "./FormattedAnswer";

const LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "sw", label: "Kiswahili" },
  { value: "mixed", label: "English and Kiswahili" },
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
  return MODEL_LABELS[model.model_id] || model.upstream_model;
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
      <summary>Sources used ({orderedChunks.length})</summary>
      <ol>
        {orderedChunks.map((chunk) => (
          <li key={chunk.message_chunk_id}>
            <strong>{chunk.citation_id}</strong>
            <span>
              {chunk.source_title || `Match ${chunk.retrieval_rank}`}
              {chunk.source_section ? ` · ${chunk.source_section}` : ""}
            </span>
            <span>{similarityPercent(chunk.similarity_score)}</span>
          </li>
        ))}
      </ol>
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

export function PassengerChat({ user }) {
  const [language, setLanguage] = useState("en");
  const [conversation, setConversation] = useState(null);
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [models, setModels] = useState([]);
  const [selectedModelId, setSelectedModelId] = useState("");
  const [modelLoading, setModelLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isStarting, setIsStarting] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const pendingSubmission = useRef(false);
  const messageListRef = useRef(null);

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
      setQuestion("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "We could not start a conversation. Please try again.");
    } finally {
      setIsStarting(false);
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
    <section className="chat-panel" id="chat" aria-labelledby="chat-heading">
      <div className="chat-panel-heading">
        <div>
          <p className="eyebrow">PASSENGER CHAT</p>
          <h2 id="chat-heading">Talk to KQ Support</h2>
          <p>Ask about Kenya Airways policies. Answers are grounded in available support information.</p>
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
          <div className="control-field">
            <label htmlFor="model-preference">AI model</label>
            <select
              id="model-preference"
              value={selectedModelId}
              onChange={(event) => setSelectedModelId(event.target.value)}
              disabled={modelLoading || isStarting || isSending || !models.length}
            >
              {modelLoading && <option>Loading models…</option>}
              {!modelLoading && !models.length && (
                <option>No configured models available</option>
              )}
              {models.map((model) => (
                <option key={model.model_id} value={model.model_id}>
                  {modelLabel(model)} — {model.provider === "openrouter" ? "OpenRouter" : "Google AI"}
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
        </div>
      </div>

      <div className="conversation-status" aria-live="polite">
        {conversation
          ? `Conversation active: ${conversation.title}`
          : "Choose a language, then start a new conversation or send your first question."}
      </div>

      {error && <p className="notice error chat-error" role="alert">{error}</p>}

      <div className="message-list" ref={messageListRef} aria-live="polite" aria-relevant="additions">
        {!messages.length && !isSending && (
          <p className="empty-chat">Your messages and KQ Support answers will appear here.</p>
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
          placeholder="For example: What is the baggage allowance for my flight?"
          rows="4"
          maxLength="4000"
          disabled={isSending || isStarting || modelLoading || !selectedModelId}
          required
        />
        <div className="composer-actions">
          <p>Press Enter to send. Use Shift+Enter for a new line.</p>
          <button
            className="button primary"
            type="submit"
            disabled={!question.trim() || isSending || isStarting || modelLoading || !selectedModelId}
          >
            {isSending ? "Sending…" : "Send question"}
          </button>
        </div>
      </form>
    </section>
  );
}
