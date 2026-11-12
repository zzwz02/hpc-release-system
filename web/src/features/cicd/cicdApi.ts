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
export const CICD_CONFIG_KEY = ["cicd", "config"] as const;

import { apiGet, apiPost } from "../../api/http";
import type {
  CicdTasksResponse,
  CicdConfigResponse,
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
  CicdMarkVisitedResponse,
} from "../../types";

// ---------------------------------------------------------------------------
// GET
// ---------------------------------------------------------------------------

export function fetchCicdTasks(statusFilter?: string): Promise<CicdTasksResponse> {
  const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
  return apiGet<CicdTasksResponse>(`/api/cicd/tasks${qs}`);
}

export function fetchCicdConfig(): Promise<CicdConfigResponse> {
  return apiGet<CicdConfigResponse>("/api/cicd/config");
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
  qs.set("include_allowed_actions", "1");
  const str = qs.toString();
  return apiGet<CicdRequestsResponse>(`/api/cicd/requests${str ? `?${str}` : ""}`);
}

export function fetchCicdNotifications(): Promise<CicdNotificationsResponse> {
  return apiGet<CicdNotificationsResponse>("/api/cicd/notifications");
}

export function fetchCicdDeliveries(status?: string): Promise<CicdDeliveriesResponse> {
  const qs = new URLSearchParams({ include_allowed_actions: "1" });
  if (status) qs.set("status", status);
  return apiGet<CicdDeliveriesResponse>(`/api/cicd/deliveries?${qs.toString()}`);
}

// ---------------------------------------------------------------------------
// POST — requests
// ---------------------------------------------------------------------------

export interface SubmitPayload {
  task_id: string | null;
  request_type: string;
  payload: Record<string, unknown>;
  source?: string;
  replace_open?: boolean;
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

export function rejectReturnedCicdRequest(body: {
  request_id: number;
  review_note: string;
}): Promise<CicdRejectResponse> {
  return apiPost<CicdRejectResponse>("/api/cicd/requests/reject-returned", body);
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

/** Identity + app_info preview for the new-App form (no writes). */
export interface FetchPreviewResponse {
  git_url: string | null;
  git_branch: string | null;
  needs_network: boolean;
  app_info_unavailable: boolean;
  app_info_error: string | null;
  app_version?: string;
  x86_chips?: string;
  arm_chips?: string;
  python_label?: string;
  pytorch_label?: string;
  os?: string;
  arch?: string;
  community_release_status?: string;
  community_artifact?: string[];
  dependencies?: string[] | null;
  commit_id?: string;
}

export function fetchCicdPreview(body: FetchPreviewPayload): Promise<FetchPreviewResponse> {
  return apiPost<FetchPreviewResponse>("/api/cicd/apps/fetch-preview", body);
}

export interface NewAppPayload {
  official_name: string;
  doc_target: string;
  app_type: string;
  repo_type: string;
  repo_name: string;
  branch: string;
  cicd_build_image?: string;
  cicd_test_timeout?: number;
  cicd_community_artifact?: string[];
  cicd_notes?: string;
  app_version?: string;
}

/** Submit a request for a new App; the App appears once RM's approval takes effect. */
export function submitNewApp(body: NewAppPayload): Promise<{ ok: boolean; request: import("../../types").CicdRequest }> {
  return apiPost("/api/cicd/apps/new", body);
}

export function markCicdVisited(): Promise<CicdMarkVisitedResponse> {
  return apiPost<CicdMarkVisitedResponse>("/api/cicd/notifications/mark-visited", {});
}
