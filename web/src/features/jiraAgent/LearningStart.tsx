import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { createLearningJobs } from "./jiraLearningApi";

const requestId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");

export function LearningStart({ keys, onCreated }: { keys: string[]; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [repository, setRepository] = useState(false);
  const [request, setRequest] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [, setParams] = useSearchParams();
  const client = useQueryClient();
  async function start() {
    setBusy(true); setError("");
    try {
      const result = await createLearningJobs(keys, instructions, repository, title, request);
      const job = result.job;
      setParams((previous) => { const next = new URLSearchParams(previous); next.set("view", "learning"); next.set("learning_list", "jobs"); next.set("learning_job", job.id); next.delete("issue"); return next; });
      onCreated(); setOpen(false);
      void client.invalidateQueries({ queryKey: ["jira-learning"] });
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  }
  return <>
    <button className="btn primary" disabled={!keys.length} onClick={() => { setRequest(requestId()); setError(""); setOpen(true); }}>联合学习{keys.length ? `（${keys.length} 张）` : ""}</button>
    {open && <div className="dialog-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="learning-start-title" className="panel jira-learning-dialog"><div className="panel-body">
      <h3 id="learning-start-title">开始联合学习</h3><p className="muted">综合所选 Jira 的验证结果与差异，生成一组待审核的 skill 修改。</p>
      <div className="jira-learning-members">{keys.map((key) => <span className="pill" key={key}>{key}</span>)}</div>
      <div className="jira-learning-actions">
        <label>任务名称（可选）<input aria-label="学习任务名称" maxLength={160} value={title} disabled={busy} onChange={(e) => { setTitle(e.target.value); setRequest(requestId()); }} placeholder="例如：运行时问题定位方法" /></label>
        <label>关注内容（可选）<textarea aria-label="本次整理要求" maxLength={20000} value={instructions} disabled={busy} onChange={(e) => { setInstructions(e.target.value); setRequest(requestId()); }} placeholder="例如：哪些判断在不同场景下有效，哪些需要限定条件" /></label>
        <details><summary>材料设置</summary><label className="jira-learning-checkbox"><input type="checkbox" checked={repository} disabled={busy} onChange={(e) => { setRepository(e.target.checked); setRequest(requestId()); }} />仅参考仓库版本，不读取分析服务当前 skill</label></details>
      </div>
      {error && <p role="alert" className="jira-agent-warning">{error}</p>}
      <div className="actions"><button className="btn" disabled={busy} onClick={() => setOpen(false)}>取消</button><button className="btn primary" disabled={busy} onClick={() => void start()}>{busy ? "提交中…" : "开始学习"}</button></div>
    </div></section></div>}
  </>;
}
