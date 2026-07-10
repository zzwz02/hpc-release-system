/**
 * toast — module-level notification store, callable from anywhere
 * (event handlers, async mutations) without React context.
 *
 * Replaces window.alert(): non-blocking, stacked top-right, auto-dismiss.
 * Consumed by <Toaster /> via useSyncExternalStore.
 */

export type ToastKind = "ok" | "bad" | "info";

export interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

const SUCCESS_MS = 3500;
const INFO_MS = 4500;
const ERROR_MS = 8000;

let nextId = 1;
let items: ToastItem[] = [];
const listeners = new Set<() => void>();
const timers = new Map<number, ReturnType<typeof setTimeout>>();

function emit() {
  for (const fn of listeners) fn();
}

function push(kind: ToastKind, message: string, ttl: number) {
  const id = nextId++;
  items = [...items, { id, kind, message }];
  timers.set(
    id,
    setTimeout(() => dismissToast(id), ttl),
  );
  emit();
  return id;
}

export function dismissToast(id: number) {
  const timer = timers.get(id);
  if (timer) clearTimeout(timer);
  timers.delete(id);
  if (items.some((t) => t.id === id)) {
    items = items.filter((t) => t.id !== id);
    emit();
  }
}

export function subscribeToasts(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getToasts(): ToastItem[] {
  return items;
}

export const toast = {
  success: (message: string) => push("ok", message, SUCCESS_MS),
  error: (message: string) => push("bad", message, ERROR_MS),
  info: (message: string) => push("info", message, INFO_MS),
};
