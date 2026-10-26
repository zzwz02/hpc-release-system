import { apiGet, apiPost, apiGetBlob } from "../../api/http";

export interface LearningJob {
  id: string;
  issue_key: string;
  issue_keys?: string[];
  title?: string;
  successor_id?: string;
  agent_group: string;
  requested_by: string;
  parent_job_id: string;
  status: "queued" | "collecting" | "running" | "completed" | "failed" | "cancelled" | "cancelling" | "recovering";
  readonly?: boolean;
  phase?: string;
  repository_only?: boolean;
  pending_count?: number;
  update_count?: number;
  source_available: boolean;
  can_message: boolean;
  acquisition_status: string;
  error: string;
  created_at: string;
  started_at: string;
  finished_at: string;
}

export const LEARNING_JOB_LABELS: Record<LearningJob["status"], string> = {
  queued: "排队中",
  collecting: "采集材料",
  running: "学习中",
  completed: "整理完成",
  cancelled: "已取消",
  cancelling: "正在停止",
  recovering: "核对远端状态",
  failed: "学习失败",
};

export const LEARNING_JOB_TONES: Record<LearningJob["status"], string> = {
  queued: "warn",
  collecting: "accent",
  running: "accent",
  completed: "ok",
  cancelled: "muted",
  cancelling: "warn",
  recovering: "warn",
  failed: "bad",
};

export interface LearningTurn {
  id: string;
  job_id: string;
  seq: number;
  trigger: "initial" | "followup";
  created_by: string;
  input_text: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  error: string;
  created_at: string;
  started_at: string;
  finished_at: string;
}

export interface LearningEvent {
  id: string;
  job_id: string;
  turn_id: string;
  seq: number;
  kind: "user_message" | "agent_message" | "reasoning" | "status" | "error" | "tool";
  payload: Record<string, unknown>;
  created_at: string;
}

export interface LearningProposal {
  id: string;
  job_id: string;
  kind: "case" | "skill_update" | "new_skill" | "do_not_publish";
  title: string;
  summary: string;
  target_path: string;
  proposed_text: string;
  patch_text: string;
  applicability: string;
  evidence_ids: string[];
  case_keys?: string[];
  limitations: string;
  material_gaps?: { source_id: string; name: string; reason: string; impact: string }[];
  validation: string;
  status: "pending" | "approved" | "rejected" | "needs_revision" | "published";
  published_commit: string;
  turn_id?: string;
  files?: { path: string; previous_text: string | null; updated_text: string; base_hash: string | null }[];
  base_commit?: string;
  repository_ref?: string;
  previous_id?: string;
  superseded_by?: string;
  version?: number;
  review_blocker?: string;
  warnings?: { job_id?: string; proposal_id: string; title: string; status?: string; reason: string; paths: string[] }[];
  deployments?: { id: string; target: string; commit_sha: string; result: string; note: string; recorded_by: string; created_at: string; verification: string }[];
  reviews: {
    id: string;
    reviewer: string;
    decision: string;
    note: string;
    commit_sha: string;
    created_at: string;
  }[];
}

export interface LearningSource {
  id: string;
  kind: string;
  text?: string;
  issue_key?: string;
  issue_url?: string;
  name?: string;
  author?: string;
  created_at?: string;
  independent: boolean;
  archive?: { source_id: string; name: string; sha256: string; member: string };
  visibility?: unknown;
  conversation_id?: string;
  turn_id?: string;
  turn_seq?: number;
  turn_status?: string;
  range?: { start: number; end: number; unit: string };
  copies?: { id: string; name?: string; conversation_id?: string; turn_id?: string }[];
}

export interface LearningProgress { total: number; ready: number; pending: number; limited: number }

const BASE = "/api/jira-agent/learning";

export function listLearningJobs(issueKey = "", offset = 0) {
  return apiGet<{ jobs: LearningJob[]; has_more?: boolean }>(BASE + `/jobs?group=HPC&issue_key=${encodeURIComponent(issueKey)}&offset=${offset}`);
}

export function createLearningJobs(issueKeys: string[], instructions = "", repositoryOnly = false, title = "", requestId = "") {
  return apiPost<{
    job: LearningJob;
    reused: boolean;
  }>(BASE + "/jobs", {
    issue_keys: issueKeys,
    title,
    request_id: requestId,
    instructions,
    repository_only: repositoryOnly,
    agent_group: "HPC",
  });
}

export function getLearningJob(id: string, after = 0) {
  return apiGet<{
    job: LearningJob;
    turns: LearningTurn[];
    events: LearningEvent[];
    proposals: LearningProposal[];
    knowledge?: LearningKnowledge;
    progress?: LearningProgress;
    collection_gaps?: { source: string; name?: string; reason: string; issue_key?: string; category?: string }[];
    combination?: { id: string; job_id: string; title: string; status: string }[];
  }>(
    BASE + "/jobs/" + encodeURIComponent(id) + (after ? `?after=${after}` : ""),
  );
}

export function sendLearningMessage(id: string, text: string) {
  return apiPost<{ job: LearningJob; turn: LearningTurn; mode: "queued" | "merged" }>(
    BASE + "/jobs/" + encodeURIComponent(id) + "/messages",
    { text },
  );
}

export function reviewLearningProposal(id: string, decision: string, note: string) {
  return apiPost(
    BASE + "/proposals/" + encodeURIComponent(id) + "/review",
    { decision, note },
  );
}

export interface LearningKnowledge {
  captured_at?: string;
  repository_only?: boolean;
  differences?: string[];
  repository?: { commit?: string; ref?: string; hashes?: Record<string, string> };
  analysis?: { server?: string; captured_at?: string; deployed_commit?: string; hashes?: Record<string, string> };
}

export interface LearningIssue {
  key: string;
  summary: string;
  status: string;
  assignee?: { name: string; display_name: string } | null;
  learning_origin?: string;
  learning?: LearningJob | null;
  learning_counts?: { pending: number; update: number; participation?: number } | null;
}

export function listLearningIssues() {
  return apiGet<{ issues: LearningIssue[]; missing: string[]; total: number }>(BASE + "/issues?group=HPC");
}

export function previewLearningIssues(query: string) {
  return apiGet<{ issues: LearningIssue[]; total: number }>(BASE + "/issues/preview?query=" + encodeURIComponent(query));
}

export function addLearningIssues(keys: string[]) {
  return apiPost<{ issues: LearningIssue[]; missing: string[] }>(BASE + "/issues", { issue_keys: keys, agent_group: "HPC" });
}

export function operateLearningJob(id: string, action: "cancel" | "retry" | "refresh", repositoryOnly = false) {
  return apiPost<{ job: LearningJob }>(`${BASE}/jobs/${encodeURIComponent(id)}/${action}`, { repository_only: repositoryOnly });
}

export function recordLearningDeployment(jobId: string, target: string, commit: string, result: string, note: string) {
  return apiPost(`${BASE}/jobs/${encodeURIComponent(jobId)}/deployments`, { target, commit_sha: commit, result, note });
}

export function learningActive(status: LearningJob["status"]) {
  return ["queued", "collecting", "running", "cancelling", "recovering"].includes(status);
}


export function reviseLearningPublication(id: string, commit: string, note: string) {
  return apiPost(`${BASE}/proposals/${encodeURIComponent(id)}/publication-revision`, { commit_sha: commit, note });
}


export const exportLearningBundle = (id: string, proposals: string[]) => apiGetBlob(`${BASE}/jobs/${encodeURIComponent(id)}/patch-bundle?proposal_ids=${encodeURIComponent(proposals.join(","))}`);
export const publishLearningBundle = (id: string, proposals: string[], commit: string, note: string) => apiPost(`${BASE}/jobs/${encodeURIComponent(id)}/publication`, { proposal_ids: proposals, commit_sha: commit, note });

export interface LearningComparison {
  relation: { reason: string; paths: string[] };
  current: ComparisonProposal;
  other: ComparisonProposal;
}
interface ComparisonProposal {
  id: string; job_id: string; title: string; summary: string; status: string; base_commit: string; superseded_by: string;
  files: { path: string; patch: string }[];
}
export const compareLearningProposals = (id: string, other: string) => apiGet<LearningComparison>(`${BASE}/proposals/${encodeURIComponent(id)}/compare/${encodeURIComponent(other)}`);
export const combineLearningProposals = (ids: string[], note: string, request: string) => apiPost<{ job: LearningJob }>(`${BASE}/proposals/combine`, { proposal_ids: ids, note, request_id: request });
export const closeLearningDuplicate = (id: string, replacement: string) => apiPost(`${BASE}/proposals/${encodeURIComponent(id)}/close-duplicate`, { replacement_id: replacement });

export function listLearningMaterials(id: string, ids: string[]) {
  const params = new URLSearchParams({ limit: "100" });
  ids.forEach((value) => params.append("ids", value));
  return apiGet<{ items: LearningSource[] }>(`${BASE}/jobs/${encodeURIComponent(id)}/materials?${params}`);
}
export interface LearningRead {
  success: boolean;
  contentItems: { type: string; text?: string; imageUrl?: string }[];
}
export function readLearningMaterial(id: string, source: string, offset = 0) {
  const params = new URLSearchParams({ source_id: source, offset: String(offset) });
  return apiGet<LearningRead>(`${BASE}/jobs/${encodeURIComponent(id)}/material?${params}`);
}
