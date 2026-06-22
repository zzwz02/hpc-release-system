/**
 * Pure helper functions for the App 工作台 tab.
 *
 * All derived from legacy index.html — no React, no side-effects.
 */
import type { Snapshot, SnapshotMissingItem, App, ReleaseDetail, SnapshotTestDoc } from "../../types";
import {
  releaseDecisionOrder,
  qaStatusLabels,
} from "../../lib/labels";
import { displayName, appSortName } from "../../lib/identity";
import { isOwner } from "../../lib/roles";
import type { User } from "../../types";

// ---------------------------------------------------------------------------
// Snapshot lookup
// ---------------------------------------------------------------------------

export function releaseSnap(
  release: ReleaseDetail | null | undefined,
  appId: string,
): Snapshot | null {
  return release?.snapshots?.[appId] ?? null;
}

export function isReleaseSnap(snap: Snapshot | null | undefined): boolean {
  return snap?.release_decision === "release";
}

// ---------------------------------------------------------------------------
// Missing-item helpers (mirrors index.html:2095-2108)
// ---------------------------------------------------------------------------

export function missingItemKind(item: SnapshotMissingItem | string): string {
  if (item && typeof item === "object") return (item as SnapshotMissingItem).kind || "doc";
  return String(item || "").startsWith("QA ") ? "qa" : "doc";
}

export function missingItemText(item: SnapshotMissingItem | string): string {
  if (item && typeof item === "object") return (item as SnapshotMissingItem).text || "";
  return String(item || "");
}

export function docsItems(snap: Snapshot | null | undefined): SnapshotMissingItem[] {
  return ((snap?.missing_items ?? []) as (SnapshotMissingItem | string)[])
    .filter((item) => missingItemKind(item) !== "qa") as SnapshotMissingItem[];
}

export function docsOk(snap: Snapshot | null | undefined): boolean {
  return isReleaseSnap(snap) && docsItems(snap).length === 0;
}

export function qaOk(snap: Snapshot | null | undefined): boolean {
  return ["qa_passed", "has_issues"].includes(snap?.qa_status ?? "");
}

// ---------------------------------------------------------------------------
// QA dot CSS class (mirrors index.html:2082-2093)
// ---------------------------------------------------------------------------

export function qaDotClass(snap: Snapshot | null | undefined): string {
  if (!isReleaseSnap(snap)) return "na";
  const s = snap?.qa_status ?? "not_checked";
  if (s === "qa_passed") return "ok";
  if (s === "has_issues") return "warn";
  if (s === "cannot_release") return "bad";
  return "todo";
}

export function qaDotTitle(snap: Snapshot | null | undefined): string {
  if (!isReleaseSnap(snap)) return `${snap?.release_decision ?? "非 release"}：不纳入 QA`;
  const s = snap?.qa_status ?? "not_checked";
  return "QA：" + (qaStatusLabels[s as keyof typeof qaStatusLabels] ?? s);
}

// ---------------------------------------------------------------------------
// App sorting (mirrors index.html:1741-1749)
// ---------------------------------------------------------------------------

export function compareAppRows(
  a: { snap: Snapshot },
  b: { snap: Snapshot },
  user: User | null | undefined,
): number {
  if (isOwner(user)) {
    const un = user?.username ?? "";
    const ownA = (a.snap.owners ?? []).includes(un) ? 0 : 1;
    const ownB = (b.snap.owners ?? []).includes(un) ? 0 : 1;
    if (ownA !== ownB) return ownA - ownB;
  }
  const da = releaseDecisionOrder[a.snap.release_decision] ?? 99;
  const db = releaseDecisionOrder[b.snap.release_decision] ?? 99;
  if (da !== db) return da - db;
  return appSortName(a.snap).localeCompare(appSortName(b.snap), "zh-CN");
}

// ---------------------------------------------------------------------------
// User label / display helpers
// ---------------------------------------------------------------------------

export function userLabel(
  username: string,
  displayNames: Record<string, string>,
): string {
  const dn = displayNames[username];
  return dn && dn !== username ? `${dn} (${username})` : username;
}

export function usersLabel(
  owners: string[] | null | undefined,
  displayNames: Record<string, string>,
): string {
  if (!owners?.length) return "无 owner";
  return owners.map((u) => {
    const dn = displayNames[u];
    return dn && dn !== u ? dn : u;
  }).join(", ");
}

export function usersSearchText(
  owners: string[] | null | undefined,
  displayNames: Record<string, string>,
): string {
  return (owners ?? []).map((u) => {
    const dn = displayNames[u] ?? "";
    return `${u} ${dn}`;
  }).join(" ");
}

// ---------------------------------------------------------------------------
// Progress completion (mirrors index.html:2135-2150)
// ---------------------------------------------------------------------------

export function ownerProgress(snap: Snapshot): { done: number; total: number; pct: number } {
  const doc = snap.doc ?? { intro: "", image_usage: "", binary_usage: "", env_setup: "", limitations: "" };
  const t = (snap.test_docs ?? []).filter((d) => !d.obsolete);
  const hasInfo = !!(snap.app_info && (snap.app_info as Record<string, unknown>)["source_type"]);
  const checks = [
    hasInfo,
    !!(doc.intro ?? "").trim(),
    !!(doc.image_usage ?? "").trim(),
    !!(doc.binary_usage ?? "").trim(),
    !!(doc.env_setup ?? "").trim(),
    !!(doc.limitations ?? "").trim(),
    hasInfo && t.length > 0 && t.every(
      (d) => (d.dataset ?? "").trim() && (d.content ?? "").trim() && (d.pass_criteria ?? "").trim(),
    ),
  ];
  const done = checks.filter(Boolean).length;
  return { done, total: checks.length, pct: Math.round((done / checks.length) * 100) };
}

// ---------------------------------------------------------------------------
// app_info source label (mirrors index.html:2165-2170)
// ---------------------------------------------------------------------------

export function appInfoSource(snap: Snapshot | null | undefined): string {
  const info = (snap?.app_info ?? {}) as Record<string, unknown>;
  if (!info["source_type"]) return "未提供";
  if (info["source_type"] === "gerrit_fetch")
    return `Gerrit 拉取；commit=${info["commit_id"] ?? "未知"}；${info["source"] ?? ""}`;
  if (info["source_type"] === "owner_upload")
    return `Owner 上传；上传人=${info["uploaded_by"] ?? "未知"}；文件=${info["source"] ?? ""}`;
  return `${info["source_type"]}: ${info["source"] ?? ""}`;
}

// ---------------------------------------------------------------------------
// orderChips (mirrors index.html:2344-2351)
// ---------------------------------------------------------------------------

export function orderChips(value: string | null | undefined): string {
  const items = String(value ?? "").split(/[,，、;；/]+/).map((s) => s.trim()).filter(Boolean);
  const seen: string[] = [];
  items.forEach((c) => { if (!seen.includes(c)) seen.push(c); });
  const rest = seen.filter((c) => c.toLowerCase() !== "x201")
    .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  const tail = seen.filter((c) => c.toLowerCase() === "x201");
  return [...rest, ...tail].join(",");
}

// ---------------------------------------------------------------------------
// Description character count (mirrors legacy appDescriptionCount)
// ---------------------------------------------------------------------------

const APP_DESCRIPTION_LIMIT = 30;

export { APP_DESCRIPTION_LIMIT };

export function appDescriptionCount(text: string | null | undefined): number {
  return (text ?? "").trim().length;
}

// ---------------------------------------------------------------------------
// F2 — copy editable fields from another release's snapshot
//
// Copies the owner-editable doc/test CONTENT fields an owner would re-enter.
// Deliberately does NOT copy identity/version/owner-confirmed/QA fields
// (official_name, owners, git_url/branch, release_decision, version, qa_*),
// which are release-specific or come from app_info.
// ---------------------------------------------------------------------------

export interface CopiedSnapshotFields {
  type: string;
  official_url: string;
  description: string;
  doc_target: string;
  intro: string;
  image_usage: string;
  binary_usage: string;
  env_setup: string;
  limitations: string;
  community_release: string;
  community_python: string;
  community_framework: string;
  sanity_arm: boolean;
  sanity_ubuntu: boolean;
}

export function copiedScalarFields(s: Snapshot): CopiedSnapshotFields {
  return {
    type: s.type ?? "",
    official_url: s.official_url ?? "",
    description: s.description ?? "",
    doc_target: s.doc_target ?? "manual",
    intro: s.doc?.intro ?? "",
    image_usage: s.doc?.image_usage ?? "",
    binary_usage: s.doc?.binary_usage ?? "",
    env_setup: s.doc?.env_setup ?? "",
    limitations: s.doc?.limitations ?? "",
    community_release: s.community?.release_status ?? "",
    community_python: s.community?.python_version ?? "",
    community_framework: s.community?.framework_version ?? "",
    sanity_arm: s.sanity?.arm_kylin ?? false,
    sanity_ubuntu: s.sanity?.ubuntu ?? false,
  };
}

/**
 * Merge a source release's test_docs content into the current form's test_docs.
 *
 * Matches by `path`: for each current entry that exists in the source, copy the
 * text fields (and `command` only for owner-added rows, since auto rows keep the
 * app_info command). Source owner-added rows whose path is absent are appended.
 * Current rows with no source match are left untouched.
 */
export function mergeCopiedTestDocs(
  current: SnapshotTestDoc[],
  source: SnapshotTestDoc[],
): SnapshotTestDoc[] {
  const sourceByPath = new Map(source.map((d) => [d.path, d]));
  const result: SnapshotTestDoc[] = current.map((d) => {
    const src = sourceByPath.get(d.path);
    if (!src) return { ...d };
    return {
      ...d,
      content: src.content ?? d.content,
      dataset: src.dataset ?? d.dataset,
      result_view: src.result_view ?? d.result_view,
      pass_criteria: src.pass_criteria ?? d.pass_criteria,
      ...(d.owner_added ? { command: src.command ?? d.command } : {}),
    };
  });
  const currentPaths = new Set(current.map((d) => d.path));
  source
    .filter((d) => d.owner_added && !currentPaths.has(d.path))
    .forEach((d) => {
      result.push({ ...d, id: `copied_${d.path}` });
    });
  return result;
}

// ---------------------------------------------------------------------------
// App-row filter (mirrors renderApps filter logic)
// ---------------------------------------------------------------------------

export function filterAppRows(
  rows: { app: App; snap: Snapshot }[],
  query: string,
  ownOnly: boolean,
  user: User | null | undefined,
  displayNames: Record<string, string>,
): { app: App; snap: Snapshot }[] {
  const q = query.toLowerCase().trim();
  let result = rows;
  if (ownOnly && user) {
    result = result.filter((r) => (r.snap.owners ?? []).includes(user.username));
  }
  if (q) {
    result = result.filter(({ snap }) =>
      [displayName(snap), snap.type, usersSearchText(snap.owners, displayNames)]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }
  return result;
}
