/**
 * 周期管理 — RM only.
 *
 * Sub-panes:
 *   发布周期       — new monthly version, dates, environment, base images,
 *                    publish / unlock, cycle list
 *   app_info 同步  — per-App sync state of the daily app_info sync
 *   通知设置       — one DingTalk robot per notification kind
 *
 * Data: GET /api/state (shared selectedReleaseId), plus the sync and
 * notification endpoints for their sub-panes.
 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { stripDockerPullPrefix } from "../../lib/containerImage";
import { DateInput } from "../../components/DateInput";
import { RefreshBar } from "../../components/RefreshBar";
import { apiGet, apiPost } from "../../api/http";
import { useUiStore } from "../../store/uiStore";
import { formatDateValue, formatServerTime } from "../../lib/time";
import { releasePhaseLabel } from "../../lib/phase";
import { cicdStatusLabel } from "../../lib/domainMetadata";
import type { BaseImage, EnvironmentRow, ReleaseDetail, ReleaseSummary, StatePayload } from "../../types";
import { toast } from "../../lib/toast";
import { confirmDialog, promptDialog } from "../../lib/confirm";

const STATE_KEY = (releaseId?: string) => (releaseId ? ["state", releaseId] : ["state"]);

async function fetchState(releaseId?: string): Promise<StatePayload> {
  const qs = releaseId ? `?release_id=${encodeURIComponent(releaseId)}` : "";
  return apiGet<StatePayload>(`/api/state${qs}`);
}

interface CycleDates {
  signup_deadline: string;
  qa_deadline: string;
  release_date: string;
}

/** The month a new version is usually created for: next month after the 1st. */
function defaultMonth(): string {
  const now = new Date();
  const target = now.getDate() > 1 ? new Date(now.getFullYear(), now.getMonth() + 1, 1) : now;
  return `${target.getFullYear()}-${String(target.getMonth() + 1).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Cycle list
// ---------------------------------------------------------------------------

function ReleasesTable({ releases }: { releases: ReleaseSummary[] }) {
  if (!releases.length) return <p className="muted small">暂无版本。</p>;
  return (
    <div className="table" data-testid="releases-table">
      <table>
        <thead>
          <tr><th>版本</th><th>阶段</th><th>报名截止</th><th>QA 截止</th><th>发布日</th><th>报名</th><th>发布</th></tr>
        </thead>
        <tbody>
          {[...releases].reverse().map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td>
              <td>{releasePhaseLabel(r.phase)}</td>
              <td>{formatDateValue(r.signup_deadline) || "—"}</td>
              <td>{formatDateValue(r.qa_deadline) || "—"}</td>
              <td>{r.release_date || "—"}</td>
              <td>{r.entry_count}</td>
              <td>{r.released_locked ? `已发布 ${r.released_count} 个（${formatServerTime(r.released_locked_at)}）` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// New cycle
// ---------------------------------------------------------------------------

function NewCyclePanel({ onCreated }: { onCreated: (id: string) => void }) {
  const [month, setMonth] = useState(defaultMonth());
  const [name, setName] = useState("");
  const [dates, setDates] = useState<CycleDates>({ signup_deadline: "", qa_deadline: "", release_date: "" });
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!/^\d{4}-\d{2}$/.test(month)) return;
    apiGet<CycleDates>(`/api/releases/defaults?month=${month}`)
      .then((d) => setDates({
        signup_deadline: formatDateValue(d.signup_deadline),
        qa_deadline: formatDateValue(d.qa_deadline),
        release_date: d.release_date,
      }))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [month]);

  async function handleCreate() {
    if (!name.trim()) return setErr("请填写版本名");
    setErr("");
    setCreating(true);
    try {
      const r = await apiPost<{ release_id: string }>("/api/releases/create", { name: name.trim(), ...dates });
      setName("");
      onCreated(r.release_id);
      toast.success(`版本「${name.trim()}」已创建`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-head"><h2>新建版本</h2></div>
      <div className="panel-body">
        <div className="form form-compact">
          <label>版本名 <span className="required" />
            <input className="input" value={name} onChange={(e) => setName(e.target.value)}
              placeholder="手工填写，例如 2026.12" data-testid="new-release-name" />
          </label>
          <label>月份
            <input className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value)}
              data-testid="new-release-month" />
          </label>
          <label>报名截止
            <DateInput value={dates.signup_deadline} onChange={(v) => setDates((d) => ({ ...d, signup_deadline: v }))}
              testId="new-signup-deadline" ariaLabel="报名截止" />
          </label>
          <label>QA 截止
            <DateInput value={dates.qa_deadline} onChange={(v) => setDates((d) => ({ ...d, qa_deadline: v }))}
              testId="new-qa-deadline" ariaLabel="QA 截止" />
          </label>
          <label>发布日
            <DateInput value={dates.release_date} onChange={(v) => setDates((d) => ({ ...d, release_date: v }))}
              testId="new-release-date" ariaLabel="发布日" />
          </label>
        </div>
        <div className="row mt-13">
          <button className="btn primary" onClick={() => void handleCreate()} disabled={creating} data-testid="create-release-btn">
            {creating ? "创建中…" : "新建版本"}
          </button>
          {err && <span className="action-err" role="alert" data-testid="create-err">{err}</span>}
        </div>
        <p className="hint">
          新版本初始为空，Owner 报名才产生条目。默认报名截止为当月 1 日、QA 截止为当月最后一天减 5、发布日为当月最后一天，
          均可修改。上个版本的当月环境会复制过来作为起点。
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Current cycle settings
// ---------------------------------------------------------------------------

function CycleSettingsPanel({ release, onMutated }: { release: ReleaseDetail; onMutated: () => void }) {
  const [name, setName] = useState(release.name);
  const [dates, setDates] = useState<CycleDates>({
    signup_deadline: formatDateValue(release.signup_deadline),
    qa_deadline: formatDateValue(release.qa_deadline),
    release_date: release.release_date,
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const locked = release.released_locked;

  useEffect(() => {
    setName(release.name);
    setDates({
      signup_deadline: formatDateValue(release.signup_deadline),
      qa_deadline: formatDateValue(release.qa_deadline),
      release_date: release.release_date,
    });
    setErr("");
  }, [release.id, release.name, release.signup_deadline, release.qa_deadline, release.release_date]);

  async function handleSave() {
    setSaving(true);
    setErr("");
    try {
      await apiPost("/api/releases/update", { release_id: release.id, name: name.trim(), ...dates });
      toast.success("版本设置已更新");
      onMutated();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-head"><h2>当前版本设置</h2><span className="pill">{releasePhaseLabel(release.phase)}</span></div>
      <div className="panel-body">
        <div className="form form-compact">
          <label>版本名
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} disabled={locked}
              data-testid="edit-release-name" />
          </label>
          <label>报名截止
            <DateInput value={dates.signup_deadline} onChange={(v) => setDates((d) => ({ ...d, signup_deadline: v }))}
              testId="edit-signup-deadline" ariaLabel="当前版本报名截止" />
          </label>
          <label>QA 截止
            <DateInput value={dates.qa_deadline} onChange={(v) => setDates((d) => ({ ...d, qa_deadline: v }))}
              testId="edit-qa-deadline" ariaLabel="当前版本 QA 截止" />
          </label>
          <label>发布日
            <DateInput value={dates.release_date} onChange={(v) => setDates((d) => ({ ...d, release_date: v }))}
              testId="edit-release-date" ariaLabel="当前版本发布日" />
          </label>
        </div>
        <div className="row mt-13">
          <button className="btn" onClick={() => void handleSave()} disabled={saving || locked} data-testid="save-deadlines-btn">
            {saving ? "保存中…" : "保存版本设置"}
          </button>
          <button className="btn primary" data-testid="export-csv-btn"
            onClick={() => window.open(`/api/test-scope.csv?release_id=${encodeURIComponent(release.id)}`, "_blank")}>
            导出测试范围 CSV
          </button>
          {err && <span className="action-err" role="alert">{err}</span>}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------

function EnvironmentPanel({ release, onMutated }: { release: ReleaseDetail; onMutated: () => void }) {
  const editable = release.phase === "signup";
  const [rows, setRows] = useState<EnvironmentRow[]>(release.environment);
  const [saving, setSaving] = useState(false);

  useEffect(() => setRows(release.environment), [release.id, release.environment]);

  function patchRow(index: number, key: keyof EnvironmentRow, value: string) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  }

  async function handleSave() {
    setSaving(true);
    try {
      await apiPost("/api/releases/environment", { release_id: release.id, rows });
      toast.success("当月环境已保存");
      onMutated();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-head"><h2>当月环境</h2></div>
      <div className="panel-body">
        {!editable && <p className="hint mt-0">报名截止后环境冻结，只读。</p>}
        <div className="table" data-testid="environment-table">
          <table>
            <thead><tr><th>组件</th><th>版本</th><th>说明</th>{editable && <th />}</tr></thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={editable ? 4 : 3} className="muted">尚未填写</td></tr>
              )}
              {rows.map((row, i) => (
                <tr key={i}>
                  <td><input className="input sm" value={row.component} disabled={!editable}
                    onChange={(e) => patchRow(i, "component", e.target.value)} aria-label="组件" /></td>
                  <td><input className="input sm" value={row.version} disabled={!editable}
                    onChange={(e) => patchRow(i, "version", e.target.value)} aria-label="版本" /></td>
                  <td><input className="input sm" value={row.note} disabled={!editable}
                    onChange={(e) => patchRow(i, "note", e.target.value)} aria-label="说明" /></td>
                  {editable && (
                    <td><button className="btn ghost sm" onClick={() => setRows((r) => r.filter((_, j) => j !== i))}>删除</button></td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {editable && (
          <div className="row mt-11">
            <button className="btn sm" onClick={() => setRows((r) => [...r, { component: "", version: "", note: "" }])}>
              + 添加组件
            </button>
            <button className="btn primary sm" onClick={() => void handleSave()} disabled={saving} data-testid="save-environment-btn">
              保存当月环境
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Base images
// ---------------------------------------------------------------------------

const EMPTY_IMAGE = { reference: "", note: "" };

function baseImageReference(image: BaseImage): string {
  const address = image.image || image.name;
  return image.digest && !address.includes("@") ? `${address}@${image.digest}` : address;
}

function BaseImagesPanel({ release, onMutated }: { release: ReleaseDetail; onMutated: () => void }) {
  const editable = !release.released_locked;
  const [draft, setDraft] = useState<{ id?: number; reference: string; note: string }>(EMPTY_IMAGE);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setSaving(true);
    setError("");
    try {
      await apiPost("/api/releases/base-image", {
        release_id: release.id, image: { ...draft, reference: stripDockerPullPrefix(draft.reference).trim() },
      });
      setDraft(EMPTY_IMAGE);
      toast.success("基础镜像已保存");
      onMutated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function remove(image: BaseImage) {
    if (!(await confirmDialog({ title: "删除基础镜像", body: `删除「${image.name}」？`, danger: true, confirmText: "删除" }))) return;
    try {
      await apiPost("/api/releases/base-image/delete", { release_id: release.id, image_id: image.id });
      if (draft.id === image.id) { setDraft(EMPTY_IMAGE); setError(""); }
      onMutated();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="panel">
      <div className="panel-head"><h2>基础镜像</h2></div>
      <div className="panel-body">
        <p className="hint mt-0">每行填写一个完整镜像引用和说明，保存时自动解析名称、镜像地址和 digest。</p>
        <div className="table base-images-table" data-testid="base-images-table">
          <table>
            <thead><tr><th>完整镜像引用</th><th>说明</th>{editable && <th />}</tr></thead>
            <tbody>
              {release.base_images.map((image) => (
                <tr key={image.id}>
                  <td className="mono small break">{baseImageReference(image)}</td>
                  <td>{image.note}</td>
                  {editable && (
                    <td className="nowrap">
                      <button className="btn ghost sm" disabled={saving} onClick={() => {
                        setDraft({ id: image.id, reference: baseImageReference(image), note: image.note }); setError("");
                      }}>编辑</button>
                      <button className="btn ghost sm" disabled={saving} onClick={() => void remove(image)}>删除</button>
                    </td>
                  )}
                </tr>
              ))}
              {editable && (
                <tr>
                  <td><input className="input sm mono" placeholder="harbor.sh.mxcr.io/hpc-release/amgx-maca@sha256:…" value={draft.reference}
                    disabled={saving} onChange={(e) => setDraft((d) => ({ ...d, reference: stripDockerPullPrefix(e.target.value) }))} aria-label="完整镜像引用" /></td>
                  <td><input className="input sm" placeholder="说明" value={draft.note} disabled={saving}
                    onChange={(e) => setDraft((d) => ({ ...d, note: e.target.value }))} aria-label="说明" /></td>
                  <td className="nowrap">
                    <button className="btn primary sm" disabled={saving || !draft.reference.trim()}
                      onClick={() => void save()} data-testid="save-base-image-btn">
                      {draft.id ? "保存" : "添加"}
                    </button>
                    {(draft.id || draft.reference || draft.note) && <button className="btn ghost sm" disabled={saving}
                      onClick={() => { setDraft(EMPTY_IMAGE); setError(""); }}>取消</button>}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {error && <div className="banner bad mt-10" role="alert">{error}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Publish / unlock
// ---------------------------------------------------------------------------

function PublishPanel({ release, releases, onMutated }: {
  release: ReleaseDetail;
  releases: ReleaseSummary[];
  onMutated: () => void;
}) {
  const active = release.entries.filter((e) => e.status === "active");
  const releasable = active.filter((e) => ["qa_passed", "has_issues"].includes(e.qa_status));
  const pendingScope = release.scope_requests.filter((r) => r.status === "pending").length;

  async function handlePublish() {
    if (!(await confirmDialog({
      title: `发布「${release.name}」`,
      body: `将发布 ${releasable.length} 个 App（QA 通过 / 存在问题），${active.length - releasable.length} 个记为本期发布失败并保持原正式版。`
        + (pendingScope ? `\n还有 ${pendingScope} 条范围扩大申请未处理，发布后将自动关闭。` : "")
        + "\n发布后写入正式版、生成 final 文档并锁定。",
      danger: true,
      confirmText: "发布",
    }))) return;
    try {
      const r = await apiPost<{ released: string[]; failed: string[] }>("/api/releases/publish", { release_id: release.id });
      toast.success(`已发布：${r.released.length} 个，发布失败 ${r.failed.length} 个`);
      onMutated();
    } catch (e) {
      toast.error(`发布失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function handleUnlock() {
    const reason = await promptDialog({
      title: `解锁「${release.name}」`,
      body: "解锁后本期的发布结果和正式版记录会撤销，final 文档被清除，更正后需重新发布。请填写原因（记入审计）。",
      confirmText: "解锁",
    });
    if (reason === null) return;
    try {
      await apiPost("/api/releases/unlock", { release_id: release.id, reason });
      toast.success("已解锁");
      onMutated();
    } catch (e) {
      toast.error(`解锁失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div className="panel">
      <div className="panel-head"><h2>发布</h2></div>
      <div className="panel-body">
        <p className="hint mt-0">
          QA 截止后由 RM 核对并发布：QA 通过 / 存在问题的报名记为已发布并写入正式版（存在问题的备注进已知问题）；
          未测试 / 不可发布的记为本期发布失败，App 保持原正式版。
        </p>
        <div className="row mt-11">
          <span className="small">本期报名 {active.length} 个，可发布 {releasable.length} 个</span>
          <span className="spacer" />
          {!release.released_locked ? (
            <button className="btn danger" onClick={() => void handlePublish()} disabled={release.phase !== "qa_closed"}
              title={release.phase !== "qa_closed" ? "QA 截止后才能发布" : undefined} data-testid="final-lock-btn">
              发布并锁定
            </button>
          ) : (
            <button className="btn warn" onClick={() => void handleUnlock()} data-testid="final-unlock-btn">
              解锁以更正
            </button>
          )}
        </div>
        <ReleasesTable releases={releases} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// app_info sync status
// ---------------------------------------------------------------------------

interface SyncStatusResponse {
  apps: Array<{
    app_id: string;
    name: string;
    cicd_status: string;
    git_url: string;
    git_branch: string;
    head_commit: string;
    checked_at: string;
    error: string;
  }>;
  job: { running: boolean; started_at: string; started_by: string; finished_at: string;
    summary: { checked?: number; updated?: number; failed?: number; error?: string } | null };
  daily: { time: string; enabled: boolean; last_run: string };
}

function SyncStatusPane() {
  const [onlyErrors, setOnlyErrors] = useState(true);
  const { data, refetch, isFetching } = useQuery({
    queryKey: ["app-info-sync-status"],
    queryFn: () => apiGet<SyncStatusResponse>("/api/app-info/sync-status"),
    refetchInterval: (query) => (query.state.data?.job.running ? 3000 : false),
  });

  async function syncAll() {
    try {
      await apiPost("/api/app-info/sync-all", {});
      void refetch();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  }

  async function syncOne(appId: string) {
    try {
      const r = await apiPost<{ status: string; error?: string }>("/api/app-info/sync", { app_id: appId, force: true });
      if (r.status === "error") toast.error(`同步失败：${r.error ?? ""}`);
      else toast.success(r.status === "updated" ? "已更新" : "没有变化");
      void refetch();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  }

  const rows = (data?.apps ?? []).filter((row) => !onlyErrors || row.error);
  const errorCount = (data?.apps ?? []).filter((row) => row.error).length;
  const job = data?.job;
  return (
    <div className="panel" data-testid="sync-status-pane">
      <div className="panel-head"><h2>app_info 同步状态</h2></div>
      <div className="panel-body">
        <p className="hint mt-0">
          每天 {data?.daily.time ?? "02:00"} 自动对 CICD 运行中的 App 检查分支头，变化时取回 app_info.json
          {data?.daily.last_run ? `（上次：${formatServerTime(data.daily.last_run)}）` : ""}。失败汇总会发到「app_info 同步失败汇总」通知。
        </p>
        <div className="row mt-11">
          <label className="check"><input type="checkbox" checked={onlyErrors} onChange={(e) => setOnlyErrors(e.target.checked)} />
            只看失败（{errorCount}）</label>
          <span className="spacer" />
          {job?.running && <span className="small">同步中（{job.started_by} · {formatServerTime(job.started_at)} 开始）…</span>}
          {!job?.running && job?.summary && (
            <span className="small muted">
              上次手动同步：检查 {job.summary.checked ?? 0}，更新 {job.summary.updated ?? 0}，失败 {job.summary.failed ?? 0}
              {job.summary.error ? `，错误：${job.summary.error}` : ""}
            </span>
          )}
          <button className="btn sm" onClick={() => void refetch()} disabled={isFetching}>刷新</button>
          <button className="btn primary sm" onClick={() => void syncAll()} disabled={job?.running}>立即同步全部</button>
        </div>
        <div className="table mt-11">
          <table>
            <thead><tr><th>App</th><th>CICD</th><th>Gerrit</th><th>分支头</th><th>最近检查</th><th>错误</th><th /></tr></thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={7} className="muted">{onlyErrors ? "没有同步失败的 App" : "无数据"}</td></tr>}
              {rows.map((row) => (
                <tr key={row.app_id}>
                  <td>{row.name}<div className="small muted">{row.app_id}</div></td>
                  <td>{cicdStatusLabel(row.cicd_status)}</td>
                  <td className="small break">{row.git_url}@{row.git_branch}</td>
                  <td className="mono small">{row.head_commit.slice(0, 12)}</td>
                  <td>{formatServerTime(row.checked_at) || "—"}</td>
                  <td className="small danger-text break">{row.error}</td>
                  <td><button className="btn ghost sm" onClick={() => void syncOne(row.app_id)}>同步</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Notification settings
// ---------------------------------------------------------------------------

interface ChannelSetting {
  kind: string;
  label: string;
  enabled: boolean;
  configured: boolean;
  access_token_mask: string;
  secret_mask: string;
  at_user_ids: string[];
  updated_at: string;
  updated_by: string;
}

function ChannelRow({ channel, onSaved }: { channel: ChannelSetting; onSaved: () => void }) {
  const [enabled, setEnabled] = useState(channel.enabled);
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [users, setUsers] = useState(channel.at_user_ids.join(", "));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setEnabled(channel.enabled);
    setUsers(channel.at_user_ids.join(", "));
    setToken("");
    setSecret("");
  }, [channel]);

  async function save() {
    setBusy(true);
    try {
      await apiPost("/api/notifications/channels/save", {
        kind: channel.kind,
        enabled,
        access_token: token.trim() ? token.trim() : null,
        secret: secret.trim() ? secret.trim() : null,
        at_user_ids: users.split(/[,，\s]+/).filter(Boolean),
      });
      toast.success(`「${channel.label}」已保存`);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    try {
      await apiPost("/api/notifications/channels/test", { kind: channel.kind });
      toast.success("测试消息已发送");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr data-testid={`channel-${channel.kind}`}>
      <td><b>{channel.label}</b><div className="small muted">{channel.kind}</div></td>
      <td><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} aria-label="启用" /></td>
      <td><input className="input sm" type="password" value={token} onChange={(e) => setToken(e.target.value)}
        placeholder={channel.access_token_mask || "未设置"} aria-label="ACCESS_TOKEN" autoComplete="off" /></td>
      <td><input className="input sm" type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
        placeholder={channel.secret_mask || "未设置"} aria-label="SECRET" autoComplete="off" /></td>
      <td><input className="input sm" value={users} onChange={(e) => setUsers(e.target.value)}
        placeholder="钉钉用户 ID，逗号分隔" aria-label="@ 用户" /></td>
      <td className="small muted">{channel.updated_at ? `${channel.updated_by} · ${formatServerTime(channel.updated_at)}` : "—"}</td>
      <td className="nowrap">
        <button className="btn primary sm" onClick={() => void save()} disabled={busy}>保存</button>
        <button className="btn ghost sm" onClick={() => void test()} disabled={busy || !channel.configured}>测试</button>
      </td>
    </tr>
  );
}

function NotificationSettingsPane() {
  const { data, refetch } = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => apiGet<{ channels: ChannelSetting[] }>("/api/notifications/channels"),
  });
  return (
    <div className="panel" data-testid="notification-settings-pane">
      <div className="panel-head"><h2>通知设置</h2></div>
      <div className="panel-body">
        <p className="hint mt-0">
          每种通知各用一个钉钉机器人。未配置或未启用的通知不发送。ACCESS_TOKEN 和 SECRET 保存后只显示掩码；留空表示不修改。
        </p>
        <div className="table">
          <table>
            <thead><tr><th>通知类型</th><th>启用</th><th>ACCESS_TOKEN</th><th>SECRET</th><th>@ 用户</th><th>最近修改</th><th /></tr></thead>
            <tbody>
              {(data?.channels ?? []).map((channel) => (
                <ChannelRow key={channel.kind} channel={channel} onSaved={() => void refetch()} />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type SubPane = "cycle" | "sync" | "notifications";

export function ReleaseCyclePage() {
  const queryClient = useQueryClient();
  const [subPane, setSubPane] = useState<SubPane>("cycle");
  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId) || undefined;
  const setSelectedReleaseId = useUiStore((s) => s.setSelectedReleaseId);

  const { data, isFetching, dataUpdatedAt, refetch, error } = useQuery({
    queryKey: STATE_KEY(selectedReleaseId),
    queryFn: () => fetchState(selectedReleaseId),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  useEffect(() => {
    if (data?.release?.id && !selectedReleaseId) {
      queryClient.setQueryData(STATE_KEY(data.release.id), data);
      setSelectedReleaseId(data.release.id);
    }
  }, [data, selectedReleaseId, setSelectedReleaseId, queryClient]);

  const releases = data?.releases ?? [];
  const release = data?.release ?? null;

  function handleMutated(newReleaseId?: string) {
    if (newReleaseId && newReleaseId !== selectedReleaseId) {
      setSelectedReleaseId(newReleaseId);
      void queryClient.invalidateQueries({ queryKey: STATE_KEY(newReleaseId) });
    } else {
      void refetch();
    }
  }

  if (error) {
    return (
      <section className="view active" data-testid="release-cycle-page">
        <p className="muted p-1r">加载失败：{(error as Error).message}</p>
      </section>
    );
  }

  return (
    <section className="view active" data-testid="release-cycle-page">
      <div className="page-toolbar">
        <h2>周期管理</h2>
        {releases.length > 0 && (
          <select className="input select-inline" value={selectedReleaseId ?? release?.id ?? ""}
            onChange={(e) => setSelectedReleaseId(e.target.value)} aria-label="选择版本">
            {releases.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        )}
        <span className="spacer" />
        <RefreshBar dataUpdatedAt={dataUpdatedAt} isFetching={isFetching} onRefresh={() => void refetch()} />
      </div>

      <div className="subtabs">
        <button className={`subtab${subPane === "cycle" ? " active" : ""}`} onClick={() => setSubPane("cycle")} data-testid="subtab-cycle">
          发布周期
        </button>
        <button className={`subtab${subPane === "sync" ? " active" : ""}`} onClick={() => setSubPane("sync")} data-testid="subtab-sync">
          app_info 同步状态
        </button>
        <button className={`subtab${subPane === "notifications" ? " active" : ""}`} onClick={() => setSubPane("notifications")}
          data-testid="subtab-notifications">
          通知设置
        </button>
      </div>

      {subPane === "cycle" && (
        isFetching && !data ? (
          <div className="muted p-1r">加载中…</div>
        ) : (
          <div data-testid="release-cycle-pane">
            <NewCyclePanel onCreated={(id) => handleMutated(id)} />
            {release && (
              <>
                <CycleSettingsPanel release={release} onMutated={() => handleMutated()} />
                <EnvironmentPanel release={release} onMutated={() => handleMutated()} />
                <BaseImagesPanel key={release.id} release={release} onMutated={() => handleMutated()} />
                <PublishPanel release={release} releases={releases} onMutated={() => handleMutated()} />
              </>
            )}
          </div>
        )
      )}
      {subPane === "sync" && <SyncStatusPane />}
      {subPane === "notifications" && <NotificationSettingsPane />}
    </section>
  );
}
