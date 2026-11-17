/**
 * Human-readable labels for enum-like values displayed in the UI.
 *
 * (docTargetLabels, qaStatusLabels, cicdTaskStatusLabels, …).
 */

import type { QaStatus, ArtifactKind } from "../types";
import {
  DOC_TARGETS,
  QA_STATUSES,
  docTargetMetadata,
  qaStatusMetadata,
} from "./domainMetadata";

// ---------------------------------------------------------------------------
// Doc target
// ---------------------------------------------------------------------------

export const docTargetOptions = [...DOC_TARGETS];

export const docTargetLabels = Object.fromEntries(
  docTargetOptions.map((target) => [target, docTargetMetadata[target].label]),
) as Record<(typeof docTargetOptions)[number], string>;

// ---------------------------------------------------------------------------
// QA status
// ---------------------------------------------------------------------------

export const qaStatusOptions: QaStatus[] = [...QA_STATUSES];

// Mirrors index.html:1463-1468 (qaStatusLabels)
export const qaStatusLabels = Object.fromEntries(
  qaStatusOptions.map((status) => [status, qaStatusMetadata[status].label]),
) as Record<QaStatus, string>;

// ---------------------------------------------------------------------------
// Artifact kinds
// ---------------------------------------------------------------------------

export const artifactKindLabels: Record<ArtifactKind, string> = {
  manual: "HPC 手册",
  ai4sci: "AI4Sci 手册",
  release_note: "Release Note",
  data: "数据 JSON",
  manager_review: "经理评审 CSV",
};

// ---------------------------------------------------------------------------
// CICD task status
// ---------------------------------------------------------------------------

export const cicdTaskStatusLabels: Record<string, string> = {
  running: "运行中",
  stopped: "已停止",
};

export const cicdTaskStatusOptions = ["running", "stopped"];

// ---------------------------------------------------------------------------
// CICD request type
// ---------------------------------------------------------------------------

export const cicdRequestTypeLabels: Record<string, string> = {
  create: "创建",
  modify: "修改",
  delete: "删除",
};

// ---------------------------------------------------------------------------
// CICD request status
// ---------------------------------------------------------------------------

export const cicdRequestStatusLabels: Record<string, string> = {
  pending: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  cancelled: "已取消",
};

// ---------------------------------------------------------------------------
// CICD approval mode
// ---------------------------------------------------------------------------

export const cicdApprovalModeLabels: Record<string, string> = {
  immediate: "立即生效",
  dispatch_spd: "下发 SPD",
};

// ---------------------------------------------------------------------------
// User roles
// ---------------------------------------------------------------------------

export const roleLabels: Record<string, string> = {
  RM: "RM",
  Owner: "Owner",
  QA: "QA",
  SPD: "SPD",
  Admin: "Admin",
  Guest: "访客",
};

export { ALL_ROLES as allRoles } from "./accessControl";
