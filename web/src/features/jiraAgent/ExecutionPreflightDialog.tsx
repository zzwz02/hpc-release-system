import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { SshKeyUploadDialog } from "./SshKeyUploadDialog";
import { checkExecution, executionRequestId, getSshKeyInfo, jiraAgentSshKeyInfoKey, type ExecutionConfig, type ExecutionPreflight, type ExecutionSubmission, type ExecutionTargetResult, type PreflightContext } from "./jiraAgentApi";

export function ExecutionPreflightDialog({ agentGroup, execution, context, onReady, onClose }: {
  agentGroup: string; execution: ExecutionConfig; context: PreflightContext;
  onReady: (submission: ExecutionSubmission) => Promise<void>; onClose: () => void;
}) {
  const checkDataset = execution.mode === "custom";
  const [sessions, setSessions] = useState<Record<string, string>>({});
  const [uploadTarget, setUploadTarget] = useState("");
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<ExecutionTargetResult[]>([]);
  const [receipt, setReceipt] = useState<ExecutionPreflight | null>(null);
  const [error, setError] = useState("");
  const controller = useRef<AbortController>();
  const requestId = useRef(executionRequestId());
  const keyQuery = useQuery({ queryKey: jiraAgentSshKeyInfoKey(agentGroup), queryFn: () => getSshKeyInfo(agentGroup), enabled: execution.mode === "custom" });
  useEffect(() => () => controller.current?.abort(), []);

  async function submit(value: ExecutionPreflight) {
    if (value.expires_at * 1000 <= Date.now()) { setError("预检查已过期，请重新检查"); return; }
    setSubmitting(true); setError("");
    try {
      await onReady({ preflight_id: value.id, request_id: requestId.current, acknowledged_issue_ids: value.snapshot.warning_ids });
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setSubmitting(false); }
  }

  async function check(keys = sessions) {
    setError(""); setReceipt(null);
    const missing = execution.targets.find(t => !keys[t.target]);
    if (missing) { setUploadTarget(missing.target); return; }
    setChecking(true); setResults([]); requestId.current = executionRequestId();
    const abort = new AbortController(); controller.current = abort;
    let completed: ExecutionPreflight | undefined;
    try {
      await checkExecution({ agent_group: agentGroup, context, execution, key_sessions: keys }, item => {
        if (item.type === "error") throw new Error(item.error);
        if (item.type === "target") setResults(old => [...old.filter(r => r.target !== item.result.target), item.result]);
        if (item.type === "completed") { completed = item.result; setReceipt(item.result); setResults(item.result.snapshot.results); }
      }, abort.signal);
      if (!completed) throw new Error("检查未完成，请重新检查");
      if (completed.snapshot.can_submit && !completed.snapshot.requires_acknowledgement) await submit(completed);
    } catch (err) { if (!abort.signal.aborted) setError(err instanceof Error ? err.message : String(err)); }
    finally { setChecking(false); }
  }

  if (uploadTarget && keyQuery.data) return <SshKeyUploadDialog key={uploadTarget} target={uploadTarget} keyInfo={keyQuery.data} onClose={() => setUploadTarget("")} onVerified={id => {
    const next = { ...sessions, [uploadTarget]: id }; setSessions(next); setUploadTarget(""); void check(next);
  }} />;

  return <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="执行环境检查"><div className="dialog-card maxw-720">
    <h3>执行环境检查</h3><div className="dialog-body">
      <p>{checkDataset ? "请逐台完成登录授权，按提示输入机器账号的登录密码；已配置免密登录时会自动跳过密码输入。随后检查连接和共享数据，检查通过后提交；存在数据问题时先展示影响。" : "从执行服务逐台检查 SSH。检查通过后提交。"}</p>
      {execution.mode === "custom" && <ul>{execution.targets.map(t => <li key={t.target}><code>{t.target}</code> {t.purpose} · {sessions[t.target] ? "登录授权完成" : "待登录授权"} {sessions[t.target] && <button type="button" className="btn sm" disabled={checking || submitting} onClick={() => { const next = { ...sessions }; delete next[t.target]; setSessions(next); setReceipt(null); }}>重新授权</button>}</li>)}</ul>}
      {results.length > 0 && <div className="jira-agent-preflight-table"><table><thead><tr><th>机器</th><th>SSH</th>{checkDataset && <th>共享数据</th>}</tr></thead><tbody>{results.map(r => <tr key={r.target}><td><code>{r.target}</code></td><td>{r.ssh.message}</td>{checkDataset && <td>{r.dataset.message}{r.dataset.mount && <small>来源：{r.dataset.mount.source}，挂载于 {r.dataset.mount.target}</small>}</td>}</tr>)}</tbody></table></div>}
      {checking && <p role="status">正在检查执行环境…</p>}
      {receipt && !receipt.snapshot.can_submit && <p role="alert">必要机器的 SSH 检查未通过。请重试，或返回修改机器清单。</p>}
      {receipt?.snapshot.can_submit && receipt.snapshot.requires_acknowledgement && <p className="jira-agent-warning">存在环境异常或未确认项。继续后只执行具备条件的工作。{checkDataset && "依赖缺失数据的测试将暂停并记录缺口。"}连接失败的机器不会使用。</p>}
      {error && <p role="alert">{error}</p>}
      {keyQuery.isError && <p role="alert">{String(keyQuery.error)}</p>}
    </div><div className="dialog-actions">
      <button type="button" className="btn" disabled={submitting} onClick={() => { controller.current?.abort(); onClose(); }}>返回修改</button>
      <button type="button" className="btn" disabled={checking || submitting || (execution.mode === "custom" && !keyQuery.data)} onClick={() => void check()}>{execution.mode === "custom" && execution.targets.some(t => !sessions[t.target]) ? "输入密码并检查" : receipt ? "重新检查" : "开始检查"}</button>
      {receipt?.snapshot.can_submit && <button type="button" className="btn primary" disabled={checking || submitting} onClick={() => void submit(receipt)}>{submitting ? "正在提交…" : receipt.snapshot.requires_acknowledgement ? "继续可执行工作" : "提交任务"}</button>}
    </div>
  </div></div>;
}
