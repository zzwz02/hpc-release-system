import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../api/AuthContext";
import { closeLearningDuplicate, combineLearningProposals, compareLearningProposals, type LearningProposal } from "./jiraLearningApi";

const labels: Record<string, string> = { pending: "待审核", approved: "已通过审核", rejected: "已关闭", needs_revision: "重新整理中", published: "已入库" };
const requestId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");

export function LearningRelations({ proposal }: { proposal: LearningProposal }) {
  const { user } = useAuth();
  const [, setParams] = useSearchParams();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [other, setOther] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [request, setRequest] = useState(requestId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [limit, setLimit] = useState(8);
  const warnings = proposal.warnings || [];
  const chosen = selected.filter((id) => warnings.some((item) => item.proposal_id === id));
  const comparison = useQuery({ queryKey: ["jira-learning", user?.username, "compare", proposal.id, other],
    queryFn: () => compareLearningProposals(proposal.id, other), enabled: open && Boolean(other), staleTime: 0 });
  async function combine() {
    setBusy(true); setError("");
    try {
      const result = await combineLearningProposals([proposal.id, ...chosen], note, request);
      await client.invalidateQueries({ queryKey: ["jira-learning"] });
      setParams((previous) => { const next = new URLSearchParams(previous); next.set("view", "learning"); next.set("learning_list", "jobs"); next.set("learning_job", result.job.id); next.delete("issue"); next.delete("learning_tab"); return next; });
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  }
  if (!warnings.length) return null;
  return <details className="jira-learning-disclosure learning-relations" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>相关提案 <span className="muted">同一文件还有 {warnings.length} 项提案</span></summary>
    {open && <>
      <p className="muted">同文件不代表冲突。可分别审核；需要一起整理时，勾选相关提案。</p>
      <div className="learning-related-list">{warnings.slice(0, limit).map((item) => <div key={item.proposal_id} className="learning-related-row" data-testid={`learning-related-${item.proposal_id}`}>
        <input type="checkbox" aria-label={`一起整理：${item.title}`} checked={chosen.includes(item.proposal_id)} disabled={busy || (chosen.length >= 7 && !chosen.includes(item.proposal_id))} onChange={(event) => { setSelected(event.target.checked ? [...chosen, item.proposal_id] : chosen.filter((id) => id !== item.proposal_id)); setRequest(requestId()); }} />
        <div><button className="learning-text-action" aria-expanded={other === item.proposal_id} onClick={() => setOther(other === item.proposal_id ? "" : item.proposal_id)}>{item.title}</button><span className="muted">{labels[item.status || "pending"]} · {item.reason}</span></div>
        <button className="btn sm" onClick={() => setOther(other === item.proposal_id ? "" : item.proposal_id)}>{other === item.proposal_id ? "收起比较" : "比较修改"}</button>
      </div>)}</div>
      {warnings.length > limit && <button className="btn sm" onClick={() => setLimit(limit + 8)}>显示更多</button>}
      {other && <section className="learning-comparison" aria-label="提案比较">
        {comparison.isPending && <p className="muted">正在读取修改…</p>}
        {comparison.isError && <p role="alert">读取失败：{String(comparison.error)}</p>}
        {comparison.data && <>
          <div className="learning-comparison-heading"><strong>{comparison.data.relation.reason}</strong><button className="learning-text-action" onClick={() => setOther("")}>关闭比较</button></div>
          <p className="muted">比较共同文件的修改片段；位置重叠仍需结合内容判断。</p>
          <div className="learning-comparison-columns">{[comparison.data.current, comparison.data.other].map((item, index) => <div key={item.id}>
            <strong>{index ? "关联提案" : "当前提案"} · {item.title}</strong>
            <p className="muted">{item.superseded_by ? "已被新版替代" : labels[item.status]} · 基于 {item.base_commit.slice(0, 12) || "未记录"}</p>
            {item.files.map((file) => <div key={file.path}><p className="learning-file-path">{file.path.replace(/^\.agents\/skills\//, "")}</p><pre className="jira-learning-diff">{file.patch.split("\n").map((line, n) => <span key={n} className={line.startsWith("+") ? "added" : line.startsWith("-") ? "removed" : ""}>{line}{"\n"}</span>)}</pre></div>)}
          </div>)}</div>
        </>}
      </section>}
      {!!chosen.length && <div className="learning-combine-form">
        <label>整理意见（可选）<textarea value={note} maxLength={4000} disabled={busy} onChange={(event) => { setNote(event.target.value); setRequest(requestId()); }} placeholder="说明希望保留的内容，或需要解决的矛盾" /></label>
        <div><span className="muted">连同当前提案共 {chosen.length + 1} 项，生成新任务后重新审核，原提案保留。</span><button className="btn primary" disabled={busy} onClick={() => void combine()}>{busy ? "正在创建…" : "一起整理"}</button></div>
      </div>}
      {error && <p role="alert" className="jira-agent-warning">{error}</p>}
    </>}
  </details>;
}

export function LearningOrigins({ sources, proposals, onUpdate }: {
  sources: { id: string; job_id: string; title: string; status: string }[];
  proposals: LearningProposal[]; onUpdate: () => Promise<void>;
}) {
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const approved = proposals.filter((p) => ["approved", "published"].includes(p.status) && !p.superseded_by);
  const replacement = approved.length === 1 ? approved[0].id : approved.find((p) => p.id === selected)?.id || "";
  async function close(id: string) {
    setBusy(true); setError("");
    try { await closeLearningDuplicate(id, replacement); await onUpdate(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  }
  return <details className="jira-learning-disclosure learning-origins"><summary>本次整理的原提案 <span className="muted">{sources.length} 项</span></summary>
    <p className="muted">新提案审核通过后，确认已覆盖原有内容，再关闭重复项。</p>
    {approved.length > 1 && <label>覆盖原内容的新提案<select aria-label="覆盖原内容的新提案" value={selected} disabled={busy} onChange={(event) => setSelected(event.target.value)}><option value="">请选择</option>{approved.map((p) => <option value={p.id} key={p.id}>{p.title}</option>)}</select></label>}
    {sources.map((p) => <div key={p.id} className="learning-related-row"><div><a href={`?view=learning&learning_list=jobs&learning_job=${p.job_id}#${p.id}`} target="_blank" rel="noreferrer">{p.title}</a><span className="muted">{labels[p.status]}</span></div>{["pending", "approved"].includes(p.status) && <button className="btn sm" disabled={!replacement || busy} onClick={() => void close(p.id)}>关闭重复项</button>}</div>)}
    {error && <p role="alert" className="jira-agent-warning">{error}</p>}
  </details>;
}
