import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet, apiPost } from "../../api/http";
import { signupOpen } from "../../lib/phase";
import { toast } from "../../lib/toast";
import type { App, Entry, ReleaseDetail, ReleaseSummary, StatePayload } from "../../types";
import { appEntry } from "./helpers";

function signupBlocker(app: App, release: ReleaseDetail): string {
  if (!signupOpen(release)) return "报名已截止";
  if (app.cicd_status !== "running") return "CICD 已停止";
  if (app.open_status_request?.target_status === "stopped") return "有未处理完的停止 CICD 申请";
  if (appEntry(release, app.id)?.status === "active") return "已报名该版本";
  if (!app.latest_app_info?.app_version.trim()) return "请先同步包含 app_version 的 app_info";
  return "";
}

interface SignupDialogProps {
  appId?: string;
  releases: ReleaseSummary[];
  initialReleaseId?: string;
  onClose: () => void;
  onSignedUp: (releaseId: string) => void;
}

export function SignupDialog({ appId, releases, initialReleaseId, onClose, onSignedUp }: SignupDialogProps) {
  const { user } = useAuth();
  const [releaseId, setReleaseId] = useState(() => {
    const current = releases.find((release) => release.id === initialReleaseId && signupOpen(release));
    return current?.id ?? [...releases].reverse().find(signupOpen)?.id ?? "";
  });
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const selectRef = useRef<HTMLSelectElement>(null);
  const { data, error, isFetching, refetch } = useQuery({
    queryKey: ["app-signup", user?.username, user?.role, releaseId],
    queryFn: () => apiGet<StatePayload>(`/api/state?release_id=${encodeURIComponent(releaseId)}&include_allowed_actions=1`),
    enabled: !!releaseId,
    staleTime: 0,
    refetchOnMount: "always",
  });
  const release = data?.release?.id === releaseId ? data.release : null;
  const apps = (data?.apps ?? []).filter((app) => appId
    ? app.id === appId
    : app.owners.includes(user?.username ?? "") && app.cicd_status === "running");
  const visibleApps = apps.filter((app) => app.name.toLowerCase().includes(search.trim().toLowerCase()));
  const eligible = release ? apps.filter((app) => !signupBlocker(app, release)) : [];
  const ids = appId ? [appId] : selected;
  const ready = !!release && !error && !isFetching && ids.length > 0
    && ids.every((id) => eligible.some((app) => app.id === id));

  useEffect(() => {
    const previousFocus = document.activeElement;
    selectRef.current?.focus();
    return () => { if (previousFocus instanceof HTMLElement) previousFocus.focus(); };
  }, []);

  useEffect(() => {
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && !submitting) onClose();
    }
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose, submitting]);

  async function submit() {
    if (!ready || submitting) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      if (appId) {
        await apiPost<{ entry: Entry }>("/api/entries/signup", {
          release_id: releaseId, app_id: appId, target_version: apps[0].latest_app_info!.app_version,
          base_image_id: null, planned_features: "", excluded_tests: [],
        });
      } else {
        await apiPost<{ entries: Entry[] }>("/api/entries/signup-batch", { release_id: releaseId, app_ids: ids });
      }
      toast.success(`已报名「${release!.name}」${appId ? "" : `，共 ${ids.length} 个 App`}`);
      onSignedUp(releaseId);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="dialog-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !submitting) onClose();
    }}>
      <div className="dialog-card signup-dialog" role="dialog" aria-modal="true" aria-labelledby="signup-dialog-title" data-testid="signup-dialog">
        <div className="dialog-head"><h3 id="signup-dialog-title">{appId ? "报名" : "批量报名"}</h3></div>
        <div className="dialog-body signup-dialog-body">
          <label>报名版本
            <select ref={selectRef} className="select" value={releaseId} disabled={submitting}
              onChange={(event) => { setReleaseId(event.target.value); setSelected([]); setSubmitError(""); }}>
              <option value="">请选择版本</option>
              {releases.map((item) => <option key={item.id} value={item.id} disabled={!signupOpen(item)}>
                {item.name}{signupOpen(item) ? "" : "（报名已截止）"}
              </option>)}
            </select>
          </label>
          {!releases.some(signupOpen) && <p className="muted">暂无可报名的版本，等待 RM 新建或调整报名截止时间。</p>}
          {isFetching && <p className="muted" role="status">加载版本环境和 App…</p>}
          {error && <div className="banner bad" role="alert">
            加载失败：{error instanceof Error ? error.message : String(error)}
            <button className="btn sm" onClick={() => void refetch()}>重试</button>
          </div>}
          {release && !isFetching && !error && (
            <>
              <h4 className="mt-12">组件环境（Driver / SDK / PyTorch）</h4>
              <div className="table" data-testid="signup-environment">
                <table>
                  <thead><tr><th>组件</th><th>版本</th><th>说明</th></tr></thead>
                  <tbody>
                    {release.environment.length ? release.environment.map((row, index) => (
                      <tr key={row.id ?? index}><td>{row.component}</td><td>{row.version || "未设置"}</td><td>{row.note || "—"}</td></tr>
                    )) : <tr><td colSpan={3} className="muted">RM 尚未填写该版本组件信息。</td></tr>}
                  </tbody>
                </table>
              </div>
              <p className="small muted">目标版本取 App 最新 app_info 的版本；默认纳入全部启用的测试项。</p>
              {appId ? (
                apps.length ? <div data-testid="signup-app-summary">
                  <b>{apps[0].name}</b>
                  {signupBlocker(apps[0], release) && <p className="warnp">{signupBlocker(apps[0], release)}</p>}
                </div> : <p className="warnp">该 App 已不存在，请刷新列表。</p>
              ) : (
                <>
                  <div className="row gap-8 wrap signup-app-toolbar">
                    <input className="input" aria-label="搜索报名 App" placeholder="搜索我的 App…" value={search}
                      disabled={submitting} onChange={(event) => setSearch(event.target.value)} />
                    <button className="btn sm nowrap" disabled={submitting || eligible.length === 0}
                      onClick={() => setSelected(eligible.map((app) => app.id))}>全选可报名 App</button>
                    <span className="small nowrap">已选 {selected.length} 个</span>
                  </div>
                  <div className="table signup-app-table mt-10">
                    <table>
                      <thead><tr><th>选择</th><th>我的运行中 App</th><th>目标版本</th><th>报名状态</th></tr></thead>
                      <tbody>
                        {visibleApps.map((app) => {
                          const blocker = signupBlocker(app, release);
                          return <tr key={app.id}>
                            <td><input type="checkbox" aria-label={`报名 ${app.name}`} checked={selected.includes(app.id)}
                              disabled={submitting || !!blocker} onChange={(event) => setSelected((current) => event.target.checked
                                ? [...current, app.id] : current.filter((id) => id !== app.id))} /></td>
                            <td>{app.name}</td><td>{app.latest_app_info?.app_version || "—"}</td><td>{blocker || "可报名"}</td>
                          </tr>;
                        })}
                        {!visibleApps.length && <tr><td colSpan={4} className="muted">{search ? "没有匹配的 App" : "你名下暂无 CICD 运行中的 App"}</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </>
          )}
          {submitError && <div className="banner bad mt-10" role="alert">{submitError}</div>}
        </div>
        <div className="dialog-actions">
          <button className="btn" disabled={submitting} onClick={onClose}>取消</button>
          <button className="btn primary" disabled={!ready || submitting} onClick={() => void submit()} data-testid="signup-confirm">
            {submitting ? "报名中…" : "确认"}
          </button>
        </div>
      </div>
    </div>
  );
}
