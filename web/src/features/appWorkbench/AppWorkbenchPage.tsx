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
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import type { StatePayload, App, Snapshot, ReleaseSummary, AppAuditEntry, SnapshotTestDoc } from "../../types";
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
} from "./helpers";

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
// New-app dialog
// ---------------------------------------------------------------------------

interface NewAppDialogProps {
  releases: ReleaseSummary[];
  currentReleaseId: string;
  onClose: () => void;
  onCreated: (appId: string) => void;
}

function NewAppDialog({ releases, currentReleaseId, onClose, onCreated }: NewAppDialogProps) {
  const release = releases.find((r) => r.id === currentReleaseId) ?? null;
  const decisionOpts = newAppDecisionOptions(release);
  const [officialName, setOfficialName] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [gitBranch, setGitBranch] = useState("");
  const [docTarget, setDocTarget] = useState<"manual" | "ai4sci">("manual");
  const [decision, setDecision] = useState<string>(decisionOpts[0] ?? "release");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  async function handleCreate() {
    if (!officialName.trim()) return setErr("请填写官方 app/模型名称");
    if (!gitUrl.trim()) return setErr("请填写 Gerrit URL");
    if (!gitBranch.trim()) return setErr("请填写 Branch");
    setErr("");
    setSaving(true);
    try {
      const r = await apiPost<{ app_id: string }>("/api/apps/new", {
        release_id: currentReleaseId,
        official_name: officialName.trim(),
        git_url: gitUrl.trim(),
        git_branch: gitBranch.trim(),
        release_decision: decision,
        doc_target: docTarget,
      });
      onCreated(r.app_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "创建失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" data-testid="new-app-dialog">
      <div className="dialog-box">
        <div className="dialog-head"><h3>新增 App</h3></div>
        <div className="dialog-body">
          <div className="form">
            <label>官方名称
              <input className="input" value={officialName} onChange={(e) => setOfficialName(e.target.value)} data-testid="new-app-name" />
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
          {err && <p className="lerr">{err}</p>}
        </div>
        <div className="dialog-actions">
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" onClick={() => void handleCreate()} disabled={saving}>
            {saving ? "创建中…" : "创建"}
          </button>
        </div>
      </div>
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

  const [editMode, setEditMode] = useState(false);
  const [dirty, setDirty] = useState(false);

  // Form state (mirrors snapshot fields editable in legacy)
  const [form, setForm] = useState<FormState>(() => snap ? snapshotToForm(snap, app!) : emptyForm());
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState("");
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [auditEntries, setAuditEntries] = useState<AppAuditEntry[] | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);

  // Sync form when app selection changes
  React.useEffect(() => {
    if (snap && app) {
      setForm(snapshotToForm(snap, app));
      setEditMode(false);
      setDirty(false);
      setSaveErr("");
      setPendingFile(null);
      setAuditEntries(null);
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
    setSaving(true);
    setSaveErr("");
    try {
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

      // Decision sync: if decision changed, ask whether to propagate to later releases
      const currentDecision = snap.release_decision;
      let syncDecision = false;
      if (snapshotUpdate["release_decision"] !== currentDecision) {
        const idx = releases.findIndex((r) => r.id === release.id);
        const later = idx >= 0 ? releases.slice(idx + 1) : [];
        if (later.length > 0) {
          syncDecision = window.confirm(
            `是否把 release 决策「${snapshotUpdate["release_decision"] as string}」同步到 ${later.length} 个后续 release？\n\n点「确定」同步，「取消」仅更改本 release。`,
          );
        }
      }

      const result = await apiPost<{ snapshot?: Snapshot; missing_items?: unknown[] }>("/api/apps/update", {
        release_id: release.id,
        app_id: app.id,
        app: { git_url: form.git_url, git_branch: form.git_branch },
        snapshot: snapshotUpdate,
        sync_decision: syncDecision,
      });

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

      {/* Footer actions */}
      <div className="detail-foot">
        <span className="foot-note">{footNote}</span>
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
      if (!window.confirm("当前 App 详情有未保存修改或处于编辑状态，切换 App 会丢失这些修改。确认切换？")) return;
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
          onClose={() => setShowNewApp(false)}
          onCreated={handleNewAppCreated}
        />
      )}
    </section>
  );
}
