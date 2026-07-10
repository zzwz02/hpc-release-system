/**
 * Toaster — fixed top-right stack rendering the module-level toast store
 * (src/lib/toast.ts). Click a toast to dismiss it early.
 */
import { useSyncExternalStore } from "react";
import { dismissToast, getToasts, subscribeToasts } from "../lib/toast";

const KIND_ICON: Record<string, string> = { ok: "✓", bad: "✕", info: "ℹ" };

export function Toaster() {
  const toasts = useSyncExternalStore(subscribeToasts, getToasts, getToasts);
  if (toasts.length === 0) return null;
  return (
    <div className="toaster" aria-live="polite">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`toast ${t.kind}`}
          data-testid="toast"
          role="status"
          onClick={() => dismissToast(t.id)}
        >
          <span className="toast-ico" aria-hidden="true">{KIND_ICON[t.kind]}</span>
          <span className="toast-msg">{t.message}</span>
        </div>
      ))}
    </div>
  );
}
