import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

// Action feedback is intentionally temporary. Confirmation dialogs and navbar
// notifications use separate components and are never dismissed by this timer.
export function AdminNotice({
  notice,
  type,
  message,
  onDismiss,
}) {
  const resolvedType = notice?.type || type || "info";
  const resolvedMessage = notice?.text || message;
  const [visible, setVisible] = useState(Boolean(resolvedMessage));
  const dismissCallback = useRef(onDismiss);
  dismissCallback.current = onDismiss;

  useEffect(() => {
    if (!resolvedMessage) {
      setVisible(false);
      return undefined;
    }

    setVisible(true);
    const delay = resolvedType === "error" ? 6000 : 4000;
    const timer = window.setTimeout(() => {
      setVisible(false);
      dismissCallback.current?.();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [resolvedMessage, resolvedType]);

  if (!visible || !resolvedMessage) return null;

  function dismiss() {
    setVisible(false);
    dismissCallback.current?.();
  }

  return (
    <div
      className={`notice admin-notice ${resolvedType}`}
      role={resolvedType === "error" ? "alert" : "status"}
    >
      <span>{resolvedMessage}</span>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss message"
      >
        <X aria-hidden="true" />
      </button>
    </div>
  );
}
