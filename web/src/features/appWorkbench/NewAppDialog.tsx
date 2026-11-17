/**
 * 新增 App — submits a CICD create request.  The App record is created only
 * when RM's approval (or SPD delivery) takes effect; until then the request
 * is visible in the CICD request lists.
 */
import React from "react";
import { fetchCicdPreview, submitNewApp, type FetchPreviewResponse } from "../cicd/cicdApi";
import { toast } from "../../lib/toast";
import {
  CICD_REPO_TYPE_DEFAULT,
  CICD_TEST_TIMEOUT_DEFAULT,
  DOC_TARGETS,
  appTypeOptionsForTarget,
  docTargetMetadata,
} from "../../lib/domainMetadata";
import {
  GERRIT_HPC_PROJECT,
  GERRIT_MANIFEST_BRANCH,
  GERRIT_MANIFEST_PROJECT,
  formatGerritUrl,
} from "../../lib/git";
import { BuildImageLabel, RepoPathInput } from "./AppCicdPane";
import { normalizeTimeoutText } from "./helpers";

export function NewAppDialog({ onClose, onSubmitted }: { onClose: () => void; onSubmitted: () => void }) {
  const [officialName, setOfficialName] = React.useState("");
  const [docTarget, setDocTarget] = React.useState("");
  const [appType, setAppType] = React.useState("");
  const [repoType, setRepoType] = React.useState<string>(CICD_REPO_TYPE_DEFAULT);
  const [repoName, setRepoName] = React.useState("");
  const [branch, setBranch] = React.useState("");
  const [buildImage, setBuildImage] = React.useState("");
  const [timeout, setTimeoutValue] = React.useState(String(CICD_TEST_TIMEOUT_DEFAULT));
  const [notes, setNotes] = React.useState("");
  const [preview, setPreview] = React.useState<FetchPreviewResponse | null>(null);
  const [busy, setBusy] = React.useState<"" | "preview" | "submit">("");
  const [error, setError] = React.useState("");

  const isRepo = repoType === "repo";
  const effectiveBranch = isRepo ? GERRIT_MANIFEST_BRANCH : branch.trim();
  const missing = !officialName.trim() ? "官方名称"
    : !docTarget ? "类型（HPC/AI4Sci）"
    : !appType ? "APP 类型"
    : !repoName.trim() ? "仓库路径"
    : !effectiveBranch ? "分支"
    : "";

  function resetPreview() {
    setPreview(null);
    setError("");
  }

  async function handlePreview() {
    setBusy("preview");
    setError("");
    try {
      setPreview(await fetchCicdPreview({ repo_type: repoType, repo_name: repoName.trim(), branch: effectiveBranch }));
    } catch (e) {
      setPreview(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function handleSubmit() {
    setBusy("submit");
    setError("");
    try {
      const result = await submitNewApp({
        official_name: officialName.trim(),
        doc_target: docTarget,
        app_type: appType,
        repo_type: repoType,
        repo_name: repoName.trim(),
        branch: effectiveBranch,
        cicd_build_image: buildImage.trim(),
        cicd_test_timeout: Number(normalizeTimeoutText(timeout)),
        cicd_community_artifact: preview?.community_artifact ?? [],
        cicd_notes: notes.trim(),
        app_version: preview?.app_version ?? "",
      });
      toast.success(`新增 App 申请 #${result.request.id} 已提交，RM 审批生效后 App 才会出现在列表中。`);
      onSubmitted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  const disabled = busy !== "";
  return (
    <div className="dialog-backdrop" data-testid="new-app-dialog">
      <div className="dialog-box minw-560 maxw-720">
        <div className="dialog-head">
          <div>
            <h3>新增 App</h3>
            <p className="muted small m-4-0-0">
              提交 CICD 新建申请；RM 审批生效后才建立 App（CICD 状态为运行中），之后即可报名月度发布。
            </p>
          </div>
        </div>
        <div className="dialog-body">
          <div className="form">
            <label>官方名称 <span className="required" />
              <input className="input" value={officialName} onChange={(e) => setOfficialName(e.target.value)}
                placeholder="例：AMBER" disabled={disabled} data-testid="new-app-name" />
            </label>
            <label>类型 <span className="required" />
              <select className="select" value={docTarget} disabled={disabled} data-testid="new-app-doc-target"
                onChange={(e) => {
                  setDocTarget(e.target.value);
                  if (!appTypeOptionsForTarget(e.target.value).includes(appType)) setAppType("");
                }}>
                <option value="">请选择 HPC/AI4Sci</option>
                {DOC_TARGETS.map((target) => <option key={target} value={target}>{docTargetMetadata[target].label}</option>)}
              </select>
            </label>
            <label>APP 类型 <span className="required" />
              <select className="select" value={appType} disabled={disabled || !docTarget}
                onChange={(e) => setAppType(e.target.value)} data-testid="new-app-type">
                <option value="">请选择 APP 类型</option>
                {appTypeOptionsForTarget(docTarget).map((type) => <option key={type} value={type}>{type}</option>)}
              </select>
            </label>
            <label>仓库类型
              <select className="select" value={repoType} disabled={disabled}
                onChange={(e) => { setRepoType(e.target.value); resetPreview(); }}>
                <option value="git">git</option>
                <option value="repo">repo</option>
              </select>
            </label>
            <label className="grid-span-all">仓库名 / Gerrit 路径 <span className="required" />
              <RepoPathInput repoType={repoType} value={repoName}
                onChange={(value) => { setRepoName(value); resetPreview(); }}
                disabled={disabled} testId="new-app-repo-name" />
              <span className="hint">
                {isRepo
                  ? `repo 类型只填写 ${GERRIT_MANIFEST_PROJECT} 仓库内的 XML 路径；分支固定为 ${GERRIT_MANIFEST_BRANCH}。`
                  : `git 类型只填写 ${GERRIT_HPC_PROJECT} 后的短路径。`}
              </span>
            </label>
            <label>分支 <span className="required" />
              <input className="input" value={isRepo ? GERRIT_MANIFEST_BRANCH : branch} disabled={isRepo || disabled}
                onChange={(e) => { setBranch(e.target.value); resetPreview(); }} data-testid="new-app-branch" />
            </label>
            <label><BuildImageLabel />
              <input className="input" value={buildImage} onChange={(e) => setBuildImage(e.target.value)} disabled={disabled} />
            </label>
            <label>超时(min)
              <input className="input" type="number" min={1} step={1} value={timeout}
                onChange={(e) => setTimeoutValue(normalizeTimeoutText(e.target.value))} disabled={disabled} />
            </label>
            <label>备注
              <input className="input" value={notes} onChange={(e) => setNotes(e.target.value)} disabled={disabled} />
            </label>
          </div>

          {preview && (
            <div className="idbox mt-10" data-testid="derived-identity-box">
              <div className="idbox-title">🔗 Gerrit 身份（服务端解析）</div>
              <div className="idbox-line break">
                <span className="idbox-label">git_url:</span>
                {preview.git_url
                  ? <span className="idbox-value" title={preview.git_url}>{formatGerritUrl(preview.git_url)}</span>
                  : <span className="idbox-warn">{preview.needs_network ? "需联网解析" : "未解析"}</span>}
              </div>
              <div className="idbox-line">
                <span className="idbox-label">git_branch:</span>
                <span className="fw-500">{preview.git_branch || "—"}</span>
              </div>
              {preview.app_info_unavailable ? (
                <div className="small warnp mt-4">app_info 拉取失败：{preview.app_info_error}（仍可提交，生效后会自动同步）</div>
              ) : (
                <div className="small mt-4">
                  版本 {preview.app_version || "—"} · X86 {preview.x86_chips || "—"} · ARM {preview.arm_chips || "—"}
                  · 依赖组件 {preview.dependencies == null ? "未声明" : preview.dependencies.join(", ") || "（空）"}
                </div>
              )}
            </div>
          )}
          {error && <p className="lerr" role="alert">{error}</p>}
        </div>
        <div className="dialog-actions">
          <span className="small muted mr-auto">{missing ? `请填写：${missing}` : ""}</span>
          <button className="btn" onClick={onClose} disabled={disabled}>取消</button>
          <button className="btn" onClick={() => void handlePreview()} disabled={disabled || !repoName.trim() || !effectiveBranch}
            data-testid="new-app-fetch">
            {busy === "preview" ? "拉取中…" : "预览 Gerrit 信息"}
          </button>
          <button className="btn primary" onClick={() => void handleSubmit()} disabled={disabled || !!missing}
            data-testid="new-app-submit">
            {busy === "submit" ? "提交中…" : "提交新建申请"}
          </button>
        </div>
      </div>
    </div>
  );
}
