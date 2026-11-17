import { apiFetch, apiGet, apiGetText, apiPost, apiPostNdjson } from "../../api/http";
import { triggerBlobDownload } from "../../lib/csv";
import type { LlmTotals } from "../../lib/llmProgress";

const BASE = "/api/weekly-report/auto";
export interface AutoOptions { group: string; since: string; until: string; report_date: string }
export interface AutoReportSummary {
  id: string; jira_group: string; since: string; until: string; report_date: string;
  created_by: string; created_at: string; revision: number;
}
export interface AutoReport extends AutoReportSummary {
  markdown: string; notes: string; llm: LlmTotals;
  jira_browse_url?: string;
  evidence: { warnings: string[]; issues: { key: string; summary: string }[]; skipped: string[]; reviews: string[] };
}
export interface AutoJob {
  id: string; status: "running" | "completed" | "failed" | "cancelled";
  stopping: boolean; message: string; error: string; user: string; options: AutoOptions;
  report_id: string; elapsed_seconds: number; llm: LlmTotals;
}
export const autoDefaults = () => apiGet<AutoOptions>(`${BASE}/defaults`);
export const autoReports = async () => (await apiGet<{ reports: AutoReportSummary[] }>(`${BASE}/reports`)).reports;
export const autoReport = (id: string) => apiGet<AutoReport>(`${BASE}/reports/${encodeURIComponent(id)}`);
export const generateReport = (options: AutoOptions) => apiPost<AutoJob>(`${BASE}/generate`, options);
export const autoJobs = async () => (await apiGet<{ jobs: AutoJob[] }>(`${BASE}/jobs`)).jobs;
export const cancelAutoJob = (id: string) => apiPost<AutoJob>(`${BASE}/jobs/${encodeURIComponent(id)}/cancel`, {});
export const watchAutoJob = (id: string, onItem: (job: AutoJob) => void, signal: AbortSignal) =>
  apiPostNdjson<AutoJob>(`${BASE}/jobs/${encodeURIComponent(id)}/watch`, {}, onItem, { signal });
export const saveAutoReport = (id: string, markdown: string, notes: string, revision: number) =>
  apiFetch<AutoReport>(`${BASE}/reports/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ markdown, notes, revision }) });
export async function downloadAutoReport(report: AutoReport) {
  const { text } = await apiGetText(`${BASE}/reports/${encodeURIComponent(report.id)}/export`);
  triggerBlobDownload(`周报_${report.report_date}_简洁版.md`, new Blob([text], { type: "text/markdown;charset=utf-8" }));
}
