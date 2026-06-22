/**
 * CICD API wrappers — typed fetch helpers for all /api/cicd/* endpoints.
 *
 * Mirrors server.py / app/api/routers/cicd.py exactly.
 * No business logic here — pure network layer.
 */

// ---------------------------------------------------------------------------
// Shared TanStack Query keys — single source of truth; import from here.
// ---------------------------------------------------------------------------

export const CICD_TASKS_KEY = ["cicd", "tasks"] as const;
export const CICD_NOTIFICATIONS_KEY = ["cicd", "notifications"] as const;

import { apiGet, apiPost } from "../../api/http";
import type {
  CicdTasksResponse,
  CicdTaskHistoryResponse,
  CicdRequestsResponse,
  CicdNotificationsResponse,
  CicdDeliveriesResponse,
  CicdSubmitResponse,
  CicdApproveResponse,
  CicdRejectResponse,
  CicdCancelResponse,
  CicdDeliverResponse,
  CicdReturnDeliveryResponse,
  CicdReDispatchResponse,
  CicdApplyReturnedResponse,
  CicdTransferOwnerResponse,
  CicdMarkVisitedResponse,
} from "../../types";

// ---------------------------------------------------------------------------
// GET
// ---------------------------------------------------------------------------

export function fetchCicdTasks(statusFilter?: string): Promise<CicdTasksResponse> {
  const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
  return apiGet<CicdTasksResponse>(`/api/cicd/tasks${qs}`);
}

export function fetchCicdTaskHistory(taskId: string): Promise<CicdTaskHistoryResponse> {
  return apiGet<CicdTaskHistoryResponse>(`/api/cicd/tasks/${encodeURIComponent(taskId)}/history`);
}

export interface FetchRequestsParams {
  onlyMine?: boolean;
  taskId?: string;
  status?: string;
  sinceDays?: number;
}

export function fetchCicdRequests(params: FetchRequestsParams = {}): Promise<CicdRequestsResponse> {
  const qs = new URLSearchParams();
  if (params.onlyMine) qs.set("only_mine", "1");
  if (params.taskId) qs.set("task_id", params.taskId);
  if (params.status) qs.set("status", params.status);
  if (params.sinceDays != null && params.sinceDays > 0) qs.set("since_days", String(params.sinceDays));
  const str = qs.toString();
  return apiGet<CicdRequestsResponse>(`/api/cicd/requests${str ? `?${str}` : ""}`);
}

export function fetchCicdNotifications(): Promise<CicdNotificationsResponse> {
  return apiGet<CicdNotificationsResponse>("/api/cicd/notifications");
}

export function fetchCicdDeliveries(status?: string): Promise<CicdDeliveriesResponse> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiGet<CicdDeliveriesResponse>(`/api/cicd/deliveries${qs}`);
}

// ---------------------------------------------------------------------------
// POST — requests
// ---------------------------------------------------------------------------

export interface SubmitPayload {
  task_id: string | null;
  request_type: string;
  payload: Record<string, unknown>;
  source?: string;
}

export function submitCicdRequest(body: SubmitPayload): Promise<CicdSubmitResponse> {
  return apiPost<CicdSubmitResponse>("/api/cicd/requests/submit", body);
}

export interface ApprovePayload {
  request_id: number;
  review_note?: string;
  approval_mode?: string;
  jira_id?: string;
  jira_auto_created?: number;
}

export function approveCicdRequest(body: ApprovePayload): Promise<CicdApproveResponse> {
  return apiPost<CicdApproveResponse>("/api/cicd/requests/approve", body);
}

export function rejectCicdRequest(body: {
  request_id: number;
  review_note: string;
}): Promise<CicdRejectResponse> {
  return apiPost<CicdRejectResponse>("/api/cicd/requests/reject", body);
}

export function cancelCicdRequest(body: {
  request_id: number;
}): Promise<CicdCancelResponse> {
  return apiPost<CicdCancelResponse>("/api/cicd/requests/cancel", body);
}

export function deliverCicdRequest(body: {
  request_id: number;
}): Promise<CicdDeliverResponse> {
  return apiPost<CicdDeliverResponse>("/api/cicd/requests/deliver", body);
}

export function returnDeliveryCicdRequest(body: {
  request_id: number;
  reason: string;
}): Promise<CicdReturnDeliveryResponse> {
  return apiPost<CicdReturnDeliveryResponse>("/api/cicd/requests/return-delivery", body);
}

export function reDispatchCicdRequest(body: {
  request_id: number;
}): Promise<CicdReDispatchResponse> {
  return apiPost<CicdReDispatchResponse>("/api/cicd/requests/re-dispatch", body);
}

export function applyReturnedCicdRequest(body: {
  request_id: number;
}): Promise<CicdApplyReturnedResponse> {
  return apiPost<CicdApplyReturnedResponse>("/api/cicd/requests/apply-returned", body);
}

// ---------------------------------------------------------------------------
// POST — tasks
// ---------------------------------------------------------------------------

export function transferCicdOwner(body: {
  task_id: string;
  new_owner: string;
}): Promise<CicdTransferOwnerResponse> {
  return apiPost<CicdTransferOwnerResponse>("/api/cicd/tasks/transfer-owner", body);
}

// ---------------------------------------------------------------------------
// POST — CICD-first app creation (Wave 3)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// POST — CICD-first app fetch-preview (Wave 3.1)
// ---------------------------------------------------------------------------

export interface FetchPreviewPayload {
  repo_type: string;
  repo_name: string;
  branch: string;
}

/** Fields returned by POST /api/cicd/apps/fetch-preview (exact backend keys).
 *
 * Wave 4 (impl-1) new contract — always HTTP 200; Gerrit failures are soft flags:
 *
 * Always present:
 *   git_url            — derived identity URL; null only when manifest needs network
 *   git_branch         — derived branch; null only when manifest needs network
 *   needs_network      — true for .xml manifest repos (identity resolution needs Gerrit)
 *   app_info_unavailable — true when Gerrit app_info content fetch failed
 *   app_info_error     — error detail when unavailable; null on success
 *
 * Only present when app_info_unavailable === false (happy path):
 *   app_version, x86_chips, arm_chips, python_label, pytorch_label,
 *   os, arch, commit_id, parsed
 *
 * HTTP errors (still raised):
 *   400 — empty repo_name / branch (bad input)
 *   403 — role not in CICD_CREATE_ROLES
 */
export interface FetchPreviewResponse {
  // ── Identity (always present) ────────────────────────────────────────────
  /** Derived Gerrit SSH URL; null when manifest resolution requires Gerrit network. */
  git_url: string | null;
  /** Derived branch; null when manifest resolution requires Gerrit network. */
  git_branch: string | null;
  /** True for repo-type .xml manifests: identity resolution needs Gerrit network. */
  needs_network: boolean;

  // ── App-info availability flags (always present) ─────────────────────────
  /** True when the Gerrit app_info content fetch failed (unreachable, archive error, …). */
  app_info_unavailable: boolean;
  /** Human-readable error when app_info_unavailable is true; null otherwise. */
  app_info_error: string | null;

  // ── App-info content fields (only when app_info_unavailable === false) ───
  app_version?: string;
  x86_chips?: string;
  arm_chips?: string;
  python_label?: string;
  pytorch_label?: string;
  os?: string;
  arch?: string;
  commit_id?: string;
  /** Full parsed blob — pass as app_info_parsed to POST /api/cicd/apps/new. */
  parsed?: Record<string, unknown>;

  // ── Legacy / backward-compat ─────────────────────────────────────────────
  /** Present in older responses; unused by new wizard logic. */
  ok?: boolean;
}

export function fetchCicdPreview(body: FetchPreviewPayload): Promise<FetchPreviewResponse> {
  return apiPost<FetchPreviewResponse>("/api/cicd/apps/fetch-preview", body);
}

// ---------------------------------------------------------------------------
// POST — CICD-first app creation (Wave 3)
// ---------------------------------------------------------------------------

export interface CicdFirstNewAppPayload {
  release_id: string;
  /** Human-readable name stored in apps table — required by backend (cicd_service.cicd_first_new_app). */
  official_name: string;
  /** Optional CICD-task display name; defaults to official_name server-side when omitted/empty. */
  app_name?: string;
  app_version?: string;
  owner_username: string;
  repo_type: string;
  repo_name: string;
  branch: string;
  build_product?: string[];
  community_artifact?: string[];
  build_image?: string;
  test_timeout?: number;
  notes?: string;
  cicd_repo_type?: string;
  cicd_community_artifact?: string;
  cicd_build_image?: string;
  cicd_test_timeout?: string;
  cicd_notes?: string;
  /** Parsed app_info blob from fetch-preview; backend persists it directly. */
  app_info_parsed?: Record<string, unknown> | null;
  /** Gerrit commit ID accompanying the parsed blob. */
  app_info_commit_id?: string;
}

export function cicdFirstNewApp(body: CicdFirstNewAppPayload): Promise<{ ok: boolean; app_id: string; request_id: number }> {
  return apiPost<{ ok: boolean; app_id: string; request_id: number }>("/api/cicd/apps/new", body);
}

// ---------------------------------------------------------------------------
// POST — notifications
// ---------------------------------------------------------------------------

export function markCicdVisited(): Promise<CicdMarkVisitedResponse> {
  return apiPost<CicdMarkVisitedResponse>("/api/cicd/notifications/mark-visited", {});
}
