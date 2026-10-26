import { apiFetch, apiGet, apiGetBlob, apiPost } from "../../api/http";

export interface TestInput {
  issue: { summary: string; description?: string; [key: string]: unknown };
  note: string;
  files: { path: string; content_base64: string; sha256?: string }[];
}
export interface CaseDraft {
  title: string;
  input: TestInput;
  provenance: Record<string, unknown>;
  notice?: string;
  package_import?: boolean;
  import_update?: string;
  criteria?: string;
  reference?: string;
  duplicates?: { id: string; title: string; review_differs?: boolean; criteria?: string; reference?: string }[];
  same_name?: { id: string; title: string }[];
}
export interface CaseVersion extends CaseDraft {
  id: string;
  version: number;
  input_sha256: string;
}
export const REVIEW_LABELS = {
  pending: "待评价", pass: "通过", partial: "部分符合", fail: "不符合",
  unknown: "无法判定", environment: "环境受限", platform: "平台故障", cancelled: "已取消",
};
export type ReviewDecision = Exclude<keyof typeof REVIEW_LABELS, "pending">;
export interface TestRun {
  conversation_id: string;
  ordinal: number;
  status: string;
  delivery_status: string;
  error: string;
  conclusion: string;
  machine_used: string;
  started_at: string;
  finished_at: string;
  evidence_key?: string;
  evaluation?: Evaluation | null;
  current?: { decision: string; source: "human" | "ai" | "system"; note: string };
  reviews: { current?: boolean; id: string; decision: ReviewDecision; note: string; reviewer: string; created_at: string }[];
}
export interface TestBatch {
  id: string;
  version_id: string;
  version: number;
  created_at: string;
  config: { model: string; effort: string; machine: string; skill_commit: string; skill_source?: string; skill_sha256: string; input_sha256: string };
  counts: Record<keyof typeof REVIEW_LABELS, number>;
  runs: TestRun[];
}
export interface CaseDetail {
  id: string; versions: CaseVersion[]; batches: TestBatch[]; existing?: boolean;
  builtin?: { key: string; update_available: boolean } | null;
}
export interface TestOptions {
  cases: { id: string; title: string; version: number; version_id?: string; criteria?: string; builtin_key?: string; builtin_update?: boolean }[];
  builtin_warning?: string;
  groups: { name: string; display_name: string; model: string; machines: { ssh_target: string; description: string }[] }[];
}
const base = "/api/jira-agent/testing";
export const listTestCases = () => apiGet<TestOptions>(`${base}/cases`);
export const getTestCase = (id: string) => apiGet<CaseDetail>(`${base}/cases/${id}`);
export const getTestBatches = (id: string) => apiGet<{ batches: TestBatch[] }>(`${base}/cases/${id}/batches`);
export const importTestCase = (conversation: string) => apiGet<CaseDraft>(`${base}/import/${conversation}`);
export const previewTestPackage = (file: File) => apiFetch<CaseDraft>(`${base}/packages/preview`, {
  method: "POST", headers: { "Content-Type": "application/zip" }, body: file,
});
export const exportTestPackage = (id: string, version: string) => apiGetBlob(`${base}/cases/${id}/versions/${version}/package`);
export const saveTestCase = (draft: CaseDraft, id = "") => apiPost<CaseDetail>(`${base}/cases${id ? `/${id}/versions` : ""}`, draft);
export const startTestBatch = (id: string, body: {
  version_id: string; agent_group: string; model: string; effort: string; machine: string; ssh_key_session_id?: string;
}) => apiPost<CaseDetail>(`${base}/cases/${id}/batches`, body);
export const reviewTestRun = (id: string, decision: ReviewDecision, note: string, extra: { confirm_ai?: boolean; evaluation_id?: string; evidence_key?: string } = {}) => apiPost(`${base}/runs/${id}/review`, { decision, note, ...extra });
export const cancelTestBatch = (id: string) => apiPost(`${base}/batches/${id}/cancel`, {});

export interface Evaluation {
  id: string; status: string; error: string; created_at: string; finished_at: string;
  evidence_key: string; material_hash: string;
  snapshot: { criteria: { id: string; text: string }[]; reference: string; sha256: string };
  result: { decision?: ReviewDecision; summary?: string; criteria?: { id: string; status: "met" | "unmet" | "unknown"; reason: string; evidence_ids: string[] }[] };
  config: { model: string; effort: string; rule_sha256: string };
}
export interface TestSuite {
  id: string; status: string; error: string; created_at: string; active: boolean; total: number; finished: number;
  phase: string; evaluated: number; reviewed: number;
  counts: Record<string, number>; ai_counts?: Record<string, number>;
  entries: { case_id: string; version_id: string; title: string; batch: TestBatch | null }[];
}
export const CURRENT_LABELS: Record<string, string> = { ...REVIEW_LABELS, pending: "待初评", legacy: "未初评（历史记录）", no_criteria: "未设置通过标准", no_result: "未形成分析结果", evaluation_failed: "初评失败", skipped: "未初评", stale: "材料已更新", preparing: "准备中", preparation_failed: "准备失败" };
export const SUITE_LABELS: Record<string, string> = { preparing: "准备中", preparation_failed: "准备失败", running: "执行中", evaluating: "初评中", completed: "已完成", stopped: "已停止" };
export const suiteSummary = (suite: TestSuite) => suite.total > 0 && suite.counts.pass === suite.total ? "全部通过"
  : Object.entries(suite.counts).filter(([, count]) => count > 0).map(([key, count]) => `${CURRENT_LABELS[key] ?? key} ${count}`).join(" · ") || "等待执行";
export const currentResult = (run: TestRun) => run.current ?? { decision: run.reviews[0]?.decision ?? "pending", source: run.reviews.length ? "human" : "system", note: run.reviews[0]?.note ?? "" };
export const resultLabel = (run: TestRun) => {
  const value = currentResult(run);
  const label = value.decision === "skipped" ? value.note : CURRENT_LABELS[value.decision] ?? value.decision;
  return label + (value.source === "human" ? " · 人工" : value.source === "ai" ? " · AI" : "");
};
export const listTestSuites = () => apiGet<{ suites: TestSuite[] }>(`${base}/suites`);
export const startTestSuite = (body: { request_id: string; case_ids: string[]; agent_group: string; model: string; effort: string; machine: string; ssh_key_session_id?: string }) => apiPost<TestSuite>(`${base}/suites`, body);
export const stopTestSuite = (id: string) => apiPost<TestSuite>(`${base}/suites/${id}/stop`, {});
export const retryTestSuite = (id: string) => apiPost<TestSuite>(`${base}/suites/${id}/retry`, {});
export const retryTestEvaluation = (id: string) => apiPost(`${base}/runs/${id}/evaluate`, {});
export const getTestEvaluations = (id: string) => apiGet<{ evaluations: Evaluation[] }>(`${base}/runs/${id}/evaluations`);
export const getTestEvidence = (id: string, evaluation: string) => apiGet<{ sources: { id: string; name?: string; text: string; truncated?: boolean }[]; collection_gaps: { source: string; reason: string }[] }>(`${base}/runs/${id}/evaluations/${evaluation}/evidence`);
export const draftTestCriteria = (agent_group: string, input: TestInput, reference: string) => apiPost<{ criteria: string }>(`${base}/criteria/draft`, { agent_group, input, reference });
