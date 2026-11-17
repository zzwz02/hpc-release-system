/** Shared display helpers for CICD requests (App 工作台 and CICD 工作台). */
import type { App, CicdRequest } from "../../types";
import { cicdPayloadConfigLabels, cicdStatusLabel } from "../../lib/domainMetadata";
import { formatCicdRepoPath } from "../../lib/git";

export const CICD_REQ_TYPE_LABEL: Record<string, string> = {
  create: "新建",
  modify: "修改",
};

const CICD_REQ_STATUS_LABEL: Record<string, string> = {
  pending: "待审批",
  approved: "已审批",
  rejected: "已拒绝",
  cancelled: "已取消",
};

const CICD_DELIVERY_STATUS_LABEL: Record<string, string> = {
  pending: "待交付",
  delivered: "已交付",
  returned: "已退回",
};

const CICD_FIELD_LABEL: Record<string, string> = {
  app_name: "App",
  app_version: "版本",
  repo_name: "Gerrit 路径",
  branch: "Branch",
  build_product: "构建产物",
  owner_username: "负责人",
  status: "CICD 状态",
  doc_target: "类型",
  app_type: "App 类型",
  ...cicdPayloadConfigLabels,
};

function valueText(value: unknown, field?: string): string {
  if (Array.isArray(value)) return value.join(", ") || "(空)";
  if (field === "repo_name") return formatCicdRepoPath(String(value ?? "")) || "(空)";
  if (field === "status") return cicdStatusLabel(String(value ?? "")) || "(空)";
  return String(value ?? "").trim() || "(空)";
}

export function cicdRequestSummary(req: CicdRequest): string {
  const payload = (req.payload ?? {}) as Record<string, unknown>;
  const entries = Object.entries(payload).filter(([field]) => field !== "app_id");
  if (!entries.length) return "无字段变更";
  return entries.map(([field, raw]) => {
    const label = CICD_FIELD_LABEL[field] ?? field;
    const change = raw as { old?: unknown; new?: unknown };
    if (raw && typeof raw === "object" && !Array.isArray(raw) && ("old" in change || "new" in change)) {
      return `${label}: ${valueText(change.old, field)} -> ${valueText(change.new, field)}`;
    }
    return `${label}: ${valueText(raw, field)}`;
  }).join("；");
}

export function cicdWorkflowState(req: CicdRequest): string {
  if (req.delivery_status) return CICD_DELIVERY_STATUS_LABEL[req.delivery_status] ?? req.delivery_status;
  return CICD_REQ_STATUS_LABEL[req.status] ?? req.status;
}

export function requestMatchesApp(req: CicdRequest, app: App): boolean {
  return (req.app_id || req.task_id) === app.id;
}

export function isOpenJiraModifyRequest(req: CicdRequest): boolean {
  return req.is_open === true && req.blocker_kind === "jira";
}

export function isOpenStatusModifyRequest(req: CicdRequest): boolean {
  return req.is_open === true && req.blocker_kind === "status";
}

export function isReplaceablePendingModifyRequest(req: CicdRequest): boolean {
  return req.replaceable === true && req.blocker_kind !== "status";
}

export function jiraModifyBlockMessage(req: CicdRequest): string {
  return `已有 Jira 申请 #${req.id} / ${req.jira_id} 正在交付，不能提交新的 CICD 修改。请先让 SPD 退回需求，再由 RM 在待交付页拒绝旧申请。`;
}

export function statusModifyBlockMessage(req: CicdRequest): string {
  return `该 App 已有启动/停止申请 #${req.id} 未完成，不能提交新的 CICD 修改；请先等待该申请处理完成。`;
}

export function uniqueCicdRequestCount(requests: CicdRequest[]): number {
  return new Set(requests.map((req) => req.id)).size;
}
