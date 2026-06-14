/**
 * DashboardPage — 总览 tab (index.html:583-612, renderSummary, renderSchedule,
 * renderDashboardOwner).
 *
 * Roles: RM / Owner / QA / Guest.
 *
 * Data: GET /api/state (optionally ?release_id=).  RefreshBar shows the
 * content-fetch time from the TanStack Query dataUpdatedAt — NOT page-load time.
 *
 * Sections:
 *   1. Stats row — app counts, release-decision count, doc-incomplete count,
 *      QA breakdown (not_checked / qa_passed / has_issues / cannot_release).
 *   2. Schedule timeline — version/branch-cut/release-at/note rows.
 *      RM can add/edit/delete entries.
 *   3. App status grid — owner-scoped for Owner role, full list for others.
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshBar } from "../../components/RefreshBar";
import { apiGet, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { useUiStore } from "../../store/uiStore";
import { isRM, isOwner } from "../../lib/roles";
import { displayName } from "../../lib/identity";
import { formatServerTime } from "../../lib/time";
import { qaStatusLabels } from "../../lib/labels";
import type {
  StatePayload,
  Snapshot,
  ReleaseScheduleEntry,
  ReleaseSummary,
  App,
  QaStatus,
} from "../../types";

// ---------------------------------------------------------------------------
// Query key + fetcher
// ---------------------------------------------------------------------------

const STATE_QUERY_KEY = (releaseId?: string) =>
  releaseId ? ["state", releaseId] : ["state"];

async function fetchState(releaseId?: string): Promise<StatePayload> {
  const qs = releaseId ? `?release_id=${encodeURIComponent(releaseId)}` : "";
  return apiGet<StatePayload>(`/api/state${qs}`);
}

// ---------------------------------------------------------------------------
// Helpers (mirror index.html logic)
// ---------------------------------------------------------------------------

function releaseSnap(
  payload: StatePayload | undefined,
  appId: string,
): Snapshot | null {
  return payload?.release?.snapshots?.[appId] ?? null;
}

function isReleaseSnap(snap: Snapshot | null | undefined): boolean {
  return snap?.release_decision === "release";
}

function docsItems(snap: Snapshot | null | undefined): unknown[] {
  return (snap?.missing_items ?? []).filter((item) => {
    const kind =
      item && typeof item === "object" && "kind" in item
        ? (item as { kind: string }).kind
        : String(item || "").startsWith("QA ") ? "qa" : "doc";
    return kind !== "qa";
  });
}

function initials(name: string): string {
  const s = String(name || "").replace(/[^a-zA-Z0-9一-龥]/g, "");
  return (s.slice(0, 2) || "··").toUpperCase();
}

function usersLabel(
  usernames: string[] | null | undefined,
  displayNames: Record<string, string>,
): string {
  const items = (usernames ?? []).map((u) => {
    const dn = displayNames[u];
    return dn && dn !== u ? dn : u;
  }).filter(Boolean);
  return items.join(",") || "无 owner";
}

function daysUntil(dateStr: string): number {
  const target = new Date(dateStr + "T00:00:00");
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - now.getTime()) / 86400000);
}

const releaseDecisionOrder: Record<string, number> = {
  release: 0,
  cicd_only: 1,
  stopped: 2,
};

function compareAppRows(
  a: { snap: Snapshot },
  b: { snap: Snapshot },
  userIsOwner: boolean,
  username: string,
): number {
  if (userIsOwner) {
    const ownA = (a.snap.owners ?? []).includes(username) ? 0 : 1;
    const ownB = (b.snap.owners ?? []).includes(username) ? 0 : 1;
    if (ownA !== ownB) return ownA - ownB;
  }
  const da = releaseDecisionOrder[a.snap.release_decision] ?? 99;
  const db = releaseDecisionOrder[b.snap.release_decision] ?? 99;
  if (da !== db) return da - db;
  return displayName(a.snap).localeCompare(displayName(b.snap), "zh-CN");
}

function ownerProgress(snap: Snapshot): { done: number; total: number; pct: number } {
  const doc = snap.doc ?? { intro: "", image_usage: "", binary_usage: "", env_setup: "", limitations: "" };
  const t = (snap.test_docs ?? []).filter((d) => !(d as unknown as Record<string, unknown>)["obsolete"]);
  const hasInfo = !!(snap.app_info && (snap.app_info as Record<string, unknown>)["source_type"]);
  const checks = [
    hasInfo,
    !!(doc.intro ?? "").trim(),
    !!(doc.image_usage ?? "").trim(),
    !!(doc.binary_usage ?? "").trim(),
    !!(doc.env_setup ?? "").trim(),
    !!(doc.limitations ?? "").trim(),
    hasInfo && t.length > 0 && t.every((d) => {
      const td = d as unknown as Record<string, string>;
      return (td["dataset"] ?? "").trim() && (td["content"] ?? "").trim() && (td["pass_criteria"] ?? "").trim();
    }),
  ];
  const done = checks.filter(Boolean).length;
  return { done, total: checks.length, pct: Math.round((done / checks.length) * 100) };
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Colored "pill" badge for release_decision. */
function DecisionPill({ decision }: { decision: string }) {
  if (decision === "release") return <span className="pill accent">release</span>;
  if (decision === "cicd_only") return <span className="pill">仅 CICD</span>;
  if (decision === "stopped") return <span className="pill bad">已停止</span>;
  return <span className="pill">{decision || ""}</span>;
}

/** Colored pill for QA status. */
function QaPill({ status }: { status?: string }) {
  const s: QaStatus = (status as QaStatus) || "not_checked";
  const cls =
    s === "qa_passed" ? "ok" :
    s === "has_issues" ? "warnp" :
    s === "cannot_release" ? "bad" : "";
  return <span className={`pill ${cls}`}>{qaStatusLabels[s] ?? s}</span>;
}

// ---------------------------------------------------------------------------
// Stats row (index.html:1802-1837)
// ---------------------------------------------------------------------------

interface StatsRowProps {
  payload: StatePayload;
  userIsOwner: boolean;
  username: string;
}

function StatsRow({ payload, userIsOwner, username }: StatsRowProps) {
  const apps = payload.apps ?? [];
  const allRows = apps
    .map((app) => ({ app, snap: releaseSnap(payload, app.id) }))
    .filter((x): x is { app: App; snap: Snapshot } => x.snap !== null);

  const rows = userIsOwner
    ? allRows.filter((x) => (x.snap.owners ?? []).includes(username))
    : allRows;

  const releaseRows = rows.filter((x) => isReleaseSnap(x.snap));

  const byTarget = (list: typeof rows, t: string) =>
    list.filter((x) => x.snap.doc_target === t).length;

  const hpcAll = byTarget(rows, "manual");
  const aiAll = byTarget(rows, "ai4sci");
  const hpcRel = byTarget(releaseRows, "manual");
  const aiRel = byTarget(releaseRows, "ai4sci");
  const docIncomplete = releaseRows.filter((x) => docsItems(x.snap).length > 0).length;

  const qa = {
    not_checked: releaseRows.filter((x) => (x.snap.qa_status || "not_checked") === "not_checked").length,
    qa_passed: releaseRows.filter((x) => x.snap.qa_status === "qa_passed").length,
    has_issues: releaseRows.filter((x) => x.snap.qa_status === "has_issues").length,
    cannot_release: releaseRows.filter((x) => x.snap.qa_status === "cannot_release").length,
  };

  return (
    <div id="summary" className="stats">
      {/* Total app count */}
      <div className="stat">
        <div className="num">{rows.length}</div>
        <div className="lbl">App 总数</div>
        <div className="sub">HPC {hpcAll} · AI4S {aiAll}</div>
      </div>

      {/* Release decision count */}
      <div className="stat accent">
        <div className="num">{releaseRows.length}</div>
        <div className="lbl">release 决策</div>
        <div className="sub">HPC {hpcRel} · AI4S {aiRel}</div>
      </div>

      {/* Doc incomplete count */}
      <div className={`stat ${docIncomplete ? "warn" : "ok"}`}>
        <div className="num">{docIncomplete}</div>
        <div className="lbl">Doc 未完成 App 数</div>
      </div>

      {/* QA breakdown */}
      <div className="stat qa-split">
        <div className="num">
          <span className="seg-todo">{qa.not_checked}</span>
          <span className="sep">/</span>
          <span className="seg-ok">{qa.qa_passed}</span>
          <span className="sep">/</span>
          <span className="seg-warn">{qa.has_issues}</span>
          <span className="sep">/</span>
          <span className="seg-bad">{qa.cannot_release}</span>
        </div>
        <div className="lbl">QA 待测试 / 通过 / 存在问题 / 不可发布</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Schedule timeline (index.html:1842-1938)
// ---------------------------------------------------------------------------

interface ScheduleForm {
  version: string;
  branch_cut_at: string;
  release_at: string;
  note: string;
}

const emptyForm = (): ScheduleForm => ({
  version: "",
  branch_cut_at: "",
  release_at: "",
  note: "",
});

function entryToForm(e: ReleaseScheduleEntry): ScheduleForm {
  return {
    version: e.version ?? "",
    branch_cut_at: e.branch_cut_at ?? "",
    release_at: e.release_at ?? "",
    note: e.note ?? "",
  };
}

interface SchedulePanelProps {
  entries: ReleaseScheduleEntry[];
  userIsRM: boolean;
  onMutated: () => void;
}

function SchedulePanel({ entries, userIsRM, onMutated }: SchedulePanelProps) {
  // editId: entry.id being edited, "__new__" for add row, null for read-only
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<ScheduleForm>(emptyForm());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const today = new Date().toISOString().slice(0, 10);

  function startEdit(entry: ReleaseScheduleEntry) {
    setEditId(entry.id);
    setForm(entryToForm(entry));
    setError("");
  }

  function startNew() {
    setEditId("__new__");
    setForm(emptyForm());
    setError("");
  }

  function cancelEdit() {
    setEditId(null);
    setError("");
  }

  async function save() {
    if (!form.version.trim()) {
      setError("请填写版本号");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await apiPost("/api/release-schedule/upsert", {
        id: editId === "__new__" ? "" : editId,
        version: form.version.trim(),
        branch_cut_at: form.branch_cut_at,
        release_at: form.release_at,
        note: form.note.trim(),
      });
      setEditId(null);
      onMutated();
    } catch (e) {
      setError("保存失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
    }
  }

  async function deleteEntry(entry: ReleaseScheduleEntry) {
    if (!confirm(`确定删除发布时间线"${entry.version || entry.id}"？`)) return;
    try {
      await apiPost("/api/release-schedule/delete", { id: entry.id });
      onMutated();
    } catch (e) {
      alert("删除失败：" + (e instanceof Error ? e.message : String(e)));
    }
  }

  const cols = userIsRM ? 5 : 4;

  return (
    <div id="scheduleePanel" className="panel">
      <div className="panel-head">
        <h2>发布时间线</h2>
        <span className="count" id="scheduleCount">
          {entries.length ? `共 ${entries.length} 个版本` : ""}
        </span>
        <span style={{ flex: 1 }} />
        {userIsRM && (
          <button
            className="btn sm primary"
            onClick={startNew}
            disabled={editId !== null}
          >
            + 新增
          </button>
        )}
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {error && <div className="lerr" style={{ padding: "8px 12px" }}>{error}</div>}
        <div id="scheduleBox" className="schedule-box">
          <table>
            <thead>
              <tr>
                <th style={{ width: "25%" }}>版本号</th>
                <th style={{ width: "25%" }}>拉 branch 时间</th>
                <th style={{ width: "25%" }}>Release 发布时间</th>
                <th>备注</th>
                {userIsRM && <th style={{ width: 120, textAlign: "right" }}>操作</th>}
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) =>
                editId === entry.id ? (
                  <ScheduleEditRow
                    key={entry.id}
                    form={form}
                    onChange={(f) => setForm(f)}
                    onSave={save}
                    onCancel={cancelEdit}
                    saving={saving}
                  />
                ) : (
                  <ScheduleReadRow
                    key={entry.id}
                    entry={entry}
                    isRM={userIsRM}
                    today={today}
                    onEdit={() => startEdit(entry)}
                    onDelete={() => deleteEntry(entry)}
                  />
                )
              )}
              {userIsRM && editId === "__new__" && (
                <ScheduleEditRow
                  key="__new__"
                  form={form}
                  onChange={(f) => setForm(f)}
                  onSave={save}
                  onCancel={cancelEdit}
                  saving={saving}
                />
              )}
              {entries.length === 0 && editId !== "__new__" && (
                <tr className="empty-row">
                  <td colSpan={cols} className="muted" style={{ textAlign: "center", padding: 14 }}>
                    {userIsRM
                      ? '尚未维护发布时间线，点击右上角"+ 新增"填入版本号、拉 branch 时间、发布时间。'
                      : "尚未维护发布时间线。"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

interface ScheduleReadRowProps {
  entry: ReleaseScheduleEntry;
  isRM: boolean;
  today: string;
  onEdit: () => void;
  onDelete: () => void;
}

function ScheduleReadRow({ entry, isRM, today, onEdit, onDelete }: ScheduleReadRowProps) {
  const rel = entry.release_at ?? "";
  const cls = rel && rel < today ? "past" : "";
  const soon = rel && rel >= today && daysUntil(rel) <= 14 ? "soon" : "";
  return (
    <tr className={cls}>
      <td className="ver">{entry.version}</td>
      <td>{formatServerTime(entry.branch_cut_at) || "—"}</td>
      <td className={soon}>{formatServerTime(rel) || "—"}</td>
      <td>{entry.note || ""}</td>
      {isRM && (
        <td className="actions" style={{ textAlign: "right" }}>
          <button className="btn sm" onClick={onEdit}>编辑</button>{" "}
          <button className="btn sm danger" onClick={onDelete}>删除</button>
        </td>
      )}
    </tr>
  );
}

interface ScheduleEditRowProps {
  form: ScheduleForm;
  onChange: (f: ScheduleForm) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
}

function ScheduleEditRow({ form, onChange, onSave, onCancel, saving }: ScheduleEditRowProps) {
  return (
    <tr>
      <td>
        <input
          className="input sm"
          value={form.version}
          placeholder="例：3.0.0"
          onChange={(e) => onChange({ ...form, version: e.target.value })}
        />
      </td>
      <td>
        <input
          className="input sm"
          type="date"
          value={form.branch_cut_at}
          onChange={(e) => onChange({ ...form, branch_cut_at: e.target.value })}
        />
      </td>
      <td>
        <input
          className="input sm"
          type="date"
          value={form.release_at}
          onChange={(e) => onChange({ ...form, release_at: e.target.value })}
        />
      </td>
      <td>
        <input
          className="input sm"
          value={form.note}
          placeholder="选填"
          onChange={(e) => onChange({ ...form, note: e.target.value })}
        />
      </td>
      <td className="actions" style={{ textAlign: "right" }}>
        <button className="btn sm primary" onClick={onSave} disabled={saving}>
          {saving ? "…" : "保存"}
        </button>{" "}
        <button className="btn sm" onClick={onCancel} disabled={saving}>
          取消
        </button>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Owner app grid (index.html:1940-1981)
// ---------------------------------------------------------------------------

interface OwnerGridProps {
  payload: StatePayload;
  userIsOwner: boolean;
  username: string;
  onJumpToApp: (appId: string) => void;
}

function OwnerGrid({ payload, userIsOwner, username, onJumpToApp }: OwnerGridProps) {
  const apps = payload.apps ?? [];
  const allRows = apps
    .map((app) => ({ app, snap: releaseSnap(payload, app.id) }))
    .filter((x): x is { app: App; snap: Snapshot } => x.snap !== null);

  const gridRows = (userIsOwner
    ? allRows.filter((x) => (x.snap.owners ?? []).includes(username))
    : allRows.slice()
  ).sort((a, b) => compareAppRows(a, b, userIsOwner, username));

  const displayNames = payload.user_display_names ?? {};
  const title = userIsOwner ? "我的 App 状态概览" : "App 状态概览";

  return (
    <div id="dashboardOwnerPanel" className="panel">
      <div className="panel-head">
        <h2 id="dashboardOwnerTitle">{title}</h2>
        <span className="count" id="dashboardOwnerCount">共 {gridRows.length} 个</span>
        <span style={{ flex: 1 }} />
        <span className="muted small">点击行 → App 工作台查看 / 编辑</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        <div id="dashboardOwnerGrid">
          {gridRows.length === 0 ? (
            <p className="muted small" style={{ padding: "14px" }}>
              {userIsOwner
                ? "本 release 没有归属于你的 app。"
                : "本 release 暂无 app。"}
            </p>
          ) : (
            <table className="app-overview-table" data-testid="dashboard-app-table">
              <thead>
                <tr>
                  <th>App 名称</th>
                  <th>类型</th>
                  {!userIsOwner && <th>Owner</th>}
                  <th>决策</th>
                  <th>QA</th>
                  <th style={{ width: 150 }}>填写完成度</th>
                  <th style={{ width: 70 }}>待办</th>
                  <th style={{ width: 40 }} aria-label="跳转" />
                </tr>
              </thead>
              <tbody>
                {gridRows.map(({ app, snap }) => {
                  const rel = isReleaseSnap(snap);
                  const prog = ownerProgress(snap);
                  const todoCount = (snap.missing_items ?? []).length;
                  const ownersLabel = usersLabel(snap.owners, displayNames);
                  return (
                    <tr
                      key={app.id}
                      onClick={() => onJumpToApp(app.id)}
                      title="在 App 工作台中打开"
                      data-testid={`dashboard-app-row-${app.id}`}
                    >
                      <td>
                        <span className="row2" style={{ gap: 8, flexWrap: "nowrap" }}>
                          <span className="app-ico">{initials(displayName(snap))}</span>
                          <span className="ov-name">{displayName(snap)}</span>
                          {rel && snap.owner_confirmed && <span className="pill ok">已确认</span>}
                        </span>
                      </td>
                      <td className="muted">{snap.type ?? "—"}</td>
                      {!userIsOwner && <td className="muted">{ownersLabel}</td>}
                      <td><DecisionPill decision={snap.release_decision} /></td>
                      <td>{rel ? <QaPill status={snap.qa_status} /> : <span className="muted">—</span>}</td>
                      <td>
                        {rel ? (
                          <span className="row2" style={{ gap: 7, flexWrap: "nowrap" }}>
                            <span className="bar" style={{ flex: 1 }}>
                              <span style={{ width: `${prog.pct}%` }} />
                            </span>
                            <span className="prog-label">{prog.pct}%</span>
                          </span>
                        ) : <span className="muted">—</span>}
                      </td>
                      <td>
                        {rel
                          ? (todoCount > 0
                              ? <span className="pill warnp">{todoCount}</span>
                              : <span className="pill ok">齐全</span>)
                          : <span className="muted">—</span>}
                      </td>
                      <td className="ov-jump" style={{ textAlign: "center" }}>›</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Release selector (mirrors index.html release select in the header)
// ---------------------------------------------------------------------------

interface ReleaseSelectorProps {
  releases: ReleaseSummary[];
  selectedId: string | null;
  onChange: (id: string) => void;
}

function ReleaseSelector({ releases, selectedId, onChange }: ReleaseSelectorProps) {
  if (!releases.length) {
    return <span className="muted small">暂无 release</span>;
  }
  return (
    <select
      className="input"
      style={{ width: "auto", minWidth: 160 }}
      value={selectedId ?? ""}
      onChange={(e) => onChange(e.target.value)}
      aria-label="选择 release"
    >
      {releases.map((r) => (
        <option key={r.id} value={r.id}>
          {r.name}{r.maca_version ? ` (${r.maca_version})` : ""}
        </option>
      ))}
    </select>
  );
}

// ---------------------------------------------------------------------------
// DashboardPage
// ---------------------------------------------------------------------------

export function DashboardPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  // Shared release selector — kept in uiStore so all tabs stay in sync.
  // "" means "not yet seeded"; the seed effect below fills it on first load.
  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId) || undefined;
  const setSelectedReleaseId = useUiStore((s) => s.setSelectedReleaseId);
  const setSelectedApp = useUiStore((s) => s.setSelectedApp);

  // Cross-link: clicking a dashboard row selects the app (shared uiStore state)
  // and jumps to the App workbench, where its detail opens immediately.
  function handleJumpToApp(appId: string) {
    setSelectedApp(appId);
    navigate("/apps");
  }

  const queryKey = STATE_QUERY_KEY(selectedReleaseId);

  const { data, isFetching, dataUpdatedAt, refetch, error } = useQuery({
    queryKey,
    queryFn: () => fetchState(selectedReleaseId),
    // R2: no auto-refetch; only explicit refetch() or invalidateQueries
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    // Mount-fetch on first nav: override the global refetchOnMount:false
    refetchOnMount: true,
  });

  const userIsRM = isRM(user ?? undefined);
  const userIsOwner = isOwner(user ?? undefined);
  const username = user?.username ?? "";

  // After the initial fetch (no specific release ID), seed the shared store and
  // the release-specific query-cache key — this avoids a second network fetch.
  React.useEffect(() => {
    if (data?.release?.id && !selectedReleaseId) {
      // Pre-populate the more specific key so switching doesn't re-fetch
      queryClient.setQueryData(STATE_QUERY_KEY(data.release.id), data);
      setSelectedReleaseId(data.release.id);
    }
  }, [data, data?.release?.id, selectedReleaseId, setSelectedReleaseId, queryClient]);

  function handleReleaseChange(id: string) {
    setSelectedReleaseId(id);
    // Invalidate so next render refetches for the new release
    void queryClient.invalidateQueries({ queryKey: STATE_QUERY_KEY(id) });
  }

  function handleRefresh() {
    void refetch();
  }

  function handleScheduleMutated() {
    void refetch();
  }

  return (
    <section className="view active">
      <div className="page-toolbar">
        <h2>总览</h2>
        {data && (
          <ReleaseSelector
            releases={data.releases ?? []}
            selectedId={selectedReleaseId ?? data.release?.id ?? null}
            onChange={handleReleaseChange}
          />
        )}
        <span className="spacer" />
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          onRefresh={handleRefresh}
          isFetching={isFetching}
        />
      </div>

      {error && (
        <div className="lerr" style={{ padding: "12px 16px" }}>
          加载失败：{error instanceof Error ? error.message : String(error)}
        </div>
      )}

      {!data && isFetching && (
        <div className="muted" style={{ padding: "1rem" }}>加载中…</div>
      )}

      {data && (
        <>
          <div className="dashboard-top">
            <StatsRow
              payload={data}
              userIsOwner={userIsOwner}
              username={username}
            />

            <SchedulePanel
              entries={data.release_schedule ?? []}
              userIsRM={userIsRM}
              onMutated={handleScheduleMutated}
            />
          </div>

          <OwnerGrid
            payload={data}
            userIsOwner={userIsOwner}
            username={username}
            onJumpToApp={handleJumpToApp}
          />
        </>
      )}
    </section>
  );
}
