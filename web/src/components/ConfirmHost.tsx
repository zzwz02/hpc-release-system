/**
 * ConfirmHost — renders the active confirmDialog()/promptDialog() request
 * (src/lib/confirm.ts) using the app's existing dialog classes.
 * Esc / backdrop click = cancel. Confirm variant autofocuses the confirm
 * button (Enter confirms); prompt variant autofocuses the input.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  getActiveConfirm,
  settleConfirm,
  subscribeConfirms,
} from "../lib/confirm";

export function ConfirmHost() {
  const req = useSyncExternalStore(subscribeConfirms, getActiveConfirm, getActiveConfirm);
  const okRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [text, setText] = useState("");

  useEffect(() => {
    if (!req) return;
    setText("");
    if (req.kind === "prompt") {
      inputRef.current?.focus();
    } else {
      okRef.current?.focus();
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && req) {
        e.stopPropagation();
        settleConfirm(req.id, req.kind === "confirm" ? false : null);
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [req]);

  if (!req) return null;

  const isPrompt = req.kind === "prompt";
  const confirmDisabled = isPrompt && (req.required ?? true) && !text.trim();

  function cancel() {
    if (req) settleConfirm(req.id, isPrompt ? null : false);
  }

  function ok() {
    if (!req || confirmDisabled) return;
    settleConfirm(req.id, isPrompt ? text : true);
  }

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) cancel();
      }}
    >
      <div
        className="dialog-card confirm-card"
        role="alertdialog"
        aria-modal="true"
        data-testid="confirm-dialog"
      >
        <h3>{req.title ?? (isPrompt ? "请输入" : "请确认")}</h3>
        <div className="dialog-body">
          {req.body && <p className="confirm-body">{req.body}</p>}
          {isPrompt && (
            <input
              ref={inputRef}
              className="input"
              placeholder={req.placeholder ?? ""}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") ok();
              }}
              data-testid="confirm-input"
            />
          )}
        </div>
        <div className="dialog-actions">
          <button className="btn" data-testid="confirm-cancel" onClick={cancel}>
            {req.cancelText ?? "取消"}
          </button>
          <button
            ref={okRef}
            className={req.danger ? "btn danger" : "btn primary"}
            data-testid="confirm-ok"
            disabled={confirmDisabled}
            onClick={ok}
          >
            {req.confirmText ?? "确认"}
          </button>
        </div>
      </div>
    </div>
  );
}
