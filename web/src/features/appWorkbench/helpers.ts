/**
 * Pure helpers shared by App 工作台, 总览 and QA: entry lookup, status
 * filters, sorting and labels.  No React, no side effects.
 */
import type { App, Entry, ReleaseDetail, User } from "../../types";
import { qaStatusLabels } from "../../lib/labels";
import { appSortName, displayName } from "../../lib/identity";
import { isOwner } from "../../lib/roles";

// ---------------------------------------------------------------------------
// Entry lookup
// ---------------------------------------------------------------------------

/** The App's entry in the cycle (active or withdrawn), if any. */
export function appEntry(release: ReleaseDetail | null | undefined, appId: string): Entry | null {
  return release?.entries?.find((entry) => entry.app_id === appId) ?? null;
}

/** The App's active signup in the cycle, if any. */
export function activeEntry(release: ReleaseDetail | null | undefined, appId: string): Entry | null {
  const entry = appEntry(release, appId);
  return entry && entry.status === "active" ? entry : null;
}

// ---------------------------------------------------------------------------
// Status filter
// ---------------------------------------------------------------------------

export type AppStatusFilter = "all" | "signed_up" | "not_signed_up" | "qa_anomaly" | "stopped";

export const APP_STATUS_FILTER_OPTIONS: readonly { value: AppStatusFilter; label: string }[] = [
  { value: "all", label: "所有" },
  { value: "signed_up", label: "只看已报名" },
  { value: "not_signed_up", label: "只看未报名" },
  { value: "qa_anomaly", label: "只看QA异常" },
  { value: "stopped", label: "只看CICD已停止" },
];

export function matchesAppStatusFilter(app: App, entry: Entry | null, filter: AppStatusFilter): boolean {
  const active = entry?.status === "active";
  switch (filter) {
    case "signed_up":
      return active;
    case "not_signed_up":
      return !active && app.cicd_status === "running";
    case "qa_anomaly":
      return active && ["has_issues", "cannot_release"].includes(entry?.qa_status ?? "");
    case "stopped":
      return app.cicd_status === "stopped";
    default:
      return true;
  }
}

// ---------------------------------------------------------------------------
// QA dot
// ---------------------------------------------------------------------------

export function qaDotClass(entry: Entry | null | undefined): string {
  if (!entry || entry.status !== "active") return "na";
  const s = entry.qa_status;
  if (s === "qa_passed") return "ok";
  if (s === "has_issues") return "warn";
  if (s === "cannot_release") return "bad";
  return "todo";
}

export function qaDotTitle(entry: Entry | null | undefined): string {
  if (!entry || entry.status !== "active") return "未报名本版本";
  return "QA：" + (qaStatusLabels[entry.qa_status] ?? entry.qa_status);
}

// ---------------------------------------------------------------------------
// Sorting and search
// ---------------------------------------------------------------------------

/** Owner's own Apps first, then signed-up, running, stopped; then by name. */
export function compareAppRows(
  a: { app: App; entry: Entry | null },
  b: { app: App; entry: Entry | null },
  user: User | null | undefined,
): number {
  if (isOwner(user)) {
    const un = user?.username ?? "";
    const ownA = a.app.owners.includes(un) ? 0 : 1;
    const ownB = b.app.owners.includes(un) ? 0 : 1;
    if (ownA !== ownB) return ownA - ownB;
  }
  const rank = (row: { app: App; entry: Entry | null }) =>
    row.entry?.status === "active" ? 0 : row.app.cicd_status === "running" ? 1 : 2;
  if (rank(a) !== rank(b)) return rank(a) - rank(b);
  return appSortName(a.app).localeCompare(appSortName(b.app), "zh-CN");
}

export function userLabel(username: string, displayNames: Record<string, string>): string {
  const dn = displayNames[username];
  return dn && dn !== username ? `${dn} (${username})` : username;
}

export function usersLabel(owners: string[] | null | undefined, displayNames: Record<string, string>): string {
  if (!owners?.length) return "无 owner";
  return owners.map((u) => displayNames[u] || u).join(", ");
}

export function usersSearchText(owners: string[] | null | undefined, displayNames: Record<string, string>): string {
  return (owners ?? []).map((u) => `${u} ${displayNames[u] ?? ""}`).join(" ");
}

export function filterAppRows<T extends { app: App }>(
  rows: T[],
  query: string,
  ownOnly: boolean,
  user: User | null | undefined,
  displayNames: Record<string, string>,
): T[] {
  const q = query.toLowerCase().trim();
  let result = rows;
  if (ownOnly && user) result = result.filter((r) => r.app.owners.includes(user.username));
  if (q) {
    result = result.filter(({ app }) =>
      [displayName(app), app.id, app.type, usersSearchText(app.owners, displayNames)]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }
  return result;
}

// ---------------------------------------------------------------------------
// Chips and description
// ---------------------------------------------------------------------------

/** Chip list in display order: alphabetical with X201 last. */
export function orderChips(value: string | readonly string[] | null | undefined): string {
  const items = (Array.isArray(value) ? value : String(value ?? "").split(/[,，、;；/]+/))
    .map((s) => String(s).trim())
    .filter(Boolean);
  const seen: string[] = [];
  items.forEach((c) => { if (!seen.includes(c)) seen.push(c); });
  const rest = seen.filter((c) => c.toLowerCase() !== "x201")
    .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  const tail = seen.filter((c) => c.toLowerCase() === "x201");
  return [...rest, ...tail].join(",");
}

export const APP_DESCRIPTION_LIMIT = 30;

/** Mirrors the backend: an ASCII alphanumeric run is one unit; other characters one each. */
export function appDescriptionCount(text: string | null | undefined): number {
  return ((text ?? "").trim().match(/[A-Za-z0-9]+|[^\s]/gu) ?? []).length;
}

export function shortCommit(commit: string | null | undefined): string {
  return (commit ?? "").slice(0, 12);
}
