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

import type {
  DocTarget as SharedDocTarget,
  QaStatus as SharedQaStatus,
  ReleasePhase as SharedReleasePhase,
} from "../lib/domainMetadata";
import type { LlmTotals } from "../lib/llmProgress";

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
// Apps (catalog: identity, CICD status, optional profile, app_info)
// ---------------------------------------------------------------------------

export type CicdStatus = "running" | "stopped";

export interface AppDoc {
  intro?: string;
  image_usage?: string;
  binary_usage?: string;
  env_setup?: string;
  limitations?: string;
}

export interface AppCommunity {
  menu_level1?: string;
  doc_url?: string;
  description?: string;
  python_version?: string;
  framework_version?: string;
}

export interface AppSanity {
  arm_kylin?: boolean;
  ubuntu?: boolean;
}

export interface AppTestDoc {
  path: string;
  dataset?: string;
  content?: string;
  result_view?: string;
  pass_criteria?: string;
}

export interface TestPlatform {
  build: string;
  os: string;
  arch: string;
  chips: string[];
  python_label: string;
  pytorch_label: string;
}

export interface AppInfoTest {
  path: string;
  command: string;
  supported_chips: string[];
  arch_list: string[];
  platforms: TestPlatform[];
}

/** Summary of one stored app_info version. */
export interface AppInfoVersion {
  id: number;
  commit_id: string;
  fetched_at: string;
  source: string;
  app_version: string;
  x86_chips: string[];
  arm_chips: string[];
  build_os: string[];
  build_arches: string[];
  python_labels: string[];
  pytorch_labels: string[];
  tests: AppInfoTest[];
  community: { release_status?: string; image_count?: number; pkg_count?: number };
  /** null = the app_info has no app_dependency field. */
  dependencies: string[] | null;
}

export interface OfficialVersion {
  release_id: string;
  release_name: string;
  version: string;
  image: string;
  digest: string;
  released_at: string;
}

export interface App {
  id: string;
  name: string;
  git_url: string;
  git_branch: string;
  aliases: string[];
  created_by: string;
  created_at: string;
  cicd_repo_type?: string;
  cicd_community_artifact?: string;
  cicd_build_image?: string;
  cicd_test_timeout?: string;
  cicd_notes?: string;
  cicd_status: CicdStatus;
  official_name: string;
  type: string;
  official_url: string;
  description: string;
  doc_target: DocTarget;
  owners: string[];
  doc: AppDoc;
  community: AppCommunity;
  sanity: AppSanity;
  test_docs: AppTestDoc[];
  sync_head_commit: string;
  sync_checked_at: string;
  sync_error: string;
  latest_app_info: AppInfoVersion | null;
  dependencies: string[];
  dependency_declared: boolean;
  official: OfficialVersion | null;
  open_status_request: { id: number; target_status: CicdStatus } | null;
  /** Unpublished cycles where the App has an active signup (blocks stopping CICD). */
  open_signups: string[];
  /** Contextual actions computed by the backend for the current user. */
  allowed_actions?: string[];
}

export type QaStatus = SharedQaStatus;

export type DocTarget = SharedDocTarget;

// ---------------------------------------------------------------------------
// Monthly cycles
// ---------------------------------------------------------------------------

export type ReleasePhase = SharedReleasePhase;

export interface ReleaseSummary {
  id: string;
  name: string;
  signup_deadline: string;
  qa_deadline: string;
  release_date: string;
  released_locked: boolean;
  released_locked_at: string;
  released_locked_by: string;
  created_at: string;
  created_by: string;
  phase: ReleasePhase;
  entry_count: number;
  released_count: number;
}

export interface EnvironmentRow {
  id?: number;
  component: string;
  version: string;
  note: string;
}

export interface BaseImage {
  id: number;
  release_id: string;
  name: string;
  image: string;
  digest: string;
  note: string;
}

export type EntryResult = "" | "released" | "failed";

export interface ScopeTest extends AppInfoTest {
  excluded: boolean;
}

export interface ScopeRequest {
  id: number;
  entry_id: number;
  app_id?: string;
  app_name?: string;
  release_id?: string;
  base_version_id: number;
  target_version_id: number;
  additions: string[];
  status: "pending" | "approved" | "rejected" | "superseded" | "closed";
  created_at: string;
  decided_by: string;
  decided_at: string;
  decision_note: string;
}

export interface Entry {
  id: number;
  release_id: string;
  app_id: string;
  status: "active" | "withdrawn";
  base_image_id: number | null;
  target_version: string;
  planned_features: string;
  actual_delivery: string;
  known_issues: string;
  locked_version_id: number | null;
  excluded_tests: string[];
  qa_status: QaStatus;
  qa_note: string;
  deliverable_image: string;
  deliverable_digest: string;
  deliverable_commit: string;
  result: EntryResult;
  created_at: string;
  created_by: string;
  updated_at: string;
  updated_by: string;
  locked_app_info: AppInfoVersion | null;
  scope_tests: ScopeTest[];
  pending_scope_request: ScopeRequest | null;
  behind_latest: boolean;
}

export interface ReleaseDetail extends ReleaseSummary {
  environment: EnvironmentRow[];
  base_images: BaseImage[];
  entries: Entry[];
  scope_requests: ScopeRequest[];
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
  app_types?: string[];
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

export interface CicdConfigResponse {
  jira_browse_url: string;
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
  origin?: string;
  /** Backend-derived lifecycle fields returned with include_allowed_actions=1. */
  is_open?: boolean;
  blocker_kind?: "create" | "jira" | "status" | "modify" | "";
  replaceable?: boolean;
  status_direction?: "start" | "stop" | "";
  /** Contextual actions computed by the backend for the current user. */
  allowed_actions?: string[];
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
  /** Set when approval succeeded but the automatic Jira issue was not created. */
  jira_error?: string;
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
  app_id: string;
  /** False for rows shown only because they were signed up in the compared cycle. */
  signed_up: boolean;
}

export interface QaReportTable {
  columns: string[];
  rows: string[][];
  rows_meta?: QaReportRowMeta[];
}

export interface QaReportsResponse {
  release_name: string;
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
  /** Seconds since the job started, worked out by the server. */
  elapsed_seconds?: number;
  /** LLM usage so far, counted like every LLM feature (lib/llmProgress). */
  llm?: LlmTotals | null;
  /** Who started it; every QA editor may watch and stop it. */
  user?: string;
  /** A stop was requested and the job is ending. */
  stopping?: boolean;
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

// ---------------------------------------------------------------------------
// Error envelope (backend error responses)
// ---------------------------------------------------------------------------

export interface ApiError {
  ok: false;
  error: string;
}
