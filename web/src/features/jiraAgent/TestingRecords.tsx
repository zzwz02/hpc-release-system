import type { ReactNode } from "react";
import { currentResult, resultLabel, suiteSummary, SUITE_LABELS, type TestSuite } from "./jiraTestingApi";

const STATUS: Record<string, string> = { queued: "排队中", running: "执行中", completed: "已完成", failed: "执行失败", cancelled: "已取消", interrupted: "已中断" };

export function TestingRecords({ records, caseId, suite, selected, selectSuite, selectRun, action, rerun, busy, children }: {
  records: TestSuite[]; caseId: string; suite?: TestSuite; selected: string;
  selectSuite: (id: string) => void; selectRun: (id: string) => void;
  action: (action: "stop" | "retry") => void; rerun: (ids: string[]) => void; busy: boolean; children: ReactNode;
}) {
  const entries = (caseId ? records : suite ? [suite] : []).flatMap((record) => record.entries.filter((item) => !caseId || item.case_id === caseId).map((item) => ({ ...item, record })));
  if (caseId || suite) return <section id="test-batch-detail" className="test-batch-detail" aria-label={caseId ? "用例测试记录" : "批次详情"}>
    {!caseId && suite && <><button className="test-text-button test-records-back" onClick={() => selectSuite("")}>← 返回测试记录</button>
    <div className="test-suite-summary">
      <div className="test-run-heading"><div><h3>{suite.created_at}</h3>
        <p className="test-suite-meta">{suite.total} 个用例 · {SUITE_LABELS[suite.phase] ?? suite.phase}{suite.reviewed > 0 && ` · 人工评价 ${suite.reviewed} 个`}</p>
      </div><div className="actions">
        {!suite.active && <button className="btn sm" disabled={busy} onClick={() => rerun(suite.entries.map((item) => item.case_id))}>再次运行本批</button>}
        {!suite.id.startsWith("legacy:") && (suite.status === "preparing" || suite.entries.some((item) => item.batch?.runs.some((run) => run.status === "queued"))) && <button className="btn sm" onClick={() => action("stop")}>停止后续测试</button>}
        {suite.status === "failed" && <button className="btn sm" onClick={() => action("retry")}>重试准备</button>}
      </div></div>
      <p className="test-counts">{suiteSummary(suite)}</p>
      {suite.active && <p className="test-progress">分析结束 {suite.finished}/{suite.total} · 初评结束 {suite.evaluated}/{suite.total}</p>}
      {suite.phase === "preparing" && <p role="status">正在准备本批测试。</p>}
      {suite.status === "stopped" && <p className="hint">后续测试已停止，已启动的任务继续执行。</p>}
      {suite.error && <p role="alert" className="jira-agent-warning">{suite.error}</p>}
    </div></>}
    {!entries.length ? <p className="empty">尚无测试记录。勾选左侧用例后开始测试。</p> : <div className="test-table-wrap"><table className="test-runs-table test-case-runs" aria-label="测试记录">
      <thead><tr><th>{caseId ? "测试时间" : "用例"}</th><th>执行</th><th>评价</th><th>详情</th></tr></thead>
      <tbody>{entries.flatMap((item) => item.batch ? item.batch.runs.map((run) => {
        const active = ["queued", "running"].includes(run.status) || run.delivery_status === "collecting";
        const result = currentResult(run);
        const evaluating = run.evaluation?.status === "running";
        return <tr key={run.conversation_id} className={selected === run.conversation_id ? "selected" : ""}>
          <td className={caseId ? "" : "test-run-name"}>{caseId ? item.record.created_at : <strong>{item.title}</strong>}</td>
          <td><span className={`test-run-status ${active ? "test-running" : run.status === "failed" ? "test-failed" : "muted"}`}>{STATUS[run.status] ?? run.status}{run.delivery_status === "collecting" ? " · 回收文件中" : run.delivery_status === "incomplete" ? " · 交付不完整" : ""}</span></td>
          <td className="test-run-verdict"><span className={`test-run-review pill ${{ pass: "ok", fail: "bad", partial: "warn" }[result.decision] ?? ""}`}>{active ? "—" : result.source === "system" && evaluating ? "初评中" : resultLabel(run)}</span></td>
          <td><button className="test-text-button" aria-expanded={selected === run.conversation_id} aria-controls="test-run-detail" onClick={() => selectRun(selected === run.conversation_id ? "" : run.conversation_id)}>{selected === run.conversation_id ? "收起" : "展开"}</button></td>
        </tr>;
      }) : [<tr key={`${item.record.id}:${item.case_id}`}><td className={caseId ? "" : "test-run-name"}>{caseId ? item.record.created_at : item.title}</td><td>{item.record.status === "stopped" ? "已取消" : item.record.status === "failed" ? "准备失败" : "准备中"}</td><td>—</td><td>—</td></tr>])}</tbody>
    </table></div>}
    {children}
  </section>;

  if (!records.length) return <p className="empty">尚无测试记录。勾选左侧用例后开始测试。</p>;
  return <div className="test-table-wrap"><table className="test-runs-table test-suite-table" aria-label="批次记录">
    <thead><tr><th>测试时间</th><th>用例</th><th>状态</th><th>结果汇总</th><th>详情</th></tr></thead>
    <tbody>{records.map((item) => <tr key={item.id}>
      <td>{item.created_at}</td><td className="test-run-name">{item.entries.length === 1 ? item.entries[0].title : `${item.total} 个用例`}</td>
      <td><span className={item.active ? "test-running" : "muted"}>{SUITE_LABELS[item.phase] ?? item.phase}</span></td>
      <td className="test-batch-result">{suiteSummary(item)}</td><td><button className="test-text-button" onClick={() => selectSuite(item.id)}>查看</button></td>
    </tr>)}</tbody>
  </table></div>;
}
