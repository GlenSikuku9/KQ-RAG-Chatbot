import { Bot, CircleCheck, FileText, RefreshCcw, UserPlus } from "lucide-react";

function formatDate(value) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(value));
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatChartLabel(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return `${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`;
}

function formatChartTooltip(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

function readableEventName(value) {
  return String(value || "System event")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function severityLabel(value) {
  return (
    {
      info: "Information",
      warning: "Warning",
      error: "Error",
    }[value] || readableEventName(value)
  );
}

function metricValue(value) {
  return value == null ? "Unavailable" : `${(value * 100).toFixed(1)}%`;
}

function modelDisplayName(modelId) {
  return (
    {
      openrouter_gemma_26b: "Gemma 4 26B A4B",
      openrouter_gemma_31b: "Gemma 4 31B",
      groq_gpt_oss_120b: "GPT-OSS 120B",
    }[modelId] || modelId
  );
}

function labelLanguages(values) {
  return Object.fromEntries(
    Object.entries(values || {}).map(([key, value]) => [
      languageLabel(key),
      value,
    ]),
  );
}

function languageLabel(value) {
  return (
    {
      en: "English",
      sw: "Kiswahili",
      mixed: "Mixed English/Kiswahili",
      unknown: "Unknown",
    }[value] || value
  );
}

function readableMetricName(value) {
  const labels = {
    generated: "Generated answer",
    fallback: "Fallback response",
    clarification: "Clarification requested",
    clarification_required: "Clarification requested",
    provider_error: "AI provider error",
    retrieval_error: "Retrieval error",
    timeout: "Request timeout",
  };

  return labels[value] || readableEventName(value);
}

function activityPresentation(action) {
  const value = String(action || "").toLowerCase();

  if (value.includes("user")) return { icon: UserPlus, tone: "user" };
  if (value.includes("document") && value.includes("process"))
    return { icon: RefreshCcw, tone: "document" };
  if (value.includes("document")) return { icon: FileText, tone: "document" };
  if (value.includes("model") || value.includes("generation"))
    return { icon: Bot, tone: "model" };
  return { icon: CircleCheck, tone: "success" };
}

export {
  formatDate,
  formatDateTime,
  formatChartLabel,
  formatChartTooltip,
  readableEventName,
  severityLabel,
  metricValue,
  modelDisplayName,
  labelLanguages,
  languageLabel,
  readableMetricName,
  activityPresentation,
};
