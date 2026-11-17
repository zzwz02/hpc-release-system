/**
 * CICD — the App's CICD config and running status.
 *
 * Config changes and start/stop are CICD requests reviewed by RM; nothing
 * changes until a request takes effect.  Stopping is refused while the App
 * is signed up for an unpublished cycle (withdraw first), and a stopped App
 * must be started (and the request take effect) before it can sign up.
 */
import React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { submitCicdRequest } from "../cicd/cicdApi";
import {
  CICD_REQ_TYPE_LABEL,
  cicdRequestSummary,
  cicdWorkflowState,
  isOpenJiraModifyRequest,
  isOpenStatusModifyRequest,
  isReplaceablePendingModifyRequest,
  jiraModifyBlockMessage,
  statusModifyBlockMessage,
} from "../cicd/requestUtils";
import { confirmDialog } from "../../lib/confirm";
import { CICD_REPO_TYPE_DEFAULT, CICD_REPO_TYPE_OPTIONS } from "../../lib/domainMetadata";
import { normalizeTimeoutText } from "./helpers";
import {
  GERRIT_HPC_BASE,
  GERRIT_HPC_PROJECT,
  GERRIT_MANIFEST_BRANCH,
  GERRIT_MANIFEST_PROJECT,
  GERRIT_MANIFEST_REPO_URL,
  formatCicdRepoPath,
} from "../../lib/git";
import { formatServerTime } from "../../lib/time";
import { toast } from "../../lib/toast";
import type { App, CicdRequest } from "../../types";
import { CicdStatusPill } from "./pills";

export function RepoPathInput({
  repoType,
  value,
  disabled,
  onChange,
  testId,
}: {
  repoType: string;
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
  testId?: string;
}) {
  const isRepoType = repoType === "repo";
  const prefix = isRepoType ? GERRIT_MANIFEST_REPO_URL : `${GERRIT_HPC_BASE}/`;
  return (
    <div className="repo-input-group">
      <span className="repo-input-prefix">{prefix}</span>
      <input
        className="input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={isRepoType ? "xml地址，例如APP/openfoam/hpc_v2206_v0.xml" : "例如hpc_amber"}
        disabled={disabled}
        data-testid={testId}
      />
    </div>
  );
}

export function BuildImageLabel() {
  return (
    <>
      构建依赖镜像
      <span className="field-label-note">（影响 SPD 编排 CICD 构建任务顺序）</span>
    </>
  );
}

interface ConfigForm {
  cicd_repo_type: string;
  git_url: string;
  git_branch: string;
  cicd_build_image: string;
  cicd_test_timeout: string;
  cicd_notes: string;
}

function formFromApp(app: App): ConfigForm {
  const repoType = app.cicd_repo_type || CICD_REPO_TYPE_DEFAULT;
  return {
    cicd_repo_type: repoType,
    git_url: formatCicdRepoPath(app.git_url, repoType),
    git_branch: app.git_branch,
    cicd_build_image: app.cicd_build_image ?? "",
    cicd_test_timeout: normalizeTimeoutText(app.cicd_test_timeout),
    cicd_notes: app.cicd_notes ?? "",
  };
}

interface AppCicdPaneProps {
  app: App;
  signedUpCycles: string[];
  canEdit: boolean;
  pendingRequests: CicdRequest[];
  historyRequests: CicdRequest[];
  onSubmitted: () => void;
  onDirtyChange: (dirty: boolean) => void;
}

export function AppCicdPane({
  app, signedUpCycles, canEdit, pendingRequests, historyRequests, onSubmitted, onDirtyChange,
}: AppCicdPaneProps) {
  const queryClient = useQueryClient();
  const [editMode, setEditMode] = React.useState(false);
  const [form, setForm] = React.useState<ConfigForm>(() => formFromApp(app));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const [repoError, setRepoError] = React.useState("");

  React.useEffect(() => {
    setForm(formFromApp(app));
    setEditMode(false);
    setError("");
    setRepoError("");
    onDirtyChange(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [app.id, app.git_url, app.git_branch, app.cicd_build_image, app.cicd_test_timeout, app.cicd_notes, app.cicd_repo_type]);

  const openStatus = pendingRequests.find(isOpenStatusModifyRequest);
  const openJira = pendingRequests.find(isOpenJiraModifyRequest);
  const disabled = !editMode || !canEdit;
  const running = app.cicd_status === "running";

  function patch<K extends keyof ConfigForm>(key: K, value: ConfigForm[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    onDirtyChange(true);
  }

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["cicd"] });
    onSubmitted();
  }

  function buildDiff(): Record<string, { old: unknown; new: unknown }> {
    const original = formFromApp(app);
    const diff: Record<string, { old: unknown; new: unknown }> = {};
    if (original.cicd_repo_type !== form.cicd_repo_type) {
      diff.repo_type = { old: original.cicd_repo_type, new: form.cicd_repo_type };
    }
    if (original.cicd_repo_type !== form.cicd_repo_type || original.git_url.trim() !== form.git_url.trim()) {
      diff.repo_name = { old: original.git_url.trim(), new: form.git_url.trim() };
    }
    if (original.git_branch.trim() !== form.git_branch.trim()) {
      diff.branch = { old: original.git_branch, new: form.git_branch.trim() };
    }
    if (original.cicd_build_image.trim() !== form.cicd_build_image.trim()) {
      diff.build_image = { old: original.cicd_build_image, new: form.cicd_build_image.trim() };
    }
    if (original.cicd_test_timeout !== normalizeTimeoutText(form.cicd_test_timeout)) {
      diff.test_timeout = { old: Number(original.cicd_test_timeout), new: Number(normalizeTimeoutText(form.cicd_test_timeout)) };
    }
    if (original.cicd_notes.trim() !== form.cicd_notes.trim()) {
      diff.notes = { old: original.cicd_notes, new: form.cicd_notes.trim() };
    }
    return diff;
  }

  async function handleSubmitConfig() {
    const diff = buildDiff();
    if (!Object.keys(diff).length) {
      setError("CICD 配置没有任何字段发生变化。");
      return;
    }
    const replaceable = pendingRequests.filter(isReplaceablePendingModifyRequest);
    if (replaceable.length && !(await confirmDialog({
      title: "替换待审批申请",
      body: `已有待审批 CICD 修改申请 ${replaceable.map((r) => `#${r.id}`).join("、")}。继续将取消旧申请，并只提交当前改动作为新申请。`,
      confirmText: "继续提交",
    }))) return;
    setSaving(true);
    setError("");
    setRepoError("");
    try {
      await submitCicdRequest({
        task_id: app.id,
        request_type: "modify",
        payload: diff,
        source: "app_workbench",
        replace_open: replaceable.length > 0,
      });
      onDirtyChange(false);
      setEditMode(false);
      toast.success("CICD 变更申请已提交，等待 RM 审批。");
      invalidate();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      if (/git 类型只填写|repo 类型只填写|repo 类型必须填写|manifest XML/.test(message)) {
        setRepoError(`Gerrit 路径校验失败：${message}`);
      } else {
        setError("提交 CICD 变更申请失败：" + message);
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleStatusRequest(target: "running" | "stopped") {
    const verb = target === "running" ? "启动" : "停止";
    if (!(await confirmDialog({
      title: `申请${verb} CICD`,
      body: target === "stopped"
        ? "停止后每日构建关闭，App 视为停止维护：不进累计手册和兼容验证清单，也不再自动同步 app_info。申请需 RM 审批生效。"
        : "启动后每日构建开启；申请生效后才能报名发布。申请需 RM 审批生效。",
      danger: target === "stopped",
      confirmText: `提交${verb}申请`,
    }))) return;
    setSaving(true);
    try {
      await submitCicdRequest({
        task_id: app.id,
        request_type: "modify",
        payload: { status: { old: app.cicd_status, new: target } },
        source: "app_workbench",
      });
      toast.success(`${verb} CICD 申请已提交，等待 RM 审批。`);
      invalidate();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const stopBlocker = signedUpCycles.length
    ? `已报名未发布的版本（${signedUpCycles.join("、")}），要停止 CICD 请先撤回报名`
    : "";
  const statusBlocker = openStatus ? statusModifyBlockMessage(openStatus) : openJira ? jiraModifyBlockMessage(openJira) : "";

  return (
    <>
      <div className="detail-body" data-testid="detail-cicd-pane">
        <div className="banner plain-strip">
          <div className="row gap-8 items-center wrap">
            <b className="fs-12">App CICD 配置</b>
            <CicdStatusPill app={app} />
            <span className="small muted" title={`${app.git_url}@${app.git_branch}`}>
              {formatCicdRepoPath(app.git_url, app.cicd_repo_type || CICD_REPO_TYPE_DEFAULT)}@{app.git_branch}
            </span>
            <span className="spacer" />
            {canEdit && running && (
              <button className="btn sm" onClick={() => void handleStatusRequest("stopped")}
                disabled={saving || !!stopBlocker || !!statusBlocker}
                title={stopBlocker || statusBlocker || undefined}
                data-testid="cicd-stop-request">
                申请停止 CICD
              </button>
            )}
            {canEdit && !running && (
              <button className="btn sm primary" onClick={() => void handleStatusRequest("running")}
                disabled={saving || !!statusBlocker}
                title={statusBlocker || undefined}
                data-testid="cicd-start-request">
                申请启动 CICD
              </button>
            )}
          </div>
          <div className="small muted mt-2 fs-11">
            运行状态只在启动/停止申请生效时改变；是否参加某月发布只看有没有报名。
            {canEdit && running && stopBlocker && <> {stopBlocker}。</>}
          </div>
        </div>

        {openJira && <div className="banner warnp m-0" data-testid="app-cicd-jira-block-banner">{jiraModifyBlockMessage(openJira)}</div>}
        {openStatus && <div className="banner warnp m-0" data-testid="app-cicd-status-block-banner">{statusModifyBlockMessage(openStatus)}</div>}

        {pendingRequests.length > 0 && (
          <div className="banner m-0" data-testid="app-cicd-pending-banner">
            <b>有待处理 CICD 流程</b>
            <div className="table mt-8">
              <table className="cicd-diff-table">
                <thead><tr><th>申请</th><th>状态</th><th>Jira</th><th>内容</th></tr></thead>
                <tbody>
                  {pendingRequests.map((req) => (
                    <tr key={req.id}>
                      <td>#{req.id} {CICD_REQ_TYPE_LABEL[req.request_type] ?? req.request_type}</td>
                      <td>{cicdWorkflowState(req)}</td>
                      <td>{req.jira_id || "—"}</td>
                      <td>{cicdRequestSummary(req)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <details className="section" open>
          <summary><span className="chev">▶</span> App CICD 配置</summary>
          <div className="section-body">
            <div className="form">
              <label>仓库类型
                <select className="select" value={form.cicd_repo_type}
                  onChange={(e) => {
                    patch("cicd_repo_type", e.target.value);
                    if (e.target.value === "repo") patch("git_branch", GERRIT_MANIFEST_BRANCH);
                  }}
                  disabled={disabled} data-testid="field-cicd-repo-type">
                  {CICD_REPO_TYPE_OPTIONS.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <label>Gerrit 路径
                <RepoPathInput repoType={form.cicd_repo_type} value={form.git_url}
                  onChange={(value) => patch("git_url", value)} disabled={disabled} testId="field-cicd-git-url" />
                <span className="field-help">
                  git 类型只填 {GERRIT_HPC_PROJECT} 后路径；repo 类型只填 {GERRIT_MANIFEST_PROJECT} 内 XML 路径。
                </span>
                {repoError && <span className="field-error" aria-live="polite">{repoError}</span>}
              </label>
              <label>Branch
                <input className="input" value={form.git_branch} onChange={(e) => patch("git_branch", e.target.value)}
                  disabled={disabled} data-testid="field-cicd-git-branch" />
              </label>
              {app.latest_app_info?.community.release_status && (
                <div>
                  <div className="field-label">开发者社区产物</div>
                  <div className="readfield">{app.latest_app_info.community.release_status}</div>
                </div>
              )}
              <label><BuildImageLabel />
                <input className="input" value={form.cicd_build_image}
                  onChange={(e) => patch("cicd_build_image", e.target.value)} disabled={disabled}
                  data-testid="field-cicd-build-image" />
              </label>
              <label>超时(min)
                <input className="input" type="number" min={1} step={1} value={form.cicd_test_timeout}
                  onChange={(e) => patch("cicd_test_timeout", normalizeTimeoutText(e.target.value))}
                  disabled={disabled} data-testid="field-cicd-timeout" />
              </label>
              <label className="grid-span-all">备注
                <input className="input" value={form.cicd_notes} onChange={(e) => patch("cicd_notes", e.target.value)}
                  disabled={disabled} data-testid="field-cicd-notes" />
              </label>
            </div>
          </div>
        </details>

        <details className="section" open>
          <summary><span className="chev">▶</span> CICD 变更历史</summary>
          <div className="section-body">
            {!historyRequests.length ? (
              <p className="muted small">暂无 CICD 变更历史。</p>
            ) : (
              <div className="table">
                <table className="cicd-diff-table">
                  <thead><tr><th>申请</th><th>提交人</th><th>时间</th><th>状态</th><th>Jira</th><th>审批备注</th><th>内容</th></tr></thead>
                  <tbody>
                    {historyRequests.map((req) => (
                      <tr key={req.id}>
                        <td>#{req.id} {CICD_REQ_TYPE_LABEL[req.request_type] ?? req.request_type}</td>
                        <td>{req.submitter_display || req.submitter}</td>
                        <td>{formatServerTime(req.reviewed_at || req.submitted_at || "")}</td>
                        <td>{cicdWorkflowState(req)}</td>
                        <td>{req.jira_id || "—"}</td>
                        <td>{req.review_note || "—"}</td>
                        <td>{cicdRequestSummary(req)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </details>
      </div>
      <div className="detail-foot">
        <span className="foot-note">
          {editMode ? "编辑中 · 提交后需 RM 审批" : canEdit ? "修改 CICD 配置需提交申请" : "你没有此 app 的编辑权限"}
        </span>
        <div className="spacer" />
        {error && <span className="foot-blocker" role="alert">{error}</span>}
        {canEdit && !editMode && (
          <button className="btn primary" onClick={() => setEditMode(true)} disabled={!!openStatus || !!openJira}>✎ 修改</button>
        )}
        {canEdit && editMode && (
          <>
            <button className="btn" onClick={() => { setForm(formFromApp(app)); setEditMode(false); setError(""); onDirtyChange(false); }}>取消</button>
            <button className="btn primary" onClick={() => void handleSubmitConfig()} disabled={saving}>提交 CICD 变更申请</button>
          </>
        )}
      </div>
    </>
  );
}
