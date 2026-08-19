/**
 * Human-readable labels for enum-like values displayed in the UI.
 *
 * Mirrors the label maps scattered throughout index.html
 * (e.g. releaseDecisionLabels, docTargetLabels, qaStatusLabels,
 * cicdTaskStatusLabels, etc.).
 */

import type {
  QaStatus,
  ReleaseDecision,
  ArtifactKind,
} from "../types";
import {
  RELEASE_DECISIONS,
  releaseDecisionMetadata,
} from "./domainMetadata";

// ---------------------------------------------------------------------------
// Release decision
// ---------------------------------------------------------------------------

export const releaseDecisionOptions: ReleaseDecision[] = [...RELEASE_DECISIONS];

// Mirrors index.html:1454-1458 exactly (full label strings used in selects;
// Wave-2 components split on ：to extract the short display portion).
export const releaseDecisionLabels = Object.fromEntries(
  releaseDecisionOptions.map((decision) => [decision, releaseDecisionMetadata[decision].label]),
) as Record<ReleaseDecision, string>;

/** Order for sorting apps by release decision in the app workbench. */
export const releaseDecisionOrder = Object.fromEntries(
  releaseDecisionOptions.map((decision) => [decision, releaseDecisionMetadata[decision].order]),
) as Record<ReleaseDecision, number>;

// ---------------------------------------------------------------------------
// Doc target
// ---------------------------------------------------------------------------

// Mirrors index.html:1461 exactly — only 2 selectable options in the UI.
// "none" is a valid DB value but is not shown in any label select.
export const docTargetOptions: Array<"manual" | "ai4sci"> = ["manual", "ai4sci"];

export const docTargetLabels: Record<"manual" | "ai4sci", string> = {
  manual: "HPC",
  ai4sci: "AI4Sci",
};

// ---------------------------------------------------------------------------
// QA status
// ---------------------------------------------------------------------------

export const qaStatusOptions: QaStatus[] = [
  "not_checked",
  "qa_passed",
  "has_issues",
  "cannot_release",
];

// Mirrors index.html:1463-1468 (qaStatusLabels)
export const qaStatusLabels: Record<QaStatus, string> = {
  not_checked: "未测试",
  qa_passed: "通过",
  has_issues: "存在问题",
  cannot_release: "不可发布",
};

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
  Running: "运行中",
  Stopped: "停止",
};

export const cicdTaskStatusOptions = ["Running", "Stopped"];

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
