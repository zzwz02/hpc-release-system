/**
 * App 工作台 — all Apps with their CICD status and signup for the selected
 * monthly cycle.
 *
 * Layout (unchanged): left = App list with search/filters, right = detail
 * panel with sub-tabs 本期报名 / app_info / CICD / 资料 / 变更记录.
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
  type AppStatusFilter,
} from "./helpers";
import { NewAppDialog } from "./NewAppDialog";
import { ChangeLogTable, CicdPendingPill, CicdStatusPill, EntryPill, QaDot, QaPill } from "./pills";
import { ProfilePane } from "./ProfilePane";
import { SignupPane } from "./SignupPane";

const STATE_KEY = (releaseId?: string) => (releaseId ? ["state", releaseId] : ["state"]);
const EMPTY_APPS: App[] = [];

async function fetchState(releaseId?: string): Promise<StatePayload> {
  const qs = new URLSearchParams({ include_allowed_actions: "1" });
  if (releaseId) qs.set("release_id", releaseId);
  return apiGet<StatePayload>(`/api/state?${qs.toString()}`);
}

type DetailTab = "signup" | "app_info" | "cicd" | "profile" | "changelog";

const DETAIL_TABS: Array<{ key: DetailTab; label: string }> = [
  { key: "signup", label: "本期报名" },
  { key: "app_info", label: "app_info" },
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
                    <span className="ver-tag">v{active.target_version}</span>
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
}

function DetailPanel({ app, entry, release, openRequests, onSaved }: DetailPanelProps) {
  const { user } = useAuth();
  const setAppDetailDirty = useUiStore((s) => s.setAppDetailDirty);
  const appDetailDirty = useUiStore((s) => s.appDetailDirty);
  const [tab, setTab] = useState<DetailTab>("signup");
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

  return (
    <div className="detail-panel" data-testid="detail-panel">
      <div className="detail-head">
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
        {isOwner(user) && editable && <div className="greet">你好 👋 在「本期报名」报名本月发布</div>}
      </div>

      <div className="cicd-subtabs bb-border mb-0">
        {DETAIL_TABS.map(({ key, label }) => (
          <button key={key} className={`cicd-subtab${tab === key ? " active" : ""}`}
            onClick={() => void switchTab(key)} data-testid={`detail-tab-${key}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "signup" && (
        release ? (
          <SignupPane app={app} entry={entry} release={release} canEdit={editable}
            onSaved={onSaved} onDirtyChange={setAppDetailDirty} />
        ) : (
          <div className="detail-body"><p className="muted">还没有版本，等待 RM 新建本月版本。</p></div>
        )
      )}
      {tab === "app_info" && <AppInfoPane app={app} canSync={canSyncAppInfo(user, app)} onSynced={onSaved} />}
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

  async function handleSelectApp(id: string) {
    if (selectedApp === id) return;
    if (appDetailDirty && selectedApp && !(await confirmDialog({
      title: "未保存的修改", body: "有未保存的修改，确认放弃并切换 app?", danger: true, confirmText: "放弃修改",
    }))) return;
    setAppDetailDirty(false);
    setSelectedApp(id);
  }

  function handleRefresh() {
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
            onChange={(e) => {
              setSelectedReleaseId(e.target.value);
              void queryClient.invalidateQueries({ queryKey: STATE_KEY(e.target.value) });
            }}>
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
            key={selectedRow?.app.id ?? ""}
            app={selectedRow?.app ?? null}
            entry={selectedRow?.entry ?? null}
            release={release}
            openRequests={openRequests ?? []}
            onSaved={() => { void queryClient.invalidateQueries({ queryKey: ["cicd"] }); void refetch(); }}
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
    </section>
  );
}
