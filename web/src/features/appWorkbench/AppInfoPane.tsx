/**
 * app_info — the latest app_info.json from the App's repository (synced
 * automatically every day), the stored version history and the App's
 * official (released) versions.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiPost } from "../../api/http";
import { toast } from "../../lib/toast";
import { formatServerTime } from "../../lib/time";
import type { App } from "../../types";
import { orderChips, shortCommit } from "./helpers";

interface AppDetailResponse {
  app_info_versions: Array<{
    id: number;
    commit_id: string;
    source: string;
    fetched_at: string;
    fetched_by: string;
  }>;
  official_versions: Array<{
    id: number;
    release_name: string;
    version: string;
    image: string;
    digest: string;
    released_at: string;
    withdrawn: number;
  }>;
}

interface SyncResult {
  status: "updated" | "unchanged" | "error";
  error?: string;
}

export function AppInfoPane({ app, canSync, onSynced }: { app: App; canSync: boolean; onSynced: () => void }) {
  const info = app.latest_app_info;
  const [syncing, setSyncing] = React.useState(false);
  const { data: detail, refetch } = useQuery({
    queryKey: ["app-detail", app.id, info?.id ?? 0],
    queryFn: () => apiGet<AppDetailResponse>(`/api/apps/detail?app_id=${encodeURIComponent(app.id)}`),
    staleTime: 0,
  });

  async function handleSync() {
    setSyncing(true);
    try {
      const result = await apiPost<SyncResult>("/api/app-info/sync", { app_id: app.id, force: true });
      if (result.status === "error") toast.error(`同步失败：${result.error ?? ""}`);
      else if (result.status === "updated") toast.success("已同步到新的 app_info");
      else toast.info("app_info 没有变化");
      onSynced();
      void refetch();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="detail-body" data-testid="app-info-pane">
      <div className="banner plain-strip">
        <div className="row gap-8 items-center wrap">
          <b className="fs-12">app_info 来自仓库</b>
          <span className="small muted">每日自动同步；仓库是唯一来源，不再手工上传。</span>
          <span className="spacer" />
          {canSync && (
            <button className="btn sm" onClick={() => void handleSync()} disabled={syncing} data-testid="app-info-sync">
              {syncing ? "同步中…" : "立即同步"}
            </button>
          )}
        </div>
        <div className="small muted mt-2 fs-11">
          最近检查：{formatServerTime(app.sync_checked_at) || "—"}
          {app.sync_head_commit && <> · 分支头 {shortCommit(app.sync_head_commit)}</>}
        </div>
      </div>
      {app.sync_error && (
        <div className="banner bad" data-testid="app-info-sync-error">最近一次同步失败：{app.sync_error}</div>
      )}

      {!info ? (
        <div className="banner warnp">尚未取得 app_info。</div>
      ) : (
        <>
          <details className="section" open>
            <summary><span className="chev">▶</span> 最新 app_info</summary>
            <div className="section-body">
              <div className="form">
                <label>版本<input className="input" value={info.app_version} disabled /></label>
                <label>Commit<input className="input mono" value={shortCommit(info.commit_id)} disabled /></label>
                <label>取得时间<input className="input" value={formatServerTime(info.fetched_at)} disabled /></label>
                <label>X86 芯片<input className="input" value={orderChips(info.x86_chips)} disabled /></label>
                <label>ARM 芯片<input className="input" value={orderChips(info.arm_chips)} disabled /></label>
                <label>OS / Arch<input className="input" value={`${info.build_os.join(",")} / ${info.build_arches.join(",")}`} disabled /></label>
                <label>Python label<input className="input" value={info.python_labels.join(",")} disabled /></label>
                <label>PyTorch label<input className="input" value={info.pytorch_labels.join(",")} disabled /></label>
                <label>依赖组件（app_dependency）
                  <input className="input" disabled
                    value={info.dependencies === null ? "未声明" : info.dependencies.join(", ") || "（空）"} />
                </label>
              </div>
              <div className="table mt-10">
                <table>
                  <thead><tr><th>测试项</th><th>芯片</th><th>命令</th></tr></thead>
                  <tbody>
                    {info.tests.length === 0 ? (
                      <tr><td colSpan={3} className="muted">没有启用的测试项</td></tr>
                    ) : info.tests.map((test) => (
                      <tr key={test.path}>
                        <td className="mono small">{test.path}</td>
                        <td className="small">{orderChips(test.supported_chips)}</td>
                        <td className="mono small break">{test.command}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </details>
        </>
      )}

      <details className="section">
        <summary><span className="chev">▶</span> app_info 版本历史</summary>
        <div className="section-body">
          <div className="table">
            <table>
              <thead><tr><th>#</th><th>Commit</th><th>取得时间</th><th>来源</th><th>操作人</th></tr></thead>
              <tbody>
                {(detail?.app_info_versions ?? []).map((version) => (
                  <tr key={version.id}>
                    <td>{version.id}</td>
                    <td className="mono small">{shortCommit(version.commit_id)}</td>
                    <td>{formatServerTime(version.fetched_at)}</td>
                    <td className="small break">{version.source}</td>
                    <td>{version.fetched_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </details>

      <details className="section" open>
        <summary><span className="chev">▶</span> 正式版</summary>
        <div className="section-body">
          {!(detail?.official_versions ?? []).length ? (
            <p className="muted small">尚无正式版。</p>
          ) : (
            <div className="table">
              <table>
                <thead><tr><th>版本周期</th><th>版本</th><th>交付镜像</th><th>Digest</th><th>发布时间</th><th /></tr></thead>
                <tbody>
                  {(detail?.official_versions ?? []).map((row) => (
                    <tr key={row.id} className={row.withdrawn ? "muted dim-55" : ""}>
                      <td>{row.release_name}</td>
                      <td>{row.version}</td>
                      <td className="mono small">{row.image}</td>
                      <td className="mono small">{row.digest}</td>
                      <td>{formatServerTime(row.released_at)}</td>
                      <td>{row.withdrawn ? "已撤销" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </details>
    </div>
  );
}
