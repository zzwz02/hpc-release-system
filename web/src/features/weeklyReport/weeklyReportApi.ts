import { apiFetch, apiGet, apiPost } from "../../api/http";
import { triggerBlobDownload } from "../../lib/csv";
import type { LlmTotals } from "../../lib/llmProgress";

export const WEEKLY_CHECK_KEY = ["weekly-report"] as const;
export const weeklyReportWeeksKey = (viewer: string) => ["weekly-report", "weeks", viewer] as const;
export const weeklyReportWeekKey = (weekKey: string, viewer: string) =>
  ["weekly-report", "week", weekKey, viewer] as const;
export const WEEKLY_CHECK_MEMBERS_KEY = ["weekly-report", "members"] as const;

export interface WeekListItem {
  week_key: string;
  label: string;
  week_start: string;
  week_end: string;
  finding_count: number;
}

export interface WeekPerson {
  sheet: string;
  date: string;
  item_count: number;
  note: string;
  /** JIRA / domain account from the manual table; "" when the sheet has no row. */
  username: string;
  /** The account's name as the site knows it; "" for an account it does not know. */
  display_name: string;
}

/** How one sheet of an upload resolves through the manual table. */
export interface SheetMapping {
  sheet: string;
  username: string;
  display_name: string;
  status: "check" | "skip";
  reason: string;
}

export interface WeekSummary {
  total: number;
  by_problem: { problem: string; count: number }[];
  by_person: { person: string; count: number }[];
  people_count: number;
  consistency: boolean;
  window_start: string;
  not_checked: string[];
  unmapped_sheets: string[];
  people: WeekPerson[];
  /** Absent on weeks analysed before these were recorded. */
  elapsed_seconds?: number;
  llm?: LlmTotals;
}

export interface WeekDetail extends WeekListItem {
  report_date: string;
  xlsx_name: string;
  xlsx_size: number;
  uploaded_by: string;
  uploaded_at: string;
  analyzed_by: string;
  analyzed_at: string;
  active_hours: number;
  summary: WeekSummary;
  /** Browser-facing JIRA issue prefix; empty when [jira] is not configured. */
  jira_browse_url: string;
  problem_types: string[];
}

export interface Finding {
  id: number;
  person: string;
  sheet_date: string;
  problem: string;
  item: string;
  jira_key: string;
  jira_status: string;
  assignee: string;
  last_updated: string;
  last_updater: string;
  note: string;
}

export interface UploadPreview {
  upload_id: string;
  filename: string;
  size: number;
  uploaded_by: string;
  uploaded_at: string;
  report_date: string;
  week: { week_key: string; week_start: string; week_end: string; label: string } | null;
  week_exists: boolean;
  people_count: number;
  mapping: SheetMapping[];
  unmapped_sheets: string[];
  undated_sheets: string[];
  active_hours: number;
}

export interface AnalysisJob {
  job_id: string;
  status: "running" | "completed" | "failed" | "cancelled";
  /** A stop was requested; the job ends once the LLM / JIRA calls under way return. */
  stopping: boolean;
  message: string;
  done: number;
  total: number;
  week_key: string;
  error: string;
  elapsed_seconds: number;
  llm: LlmTotals;
}

export interface Member {
  sheet: string;
  jira_username: string;
  skip: boolean;
  note: string;
}

export interface MembersResponse {
  members: Member[];
  version: string;
}

export async function listWeeks(): Promise<WeekListItem[]> {
  return (await apiGet<{ weeks: WeekListItem[] }>("/api/weekly-report/weeks")).weeks;
}

export async function getWeek(weekKey: string): Promise<{ week: WeekDetail; findings: Finding[] }> {
  const base = `/api/weekly-report/weeks/${encodeURIComponent(weekKey)}`;
  const [detail, list] = await Promise.all([
    apiGet<{ week: WeekDetail }>(base),
    apiGet<{ findings: Finding[] }>(`${base}/findings`),
  ]);
  return { week: detail.week, findings: list.findings };
}

/** The workbook goes up as the raw request body; the server never stores it. */
export function uploadReport(file: File): Promise<UploadPreview> {
  return apiFetch<UploadPreview>(`/api/weekly-report/uploads?filename=${encodeURIComponent(file.name)}`, {
    method: "POST",
    body: file,
    headers: { "Content-Type": "application/octet-stream" },
  });
}

export function startAnalysis(
  uploadId: string,
  options: { report_date: string; active_hours: number; consistency: boolean },
): Promise<AnalysisJob> {
  return apiPost<AnalysisJob>(`/api/weekly-report/uploads/${encodeURIComponent(uploadId)}/analyze`, options);
}

export function getJob(jobId: string): Promise<AnalysisJob> {
  return apiGet<AnalysisJob>(`/api/weekly-report/jobs/${encodeURIComponent(jobId)}`);
}

export function cancelJob(jobId: string): Promise<AnalysisJob> {
  return apiPost<AnalysisJob>(`/api/weekly-report/jobs/${encodeURIComponent(jobId)}/cancel`, {});
}

export function getMembers(): Promise<MembersResponse> {
  return apiGet<MembersResponse>("/api/weekly-report/members");
}

export function saveMembers(members: Member[], version: string): Promise<MembersResponse> {
  return apiFetch<MembersResponse>("/api/weekly-report/members", {
    method: "PUT",
    body: JSON.stringify({ members, version }),
  });
}

/** Download an export as the server wrote it (the CSV keeps its BOM). */
export async function downloadExport(week: WeekDetail, format: "md" | "csv"): Promise<void> {
  const res = await fetch(
    `/api/weekly-report/weeks/${encodeURIComponent(week.week_key)}/export?format=${format}`,
    { credentials: "include" },
  );
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error ?? res.statusText);
  }
  triggerBlobDownload(`weekly_jira_check_${week.report_date}.${format}`, await res.blob());
}

/** ISO week key (2026-W41) of a YYYY-MM-DD date; "" when the text is not a date. */
export function isoWeekKey(dateText: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText.trim());
  if (!match) return "";
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return "";
  // The ISO year and week are those of the week's Thursday.
  date.setUTCDate(date.getUTCDate() + 4 - (date.getUTCDay() || 7));
  const yearStart = Date.UTC(date.getUTCFullYear(), 0, 1);
  const week = Math.ceil(((date.getTime() - yearStart) / 86_400_000 + 1) / 7);
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

export function formatFileSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(size / 1024))} KB`;
}

const SKIP_WORDS = new Set(["不检查", "skip", "1", "true", "yes", "y", "是"]);

/**
 * Parse pasted manual rows, one per line, cells split by comma or tab:
 * `页名,JIRA 用户名[,不检查]`.  A `sheet,...` header line is skipped.
 */
export function parseMemberRows(text: string): { sheet: string; jira_username: string; skip: boolean }[] {
  const rows: { sheet: string; jira_username: string; skip: boolean }[] = [];
  for (const line of text.replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const [sheet = "", username = "", skip = ""] = line.split(/[,\t]/).map((cell) => cell.trim());
    if (!sheet || sheet.toLowerCase() === "sheet") continue;
    rows.push({ sheet, jira_username: username, skip: SKIP_WORDS.has(skip.toLowerCase()) });
  }
  return rows;
}
