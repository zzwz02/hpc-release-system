import { apiFetch, apiGet, apiPost } from "../../api/http";

export const JIRA_AGENT_KEY = ["jira-agent"] as const;
export const JIRA_AGENT_CONVERSATIONS_KEY = ["jira-agent", "conversations"] as const;
export const jiraAgentConversationKey = (id: string) => ["jira-agent", "conversation", id] as const;

export type TurnStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
export type ConversationState =
  | "idle"
  | "queued"
  | "running"
  | "waiting_review"
  | "delivering"
  | "delivery_incomplete"
  | "failed"
  | "cancelled"
  | "interrupted"
  | "closed";
export type CommentStatus = "" | "pending" | "posted" | "failed";

export interface AgentResult {
  conclusion: string;
  /** Required on new results; absent on historical result JSON. */
  action_required?: string;
  assistance?: { current_issue: string; owner: string; request: string };
  issue_category: string;
  ownership: { belongs_to_us: string; target_group: string; reasoning: string };
  summary: string;
  root_cause: string;
  /** user@host the agent actually used; absent in results from before machines existed. */
  machine?: string;
  reproduction: { reproduced: boolean; environment: string; steps: string[] };
  evidence: { description: string; command: string; result: string }[];
  fix: { description: string };
  artifacts: string[];
  next_steps: string[];
  skills_used: string[];
}

export interface AgentTurn {
  id: string;
  conversation_id: string;
  seq: number;
  trigger: "handover" | "followup";
  created_by: string;
  input_text: string;
  status: TurnStatus;
  codex_turn_id: string;
  result: AgentResult | null;
  conclusion: string;
  comment_status: CommentStatus;
  delivery_status: "" | "collecting" | "incomplete" | "complete";
  delivery_errors: { path: string; error: string }[];
  // false = the result stays on the website; no JIRA comment
  post_comment: boolean;
  comment_id: string;
  comment_body: string;
  comment_error: string;
  error: string;
  created_at: string;
  started_at: string;
  finished_at: string;
  queue_position: number | null;
}

export interface AgentConversation {
  id: string;
  issue_key: string;
  issue_summary: string;
  agent_group: string;
  owner: string;
  created_by: string;
  thread_id: string;
  workspace: string;
  /** "" = the agent picks from the system machine list; else user@host. */
  machine: string;
  /** user@host the agent reported using; "" until a turn with a machine ends. */
  machine_used: string;
  status: "open" | "closed";
  close_reason: "" | "superseded" | "new_conversation";
  created_at: string;
  updated_at: string;
  closed_at: string;
  state: ConversationState;
  phase: string;
  latest_turn: AgentTurn | null;
  read_only: boolean;
  can_write: boolean;
  browse_url: string;
}

export interface AgentEvent {
  id: string;
  conversation_id: string;
  turn_id: string;
  seq: number;
  rev: number;
  item_id: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AgentFile {
  id: string;
  conversation_id: string;
  turn_id: string;
  direction: "input" | "output";
  source: "jira" | "upload" | "artifact";
  source_ref: string;
  name: string;
  size: number;
  sha256: string;
  remote_path: string;
  created_at: string;
  downloadable: boolean;
  preview: string;
  preview_truncated: boolean;
}

export interface IssueConversationRef {
  id: string;
  owner: string;
  status: "open" | "closed";
  close_reason: string;
  created_at: string;
}

export interface ConversationDetail {
  conversation: AgentConversation;
  turns: AgentTurn[];
  files: AgentFile[];
  events: AgentEvent[];
  rev: number;
  issue_conversations: IssueConversationRef[];
}

export interface EventsResponse {
  conversation: AgentConversation;
  events: AgentEvent[];
  rev: number;
}

export interface IssuePreview {
  issue: {
    key: string;
    url: string;
    summary: string;
    issue_type: string;
    status: string;
    priority: string;
    assignee: { name: string; display_name: string } | null;
    components: string[];
    attachment_count: number;
    comment_count: number;
  };
  /** Digital employee group that would handle the issue ("" when none is configured). */
  agent_group: string;
  can_handover: boolean;
  /** recovering: a restarted site is re-attaching to its running turn; closing it must wait. */
  open_conversation: { id: string; owner: string; owner_is_assignee: boolean; recovering?: boolean } | null;
  conversations: AgentConversation[];
}

export interface IssueSearchItem {
  key: string;
  url: string;
  summary: string;
  issue_type: string;
  status: string;
  priority: string;
  assignee: { name: string; display_name: string } | null;
  components: string[];
  updated: string;
  can_handover: boolean;
  /** id is empty when the conversation belongs to someone the viewer cannot see. */
  open_conversation: {
    id: string;
    owner: string;
    owner_is_assignee: boolean;
    state: ConversationState;
  } | null;
  /** Only in the RM "handled" list. */
  agent?: {
    conversation_count: number;
    last_activity: string;
    latest_conversation: {
      id: string;
      owner: string;
      state: ConversationState;
      conclusion: string;
      action_required?: string;
    };
  };
}

export interface IssueSearchResponse {
  mode: "mine" | "key" | "jql" | "handled";
  jql: string;
  total: number;
  missing: string[];
  issues: IssueSearchItem[];
}

export const JIRA_AGENT_ISSUE_SEARCH_KEY = ["jira-agent", "issues"] as const;
export const jiraAgentIssueSearchKey = (query: string) => ["jira-agent", "issues", query] as const;
export const JIRA_AGENT_HANDLED_ISSUES_KEY = ["jira-agent", "issues", { scope: "handled" }] as const;
export const jiraAgentIssueKey = (issueKey: string) => ["jira-agent", "issue", issueKey] as const;

export function searchIssues(query: string) {
  return apiGet<IssueSearchResponse>(`/api/jira-agent/issues?q=${encodeURIComponent(query)}`);
}

/** RM only: every issue the agent has handled, JIRA-closed ones included. */
export function listHandledIssues() {
  return apiGet<IssueSearchResponse>("/api/jira-agent/issues?scope=handled");
}

export interface UploadPayload {
  filename: string;
  content_base64: string;
}

export const CONCLUSION_LABELS: Record<string, string> = {
  insufficient_evidence: "暂时无法判断",
  reproduced: "问题已复现",
  cannot_reproduce: "当前未复现",
  scope_narrowed: "已定位到具体范围",
  root_cause_confirmed: "已找到原因",
  fix_prepared: "已有修复方案，待验证",
  fixed_pending_review: "修复已验证，待审核",
  analysis_done: "已完成分析",
  not_our_group: "建议由其他组处理",
};

export const ACTION_REQUIRED_LABELS: Record<string, string> = {
  none: "",
  needs_info: "需要补充",
  needs_help: "需要协助",
  needs_review: "需要审核",
  needs_handoff: "需要转交",
};

const LEGACY_CONCLUSIONS: Record<string, string> = {
  needs_info: "insufficient_evidence",
  needs_help: "insufficient_evidence",
};

const LEGACY_ACTIONS: Record<string, string> = {
  fixed_pending_review: "needs_review",
  needs_info: "needs_info",
  needs_help: "needs_help",
  not_our_group: "needs_handoff",
};

export function resultState(conclusion: string, action?: string) {
  return {
    conclusion: LEGACY_CONCLUSIONS[conclusion] ?? conclusion,
    action: action !== undefined && ACTION_REQUIRED_LABELS[action] !== undefined
      ? action
      : (LEGACY_ACTIONS[conclusion] ?? "none"),
  };
}

export const OWNERSHIP_LABELS: Record<string, string> = {
  yes: "属于本组",
  no: "不属于本组",
  unclear: "暂无法判断",
};

export const STATE_LABELS: Record<ConversationState, string> = {
  idle: "未开始",
  queued: "排队中",
  running: "处理中",
  waiting_review: "待 assignee 审阅",
  delivering: "分析已完成，正在回收文件",
  delivery_incomplete: "分析已完成，交付不完整",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  closed: "已结束（只读）",
};

export const CLOSE_REASON_LABELS: Record<string, string> = {
  superseded: "JIRA assignee 已变更，本对话已结束",
  new_conversation: "已新建对话，本对话已结束",
};

export function listConversations() {
  return apiGet<{ conversations: AgentConversation[] }>("/api/jira-agent/conversations");
}

export function previewIssue(issueKey: string) {
  return apiGet<IssuePreview>(`/api/jira-agent/issues/${encodeURIComponent(issueKey)}`);
}

export function handoverIssue(body: {
  issue_key: string;
  note?: string;
  files?: UploadPayload[];
  new_conversation?: boolean;
  machine?: string;
  /** The key upload to a user@host machine that just passed; each hand-over needs its own. */
  ssh_key_session_id?: string;
  post_comment?: boolean;
}) {
  return apiPost<{ created: boolean; conversation: AgentConversation }>(
    "/api/jira-agent/conversations",
    body,
  );
}

export function getConversation(id: string) {
  return apiGet<ConversationDetail>(`/api/jira-agent/conversations/${encodeURIComponent(id)}`);
}

export function getConversationEvents(id: string, after: number) {
  return apiGet<EventsResponse>(
    `/api/jira-agent/conversations/${encodeURIComponent(id)}/events?after=${after}`,
  );
}

export function sendConversationMessage(
  id: string,
  body: { text: string; files?: UploadPayload[]; post_comment?: boolean },
) {
  return apiPost<{ mode: "steer" | "merged" | "queued"; conversation: AgentConversation }>(
    `/api/jira-agent/conversations/${encodeURIComponent(id)}/messages`,
    body,
  );
}

export function cancelConversationTurn(id: string) {
  return apiPost<{ conversation: AgentConversation }>(
    `/api/jira-agent/conversations/${encodeURIComponent(id)}/cancel`,
    {},
  );
}

export function retryTurnDelivery(turnId: string) {
  return apiPost<{ turn: AgentTurn }>(
    `/api/jira-agent/turns/${encodeURIComponent(turnId)}/delivery/retry`,
    {},
  );
}

export function retryTurnComment(turnId: string) {
  return apiPost<{ turn: AgentTurn }>(
    `/api/jira-agent/turns/${encodeURIComponent(turnId)}/comment/retry`,
    {},
  );
}

export function fileDownloadUrl(conversationId: string, fileId: string): string {
  return `/api/jira-agent/conversations/${encodeURIComponent(conversationId)}/files/${encodeURIComponent(fileId)}`;
}

export async function fileToUpload(file: File): Promise<UploadPayload> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return { filename: file.name, content_base64: btoa(binary) };
}

// ─────────────────────────────────────────────────────────────
// Execution machines
// ─────────────────────────────────────────────────────────────

export interface AgentMachine {
  id: string;
  agent_group: string;
  ssh_target: string;
  description: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface MachinesResponse {
  groups: { name: string; display_name: string }[];
  machines: AgentMachine[];
  can_manage: boolean;
}

export interface SshKeyInfo {
  agent_group: string;
  display_name: string;
  fingerprint: string;
  comment: string;
  path: string;
}

export type KeySessionStatus = "running" | "verifying" | "succeeded" | "failed" | "closed";

export interface KeySession {
  id: string;
  agent_group: string;
  target: string;
  fingerprint: string;
  status: KeySessionStatus;
  message: string;
  exit_code: number | null;
  /** Terminal output after the requested offset. */
  output: string;
  offset: number;
}

export const JIRA_AGENT_MACHINES_KEY = ["jira-agent", "machines"] as const;
export const jiraAgentSshKeyInfoKey = (group: string) => ["jira-agent", "ssh-key-info", group] as const;

/** A system machine or a user-given ssh target: user@host, no port. */
export const SSH_TARGET_RE = /^[A-Za-z_][A-Za-z0-9_.-]{0,31}@[A-Za-z0-9][A-Za-z0-9.-]*$/;

export function listMachines() {
  return apiGet<MachinesResponse>("/api/jira-agent/machines");
}

export function createMachine(body: { agent_group: string; ssh_target: string; description: string }) {
  return apiPost<{ machine: AgentMachine }>("/api/jira-agent/machines", body);
}

export function updateMachine(id: string, body: { ssh_target: string; description: string }) {
  return apiFetch<{ machine: AgentMachine }>(`/api/jira-agent/machines/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteMachine(id: string) {
  return apiFetch<{ ok: boolean }>(`/api/jira-agent/machines/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function getSshKeyInfo(group: string) {
  return apiGet<SshKeyInfo>(`/api/jira-agent/ssh-key-info?group=${encodeURIComponent(group)}`);
}

export function startKeySession(body: { agent_group: string; target: string }) {
  return apiPost<KeySession>("/api/jira-agent/ssh-key-sessions", body);
}

export function getKeySession(id: string, after: number) {
  return apiGet<KeySession>(`/api/jira-agent/ssh-key-sessions/${encodeURIComponent(id)}?after=${after}`);
}

export function sendKeySessionInput(id: string, data: string) {
  return apiPost<{ ok: boolean }>(`/api/jira-agent/ssh-key-sessions/${encodeURIComponent(id)}/input`, { data });
}

export function closeKeySession(id: string) {
  return apiPost<KeySession>(`/api/jira-agent/ssh-key-sessions/${encodeURIComponent(id)}/close`, {});
}
