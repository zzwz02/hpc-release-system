/**
 * DashboardPage — 总览: cycle statistics, components and App status.
 *
 * Roles: RM / Owner / QA / Guest.
 *
 * Data: GET /api/state (optionally ?release_id=).  RefreshBar shows the
 * content-fetch time from the TanStack Query dataUpdatedAt — NOT page-load time.
 *
 * Sections:
 *   1. Stats row — App counts, signups of the selected cycle, pending scope
 *      requests, QA breakdown (not_checked / qa_passed / has_issues / cannot_release).
 *   2. Components of the selected cycle — component/version/note rows.
 *   3. App status grid — owner-scoped for Owner role, full list for others.
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshBar } from "../../components/RefreshBar";
import { ReleaseDeadlines } from "../../components/ReleaseDeadlines";
import { apiGet } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { useUiStore } from "../../store/uiStore";
import { isOwner } from "../../lib/roles";
import { displayName } from "../../lib/identity";
import { normalizeDocTarget } from "../../lib/domainMetadata";
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
  ReleaseDetail,
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
// Selected cycle components
// ---------------------------------------------------------------------------

function ComponentsPanel({ release }: { release: ReleaseDetail | null }) {
  return (
    <div className="panel" data-testid="dashboard-components">
      <div className="panel-head">
        <h2>组件信息</h2>
        {release && <span className="count">{release.name}</span>}
      </div>
      <div className="panel-body p-0">
        <div className="table">
          <table>
            <thead><tr><th>组件</th><th>版本</th><th>说明</th></tr></thead>
            <tbody>
              {(release?.environment ?? []).map((row, index) => (
                <tr key={row.id ?? index}>
                  <td>{row.component}</td><td>{row.version || "未设置"}</td><td>{row.note || "—"}</td>
                </tr>
              ))}
              {!release?.environment.length && (
                <tr><td colSpan={3} className="muted p-14">
                  {release ? "RM 尚未填写该版本的组件信息。" : "还没有版本，等待 RM 新建版本。"}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
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

            <ComponentsPanel release={data.release} />
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
