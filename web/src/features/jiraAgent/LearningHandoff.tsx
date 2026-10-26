import { useState } from "react";
import { exportLearningBundle, publishLearningBundle, recordLearningDeployment, type LearningProposal } from "./jiraLearningApi";

export function LearningHandoff({ jobId, proposals, onUpdate }: { jobId: string; proposals: LearningProposal[]; onUpdate: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [mode, setMode] = useState("export");
  const [commit, setCommit] = useState("");
  const [target, setTarget] = useState("");
  const [result, setResult] = useState("succeeded");
  const [deployedCommit, setDeployedCommit] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const eligible = proposals.filter((p) => !p.superseded_by && p.status === "approved");
  const commits = [...new Set(proposals.filter((p) => p.status === "published").map((p) => p.published_commit))];
  const deploymentCommit = commits.includes(deployedCommit) ? deployedCommit : commits[0] || "";
  const deployments = [...new Map(proposals.flatMap((p) => p.deployments || []).map((entry) => [entry.id, entry])).values()];
  const ids = selected.filter((id) => eligible.some((p) => p.id === id));
  async function act() {
    setBusy(true); setError(""); setNotice("");
    try {
      if (mode === "export") {
        const data = await exportLearningBundle(jobId, ids);
        const url = URL.createObjectURL(data);
        const link = document.createElement("a"); link.href = url; link.download = "skill-proposals.zip"; link.click(); URL.revokeObjectURL(url);
        setNotice("补丁包已下载，包含基线、修改和验证说明。");
      }
      if (mode === "publish") {
        await publishLearningBundle(jobId, ids, commit, note); setSelected([]); setNotice("已核对提交并登记入库。");
      }
      if (mode === "deployment") {
        await recordLearningDeployment(jobId, target, deploymentCommit, result, note); setNotice("已登记部署，同组相同提交的提案共享此记录。");
      }
      await onUpdate();
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  }
  return <details className="jira-learning-handoff jira-learning-disclosure" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>人工更新与验证 <span className="muted">打包修改、登记提交、验证与部署</span></summary>
    {open && <div className="jira-learning-actions">
      <div className="jira-learning-tabs" role="tablist" aria-label="提案交付"><button role="tab" aria-selected={mode === "export"} onClick={() => { setMode("export"); setNotice(""); setError(""); }}>导出补丁包</button><button role="tab" aria-selected={mode === "publish"} onClick={() => { setMode("publish"); setNotice(""); setError(""); }}>登记提交</button><button role="tab" aria-selected={mode === "deployment"} onClick={() => { setMode("deployment"); setNotice(""); setError(""); }}>登记部署</button></div>
      <p className="muted">{mode === "export" ? "选择审核通过的提案，统一导出一个补丁包。" : mode === "publish" ? "人工提交到正式 skill 仓库后，在此统一登记；内容不同需重新审核。" : "人工完成部署后，按提交和目标服务登记一次，同组相同提交的提案共享记录。"}</p>
      {mode !== "deployment" && eligible.map((proposal) => <label className="jira-learning-checkbox" key={proposal.id}><input type="checkbox" checked={ids.includes(proposal.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, proposal.id] : selected.filter((id) => id !== proposal.id))} />{proposal.title} · 第 {proposal.version || 1} 版</label>)}
      {mode !== "deployment" && !eligible.length && <p className="muted">暂无可选提案。</p>}
      {mode === "publish" && <input aria-label="统一入库提交 SHA" placeholder="已提交的 SHA" value={commit} onChange={(event) => setCommit(event.target.value)} />}
      {mode === "deployment" && <>
        <label>入库版本<select aria-label="部署提交" value={deploymentCommit} onChange={(event) => setDeployedCommit(event.target.value)}>{!commits.length && <option value="">暂无已入库提交</option>}{commits.map((value) => <option key={value} value={value}>{value.slice(0, 12)}</option>)}</select></label>
        <input aria-label="部署目标服务" value={target} onChange={(event) => setTarget(event.target.value)} placeholder="目标服务名称或地址" />
        <select aria-label="部署结果" value={result} onChange={(event) => setResult(event.target.value)}><option value="succeeded">部署成功</option><option value="failed">部署失败</option></select>
      </>}
      {mode !== "export" && <textarea aria-label="交付备注" value={note} onChange={(event) => setNote(event.target.value)} placeholder="验证情况或需要说明的限制（可选）" />}
      {error && <p role="alert" className="jira-agent-warning">{error}</p>}{notice && <p role="status" className="jira-agent-ok">{notice}</p>}
      <div className="actions"><button className="btn primary" disabled={busy || (mode === "deployment" ? !deploymentCommit || !target.trim() : !ids.length) || (mode === "publish" && !commit.trim())} onClick={() => void act()}>{busy ? "处理中…" : mode === "export" ? "下载补丁包" : mode === "publish" ? "核对并登记" : "登记部署结果"}</button></div>
      {mode === "deployment" && deployments.map((deployment) => <p key={deployment.id}><code>{deployment.commit_sha.slice(0, 12)}</code> · {deployment.target} · {deployment.result === "succeeded" ? "部署成功" : "部署失败"} · 人工登记 · {deployment.recorded_by} · {deployment.created_at} {deployment.note}</p>)}
    </div>}
  </details>;
}
