/**
 * AdminPage — 系统管理 tab (index.html:795-847, 2017+, 5044-5065, renderMembers).
 *
 * Roles: Admin only (enforced by RequireRole in AppRouter).
 *
 * Two sub-panels:
 *   1. 数据库管理
 *      - 清空数据库: confirm text "CLEAR_DATABASE" + admin password → POST /api/admin/clear-db
 *      - 删除单个 App: table from GET /api/state apps + POST /api/admin/apps/delete (prompt)
 *   2. 成员管理
 *      - GET /api/admin/users → table with role dropdowns
 *      - POST /api/admin/users/set-role per row
 *
 * Data for the app-delete table comes from the shared state (GET /api/state).
 * Members are loaded separately on-demand.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshBar } from "../../components/RefreshBar";
import { apiGet, apiPost } from "../../api/http";
import { useUiStore } from "../../store/uiStore";
import { displayName } from "../../lib/identity";
import { formatGerritUrl } from "../../lib/git";
import type {
  StatePayload,
  AdminUser,
  AdminUsersResponse,
  AdminClearDbResponse,
  AdminDeleteAppResponse,
  AdminSetRoleResponse,
} from "../../types";

// ---------------------------------------------------------------------------
// Query keys + fetchers
// ---------------------------------------------------------------------------

const STATE_QK = (releaseId?: string) =>
  releaseId ? ["state", releaseId] : ["state"];

async function fetchState(releaseId?: string): Promise<StatePayload> {
  const qs = releaseId
    ? `?release_id=${encodeURIComponent(releaseId)}`
    : "";
  return apiGet<StatePayload>(`/api/state${qs}`);
}

const USERS_QK = ["admin", "users"] as const;

async function fetchUsers(): Promise<AdminUsersResponse> {
  return apiGet<AdminUsersResponse>("/api/admin/users");
}

// ---------------------------------------------------------------------------
// Role constants (mirrors index.html:5162)
// ---------------------------------------------------------------------------

const ALL_ROLES = ["RM", "Owner", "QA", "Guest", "Admin", "SPD"] as const;

const ROLE_SOURCE_LABEL: Record<string, string> = {
  local: "内建账号",
  ldap: "域账号",
};

const ROLE_PILL_CLASS: Record<string, string> = {
  RM: "accent",
  Admin: "bad",
  QA: "warn",
  Guest: "",
  SPD: "",
  Owner: "",
};

// ---------------------------------------------------------------------------
// Sub-panel: database management
// ---------------------------------------------------------------------------

interface DbPanelProps {
  payload: StatePayload | undefined;
  onStateRefresh: () => void;
}

function DbPanel({ payload, onStateRefresh }: DbPanelProps) {
  const [clearConfirm, setClearConfirm] = useState("");
  const [clearPassword, setClearPassword] = useState("");
  const [log, setLog] = useState("");
  const [clearing, setClearing] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const apps = payload?.apps ?? [];

  async function handleClearDb() {
    if (!clearPassword) {
      alert("请重新输入 admin 密码以确认");
      return;
    }
    if (!window.confirm("确认要备份并清空数据库？")) return;
    setClearing(true);
    setLog("清空中...");
    try {
      const r = await apiPost<AdminClearDbResponse>("/api/admin/clear-db", {
        confirm: clearConfirm,
        password: clearPassword,
      });
      setLog("已清空，备份文件：" + r.backup);
      setClearPassword("");
      setClearConfirm("");
      onStateRefresh();
    } catch (e) {
      alert("清空失败：" + (e instanceof Error ? e.message : String(e)));
      setLog("");
    } finally {
      setClearing(false);
    }
  }

  async function handleDeleteApp(appId: string) {
    const confirmText = window.prompt(
      `删除 app 会先备份数据库，并删除所有未锁定 release 中的相关 snapshot。\n\n请输入 app_id 确认删除：${appId}`,
    );
    if (confirmText !== appId) return;
    setDeletingId(appId);
    setLog("删除中...");
    try {
      const r = await apiPost<AdminDeleteAppResponse>(
        "/api/admin/apps/delete",
        { app_id: appId, confirm: confirmText },
      );
      void r;
      setLog(`已删除 ${appId}，操作完成`);
      onStateRefresh();
    } catch (e) {
      setLog(
        "删除失败：" + (e instanceof Error ? e.message : String(e)),
      );
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <>
      {/* Clear database */}
      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-head">
          <h2>清空数据库</h2>
        </div>
        <div className="panel-body">
          <p className="hint" style={{ marginTop: 0 }}>
            清空数据库会先备份当前 SQLite 文件，然后删除 app、release、snapshot、artifact
            和审计数据；默认账号会保留。需重新输入 admin 密码确认。
          </p>
          <div className="row" style={{ marginTop: 11, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input
              className="input"
              placeholder="输入：CLEAR_DATABASE"
              style={{ minWidth: 230 }}
              value={clearConfirm}
              onChange={(e) => setClearConfirm(e.target.value)}
              data-testid="clear-confirm-input"
            />
            <input
              className="input"
              type="password"
              placeholder="再次输入 admin 密码"
              style={{ minWidth: 230 }}
              autoComplete="current-password"
              value={clearPassword}
              onChange={(e) => setClearPassword(e.target.value)}
              data-testid="clear-password-input"
            />
            <button
              className="btn danger"
              onClick={handleClearDb}
              disabled={clearing}
              data-testid="clear-db-btn"
            >
              备份并清空数据库
            </button>
          </div>
          {log && (
            <div className="log" data-testid="admin-log">
              {log}
            </div>
          )}
        </div>
      </div>

      {/* Delete single app */}
      <div className="panel">
        <div className="panel-head">
          <h2>删除单个 App</h2>
        </div>
        <div className="panel-body">
          <p className="hint" style={{ marginTop: 0 }}>
            删除前会自动备份数据库；已最终锁定的 release 中的 app 不允许删除。
          </p>
          <div className="table" style={{ marginTop: 12 }}>
            <table data-testid="admin-app-table">
              <thead>
                <tr>
                  <th>App</th>
                  <th>Owner</th>
                  <th>Gerrit</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {apps.length > 0 ? (
                  apps.map((app) => {
                    const snap =
                      payload?.release?.snapshots?.[app.id];
                    const name = snap ? displayName(snap) : app.id;
                    const owners = snap?.owners ?? [];
                    return (
                      <tr key={app.id}>
                        <td>
                          <b>{name}</b>
                          <div className="small muted">{app.id}</div>
                        </td>
                        <td>{owners.join(", ")}</td>
                        <td>
                          {formatGerritUrl(app.git_url)}
                          <div className="small muted">
                            {app.git_branch || ""}
                          </div>
                        </td>
                        <td>
                          <button
                            className="btn danger sm"
                            onClick={() => handleDeleteApp(app.id)}
                            disabled={deletingId === app.id}
                            data-testid={`delete-app-${app.id}`}
                          >
                            删除
                          </button>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={4} className="muted">
                      无数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Sub-panel: member management
// ---------------------------------------------------------------------------

interface MemberPanelProps {
  users: AdminUser[];
  isFetching: boolean;
  dataUpdatedAt: number;
  onRefresh: () => void;
}

function MemberPanel({
  users,
  isFetching,
  dataUpdatedAt,
  onRefresh,
}: MemberPanelProps) {
  // Local role overrides keyed by username (before save)
  const [roleOverrides, setRoleOverrides] = useState<Record<string, string>>({});
  const [savingUser, setSavingUser] = useState<string | null>(null);
  const [saveLog, setSaveLog] = useState<Record<string, string>>({});

  async function handleSaveRole(username: string) {
    const newRole = roleOverrides[username] ?? users.find((u) => u.username === username)?.role ?? "";
    setSavingUser(username);
    try {
      await apiPost<AdminSetRoleResponse>("/api/admin/users/set-role", {
        username,
        role: newRole,
      });
      setSaveLog((prev) => ({
        ...prev,
        [username]: `已将角色设为 ${newRole}`,
      }));
      // Remove override since it's now saved
      setRoleOverrides((prev) => {
        const next = { ...prev };
        delete next[username];
        return next;
      });
      onRefresh();
    } catch (e) {
      alert(
        "保存失败：" + (e instanceof Error ? e.message : String(e)),
      );
    } finally {
      setSavingUser(null);
    }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>成员管理</h2>
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          isFetching={isFetching}
          onRefresh={onRefresh}
        />
      </div>
      <div className="panel-body">
        <p className="hint" style={{ marginTop: 0 }}>
          列出所有内建账号与已通过域账号登录过的用户，可在此调整角色。
          角色说明：<b>RM</b> — 发布管理；<b>Owner</b> — App 负责人；
          <b>QA</b> — 质量测试；<b>Admin</b> — 系统管理；
          <b>SPD</b> — 交付执行。
        </p>
        <div className="table" style={{ marginTop: 12 }}>
          <table data-testid="members-table">
            <thead>
              <tr>
                <th>用户名</th>
                <th>登录方式</th>
                <th>当前角色</th>
                <th>修改为</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.length > 0 ? (
                users.map((u) => {
                  const currentRole = roleOverrides[u.username] ?? u.role;
                  const pillCls = ROLE_PILL_CLASS[u.role] ?? "";
                  return (
                    <tr key={u.username}>
                      <td>
                        <b>
                          {u.display_name
                            ? `${u.display_name} (${u.username})`
                            : u.username}
                        </b>
                      </td>
                      <td>
                        <span
                          className={`pill${u.auth_source === "ldap" ? " accent" : ""}`}
                        >
                          {ROLE_SOURCE_LABEL[u.auth_source] ?? u.auth_source}
                        </span>
                      </td>
                      <td>
                        <span className={`pill ${pillCls}`}>{u.role}</span>
                      </td>
                      <td>
                        <select
                          className="select sm"
                          style={{ width: 88 }}
                          value={currentRole}
                          onChange={(e) =>
                            setRoleOverrides((prev) => ({
                              ...prev,
                              [u.username]: e.target.value,
                            }))
                          }
                          data-testid={`role-select-${u.username}`}
                        >
                          {ALL_ROLES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <button
                          className="btn sm primary"
                          onClick={() => handleSaveRole(u.username)}
                          disabled={savingUser === u.username}
                          data-testid={`save-role-${u.username}`}
                        >
                          保存
                        </button>
                        {saveLog[u.username] && (
                          <span
                            className="small muted"
                            style={{ marginLeft: 8 }}
                          >
                            {saveLog[u.username]}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={5} className="muted">
                    无数据
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

// ---------------------------------------------------------------------------
// AdminPage
// ---------------------------------------------------------------------------

type AdminPane = "db" | "members";

export function AdminPage() {
  const queryClient = useQueryClient();
  const [activePane, setActivePane] = useState<AdminPane>("db");

  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId) || undefined;

  // State query for the app table in DbPanel
  const {
    data: stateData,
    isFetching: stateFetching,
    dataUpdatedAt: stateUpdatedAt,
    refetch: refetchState,
    error: stateError,
  } = useQuery({
    queryKey: STATE_QK(selectedReleaseId),
    queryFn: () => fetchState(selectedReleaseId),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  // Users query — only fetched when the members pane is active
  const {
    data: usersData,
    isFetching: usersFetching,
    dataUpdatedAt: usersUpdatedAt,
    refetch: refetchUsers,
    error: usersError,
  } = useQuery({
    queryKey: USERS_QK,
    queryFn: fetchUsers,
    enabled: activePane === "members",
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  const users = usersData?.users ?? [];

  return (
    <section className="view active">
      <div className="page-toolbar">
        <h2>系统管理</h2>
        <span className="spacer" />
        <RefreshBar
          dataUpdatedAt={
            activePane === "members" ? usersUpdatedAt : stateUpdatedAt
          }
          isFetching={activePane === "members" ? usersFetching : stateFetching}
          onRefresh={() => {
            if (activePane === "members") void refetchUsers();
            else void refetchState();
          }}
        />
      </div>

      {/* Sub-tabs */}
      <div className="subtabs">
        <button
          className={`subtab${activePane === "db" ? " active" : ""}`}
          onClick={() => setActivePane("db")}
          data-testid="admin-tab-db"
        >
          数据库管理
        </button>
        <button
          className={`subtab${activePane === "members" ? " active" : ""}`}
          onClick={() => setActivePane("members")}
          data-testid="admin-tab-members"
        >
          成员管理
        </button>
      </div>

      {/* Error banners */}
      {stateError && activePane === "db" && (
        <div style={{ padding: "1rem", color: "var(--bad)" }}>
          加载失败：{stateError instanceof Error ? stateError.message : String(stateError)}
        </div>
      )}
      {usersError && activePane === "members" && (
        <div style={{ padding: "1rem", color: "var(--bad)" }}>
          加载成员失败：{usersError instanceof Error ? usersError.message : String(usersError)}
        </div>
      )}

      {/* Panes */}
      {activePane === "db" && (
        <DbPanel
          payload={stateData}
          onStateRefresh={() => {
            void queryClient.invalidateQueries({ queryKey: STATE_QK(selectedReleaseId) });
          }}
        />
      )}

      {activePane === "members" && (
        <MemberPanel
          users={users}
          isFetching={usersFetching}
          dataUpdatedAt={usersUpdatedAt}
          onRefresh={() => void refetchUsers()}
        />
      )}
    </section>
  );
}
