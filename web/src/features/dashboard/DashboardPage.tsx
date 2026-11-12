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
 *   1. Stats row — App counts, signups of the selected cycle, pending scope
 *      requests, QA breakdown (not_checked / qa_passed / has_issues / cannot_release).
 *   2. Schedule timeline — version/branch-cut/release-at/note rows.
 *      RM can add/edit/delete entries.
 *   3. App status grid — owner-scoped for Owner role, full list for others.
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DateInput } from "../../components/DateInput";
import { RefreshBar } from "../../components/RefreshBar";
import { ReleaseDeadlines } from "../../components/ReleaseDeadlines";
import { apiGet, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { useUiStore } from "../../store/uiStore";
import { isOwner } from "../../lib/roles";
import { can } from "../../lib/accessControl";
import { displayName } from "../../lib/identity";
import { formatDateValue, formatServerTime } from "../../lib/time";
import { normalizeDocTarget } from "../../lib/domainMetadata";
import { toast } from "../../lib/toast";
import { confirmDialog } from "../../lib/confirm";
import {
  APP_STATUS_FILTER_OPTIONS,
  appEntry,
  compareAppRows,
  matchesAppStatusFilter,
  usersLabel,
  versionTag,
  type AppStatusFilter,
} from "../appWorkbench/helpers";
import { CicdStatusPill, EntryPill, QaPill } from "../appWorkbench/pills";
import type {
  StatePayload,
  ReleaseScheduleEntry,
  ReleaseSummary,
  App,
  Entry,
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
// Helpers
// ---------------------------------------------------------------------------

function initials(name: string): string {
  const s = String(name || "").replace(/[^a-zA-Z0-9一-龥]/g, "");
  return (s.slice(0, 2) || "··").toUpperCase();
}

function daysUntil(dateStr: string): number {
  const target = new Date(dateStr + "T00:00:00");
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - now.getTime()) / 86400000);
}

interface Row {
  app: App;
  entry: Entry | null;
}

function rowsFor(payload: StatePayload, userIsOwner: boolean, username: string): Row[] {
  const rows = (payload.apps ?? []).map((app) => ({ app, entry: appEntry(payload.release, app.id) }));
  return userIsOwner ? rows.filter((row) => row.app.owners.includes(username)) : rows;
}

// ---------------------------------------------------------------------------
// Stats row
// ---------------------------------------------------------------------------

interface StatsRowProps {
  payload: StatePayload;
  userIsOwner: boolean;
  username: string;
}

function StatsRow({ payload, userIsOwner, username }: StatsRowProps) {
  const rows = rowsFor(payload, userIsOwner, username);
  const running = rows.filter((row) => row.app.cicd_status === "running");
  const signed = rows.filter((row) => row.entry?.status === "active");
  const byTarget = (list: Row[], t: string) =>
    list.filter((row) => normalizeDocTarget(row.app.doc_target) === t).length;
  const pendingScope = signed.filter((row) => row.entry?.pending_scope_request).length;
  const qa = {
    not_checked: signed.filter((row) => row.entry?.qa_status === "not_checked").length,
    qa_passed: signed.filter((row) => row.entry?.qa_status === "qa_passed").length,
    has_issues: signed.filter((row) => row.entry?.qa_status === "has_issues").length,
    cannot_release: signed.filter((row) => row.entry?.qa_status === "cannot_release").length,
  };

  return (
    <div id="summary" className="stats">
      <div className="stat">
        <div className="num">{running.length}</div>
        <div className="lbl">CICD 运行中 App</div>
        <div className="sub">HPC {byTarget(running, "manual")} · AI4S {byTarget(running, "ai4sci")} · 已停止 {rows.length - running.length}</div>
      </div>
      <div className="stat accent">
        <div className="num">{signed.length}</div>
        <div className="lbl">本期报名</div>
        <div className="sub">HPC {byTarget(signed, "manual")} · AI4S {byTarget(signed, "ai4sci")}</div>
      </div>
      <div className={`stat ${pendingScope ? "warn" : "ok"}`}>
        <div className="num">{pendingScope}</div>
        <div className="lbl">范围扩大待 QA 审批</div>
      </div>
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
    branch_cut_at: formatDateValue(e.branch_cut_at),
    release_at: formatDateValue(e.release_at),
    note: e.note ?? "",
  };
}

interface SchedulePanelProps {
  entries: ReleaseScheduleEntry[];
  canManage: boolean;
  onMutated: () => void;
}

function SchedulePanel({ entries, canManage, onMutated }: SchedulePanelProps) {
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
        branch_cut_at: formatDateValue(form.branch_cut_at),
        release_at: formatDateValue(form.release_at),
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
    if (!(await confirmDialog({
      body: `确定删除发布时间线"${entry.version || entry.id}"？`,
      danger: true,
      confirmText: "删除",
    }))) return;
    try {
      await apiPost("/api/release-schedule/delete", { id: entry.id });
      onMutated();
    } catch (e) {
      toast.error("删除失败：" + (e instanceof Error ? e.message : String(e)));
    }
  }

  const cols = canManage ? 5 : 4;

  return (
    <div id="scheduleePanel" className="panel">
      <div className="panel-head">
        <h2>发布时间线</h2>
        <span className="count" id="scheduleCount">
          {entries.length ? `共 ${entries.length} 个版本` : ""}
        </span>
        <span className="flex-1" />
        {canManage && (
          <button
            className="btn sm primary"
            onClick={startNew}
            disabled={editId !== null}
          >
            + 新增
          </button>
        )}
      </div>
      <div className="panel-body p-0">
        {error && <div className="lerr p-8-12">{error}</div>}
        <div id="scheduleBox" className="schedule-box">
          <table>
            <thead>
              <tr>
                <th className="w-25p">版本号</th>
                <th className="w-25p">拉 branch 时间</th>
                <th className="w-25p">Release 发布时间</th>
                <th>备注</th>
                {canManage && <th className="w-120 ta-r">操作</th>}
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
                    canManage={canManage}
                    today={today}
                    onEdit={() => startEdit(entry)}
                    onDelete={() => deleteEntry(entry)}
                  />
                )
              )}
              {canManage && editId === "__new__" && (
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
                  <td colSpan={cols} className="muted ta-c p-14">
                    {canManage
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
  canManage: boolean;
  today: string;
  onEdit: () => void;
  onDelete: () => void;
}

function ScheduleReadRow({ entry, canManage, today, onEdit, onDelete }: ScheduleReadRowProps) {
  const rel = entry.release_at ?? "";
  const cls = rel && rel < today ? "past" : "";
  const soon = rel && rel >= today && daysUntil(rel) <= 14 ? "soon" : "";
  return (
    <tr className={cls}>
      <td className="ver">{entry.version}</td>
      <td>{formatServerTime(entry.branch_cut_at) || "—"}</td>
      <td className={soon}>{formatServerTime(rel) || "—"}</td>
      <td>{entry.note || ""}</td>
      {canManage && (
        <td>
          <div className="cell-actions">
            <button className="btn sm" onClick={onEdit}>编辑</button>
            <button className="btn sm danger" onClick={onDelete}>删除</button>
          </div>
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
        <DateInput
          value={form.branch_cut_at}
          onChange={(value) => onChange({ ...form, branch_cut_at: value })}
          testId="schedule-branch-cut"
          ariaLabel="拉 branch 时间"
          size="sm"
        />
      </td>
      <td>
        <DateInput
          value={form.release_at}
          onChange={(value) => onChange({ ...form, release_at: value })}
          testId="schedule-release-at"
          ariaLabel="Release 发布时间"
          size="sm"
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
      <td>
        <div className="cell-actions">
          <button className="btn sm primary" onClick={onSave} disabled={saving}>
            {saving ? "…" : "保存"}
          </button>
          <button className="btn sm" onClick={onCancel} disabled={saving}>
            取消
          </button>
        </div>
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
  const [statusFilter, setStatusFilter] = useState<AppStatusFilter>("all");
  const user = { username, role: userIsOwner ? "Owner" : "", display_name: "" };
  const gridRows = rowsFor(payload, userIsOwner, username)
    .filter((row) => matchesAppStatusFilter(row.app, row.entry, statusFilter))
    .sort((a, b) => compareAppRows(a, b, user));
  const displayNames = payload.user_display_names ?? {};
  const title = userIsOwner ? "我的 App 状态概览" : "App 状态概览";

  return (
    <div id="dashboardOwnerPanel" className="panel">
      <div className="panel-head">
        <h2 id="dashboardOwnerTitle">{title}</h2>
        <span className="count" id="dashboardOwnerCount">共 {gridRows.length} 个</span>
        <select className="select sm select-inline" aria-label="App 状态筛选" value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as AppStatusFilter)} data-testid="dashboard-app-status-filter">
          {APP_STATUS_FILTER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
        <span className="flex-1" />
        <span className="muted small">点击行 → App 工作台查看 / 报名</span>
      </div>
      <div className="panel-body p-0">
        <div id="dashboardOwnerGrid">
          {gridRows.length === 0 ? (
            <p className="muted small p-14">{userIsOwner ? "没有符合条件的、归属于你的 app。" : "没有符合条件的 app。"}</p>
          ) : (
            <table className="app-overview-table" data-testid="dashboard-app-table">
              <thead>
                <tr>
                  <th>App 名称</th>
                  <th>类型</th>
                  {!userIsOwner && <th>Owner</th>}
                  <th>CICD</th>
                  <th>本期</th>
                  <th>QA</th>
                  <th>正式版</th>
                  <th className="w-40" aria-label="跳转" />
                </tr>
              </thead>
              <tbody>
                {gridRows.map(({ app, entry }) => {
                  const active = entry?.status === "active" ? entry : null;
                  return (
                    <tr key={app.id} onClick={() => onJumpToApp(app.id)} title="在 App 工作台中打开"
                      data-testid={`dashboard-app-row-${app.id}`}>
                      <td>
                        <span className="row2 gap-8 nowrap-flex">
                          <span className="app-ico">{initials(displayName(app))}</span>
                          <span className="ov-name">{displayName(app)}</span>
                          {active?.target_version && <span className="ver-tag">{versionTag(active.target_version)}</span>}
                        </span>
                      </td>
                      <td className="muted">{app.type || "—"}</td>
                      {!userIsOwner && <td className="muted">{usersLabel(app.owners, displayNames)}</td>}
                      <td><CicdStatusPill app={app} /></td>
                      <td><EntryPill entry={entry} /></td>
                      <td>{active ? <QaPill status={active.qa_status} /> : <span className="muted">—</span>}</td>
                      <td className="muted">{app.official ? `${app.official.version}（${app.official.release_name}）` : "—"}</td>
                      <td className="ov-jump ta-c">›</td>
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
// Release selector
// ---------------------------------------------------------------------------

interface ReleaseSelectorProps {
  releases: ReleaseSummary[];
  selectedId: string | null;
  onChange: (id: string) => void;
}

function ReleaseSelector({ releases, selectedId, onChange }: ReleaseSelectorProps) {
  if (!releases.length) return <span className="muted small">暂无版本</span>;
  return (
    <select className="input select-inline" value={selectedId ?? ""} onChange={(e) => onChange(e.target.value)}
      aria-label="选择版本">
      {releases.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
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

  const canManageSchedule = can(user?.role, "release.manage");
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
        <ReleaseDeadlines release={data?.release} />
        <span className="spacer" />
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          onRefresh={handleRefresh}
          isFetching={isFetching}
        />
      </div>

      {error && (
        <div className="lerr p-12-16">
          加载失败：{error instanceof Error ? error.message : String(error)}
        </div>
      )}

      {!data && isFetching && (
        <div className="muted p-1r">加载中…</div>
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
              canManage={canManageSchedule}
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
