import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../api/AuthContext";
import { learningActive, LEARNING_JOB_LABELS, LEARNING_JOB_TONES, listLearningJobs } from "./jiraLearningApi";

export function LearningTaskList() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("");
  const query = useInfiniteQuery({
    queryKey: ["jira-learning", user?.username, "jobs", ""],
    queryFn: ({ pageParam }) => listLearningJobs("", pageParam), initialPageParam: 0,
    getNextPageParam: (page, pages) => page.has_more ? pages.reduce((count, p) => count + p.jobs.length, 0) : undefined,
    refetchInterval: (q) => q.state.data?.pages.some((page) => page.jobs.some((job) => learningActive(job.status))) ? 5000 : false,
  });
  const jobs = query.data?.pages.flatMap((p) => p.jobs) ?? [];
  return <div className="jira-learning-task-list">
    <div className="row"><input aria-label="过滤学习任务" value={filter} placeholder="任务名称或 Jira 编号" onChange={(e) => setFilter(e.target.value)} /><button className="btn sm" onClick={() => void query.refetch()}>刷新</button></div>
    {query.isError && <p role="alert">{String(query.error)}</p>}
    <div className="jira-agent-issue-results">{jobs.filter((job) => [job.title, ...(job.issue_keys || [job.issue_key])].join(" ").toLowerCase().includes(filter.trim().toLowerCase())).map((job) => <button key={job.id} className={`jira-agent-list-item jira-learning-job-link${params.get("learning_job") === job.id ? " active" : ""}`} onClick={() => setParams((previous) => { const next = new URLSearchParams(previous); next.set("learning_job", job.id); next.delete("issue"); return next; })}>
      <span className="jira-agent-list-top"><strong>{job.title || job.issue_key}</strong><span className={`pill ${LEARNING_JOB_TONES[job.status]}`}>{LEARNING_JOB_LABELS[job.status]}</span></span>
      {(job.issue_keys || [job.issue_key]).length > 1 && <span className="jira-agent-list-summary">{job.issue_keys!.length} 张 Jira · {job.issue_keys!.slice(0, 2).join("、")}{job.issue_keys!.length > 2 ? " 等" : ""}</span>}
      <span className="muted">{job.created_at} · {job.requested_by}</span>
      {!!(job.pending_count || job.update_count) && <span className="muted">{[job.pending_count ? `待审核 ${job.pending_count}` : "", job.update_count ? `待更新 ${job.update_count}` : ""].filter(Boolean).join(" · ")}</span>}
    </button>)}</div>
    {query.hasNextPage && <button className="btn sm" onClick={() => void query.fetchNextPage()}>加载更早任务</button>}
    {!query.isLoading && !jobs.length && <p className="muted">尚无学习任务，从 Jira 列表选择后开始学习。</p>}
  </div>;
}
