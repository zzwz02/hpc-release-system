import { useEffect, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useSearchParams } from "react-router-dom";
import { useAuth } from "../../api/AuthContext";
import { Markdown } from "../../components/Markdown";
import {
  getLearningJob, listLearningMaterials, LEARNING_JOB_LABELS, LEARNING_JOB_TONES,
  listLearningJobs, reviewLearningProposal, sendLearningMessage,
  learningActive, operateLearningJob, reviseLearningPublication,
  type LearningEvent, type LearningProposal, type LearningTurn, type LearningJob,
} from "./jiraLearningApi";
import { LearningHandoff } from "./LearningHandoff";
import { MaterialList } from "./LearningMaterials";
import { LearningRelations, LearningOrigins } from "./LearningRelations";

const KIND_LABELS = { skill_update: "修改现有 skill", new_skill: "新 skill 候选" };
const STATUS_LABELS = { pending: "待审核", approved: "审核通过，待更新", rejected: "已拒绝", needs_revision: "重新整理中", published: "已入库" };
const PHASE_LABELS: Record<string, string> = { collecting: "采集材料", connecting: "连接学习服务", starting: "启动学习轮次", running: "整理提案", reading: "正在补充相关材料", validating: "校验提案", recovering: "核对远端状态" };
const REVIEW_LABELS: Record<string, string> = { approved: "审核通过", rejected: "拒绝", needs_revision: "退回整理", published: "登记入库" };
function errorText(error: unknown) { return error instanceof Error ? error.message : String(error); }

export function LearningPanel({ focusedIssueKey }: { focusedIssueKey: string }) {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [repositoryOnly, setRepositoryOnly] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<Record<string, string>>({});
  const client = useQueryClient();
  const jobs = useInfiniteQuery({
    queryKey: ["jira-learning", user?.username, "jobs", focusedIssueKey],
    queryFn: ({ pageParam }) => listLearningJobs(focusedIssueKey, pageParam), initialPageParam: 0,
    getNextPageParam: (page, pages) => page.has_more ? pages.reduce((sum, p) => sum + p.jobs.length, 0) : undefined,
    enabled: Boolean(focusedIssueKey),
    refetchInterval: (q) => q.state.data?.pages.some((p) => p.jobs.some((job) => learningActive(job.status))) ? 5000 : false,
  });
  const history = jobs.data?.pages.flatMap((page) => page.jobs) ?? [];
  const id = params.get("learning_job") || "";
  const detail = useQuery({
    queryKey: ["jira-learning", user?.username, "job", id], queryFn: async () => {
      const previous = client.getQueryData<Awaited<ReturnType<typeof getLearningJob>>>(["jira-learning", user?.username, "job", id]);
      const result = await getLearningJob(id, previous?.events.at(-1)?.seq || 0);
      return { ...result, events: [...new Map([...(previous?.events || []), ...result.events].map((event) => [event.id, event])).values()].sort((a, b) => a.seq - b.seq) };
    }, enabled: Boolean(id),
    refetchInterval: (q) => q.state.data && (learningActive(q.state.data.job.status) || q.state.data.progress?.pending) ? 2000 : false,
  });
  const current = detail.data?.proposals.filter((p) => !p.superseded_by && p.kind !== "case") ?? [];
  const proposals = current.filter((p) => p.kind === "skill_update" || p.kind === "new_skill");
  const notes = current.filter((p) => p.kind === "do_not_publish");
  const pending = proposals.filter((p) => p.status === "pending").length;
  const approved = proposals.filter((p) => p.status === "approved").length;
  const published = proposals.filter((p) => p.status === "published").length;
  useEffect(() => {
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: "nearest" });
  }, [id, detail.data?.job.id, location.hash]);
  function openProposal(value: string) {
    setExpanded((previous) => ({ ...previous, [id]: (previous[id] ?? location.hash.slice(1)) === value ? "" : value }));
  }
  function selectJob(job: LearningJob) {
    setParams((previous) => { const next = new URLSearchParams(previous); next.set("view", "learning"); next.delete("issue"); next.set("learning_list", "jobs"); next.set("learning_job", job.id); next.delete("learning_tab"); return next; });
  }
  async function update() { await client.invalidateQueries({ queryKey: ["jira-learning"] }); }
  async function operate(action: "cancel" | "retry" | "refresh") {
    setBusy(true); setError("");
    try { const result = await operateLearningJob(id, action, repositoryOnly); if (action === "refresh") selectJob(result.job); await update(); }
    catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  }
  const job = detail.data?.job;
  const unchanged = job?.status === "completed" && proposals.length === 0 && notes.length > 0;
  return <div className="jira-learning-layout" data-testid="jira-learning-panel">
    {error && <p role="alert" className="jira-agent-warning">{error}</p>}
    <section className="panel"><div className="panel-body">
      <button className="btn sm jira-learning-back" onClick={() => document.querySelector(".jira-agent-sidebar")?.scrollIntoView()}>返回工单列表</button>
      {!id && <>
        <h3>{focusedIssueKey ? `${focusedIssueKey} 参与的学习` : "联合学习"}</h3>
        <p className="muted">从左侧勾选 Jira，综合不同案例整理 skill 改进；已有任务可在“学习任务”中继续查看。</p>
        {focusedIssueKey && history.map((item) => <button key={item.id} className="jira-learning-job-link" onClick={() => selectJob(item)}><strong>{item.title || item.issue_key}</strong><span>{(item.issue_keys || [item.issue_key]).join("、")}</span><span className="muted">{item.created_at} · {LEARNING_JOB_LABELS[item.status]}</span></button>)}
        {focusedIssueKey && !jobs.isLoading && !history.length && <p className="muted">尚未参与学习。</p>}
        {jobs.hasNextPage && <button className="btn sm" onClick={() => void jobs.fetchNextPage()}>加载更早任务</button>}
      </>}
      {id && detail.isLoading && <p className="jira-learning-empty muted">正在读取学习记录…</p>}
      {(detail.isError || jobs.isError) && <p role="alert" className="jira-agent-warning">{errorText(detail.error || jobs.error)}</p>}
      {job && detail.data && <>
        <header className="learning-task-header">
          <div className="learning-task-identity"><h3>{job.title || job.issue_key}</h3><span className={`pill ${LEARNING_JOB_TONES[job.status]}`} role="status">{learningActive(job.status) && !["cancelling", "recovering", "queued"].includes(job.status) ? PHASE_LABELS[job.phase || ""] || LEARNING_JOB_LABELS[job.status] : LEARNING_JOB_LABELS[job.status]}</span></div>
          <div className="learning-task-controls">
            {learningActive(job.status) && <button className="learning-quiet-action" disabled={busy || !["queued", "collecting", "running"].includes(job.status)} onClick={() => void operate("cancel")}>取消学习</button>}
            {!job.readonly && ["failed", "cancelled"].includes(job.status) && <button className="btn sm" disabled={busy} onClick={() => void operate("retry")}>重试整理</button>}
            {!job.readonly && !learningActive(job.status) && <details className="learning-task-menu"><summary>更多</summary><div><button disabled={busy} onClick={() => void operate("refresh")}>{detail.data.combination?.length ? "按最新 skill 重新整理" : "重新采集并学习"}</button></div></details>}
          </div>
        </header>
        <div className="learning-task-meta"><span>{job.requested_by} · {job.created_at}</span><span>{(job.issue_keys || [job.issue_key]).length} 张 Jira</span></div>
        {detail.data.progress && detail.data.progress.pending > 0 && <p className="muted" role="status">材料准备：{detail.data.progress.ready} / {detail.data.progress.total} 张已就绪，可边学习边读取。</p>}
        {detail.data.collection_gaps?.filter((gap) => gap.category === "blocking").map((gap, index) => <p key={index} className="jira-agent-warning">{gap.issue_key ? `${gap.issue_key}：` : ""}{gap.reason}</p>)}
        {detail.data.knowledge?.repository?.commit && <details className="jira-learning-disclosure"><summary>学习使用的 skill 版本</summary><p>仓库基线：<code>{detail.data.knowledge.repository.commit.slice(0,12)}</code></p><p>分析服务参考：{detail.data.knowledge.repository_only ? "本次仅参考仓库" : detail.data.knowledge.analysis?.deployed_commit?.slice(0,12) || "已保存当时内容，未记录提交号"}</p></details>}
        {job.readonly && <p className="muted">材料已重新采集，继续整理请打开新任务。<button className="btn sm" onClick={() => setParams((previous) => { const next = new URLSearchParams(previous); next.set("learning_job", job.successor_id || ""); return next; })}>查看新任务</button></p>}
        {job.status === "failed" && <label className="jira-learning-checkbox"><input type="checkbox" checked={repositoryOnly} onChange={(e) => setRepositoryOnly(e.target.checked)} />重试时仅参考仓库版本</label>}
        {job.error && <p className="jira-agent-warning">{job.phase ? `阶段：${PHASE_LABELS[job.phase] || job.phase}。` : ""}{job.error}</p>}
        <div className="jira-learning-results">
          <div className="jira-learning-heading"><h3>学习结果</h3>{proposals.length > 0 && <span className="learning-review-summary" aria-label="提案进度">{[pending ? `${pending} 项待审核` : "", approved ? `${approved} 项待更新` : "", published ? `${published} 项已入库` : ""].filter(Boolean).join(" · ")}</span>}</div>
          {learningActive(job.status) && proposals.length > 0 && <p className="muted">本轮正在整理，以下是已有提案，新结果会自动更新。</p>}
          {proposals.length > 0 && notes.length > 0 && <details className="jira-learning-disclosure"><summary>未形成修改的内容</summary>{notes.map((item) => <div key={item.id}><p>{item.summary || item.title}</p><MaterialGaps gaps={item.material_gaps} /></div>)}</details>}
          {unchanged && <NoUpdate key={id} jobId={id} proposals={notes} />}
          {!proposals.length && !unchanged && <p className="jira-learning-empty muted">{learningActive(job.status) ? "正在整理，提案会自动显示在这里。可展开下方对话查看进度。" : "暂无可用的 skill 改进，可查看对话或继续学习。"}</p>}
          {proposals.map((proposal) => <ProposalCard key={proposal.id} proposal={proposal} expanded={(expanded[id] ?? location.hash.slice(1)) === proposal.id} onToggle={() => openProposal(proposal.id)} canRevise={!job.readonly && job.can_message} onUpdate={update} />)}
        </div>
        {!!detail.data.combination?.length && <LearningOrigins sources={detail.data.combination} proposals={proposals} onUpdate={update} />}
        {proposals.some((p) => ["approved", "published"].includes(p.status)) && <LearningHandoff key={`handoff-${id}`} jobId={id} proposals={proposals} onUpdate={update} />}
        <LearningConversation key={`conversation-${id}`} jobId={id} canMessage={job.can_message && !job.readonly} active={learningActive(job.status)} initialOpen={params.get("learning_tab") === "dialogue" || learningActive(job.status) || job.status === "failed"} readonly={Boolean(job.readonly)} turns={detail.data.turns} events={detail.data.events} text={drafts[id] ?? ""} onText={(text) => setDrafts((previous) => ({ ...previous, [id]: text }))} onUpdate={update} />
      </>}
    </div></section>
  </div>;
}

const LEARNING_MESSAGE_MODES: Record<string, string> = {
  initial: "发起学习",
  queued: "已排队，当前轮结束后继续",
  merged: "已合并到排队轮次",
};

function LearningConversation({
  jobId,
  canMessage,
  active,
  initialOpen,
  readonly,
  turns,
  events,
  onUpdate,
  text,
  onText,
}: {
  jobId: string;
  canMessage: boolean;
  active: boolean;
  initialOpen: boolean;
  readonly: boolean;
  turns: LearningTurn[];
  events: LearningEvent[];
  onUpdate: () => Promise<void>;
  text: string;
  onText: (value: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [count, setCount] = useState(100);
  const [open, setOpen] = useState(initialOpen);
  const shown = events.slice(-count);
  const turnsById = new Map(turns.map((turn) => [turn.id, turn]));

  async function send() {
    const message = text.trim();
    if (!message) return;
    setBusy(true);
    setError("");
    try {
      await sendLearningMessage(jobId, message);
      onText("");
      await onUpdate();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="jira-learning-conversation">
      <details className="jira-learning-process" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary>学习对话 <span className="muted">{turns.length} 轮{active ? " · 正在处理" : ""}</span><span className="jira-learning-fold">{open ? "收起" : "展开"}</span></summary>
      <div className="jira-agent-timeline" data-testid="jira-learning-timeline">
        {events.length > count && <button className="btn sm" onClick={() => setCount(count + 100)}>加载更早对话</button>}
        {shown.map((event, index) => {
          const turn = turnsById.get(event.turn_id);
          const previous = shown[index - 1];
          const separator = turn && previous?.turn_id !== event.turn_id;
          const content = (() => {
            const value = typeof event.payload.text === "string" ? event.payload.text : "";
            if (event.kind === "user_message") {
              return (
                <div className="jira-agent-event user">
                  <div className="jira-agent-event-head">
                    <strong>{String(event.payload.author ?? "RM")}</strong>
                    <span className="muted">
                      {LEARNING_MESSAGE_MODES[String(event.payload.mode ?? "")] ?? "补充要求"} · {event.created_at}
                    </span>
                  </div>
                  <div className="jira-agent-pre">{value}</div>
                </div>
              );
            }
            if (event.kind === "agent_message") {
              return (
                <div className="jira-agent-event agent">
                  <div className="jira-agent-event-head">
                    <strong>学习 agent</strong>
                    <span className="muted">{event.created_at}</span>
                  </div>
                  <Markdown value={value} className="md-view jira-agent-md" />
                </div>
              );
            }
            if (event.kind === "reasoning") {
              return (
                <details className="jira-agent-event reasoning">
                  <summary className="muted">关键判断与证据</summary>
                  <div className="jira-agent-pre">{value}</div>
                </details>
              );
            }
            if (event.kind === "tool" && event.payload.type === "learning_material") {
              const materials = event.payload.materials as { id: string; name: string; kind: string; independent: boolean; error?: string }[];
              return <div className="jira-agent-event tool"><div className="jira-agent-event-head"><span>读取材料</span><span className="muted">{event.created_at}</span></div><MaterialList jobId={jobId} sources={materials.filter((item) => !item.error)} />{materials.filter((item) => item.error).map((item) => <p className="muted" key={item.id}>{item.name}：{item.error}</p>)}</div>;
            }
            if (event.kind === "tool") {
              return (
                <details className="jira-agent-event tool">
                  <summary>内部处理：{String(event.payload.type ?? "")}</summary>
                  <pre className="jira-agent-output">{String(event.payload.detail ?? "")}</pre>
                </details>
              );
            }
            return (
              <div className={`jira-agent-event ${event.kind === "error" ? "error" : "status"}`}>
                {value || (event.kind === "error" ? "学习轮次失败" : "状态更新")}
                <span className="muted"> · {event.created_at}</span>
              </div>
            );
          })();
          return (
            <div key={event.id}>
              {separator && (
                <div className="jira-agent-turn-separator">
                  <span>
                    第 {turn.seq} 轮 · {turn.trigger === "initial" ? "首次学习" : "补充要求"}
                    {" · "}{turn.created_by} · {turn.created_at}
                    {turn.status === "queued" && " · 排队中"}
                    {turn.status === "running" && " · 运行中"}
                    {turn.status === "failed" && " · 失败"}
                  </span>
                </div>
              )}
              {content}
            </div>
          );
        })}
        {events.length === 0 && <p className="muted">尚无对话记录</p>}
      </div>
      </details>
      {canMessage ? <div className="jira-learning-composer">
        <label htmlFor={`learning-message-${jobId}`}>继续对话</label>
        <textarea
          id={`learning-message-${jobId}`}
          aria-label="补充学习要求"
          rows={3}
          placeholder={active ? "补充要求将在当前轮结束后继续处理" : "补充要求，让学习 agent 在同一对话中重新整理提案"}
          value={text}
          disabled={!canMessage}
          onChange={(event) => onText(event.target.value)}
        />
        {error && <p className="jira-agent-warning">{error}</p>}

        <div className="actions">
          <button
            type="button"
            className="btn primary"
            disabled={busy || !canMessage || !text.trim()}
            onClick={() => void send()}
          >
            {busy ? "发送中…" : "发送"}
          </button>
        </div>
      </div> : <p className="muted">{readonly ? "历史对话只读，请在最新学习记录中继续。" : "当前暂不能继续对话，请等待任务就绪或重试整理。"}</p>}
    </div>
  );
}


function SourceEvidence({ jobId, refs }: { jobId: string; refs: string[] }) {
  const { user } = useAuth();
  const source = useQuery({ queryKey: ["jira-learning", user?.username, "evidence", jobId, refs], queryFn: () => listLearningMaterials(jobId, refs) });
  if (source.isError) return <p className="jira-agent-warning">读取来源失败：{errorText(source.error)}</p>;
  if (!source.data) return <p className="muted">读取来源…</p>;
  return <div>
    {refs.some((ref) => !source.data.items.some((item) => item.id === ref)) && <p className="jira-agent-warning">部分引用未找到对应材料，请核对后审核。</p>}
    {refs.length === 0 && <p className="muted">未关联来源证据。</p>}
    <MaterialList jobId={jobId} sources={source.data.items.filter((item) => refs.includes(item.id))} showIssue />
  </div>;
}

function MaterialGaps({ gaps }: { gaps?: LearningProposal["material_gaps"] }) {
  if (!gaps?.length) return null;
  return <div className="jira-agent-warning"><strong>影响判断的材料缺失</strong>{gaps.map((gap) => <p key={gap.source_id}><strong>{gap.name}</strong>：{gap.reason}。{gap.impact}</p>)}</div>;
}

function NoUpdate({ jobId, proposals }: { jobId: string; proposals: LearningProposal[] }) {
  const [evidence, setEvidence] = useState(false);
  const reasons = [...new Set(proposals.map((proposal) => proposal.summary.trim() || proposal.title))];
  const refs = [...new Set(proposals.flatMap((proposal) => proposal.evidence_ids))];
  return <div className="jira-learning-no-update" data-testid="jira-learning-no-update">
    <strong>{proposals.some((p) => p.material_gaps?.length) ? "暂不能形成修改" : "无需更新"}</strong>
    <MaterialGaps gaps={proposals.flatMap((p) => p.material_gaps || [])} />
    <p>{reasons.join(" ")}</p>
    {refs.length > 0 && <details className="jira-learning-disclosure" onToggle={(event) => setEvidence(event.currentTarget.open)}>
      <summary>来源证据 <span className="muted">{refs.length} 条</span></summary>
      {evidence && <SourceEvidence jobId={jobId} refs={refs} />}
    </details>}
  </div>;
}

function ProposalCard({ proposal, expanded, onToggle, canRevise, onUpdate }: {
  proposal: LearningProposal; expanded: boolean; onToggle: () => void;
  canRevise: boolean; onUpdate: () => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [commit, setCommit] = useState("");
  const [evidence, setEvidence] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await action(); await onUpdate(); }
    catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  }
  function download() {
    const header = `# jira_agent_skill\n# Base: ${proposal.base_commit || "unknown"} (${proposal.repository_ref || ""})\n`;
    const url = URL.createObjectURL(new Blob([header, proposal.patch_text], { type: "text/x-diff;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `${proposal.id}.patch`; link.click(); URL.revokeObjectURL(url);
  }
  if (proposal.kind === "case" || proposal.kind === "do_not_publish" || proposal.superseded_by) return null;
  return <article className="jira-learning-proposal" id={proposal.id} data-testid="jira-learning-proposal">
    <button className="jira-learning-proposal-toggle" aria-expanded={expanded} aria-controls={`${proposal.id}-body`} onClick={onToggle}>
      <span className="jira-learning-proposal-top"><span className="jira-learning-kind">{KIND_LABELS[proposal.kind]}</span><span className={`pill ${proposal.status === "pending" || proposal.status === "approved" ? "warn" : proposal.status === "published" ? "ok" : ""}`}>{STATUS_LABELS[proposal.status]}</span></span>
      <strong className="jira-learning-proposal-title">{proposal.title}</strong>
      <span className="jira-learning-proposal-summary">{proposal.summary}</span>
      <span className="jira-learning-proposal-bottom"><span>第 {proposal.version ?? 1} 版{proposal.files?.length ? ` · ${proposal.files.length} 个文件` : ""} · {proposal.evidence_ids.length} 条来源{proposal.case_keys?.length ? ` · ${proposal.case_keys.length} 张 Jira` : ""}</span><span className="jira-learning-link">{expanded ? "收起详情 ↑" : proposal.status === "pending" ? "查看并审核 →" : "查看详情 →"}</span></span>
    </button>
    <div id={`${proposal.id}-body`} hidden={!expanded} className="jira-learning-proposal-body">
    <MaterialGaps gaps={proposal.material_gaps} />
    {proposal.applicability && <div className="jira-learning-field"><strong>适用范围</strong><p>{proposal.applicability}</p></div>}
    {proposal.proposed_text && proposal.proposed_text !== proposal.summary && <div className="jira-learning-field"><strong>整理内容</strong><p>{proposal.proposed_text}</p></div>}
    {proposal.patch_text && <details className="jira-learning-disclosure"><summary>修改对比 <span className="muted">展开核对 patch</span></summary>
      <div className="jira-learning-diff-meta"><span className="muted">基于 <code title={proposal.base_commit}>{proposal.base_commit?.slice(0, 12) || "未记录版本"}</code> · {proposal.repository_ref}</span><button className="btn sm" onClick={download}>下载 patch</button></div>
      <pre className="jira-learning-diff">{proposal.patch_text.split("\n").map((line, index) => <span key={index} className={line.startsWith("+") ? "added" : line.startsWith("-") ? "removed" : line.startsWith("@@") ? "hunk" : ""}>{line}{"\n"}</span>)}</pre>
      {proposal.files?.map((file) => <details key={file.path}><summary>修改后全文：<code>{file.path}</code></summary><pre className="jira-learning-text">{file.updated_text}</pre></details>)}
    </details>}
    <details className="jira-learning-disclosure" onToggle={(event) => { if (event.currentTarget.open) setEvidence(true); }}><summary>来源证据 <span className="muted">{proposal.evidence_ids.length} 条</span></summary>{evidence && expanded && <SourceEvidence jobId={proposal.job_id} refs={proposal.evidence_ids} />}</details>
    {proposal.limitations && <div className="jira-learning-field"><strong>限制与缺口</strong><p>{proposal.limitations}</p></div>}
    {proposal.validation && <div className="jira-learning-field"><strong>建议验证</strong><p>{proposal.validation}</p></div>}
    <LearningRelations proposal={proposal} />
    {proposal.review_blocker && <p role="alert" className="jira-agent-warning">暂不能审核通过：{proposal.review_blocker}</p>}
    {proposal.status === "pending" && <div className="jira-learning-actions">
      <textarea aria-label="审核意见" value={note} onChange={(e) => setNote(e.target.value)} placeholder="填写审核意见；退回整理时请说明需要调整的内容" />
      <div><button className="btn primary" disabled={busy || Boolean(proposal.review_blocker)} onClick={() => void act(() => reviewLearningProposal(proposal.id, "approved", note))}>审核通过</button><button className="btn" disabled={busy || !canRevise || !note.trim()} title={!canRevise ? "请在最新可对话的学习记录中重新整理" : "填写意见后重新整理此提案"} onClick={() => void act(() => reviewLearningProposal(proposal.id, "needs_revision", note))}>退回并重新整理</button><button className="btn" disabled={busy} onClick={() => void act(() => reviewLearningProposal(proposal.id, "rejected", note))}>拒绝</button></div>
    </div>}
    {proposal.status === "approved" && <div className="jira-learning-actions">
      <p className="muted">在“人工更新与验证”中登记提交。实际修改与提案不同时，在此重新审核。</p>
      <input aria-label="实际修改提交 SHA" value={commit} onChange={(e) => setCommit(e.target.value)} placeholder="需要重新审核的提交 SHA" />
      <textarea aria-label="实际修改说明" value={note} onChange={(e) => setNote(e.target.value)} placeholder="说明实际修改与原提案的差异" />
      <button className="btn" disabled={busy || !commit.trim() || !note.trim()} onClick={() => void act(() => reviseLearningPublication(proposal.id, commit, note))}>按实际提交重新审核</button>
    </div>}
    {proposal.status === "needs_revision" && <p className="muted">已退回，重新整理的进度见下方学习对话。</p>}
    {proposal.status === "published" && <p>已入库：<code>{proposal.published_commit}</code></p>}
    {!!proposal.reviews.length && <details className="jira-learning-disclosure"><summary>审核记录 <span className="muted">{proposal.reviews.length} 条</span></summary>{proposal.reviews.map((review) => <div className="jira-learning-review" key={review.id}><strong>{REVIEW_LABELS[review.decision] || review.decision}</strong><span className="muted">{review.reviewer} · {review.created_at}</span>{review.note && <p>{review.note}</p>}</div>)}</details>}
    {error && <p role="alert" className="jira-agent-warning">{error}</p>}
    </div>
  </article>;
}
