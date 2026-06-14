/**
 * Time formatting utilities.
 *
 * Contract (brief §2 — single contract, NO double offset):
 *   - Server sends naive Beijing strings ("YYYY-MM-DD HH:MM:SS").
 *   - Frontend displays them with ZERO offset: passthrough/normalize only.
 *   - The legacy toBeijing() +8 hack (index.html:2001) is NOT ported here.
 *
 * Dev note: the un-migrated release_system.db may still have UTC-ISO timestamps
 * in a few columns, so some values may appear 8 h off in dev.  That is expected
 * for the dev DB; do NOT add offset logic to "fix" it.
 */

/**
 * Normalize a server-sent timestamp string for display.
 *
 * Rules:
 *   - Empty / null / undefined → ""
 *   - Contains "T": replace with space, strip trailing fractional seconds
 *   - Contains timezone offset ("Z" or "+HH:MM" / "-HH:MM"): strip it
 *     (the brief says no +8 math; we just drop the offset marker so the
 *     naive digits are shown as-is)
 *   - Otherwise: return the string unchanged
 *
 * Examples:
 *   "2026-05-11 14:00:00"         → "2026-05-11 14:00:00"
 *   "2026-05-11T14:00:00"         → "2026-05-11 14:00:00"
 *   "2026-05-11T14:00:00.123456"  → "2026-05-11 14:00:00"
 *   "2026-05-11T14:00:00Z"        → "2026-05-11 14:00:00"  (no +8 applied)
 *   ""                            → ""
 */
export function formatServerTime(s: string | null | undefined): string {
  if (!s) return "";
  let t = String(s).trim();
  if (!t) return "";

  // Replace T separator
  t = t.replace("T", " ");

  // Strip fractional seconds (.123456)
  t = t.replace(/\.\d+/, "");

  // Strip timezone suffix ("Z", "+08:00", "-05:00", "+0800", etc.)
  t = t.replace(/[Zz]$/, "").replace(/[+-]\d{2}:?\d{2}$/, "").trim();

  return t;
}

/**
 * Format a client-side epoch millisecond value (e.g. from Date.now() or
 * queryState.dataUpdatedAt) into a local "YYYY-MM-DD HH:MM:SS" string.
 *
 * Used exclusively by RefreshBar to display the moment the data was fetched.
 * This uses the client's local clock — no server time involved.
 */
export function formatClientFetchTime(epochMs: number): string {
  if (!epochMs) return "";
  const d = new Date(epochMs);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}
