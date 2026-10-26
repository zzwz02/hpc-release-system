import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "../../lib/toast";
import { currentResult, CURRENT_LABELS, getTestEvidence, getTestEvaluations, retryTestEvaluation, type TestRun } from "./jiraTestingApi";

export function TestingEvaluation({ run, refresh }: { run: TestRun; refresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState(false);
  const [source, setSource] = useState("");
  const evaluation = run.evaluation;
  const materials = useQuery({ queryKey: ["jira-testing", "evidence", run.conversation_id, evaluation?.id],
    queryFn: () => getTestEvidence(run.conversation_id, evaluation!.id), enabled: !!source && !!evaluation });
  const records = useQuery({ queryKey: ["jira-testing", "evaluations", run.conversation_id, evaluation?.id],
    queryFn: () => getTestEvaluations(run.conversation_id), enabled: history });
  if (!evaluation) return null;
  const active = ["queued", "running"].includes(run.status) || run.delivery_status === "collecting";
  const canRetry = !active && ["failed", "completed"].includes(evaluation.status);
  const result = currentResult(run);
  const checked = evaluation.result.criteria?.filter((item) => item.status === "met").length ?? 0;
  const summary = evaluation.status === "completed" ? `${checked}/${evaluation.snapshot.criteria.length} 项满足`
    : ({ awaiting: "等待分析结束", queued: "等待初评", running: "初评中", failed: "初评失败", skipped: "未初评" }[evaluation.status] ?? "");
  async function retry() {
    setBusy(true);
    try { await retryTestEvaluation(run.conversation_id); refresh(); }
    catch (error) { toast.error(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  }
  return <details className="test-evaluation test-disclosure" aria-label="通过标准核对">
    <summary>通过标准核对 <span className="muted">{summary}</span></summary>
    <div className="test-disclosure-body">
    {["awaiting", "queued", "running"].includes(evaluation.status) && <p className="hint">{evaluation.status === "awaiting" ? "分析和文件回收结束后自动初评。" : evaluation.status === "queued" ? "等待 AI 初评。" : "AI 正在逐项核对。"}</p>}
    {evaluation.error && <p className="jira-agent-warning">{evaluation.error}</p>}
    {result.source === "human" && evaluation.result.decision && <p className="hint">AI 初评：{CURRENT_LABELS[evaluation.result.decision]}。当前采用人工评价。{evaluation.result.summary}</p>}
    {!!evaluation.snapshot.criteria.length && <div className="test-table-wrap"><table className="test-criteria-table"><thead><tr><th>标准</th><th>AI 核对</th><th>依据</th></tr></thead>
      <tbody>{evaluation.snapshot.criteria.map((criterion) => {
        const item = evaluation.result.criteria?.find((item) => item.id === criterion.id);
        return <tr key={criterion.id}><td>{criterion.text}</td><td>{item ? { met: "满足", unmet: "未满足", unknown: "证据不足" }[item.status] : "待核对"}</td>
          <td>{item?.reason}{item?.evidence_ids.map((id, index) => <button className="test-evidence-link" key={id} onClick={() => setSource(source === id ? "" : id)}>查看依据 {index + 1}</button>)}</td></tr>;
      })}</tbody></table></div>}
    {source && <div className="test-evidence"><div className="test-run-heading"><strong>引用材料</strong><button className="btn sm" onClick={() => setSource("")}>收起</button></div>
      {materials.isLoading && <p>读取中…</p>}{materials.isError && <p role="alert">材料读取失败</p>}
      {materials.data?.sources.filter((item) => item.id === source).map((item) => <div key={item.id}><p>{item.name || item.id}{item.truncated ? "（部分内容）" : ""}</p><pre>{item.text}</pre></div>)}
    </div>}
    <div className="test-evaluation-actions">{canRetry && <button className="btn sm" disabled={busy} onClick={() => void retry()}>{busy ? "提交中…" : "重新初评"}</button>}</div>
    <details className="test-run-config" onToggle={(event) => setHistory(event.currentTarget.open)}><summary>历史初评与参考结果</summary>
      {evaluation.snapshot.reference && <details><summary>本次参考结果</summary><pre>{evaluation.snapshot.reference}</pre></details>}
      {records.isError && <p role="alert">评价记录读取失败</p>}
      {records.data?.evaluations.map((item) => <p key={item.id}>{item.created_at} · {item.result.decision ? CURRENT_LABELS[item.result.decision] : ({ awaiting: "等待分析结束", queued: "等待初评", running: "初评中", failed: "初评失败", skipped: "未初评" }[item.status] ?? item.status)} · {item.result.summary || item.error}</p>)}
    </details>
    </div>
  </details>;
}
