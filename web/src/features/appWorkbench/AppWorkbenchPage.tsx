/**
 * App 工作台 (App Workbench) tab — index.html:2038+
 *
 * Layout mirrors the legacy two-column split:
 *   left panel  = app list (search, own-only filter, new-app button)
 *   right panel = detail editor for the selected app
 *
 * Data: GET /api/state (with optional ?release_id=)
 * Mutations: POST /api/apps/new, /api/apps/update, /api/app-info,
 *            /api/app-info/fetch, GET /api/app-audit
 *
 * R2: data moves only via explicit refetch after mutation or manual refresh.
 * No polling.
 */
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchCicdTasks, fetchCicdRequests, fetchCicdTaskHistory, cicdFirstNewApp, fetchCicdPreview, CICD_TASKS_KEY } from "../cicd/cicdApi";
import type { FetchPreviewResponse } from "../cicd/cicdApi";
import { RefreshBar } from "../../components/RefreshBar";
import { Markdown } from "../../components/Markdown";
import { formatServerTime } from "../../lib/time";
import { apiGet, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { useUiStore } from "../../store/uiStore";
import { isRM, isOwner, canEdit } from "../../lib/roles";
import { beforeAppFreeze, beforeDocDeadline, releaseLocked, newAppDecisionOptions } from "../../lib/phase";
import { displayName } from "../../lib/identity";
import {
  releaseDecisionLabels,
  releaseDecisionOptions,
  docTargetLabels,
  docTargetOptions,
  qaStatusLabels,
} from "../../lib/labels";
import type { StatePayload, App, Snapshot, ReleaseSummary, AppAuditEntry, SnapshotTestDoc, CicdTask } from "../../types";
import {
  releaseSnap,
  isReleaseSnap,
  compareAppRows,
  filterAppRows,
  qaDotClass,
  qaDotTitle,
  docsItems,
  docsOk,
  qaOk,
  ownerProgress,
  appInfoSource,
  orderChips,
  missingItemText,
  usersLabel,
  APP_DESCRIPTION_LIMIT,
  appDescriptionCount,
  copiedScalarFields,
  mergeCopiedTestDocs,
} from "./helpers";

// ---------------------------------------------------------------------------
// F1 — decision-sync preview types
// ---------------------------------------------------------------------------

interface DecisionSyncPreviewRow {
  release_id: string;
  release_name: string;
  phase_label: string;
  resulting_decision: string | null;
  skipped: boolean;
  reason?: string;
}

interface DecisionSyncPreview {
  decision: string;
  releases: DecisionSyncPreviewRow[];
}

// ---------------------------------------------------------------------------
// Query key + fetcher
// ---------------------------------------------------------------------------

const STATE_KEY = (releaseId?: string) =>
  releaseId ? ["state", releaseId] : ["state"];

async function fetchState(releaseId?: string): Promise<StatePayload> {
  const qs = releaseId ? `?release_id=${encodeURIComponent(releaseId)}` : "";
  return apiGet<StatePayload>(`/api/state${qs}`);
}

// ---------------------------------------------------------------------------
// Shared pill components
// ---------------------------------------------------------------------------

function DecisionPill({ decision }: { decision: string }) {
  const short = (releaseDecisionLabels[decision as keyof typeof releaseDecisionLabels] ?? decision)
    .split("：")[0];
  if (decision === "release") return <span className="pill accent">{short}</span>;
  if (decision === "cicd_only") return <span className="pill">{short}</span>;
  if (decision === "stopped") return <span className="pill bad">{short}</span>;
  return <span className="pill">{short}</span>;
}

function QaPill({ status }: { status?: string }) {
  const s = status ?? "not_checked";
  const cls = s === "qa_passed" ? "ok" : s === "has_issues" ? "warnp" : s === "cannot_release" ? "bad" : "";
  return <span className={`pill ${cls}`}>{qaStatusLabels[s as keyof typeof qaStatusLabels] ?? s}</span>;
}

function QaDot({ snap }: { snap: Snapshot }) {
  return (
    <span
      className={`app-dot ${qaDotClass(snap)}`}
      title={qaDotTitle(snap)}
    />
  );
}

/**
 * Browse-mode renderer for a free-text field: renders the value as Markdown
 * (via the sole <Markdown> sink) inside a capped-height, internally-scrolling
 * box so one long field can't blow up the page.  Empty → muted placeholder.
 */
function ReadField({ value, code = false }: { value?: string | null; code?: boolean }) {
  const v = (value ?? "").trim();
  if (!v) return <div className="readfield empty">（空）</div>;
  // Shell commands are shown verbatim as plain text (NOT a Markdown sink).
  if (code) return <div className="readfield code-block"><pre>{v}</pre></div>;
  return (
    <div className="readfield">
      <Markdown value={value} className="md-view" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// App list panel
// ---------------------------------------------------------------------------

interface AppListProps {
  rows: { app: App; snap: Snapshot }[];
  selectedAppId: string;
  onSelectApp: (id: string) => void;
  search: string;
  onSearchChange: (v: string) => void;
  ownOnly: boolean;
  onOwnOnlyChange: (v: boolean) => void;
  showOwnOnly: boolean;
  displayNames: Record<string, string>;
  canCreateApp: boolean;
  onNewApp: () => void;
}

function AppListPanel({
  rows, selectedAppId, onSelectApp, search, onSearchChange,
  ownOnly, onOwnOnlyChange, showOwnOnly, displayNames, canCreateApp, onNewApp,
}: AppListProps) {
  return (
    <div className="app-list-panel">
      <div className="app-list-toolbar">
        <input
          className="input"
          placeholder="搜索 app…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          data-testid="app-search"
        />
        {showOwnOnly && (
          <label className="check" style={{ whiteSpace: "nowrap" }}>
            <input
              type="checkbox"
              checked={ownOnly}
              onChange={(e) => onOwnOnlyChange(e.target.checked)}
              data-testid="own-only-checkbox"
            />
            只看我的
          </label>
        )}
        <span className="count" data-testid="app-count">共 {rows.length} 个</span>
        {canCreateApp && (
          <button className="btn sm primary" onClick={onNewApp} data-testid="new-app-btn">
            + 新增
          </button>
        )}
      </div>
      <div className="app-table" data-testid="app-table">
        {rows.length === 0 ? (
          <p className="muted small" style={{ padding: "14px", textAlign: "center" }}>无数据</p>
        ) : (
          rows.map(({ app, snap }) => {
            const nm = displayName(snap);
            const ver = (snap.version ?? "").trim();
            // E: only show the small version chip when the name does NOT already
            // carry that version (avoids "Amber 22 · v22" duplication).
            const showVer = !!ver && !nm.includes(ver);
            const rel = isReleaseSnap(snap);
            const docMissing = docsItems(snap).length;
            return (
              <div
                key={app.id}
                className={`app-row ${selectedAppId === app.id ? "active" : ""}`}
                onClick={() => onSelectApp(app.id)}
                data-testid={`app-row-${app.id}`}
              >
                <QaDot snap={snap} />
                <div className="app-meta">
                  <div className="name">
                    <span className="nm-txt">{nm}</span>
                    {showVer && <span className="ver-tag">v{ver}</span>}
                  </div>
                  <div className="sub">
                    {snap.type || "—"} · {usersLabel(snap.owners, displayNames)}
                  </div>
                  {/* D: blocking status at a glance — decision + doc + QA pills */}
                  <div className="app-status">
                    <DecisionPill decision={snap.release_decision} />
                    {rel && (
                      docMissing > 0
                        ? <span className="pill warnp" title="文档信息待补充">文档待补 {docMissing}</span>
                        : <span className="pill ok" title="文档信息齐全">文档 OK</span>
                    )}
                    {rel && <QaPill status={snap.qa_status} />}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Git identity normalization (mirrors app/identity.py normalize_git_url)
// ---------------------------------------------------------------------------

const RESOLVED_REPO_BASE = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC";

/** Expand a short repo name to the full Gerrit SSH URL — mirrors Python normalize_git_url(). */
function normalizeGitUrl(url: string): string {
  const v = (url ?? "").trim();
  if (!v) return v;
  // Already absolute (has ://, is git@ remote, or is a .xml manifest path) → pass through
  if (v.includes("://") || v.startsWith("git@") || v.endsWith(".xml")) return v;
  return `${RESOLVED_REPO_BASE}/${v.replace(/^\//, "")}`;
}

/**
 * Return true if (repoName, taskBranch) matches (gitUrl, appBranch) after
 * normalising both sides — mirrors identity.same_identity().
 * Handles legacy bare-name apps AND cicd-first full-SSH-URL apps.
 */
function sameGitIdentity(repoName: string, taskBranch: string, gitUrl: string, appBranch: string): boolean {
  if (!repoName || !gitUrl) return false;
  return normalizeGitUrl(repoName) === normalizeGitUrl(gitUrl) && taskBranch === appBranch;
}

// ---------------------------------------------------------------------------
// IdentityBox — W4: prominent display of derived Gerrit identity in the wizard
// ---------------------------------------------------------------------------

/**
 * Prominently displays the derived (git_url @ git_branch) so the repo→Gerrit
 * mapping is debuggable at real deployment even when the content fetch 502s.
 *
 * - git-type repos: git_url is always derivable offline (short name → full SSH URL)
 * - repo-type (.xml manifests): identity needs network; gitUrl === null → "需联网解析"
 */
function IdentityBox({ gitUrl, gitBranch }: { gitUrl: string | null; gitBranch: string }) {
  const isUnresolved = !gitUrl;
  return (
    <div
      style={{
        background: "var(--surface2, #e8f4fd)",
        border: "1px solid var(--accent, #1976d2)",
        borderLeft: "4px solid var(--accent, #1976d2)",
        borderRadius: 4,
        padding: "8px 12px",
        marginBottom: 10,
        fontSize: 12,
      }}
      data-testid="derived-identity-box"
    >
      <div style={{ fontWeight: 600, color: "var(--accent, #1976d2)", marginBottom: 5, fontSize: 11, letterSpacing: 0.5 }}>
        🔗 Gerrit 身份（已解析）
      </div>
      <div style={{ fontFamily: "monospace", wordBreak: "break-all", lineHeight: 1.8 }}>
        <span style={{ color: "var(--muted, #666)", minWidth: 80, display: "inline-block" }}>git_url:</span>
        {isUnresolved
          ? <span style={{ background: "var(--warn-bg, #fff3cd)", color: "var(--warn-fg, #856404)", padding: "1px 6px", borderRadius: 3 }}>需联网解析</span>
          : <span style={{ color: "var(--fg, #111)", fontWeight: 500 }}>{gitUrl}</span>
        }
      </div>
      <div style={{ fontFamily: "monospace", lineHeight: 1.8 }}>
        <span style={{ color: "var(--muted, #666)", minWidth: 80, display: "inline-block" }}>git_branch:</span>
        <span style={{ fontWeight: 500 }}>{gitBranch || "—"}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// New-app dialog (CICD-first wizard, Wave 3 / 3.1)
// ---------------------------------------------------------------------------

interface NewAppDialogProps {
  releases: ReleaseSummary[];
  currentReleaseId: string;
  currentUsername: string;
  userRole: string;
  onClose: () => void;
  onCreated: (appId: string) => void;
}

function NewAppDialog({ releases, currentReleaseId, currentUsername, userRole, onClose, onCreated }: NewAppDialogProps) {
  const release = releases.find((r) => r.id === currentReleaseId) ?? null;

  // ── Wizard state (CICD-first, step 1 → fetch → step 2 confirm) ──────────
  const [officialName, setOfficialName] = useState("");
  const [repoType, setRepoType] = useState("git");
  const [repoName, setRepoName] = useState("");
  const [branch, setBranch] = useState("");

  type WizardStep = "form" | "fetching" | "preview" | "fetch-error" | "creating";
  const [step, setStep] = useState<WizardStep>("form");
  const [preview, setPreview] = useState<FetchPreviewResponse | null>(null);
  const [fetchErrMsg, setFetchErrMsg] = useState("");
  const [createErrMsg, setCreateErrMsg] = useState("");

  // W4: derived identity — pre-computed from user inputs (or from server when
  // impl-1's backend returns identity even on content-fetch failure).
  // null git_url = repo-type manifest needs network to resolve.
  const [derivedGitUrl, setDerivedGitUrl] = useState<string | null>(null);
  const [derivedGitBranch, setDerivedGitBranch] = useState<string>("");

  // ── RM escape-hatch: direct /api/apps/new (no CICD task) ────────────────
  const [useDirectCreate, setUseDirectCreate] = useState(false);
  const [directName, setDirectName] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [gitBranch, setGitBranch] = useState("");
  const [docTarget, setDocTarget] = useState<"manual" | "ai4sci">("manual");
  const decisionOpts = newAppDecisionOptions(release);
  const [decision, setDecision] = useState<string>(decisionOpts[0] ?? "release");
  const [directSaving, setDirectSaving] = useState(false);
  const [directErr, setDirectErr] = useState("");

  const isRM = userRole === "RM";
  const isRepo = repoType === "repo";
  const effectiveBranch = isRepo ? "master" : branch.trim();

  // ── Wizard handlers ──────────────────────────────────────────────────────

  async function handleFetch() {
    if (!officialName.trim()) { setFetchErrMsg("请填写官方名称"); return; }
    if (!repoName.trim()) { setFetchErrMsg("请填写仓库名"); return; }
    if (!effectiveBranch) { setFetchErrMsg("请填写分支"); return; }

    // W4: Pre-compute client-side derived identity BEFORE the network call so
    // it is always displayable even if the Gerrit content fetch 502s.
    // git-type: normalize short name → full SSH URL (offline, always derivable).
    // repo-type (.xml manifest): needs Gerrit network to resolve → null = "需联网解析".
    const clientUrl = isRepo ? null : normalizeGitUrl(repoName.trim());
    setDerivedGitUrl(clientUrl);
    setDerivedGitBranch(effectiveBranch);

    setFetchErrMsg("");
    setStep("fetching");
    try {
      const data = await fetchCicdPreview({ repo_type: repoType, repo_name: repoName.trim(), branch: effectiveBranch });

      // W4: impl-1 backend always returns identity fields (git_url / git_branch)
      // even when the Gerrit content fetch fails (app_info_unavailable=true, HTTP 200).
      // Update derived identity from server — more authoritative than the client-computed value.
      if (data.git_url) setDerivedGitUrl(data.git_url);
      else if (data.needs_network) setDerivedGitUrl(null); // manifest still needs network
      if (data.git_branch) setDerivedGitBranch(data.git_branch);

      if (data.app_info_unavailable) {
        // Gerrit content fetch failed (soft 200); identity already updated above.
        setFetchErrMsg(data.app_info_error ?? "Gerrit app_info 不可用");
        setStep("fetch-error");
        return;
      }

      setPreview(data);
      setStep("preview");
    } catch (e) {
      // Only 400 / 403 / network errors reach here under the new impl-1 contract.
      setFetchErrMsg(e instanceof Error ? e.message : "Gerrit 信息拉取失败");
      setStep("fetch-error");
    }
  }

  /** Create without Gerrit preview (fetch failed or user skipped). */
  async function handleSkipAndCreate() {
    setStep("creating");
    setCreateErrMsg("");
    try {
      const r = await cicdFirstNewApp({
        release_id: currentReleaseId,
        official_name: officialName.trim(),
        app_name: officialName.trim(),
        owner_username: currentUsername,
        repo_type: repoType,
        repo_name: repoName.trim(),
        branch: effectiveBranch,
      });
      onCreated(r.app_id);
    } catch (e) {
      setCreateErrMsg(e instanceof Error ? e.message : "创建失败");
      setStep("fetch-error");
    }
  }

  /** Create with fetched app_info (step 2 confirm). */
  async function handleConfirmCreate() {
    setStep("creating");
    setCreateErrMsg("");
    try {
      const r = await cicdFirstNewApp({
        release_id: currentReleaseId,
        official_name: officialName.trim(),
        app_name: officialName.trim(),
        owner_username: currentUsername,
        repo_type: repoType,
        repo_name: repoName.trim(),
        branch: effectiveBranch,
        app_info_parsed: preview?.parsed ?? undefined,
        app_info_commit_id: preview?.commit_id ?? undefined,
      });
      onCreated(r.app_id);
    } catch (e) {
      setCreateErrMsg(e instanceof Error ? e.message : "创建失败");
      setStep("preview");
    }
  }

  async function handleDirectCreate() {
    if (!directName.trim()) return setDirectErr("请填写官方 app/模型名称");
    if (!gitUrl.trim()) return setDirectErr("请填写 Gerrit URL");
    if (!gitBranch.trim()) return setDirectErr("请填写 Branch");
    setDirectErr("");
    setDirectSaving(true);
    try {
      const r = await apiPost<{ app_id: string }>("/api/apps/new", {
        release_id: currentReleaseId,
        official_name: directName.trim(),
        git_url: gitUrl.trim(),
        git_branch: gitBranch.trim(),
        release_decision: decision,
        doc_target: docTarget,
      });
      onCreated(r.app_id);
    } catch (e) {
      setDirectErr(e instanceof Error ? e.message : "创建失败");
    } finally {
      setDirectSaving(false);
    }
  }

  // ── RM escape-hatch render ───────────────────────────────────────────────
  if (useDirectCreate && isRM) {
    return (
      <div className="dialog-backdrop" data-testid="new-app-dialog">
        <div className="dialog-box">
          <div className="dialog-head"><h3>直连创建 App（RM 快捷通道）</h3></div>
          <div className="dialog-body">
            <div className="banner" style={{ marginBottom: 8, fontSize: 12 }}>
              ⚠️ 直连创建绕过 CICD-first 流程，App 初始不关联 CICD 任务。
            </div>
            <div className="form">
              <label>官方名称 <span className="required">*</span>
                <input className="input" value={directName} onChange={(e) => setDirectName(e.target.value)} data-testid="new-app-name" />
              </label>
              <label>Gerrit URL
                <input className="input" value={gitUrl} onChange={(e) => setGitUrl(e.target.value)} />
              </label>
              <label>Branch
                <input className="input" value={gitBranch} onChange={(e) => setGitBranch(e.target.value)} />
              </label>
              <label>类型
                <select className="select" value={docTarget} onChange={(e) => setDocTarget(e.target.value as "manual" | "ai4sci")}>
                  {docTargetOptions.map((v) => (
                    <option key={v} value={v}>{docTargetLabels[v]}</option>
                  ))}
                </select>
              </label>
              <label>Release 决策
                <select className="select" value={decision} onChange={(e) => setDecision(e.target.value)}>
                  {decisionOpts.map((v) => (
                    <option key={v} value={v}>{releaseDecisionLabels[v]}</option>
                  ))}
                </select>
              </label>
              {!beforeAppFreeze(release) && (
                <p className="hint" style={{ color: "var(--warn-fg)" }}>
                  已过 App 冻结 deadline，本 release 不允许以 release 状态新增 app。
                </p>
              )}
            </div>
            {directErr && <p className="lerr">{directErr}</p>}
          </div>
          <div className="dialog-actions">
            <button className="btn ghost sm" onClick={() => setUseDirectCreate(false)} style={{ marginRight: "auto" }}>← 返回 CICD-first</button>
            <button className="btn" onClick={onClose}>取消</button>
            <button className="btn primary" onClick={() => void handleDirectCreate()} disabled={directSaving}>
              {directSaving ? "创建中…" : "直连创建"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Step 2: preview confirmation ─────────────────────────────────────────
  if (step === "preview" || (step === "creating" && preview !== null)) {
    const isCreating = step === "creating";
    // W4: Use server-returned identity (most authoritative); fall back to client-computed.
    const identityUrl = (preview?.git_url) || derivedGitUrl;
    const identityBranch = (preview?.git_branch) || derivedGitBranch || effectiveBranch;
    return (
      <div className="dialog-backdrop" data-testid="new-app-dialog">
        <div className="dialog-box" style={{ maxWidth: 560 }}>
          <div className="dialog-head"><h3>确认 App 信息（CICD-first）</h3></div>
          <div className="dialog-body">
            <div className="banner" style={{ marginBottom: 8, fontSize: 12, background: "var(--surface2,#f0f7ff)", borderLeft: "3px solid var(--accent,#1976d2)" }}>
              ✅ Gerrit 信息已拉取。请确认以下字段后提交创建请求（RM 审批后生效）。
            </div>
            {/* W4: Derived Gerrit identity — always shown so the repo→Gerrit mapping
                is debuggable at real deployment (helps verify normalization is correct). */}
            <IdentityBox gitUrl={identityUrl} gitBranch={identityBranch} />
            <div className="form" style={{ pointerEvents: "none", opacity: 0.9 }} data-testid="new-app-preview">
              <label>官方名称<input className="input" value={officialName.trim()} disabled /></label>
              <label>仓库<input className="input" value={`${repoName.trim()} @ ${effectiveBranch}`} disabled /></label>
              {preview && (<>
                <label>版本<input className="input" value={preview.app_version || "—"} disabled /></label>
                <label>x86 芯片<input className="input" value={preview.x86_chips || "—"} disabled /></label>
                <label>arm 芯片<input className="input" value={preview.arm_chips || "—"} disabled /></label>
                <label>Python label<input className="input" value={preview.python_label || "—"} disabled /></label>
                <label>PyTorch label<input className="input" value={preview.pytorch_label || "—"} disabled /></label>
                <label>OS<input className="input" value={preview.os || "—"} disabled /></label>
                <label>架构<input className="input" value={preview.arch || "—"} disabled /></label>
              </>)}
            </div>
            {createErrMsg && <p className="lerr">{createErrMsg}</p>}
          </div>
          <div className="dialog-actions">
            <button className="btn ghost sm" onClick={() => setStep("form")} style={{ marginRight: "auto" }} disabled={isCreating}>← 重新填写</button>
            <button className="btn" onClick={onClose} disabled={isCreating}>取消</button>
            <button className="btn primary" onClick={() => void handleConfirmCreate()} disabled={isCreating} data-testid="new-app-submit">
              {isCreating ? "提交中…" : "确认并创建"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Fetch-error: Gerrit unreachable ──────────────────────────────────────
  if (step === "fetch-error" || (step === "creating" && preview === null)) {
    const isCreating = step === "creating";
    // W4: show derived identity even when content fetch fails.
    // derivedGitUrl was pre-computed just before the fetch call so it
    // reflects the latest user inputs (or the server-returned value when
    // impl-1's backend returns identity even on content_ok=false).
    const errIdentityBranch = derivedGitBranch || effectiveBranch;
    return (
      <div className="dialog-backdrop" data-testid="new-app-dialog">
        <div className="dialog-box" style={{ maxWidth: 520 }}>
          <div className="dialog-head"><h3>新增 App（CICD-first）</h3></div>
          <div className="dialog-body">
            <div className="banner bad" style={{ marginBottom: 8 }}>
              ⚠️ Gerrit 信息拉取失败：{fetchErrMsg || "网络不可达"}
              <br />
              <span className="small muted" style={{ marginTop: 4, display: "block" }}>
                注：本环境 Gerrit 不可达，芯片/版本信息可在创建后于文档信息页手动完善。
              </span>
            </div>
            {/* W4: Derived identity — always shown so the repo→Gerrit URL mapping
                is debuggable at real deployment even when Gerrit content is unreachable.
                For git-type repos, the full SSH URL is derived offline. */}
            <IdentityBox gitUrl={derivedGitUrl} gitBranch={errIdentityBranch} />
            <p className="small muted">
              <b>官方名称：</b>{officialName.trim()}<br />
              <b>仓库：</b>{repoName.trim()} @ {effectiveBranch}
            </p>
            {createErrMsg && <p className="lerr">{createErrMsg}</p>}
          </div>
          <div className="dialog-actions">
            <button className="btn ghost sm" onClick={() => { setFetchErrMsg(""); setStep("form"); }} style={{ marginRight: "auto" }} disabled={isCreating}>← 返回修改</button>
            <button className="btn" onClick={onClose} disabled={isCreating}>取消</button>
            <button className="btn warn" onClick={() => void handleFetch()} disabled={isCreating}>重试拉取</button>
            <button className="btn primary" onClick={() => void handleSkipAndCreate()} disabled={isCreating} data-testid="new-app-submit">
              {isCreating ? "提交中…" : "跳过，直接创建"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Step 1: identity form (default / fetching) ───────────────────────────
  const isFetching = step === "fetching";
  return (
    <div className="dialog-backdrop" data-testid="new-app-dialog">
      <div className="dialog-box" style={{ maxWidth: 520 }}>
        <div className="dialog-head"><h3>新增 App（CICD-first）</h3></div>
        <div className="dialog-body">
          <div className="banner" style={{ marginBottom: 8, fontSize: 12 }}>
            📋 CICD-first 流程：创建后生成待审批请求，RM 审批通过后 CICD 任务正式生效。<br />
            版本、芯片等信息从 Gerrit 拉取后确认；App 初始决策为 <b>cicd_only</b>。
          </div>
          <div className="form">
            <label>官方名称 <span className="required">*</span>
              <input className="input" value={officialName} onChange={(e) => setOfficialName(e.target.value)}
                data-testid="new-app-name" placeholder="例：AMBER" disabled={isFetching} />
            </label>
            <label>仓库类型
              <select className="select" value={repoType} disabled={isFetching} onChange={(e) => {
                const v = e.target.value;
                setRepoType(v);
                if (v === "repo") setBranch("master");
              }}>
                <option value="git">git</option>
                <option value="repo">repo</option>
              </select>
            </label>
            <label>仓库名 / Gerrit URL <span className="required">*</span>
              <input className="input" value={repoName} onChange={(e) => setRepoName(e.target.value)}
                placeholder="例：sw-metax-open/amber" disabled={isFetching} />
            </label>
            <label>分支 <span className="required">*</span>
              <input className="input" value={isRepo ? "master" : branch} disabled={isRepo || isFetching}
                onChange={(e) => setBranch(e.target.value)} placeholder="例：master" />
            </label>
          </div>
          {fetchErrMsg && <p className="lerr">{fetchErrMsg}</p>}
        </div>
        <div className="dialog-actions">
          {isRM && (
            <button className="btn ghost sm" onClick={() => setUseDirectCreate(true)} style={{ marginRight: "auto" }}
              data-testid="direct-create-btn" disabled={isFetching}>
              RM 直连创建 ↗
            </button>
          )}
          <button className="btn" onClick={onClose} disabled={isFetching}>取消</button>
          <button className="btn primary" onClick={() => void handleFetch()} disabled={isFetching} data-testid="new-app-fetch">
            {isFetching ? "拉取中…" : "拉取 Gerrit 信息 →"}
          </button>
        </div>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// AppCicdPane — CICD sub-tab inside the App detail panel
// ---------------------------------------------------------------------------

const REQ_TYPE_LABEL_APP: Record<string, string> = {
  create: "新建",
  modify: "修改",
  owner_transfer: "负责人变更",
};
const REQ_STATUS_LABEL_APP: Record<string, string> = {
  pending: "等待 RM 审批",
  approved: "已通过",
  rejected: "已拒绝",
  cancelled: "已取消",
};
const FIELD_LABEL_APP: Record<string, string> = {
  app_name: "项目名称",
  app_version: "项目版本",
  repo_type: "仓库类型",
  repo_name: "仓库名",
  branch: "分支",
  build_product: "构建产物",
  community_artifact: "开发者社区产物",
  build_image: "构建依赖镜像",
  test_timeout: "超时(min)",
  owner_username: "负责人",
  status: "状态",
  notes: "备注",
};

function buildProductStr(arr: string[] | null | undefined): string {
  return (Array.isArray(arr) ? arr : []).join(", ") || "—";
}

function communityArtifactStr(arr: string[] | null | undefined): string {
  const items = Array.isArray(arr) ? arr : [];
  if (!items.length) return "—";
  return items.map((v) => (v === "image" ? "镜像" : v === "pkg" ? "软件包" : v)).join("、");
}

interface AppCicdPaneProps {
  task: CicdTask | null;
  app: App;
  /** Used to conditionally show the CICD workbench link (RM/SPD only). */
  userRole?: string;
}

function AppCicdPane({ task, app, userRole }: AppCicdPaneProps) {
  const canAccessWorkbench = userRole === "RM" || userRole === "SPD";
  const [showHistory, setShowHistory] = useState(false);

  const { data: pendingData, isLoading: pendingLoading } = useQuery({
    queryKey: ["cicd", "requests", "pending", task?.id ?? null],
    queryFn: () => task ? fetchCicdRequests({ taskId: task.id, status: "pending" }) : Promise.resolve({ requests: [] }),
    staleTime: 0,
    refetchOnMount: true,
    enabled: !!task,
  });

  const { data: historyData, isLoading: historyLoading } = useQuery({
    queryKey: ["cicd", "history", task?.id ?? null],
    queryFn: () => task ? fetchCicdTaskHistory(task.id) : Promise.resolve({ history: [] }),
    staleTime: 0,
    refetchOnMount: true,
    enabled: !!task && showHistory,
  });

  if (!task) {
    return (
      <div style={{ padding: "1.5rem", color: "var(--muted, #888)" }}>
        <div style={{ textAlign: "center", marginBottom: 12, fontSize: "1.2em" }}>⚙️</div>
        <p>此 App 暂无关联 CICD 任务。</p>
        <p className="small muted" style={{ marginTop: 8 }}>
          CICD 任务通过 CICD-first 创建流程建立（新增 App 时自动提交，RM 审批后生效）。<br />
          已有 CICD 任务的 App 通过 <b>仓库名 + 分支</b> 自动关联，当前 App 仓库信息：<br />
          <code style={{ background: "var(--surface2, #f0f0f0)", padding: "2px 4px", borderRadius: 3 }}>
            {app.git_url}@{app.git_branch}
          </code>
        </p>
      </div>
    );
  }

  const pendingReqs = pendingData?.requests ?? [];
  const history = historyData?.history ?? [];

  const statusCls = task.status === "Running" ? "ok" : task.status === "Stopped" ? "warnp" : "";

  return (
    <div data-testid="cicd-link-card" style={{ display: "flex", flexDirection: "column", gap: 12, padding: "4px 0" }}>
      {/* Identity + status row */}
      <div className="banner" style={{ background: "var(--surface2, #f5f5f5)", borderLeft: "3px solid var(--accent, #1976d2)" }}>
        <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <b style={{ fontSize: 12 }}>CICD 任务</b>
          <span className={`pill ${statusCls}`}>{task.status}</span>
          <span className="small muted">{app.git_url}@{app.git_branch}</span>
          {canAccessWorkbench && (
            <Link to="/cicd" className="small" data-testid="cicd-task-link">
              查看 CICD 工作台任务 #{task.id}
            </Link>
          )}
        </div>
        {task.has_pending && (
          <div className="small warnp" style={{ marginTop: 4 }}>
            ⏳ 有待审批的 CICD 修改申请
          </div>
        )}
        <div className="small muted" style={{ marginTop: 2, fontSize: 11 }}>
          运行/停止由本 app 决策决定
          {canAccessWorkbench && "；构建配置在 CICD 工作台改"}
        </div>
      </div>

      {/* Config section */}
      <details className="section" open>
        <summary><span className="chev">▶</span> 构建配置（只读）</summary>
        <div className="section-body">
          <div className="form" style={{ pointerEvents: "none", opacity: 0.85 }}>
            <label>仓库类型<input className="input" value={task.repo_type} disabled /></label>
            <label>仓库名<input className="input" value={task.repo_name} disabled /></label>
            <label>分支<input className="input" value={task.branch} disabled /></label>
            <label>负责人<input className="input" value={task.owner_display || task.owner_username} disabled /></label>
            <label>构建产物<input className="input" value={buildProductStr(task.build_product)} disabled /></label>
            <label>开发者社区产物<input className="input" value={communityArtifactStr(task.community_artifact)} disabled /></label>
            <label>构建依赖镜像<input className="input" value={task.build_image || "—"} disabled /></label>
            <label>超时(min)<input className="input" value={task.test_timeout ?? 40} disabled /></label>
            {task.notes && <label>备注<input className="input" value={task.notes} disabled /></label>}
          </div>
        </div>
      </details>

      {/* Pending requests */}
      <details className="section" open>
        <summary>
          <span className="chev">▶</span> 待审批申请
          {pendingReqs.length > 0 && (
            <span className="badge warnp" style={{ marginLeft: 6 }}>{pendingReqs.length}</span>
          )}
        </summary>
        <div className="section-body">
          {pendingLoading ? (
            <p className="muted small">加载中…</p>
          ) : pendingReqs.length === 0 ? (
            <p className="muted small">无待审批申请</p>
          ) : (
            <div className="cicd-table-wrap">
              <table className="cicd-table">
                <thead>
                  <tr>
                    <th>申请ID</th>
                    <th>类型</th>
                    <th>提交人</th>
                    <th>提交时间</th>
                    <th>变更内容</th>
                  </tr>
                </thead>
                <tbody>
                  {pendingReqs.map((r) => {
                    const payload = (r.payload ?? {}) as Record<string, unknown>;
                    const summary = Object.keys(payload)
                      .filter((k) => FIELD_LABEL_APP[k])
                      .map((k) => FIELD_LABEL_APP[k])
                      .join(", ") || (r.request_type === "create" ? "新建任务" : "—");
                    return (
                      <tr key={r.id}>
                        <td className="cicd-id">#{r.id}</td>
                        <td>{REQ_TYPE_LABEL_APP[r.request_type] ?? r.request_type}</td>
                        <td>{r.submitter_display || r.submitter}</td>
                        <td className="small muted">{formatServerTime(r.submitted_at ?? "")}</td>
                        <td style={{ maxWidth: 200, fontSize: 12 }}>{summary}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </details>

      {/* History — lazy loaded */}
      <details className="section" onToggle={(e) => setShowHistory((e.target as HTMLDetailsElement).open)}>
        <summary><span className="chev">▶</span> 变更历史</summary>
        <div className="section-body">
          {!showHistory ? (
            <p className="muted small">展开后加载</p>
          ) : historyLoading ? (
            <p className="muted small">加载中…</p>
          ) : history.length === 0 ? (
            <p className="muted small">暂无历史记录</p>
          ) : (
            <div className="cicd-table-wrap">
              <table className="cicd-table">
                <thead>
                  <tr>
                    <th>申请ID</th>
                    <th>类型</th>
                    <th>提交人</th>
                    <th>审批时间</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {[...history].reverse().map((h) => (
                    <tr key={h.id}>
                      <td className="cicd-id">#{h.id}</td>
                      <td>{REQ_TYPE_LABEL_APP[h.request_type] ?? h.request_type}</td>
                      <td>{h.submitter_display || h.submitter}</td>
                      <td className="small muted">{formatServerTime(h.reviewed_at || h.submitted_at || "")}</td>
                      <td>
                        <span className={`cicd-req-status-${h.status}`}>
                          {REQ_STATUS_LABEL_APP[h.status] ?? h.status}
                        </span>
                      </td>
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

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

interface FormState {
  official_name: string;
  type: string;
  official_url: string;
  description: string;
  doc_target: string;
  owners: string;
  release_decision: string;
  git_url: string;
  git_branch: string;
  intro: string;
  image_usage: string;
  binary_usage: string;
  env_setup: string;
  limitations: string;
  community_release: string;
  community_python: string;
  community_framework: string;
  sanity_arm: boolean;
  sanity_ubuntu: boolean;
  test_docs: SnapshotTestDoc[];
}

interface DetailPanelProps {
  app: App | null;
  snap: Snapshot | null;
  release: import("../../types").ReleaseDetail | null;
  releases: ReleaseSummary[];
  user: import("../../types").User | null | undefined;
  displayNames: Record<string, string>;
  onSaved: () => void;
}

function DetailPanel({ app, snap, release, releases, user, displayNames: _displayNames, onSaved }: DetailPanelProps) {
  const userIsRM = isRM(user);
  const userIsOwner = isOwner(user);
  const locked = releaseLocked(release);
  const docDeadline = beforeDocDeadline(release);
  const appFreeze = beforeAppFreeze(release);
  const canEditDetail = !!(app && snap && canEdit(user, snap) && !locked && docDeadline);

  // W3: two sub-tabs in the detail panel
  const [detailTab, setDetailTab] = useState<"docs" | "cicd">("docs");

  const [editMode, setEditMode] = useState(false);
  const [dirty, setDirty] = useState(false);

  // Form state (mirrors snapshot fields editable in legacy)
  const [form, setForm] = useState<FormState>(() => snap ? snapshotToForm(snap, app!) : emptyForm());
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState("");
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [auditEntries, setAuditEntries] = useState<AppAuditEntry[] | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);
  // F1: pending decision-sync owner-choice dialog (null = closed)
  const [syncDialog, setSyncDialog] = useState<{
    preview: DecisionSyncPreview;
    newDecision: string;
    confirmOwner: boolean;
    snapshotUpdate: Record<string, unknown>;
  } | null>(null);
  // F2: copy-from-version picker (null = closed)
  const [showCopyDialog, setShowCopyDialog] = useState(false);

  // CICD tasks — fetched once and cached; used for CicdLinkCard.
  // staleTime:Infinity so this never re-fetches in the background (R2 rule).
  const { data: cicdTasksData } = useQuery({
    queryKey: CICD_TASKS_KEY,
    queryFn: () => fetchCicdTasks(),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  // F3: keep the shared store in sync so the page-level app-switch guard and
  // the TabNav tab-switch guard know whether the detail form is dirty.
  const setAppDetailDirty = useUiStore((s) => s.setAppDetailDirty);
  React.useEffect(() => {
    setAppDetailDirty(dirty);
  }, [dirty, setAppDetailDirty]);
  // Reset the shared dirty flag when the panel unmounts (e.g. leaving the tab).
  React.useEffect(() => () => setAppDetailDirty(false), [setAppDetailDirty]);

  // F3: native browser "Leave site?" prompt on refresh/close while dirty.
  React.useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
      return "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  // Sync form when app selection changes
  React.useEffect(() => {
    if (snap && app) {
      setForm(snapshotToForm(snap, app));
      setEditMode(false);
      setDirty(false);
      setSaveErr("");
      setPendingFile(null);
      setAuditEntries(null);
      setSyncDialog(null);
      setShowCopyDialog(false);
      setDetailTab("docs");
    }
  }, [app?.id, snap?.app_id]); // eslint-disable-line react-hooks/exhaustive-deps

  function snapshotToForm(s: Snapshot, a: App): FormState {
    return {
      official_name: s.official_name ?? "",
      type: s.type ?? "",
      official_url: s.official_url ?? "",
      description: s.description ?? "",
      doc_target: s.doc_target ?? "manual",
      owners: (s.owners ?? []).join(","),
      release_decision: s.release_decision ?? "release",
      git_url: a.git_url ?? "",
      git_branch: a.git_branch ?? "",
      // doc fields
      intro: s.doc?.intro ?? "",
      image_usage: s.doc?.image_usage ?? "",
      binary_usage: s.doc?.binary_usage ?? "",
      env_setup: s.doc?.env_setup ?? "",
      limitations: s.doc?.limitations ?? "",
      // community
      community_release: s.community?.release_status ?? "",
      community_python: s.community?.python_version ?? "",
      community_framework: s.community?.framework_version ?? "",
      // sanity
      sanity_arm: s.sanity?.arm_kylin ?? false,
      sanity_ubuntu: s.sanity?.ubuntu ?? false,
      // test_docs — managed as mutable array
      test_docs: (s.test_docs ?? []).filter((d) => !d.obsolete).map((d) => ({ ...d })),
    };
  }

  function emptyForm(): FormState {
    return {
      official_name: "", type: "", official_url: "", description: "",
      doc_target: "manual", owners: "", release_decision: "release",
      git_url: "", git_branch: "",
      intro: "", image_usage: "", binary_usage: "", env_setup: "", limitations: "",
      community_release: "", community_python: "", community_framework: "",
      sanity_arm: false, sanity_ubuntu: false,
      test_docs: [],
    };
  }

  function patch<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty(true);
  }

  async function loadAudit() {
    if (!app || !release) return;
    setAuditLoading(true);
    try {
      const data = await apiGet<{ entries: AppAuditEntry[] }>(
        `/api/app-audit?app_id=${encodeURIComponent(app.id)}&release_id=${encodeURIComponent(release.id)}`,
      );
      setAuditEntries(data.entries ?? []);
    } catch {
      setAuditEntries([]);
    } finally {
      setAuditLoading(false);
    }
  }

  function buildSnapshotUpdate(confirmOwner: boolean): Record<string, unknown> {
    const snapshotUpdate: Record<string, unknown> = {
      release_decision: form.release_decision,
      official_name: form.official_name,
      type: form.type,
      official_url: form.official_url,
      description: form.description,
      doc_target: form.doc_target,
      owners: form.owners.split(/[,，、;；/]+/).map((x) => x.trim()).filter(Boolean),
      doc: {
        intro: form.intro,
        image_usage: form.image_usage,
        binary_usage: form.binary_usage,
        env_setup: form.env_setup,
        limitations: form.limitations,
      },
      community: {
        release_status: form.community_release,
        python_version: form.community_python,
        framework_version: form.community_framework,
      },
      sanity: {
        arm_kylin: form.sanity_arm,
        ubuntu: form.sanity_ubuntu,
      },
      test_docs: form.test_docs,
    };
    if (confirmOwner) snapshotUpdate["owner_confirmed"] = true;
    return snapshotUpdate;
  }

  async function handleSave(confirmOwner: boolean) {
    if (!app || !snap || !release) return;
    const descCount = appDescriptionCount(form.description);
    if (descCount > APP_DESCRIPTION_LIMIT) {
      setSaveErr(`描述不能超过${APP_DESCRIPTION_LIMIT}字（当前 ${descCount}/${APP_DESCRIPTION_LIMIT}）`);
      return;
    }
    if (userIsOwner && !confirmOwner) {
      alert("Owner 修改必须通过「保存并提交 Owner 确认」提交。");
      return;
    }
    const snapshotUpdate = buildSnapshotUpdate(confirmOwner);
    const newDecision = form.release_decision;

    // F1: when the release_decision changed AND there are later unlocked
    // releases containing this app, show the owner-choice dialog (image.png)
    // instead of saving straight away. The dialog decides sync_decision.
    if (newDecision !== snap.release_decision) {
      const idx = releases.findIndex((r) => r.id === release.id);
      const hasLater = idx >= 0 && idx < releases.length - 1;
      if (hasLater) {
        try {
          const preview = await apiPost<DecisionSyncPreview>("/api/apps/decision-sync/preview", {
            release_id: release.id,
            app_id: app.id,
            decision: newDecision,
          });
          const applicable = (preview.releases ?? []).filter((r) => !r.skipped);
          if (applicable.length > 0) {
            setSyncDialog({ preview, newDecision, confirmOwner, snapshotUpdate });
            return; // wait for the user's choice (取消 / 仅本 release / 同步到后续)
          }
        } catch {
          // Preview failed → fall through to a plain save (no sync).
        }
      }
    }

    await doSave(confirmOwner, snapshotUpdate, false);
  }

  async function doSave(
    confirmOwner: boolean,
    snapshotUpdate: Record<string, unknown>,
    syncDecision: boolean,
  ) {
    if (!app || !snap || !release) return;
    setSaving(true);
    setSaveErr("");
    try {
      const result = await apiPost<{ snapshot?: Snapshot; missing_items?: unknown[] }>("/api/apps/update", {
        release_id: release.id,
        app_id: app.id,
        app: { git_url: form.git_url, git_branch: form.git_branch },
        snapshot: snapshotUpdate,
        sync_decision: syncDecision,
      });

      setSyncDialog(null);
      setDirty(false);
      setEditMode(false);
      onSaved();

      if (confirmOwner) {
        const missing = result.snapshot?.missing_items ?? result.missing_items ?? [];
        const arr = Array.isArray(missing) ? missing : [];
        let msg = "Owner 确认已提交。";
        msg += arr.length
          ? `仍有 ${arr.length} 个待办/门禁项：\n\n- ${(arr as (import("../../types").SnapshotMissingItem | string)[]).map(missingItemText).join("\n- ")}`
          : "当前无待办/门禁项。";
        alert(msg);
      } else {
        alert("保存成功");
      }
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  // F2: copy this app's editable content fields from another release.
  async function handleCopyFromVersion(sourceReleaseId: string) {
    if (!app) return;
    setShowCopyDialog(false);
    if (dirty && !window.confirm("当前表单已有未保存修改，从其他版本复制会覆盖这些内容。确认继续？")) {
      return;
    }
    try {
      const state = await fetchState(sourceReleaseId);
      const srcSnap = state.release?.snapshots?.[app.id] ?? null;
      const srcName = state.release?.name ?? sourceReleaseId;
      if (!srcSnap) {
        alert(`版本「${srcName}」中没有此 app，无法复制。`);
        return;
      }
      setForm((f) => ({
        ...f,
        ...copiedScalarFields(srcSnap),
        test_docs: mergeCopiedTestDocs(f.test_docs, srcSnap.test_docs ?? []),
      }));
      setDirty(true);
      alert(`已从版本「${srcName}」复制可编辑信息（文档/测试/社区/Sanity）。请检查后保存。`);
    } catch (e) {
      alert(`从其他版本复制失败：\n\n${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function handleUploadAppInfo() {
    if (!app || !release || !pendingFile) return;
    try {
      const text = await pendingFile.text();
      await apiPost("/api/app-info", {
        release_id: release.id,
        app_id: app.id,
        source: pendingFile.name,
        app_info: text,
      });
      setPendingFile(null);
      setDirty(false);
      onSaved();
      alert(`app_info.json 上传成功：${pendingFile.name}\n\n已停留在编辑状态，可继续补充文档 / 测试说明。`);
    } catch (e) {
      alert(`app_info.json 上传失败：\n\n${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function handleFetchAppInfo() {
    if (!app || !release) return;
    if (!window.confirm(`从 Gerrit 拉取 ${app.git_url} ${app.git_branch} 的 app_info.json？`)) return;
    if (dirty && !window.confirm("拉取 app_info 会用服务端最新内容刷新表单，未保存的表单修改会丢失。是否继续？")) return;
    try {
      const result = await apiPost<{ commit_id?: string; source?: string }>("/api/app-info/fetch", {
        release_id: release.id,
        app_id: app.id,
      });
      setDirty(false);
      onSaved();
      alert(`Gerrit app_info.json 拉取成功，已停留在编辑状态，可继续编辑。\n\ncommit: ${result.commit_id ?? "未知"}\nsource: ${result.source ?? ""}`);
    } catch (e) {
      alert(`Gerrit app_info.json 拉取失败：\n\n${e instanceof Error ? e.message : String(e)}`);
    }
  }

  if (!app || !snap) {
    return (
      <div className="detail-empty" data-testid="detail-empty">
        <div className="empty-ic">▤</div>
        <p>从左侧选择一个 App，查看与编辑发布信息。</p>
      </div>
    );
  }

  // Find the CICD task linked to this app via normalized (repo_name, branch) identity key.
  // sameGitIdentity mirrors Python identity.same_identity() so both legacy bare-name apps
  // (app.git_url = "hpc_abacus") and cicd-first full-SSH-URL apps match task.repo_name.
  const cicdTask: CicdTask | null =
    (cicdTasksData?.tasks ?? []).find(
      (t) => sameGitIdentity(t.repo_name, t.branch, app.git_url ?? "", app.git_branch ?? ""),
    ) ?? null;

  const rel = isReleaseSnap(snap);
  const prog = ownerProgress(snap);
  const todo = docsItems(snap);
  const descCount = appDescriptionCount(form.description);

  const footNote = editMode
    ? "编辑中 · 刷新前请先保存或取消"
    : locked ? "Release 已锁定，只读"
    : !docDeadline ? "Doc deadline 已过，表单只读"
    : canEditDetail ? "点击「修改」开始编辑"
    : "你没有此 app 的编辑权限";

  // Allowed decisions in edit mode
  const allowedDecisions = appFreeze
    ? releaseDecisionOptions
    : snap.release_decision === "release"
      ? ["release", "cicd_only", "stopped"] as const
      : ["cicd_only", "stopped"] as const;

  return (
    <div className="detail-panel" data-testid="detail-panel">
      {/* Header */}
      <div className="detail-head">
        <QaDot snap={snap} />
        <div className="dh-main">
          <h2>{displayName(snap)}</h2>
          <div className="row2">
            <DecisionPill decision={snap.release_decision} />
            {snap.version && <span className="pill">版本 {snap.version}</span>}
            <span className="pill">{(docTargetLabels as Record<string, string>)[snap.doc_target] ?? snap.doc_target}</span>
            {rel && (todo.length ? <span className="pill warnp">待补 {todo.length} 项</span> : <span className="pill ok">信息齐全</span>)}
            {rel && <QaPill status={snap.qa_status} />}
          </div>
        </div>
        {userIsOwner && <div className="greet">你好 👋 完成清单后提交确认</div>}
      </div>

      {/* W3: Sub-tab navigation */}
      <div className="cicd-subtabs" style={{ borderBottom: "1px solid var(--border, #ddd)", marginBottom: 0 }}>
        <button
          className={`cicd-subtab${detailTab === "docs" ? " active" : ""}`}
          onClick={() => setDetailTab("docs")}
          data-testid="detail-tab-docs"
        >
          文档信息
        </button>
        <button
          className={`cicd-subtab${detailTab === "cicd" ? " active" : ""}`}
          onClick={() => setDetailTab("cicd")}
          data-testid="detail-tab-cicd"
          style={{ position: "relative" }}
        >
          CICD
          {cicdTask?.has_pending && (
            <span className="cicd-badge" style={{ marginLeft: 4, fontSize: 10 }}>!</span>
          )}
        </button>
      </div>

      {detailTab === "cicd" ? (
        <div className="detail-body" data-testid="detail-cicd-pane">
          <AppCicdPane task={cicdTask} app={app} userRole={user?.role ?? ""} />
        </div>
      ) : (
      <div className="detail-body">
        {/* Phase/lock banners */}
        {locked && (
          <div className="banner bad">🔒 Release 已最终锁定，所有信息冻结。</div>
        )}
        {!locked && !docDeadline && (
          <div className="banner warnp">⏰ 已过 Doc deadline：表单、文档和 app_info 已冻结，仅 QA 可继续标注状态。</div>
        )}
        {!locked && docDeadline && !appFreeze && (
          <div className="banner warnp">❄️ 已过 App 冻结 deadline：本 release 不可再新增或切换为 release 决策。</div>
        )}

        {/* Dirty banner */}
        {(dirty || editMode) && (
          <div className="banner warnp">
            {dirty ? "有未保存修改，刷新前请先保存或取消编辑。" : "当前处于编辑状态，刷新前请先保存或取消编辑。"}
          </div>
        )}

        {/* Todo checklist */}
        {rel && todo.length > 0 && (
          <details className="section checklist compact" open>
            <summary>
              <div className="checklist-h">
                <h3>⚠️ 还需完成 {todo.length} 项</h3>
                <span className="prog-label">填写完成度 {prog.pct}%</span>
              </div>
              <div className="bar"><span style={{ width: `${prog.pct}%` }} /></div>
            </summary>
            <div className="checklist-items">
              {todo.map((it, i) => (
                <div key={i} className="check-item">
                  <span className="box" />
                  <span className="ct">{missingItemText(it)}</span>
                </div>
              ))}
            </div>
          </details>
        )}
        {rel && todo.length === 0 && (
          <div className="checklist done">
            <div className="checklist-h">
              <h3>✅ 发布信息已齐全</h3>
              <span className="prog-label">填写完成度 {prog.pct}%</span>
            </div>
            <div className="bar"><span style={{ width: `${prog.pct}%` }} /></div>
          </div>
        )}
        {!rel && (
          <div className="banner">本 app 本轮决策为 <b>{snap.release_decision}</b>，不进入文档生成与 QA，无需补充文档 / 测试说明。</div>
        )}

        {/* QA issue note */}
        {snap.qa_status === "has_issues" && snap.qa_issue_note && (
          <div className="banner warnp">QA 标注「存在问题」：{snap.qa_issue_note}（会附加到已知限制段）</div>
        )}

        {/* Basic info section */}
        <details className="section" open>
          <summary><span className="chev">▶</span> 基本信息</summary>
          <div className="section-body">
            <div className="form">
              <label>官方名称
                <input className="input" value={form.official_name}
                  onChange={(e) => patch("official_name", e.target.value)}
                  disabled={!editMode || !userIsRM} data-testid="field-official-name" />
              </label>
              <label>Owner
                <input className="input" value={form.owners}
                  onChange={(e) => patch("owners", e.target.value)}
                  disabled={!editMode || !userIsRM} />
              </label>
              <label>类型
                <select className="select" value={form.doc_target}
                  onChange={(e) => patch("doc_target", e.target.value as "manual" | "ai4sci")}
                  disabled={!editMode || !userIsRM}>
                  {docTargetOptions.map((v) => (
                    <option key={v} value={v}>{docTargetLabels[v]}</option>
                  ))}
                </select>
              </label>
              <label>App 类型
                <input className="input" value={form.type}
                  onChange={(e) => patch("type", e.target.value)}
                  disabled={!editMode} />
              </label>
              <label>Gerrit URL
                <input className="input" value={form.git_url}
                  onChange={(e) => patch("git_url", e.target.value)}
                  disabled={!editMode || !userIsRM} />
              </label>
              <label>Branch
                <input className="input" value={form.git_branch}
                  onChange={(e) => patch("git_branch", e.target.value)}
                  disabled={!editMode || !userIsRM} />
              </label>
              <label>官方 URL
                <input className="input" value={form.official_url}
                  onChange={(e) => patch("official_url", e.target.value)}
                  disabled={!editMode} />
              </label>
              <label>Release 决策
                <select className="select" value={form.release_decision}
                  onChange={(e) => patch("release_decision", e.target.value as typeof form["release_decision"])}
                  disabled={!editMode}
                  data-testid="field-decision">
                  {allowedDecisions.map((v) => (
                    <option key={v} value={v}>{releaseDecisionLabels[v]}</option>
                  ))}
                </select>
              </label>
              {/* Inline preview: show expected CICD status change when decision differs */}
              {editMode && cicdTask && form.release_decision !== snap.release_decision && (
                <div style={{ gridColumn: "1 / -1", marginTop: -4, marginBottom: 4 }} data-testid="cicd-decision-preview">
                  <span className="small warnp">
                    ⟳ 待审批：CICD 任务将变为{" "}
                    <b>{form.release_decision === "stopped" ? "Stopped" : "Running"}</b>
                  </span>
                </div>
              )}
              <label>版本（来自 app_info）
                <input className="input" value={snap.version ?? ""} disabled />
              </label>
              <label>X86 芯片
                <input className="input" value={orderChips(snap.x86_chips)} disabled />
              </label>
              <label>ARM 芯片
                <input className="input" value={orderChips(snap.arm_chips)} disabled />
              </label>
              <label>Python label（来自 app_info）
                <input className="input" value={snap.python_labels ?? ""} disabled />
              </label>
              <label>PyTorch label（来自 app_info）
                <input className="input" value={snap.pytorch_labels ?? ""} disabled />
              </label>
              <label>OS（来自 app_info）
                <input className="input" value={snap.build_os ?? ""} disabled />
              </label>
              <label>Arch（来自 app_info）
                <input className="input" value={snap.build_arches ?? ""} disabled />
              </label>
            </div>
            {/* Description with char counter */}
            <label className="docfield desc-field">
              描述（30字内）
              {editMode ? (
                <div className="textarea-wrap">
                  <textarea
                    value={form.description}
                    onChange={(e) => patch("description", e.target.value)}
                    data-testid="field-description"
                  />
                  <span className={`desc-counter ${descCount > APP_DESCRIPTION_LIMIT ? "over" : ""}`}>
                    {descCount}/{APP_DESCRIPTION_LIMIT}
                  </span>
                </div>
              ) : (
                <ReadField value={form.description} />
              )}
            </label>
          </div>
        </details>

        {/* app_info section */}
        <details className="section" open>
          <summary>
            <span className="chev">▶</span> app_info 与 diff
            {rel && (
              snap.app_info && (snap.app_info as Record<string, unknown>)["source_type"]
                ? ((snap.app_info_diffs ?? []).length
                    ? <span className="badge">{snap.app_info_diffs.length} 项差异</span>
                    : <span className="badge ok">已上传</span>)
                : <span className="badge warnp">待上传</span>
            )}
          </summary>
          <div className="section-body">
            <div className="src-line">app_info 来源：{appInfoSource(snap)}</div>
            {editMode && (
              <div className="row" style={{ marginBottom: 12 }}>
                <label className="btn ghost sm" style={{ cursor: "pointer" }}>
                  选择 app_info.json
                  <input
                    type="file" accept=".json" hidden
                    onChange={(e) => setPendingFile(e.target.files?.[0] ?? null)}
                  />
                </label>
                <span className="small muted">
                  {pendingFile ? `已选择：${pendingFile.name}` : "未选择文件"}
                </span>
                {pendingFile && (
                  <button className="btn ghost sm" onClick={() => setPendingFile(null)}>清除</button>
                )}
                <button className="btn sm" onClick={() => void handleUploadAppInfo()} disabled={!pendingFile}>
                  上传 app_info
                </button>
                <button className="btn sm" onClick={() => void handleFetchAppInfo()}>从 Gerrit 拉取</button>
              </div>
            )}
            {(snap.app_info_diffs ?? []).length > 0 && (
              <div className="table">
                <table>
                  <thead><tr><th>类型</th><th>字段</th><th>旧值</th><th>新值</th></tr></thead>
                  <tbody>
                    {snap.app_info_diffs.map((d, i) => (
                      <tr key={i}>
                        <td>{d.field}</td>
                        <td>{d.field}</td>
                        <td>{JSON.stringify(d.old)}</td>
                        <td>{JSON.stringify(d.new)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </details>

        {/* Community & Sanity section */}
        <details className="section" open>
          <summary><span className="chev">▶</span> 社区发布与 Sanity</summary>
          <div className="section-body">
            <div className="form">
              <label>开发者社区发布情况
                <input className="input" value={form.community_release}
                  onChange={(e) => patch("community_release", e.target.value)}
                  disabled={!editMode} />
              </label>
              <label>社区包支持 Python 版本
                <input className="input" value={form.community_python}
                  onChange={(e) => patch("community_python", e.target.value)}
                  disabled={!editMode} />
              </label>
              <label>社区包支持框架及版本
                <input className="input" value={form.community_framework}
                  onChange={(e) => patch("community_framework", e.target.value)}
                  disabled={!editMode} />
              </label>
            </div>
            {!userIsOwner && (
              <div className="row" style={{ marginTop: 11, gap: 24, flexWrap: "wrap" }}>
                <label className="check">
                  <input type="checkbox" checked={form.sanity_arm}
                    onChange={(e) => patch("sanity_arm", e.target.checked)}
                    disabled={!editMode || !userIsRM} />
                  ARM / Kylin Sanity <span className="small muted">（RM 填写）</span>
                </label>
                <label className="check">
                  <input type="checkbox" checked={form.sanity_ubuntu}
                    onChange={(e) => patch("sanity_ubuntu", e.target.checked)}
                    disabled={!editMode || !userIsRM} />
                  Ubuntu / 兼容性 Sanity <span className="small muted">（RM 填写）</span>
                </label>
              </div>
            )}
          </div>
        </details>

        {/* Documentation fields */}
        <details className="section" open>
          <summary>
            <span className="chev">▶</span> 文档字段（Markdown 格式）
            {rel && (
              (docsOk(snap) && qaOk(snap))
                ? <span className="badge ok">可发布</span>
                : <span className="badge warnp">未就绪</span>
            )}
          </summary>
          <div className="section-body docfields-2col">
            {(["intro", "image_usage", "binary_usage", "env_setup", "limitations"] as const).map((key) => {
              const labels: Record<string, string> = {
                intro: "基本介绍", image_usage: "镜像使用方法", binary_usage: "二进制包使用方法",
                env_setup: "环境搭建", limitations: "已知限制",
              };
              return (
                <label key={key} className="docfield docfield-split">
                  <span className="df-label">{labels[key]}</span>
                  {editMode ? (
                    <textarea
                      value={form[key]}
                      onChange={(e) => patch(key, e.target.value)}
                      data-testid={`field-doc-${key}`}
                    />
                  ) : (
                    <ReadField value={form[key]} />
                  )}
                </label>
              );
            })}
          </div>
        </details>

        {/* Test docs section */}
        <details className="section" open>
          <summary><span className="chev">▶</span> 测试说明（各 test_cmd）</summary>
          <div className="section-body">
            <TestDocsEditor
              testDocs={form.test_docs}
              editMode={editMode}
              userIsOwner={userIsOwner}
              onChange={(docs) => { setForm((f) => ({ ...f, test_docs: docs })); setDirty(true); }}
            />
          </div>
        </details>

        {/* Change log / audit */}
        <details className="section" onToggle={(e) => {
          if ((e.target as HTMLDetailsElement).open && !auditEntries && !auditLoading) {
            void loadAudit();
          }
        }}>
          <summary><span className="chev">▶</span> 变更记录（本 release）</summary>
          <div className="section-body">
            <ChangeLogTable entries={auditEntries} loading={auditLoading} />
          </div>
        </details>

        {saveErr && <p className="lerr" style={{ padding: "0 1rem 1rem" }}>{saveErr}</p>}
      </div>
      )}

      {/* Footer actions — only shown on docs tab */}
      {detailTab === "docs" && (
      <div className="detail-foot">
        <span className="foot-note">{footNote}</span>
        {canEditDetail && editMode && (
          <button
            className="btn ghost sm"
            onClick={() => setShowCopyDialog(true)}
            data-testid="copy-from-version-btn"
            title="从其他 release 复制本 app 的文档 / 测试说明 / 社区 / Sanity 等可编辑信息到当前表单"
          >
            ⇄ 从其他版本复制信息
          </button>
        )}
        <div className="spacer" />
        {canEditDetail && !editMode && (
          <button className="btn primary" onClick={() => setEditMode(true)}>✎ 修改</button>
        )}
        {canEditDetail && editMode && userIsRM && (
          <>
            <button className="btn" onClick={() => { setEditMode(false); setDirty(false); snap && app && setForm(snapshotToForm(snap, app)); }}>取消</button>
            <button className="btn primary" onClick={() => void handleSave(false)} disabled={saving}>保存</button>
          </>
        )}
        {canEditDetail && editMode && userIsOwner && (
          <>
            <button className="btn" onClick={() => { setEditMode(false); setDirty(false); snap && app && setForm(snapshotToForm(snap, app)); }}>取消</button>
            <button className="btn good" onClick={() => void handleSave(true)} disabled={saving} title="保存当前内容，并确认本 app 的发布信息已补齐">
              ✓ 保存并提交 Owner 确认
            </button>
          </>
        )}
      </div>
      )}

      {/* F1 — decision-sync owner-choice dialog */}
      {syncDialog && (
        <DecisionSyncDialog
          preview={syncDialog.preview}
          newDecision={syncDialog.newDecision}
          saving={saving}
          onCancel={() => setSyncDialog(null)}
          onLocalOnly={() => void doSave(syncDialog.confirmOwner, syncDialog.snapshotUpdate, false)}
          onSyncAll={() => void doSave(syncDialog.confirmOwner, syncDialog.snapshotUpdate, true)}
        />
      )}

      {/* F2 — copy-from-version picker */}
      {showCopyDialog && release && (
        <CopyFromVersionDialog
          releases={releases}
          currentReleaseId={release.id}
          onClose={() => setShowCopyDialog(false)}
          onPick={(rid) => void handleCopyFromVersion(rid)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// F1 — DecisionSyncDialog (the image.png owner-choice dialog)
// ---------------------------------------------------------------------------

interface DecisionSyncDialogProps {
  preview: DecisionSyncPreview;
  newDecision: string;
  saving: boolean;
  onCancel: () => void;
  onLocalOnly: () => void;
  onSyncAll: () => void;
}

function DecisionSyncDialog({
  preview, newDecision, saving, onCancel, onLocalOnly, onSyncAll,
}: DecisionSyncDialogProps) {
  const rows = preview.releases ?? [];
  const applicable = rows.filter((r) => !r.skipped);
  return (
    <div className="dialog-backdrop" data-testid="decision-sync-dialog">
      <div className="dialog-box" style={{ minWidth: 560, maxWidth: 720 }}>
        <div className="dialog-head"><h3>同步 release 决策到后续 release?</h3></div>
        <div className="dialog-body">
          <p className="muted">
            你把 release 决策改为「{newDecision}」。是否把该决策同步到下列 {applicable.length} 个后续 release?
          </p>
          <div className="table">
            <table>
              <thead>
                <tr><th>RELEASE</th><th>阶段</th><th>RELEASE 决策</th></tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const gated = !r.skipped && r.resulting_decision !== newDecision;
                  return (
                    <tr
                      key={r.release_id}
                      className={r.skipped ? "muted" : ""}
                      data-testid={`sync-row-${r.release_id}`}
                      style={r.skipped ? { opacity: 0.55 } : undefined}
                    >
                      <td>{r.release_name}</td>
                      <td>{r.phase_label}</td>
                      <td>
                        {r.skipped ? (
                          <span className="muted small">跳过：{r.reason}</span>
                        ) : gated ? (
                          <span className="pill warnp" title="该 release 处于冻结期，升级为 release 会扩大 QA 范围，已降级为 cicd_only">
                            调整为 {r.resulting_decision}（冻结期降级）
                          </span>
                        ) : (
                          <span>调整为 {r.resulting_decision}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        <div className="dialog-actions">
          <button className="btn" onClick={onCancel} disabled={saving} data-testid="sync-cancel">取消</button>
          <button className="btn" onClick={onLocalOnly} disabled={saving} data-testid="sync-local-only">
            不同步，仅本 release
          </button>
          <button className="btn primary" onClick={onSyncAll} disabled={saving} data-testid="sync-all">
            同步到后续 release
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// F2 — CopyFromVersionDialog
// ---------------------------------------------------------------------------

interface CopyFromVersionDialogProps {
  releases: ReleaseSummary[];
  currentReleaseId: string;
  onClose: () => void;
  onPick: (releaseId: string) => void;
}

function CopyFromVersionDialog({
  releases, currentReleaseId, onClose, onPick,
}: CopyFromVersionDialogProps) {
  const others = releases.filter((r) => r.id !== currentReleaseId);
  const [selected, setSelected] = useState(others[0]?.id ?? "");
  return (
    <div className="dialog-backdrop" data-testid="copy-from-version-dialog">
      <div className="dialog-box">
        <div className="dialog-head"><h3>从其他版本复制</h3></div>
        <div className="dialog-body">
          {others.length === 0 ? (
            <p className="muted">没有其他 release 可供复制。</p>
          ) : (
            <div className="form">
              <p className="muted small">
                选择一个 release，复制其中本 app 的文档 / 测试说明 / 社区 / Sanity 等可编辑信息到当前表单。
                不会复制 Owner / Gerrit / 版本 / release 决策 / QA 等信息。
              </p>
              <label>源 release
                <select
                  className="select"
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                  data-testid="copy-source-select"
                >
                  {others.map((r) => (
                    <option key={r.id} value={r.id}>{r.name}</option>
                  ))}
                </select>
              </label>
            </div>
          )}
        </div>
        <div className="dialog-actions">
          <button className="btn" onClick={onClose}>取消</button>
          <button
            className="btn primary"
            disabled={!selected}
            onClick={() => selected && onPick(selected)}
            data-testid="copy-confirm"
          >
            复制
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TestDocsEditor
// ---------------------------------------------------------------------------

interface TestDocsEditorProps {
  testDocs: SnapshotTestDoc[];
  editMode: boolean;
  userIsOwner: boolean;
  onChange: (docs: SnapshotTestDoc[]) => void;
}

function TestDocsEditor({ testDocs, editMode, userIsOwner, onChange }: TestDocsEditorProps) {
  const active = testDocs.filter((d) => !d.obsolete);

  if (!active.length) {
    return <p className="muted small">上传 app_info 后会自动生成测试项。</p>;
  }

  function patchDoc(id: string, field: keyof SnapshotTestDoc, value: unknown) {
    onChange(testDocs.map((d) => d.id === id ? { ...d, [field]: value } : d));
  }

  return (
    <>
      {active.map((d) => (
        <div key={d.id} className="tcard">
          <div className="tcard-h">
            <span className="code">{d.path}</span>
            {d.owner_added && <span className="pill">owner-added</span>}
          </div>
          <div className="tcard-b">
            <label className="full">命令
              {editMode ? (
                <textarea className="code"
                  value={d.command ?? ""}
                  onChange={(e) => patchDoc(d.id, "command", e.target.value)}
                  disabled={!d.owner_added} />
              ) : (
                <ReadField value={d.command} code />
              )}
            </label>
            <label>测试内容
              {editMode
                ? <textarea value={d.content ?? ""} onChange={(e) => patchDoc(d.id, "content", e.target.value)} />
                : <ReadField value={d.content} />}
            </label>
            <label>测试数据集
              {editMode
                ? <textarea value={d.dataset ?? ""} onChange={(e) => patchDoc(d.id, "dataset", e.target.value)} />
                : <ReadField value={d.dataset} />}
            </label>
            <label>结果查看
              {editMode
                ? <textarea value={d.result_view ?? ""} onChange={(e) => patchDoc(d.id, "result_view", e.target.value)} />
                : <ReadField value={d.result_view} />}
            </label>
            <label>通过标准
              {editMode
                ? <textarea value={d.pass_criteria ?? ""} onChange={(e) => patchDoc(d.id, "pass_criteria", e.target.value)} />
                : <ReadField value={d.pass_criteria} />}
            </label>
          </div>
        </div>
      ))}
      {editMode && userIsOwner && (
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn sm" onClick={() => {
            const newId = `new_${Date.now()}`;
            onChange([...testDocs, {
              id: newId, path: `owner_added.${active.length + 1}`,
              command: "", dataset: "", content: "", result_view: "", pass_criteria: "",
              owner_added: true,
            }]);
          }}>
            + 新增 owner-added 测试项
          </button>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// ChangeLogTable
// ---------------------------------------------------------------------------

interface ChangeLogTableProps {
  entries: AppAuditEntry[] | null;
  loading: boolean;
}

function ChangeLogTable({ entries, loading }: ChangeLogTableProps) {
  if (loading) return <p className="muted small">加载中…</p>;
  if (!entries) return <p className="muted small">展开后自动加载</p>;
  if (!entries.length) return <p className="muted small">暂无变更记录</p>;
  return (
    <div className="table" data-testid="changelog-table">
      <table>
        <thead>
          <tr><th>时间</th><th>操作人</th><th>角色</th><th>事件</th></tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={i}>
              <td>{formatServerTime(e.ts)}</td>
              <td>{e.user}</td>
              <td>{e.role}</td>
              <td>{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AppWorkbenchPage — root
// ---------------------------------------------------------------------------

export function AppWorkbenchPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const userIsOwner = isOwner(user);

  const {
    selectedApp, setSelectedApp,
    appDetailDirty,
  } = useUiStore();

  // Shared release selector — "" in store becomes undefined so the bootstrap
  // query fires (key ["state"] → default release); once data arrives the
  // effect below seeds the store and the key flips to ["state", "<id>"].
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

  // Seed shared store + specific cache key after first load (mirrors DashboardPage).
  React.useEffect(() => {
    if (data?.release?.id && !selectedReleaseId) {
      queryClient.setQueryData(STATE_KEY(data.release.id), data);
      setSelectedReleaseId(data.release.id);
    }
  }, [data, data?.release?.id, selectedReleaseId, setSelectedReleaseId, queryClient]);

  const [search, setSearch] = useState("");
  const [ownOnly, setOwnOnly] = useState(false);
  const [showNewApp, setShowNewApp] = useState(false);

  const release = data?.release ?? null;
  const apps = data?.apps ?? [];
  const releases = data?.releases ?? [];
  const displayNames = data?.user_display_names ?? {};
  const locked = releaseLocked(release);

  // Build filtered + sorted app rows
  const allRows = apps
    .map((app) => ({ app, snap: releaseSnap(release, app.id) }))
    .filter((x): x is { app: App; snap: Snapshot } => x.snap !== null);

  const filteredRows = filterAppRows(allRows, search, ownOnly, user, displayNames)
    .sort((a, b) => compareAppRows(a, b, user));

  const selectedAppObj = apps.find((a) => a.id === selectedApp) ?? null;
  const selectedSnap = selectedAppObj ? releaseSnap(release, selectedAppObj.id) : null;

  const canCreateApp = !!(data?.release && !locked);

  function handleSelectApp(id: string) {
    if (selectedApp === id) return;
    if (appDetailDirty && selectedApp && selectedApp !== id) {
      if (!window.confirm("有未保存的修改，确认放弃并切换 app?")) return;
    }
    setSelectedApp(id);
  }

  function handleSaved() {
    void refetch();
  }

  function handleNewAppCreated(appId: string) {
    setShowNewApp(false);
    void refetch().then(() => setSelectedApp(appId));
    alert("新增 app 已创建");
  }

  if (error) {
    return (
      <section className="view active">
        <p className="muted" style={{ padding: "1rem" }}>加载失败：{(error as Error).message}</p>
      </section>
    );
  }

  return (
    <section className="view view--split active" data-testid="appworkbench-page">
      <div className="page-toolbar">
        <h2>App 工作台</h2>
        {data && (
          <select
            className="select"
            style={{ width: "auto", minWidth: 160 }}
            value={selectedReleaseId ?? ""}
            onChange={(e) => {
              const id = e.target.value;
              setSelectedReleaseId(id);
              void queryClient.invalidateQueries({ queryKey: STATE_KEY(id) });
            }}
            aria-label="选择 release"
          >
            {releases.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        )}
        <span className="spacer" />
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          isFetching={isFetching}
          onRefresh={() => void refetch()}
        />
      </div>

      {isFetching && !data ? (
        <div style={{ padding: "1rem" }} className="muted">加载中…</div>
      ) : (
        <div className="apps-layout">
          <AppListPanel
            rows={filteredRows}
            selectedAppId={selectedApp}
            onSelectApp={handleSelectApp}
            search={search}
            onSearchChange={setSearch}
            ownOnly={ownOnly}
            onOwnOnlyChange={setOwnOnly}
            showOwnOnly={userIsOwner}
            displayNames={displayNames}
            canCreateApp={canCreateApp}
            onNewApp={() => setShowNewApp(true)}
          />
          <DetailPanel
            app={selectedAppObj}
            snap={selectedSnap}
            release={release}
            releases={releases}
            user={user}
            displayNames={displayNames}
            onSaved={handleSaved}
          />
        </div>
      )}

      {showNewApp && release && (
        <NewAppDialog
          releases={releases}
          currentReleaseId={release.id}
          currentUsername={user?.username ?? ""}
          userRole={user?.role ?? ""}
          onClose={() => setShowNewApp(false)}
          onCreated={handleNewAppCreated}
        />
      )}
    </section>
  );
}
