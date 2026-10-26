import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { exportLearningBundle, linkLearningValidation, listLearningValidations, publishLearningBundle, recordLearningDeployment, type LearningProposal } from "./jiraLearningApi";
import { CURRENT_LABELS, listTestSuites, suiteSummary } from "./jiraTestingApi";

export function LearningHandoff({ jobId, proposals, onUpdate }: { jobId: string; proposals: LearningProposal[]; onUpdate: () => Promise<void> }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [mode, setMode] = useState("export");
  const [commit, setCommit] = useState("");
  const [target, setTarget] = useState("");
  const [result, setResult] = useState("succeeded");
  const [deployedCommit, setDeployedCommit] = useState("");
  const [suite, setSuite] = useState("");
  const [purpose, setPurpose] = useState("original");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const eligible = proposals.filter((p) => !p.superseded_by && (p.status === "approved" || (mode === "validation" && p.status === "published")));
  const commits = [...new Set(proposals.filter((p) => p.status === "published").map((p) => p.published_commit))];
  const deploymentCommit = commits.includes(deployedCommit) ? deployedCommit : commits[0] || "";
  const deployments = [...new Map(proposals.flatMap((p) => p.deployments || []).map((entry) => [entry.id, entry])).values()];
  const ids = selected.filter((id) => eligible.some((p) => p.id === id));
  const suites = useQuery({ queryKey: ["jira-learning", user?.username, "validation-suites"], queryFn: listTestSuites, enabled: open && mode === "validation" });
  const records = useQuery({ queryKey: ["jira-learning", user?.username, "validations", jobId], queryFn: () => listLearningValidations(jobId), enabled: open,
    refetchInterval: (query) => query.state.data?.validations.some((row) => row.active) ? 5000 : false });
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
      if (mode === "validation") {
        await linkLearningValidation(jobId, ids, suite, purpose, note); setNotice("已关联测试记录。");
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
      <div className="jira-learning-tabs" role="tablist" aria-label="提案交付"><button role="tab" aria-selected={mode === "export"} onClick={() => { setMode("export"); setNotice(""); setError(""); }}>导出补丁包</button><button role="tab" aria-selected={mode === "publish"} onClick={() => { setMode("publish"); setNotice(""); setError(""); }}>登记提交</button><button role="tab" aria-selected={mode === "validation"} onClick={() => { setMode("validation"); setNotice(""); setError(""); }}>关联验证</button><button role="tab" aria-selected={mode === "deployment"} onClick={() => { setMode("deployment"); setNotice(""); setError(""); }}>登记部署</button></div>
      <p className="muted">{mode === "export" ? "选择审核通过的提案，统一导出一个补丁包。" : mode === "publish" ? "人工提交到正式 skill 仓库后，在此统一登记；内容不同需重新审核。" : mode === "deployment" ? "人工完成部署后，按提交和目标服务登记一次，同组相同提交的提案共享记录。" : "选择独立测试记录，用原案例和其他场景检查修改。测试结果不会送入本轮学习，也不会自动更新分析服务。"}</p>
      {mode !== "deployment" && eligible.map((proposal) => <label className="jira-learning-checkbox" key={proposal.id}><input type="checkbox" checked={ids.includes(proposal.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, proposal.id] : selected.filter((id) => id !== proposal.id))} />{proposal.title} · 第 {proposal.version || 1} 版</label>)}
      {mode !== "deployment" && !eligible.length && <p className="muted">暂无可选提案。</p>}
      {mode === "publish" && <input aria-label="统一入库提交 SHA" placeholder="已提交的 SHA" value={commit} onChange={(event) => setCommit(event.target.value)} />}
      {mode === "deployment" && <>
        <label>入库版本<select aria-label="部署提交" value={deploymentCommit} onChange={(event) => setDeployedCommit(event.target.value)}>{!commits.length && <option value="">暂无已入库提交</option>}{commits.map((value) => <option key={value} value={value}>{value.slice(0, 12)}</option>)}</select></label>
        <input aria-label="部署目标服务" value={target} onChange={(event) => setTarget(event.target.value)} placeholder="目标服务名称或地址" />
        <select aria-label="部署结果" value={result} onChange={(event) => setResult(event.target.value)}><option value="succeeded">部署成功</option><option value="failed">部署失败</option></select>
      </>}
      {mode === "validation" && <>
        <p className="muted">候选 skill 应在独立验证环境使用。系统核对测试保存的 skill 内容是否匹配所选修改；少量案例通过不能代表所有场景。</p>
        <label>测试记录<select aria-label="关联测试记录" value={suite} onChange={(event) => setSuite(event.target.value)}><option value="">请选择</option>{suites.data?.suites.map((item) => <option key={item.id} value={item.id}>{item.created_at} · {item.total} 个用例 · {suiteSummary(item)}</option>)}</select></label>
        {suites.isError && <p role="alert">读取测试记录失败：{String(suites.error)}</p>}
        <select aria-label="验证用途" value={purpose} onChange={(event) => setPurpose(event.target.value)}><option value="original">原案例复测</option><option value="other">其他场景验证</option></select>
      </>}
      {mode !== "export" && <textarea aria-label="交付备注" value={note} onChange={(event) => setNote(event.target.value)} placeholder="验证情况或需要说明的限制（可选）" />}
      {error && <p role="alert" className="jira-agent-warning">{error}</p>}{notice && <p role="status" className="jira-agent-ok">{notice}</p>}
      <div className="actions"><button className="btn primary" disabled={busy || (mode === "deployment" ? !deploymentCommit || !target.trim() : !ids.length) || (mode === "publish" && !commit.trim()) || (mode === "validation" && !suite)} onClick={() => void act()}>{busy ? "处理中…" : mode === "export" ? "下载补丁包" : mode === "publish" ? "核对并登记" : mode === "deployment" ? "登记部署结果" : "关联测试"}</button></div>
      {mode === "deployment" && deployments.map((deployment) => <p key={deployment.id}><code>{deployment.commit_sha.slice(0, 12)}</code> · {deployment.target} · {deployment.result === "succeeded" ? "部署成功" : "部署失败"} · 人工登记 · {deployment.recorded_by} · {deployment.created_at} {deployment.note}</p>)}
      {records.isError && <p role="alert">读取验证记录失败：{String(records.error)}</p>}
      {!!records.data?.validations.length && <div className="jira-learning-validations"><strong>验证记录</strong>{records.data.validations.map((row) => <div key={row.id}>
        <a href={`?view=testing&tab=records&suite=${encodeURIComponent(row.suite_id)}`}>{row.purpose === "original" ? "原案例复测" : "其他场景验证"} · {row.created_at}</a>
        <p>{Object.entries(row.counts).filter(([, count]) => count).map(([status, count]) => `${CURRENT_LABELS[status] || status} ${count}`).join(" · ") || "尚无结果"}</p>
        <p className={row.matches && !row.superseded ? "muted" : "jira-agent-warning"}>{row.superseded ? "提案已有新版，此记录仅对应旧版。" : row.matches ? "测试保存的 skill 内容与所选修改一致。" : "测试 skill 未确认或与所选修改不同，不能据此确认修改有效。"}</p>
        {row.note && <p>{row.note}</p>}
        <details><summary>测试版本</summary>{row.tests.map((test) => <p key={test.case_id}>{test.case_id} · <code>{test.skill_sha256 || "未记录"}</code></p>)}</details>
      </div>)}</div>}
    </div>}
  </details>;
}
