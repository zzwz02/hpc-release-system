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

export function deleteCicdTask(body: {
  task_id: string;
}): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>("/api/cicd/tasks/delete", body);
}

export function abandonCicdTask(body: {
  task_id: string;
}): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>("/api/cicd/tasks/abandon", body);
}

// ---------------------------------------------------------------------------
// POST — notifications
// ---------------------------------------------------------------------------

export function markCicdVisited(): Promise<CicdMarkVisitedResponse> {
  return apiPost<CicdMarkVisitedResponse>("/api/cicd/notifications/mark-visited", {});
}
