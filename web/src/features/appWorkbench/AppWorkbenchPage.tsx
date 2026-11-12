/**
 * App 工作台 — all Apps with their CICD status and signup for the selected
 * monthly cycle.
 *
 * Layout: left = App list with search/filters, right = detail
 * panel with sub-tabs 基本信息和app_info / CICD / 资料 / 变更记录.
 *
 * Data: GET /api/state (?release_id=).  Data moves only via explicit refetch
 * after a mutation or manual refresh.
 */
import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchCicdDeliveries, fetchCicdRequests } from "../cicd/cicdApi";
import { requestMatchesApp, uniqueCicdRequestCount } from "../cicd/requestUtils";
import { RefreshBar } from "../../components/RefreshBar";
import { ReleaseDeadlines } from "../../components/ReleaseDeadlines";
import { apiGet, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { confirmDialog } from "../../lib/confirm";
import { docTargetLabels } from "../../lib/labels";
import { normalizeDocTarget } from "../../lib/domainMetadata";
import { canCreateApp, canEdit, canEditRmFields, canSyncAppInfo, isOwner, isRM } from "../../lib/roles";
import { can } from "../../lib/accessControl";
import { toast } from "../../lib/toast";
import { useUiStore } from "../../store/uiStore";
import type { App, AppAuditEntry, Entry, ReleaseDetail, StatePayload } from "../../types";
import { AppCicdPane } from "./AppCicdPane";
import { AppInfoPane } from "./AppInfoPane";
import {
  APP_STATUS_FILTER_OPTIONS,
  appEntry,
  compareAppRows,
  filterAppRows,
  matchesAppStatusFilter,
  usersLabel,
  versionTag,
  type AppStatusFilter,
} from "./helpers";
import { NewAppDialog } from "./NewAppDialog";
import { ChangeLogTable, CicdPendingPill, CicdStatusPill, EntryPill, QaDot, QaPill } from "./pills";
import { ProfilePane } from "./ProfilePane";
import { SignupDialog } from "./SignupDialog";

const STATE_KEY = (releaseId?: string) => (releaseId ? ["state", releaseId] : ["state"]);
const EMPTY_APPS: App[] = [];

async function fetchState(releaseId?: string): Promise<StatePayload> {
  const qs = new URLSearchParams({ include_allowed_actions: "1" });
  if (releaseId) qs.set("release_id", releaseId);
  return apiGet<StatePayload>(`/api/state?${qs.toString()}`);
}

type DetailTab = "app_info" | "cicd" | "profile" | "changelog";

const DETAIL_TABS: Array<{ key: DetailTab; label: string }> = [
  { key: "app_info", label: "基本信息和app_info" },
  { key: "cicd", label: "CICD" },
  { key: "profile", label: "资料" },
  { key: "changelog", label: "变更记录" },
];

// ---------------------------------------------------------------------------
// App list
// ---------------------------------------------------------------------------

interface Row {
  app: App;
  entry: Entry | null;
}

interface AppListProps {
  rows: Row[];
  selectedAppId: string | null;
  onSelectApp: (id: string) => void;
  search: string;
  onSearchChange: (v: string) => void;
  ownOnly: boolean;
  onOwnOnlyChange: (v: boolean) => void;
  showOwnOnly: boolean;
  statusFilter: AppStatusFilter;
  onStatusFilterChange: (v: AppStatusFilter) => void;
  displayNames: Record<string, string>;
  cicdPendingCounts: Record<string, number>;
  canCreateApp: boolean;
  onNewApp: () => void;
}

function AppListPanel({
  rows, selectedAppId, onSelectApp, search, onSearchChange, ownOnly, onOwnOnlyChange, showOwnOnly,
  statusFilter, onStatusFilterChange, displayNames, cicdPendingCounts, canCreateApp, onNewApp,
}: AppListProps) {
  return (
    <div className="app-list-panel">
      <div className="app-list-toolbar">
        <input className="input" placeholder="搜索 app…" value={search}
          onChange={(e) => onSearchChange(e.target.value)} data-testid="app-search" />
        {showOwnOnly && (
          <label className="check nowrap">
            <input type="checkbox" checked={ownOnly} onChange={(e) => onOwnOnlyChange(e.target.checked)}
              data-testid="own-only-checkbox" />
            只看我的
          </label>
        )}
        <select className="select sm select-inline" aria-label="App 状态筛选" value={statusFilter}
          onChange={(e) => onStatusFilterChange(e.target.value as AppStatusFilter)} data-testid="app-status-filter">
          {APP_STATUS_FILTER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
        <span className="count" data-testid="app-count">共 {rows.length} 个</span>
        {canCreateApp && (
          <button className="btn sm primary" onClick={onNewApp} data-testid="new-app-btn">+ 新增</button>
        )}
      </div>
      <div className="app-table" data-testid="app-table">
        {rows.length === 0 ? (
          <p className="muted small p-14 ta-c">无数据</p>
        ) : rows.map(({ app, entry }) => {
          const active = entry?.status === "active" ? entry : null;
          return (
            <div key={app.id} className={`app-row ${selectedAppId === app.id ? "active" : ""}`}
              onClick={() => onSelectApp(app.id)} data-testid={`app-row-${app.id}`}>
              <QaDot entry={active} />
              <div className="app-meta">
                <div className="name">
                  <span className="nm-txt">{app.name}</span>
                  {active && active.target_version && !app.name.includes(active.target_version) && (
                    <span className="ver-tag">{versionTag(active.target_version)}</span>
                  )}
                </div>
                <div className="sub">{app.type || "—"} · {usersLabel(app.owners, displayNames)}</div>
                <div className="app-status">
                  {app.cicd_status === "stopped" && <CicdStatusPill app={app} />}
                  <EntryPill entry={entry} />
                  {active && <QaPill status={active.qa_status} />}
                  {active?.pending_scope_request && <span className="pill warnp">范围扩大待审批</span>}
                  {app.sync_error && app.cicd_status === "running" && <span className="pill bad">app_info 同步失败</span>}
                  <CicdPendingPill count={cicdPendingCounts[app.id] ?? 0} />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

interface DetailPanelProps {
  app: App | null;
  entry: Entry | null;
  release: ReleaseDetail | null;
  openRequests: import("../../types").CicdRequest[];
  onSaved: () => void;
  onSignup: (appId: string) => void;
}

function DetailPanel({ app, entry, release, openRequests, onSaved, onSignup }: DetailPanelProps) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const setAppDetailDirty = useUiStore((s) => s.setAppDetailDirty);
  const appDetailDirty = useUiStore((s) => s.appDetailDirty);
  const [tab, setTab] = useState<DetailTab>("app_info");
  const [syncing, setSyncing] = useState(false);
  const [audit, setAudit] = useState<AppAuditEntry[] | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);

  const { data: history } = useQuery({
    queryKey: ["cicd", "requests", "app-workbench", "history", app?.id ?? ""],
    queryFn: () => fetchCicdRequests({ taskId: app!.id }),
    enabled: !!app && tab === "cicd",
    staleTime: 0,
  });

  React.useEffect(() => {
    setAudit(null);
  }, [app?.id, release?.id]);

  React.useEffect(() => {
    if (tab !== "changelog" || !app || audit || auditLoading) return;
    setAuditLoading(true);
    const qs = new URLSearchParams({ app_id: app.id });
    if (release) qs.set("release_id", release.id);
    apiGet<{ entries: AppAuditEntry[] }>(`/api/app-audit?${qs.toString()}`)
      .then((r) => setAudit(r.entries))
      .catch((e) => toast.error(e instanceof Error ? e.message : String(e)))
      .finally(() => setAuditLoading(false));
  }, [tab, app, release, audit, auditLoading]);

  async function switchTab(next: DetailTab) {
    if (next === tab) return;
    if (appDetailDirty && !(await confirmDialog({
      title: "未保存的修改", body: "有未保存的修改，确认放弃并切换？", danger: true, confirmText: "放弃修改",
    }))) return;
    setAppDetailDirty(false);
    setTab(next);
  }

  async function handleSync() {
    if (!app || appDetailDirty) return;
    setSyncing(true);
    try {
      const result = await apiPost<{ status: "updated" | "unchanged" | "error"; error?: string }>(
        "/api/app-info/sync", { app_id: app.id, force: true },
      );
      if (result.status === "error") toast.error(`同步失败：${result.error ?? ""}`);
      else if (result.status === "updated") toast.success("已同步到新的 app_info");
      else toast.info("app_info 没有变化");
    } catch (e) {
      toast.error(`同步失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      void queryClient.invalidateQueries({ queryKey: ["app-detail", app.id] });
      onSaved();
      setSyncing(false);
    }
  }

  if (!app) {
    return (
      <div className="detail-panel" data-testid="detail-panel">
        <div className="detail-body"><p className="muted p-1r">← 选择左侧 App 查看详情</p></div>
      </div>
    );
  }

  const editable = canEdit(user, app);
  const active = entry?.status === "active" ? entry : null;
  const appOpen = openRequests.filter((req) => requestMatchesApp(req, app));
  const pendingScope = active?.pending_scope_request;
  const scopeBlocked = active?.behind_latest && release && release.phase !== "signup" && release.phase !== "released";

  return (
    <div className="detail-panel" data-testid="detail-panel">
      <div className="detail-head" data-testid="app-detail-header">
        <QaDot entry={active} />
        <div className="dh-main">
          <h2>{app.name}</h2>
          <div className="row2">
            <CicdStatusPill app={app} />
            <EntryPill entry={entry} />
            {active && <QaPill status={active.qa_status} />}
            <span className="pill">{docTargetLabels[normalizeDocTarget(app.doc_target)]}</span>
            {app.official && <span className="pill" title={`${app.official.release_name} 发布`}>正式版 {app.official.version}</span>}
            <CicdPendingPill count={uniqueCicdRequestCount(appOpen)} />
          </div>
        </div>
        {editable && (
          <button className="btn sm" onClick={() => onSignup(app.id)} disabled={appDetailDirty || app.cicd_status !== "running"}
            title={appDetailDirty ? "请先保存或取消修改，再报名" : app.cicd_status !== "running" ? "CICD 已停止，不能报名" : "选择版本报名"}
            data-testid="app-signup-btn">报名</button>
        )}
      </div>

      {(pendingScope || scopeBlocked) && (
        <div className="banner warnp app-scope-warning" role="status" data-testid="app-scope-warning">
          <b>{release?.name}：{pendingScope ? `测试范围扩大待 QA 审批（#${pendingScope.id}）` : "测试范围扩大未获 QA 同意"}</b>
          <div className="small mt-4">最新 app_info 已同步；本期报名仍使用原锁定版本{active?.locked_app_info?.commit_id ? `（${active.locked_app_info.commit_id.slice(0, 12)}）` : ""}测试。</div>
          {pendingScope && <div className="small mt-4 app-scope-additions">{pendingScope.additions.join("；")}</div>}
        </div>
      )}
      {app.sync_error && <div className="banner bad app-scope-warning" role="alert" data-testid="app-info-sync-error">最近一次同步失败：{app.sync_error}</div>}

      <div className="cicd-subtabs bb-border mb-0">
        {DETAIL_TABS.map(({ key, label }) => (
          <button key={key} className={`cicd-subtab${tab === key ? " active" : ""}`}
            onClick={() => void switchTab(key)} data-testid={`detail-tab-${key}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "app_info" && <AppInfoPane app={app} canEdit={editable} canEditRmFields={canEditRmFields(user)}
        onSaved={onSaved} onDirtyChange={setAppDetailDirty}
        onSync={canSyncAppInfo(user, app) ? () => void handleSync() : undefined}
        syncing={syncing} syncDisabled={appDetailDirty} />}
      {tab === "cicd" && (
        <AppCicdPane
          app={app}
          signedUpCycles={app.open_signups}
          canEdit={editable}
          pendingRequests={appOpen}
          historyRequests={(history?.requests ?? []).filter((req) => !req.is_open)}
          onSubmitted={onSaved}
          onDirtyChange={setAppDetailDirty}
        />
      )}
      {tab === "profile" && (
        <ProfilePane app={app} canEdit={editable} canEditRmFields={canEditRmFields(user)}
          onSaved={onSaved} onDirtyChange={setAppDetailDirty} />
      )}
      {tab === "changelog" && (
        <div className="detail-body">
          <ChangeLogTable entries={audit} loading={auditLoading} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function AppWorkbenchPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const { selectedApp, setSelectedApp, appDetailDirty, setAppDetailDirty } = useUiStore();
  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId) || undefined;
  const setSelectedReleaseId = useUiStore((s) => s.setSelectedReleaseId);
  const queryKey = STATE_KEY(selectedReleaseId);

  const { data, isFetching, dataUpdatedAt, refetch, error } = useQuery({
    queryKey,
    queryFn: () => fetchState(selectedReleaseId),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  React.useEffect(() => {
    if (data?.release?.id && !selectedReleaseId) {
      queryClient.setQueryData(STATE_KEY(data.release.id), data);
      setSelectedReleaseId(data.release.id);
    }
  }, [data, selectedReleaseId, setSelectedReleaseId, queryClient]);

  const [search, setSearch] = useState("");
  const [ownOnly, setOwnOnly] = useState(user?.role === "Owner");
  React.useEffect(() => setOwnOnly(user?.role === "Owner"), [user?.username, user?.role]);
  const [statusFilter, setStatusFilter] = useState<AppStatusFilter>("all");
  const [showNewApp, setShowNewApp] = useState(false);
  const [syncingAll, setSyncingAll] = useState(false);
  const [detailReset, setDetailReset] = useState(0);
  const [signupDialog, setSignupDialog] = useState<{ appId?: string } | null>(null);

  const release = data?.release ?? null;
  const apps = data?.apps ?? EMPTY_APPS;
  const releases = data?.releases ?? [];
  const displayNames = data?.user_display_names ?? {};

  const allRows: Row[] = apps.map((app) => ({ app, entry: appEntry(release, app.id) }));
  const rows = filterAppRows(allRows, search, ownOnly, user, displayNames)
    .filter((row) => matchesAppStatusFilter(row.app, row.entry, statusFilter))
    .sort((a, b) => compareAppRows(a, b, user));
  const selectedRow = allRows.find((row) => row.app.id === selectedApp) ?? null;

  const { data: openRequests } = useQuery({
    queryKey: ["cicd", "requests", "app-workbench", "open-summary"],
    queryFn: async () => {
      const [pending, deliveries] = await Promise.all([
        fetchCicdRequests({ status: "pending" }),
        fetchCicdDeliveries("pending_or_returned"),
      ]);
      return [...(pending.requests ?? []), ...(deliveries.deliveries ?? [])];
    },
    enabled: apps.length > 0,
    staleTime: 0,
    refetchOnMount: true,
  });

  const cicdPendingCounts = React.useMemo(() => {
    const counts: Record<string, number> = {};
    for (const app of apps) {
      const count = uniqueCicdRequestCount((openRequests ?? []).filter((req) => requestMatchesApp(req, app)));
      if (count) counts[app.id] = count;
    }
    return counts;
  }, [openRequests, apps]);

  async function discardChanges() {
    if (appDetailDirty && !(await confirmDialog({
      title: "未保存的修改", body: "有未保存的修改，确认放弃？", danger: true, confirmText: "放弃修改",
    }))) return false;
    if (appDetailDirty) setDetailReset((current) => current + 1);
    setAppDetailDirty(false);
    return true;
  }

  async function handleSelectApp(id: string) {
    if (selectedApp === id || (appDetailDirty && !(await discardChanges()))) return;
    setSelectedApp(id);
  }

  async function handleSelectRelease(id: string) {
    if (selectedReleaseId === id || (appDetailDirty && !(await discardChanges()))) return;
    setSelectedReleaseId(id);
    void queryClient.invalidateQueries({ queryKey: STATE_KEY(id) });
  }

  async function handleRefresh() {
    if (appDetailDirty && !(await discardChanges())) return;
    void queryClient.invalidateQueries({ queryKey: ["cicd"] });
    void refetch();
  }

  async function handleSyncAll() {
    if (!(await confirmDialog({
      title: "同步全部 app_info",
      body: "对所有 CICD 运行中的 App 立即检查仓库并同步 app_info.json？同步在后台进行，结果可在「周期管理 → app_info 同步状态」查看。",
      confirmText: "开始同步",
    }))) return;
    setSyncingAll(true);
    try {
      await apiPost("/api/app-info/sync-all", {});
      toast.success("已开始同步全部 app_info，稍后刷新查看结果。");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncingAll(false);
    }
  }

  if (error) {
    return (
      <section className="view active">
        <p className="muted p-1r">加载失败：{(error as Error).message}</p>
      </section>
    );
  }

  return (
    <section className="view view--split active" data-testid="appworkbench-page">
      <div className="page-toolbar">
        <h2>App 工作台</h2>
        {data && releases.length > 0 && (
          <select className="select select-inline" value={selectedReleaseId ?? ""} aria-label="选择版本"
            onChange={(e) => void handleSelectRelease(e.target.value)}>
            {releases.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        )}
        {isRM(user) && (
          <button className="btn sm" onClick={() => void handleSyncAll()} disabled={syncingAll}
            title="立即对所有运行中的 App 同步 app_info" data-testid="sync-all-app-info-btn">
            同步全部 app_info
          </button>
        )}
        <ReleaseDeadlines release={release} />
        <span className="spacer" />
        {(can(user?.role, "app.edit.any") || can(user?.role, "app.edit.owned")) && (
          <button className="btn primary sm" disabled={appDetailDirty || !data}
            title={appDetailDirty ? "请先保存或取消修改，再报名" : "为自己名下运行中的 App 批量报名"}
            onClick={() => setSignupDialog({})} data-testid="batch-signup-btn">批量报名</button>
        )}
        <RefreshBar dataUpdatedAt={dataUpdatedAt} isFetching={isFetching} onRefresh={handleRefresh} />
      </div>

      {isFetching && !data ? (
        <div className="muted p-1r">加载中…</div>
      ) : (
        <div className="apps-layout">
          <AppListPanel
            rows={rows}
            selectedAppId={selectedApp}
            onSelectApp={(id) => void handleSelectApp(id)}
            search={search}
            onSearchChange={setSearch}
            ownOnly={ownOnly}
            onOwnOnlyChange={setOwnOnly}
            showOwnOnly={isOwner(user)}
            statusFilter={statusFilter}
            onStatusFilterChange={setStatusFilter}
            displayNames={displayNames}
            cicdPendingCounts={cicdPendingCounts}
            canCreateApp={canCreateApp(user)}
            onNewApp={() => setShowNewApp(true)}
          />
          <DetailPanel
            key={`${selectedRow?.app.id ?? ""}:${detailReset}`}
            app={selectedRow?.app ?? null}
            entry={selectedRow?.entry ?? null}
            release={release}
            openRequests={openRequests ?? []}
            onSaved={() => { void queryClient.invalidateQueries({ queryKey: ["cicd"] }); void refetch(); }}
            onSignup={(appId) => setSignupDialog({ appId })}
          />
        </div>
      )}

      {showNewApp && (
        <NewAppDialog
          onClose={() => setShowNewApp(false)}
          onSubmitted={() => {
            setShowNewApp(false);
            void queryClient.invalidateQueries({ queryKey: ["cicd"] });
          }}
        />
      )}
      {signupDialog && (
        <SignupDialog key={`${user?.username}:${user?.role}`} appId={signupDialog.appId} releases={releases}
          initialReleaseId={selectedReleaseId} onClose={() => setSignupDialog(null)}
          onSignedUp={(releaseId) => {
            void queryClient.invalidateQueries({ queryKey: ["state"] });
            void queryClient.invalidateQueries({ queryKey: ["cicd"] });
            void queryClient.invalidateQueries({ queryKey: ["app-signup"] });
            setSelectedReleaseId(releaseId);
            setSignupDialog(null);
          }} />
      )}
    </section>
  );
}
