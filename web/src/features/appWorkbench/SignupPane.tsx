/**
 * 本期报名 — an App's signup for the selected cycle.
 *
 * Before the signup deadline the Owner signs up (base image, target
 * version, planned features, test scope) and may change anything.  After
 * the deadline the scope can only shrink; until publication the Owner fills
 * in the actual delivery and known issues.
 */
import React from "react";
import { apiPost } from "../../api/http";
import { confirmDialog } from "../../lib/confirm";
import { toast } from "../../lib/toast";
import { releasePhaseLabel } from "../../lib/phase";
import type { App, AppInfoTest, Entry, ReleaseDetail } from "../../types";
import { orderChips, shortCommit } from "./helpers";
import { QaPill, ReadField } from "./pills";

interface SignupPaneProps {
  app: App;
  entry: Entry | null;
  release: ReleaseDetail;
  canEdit: boolean;
  onSaved: () => void;
  onDirtyChange: (dirty: boolean) => void;
}

export function EnvironmentSection({ release, open }: { release: ReleaseDetail; open?: boolean }) {
  return (
    <details className="section" open={open} data-testid="cycle-environment">
      <summary><span className="chev">▶</span> 当月环境与基础镜像（{release.name}）</summary>
      <div className="section-body">
        {release.environment.length === 0 ? (
          <p className="muted small">RM 尚未填写当月环境。</p>
        ) : (
          <div className="table">
            <table>
              <thead><tr><th>组件</th><th>版本</th><th>说明</th></tr></thead>
              <tbody>
                {release.environment.map((row, i) => (
                  <tr key={i}><td>{row.component}</td><td>{row.version}</td><td>{row.note}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="table mt-10">
          <table>
            <thead><tr><th>基础镜像</th><th>镜像地址</th><th>Digest</th><th>说明</th></tr></thead>
            <tbody>
              {release.base_images.length === 0 ? (
                <tr><td colSpan={4} className="muted">RM 尚未发布基础镜像</td></tr>
              ) : release.base_images.map((image) => (
                <tr key={image.id}>
                  <td>{image.name}</td>
                  <td className="mono small">{image.image}</td>
                  <td className="mono small">{image.digest}</td>
                  <td>{image.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </details>
  );
}

function ScopeChecklist({
  tests,
  excluded,
  canExclude,
  canInclude,
  onChange,
}: {
  tests: AppInfoTest[];
  excluded: string[];
  canExclude: boolean;
  canInclude: boolean;
  onChange: (excluded: string[]) => void;
}) {
  if (!tests.length) return <p className="muted small">app_info 中没有启用的测试项。</p>;
  return (
    <div className="table" data-testid="scope-tests">
      <table>
        <thead><tr><th className="w-48">纳入</th><th>测试项</th><th>芯片</th><th>命令</th></tr></thead>
        <tbody>
          {tests.map((test) => {
            const isIn = !excluded.includes(test.path);
            const disabled = isIn ? !canExclude : !canInclude;
            return (
              <tr key={test.path} className={isIn ? "" : "muted dim-55"}>
                <td>
                  <input
                    type="checkbox"
                    checked={isIn}
                    disabled={disabled}
                    aria-label={`纳入 ${test.path}`}
                    onChange={(e) =>
                      onChange(e.target.checked ? excluded.filter((p) => p !== test.path) : [...excluded, test.path])
                    }
                  />
                </td>
                <td className="mono small">{test.path}</td>
                <td className="small">{orderChips(test.supported_chips)}</td>
                <td className="mono small break">{test.command}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface FormState {
  base_image_id: string;
  target_version: string;
  planned_features: string;
  excluded_tests: string[];
  actual_delivery: string;
  known_issues: string;
}

function formFromEntry(entry: Entry | null, app: App): FormState {
  if (entry && entry.status === "active") {
    return {
      base_image_id: entry.base_image_id ? String(entry.base_image_id) : "",
      target_version: entry.target_version,
      planned_features: entry.planned_features,
      excluded_tests: [...entry.excluded_tests],
      actual_delivery: entry.actual_delivery,
      known_issues: entry.known_issues,
    };
  }
  return {
    base_image_id: "",
    target_version: app.latest_app_info?.app_version ?? "",
    planned_features: "",
    excluded_tests: [],
    actual_delivery: "",
    known_issues: "",
  };
}

export function SignupPane({ app, entry, release, canEdit, onSaved, onDirtyChange }: SignupPaneProps) {
  const active = entry?.status === "active" ? entry : null;
  const phase = release.phase;
  const locked = release.released_locked;
  const signupPhase = phase === "signup";
  const [editMode, setEditMode] = React.useState(false);
  const [form, setForm] = React.useState<FormState>(() => formFromEntry(entry, app));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    setForm(formFromEntry(entry, app));
    setEditMode(false);
    setError("");
    onDirtyChange(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entry?.id, entry?.updated_at, entry?.status, app.id, release.id]);

  function patch<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    onDirtyChange(true);
  }

  const signupBlocker = !canEdit
    ? "只有该 App 的 Owner 或 RM 可以报名"
    : locked
      ? "版本已发布"
      : !signupPhase
        ? `报名已截止（当前阶段：${releasePhaseLabel(phase)}）`
        : app.cicd_status !== "running"
          ? "该 App 的 CICD 已停止，需先在 CICD tab 申请启动并生效后才能报名"
          : app.open_status_request?.target_status === "stopped"
            ? `该 App 有未处理完的停止 CICD 申请 #${app.open_status_request.id}，不能报名`
            : !app.latest_app_info
              ? "该 App 还没有 app_info，请先在 app_info tab 同步"
              : "";

  async function handleSignup() {
    setSaving(true);
    setError("");
    try {
      await apiPost("/api/entries/signup", {
        release_id: release.id,
        app_id: app.id,
        base_image_id: form.base_image_id ? Number(form.base_image_id) : null,
        target_version: form.target_version,
        planned_features: form.planned_features,
        excluded_tests: form.excluded_tests,
      });
      onDirtyChange(false);
      toast.success(`已报名「${release.name}」`);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleSave() {
    if (!active) return;
    const changes: Record<string, unknown> = {};
    if (signupPhase) {
      changes.base_image_id = form.base_image_id ? Number(form.base_image_id) : null;
      changes.target_version = form.target_version;
      changes.planned_features = form.planned_features;
    }
    if ([...form.excluded_tests].sort().join("\n") !== [...active.excluded_tests].sort().join("\n")) {
      changes.excluded_tests = form.excluded_tests;
    }
    changes.actual_delivery = form.actual_delivery;
    changes.known_issues = form.known_issues;
    setSaving(true);
    setError("");
    try {
      await apiPost("/api/entries/update", { entry_id: active.id, changes });
      onDirtyChange(false);
      setEditMode(false);
      toast.success("报名信息已保存");
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleWithdraw() {
    if (!active) return;
    if (!(await confirmDialog({
      title: "撤回报名",
      body: `确认撤回「${app.name}」在「${release.name}」的报名？撤回后本期不发布该 App${signupPhase ? "，报名截止前可重新报名" : "，且报名已截止，不能再报名"}。`,
      danger: true,
      confirmText: "撤回报名",
    }))) return;
    setSaving(true);
    try {
      await apiPost("/api/entries/withdraw", { entry_id: active.id });
      toast.success("已撤回报名");
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const imageSelect = (disabled: boolean) => (
    <select
      className="select"
      value={form.base_image_id}
      onChange={(e) => patch("base_image_id", e.target.value)}
      disabled={disabled}
      data-testid="signup-base-image"
    >
      <option value="">{release.base_images.length ? "请选择基础镜像" : "（RM 尚未发布基础镜像）"}</option>
      {release.base_images.map((image) => (
        <option key={image.id} value={image.id}>{image.name}</option>
      ))}
    </select>
  );

  // ── Not signed up ────────────────────────────────────────────────────────
  if (!active) {
    return (
      <>
      <div className="detail-body" data-testid="signup-pane">
        {entry?.status === "withdrawn" && (
          <div className="banner">该 App 曾报名本版本，已于 {entry.updated_at} 撤回。</div>
        )}
        {signupBlocker && <div className="banner warnp" data-testid="signup-blocker">{signupBlocker}</div>}
        <EnvironmentSection release={release} open />
        {!signupBlocker && (
          <details className="section" open>
            <summary><span className="chev">▶</span> 报名「{release.name}」</summary>
            <div className="section-body">
              <div className="form">
                <label>基础镜像{release.base_images.length > 0 && <span className="required"> *</span>}
                  {imageSelect(false)}
                </label>
                <label>目标版本 <span className="required">*</span>
                  <input className="input" value={form.target_version}
                    onChange={(e) => patch("target_version", e.target.value)}
                    data-testid="signup-target-version" />
                  <span className="field-help">默认取 app_info 的 app_version。</span>
                </label>
                <label className="grid-span-all">计划 feature
                  <textarea value={form.planned_features}
                    onChange={(e) => patch("planned_features", e.target.value)}
                    placeholder="本月计划交付的内容"
                    data-testid="signup-planned-features" />
                </label>
              </div>
              <h4 className="mt-12">测试范围（来自最新 app_info，取消勾选即不测）</h4>
              <ScopeChecklist
                tests={app.latest_app_info?.tests ?? []}
                excluded={form.excluded_tests}
                canExclude
                canInclude
                onChange={(next) => patch("excluded_tests", next)}
              />
            </div>
          </details>
        )}
      </div>
      <div className="detail-foot">
        <span className="foot-note">{signupBlocker ? "当前不能报名" : `报名截止 ${release.signup_deadline}`}</span>
        <div className="spacer" />
        {error && <span className="foot-blocker" role="alert">{error}</span>}
        {!signupBlocker && (
          <button
            className="btn primary"
            onClick={() => void handleSignup()}
            disabled={saving || (release.base_images.length > 0 && !form.base_image_id) || !form.target_version.trim()}
            data-testid="signup-submit"
          >
            {saving ? "提交中…" : "报名"}
          </button>
        )}
      </div>
      </>
    );
  }

  // ── Signed up ────────────────────────────────────────────────────────────
  const editable = canEdit && !locked;
  const planEditable = editMode && signupPhase;
  const canExclude = editMode && (phase === "signup" || phase === "in_progress");
  const canInclude = editMode && signupPhase;
  const deliverableEditable = editMode && !locked;
  const lockedInfo = active.locked_app_info;
  const pending = active.pending_scope_request;

  return (
    <>
    <div className="detail-body" data-testid="signup-pane">
      {locked && <div className="banner bad">🔒 版本已发布，报名信息只读。</div>}
      {!locked && !signupPhase && (
        <div className="banner warnp">
          ⏰ 报名已截止：基础镜像、目标版本和计划 feature 不能再改；测试范围只能删减
          {phase === "qa_closed" ? "（QA 已截止，测试范围不能再改）" : ""}。发布前请把计划 feature 更新为实际交付和已知问题。
        </div>
      )}
      {pending && (
        <div className="banner warnp" data-testid="pending-scope-request">
          <b>测试范围扩大待 QA 审批（#{pending.id}）</b>
          <div className="small mt-4">仓库中新的 app_info 扩大了测试范围，QA 同意前本条报名仍按原版本测试：</div>
          <ul className="small m-4-0-0">
            {pending.additions.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
      {!pending && active.behind_latest && !signupPhase && (
        <div className="banner" data-testid="behind-latest">
          仓库中有更新的 app_info，但它扩大了测试范围且未获 QA 同意，本条报名仍按锁定版本测试。
        </div>
      )}

      <details className="section" open>
        <summary><span className="chev">▶</span> QA 结论</summary>
        <div className="section-body">
          <div className="row gap-8 items-center wrap">
            <QaPill status={active.qa_status} />
            {active.qa_note && <span className="small">{active.qa_note}</span>}
          </div>
          {(active.deliverable_image || active.deliverable_digest) && (
            <div className="small mt-8">
              交付镜像：<span className="mono">{active.deliverable_image || "—"}</span>
              {active.deliverable_digest && <> · digest <span className="mono">{active.deliverable_digest}</span></>}
            </div>
          )}
        </div>
      </details>

      <details className="section" open>
        <summary><span className="chev">▶</span> 报名信息</summary>
        <div className="section-body">
          <div className="form">
            <label>基础镜像
              {imageSelect(!planEditable)}
            </label>
            <label>目标版本
              <input className="input" value={form.target_version}
                onChange={(e) => patch("target_version", e.target.value)}
                disabled={!planEditable} data-testid="entry-target-version" />
            </label>
            <label>锁定的 app_info
              <input className="input" disabled
                value={lockedInfo ? `${lockedInfo.app_version || "—"} · commit ${shortCommit(lockedInfo.commit_id) || "—"}` : "—"} />
            </label>
          </div>
          <label className="docfield">计划 feature
            {planEditable ? (
              <textarea value={form.planned_features} onChange={(e) => patch("planned_features", e.target.value)} />
            ) : (
              <ReadField value={form.planned_features} />
            )}
          </label>
        </div>
      </details>

      <details className="section" open>
        <summary>
          <span className="chev">▶</span> 测试范围
          <span className="badge">{active.scope_tests.length - form.excluded_tests.length}/{active.scope_tests.length} 项</span>
        </summary>
        <div className="section-body">
          <ScopeChecklist
            tests={active.scope_tests}
            excluded={form.excluded_tests}
            canExclude={canExclude}
            canInclude={canInclude}
            onChange={(next) => patch("excluded_tests", next)}
          />
        </div>
      </details>

      <details className="section" open>
        <summary><span className="chev">▶</span> 实际交付与已知问题</summary>
        <div className="section-body docfields-2col">
          <label className="docfield docfield-split">
            <span className="df-label">实际交付</span>
            {deliverableEditable ? (
              <textarea value={form.actual_delivery} onChange={(e) => patch("actual_delivery", e.target.value)}
                placeholder="发布前把计划 feature 更新为实际交付内容" data-testid="entry-actual-delivery" />
            ) : (
              <ReadField value={form.actual_delivery} />
            )}
          </label>
          <label className="docfield docfield-split">
            <span className="df-label">已知问题</span>
            {deliverableEditable ? (
              <textarea value={form.known_issues} onChange={(e) => patch("known_issues", e.target.value)}
                data-testid="entry-known-issues" />
            ) : (
              <ReadField value={form.known_issues} />
            )}
          </label>
        </div>
      </details>

      <EnvironmentSection release={release} />
    </div>

      <div className="detail-foot">
        <span className="foot-note">
          {editMode ? "编辑中 · 刷新前请先保存或取消" : editable ? "点击「修改」编辑报名信息" : "只读"}
        </span>
        <div className="spacer" />
        {error && <span className="foot-blocker" role="alert">{error}</span>}
        {editable && !editMode && (
          <>
            <button className="btn" onClick={() => void handleWithdraw()} disabled={saving} data-testid="entry-withdraw">
              撤回报名
            </button>
            <button className="btn primary" onClick={() => setEditMode(true)} data-testid="entry-edit">✎ 修改</button>
          </>
        )}
        {editable && editMode && (
          <>
            <button className="btn" onClick={() => { setForm(formFromEntry(entry, app)); setEditMode(false); setError(""); onDirtyChange(false); }}>
              取消
            </button>
            <button className="btn primary" onClick={() => void handleSave()} disabled={saving} data-testid="entry-save">
              保存
            </button>
          </>
        )}
      </div>
    </>
  );
}
