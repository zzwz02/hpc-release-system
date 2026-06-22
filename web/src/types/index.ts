/**
 * TypeScript interfaces mirroring every Phase-2 FastAPI response shape.
 *
 * Derived from:
 *   - app/api/routers/*  (exact return shapes)
 *   - app/services/app_service.py  (state_payload)
 *   - tests/golden/responses/*.json  (live response shapes)
 *
 * Field names match the Python serialization exactly.
 * No camelCase conversion — these mirror the raw JSON.
 */

// ---------------------------------------------------------------------------
// Auth / User
// ---------------------------------------------------------------------------

export interface User {
  username: string;
  role: string;
  display_name: string;
}

/** GET /api/me */
export interface MeResponse {
  user: User | null;
}

/** GET /api/ldap/status */
export interface LdapStatusResponse {
  enabled: boolean;
  uri: string;
}

/** POST /api/login, POST /api/login/ldap */
export interface LoginResponse {
  ok: boolean;
}

/** POST /api/logout */
export interface LogoutResponse {
  ok: boolean;
}

// ---------------------------------------------------------------------------
// Apps (global app registry, not release-scoped)
// ---------------------------------------------------------------------------

export interface App {
  id: string;
  git_url: string;
  git_branch: string;
  cicd_repo_type?: string;
  cicd_community_artifact?: string;
  cicd_build_image?: string;
  cicd_test_timeout?: string;
  cicd_notes?: string;
  created_by: string;
  created_at: string;
  aliases: string[];
}

// ---------------------------------------------------------------------------
// Release
// ---------------------------------------------------------------------------

export type ReleasePhase =
  | "before_app_freeze"
  | "after_app_freeze"
  | "released";

export interface ReleaseSummary {
  id: string;
  name: string;
  maca_version: string;
  app_freeze_deadline: string;
  doc_deadline: string;
  released_locked: boolean;
  released_locked_at: string;
  released_locked_by: string;
  created_at: string;
  source: string;
  cloned_from: string;
  phase: ReleasePhase;
}

// ---------------------------------------------------------------------------
// Snapshot (release-scoped app entry)
// ---------------------------------------------------------------------------

export interface SnapshotDoc {
  intro: string;
  image_usage: string;
  binary_usage: string;
  env_setup: string;
  limitations: string;
}

export interface SnapshotCommunity {
  release_status: string;
  python_version: string;
  framework_version: string;
}

export interface SnapshotSanity {
  arm_kylin: boolean;
  ubuntu: boolean;
}

export interface SnapshotMissingItem {
  kind: string;  // "doc" | "qa" | etc.
  text: string;
}

export interface SnapshotTestDoc {
  /** Unique ID within the snapshot (from app_info or owner-added). */
  id: string;
  /** Relative path of the test script (e.g. "tests/test_foo.sh"). */
  path: string;
  command: string;
  dataset: string;
  content: string;
  result_view: string;
  pass_criteria: string;
  /** True when this entry was added by an Owner (not from app_info). */
  owner_added?: boolean;
  /** True when obsolete (app_info no longer contains this path). */
  obsolete?: boolean;
}

export type QaStatus =
  | "not_checked"
  | "qa_passed"
  | "has_issues"
  | "cannot_release";

export type ReleaseDecision =
  | "release"
  | "cicd_only"
  | "stopped";

export type DocTarget =
  | "manual"
  | "ai4sci"
  | "none";

export interface AppInfoSnapshot {
  // Opaque dict from Gerrit/upload; shape varies
  [key: string]: unknown;
}

export interface AppInfoDiff {
  field: string;
  old: unknown;
  new: unknown;
}

export interface Snapshot {
  app_id: string;
  official_name: string;
  version: string;
  description: string;
  official_url: string;
  type: string;
  arch: string;
  x86_chips: string;
  arm_chips: string;
  hpcc_chip: string;
  maca_version: string;
  build_arches: string;
  build_os: string;
  doc_target: DocTarget;
  release_decision: ReleaseDecision;
  owners: string[];
  owner_confirmed: boolean;
  qa_status: QaStatus;
  qa_issue_note: string;
  doc: SnapshotDoc;
  community: SnapshotCommunity;
  sanity: SnapshotSanity;
  python_labels: string;
  pytorch_labels: string;
  test_docs: SnapshotTestDoc[];
  missing_items: SnapshotMissingItem[];
  app_info: AppInfoSnapshot | null;
  app_info_diffs: AppInfoDiff[];
}

// ---------------------------------------------------------------------------
// Release detail (with snapshots)
// ---------------------------------------------------------------------------

export interface ReleaseDetail extends ReleaseSummary {
  snapshots: Record<string, Snapshot>;
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

export type ArtifactKind =
  | "manual"
  | "ai4sci"
  | "release_note"
  | "data"
  | "manager_review";

export interface ArtifactMeta {
  kind: ArtifactKind;
  name: string;
  final: number;  // 0 | 1
  generated_at: string;
}

// ---------------------------------------------------------------------------
// QA log
// ---------------------------------------------------------------------------

export interface QaLog {
  release_id: string;
  filename: string;
  uploaded_at: string;
  uploaded_by: string;
  size_bytes: number;
  has_analysis: boolean;
  analysis_summary: string;
}

// ---------------------------------------------------------------------------
// QA audit log entries
// ---------------------------------------------------------------------------

export interface QaAuditDetail {
  field: string;
  label: string;
  old: unknown;
  new: unknown;
}

export interface QaAuditEntry {
  ts: string;
  user: string;
  role: string;
  app_id: string;
  release_id: string;
  event: string;
  message: string;
  detail: QaAuditDetail[];
}

// ---------------------------------------------------------------------------
// Release schedule
// ---------------------------------------------------------------------------

export interface ReleaseScheduleEntry {
  id: string;
  version: string;
  branch_cut_at: string;
  release_at: string;
  note: string;
  created_at: string;
  created_by: string;
  updated_at: string;
  updated_by: string;
}

// ---------------------------------------------------------------------------
// App audit log (GET /api/app-audit)
// ---------------------------------------------------------------------------

export interface AppAuditEntry {
  ts: string;
  user: string;
  role: string;
  app_id: string;
  release_id: string;
  event: string;
  message: string;
  detail: Array<{ field: string; label: string; old: unknown; new: unknown }>;
}

export interface AppAuditResponse {
  entries: AppAuditEntry[];
}

// ---------------------------------------------------------------------------
// Full state payload (GET /api/state)
// ---------------------------------------------------------------------------

export interface StatePayload {
  apps: App[];
  releases: ReleaseSummary[];
  release: ReleaseDetail | null;
  artifacts: ArtifactMeta[];
  user: User;
  user_display_names: Record<string, string>;
  qa_log: QaLog | null;
  qa_audit_logs: Record<string, QaAuditEntry[]>;
  release_schedule: ReleaseScheduleEntry[];
}

// ---------------------------------------------------------------------------
// CICD tasks
// ---------------------------------------------------------------------------

export interface CicdTask {
  id: string;
  app_id?: string;
  app_name: string;
  app_version: string;
  repo_type: string;
  repo_name: string;
  branch: string;
  build_product: string[];
  community_artifact: string[];
  build_image: string;
  test_timeout: number;
  owner_username: string;
  status: string;
  notes: string;
  created_at: string;
  updated_at: string;
  has_pending: boolean;
  has_pending_delivery: boolean;
  owner_display: string;
}

export interface CicdTasksResponse {
  tasks: CicdTask[];
}

export interface CicdTaskHistoryResponse {
  history: CicdRequest[];
}

// ---------------------------------------------------------------------------
// CICD requests
// ---------------------------------------------------------------------------

export interface CicdRequestPayload {
  [key: string]: unknown;
}

export interface CicdRequest {
  id: number;
  task_id: string | null;
  app_id?: string;
  request_type: string;  // "create" | "modify" | "owner_transfer"
  payload: CicdRequestPayload;
  submitter: string;
  submitter_display: string;
  submitted_at: string;
  status: string;  // "pending" | "approved" | "rejected" | "cancelled"
  reviewer: string;
  reviewed_at: string;
  review_note: string;
  is_self_approved: number;  // 0 | 1
  approval_mode: string;  // "immediate" | "dispatch_spd"
  delivery_status: string;
  jira_id: string;
  jira_auto_created: number;  // 0 | 1
  delivered_by: string;
  delivered_at: string;
  returned_reason: string;
  returned_at: string;
  task_app_name: string;
  task_app_version: string;
  task_repo_name: string;
  task_branch: string;
  task_status: string;
  // origin distinguishes build-config requests ("cicd_workbench") from
  // decision-sync requests ("release_decision_sync") auto-created when an
  // App's release_decision changes (R3 Ruling D). Exposed by the API (F3).
  origin?: string;
}

export interface CicdRequestsResponse {
  requests: CicdRequest[];
}

// ---------------------------------------------------------------------------
// CICD deliveries
// ---------------------------------------------------------------------------

/** Delivery rows have the same shape as requests (delivery workflow) */
export type CicdDelivery = CicdRequest;

export interface CicdDeliveriesResponse {
  deliveries: CicdDelivery[];
}

// ---------------------------------------------------------------------------
// CICD notifications
// ---------------------------------------------------------------------------

export interface CicdNotificationsResponse {
  count: number;
  last_visited_at: string;
}

// ---------------------------------------------------------------------------
// CICD mutation responses
// ---------------------------------------------------------------------------

export interface CicdSubmitResponse {
  ok: boolean;
  request: CicdRequest;
}

export interface CicdApproveResponse {
  ok: boolean;
  request: CicdRequest;
}

export interface CicdRejectResponse {
  ok: boolean;
  request: CicdRequest;
}

export interface CicdCancelResponse {
  ok: boolean;
  request: CicdRequest;
}

export interface CicdDeliverResponse {
  ok: boolean;
  request: CicdRequest;
}

export interface CicdReturnDeliveryResponse {
  ok: boolean;
  request: CicdRequest;
}

export interface CicdReDispatchResponse {
  ok: boolean;
  request: CicdRequest;
}

export interface CicdApplyReturnedResponse {
  ok: boolean;
  request: CicdRequest;
}

export interface CicdMarkVisitedResponse {
  ok: boolean;
}

// ---------------------------------------------------------------------------
// QA reports (GET /api/qa-reports)
// ---------------------------------------------------------------------------

export interface QaReportRowMeta {
  release_decision: ReleaseDecision;
  is_release: boolean;
}

export interface QaReportTable {
  columns: string[];
  rows: string[][];
  rows_meta?: QaReportRowMeta[];
}

export interface QaReportsResponse {
  release_name: string;
  maca_version: string;
  compare_release_id: string;
  compare_release_name: string;
  release_report: QaReportTable;
  test_cmd: QaReportTable;
  generated_at: string;
}

// ---------------------------------------------------------------------------
// QA analysis job (GET /api/qa/analyze-log/status)
// ---------------------------------------------------------------------------

export interface QaAnalysisJob {
  job_id: string;
  release_id: string;
  status: string;  // "running" | "done" | "error"
  started_at: string;
  finished_at: string | null;
  summary: string;
  error: string | null;
  progress: number | null;
}

// ---------------------------------------------------------------------------
// QA mutation responses
// ---------------------------------------------------------------------------

export interface QaUploadLogResponse {
  ok: boolean;
}

export interface QaStatusBatchResponse {
  ok: boolean;
  updated: number;
}

export interface QaAnalyzeLogStartResponse extends QaAnalysisJob {}

// ---------------------------------------------------------------------------
// Wiki articles
// ---------------------------------------------------------------------------

export interface WikiArticleSummary {
  id: string;
  title: string;
  pinned: boolean;
  created_by: string;
  created_at: string;
  updated_by: string;
  updated_at: string;
  deleted: boolean;
  excerpt: string;
}

export interface WikiArticle {
  id: string;
  title: string;
  body_md: string;
  pinned: boolean;
  created_by: string;
  created_at: string;
  updated_by: string;
  updated_at: string;
  deleted: boolean;
}

export interface WikiArticlesResponse {
  articles: WikiArticleSummary[];
}

export interface WikiArticleResponse {
  article: WikiArticle;
}

export interface WikiSaveResponse {
  ok: boolean;
  article: WikiArticle;
}

export interface WikiPinResponse {
  ok: boolean;
  article: WikiArticle;
}

export interface WikiDeleteResponse {
  ok: boolean;
}

export interface WikiImage {
  id: string;
  filename: string;
  content_type: string;
  /** Absolute URL path served by GET /api/wiki/images/{id} */
  url: string;
  created_by: string;
  created_at: string;
}

export interface WikiImageUploadResponse {
  ok: boolean;
  image: WikiImage;
}

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

export interface AdminUser {
  username: string;
  role: string;
  auth_source: string;
  display_name: string;
}

export interface AdminUsersResponse {
  users: AdminUser[];
}

export interface AdminSetRoleResponse {
  ok: boolean;
}

export interface AdminClearDbResponse {
  ok: boolean;
  backup: string;
}

export interface AdminDeleteAppResponse {
  ok: boolean;
}

// ---------------------------------------------------------------------------
// Releases mutation responses
// ---------------------------------------------------------------------------

export interface ImportInitialResponse {
  release_id: string;
}

export interface CreateReleaseResponse {
  release_id: string;
}

export interface UpdateDeadlinesResponse {
  release: ReleaseDetail;
}

export interface FinalLockResponse {
  artifacts: ArtifactMeta[];
}

export interface FinalUnlockResponse {
  ok: boolean;
}

export interface ScheduleUpsertResponse {
  entry: ReleaseScheduleEntry;
}

export interface ScheduleDeleteResponse {
  ok: boolean;
}

// ---------------------------------------------------------------------------
// App mutation responses
// ---------------------------------------------------------------------------

export interface AppsNewResponse {
  app_id: string;
}

export interface AppsUpdateResponse {
  ok: boolean;
}

export interface AppInfoResponse {
  ok: boolean;
}

export interface ArtifactGenerateResponse {
  artifacts: ArtifactMeta[];
}

export interface ArtifactManagerReviewResponse {
  artifact: string;
  bytes: number;
}

export interface GerritPlanResponse {
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Error envelope (backend error responses)
// ---------------------------------------------------------------------------

export interface ApiError {
  ok: false;
  error: string;
}
