/**
 * confirmDialog — promise-based replacement for window.confirm(), callable
 * from any async event handler without React context.
 *
 *   if (!(await confirmDialog({ body: "确认删除？", danger: true }))) return;
 *
 * Rendered by <ConfirmHost /> (mounted once in App). Only one request is
 * shown at a time; concurrent requests queue in FIFO order.
 */

export interface ConfirmOptions {
  title?: string;
  body: string;
  confirmText?: string;
  cancelText?: string;
  /** Destructive action: confirm button renders as .btn.danger */
  danger?: boolean;
}

export interface PromptOptions {
  title?: string;
  body?: string;
  placeholder?: string;
  /** Disable confirm until the input is non-blank (default true) */
  required?: boolean;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

export interface ConfirmRequest extends ConfirmOptions {
  id: number;
  kind: "confirm" | "prompt";
  placeholder?: string;
  required?: boolean;
  resolve: (value: boolean | string | null) => void;
}

let nextId = 1;
let queue: ConfirmRequest[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const fn of listeners) fn();
}

export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    queue = [
      ...queue,
      {
        ...options,
        id: nextId++,
        kind: "confirm",
        resolve: resolve as (value: boolean | string | null) => void,
      },
    ];
    emit();
  });
}

/**
 * promptDialog — replacement for window.prompt(). Resolves with the entered
 * text, or null when cancelled.
 */
export function promptDialog(options: PromptOptions): Promise<string | null> {
  return new Promise<string | null>((resolve) => {
    queue = [
      ...queue,
      {
        ...options,
        body: options.body ?? "",
        required: options.required ?? true,
        id: nextId++,
        kind: "prompt",
        resolve: resolve as (value: boolean | string | null) => void,
      },
    ];
    emit();
  });
}

export function settleConfirm(id: number, value: boolean | string | null) {
  const req = queue.find((r) => r.id === id);
  if (!req) return;
  queue = queue.filter((r) => r.id !== id);
  emit();
  if (req.kind === "confirm") {
    req.resolve(value === true);
  } else {
    req.resolve(typeof value === "string" ? value : null);
  }
}

export function subscribeConfirms(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getActiveConfirm(): ConfirmRequest | null {
  return queue[0] ?? null;
}
