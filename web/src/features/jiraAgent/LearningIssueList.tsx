import { useSearchParams } from "react-router-dom";
import { LearningStart } from "./LearningStart";
import { LearningTaskList } from "./LearningTaskList";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { addLearningIssues, learningActive, LEARNING_JOB_LABELS, LEARNING_JOB_TONES, listLearningIssues, previewLearningIssues, type LearningIssue } from "./jiraLearningApi";

export function LearningIssueList({ selectedKey, selectedKeys, onSelection, onSelect }: {
  selectedKey: string; selectedKeys: string[]; onSelection: (keys: string[]) => void; onSelect: (key: string) => void;
}) {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const tasks = params.get("learning_list") === "jobs";
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState("");
  const [count, setCount] = useState(50);
  const [history, setHistory] = useState(false);
  const query = useQuery({
    queryKey: ["jira-learning", user?.username, "issues"], queryFn: listLearningIssues, enabled: !tasks,
    refetchInterval: (q) => q.state.data?.issues.some((item) => item.learning && learningActive(item.learning.status)) ? 5000 : false,
  });
  const needle = filter.trim().toLowerCase();
  const issues = (query.data?.issues ?? []).filter((item) =>
    (!needle || [item.key, item.summary, item.assignee?.display_name ?? ""].some((text) => text.toLowerCase().includes(needle)))
    && (!status || (status === "pending" ? (item.learning_counts?.pending ?? 0) > 0
      : status === "update" ? (item.learning_counts?.update ?? 0) > 0 : (item.learning?.status ?? "new") === status)));
  const visible = new Set(issues.map((issue) => issue.key));
  const all = issues.length > 0 && issues.every((issue) => selectedKeys.includes(issue.key));
  const hidden = selectedKeys.filter((key) => !visible.has(key)).length;
  return <div className="panel-body jira-agent-search jira-learning-search">
    <div className="jira-learning-tabs" role="tablist" aria-label="学习列表"><button role="tab" aria-selected={!tasks} onClick={() => setParams((previous) => { const next = new URLSearchParams(previous); next.delete("learning_list"); return next; })}>Jira</button><button role="tab" aria-selected={tasks} onClick={() => setParams((previous) => { const next = new URLSearchParams(previous); next.set("learning_list", "jobs"); return next; })}>学习任务</button></div>
    {tasks ? <LearningTaskList /> : <>

    <div className="row">
      <input aria-label="过滤学习工单" placeholder="编号、标题或负责人" value={filter} onChange={(e) => { setFilter(e.target.value); setCount(50); }} />
      <button className="btn sm" onClick={() => void query.refetch()}>刷新</button>
    </div>
    <div className="row">
      <select aria-label="学习状态" value={status} onChange={(e) => { setStatus(e.target.value); setCount(50); }}>
        <option value="">全部状态</option><option value="new">未学习</option><option value="pending">待审核</option><option value="update">待更新</option>
        {Object.entries(LEARNING_JOB_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
      <button className="btn sm" onClick={() => setHistory(true)}>添加历史 Jira</button>
    </div>
    <div className="jira-learning-selection">
      <span className="muted">已选择 {selectedKeys.length} 张{hidden > 0 && `，其中 ${hidden} 张不在当前结果中`}</span>
      <div><button className="btn sm" disabled={!issues.length} onClick={() => onSelection(all ? selectedKeys.filter((key) => !visible.has(key)) : [...new Set([...selectedKeys, ...visible])])}>{all ? "取消选择当前结果" : "全选当前结果"}</button>
        <button className="btn sm" disabled={!selectedKeys.length} onClick={() => onSelection([])}>清空</button></div>
    </div>
    <LearningStart keys={selectedKeys} onCreated={() => onSelection([])} />
    <p className="muted">共 {issues.length} 张 Jira</p>
    {query.isError && <p role="alert" className="jira-agent-warning">{String(query.error)}</p>}
    {!!query.data?.missing.length && <p className="muted">未找到或无权访问：{query.data.missing.join("、")}</p>}
    <div className="jira-agent-issue-results" data-testid="jira-learning-issues">
      {issues.slice(0, count).map((item) => <div key={item.key} className={`jira-agent-list-item jira-agent-selectable${selectedKey === item.key ? " active" : ""}${selectedKeys.includes(item.key) ? " checked" : ""}`}>
        <input type="checkbox" aria-label={`选择 Jira ${item.key}`} checked={selectedKeys.includes(item.key)} onChange={(e) => onSelection(e.target.checked ? [...selectedKeys, item.key] : selectedKeys.filter((key) => key !== item.key))} />
        <button className="jira-learning-issue-button" aria-label={`查看 Jira ${item.key} 的学习状态`} onClick={() => onSelect(item.key)}>
          <span className="jira-agent-list-top"><strong>{item.key}</strong><span className="pill">{item.status}</span></span>
          <span className="jira-agent-list-summary">{item.summary}</span>
          <span className="jira-agent-list-meta"><span className="muted">{item.assignee?.display_name ?? "未分配"}</span>
            <span className={`pill ${item.learning && learningActive(item.learning.status) ? LEARNING_JOB_TONES[item.learning.status] : item.learning_counts?.pending || item.learning_counts?.update ? "warn" : item.learning ? LEARNING_JOB_TONES[item.learning.status] : ""}`}>{item.learning && learningActive(item.learning.status) ? LEARNING_JOB_LABELS[item.learning.status] : item.learning_counts?.pending ? "待审核" : item.learning_counts?.update ? "待更新" : item.learning ? LEARNING_JOB_LABELS[item.learning.status] : "未学习"}</span></span>
          <span className="muted">{item.learning_origin === "historical" ? "RM 添加的历史 Jira" : "agent 处理过"}
            {!!item.learning_counts?.participation && ` · 参与过 ${item.learning_counts.participation} 次`}{!!item.learning_counts?.pending && ` · 待审核 ${item.learning_counts.pending}`}{!!item.learning_counts?.update && ` · 待更新 ${item.learning_counts.update}`}</span>
        </button>
      </div>)}
      {issues.length > count && <button className="btn sm" onClick={() => setCount(count + 50)}>显示更多工单</button>}
      {!query.isLoading && !issues.length && <p className="muted">没有匹配的 Jira</p>}
    </div>
    </>}
    {history && <HistoryDialog onClose={() => setHistory(false)} />}
  </div>;
}

function HistoryDialog({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [issues, setIssues] = useState<LearningIssue[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [searched, setSearched] = useState(false);
  const client = useQueryClient();
  async function search() {
    setBusy(true); setError("");
    try { const result = await previewLearningIssues(query); setIssues(result.issues); setSelected([]); setSearched(true); }
    catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  }
  async function add() {
    setBusy(true); setError("");
    try {
      const result = await addLearningIssues(selected);
      await client.invalidateQueries({ queryKey: ["jira-learning"] });
      if (result.missing.length) { setSelected(result.missing); setError("未加入：" + result.missing.join("、")); }
      else onClose();
    } catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  }
  return <div className="dialog-backdrop" onClick={onClose}><section role="dialog" aria-modal="true" aria-labelledby="learning-history-title" className="panel jira-learning-dialog" onClick={(e) => e.stopPropagation()}>
    <div className="panel-body"><h3 id="learning-history-title">添加历史 Jira</h3><p className="muted">查询后加入学习列表，不启动问题分析或发送 Jira 评论。</p>
      <div className="row"><input autoFocus aria-label="历史 Jira 编号或 JQL" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Jira 编号或 JQL" /><button className="btn" disabled={busy || !query.trim()} onClick={() => void search()}>查询</button></div>
      {error && <p role="alert" className="jira-agent-warning">{error}</p>}
      <div className="jira-learning-history-results">{issues.map((item) => <label key={item.key}><input type="checkbox" aria-label={`加入 ${item.key}`} checked={selected.includes(item.key)} onChange={(e) => setSelected(e.target.checked ? [...selected, item.key] : selected.filter((key) => key !== item.key))} /><strong>{item.key}</strong> {item.summary}</label>)}</div>
      {searched && !issues.length && <p className="muted">没有匹配的 Jira，或当前连接无权访问。</p>}
      <div className="actions"><button className="btn" onClick={onClose}>关闭</button><button className="btn primary" disabled={busy || !selected.length} onClick={() => void add()}>加入列表（{selected.length}）</button></div>
    </div>
  </section></div>;
}
