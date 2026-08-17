import { apiGet, apiPost } from "../../api/http";

export const CICD_AGENT_FAILURES_KEY = ["cicd-agent", "failures"] as const;
export const CICD_AGENT_FILTER_OPTIONS_KEY = ["cicd-agent", "filter-options"] as const;
export const CICD_AGENT_SUMMARY_KEY = ["cicd-agent", "summary"] as const;

export interface FailureRecordFilters {
  date_from?: string;
  date_to?: string;
  job_name?: string;
  job_type?: string;
  normalized_stage?: string;
  owner_role?: string;
  code_owner?: string;
  owner_account?: string;
  official_name?: string;
  app_version?: string;
  git_url?: string;
  git_branch?: string;
  maca_project?: string;
  maca_version?: string;
  arch?: string;
  chip?: string;
  failure_category?: string;
  matched_rule_id?: string;
  confidence?: string;
  need_human_review?: boolean;
  notification_status?: string;
  keyword?: string;
}

export interface FailureRecordListItem {
  id: number;
  job_name: string;
  build_number: number;
  build_url?: string | null;
  result?: string | null;
  failed_stage?: string | null;
  normalized_stage?: string | null;
  job_type?: string | null;
  build_type?: string | null;
  arch?: string | null;
  maca_project?: string | null;
  maca_version?: string | null;
  chip?: string | null;
  owner_role?: string | null;
  code_owner?: string | null;
  owner_account?: string | null;
  official_name?: string | null;
  app_version?: string | null;
  git_url?: string | null;
  git_branch?: string | null;
  failure_category?: string | null;
  matched_rule_id?: string | null;
  confidence?: string | null;
  need_human_review?: boolean;
  reason_summary?: string | null;
  key_error_snippet?: string | null;
  action_suggestion?: string | null;
  notification_status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface FailureRecordDetail extends FailureRecordListItem {
  status?: string | null;
  node_id?: string | null;
  node_result?: string | null;
  log_source?: string | null;
  owner_reason?: string | null;
  route_reason?: string | null;
  rag_used?: boolean;
  rag_source?: string | null;
  rag_query?: string | null;
  rag_error?: string | null;
  provider?: string | null;
  model?: string | null;
  llm_error?: string | null;
  notification_mode?: string | null;
  notification_channels?: string[];
  notification_errors?: string[];
  notified_at?: string | null;
  daily_reported_at?: string | null;
}

export interface FailureRecordListResponse {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  records: FailureRecordListItem[];
}

export interface FailureFilterOptionsResponse {
  job_types: string[];
  stages: string[];
  owner_roles: string[];
  owners: Array<{ code_owner: string }>;
  owner_accounts: string[];
  git_urls: string[];
  git_branches: string[];
  maca_projects: string[];
  maca_versions: string[];
  arches: string[];
  chips: string[];
  failure_categories: string[];
  matched_rule_ids: string[];
  notification_statuses: string[];
  confidences: string[];
  job_names: string[];
}

export interface FailureSummaryGroup {
  group: string;
  failure_count: number;
  job_count: number;
  project_count: number;
  human_review_count: number;
  latest_created_at?: string | null;
}

export interface FailureSummaryResponse {
  group_by: string;
  total_records: number;
  groups: FailureSummaryGroup[];
}

export interface FailureChatResponse {
  answer: string;
  query: Record<string, unknown>;
  records: FailureRecordListItem[];
  summary: Record<string, unknown>;
  need_clarification?: boolean;
  provider?: string | null;
  model?: string | null;
  error?: string | null;
}

function compactParams(values: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(values)
      .filter(([, value]) => value !== undefined && value !== null && value !== "")
      .map(([key, value]) => [key, String(value)]),
  );
}

export function todayString(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function buildQuery(values: Record<string, unknown>): string {
  const params = new URLSearchParams(compactParams(values));
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function fetchFailureRecords(
  filters: FailureRecordFilters,
  page: number,
): Promise<FailureRecordListResponse> {
  return apiGet<FailureRecordListResponse>(
    `/api/cicd-agent/failures${buildQuery({
      ...filters,
      page,
      page_size: 20,
      sort_by: "created_at",
      sort_order: "desc",
    })}`,
  );
}

export function fetchFailureDetail(recordId: number): Promise<FailureRecordDetail> {
  return apiGet<FailureRecordDetail>(`/api/cicd-agent/failures/${recordId}`);
}

export function fetchFailureFilterOptions(
  filters: Pick<FailureRecordFilters, "date_from" | "date_to">,
): Promise<FailureFilterOptionsResponse> {
  return apiGet<FailureFilterOptionsResponse>(
    `/api/cicd-agent/failures/filter-options${buildQuery(filters)}`,
  );
}

export function fetchFailureSummary(
  filters: FailureRecordFilters,
  groupBy = "normalized_stage",
): Promise<FailureSummaryResponse> {
  return apiGet<FailureSummaryResponse>(
    `/api/cicd-agent/failures/summary${buildQuery({ ...filters, group_by: groupBy })}`,
  );
}

export function sendFailureChat(
  message: string,
  filters: FailureRecordFilters,
): Promise<FailureChatResponse> {
  return apiPost<FailureChatResponse>("/api/cicd-agent/failure-chat", {
    message,
    filters,
    limit: 50,
  });
}
