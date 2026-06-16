/**
 * CicdPage — CICD 工作台 tab.
 *
 * Mirrors index.html:857-972 (HTML) and index.html:3886-4857 (JS).
 *
 * Sub-panes:
 *   - 任务总览  (OverviewPane)    — all tasks, status filter, search
 *   - 我的CICD任务 (MyPane)       — tasks owned by current user
 *   - 待审批   (PendingPane)      — pending requests (RM/Admin can approve)
 *   - 最近申请  (RecentPane)      — recent requests with since_days + only_mine
 *   - 待交付   (DeliveryPane)     — delivery workflow (SPD/RM/Admin)
 *   - 已交付   (DeliveredPane)    — delivered history
 *
 * Role visibility (mirrors bindCicd index.html:4738-4754):
 *   SPD:  only 待交付 + 已交付 (delivery panes); no new task button
 *   Others: all panes visible; RM/Admin/Owner get delivery panes too
 *
 * On mount: fetches tasks + notifications + marks visited.
 * R2: no polling.  Only explicit refetch on user action.
 */

import React, { useState, useCallback, useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { RefreshBar } from "../../components/RefreshBar";
import { formatServerTime } from "../../lib/time";
import { useUiStore } from "../../store/uiStore";
import {
  CICD_TASKS_KEY,
  CICD_NOTIFICATIONS_KEY,
  fetchCicdTasks,
  fetchCicdTaskHistory,
  fetchCicdRequests,
  fetchCicdNotifications,
  fetchCicdDeliveries,
  submitCicdRequest,
  approveCicdRequest,
  rejectCicdRequest,
  cancelCicdRequest,
  deliverCicdRequest,
  returnDeliveryCicdRequest,
  reDispatchCicdRequest,
  applyReturnedCicdRequest,
  deleteCicdTask,
  abandonCicdTask,
  markCicdVisited,
} from "./cicdApi";
import type { CicdTask, CicdRequest } from "../../types";

// Re-export so consumers (tests, TabNav) can import from either location.
export { CICD_TASKS_KEY, CICD_NOTIFICATIONS_KEY };

// ---------------------------------------------------------------------------
// Label helpers (mirrors index.html:3897-3905)
// ---------------------------------------------------------------------------

const REQ_TYPE_LABEL: Record<string, string> = {
  create: "新建",
  modify: "修改",
  owner_transfer: "负责人变更",
};

/** Marks requests auto-created by App release-decision sync (R3 Ruling D)
 *  so reviewers can tell them apart from build-config (workbench) requests.
 *  Renders nothing for ordinary "cicd_workbench" requests. */
function OriginBadge({ origin }: { origin?: string }) {
  if (origin !== "release_decision_sync") return null;
  return (
    <span
      className="pill accent"
      style={{ fontSize: 11, marginLeft: 4 }}
      title="由 App 发布决策自动联动创建（Ruling D）"
    >
      同步联动
    </span>
  );
}

const REQ_STATUS_LABEL: Record<string, string> = {
  pending: "等待 RM 审批",
  approved: "已通过",
  rejected: "已拒绝",
  cancelled: "已取消",
};

const FIELD_LABEL: Record<string, string> = {
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

const DELIVERY_STATUS_LABEL: Record<string, string> = {
  pending: "待交付",
  delivered: "已交付",
  returned: "已退回",
};

// ---------------------------------------------------------------------------
// Pure UI helpers
// ---------------------------------------------------------------------------

function buildProductStr(arr: string[] | null | undefined): string {
  return (Array.isArray(arr) ? arr : []).join(", ") || "—";
}

function communityArtifactStr(arr: string[] | null | undefined): string {
  const items = Array.isArray(arr) ? arr : [];
  if (!items.length) return "—";
  return items
    .map((v) => (v === "image" ? "镜像" : v === "pkg" ? "软件包" : v))
    .join("、");
}

function ownerLabel(task: CicdTask): string {
  return task.owner_display || task.owner_username || "";
}

function userLabel(username: string, display?: string): string {
  return display && display !== username ? display : username;
}

// ---------------------------------------------------------------------------
// StatusPill
// ---------------------------------------------------------------------------

function StatusPill({ status }: { status: string }) {
  const cls =
    status === "Running" ? "ok" : status === "Stopped" ? "warnp" : "";
  return <span className={`pill ${cls}`}>{status}</span>;
}

// ---------------------------------------------------------------------------
// ReqStatusSpan
// ---------------------------------------------------------------------------

function ReqStatusSpan({ status }: { status: string }) {
  return (
    <span className={`cicd-req-status-${status}`}>
      {REQ_STATUS_LABEL[status] ?? status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// diff helpers (mirrors index.html:4162-4198)
// ---------------------------------------------------------------------------

interface DiffEntry {
  old: unknown;
  new: unknown;
}

function diffSummary(
  payload: Record<string, unknown>,
  reqType: string,
): React.ReactNode {
  if (reqType === "create") {
    const name = (payload?.app_name as string) || "";
    return (
      <>
        新建任务：<b>{name}</b>
      </>
    );
  }
  if (reqType === "owner_transfer") {
    const ch = (payload?.owner_username as DiffEntry) || { old: "", new: "" };
    return (
      <>
        负责人：{String(ch.old ?? "")} → {String(ch.new ?? "")}
      </>
    );
  }
  if (!payload || !Object.keys(payload).length) return "—";
  return Object.keys(payload)
    .map((field) => FIELD_LABEL[field] ?? field)
    .join(", ");
}

function DiffTable({
  payload,
  reqType,
}: {
  payload: Record<string, unknown>;
  reqType: string;
}) {
  if (reqType === "create") {
    const rows = Object.entries(payload ?? {}).filter(([k]) => FIELD_LABEL[k]);
    return (
      <table className="cicd-diff-table">
        <thead>
          <tr>
            <th>字段</th>
            <th colSpan={2}>值</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, v]) => {
            const val = Array.isArray(v) ? v.join(", ") : String(v ?? "");
            return (
              <tr key={k}>
                <td>{FIELD_LABEL[k] ?? k}</td>
                <td colSpan={2} className="cicd-diff-new">
                  {val}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  }
  return (
    <table className="cicd-diff-table">
      <thead>
        <tr>
          <th>字段</th>
          <th>原值</th>
          <th>新值</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(payload ?? {}).map(([field, ch]) => {
          const entry = ch as DiffEntry;
          const oldV = Array.isArray(entry?.old)
            ? entry.old.join(", ")
            : String(entry?.old ?? "");
          const newV = Array.isArray(entry?.new)
            ? entry.new.join(", ")
            : String(entry?.new ?? "");
          return (
            <tr key={field}>
              <td>{FIELD_LABEL[field] ?? field}</td>
              <td className="cicd-diff-old">{oldV || <em>(空)</em>}</td>
              <td className="cicd-diff-new">{newV || <em>(空)</em>}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Task form dialog (new + modify)
// ---------------------------------------------------------------------------

interface TaskFormValues {
  app_name: string;
  app_version: string;
  owner_username: string;
  repo_type: string;
  repo_name: string;
  branch: string;
  build_product: string[];
  community_artifact: string[];
  build_image: string;
  test_timeout: number;
  status: string;
  notes: string;
  [key: string]: unknown;
}

function emptyTaskForm(username: string): TaskFormValues {
  return {
    app_name: "",
    app_version: "",
    owner_username: username,
    repo_type: "git",
    repo_name: "",
    branch: "",
    build_product: [],
    community_artifact: [],
    build_image: "",
    test_timeout: 40,
    status: "Running",
    notes: "",
  };
}

function taskToForm(t: CicdTask): TaskFormValues {
  return {
    app_name: t.app_name,
    app_version: t.app_version,
    owner_username: t.owner_username,
    repo_type: t.repo_type,
    repo_name: t.repo_name,
    branch: t.branch,
    build_product: t.build_product ?? [],
    community_artifact: t.community_artifact ?? [],
    build_image: t.build_image,
    test_timeout: t.test_timeout ?? 40,
    status: t.status,
    notes: t.notes,
  };
}

const BUILD_PRODUCT_OPTIONS = ["maca", "x86", "arm"];
const COMMUNITY_ARTIFACT_OPTIONS = [
  { value: "image", label: "镜像" },
  { value: "pkg", label: "软件包" },
];

interface TaskFormDialogProps {
  task: CicdTask | null;     // null = new task
  username: string;
  onSubmitted: () => void;
  onClose: () => void;
}

function TaskFormDialog({
  task,
  username,
  onSubmitted,
  onClose,
}: TaskFormDialogProps) {
  const [form, setForm] = useState<TaskFormValues>(() =>
    task ? taskToForm(task) : emptyTaskForm(username),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const isRepo = form.repo_type === "repo";

  function toggleArr(arr: string[], val: string): string[] {
    return arr.includes(val) ? arr.filter((x) => x !== val) : [...arr, val];
  }

  async function handleSubmit() {
    if (!form.app_name.trim()) {
      setError("请填写项目名称");
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (!task) {
        // New task
        const payload = { ...form };
        await submitCicdRequest({
          task_id: null,
          request_type: "create",
          payload: payload,
        });
      } else {
        // Modify: build diff
        const orig = taskToForm(task);
        const diff: Record<string, { old: unknown; new: unknown }> = {};
        for (const [key, newVal] of Object.entries(form)) {
          const oldVal = (orig as Record<string, unknown>)[key];
          const oldStr = Array.isArray(oldVal) ? oldVal.join(",") : String(oldVal ?? "");
          const newStr = Array.isArray(newVal) ? (newVal as string[]).join(",") : String(newVal ?? "");
          if (oldStr !== newStr) {
            diff[key] = { old: oldVal, new: newVal };
          }
        }
        if (!Object.keys(diff).length) {
          setError("没有任何字段发生变化");
          setSaving(false);
          return;
        }
        // Cancel existing pending if any (mirrors index.html:4357-4363)
        if (task.has_pending) {
          try {
            const pend = await fetchCicdRequests({
              taskId: task.id,
              status: "pending",
            });
            for (const r of pend.requests ?? []) {
              await cancelCicdRequest({ request_id: r.id }).catch(() => {});
            }
          } catch { /* cancelling stale pending requests is best-effort; ignore errors */ }
        }
        await submitCicdRequest({
          task_id: task.id,
          request_type: "modify",
          payload: diff,
        });
      }
      onSubmitted();
      onClose();
    } catch (e) {
      setError("提交失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog-card" style={{ maxWidth: 560 }}>
        <h2>{task ? `修改 CICD 任务 ${task.id}` : "新建 CICD 任务"}</h2>
        {task?.has_pending && (
          <div className="lerr" style={{ marginBottom: 8 }}>
            警告：该任务有待审批的申请，提交后将自动取消旧申请。
          </div>
        )}
        <div className="dialog-body" style={{ display: "grid", gap: 8 }}>
          <label>
            项目名称 <span className="required">*</span>
            <input
              className="input"
              value={form.app_name}
              onChange={(e) => setForm({ ...form, app_name: e.target.value })}
              placeholder="例：amber"
            />
          </label>
          <label>
            项目版本
            <input
              className="input"
              value={form.app_version}
              onChange={(e) => setForm({ ...form, app_version: e.target.value })}
            />
          </label>
          <label>
            负责人
            <input
              className="input"
              value={form.owner_username}
              onChange={(e) => setForm({ ...form, owner_username: e.target.value })}
            />
          </label>
          <label>
            仓库类型
            <select
              className="input"
              value={form.repo_type}
              onChange={(e) => {
                const v = e.target.value;
                setForm({
                  ...form,
                  repo_type: v,
                  branch: v === "repo" ? "master" : form.branch,
                });
              }}
            >
              <option value="git">git</option>
              <option value="repo">repo</option>
            </select>
          </label>
          <label>
            仓库名
            <input
              className="input"
              value={form.repo_name}
              onChange={(e) => setForm({ ...form, repo_name: e.target.value })}
            />
          </label>
          <label>
            分支
            <input
              className="input"
              value={isRepo ? "master" : form.branch}
              disabled={isRepo}
              onChange={(e) => setForm({ ...form, branch: e.target.value })}
            />
          </label>
          <div>
            <div className="field-label">构建产物</div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              {BUILD_PRODUCT_OPTIONS.map((v) => (
                <label key={v} className="check">
                  <input
                    type="checkbox"
                    checked={form.build_product.includes(v)}
                    onChange={() =>
                      setForm({
                        ...form,
                        build_product: toggleArr(form.build_product, v),
                      })
                    }
                  />{" "}
                  {v}
                </label>
              ))}
            </div>
          </div>
          <div>
            <div className="field-label">开发者社区产物</div>
            <div className="row" style={{ gap: 8 }}>
              {COMMUNITY_ARTIFACT_OPTIONS.map(({ value, label }) => (
                <label key={value} className="check">
                  <input
                    type="checkbox"
                    checked={form.community_artifact.includes(value)}
                    onChange={() =>
                      setForm({
                        ...form,
                        community_artifact: toggleArr(form.community_artifact, value),
                      })
                    }
                  />{" "}
                  {label}
                </label>
              ))}
            </div>
          </div>
          <label>
            构建依赖镜像
            <input
              className="input"
              value={form.build_image}
              onChange={(e) => setForm({ ...form, build_image: e.target.value })}
            />
          </label>
          <label>
            超时(min)
            <input
              className="input"
              type="number"
              min={1}
              value={form.test_timeout}
              onChange={(e) =>
                setForm({ ...form, test_timeout: parseInt(e.target.value) || 40 })
              }
            />
          </label>
          {/* Status is read-only — changed only via decision-sync or RM abandon action */}
          <label>
            状态
            <input className="input" value={form.status} disabled readOnly />
          </label>
          <label>
            备注
            <input
              className="input"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
          </label>
          {error && <div className="lerr">{error}</div>}
        </div>
        <div className="row dialog-actions" style={{ justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <button className="btn" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button className="btn primary" onClick={handleSubmit} disabled={saving}>
            {saving ? "提交中…" : "提交申请"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Approve dialog
// ---------------------------------------------------------------------------

interface ApproveDialogProps {
  req: CicdRequest;
  tasks: CicdTask[];
  onDone: () => void;
  onClose: () => void;
}

function ApproveDialog({ req, tasks, onDone, onClose }: ApproveDialogProps) {
  const { user } = useAuth();
  const isSelfApproval = req.submitter === (user?.username ?? "");
  const [note, setNote] = useState("");
  const [approvalMode, setApprovalMode] = useState<"immediate" | "dispatch_spd">("immediate");
  const [jiraMode, setJiraMode] = useState<"none" | "auto" | "manual">("none");
  const [jiraIdImmediate, setJiraIdImmediate] = useState("");
  const [jiraIdManual, setJiraIdManual] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const payload = (req.payload ?? {}) as Record<string, unknown>;
  const taskName =
    (payload.app_name as string) ||
    tasks.find((t) => t.id === req.task_id)?.app_name ||
    req.task_id ||
    "";
  const jiraTitle =
    req.request_type === "create"
      ? tasks.some((t) => t.app_name === taskName)
        ? `[Append] ${taskName} 【追加发布新版本】`
        : `[New] ${taskName} 【新发布项目】`
      : `[Change] ${taskName} 【修改项目】`;

  async function handleApprove() {
    setSaving(true);
    setError("");
    try {
      let jira_id = "";
      let jira_auto_created = 0;
      if (approvalMode === "immediate") {
        jira_id = jiraIdImmediate.trim();
      } else {
        if (jiraMode === "auto") {
          jira_auto_created = 1;
        } else if (jiraMode === "manual") {
          jira_id = jiraIdManual.trim();
        }
      }
      await approveCicdRequest({
        request_id: req.id,
        review_note: note.trim(),
        approval_mode: approvalMode,
        jira_id,
        jira_auto_created,
      });
      onDone();
      onClose();
    } catch (e) {
      setError("审批失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
    }
  }

  async function handleReject() {
    if (!note.trim()) {
      setError("拒绝必须填写理由");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await rejectCicdRequest({ request_id: req.id, review_note: note.trim() });
      onDone();
      onClose();
    } catch (e) {
      setError("拒绝失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog-card" style={{ maxWidth: 580 }}>
        <h2>审批申请 #{req.id}</h2>
        <div className="dialog-body">
          <div className="small muted" style={{ marginBottom: 8 }}>
            提交人：{userLabel(req.submitter, req.submitter_display)}
            {isSelfApproval && (
              <span className="pill accent" style={{ fontSize: 11, marginLeft: 4 }}>
                本人提交
              </span>
            )}{" "}
            &nbsp;|&nbsp;
            类型：{REQ_TYPE_LABEL[req.request_type] ?? req.request_type}
            <OriginBadge origin={req.origin} /> &nbsp;|&nbsp;
            {req.task_id ? `任务：${req.task_id}` : "新建任务"}
          </div>
          <DiffTable
            payload={payload}
            reqType={req.request_type}
          />
          <div style={{ marginTop: 12 }}>
            <div className="field-label">审批意见（拒绝时必填）</div>
            <textarea
              className="input"
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              style={{ width: "100%", resize: "vertical" }}
            />
          </div>
          <div style={{ marginTop: 8 }}>
            <div className="field-label">审批模式</div>
            <label className="check">
              <input
                type="radio"
                name="approvalMode"
                value="immediate"
                checked={approvalMode === "immediate"}
                onChange={() => setApprovalMode("immediate")}
              />{" "}
              立即生效
            </label>
            {" "}
            <label className="check">
              <input
                type="radio"
                name="approvalMode"
                value="dispatch_spd"
                checked={approvalMode === "dispatch_spd"}
                onChange={() => setApprovalMode("dispatch_spd")}
              />{" "}
              下发 SPD
            </label>
          </div>
          {approvalMode === "immediate" && (
            <div style={{ marginTop: 8 }}>
              <label className="field-label">
                Jira ID（选填）
                <input
                  className="input sm"
                  value={jiraIdImmediate}
                  placeholder="例：HPC-123"
                  onChange={(e) => setJiraIdImmediate(e.target.value)}
                  style={{ marginLeft: 8, width: 160 }}
                />
              </label>
            </div>
          )}
          {approvalMode === "dispatch_spd" && (
            <div style={{ marginTop: 8 }}>
              <div className="field-label">Jira 处理方式</div>
              <label className="check">
                <input
                  type="radio"
                  name="jiraMode"
                  value="none"
                  checked={jiraMode === "none"}
                  onChange={() => setJiraMode("none")}
                />{" "}
                不创建
              </label>{" "}
              <label className="check">
                <input
                  type="radio"
                  name="jiraMode"
                  value="auto"
                  checked={jiraMode === "auto"}
                  onChange={() => setJiraMode("auto")}
                />{" "}
                自动创建
              </label>{" "}
              <label className="check">
                <input
                  type="radio"
                  name="jiraMode"
                  value="manual"
                  checked={jiraMode === "manual"}
                  onChange={() => setJiraMode("manual")}
                />{" "}
                手动填写
              </label>
              {jiraMode === "auto" && (
                <div className="small muted" style={{ marginTop: 4 }}>
                  将自动创建 Jira：{jiraTitle}
                </div>
              )}
              {jiraMode === "manual" && (
                <input
                  className="input sm"
                  value={jiraIdManual}
                  placeholder="例：HPC-123"
                  onChange={(e) => setJiraIdManual(e.target.value)}
                  style={{ marginTop: 4, width: 160 }}
                />
              )}
            </div>
          )}
          {error && <div className="lerr" style={{ marginTop: 8 }}>{error}</div>}
        </div>
        <div className="row dialog-actions" style={{ justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <button className="btn" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button className="btn danger" onClick={handleReject} disabled={saving}>
            拒绝
          </button>
          <button className="btn primary" onClick={handleApprove} disabled={saving}>
            {saving ? "…" : "通过"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// History dialog
// ---------------------------------------------------------------------------

function HistoryDialog({
  taskId,
  tasks,
  onClose,
}: {
  taskId: string;
  tasks: CicdTask[];
  onClose: () => void;
}) {
  const task = tasks.find((t) => t.id === taskId);
  const { data, isLoading, error } = useQuery({
    queryKey: ["cicd", "history", taskId],
    queryFn: () => fetchCicdTaskHistory(taskId),
    staleTime: 0,
    refetchOnMount: true,
  });

  const title = `历史记录：${taskId}${task ? " · " + task.app_name : ""}`;
  const history = data?.history ?? [];

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog-card" style={{ maxWidth: 660, maxHeight: "80vh", overflow: "auto" }}>
        <h2>{title}</h2>
        <div className="dialog-body">
          {isLoading && <div className="muted">加载中…</div>}
          {error && (
            <div className="lerr">
              加载失败：{error instanceof Error ? error.message : String(error)}
            </div>
          )}
          {!isLoading && !error && !history.length && (
            <div className="muted empty">暂无历史记录</div>
          )}
          {[...history].reverse().map((h) => (
            <div key={h.id} className="cicd-history-entry">
              <div className="cicd-history-meta">
                <b>{REQ_TYPE_LABEL[h.request_type] ?? h.request_type}</b>
                <OriginBadge origin={h.origin} />
                &nbsp;·&nbsp; 提交人：{userLabel(h.submitter, h.submitter_display)}
                &nbsp;·&nbsp; {formatServerTime(h.reviewed_at || h.submitted_at || "")}
                {h.is_self_approved
                  ? <>&nbsp;·&nbsp;<span className="pill accent" style={{ fontSize: 11 }}>RM 本人提交</span></>
                  : h.reviewer
                  ? <>&nbsp;·&nbsp; 审批人：{h.reviewer}</>
                  : null}
                {h.review_note && (
                  <>&nbsp;·&nbsp; 备注：<b>{h.review_note}</b></>
                )}
              </div>
              <DiffTable
                payload={(h.payload ?? {}) as Record<string, unknown>}
                reqType={h.request_type}
              />
            </div>
          ))}
        </div>
        <div className="row dialog-actions" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button className="btn" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail dialog (read-only — mirrors index.html:4506-4533)
// ---------------------------------------------------------------------------

function DetailDialog({
  req,
  tasks,
  onClose,
}: {
  req: CicdRequest;
  tasks: CicdTask[];
  onClose: () => void;
}) {
  const payload = (req.payload ?? {}) as Record<string, unknown>;
  const jiraHref = req.jira_id
    ? `http://jira.metax-tech.com/browse/${req.jira_id}`
    : null;

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog-card" style={{ maxWidth: 580 }}>
        <h2>申请详情 #{req.id}</h2>
        <div className="dialog-body">
          <div className="small muted" style={{ marginBottom: 8 }}>
            提交人：<b>{req.submitter_display || req.submitter}</b>
            &nbsp;|&nbsp; 类型：{REQ_TYPE_LABEL[req.request_type] ?? req.request_type}
            <OriginBadge origin={req.origin} />
            &nbsp;|&nbsp; {req.task_id ? `任务：${req.task_id}` : "新建任务"}
            &nbsp;|&nbsp; 状态：<ReqStatusSpan status={req.status} />
            {req.jira_id && jiraHref && (
              <>
                &nbsp;|&nbsp; Jira：{" "}
                <a href={jiraHref} target="_blank" rel="noopener noreferrer">
                  {req.jira_id}
                </a>
              </>
            )}
            {req.delivery_status && (
              <>
                &nbsp;|&nbsp; 交付状态：
                <b>{DELIVERY_STATUS_LABEL[req.delivery_status] ?? req.delivery_status}</b>
              </>
            )}
          </div>
          {req.reviewer && (
            <div className="small muted" style={{ marginBottom: 8 }}>
              审批人：{req.reviewer}
              &nbsp;|&nbsp; 审批时间：{formatServerTime(req.reviewed_at || "")}
              {req.review_note && (
                <>&nbsp;|&nbsp; 备注：<b>{req.review_note}</b></>
              )}
            </div>
          )}
          {req.request_type === "modify" && (() => {
            const ctx = {
              app_name: req.task_app_name || "",
              app_version: req.task_app_version || "",
              repo_name: req.task_repo_name || "",
              branch: req.task_branch || "",
            };
            const task = tasks.find((t) => t.id === req.task_id);
            const items: [string, string][] = [
              ["项目名称", task?.app_name || ctx.app_name || "—"],
              ["版本", task?.app_version || ctx.app_version || "—"],
              ["仓库名", task?.repo_name || ctx.repo_name || "—"],
              ["分支", task?.branch || ctx.branch || "—"],
            ];
            return (
              <div className="cicd-context" style={{ marginBottom: 8 }}>
                <div className="cicd-context-title">任务基础信息</div>
                <div className="cicd-context-grid">
                  {items.map(([label, value]) => (
                    <div key={label} className="cicd-context-item">
                      <div className="cicd-context-label">{label}</div>
                      <div className="cicd-context-value">{value}</div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}
          <DiffTable payload={payload} reqType={req.request_type} />
        </div>
        <div className="row dialog-actions" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button className="btn" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// OverviewPane — 任务总览
// ---------------------------------------------------------------------------

interface OverviewPaneProps {
  tasks: CicdTask[];
  canSubmit: boolean;
  canApprove: boolean;
  overviewFilter: string;
  onFilterChange: (f: string) => void;
  onNewTask: () => void;
  onEdit: (t: CicdTask) => void;
  onHistory: (t: CicdTask) => void;
  onDelete: (t: CicdTask) => void;
  onAbandon: (t: CicdTask) => void;
}

function OverviewPane({
  tasks,
  canSubmit,
  canApprove,
  overviewFilter,
  onFilterChange,
  onNewTask,
  onEdit,
  onHistory,
  onDelete,
  onAbandon,
}: OverviewPaneProps) {
  const [search, setSearch] = useState("");
  const q = search.toLowerCase();

  let filtered = tasks;
  if (overviewFilter) filtered = filtered.filter((t) => t.status === overviewFilter);
  if (q)
    filtered = filtered.filter(
      (t) =>
        t.app_name.toLowerCase().includes(q) ||
        ownerLabel(t).toLowerCase().includes(q) ||
        t.owner_username.toLowerCase().includes(q),
    );

  const STATUS_FILTERS = [
    { label: "Running", value: "Running" },
    { label: "全部", value: "" },
    { label: "Abandoned", value: "Abandoned" },
  ];

  return (
    <div className="cicd-pane active">
      <div className="cicd-toolbar">
        <div className="cicd-filters" id="cicdStatusFilters">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              className={`cicd-filter-btn${overviewFilter === f.value ? " active" : ""}`}
              onClick={() => onFilterChange(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <input
          className="input sm cicd-search"
          placeholder="搜索 app / 负责人"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span style={{ flex: 1 }} />
        {canSubmit && (
          <button className="btn sm primary" onClick={onNewTask}>
            + 新建 CICD 任务
          </button>
        )}
      </div>
      <div className="cicd-table-wrap">
        <table className="cicd-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>项目名称</th>
              <th>版本</th>
              <th>仓库类型</th>
              <th>仓库名</th>
              <th>分支</th>
              <th>构建产物</th>
              <th>开发者社区产物</th>
              <th>构建依赖镜像</th>
              <th>超时(min)</th>
              <th>负责人</th>
              <th>状态</th>
              <th>备注</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td
                  colSpan={14}
                  className="empty"
                  style={{ textAlign: "center", color: "var(--muted)", padding: 30 }}
                >
                  暂无任务
                </td>
              </tr>
            ) : (
              filtered.map((t) => (
                <tr key={t.id}>
                  <td className="cicd-id">{t.id}</td>
                  <td>
                    <b>{t.app_name}</b>
                    {t.has_pending && (
                      <span className="cicd-pending-icon" title="有待审批的修改申请">
                        {" "}✏️
                      </span>
                    )}
                    {t.has_pending_delivery && (
                      <span title="待 SPD 交付中"> ⏳</span>
                    )}
                  </td>
                  <td>{t.app_version}</td>
                  <td>{t.repo_type}</td>
                  <td style={{ maxWidth: 160, wordBreak: "break-all" }}>
                    {t.repo_name}
                  </td>
                  <td>{t.branch}</td>
                  <td>{buildProductStr(t.build_product)}</td>
                  <td>{communityArtifactStr(t.community_artifact)}</td>
                  <td style={{ maxWidth: 160, wordBreak: "break-all" }}>
                    {t.build_image}
                  </td>
                  <td style={{ textAlign: "center" }}>{t.test_timeout}</td>
                  <td>{ownerLabel(t)}</td>
                  <td>
                    <StatusPill status={t.status} />
                  </td>
                  <td style={{ maxWidth: 160 }}>{t.notes}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {canSubmit && t.status !== "Abandoned" && (
                      <button className="btn sm" onClick={() => onEdit(t)}>
                        修改
                      </button>
                    )}{" "}
                    <button className="btn sm" onClick={() => onHistory(t)}>
                      历史
                    </button>{" "}
                    {canApprove && t.status === "Stopped" && (
                      <button
                        className="btn sm warn"
                        onClick={() => onAbandon(t)}
                        data-testid={`abandon-btn-${t.id}`}
                      >
                        废弃/退役
                      </button>
                    )}{" "}
                    {canApprove && t.status === "Abandoned" && (
                      <button
                        className="btn sm danger"
                        onClick={() => onDelete(t)}
                      >
                        删除
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MyPane — 我的CICD任务
// ---------------------------------------------------------------------------

interface MyPaneProps {
  tasks: CicdTask[];
  username: string;
  canSubmit: boolean;
  onNewTask: () => void;
  onEdit: (t: CicdTask) => void;
  onHistory: (t: CicdTask) => void;
}

function MyPane({ tasks, username, canSubmit, onNewTask, onEdit, onHistory }: MyPaneProps) {
  const [search, setSearch] = useState("");
  const q = search.toLowerCase();
  let myTasks = tasks.filter((t) => t.owner_username === username);
  if (q) myTasks = myTasks.filter((t) => t.app_name.toLowerCase().includes(q));

  return (
    <div className="cicd-pane">
      <div className="cicd-toolbar">
        <input
          className="input sm cicd-search"
          placeholder="搜索 app"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span style={{ flex: 1 }} />
        {canSubmit && (
          <button className="btn sm primary" onClick={onNewTask}>
            + 新建 CICD 任务
          </button>
        )}
      </div>
      <div className="cicd-table-wrap">
        <table className="cicd-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>项目名称</th>
              <th>版本</th>
              <th>仓库类型</th>
              <th>仓库名</th>
              <th>分支</th>
              <th>构建产物</th>
              <th>开发者社区产物</th>
              <th>状态</th>
              <th>备注</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {myTasks.length === 0 ? (
              <tr>
                <td
                  colSpan={11}
                  className="empty"
                  style={{ textAlign: "center", color: "var(--muted)", padding: 30 }}
                >
                  暂无我负责的 CICD 任务
                </td>
              </tr>
            ) : (
              myTasks.map((t) => (
                <tr key={t.id}>
                  <td className="cicd-id">{t.id}</td>
                  <td>
                    <b>{t.app_name}</b>
                    {t.has_pending && (
                      <span className="pill accent" style={{ fontSize: 11, marginLeft: 4 }}>
                        待审批
                      </span>
                    )}
                  </td>
                  <td>{t.app_version}</td>
                  <td>{t.repo_type}</td>
                  <td style={{ maxWidth: 140, wordBreak: "break-all" }}>
                    {t.repo_name}
                  </td>
                  <td>{t.branch}</td>
                  <td>{buildProductStr(t.build_product)}</td>
                  <td>{communityArtifactStr(t.community_artifact)}</td>
                  <td>
                    <StatusPill status={t.status} />
                  </td>
                  <td style={{ maxWidth: 120 }}>{t.notes}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {canSubmit && t.status !== "Abandoned" && (
                      <button className="btn sm" onClick={() => onEdit(t)}>
                        修改
                      </button>
                    )}{" "}
                    <button className="btn sm" onClick={() => onHistory(t)}>
                      历史
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PendingPane — 待审批
// ---------------------------------------------------------------------------

interface PendingPaneProps {
  username: string;
  canApprove: boolean;
  tasks: CicdTask[];
  onApprove: (req: CicdRequest) => void;
  onDetail: (req: CicdRequest) => void;
  onCancelled: () => void;
  pendingCount: (n: number) => void;
}

function PendingPane({
  username,
  canApprove,
  tasks: _tasks,
  onApprove,
  onDetail,
  onCancelled,
  pendingCount,
}: PendingPaneProps) {
  const [search, setSearch] = useState("");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["cicd", "requests", "pending"],
    queryFn: () => fetchCicdRequests({ status: "pending" }),
    staleTime: 0,
    refetchOnMount: true,
  });

  // Memoize so the array reference is stable across renders, satisfying
  // useEffect's exhaustive-deps rule (react-hooks/exhaustive-deps).
  const reqs = useMemo(() => data?.requests ?? [], [data]);
  const q = search.toLowerCase();
  const filtered = reqs.filter(
    (r) =>
      !q ||
      ((r.payload as Record<string, unknown>)?.app_name as string || "")
        .toLowerCase()
        .includes(q) ||
      r.submitter.toLowerCase().includes(q) ||
      userLabel(r.submitter, r.submitter_display).toLowerCase().includes(q) ||
      (r.task_id ?? "").toLowerCase().includes(q),
  );

  // Update subtab badge count
  useEffect(() => {
    const own = canApprove
      ? reqs.length
      : reqs.filter((r) => r.submitter === username).length;
    pendingCount(own);
  }, [reqs, canApprove, username, pendingCount]);

  async function handleCancel(r: CicdRequest) {
    if (!confirm("确认取消该申请？")) return;
    try {
      await cancelCicdRequest({ request_id: r.id });
      void refetch();
      onCancelled();
    } catch (e) {
      alert("取消失败：" + (e instanceof Error ? e.message : String(e)));
    }
  }

  return (
    <div className="cicd-pane">
      <div className="cicd-toolbar">
        <input
          className="input sm cicd-search"
          placeholder="搜索 app / 提交人"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="cicd-table-wrap">
        {isLoading && <div className="muted" style={{ padding: 14 }}>加载中…</div>}
        {error && (
          <div className="lerr" style={{ padding: 14 }}>
            加载失败：{error instanceof Error ? error.message : String(error)}
          </div>
        )}
        {!isLoading && (
          <table className="cicd-table">
            <thead>
              <tr>
                <th>申请ID</th>
                <th>类型</th>
                <th>任务ID</th>
                <th>提交人</th>
                <th>提交时间</th>
                <th>变更内容</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className="empty"
                    style={{ textAlign: "center", color: "var(--muted)", padding: 30 }}
                  >
                    暂无待审批申请
                  </td>
                </tr>
              ) : (
                filtered.map((r) => {
                  const isMyReq = r.submitter === username;
                  const payload = (r.payload ?? {}) as Record<string, unknown>;
                  return (
                    <tr key={r.id}>
                      <td className="cicd-id">#{r.id}</td>
                      <td>
                        {REQ_TYPE_LABEL[r.request_type] ?? r.request_type}
                        <OriginBadge origin={r.origin} />
                      </td>
                      <td className="cicd-id">{r.task_id ?? "(新建)"}</td>
                      <td>{userLabel(r.submitter, r.submitter_display)}</td>
                      <td className="small muted">
                        {formatServerTime(r.submitted_at ?? "")}
                      </td>
                      <td style={{ maxWidth: 220, fontSize: 12 }}>
                        {diffSummary(payload, r.request_type)}
                      </td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {canApprove && (
                          <button
                            className="btn sm primary"
                            onClick={() => onApprove(r)}
                          >
                            审批
                          </button>
                        )}{" "}
                        {(isMyReq || canApprove) && (
                          <button
                            className="btn sm warn"
                            onClick={() => handleCancel(r)}
                          >
                            取消
                          </button>
                        )}{" "}
                        <button
                          className="btn sm"
                          onClick={() => onDetail(r)}
                        >
                          详情
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RecentPane — 最近申请
// ---------------------------------------------------------------------------

interface RecentPaneProps {
  username: string;
  sinceDays: number;
  onSinceDaysChange: (d: number) => void;
  tasks: CicdTask[];
  onReEdit: (req: CicdRequest) => void;
  onDetail: (req: CicdRequest) => void;
}

function RecentPane({
  username,
  sinceDays,
  onSinceDaysChange,
  onDetail,
  onReEdit,
}: RecentPaneProps) {
  const [search, setSearch] = useState("");
  const [onlyMine, setOnlyMine] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["cicd", "requests", "recent", sinceDays, onlyMine],
    queryFn: () => fetchCicdRequests({ sinceDays, onlyMine }),
    staleTime: 0,
    refetchOnMount: true,
  });

  const reqs = data?.requests ?? [];
  const q = search.toLowerCase();
  const filtered = reqs.filter(
    (r) =>
      !q ||
      ((r.payload as Record<string, unknown>)?.app_name as string || "")
        .toLowerCase()
        .includes(q) ||
      r.submitter.toLowerCase().includes(q) ||
      userLabel(r.submitter, r.submitter_display).toLowerCase().includes(q) ||
      (r.task_id ?? "").toLowerCase().includes(q),
  );

  const TIME_FILTERS = [
    { label: "近1个月", days: 30 },
    { label: "近3个月", days: 90 },
    { label: "近半年", days: 180 },
    { label: "全部", days: 0 },
  ];

  return (
    <div className="cicd-pane">
      <div className="cicd-toolbar">
        <div className="cicd-filters">
          {TIME_FILTERS.map((f) => (
            <button
              key={f.days}
              className={`cicd-filter-btn${sinceDays === f.days ? " active" : ""}`}
              onClick={() => onSinceDaysChange(f.days)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label className="check" style={{ marginLeft: 10 }}>
          <input
            type="checkbox"
            checked={onlyMine}
            onChange={(e) => setOnlyMine(e.target.checked)}
          />{" "}
          只看我的
        </label>
        <input
          className="input sm cicd-search"
          placeholder="搜索 app / 提交人"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ marginLeft: 8 }}
        />
      </div>
      <div className="cicd-table-wrap">
        {isLoading && <div className="muted" style={{ padding: 14 }}>加载中…</div>}
        {error && (
          <div className="lerr" style={{ padding: 14 }}>
            加载失败：{error instanceof Error ? error.message : String(error)}
          </div>
        )}
        {!isLoading && (
          <table className="cicd-table">
            <thead>
              <tr>
                <th>申请ID</th>
                <th>类型</th>
                <th>任务ID</th>
                <th>提交人</th>
                <th>提交时间</th>
                <th>状态</th>
                <th>审批人</th>
                <th>审批备注</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td
                    colSpan={9}
                    className="empty"
                    style={{ textAlign: "center", color: "var(--muted)", padding: 30 }}
                  >
                    暂无申请记录
                  </td>
                </tr>
              ) : (
                filtered.map((r) => {
                  const canReEdit =
                    r.status === "rejected" && r.submitter === username;
                  return (
                    <tr key={r.id}>
                      <td className="cicd-id">#{r.id}</td>
                      <td>
                        {REQ_TYPE_LABEL[r.request_type] ?? r.request_type}
                        <OriginBadge origin={r.origin} />
                      </td>
                      <td className="cicd-id">{r.task_id ?? "(新建)"}</td>
                      <td>{userLabel(r.submitter, r.submitter_display)}</td>
                      <td className="small muted">
                        {formatServerTime(r.submitted_at ?? "")}
                      </td>
                      <td>
                        <ReqStatusSpan status={r.status} />
                      </td>
                      <td className="small muted">
                        {r.reviewer ? userLabel(r.reviewer) : "—"}
                      </td>
                      <td style={{ maxWidth: 160, fontSize: 12 }}>
                        {r.review_note || "—"}
                      </td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {canReEdit && (
                          <button
                            className="btn sm"
                            onClick={() => onReEdit(r)}
                          >
                            重新编辑
                          </button>
                        )}{" "}
                        <button
                          className="btn sm"
                          onClick={() => onDetail(r)}
                        >
                          详情
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DeliveryPane — 待交付 + 已交付 combined (via status param)
// ---------------------------------------------------------------------------

interface DeliveryPaneProps {
  username: string;
  role: string;
  delivered: boolean;  // false = pending/returned; true = delivered
  tasks: CicdTask[];
  onDetail: (req: CicdRequest) => void;
  onRefreshed: () => void;
  deliveryCount?: (n: number) => void;
}

function DeliveryPane({
  role,
  delivered,
  tasks: _tasks,
  onDetail,
  onRefreshed,
  deliveryCount,
}: DeliveryPaneProps) {
  const [search, setSearch] = useState("");
  const status = delivered
    ? "delivered"
    : role === "SPD"
    ? "pending"
    : "pending_or_returned";

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["cicd", "deliveries", status],
    queryFn: () => fetchCicdDeliveries(status),
    staleTime: 0,
    refetchOnMount: true,
  });

  const deliveries = data?.deliveries ?? [];
  const q = search.toLowerCase();
  const filtered = deliveries.filter(
    (d) =>
      !q ||
      (d.task_app_name ?? "").toLowerCase().includes(q) ||
      (d.submitter ?? "").toLowerCase().includes(q) ||
      userLabel(d.submitter, d.submitter_display).toLowerCase().includes(q) ||
      (d.task_id ?? "").toLowerCase().includes(q),
  );

  useEffect(() => {
    if (!delivered && deliveryCount) {
      deliveryCount(deliveries.length);
    }
  }, [deliveries.length, delivered, deliveryCount]);

  async function handleDeliver(id: number) {
    if (!confirm(`确认交付申请 #${id}？此操作将实际执行 CICD 配置变更。`)) return;
    try {
      await deliverCicdRequest({ request_id: id });
      void refetch();
      onRefreshed();
    } catch (e) {
      alert("交付失败：" + (e instanceof Error ? e.message : String(e)));
    }
  }

  async function handleReturn(id: number) {
    const reason = prompt("请填写退回原因（必填）：");
    if (reason === null) return;
    if (!reason.trim()) {
      alert("退回原因不能为空");
      return;
    }
    try {
      await returnDeliveryCicdRequest({ request_id: id, reason: reason.trim() });
      void refetch();
    } catch (e) {
      alert("退回失败：" + (e instanceof Error ? e.message : String(e)));
    }
  }

  async function handleReDispatch(id: number) {
    if (!confirm(`重新下发申请 #${id} 给 SPD？`)) return;
    try {
      await reDispatchCicdRequest({ request_id: id });
      void refetch();
    } catch (e) {
      alert("操作失败：" + (e instanceof Error ? e.message : String(e)));
    }
  }

  async function handleApplyReturned(id: number) {
    if (!confirm(`直接生效申请 #${id}？此操作绕过 SPD，由 RM 直接执行配置变更。`)) return;
    try {
      await applyReturnedCicdRequest({ request_id: id });
      void refetch();
      onRefreshed();
    } catch (e) {
      alert("操作失败：" + (e instanceof Error ? e.message : String(e)));
    }
  }

  const jiraBase = "http://jira.metax-tech.com/browse/";

  return (
    <div className="cicd-pane">
      <div className="cicd-toolbar">
        <input
          className="input sm cicd-search"
          placeholder={delivered ? "搜索 app / 交付人" : "搜索 app / 提交人"}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="cicd-table-wrap">
        {isLoading && <div className="muted" style={{ padding: 14 }}>加载中…</div>}
        {error && (
          <div className="lerr" style={{ padding: 14 }}>
            {error instanceof Error ? error.message : String(error)}
          </div>
        )}
        {!isLoading && (
          <table className="cicd-table">
            <thead>
              {delivered ? (
                <tr>
                  <th>申请ID</th>
                  <th>类型</th>
                  <th>任务ID</th>
                  <th>项目名称</th>
                  <th>版本</th>
                  <th>交付人</th>
                  <th>交付时间</th>
                  <th>Jira</th>
                  <th>操作</th>
                </tr>
              ) : (
                <tr>
                  <th>申请ID</th>
                  <th>类型</th>
                  <th>项目名称</th>
                  <th>版本</th>
                  <th>提交人</th>
                  <th>审批时间</th>
                  <th>Jira</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              )}
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td
                    colSpan={9}
                    className="empty"
                    style={{ textAlign: "center", color: "var(--muted)", padding: 30 }}
                  >
                    {delivered ? "暂无已交付记录" : "暂无待交付申请"}
                  </td>
                </tr>
              ) : delivered ? (
                filtered.map((d) => (
                  <tr key={d.id}>
                    <td className="cicd-id">#{d.id}</td>
                    <td>{REQ_TYPE_LABEL[d.request_type] ?? d.request_type}</td>
                    <td className="cicd-id">{d.task_id ?? "—"}</td>
                    <td>
                      <b>{d.task_app_name || "—"}</b>
                    </td>
                    <td>{d.task_app_version || "—"}</td>
                    <td>{d.delivered_by || "—"}</td>
                    <td>{formatServerTime(d.delivered_at ?? "")}</td>
                    <td>
                      {d.jira_id ? (
                        <a
                          href={jiraBase + d.jira_id}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {d.jira_id}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <button className="btn sm" onClick={() => onDetail(d)}>
                        详情
                      </button>
                    </td>
                  </tr>
                ))
              ) : (
                filtered.map((d) => (
                  <tr key={d.id}>
                    <td className="cicd-id">#{d.id}</td>
                    <td>{REQ_TYPE_LABEL[d.request_type] ?? d.request_type}</td>
                    <td>
                      <b>{d.task_app_name || "—"}</b>
                    </td>
                    <td>{d.task_app_version || "—"}</td>
                    <td>{d.submitter_display || d.submitter}</td>
                    <td>{formatServerTime(d.reviewed_at ?? "")}</td>
                    <td>
                      {d.jira_id ? (
                        <a
                          href={jiraBase + d.jira_id}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {d.jira_id}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <span className={`cicd-delivery-status-${d.delivery_status}`}>
                        {DELIVERY_STATUS_LABEL[d.delivery_status ?? ""] ?? d.delivery_status}
                      </span>
                      {d.returned_reason && (
                        <div className="small muted" style={{ marginTop: 2 }}>
                          {d.returned_reason}
                        </div>
                      )}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {["SPD", "RM"].includes(role) && (
                        <button
                          className="btn sm primary"
                          onClick={() => handleDeliver(d.id)}
                        >
                          确认交付
                        </button>
                      )}{" "}
                      {role === "SPD" && (
                        <button
                          className="btn sm danger"
                          onClick={() => handleReturn(d.id)}
                        >
                          退回
                        </button>
                      )}{" "}
                      {role === "RM" &&
                        d.delivery_status === "returned" && (
                          <>
                            <button
                              className="btn sm"
                              onClick={() => handleReDispatch(d.id)}
                            >
                              重新下发
                            </button>{" "}
                            <button
                              className="btn sm warn"
                              onClick={() => handleApplyReturned(d.id)}
                            >
                              直接生效
                            </button>{" "}
                          </>
                        )}
                      <button className="btn sm" onClick={() => onDetail(d)}>
                        详情
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CicdPage
// ---------------------------------------------------------------------------

type SubPane =
  | "overview"
  | "my"
  | "pending"
  | "recent"
  | "delivery"
  | "delivered";

export function CicdPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const { cicdOverviewFilter, setCicdOverviewFilter, cicdRecentDays, setCicdRecentDays } =
    useUiStore();

  const role = user?.role ?? "";
  const username = user?.username ?? "";

  // Ruling C: Admin has NO CICD create/approve/deliver affordances.
  const canSubmit = ["Owner", "RM"].includes(role);
  const canApprove = ["RM"].includes(role);
  const canDelivery = ["SPD", "RM", "Owner"].includes(role);

  // Pane visibility by role (mirrors bindCicd:4738-4754)
  const isSPD = role === "SPD";
  const availablePanes: SubPane[] = isSPD
    ? ["delivery", "delivered"]
    : canDelivery
    ? ["overview", "my", "pending", "recent", "delivery", "delivered"]
    : ["overview", "my", "pending", "recent"];

  const defaultPane: SubPane = isSPD ? "delivery" : "overview";
  const [activePane, setActivePane] = useState<SubPane>(defaultPane);

  // Dialog state
  const [taskFormTarget, setTaskFormTarget] = useState<CicdTask | null | "new">(null);
  const [approveReq, setApproveReq] = useState<CicdRequest | null>(null);
  const [historyTaskId, setHistoryTaskId] = useState<string | null>(null);
  const [detailReq, setDetailReq] = useState<CicdRequest | null>(null);

  // Badge / sub-tab counts
  const [pendingBadge, setPendingBadge] = useState(0);
  const [deliveryBadge, setDeliveryBadge] = useState(0);

  // Tasks query (primary data for overview + my panes)
  const {
    data: tasksData,
    isFetching,
    dataUpdatedAt,
    refetch: refetchTasks,
  } = useQuery({
    queryKey: CICD_TASKS_KEY,
    queryFn: () => fetchCicdTasks(),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  // Notifications query (for badge in TabNav)
  const { data: notifData, refetch: refetchNotif } = useQuery({
    queryKey: CICD_NOTIFICATIONS_KEY,
    queryFn: fetchCicdNotifications,
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  // On mount: mark visited (mirrors bindCicd:4756 markCicdVisited)
  useEffect(() => {
    void markCicdVisited();
    // Invalidate notifications so TabNav badge clears
    void queryClient.invalidateQueries({ queryKey: CICD_NOTIFICATIONS_KEY });
  }, [queryClient]);

  const tasks = tasksData?.tasks ?? [];
  const notifCount = notifData?.count ?? 0;

  const handleRefresh = useCallback(() => {
    void refetchTasks();
    void refetchNotif();
  }, [refetchTasks, refetchNotif]);

  function handleMutated() {
    void refetchTasks();
    void refetchNotif();
    // Invalidate all request-related queries
    void queryClient.invalidateQueries({ queryKey: ["cicd", "requests"] });
    void queryClient.invalidateQueries({ queryKey: ["cicd", "deliveries"] });
  }

  // Re-edit after rejection (mirrors openCicdReEdit)
  async function handleReEdit(req: CicdRequest) {
    // Find the task to prefill (we already have tasks loaded)
    const task = req.task_id ? tasks.find((t) => t.id === req.task_id) ?? null : null;
    // For "create" rejected requests, show new-task form with payload prefilled
    if (req.request_type === "create") {
      // Build a synthetic task from the payload to prefill the form
      const p = (req.payload ?? {}) as Record<string, unknown>;
      const syntheticTask: CicdTask = {
        id: "",
        app_name: (p.app_name as string) ?? "",
        app_version: (p.app_version as string) ?? "",
        owner_username: (p.owner_username as string) ?? username,
        repo_type: (p.repo_type as string) ?? "git",
        repo_name: (p.repo_name as string) ?? "",
        branch: (p.branch as string) ?? "",
        build_product: (p.build_product as string[]) ?? [],
        community_artifact: (p.community_artifact as string[]) ?? [],
        build_image: (p.build_image as string) ?? "",
        test_timeout: (p.test_timeout as number) ?? 40,
        status: (p.status as string) ?? "Running",
        notes: (p.notes as string) ?? "",
        has_pending: false,
        has_pending_delivery: false,
        owner_display: "",
        created_at: "",
        updated_at: "",
      };
      // If the task already exists, re-edit as modify; otherwise re-use payload as create
      if (task) {
        setTaskFormTarget(task);
      } else {
        setTaskFormTarget(syntheticTask);
      }
      return;
    }
    setTaskFormTarget(task);
  }

  const PANE_LABELS: Record<SubPane, string> = {
    overview: "任务总览",
    my: "我的 CICD 任务",
    pending: "待审批",
    recent: "最近申请",
    delivery: "待交付",
    delivered: "已交付",
  };

  return (
    <section className="view active">
      {/* Dialogs */}
      {(taskFormTarget !== null) && (
        <TaskFormDialog
          task={taskFormTarget === "new" ? null : taskFormTarget}
          username={username}
          onSubmitted={handleMutated}
          onClose={() => setTaskFormTarget(null)}
        />
      )}
      {approveReq && (
        <ApproveDialog
          req={approveReq}
          tasks={tasks}
          onDone={handleMutated}
          onClose={() => setApproveReq(null)}
        />
      )}
      {historyTaskId && (
        <HistoryDialog
          taskId={historyTaskId}
          tasks={tasks}
          onClose={() => setHistoryTaskId(null)}
        />
      )}
      {detailReq && (
        <DetailDialog
          req={detailReq}
          tasks={tasks}
          onClose={() => setDetailReq(null)}
        />
      )}

      <div className="page-toolbar">
        <h2>CICD 工作台</h2>
        {notifCount > 0 && (
          <span className="cicd-badge" style={{ marginLeft: 6 }}>
            {notifCount > 99 ? "99+" : notifCount}
          </span>
        )}
        <span className="spacer" />
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          onRefresh={handleRefresh}
          isFetching={isFetching}
        />
      </div>

      {/* Sub-tab nav */}
      <div className="cicd-subtabs">
        {availablePanes.map((pane) => {
          let label = PANE_LABELS[pane];
          if (pane === "pending" && pendingBadge > 0) label += ` (${pendingBadge})`;
          if (pane === "delivery" && deliveryBadge > 0) label += ` (${deliveryBadge})`;
          return (
            <button
              key={pane}
              className={`cicd-subtab${activePane === pane ? " active" : ""}`}
              onClick={() => setActivePane(pane)}
            >
              {label}
            </button>
          );
        })}
      </div>

      {/* Pane content */}
      {activePane === "overview" && (
        <OverviewPane
          tasks={tasks}
          canSubmit={canSubmit}
          canApprove={canApprove}
          overviewFilter={cicdOverviewFilter}
          onFilterChange={setCicdOverviewFilter}
          onNewTask={() => setTaskFormTarget("new")}
          onEdit={(t) => setTaskFormTarget(t)}
          onHistory={(t) => setHistoryTaskId(t.id)}
          onAbandon={async (t) => {
            if (!confirm(`确认废弃/退役 Stopped 任务 ${t.id} (${t.app_name})？任务将变为 Abandoned 状态。`)) return;
            try {
              await abandonCicdTask({ task_id: t.id });
              handleMutated();
            } catch (e) {
              alert("操作失败：" + (e instanceof Error ? e.message : String(e)));
            }
          }}
          onDelete={async (t) => {
            if (!confirm(`确认删除 Abandoned 任务 ${t.id}？此操作不可恢复。`)) return;
            try {
              await deleteCicdTask({ task_id: t.id });
              handleMutated();
            } catch (e) {
              alert("删除失败：" + (e instanceof Error ? e.message : String(e)));
            }
          }}
        />
      )}
      {activePane === "my" && (
        <MyPane
          tasks={tasks}
          username={username}
          canSubmit={canSubmit}
          onNewTask={() => setTaskFormTarget("new")}
          onEdit={(t) => setTaskFormTarget(t)}
          onHistory={(t) => setHistoryTaskId(t.id)}
        />
      )}
      {activePane === "pending" && (
        <PendingPane
          username={username}
          canApprove={canApprove}
          tasks={tasks}
          onApprove={(r) => setApproveReq(r)}
          onDetail={(r) => setDetailReq(r)}
          onCancelled={handleMutated}
          pendingCount={setPendingBadge}
        />
      )}
      {activePane === "recent" && (
        <RecentPane
          username={username}
          sinceDays={cicdRecentDays}
          onSinceDaysChange={setCicdRecentDays}
          tasks={tasks}
          onReEdit={handleReEdit}
          onDetail={(r) => setDetailReq(r)}
        />
      )}
      {activePane === "delivery" && (
        <DeliveryPane
          username={username}
          role={role}
          delivered={false}
          tasks={tasks}
          onDetail={(r) => setDetailReq(r)}
          onRefreshed={handleMutated}
          deliveryCount={setDeliveryBadge}
        />
      )}
      {activePane === "delivered" && (
        <DeliveryPane
          username={username}
          role={role}
          delivered={true}
          tasks={tasks}
          onDetail={(r) => setDetailReq(r)}
          onRefreshed={handleMutated}
        />
      )}
    </section>
  );
}
