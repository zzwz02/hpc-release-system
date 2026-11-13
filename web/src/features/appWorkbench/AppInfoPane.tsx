/**
 * app_info — the latest app_info.json from the App's repository (synced
 * automatically every day), the stored version history and the App's
 * official (released) versions.
 */
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../api/http";
import { formatServerTime } from "../../lib/time";
import type { App, DeliveryImage } from "../../types";
import { DeliveryImages } from "../../components/DeliveryImages";
import { recordedImages } from "../../lib/deliveryImages";
import { orderChips, shortCommit } from "./helpers";
import { BasicInfoSection } from "./BasicInfoSection";
import { TestScopeTable } from "../../components/TestScopeTable";

interface AppDetailResponse {
  app_info_versions: Array<{
    id: number;
    commit_id: string;
    source: string;
    fetched_at: string;
    fetched_by: string;
  }>;
  official_versions: Array<{
    id: number | null;
    entry_id: number | null;
    release_name: string;
    version: string;
    image: string;
    digest: string;
    images?: DeliveryImage[];
    released_at: string;
    withdrawn: number;
  }>;
}

interface AppInfoPaneProps {
  app: App;
  canEdit: boolean;
  canEditRmFields: boolean;
  onSaved: () => void;
  onDirtyChange: (dirty: boolean) => void;
  onSync?: () => void;
  syncing: boolean;
  syncDisabled: boolean;
}

export function AppInfoPane({ app, canEdit, canEditRmFields, onSaved, onDirtyChange, onSync, syncing, syncDisabled }: AppInfoPaneProps) {
  const info = app.latest_app_info;
  const { data: detail, error, isPending } = useQuery({
    queryKey: ["app-detail", app.id, info?.id ?? 0],
    queryFn: () => apiGet<AppDetailResponse>(`/api/apps/detail?app_id=${encodeURIComponent(app.id)}`),
    staleTime: 0,
    refetchOnMount: true,
  });

  return (
    <div className="detail-body" data-testid="app-info-pane">
      <BasicInfoSection app={app} canEdit={canEdit} canEditRmFields={canEditRmFields}
        onSaved={onSaved} onDirtyChange={onDirtyChange} />
      <details className="section" open data-testid="latest-app-info-section">
        <summary>
          <span className="chev">▶</span> 最新 app_info
          {onSync && (
            <button type="button" className="btn sm" disabled={syncing || syncDisabled}
              title={syncDisabled ? "请先保存或取消修改，再同步 app_info" : "立即同步app_info"}
              data-testid="app-info-sync"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onSync();
              }}>
              {syncing ? "同步中…" : "立即同步app_info"}
            </button>
          )}
        </summary>
        <div className="section-body">
          {!info ? (
            <div className="banner warnp">尚未取得 app_info。</div>
          ) : (
            <>
              <div className="form">
                <label>版本<input className="input" value={info.app_version} disabled /></label>
                <label>Commit<input className="input mono" value={shortCommit(info.commit_id)} disabled /></label>
                <label>取得时间<input className="input" value={formatServerTime(info.fetched_at)} disabled /></label>
                <label>X86 芯片<input className="input" value={orderChips(info.x86_chips)} disabled /></label>
                <label>ARM 芯片<input className="input" value={orderChips(info.arm_chips)} disabled /></label>
                <label>OS<input className="input" value={info.build_os.join(",")} disabled /></label>
                <label>CPU 架构<input className="input" value={info.build_arches.join(",")} disabled /></label>
                <label>Python label<input className="input" value={info.python_labels.join(",")} disabled /></label>
                <label>PyTorch label<input className="input" value={info.pytorch_labels.join(",")} disabled /></label>
                <label>依赖组件（app_dependency）
                  <input className="input" disabled
                    value={info.dependencies === null ? "未声明" : info.dependencies.join(", ") || "（空）"} />
                </label>
              </div>
              <TestScopeTable tests={info.tests} />
            </>
          )}
        </div>
      </details>

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

      <details className="section" open data-testid="official-version-history">
        <summary><span className="chev">▶</span> 正式版发布历史</summary>
        <div className="section-body">
          {isPending ? <p className="muted small" role="status">正在加载发布历史…</p>
            : error ? <p className="lerr" role="alert">发布历史加载失败：{error.message}</p>
            : !(detail?.official_versions ?? []).length ? (
            <p className="muted small">尚无正式版。</p>
          ) : (
            <div data-testid="official-version-table">
              {(detail?.official_versions ?? []).map((row) => (
                <div key={row.id ?? `entry-${row.entry_id}`} data-testid="official-version-row"
                  className={`official-history-version${row.withdrawn ? " muted dim-55" : ""}`}>
                  <div className="row2 mb-8">
                    <b data-testid="official-release-name">{row.release_name}</b>
                    <span>版本 <span data-testid="official-app-version">{row.version}</span></span>
                    <span className="small muted">{formatServerTime(row.released_at)}</span>
                    <span className="pill" data-testid="official-release-status">{row.withdrawn ? "已撤销" : "已发布"}</span>
                  </div>
                  <DeliveryImages images={recordedImages(row.images, row.image, row.digest)} />
                </div>
              ))}
            </div>
          )}
        </div>
      </details>
    </div>
  );
}
