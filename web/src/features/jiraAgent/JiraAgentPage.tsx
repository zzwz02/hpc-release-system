/**
 * JIRA agent — find JIRA issues, hand them to the group's digital employee
 * and follow its work.  The agent itself runs on the group's Codex
 * app-server; this page manages conversations, messages, files and the
 * execution timeline.
 *
 * URL: ?issue=KEY[&conversation=ID|new].  "new" is an unsent hand-over
 * draft; a bare ?conversation=ID (links in JIRA comments) resolves its issue.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../api/AuthContext";
import { Markdown } from "../../components/Markdown";
import { confirmDialog } from "../../lib/confirm";
import { isRM } from "../../lib/roles";
import { toast } from "../../lib/toast";
import { MachineChoice } from "./MachineChoice";
import { MachinesDialog } from "./MachinesDialog";
import {
  CLOSE_REASON_LABELS,
  CONCLUSION_LABELS,
  JIRA_AGENT_HANDLED_ISSUES_KEY,
  JIRA_AGENT_ISSUE_SEARCH_KEY,
  OWNERSHIP_LABELS,
  STATE_LABELS,
  cancelConversationTurn,
  fileDownloadUrl,
  fileToUpload,
  getConversation,
  getConversationEvents,
  handoverIssue,
  jiraAgentConversationKey,
  jiraAgentIssueKey,
  jiraAgentIssueSearchKey,
  listHandledIssues,
  previewIssue,
  retryTurnComment,
  searchIssues,
  sendConversationMessage,
  type AgentConversation,
  type AgentEvent,
  type AgentFile,
  type AgentResult,
  type AgentTurn,
  type ConversationState,
  type IssuePreview,
  type IssueSearchItem,
  type IssueSearchResponse,
} from "./jiraAgentApi";

const DRAFT = "new";

const SEARCH_MODE_LABELS: Record<IssueSearchResponse["mode"], string> = {
  mine: "默认列表（未关闭）",
  key: "按 JIRA 编号",
  jql: "JQL",
  handled: "agent 处理过（含已关闭）",
};

const STATE_TONE: Record<ConversationState, string> = {
  idle: "",
  queued: "warn",
  running: "accent",
  waiting_review: "ok",
  failed: "bad",
  cancelled: "",
  interrupted: "warn",
  closed: "",
};

const SOURCE_LABELS: Record<AgentFile["source"], string> = {
  jira: "JIRA 附件",
  upload: "上传",
  artifact: "产物",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isActive(conversation: AgentConversation | undefined): boolean {
  if (!conversation) return false;
  return (
    conversation.state === "queued"
    || conversation.state === "running"
    || conversation.latest_turn?.comment_status === "pending"
  );
}

const RECOVERING_TEXT = "网站刚重启，正在重新接管本轮，几秒后可以操作";

function stateText(conversation: AgentConversation): string {
  const turn = conversation.latest_turn;
  if (conversation.state === "queued" && turn?.queue_position != null) {
    return turn.queue_position > 0 ? `排队中，前面还有 ${turn.queue_position} 轮` : "排队中，即将开始";
  }
  if (conversation.state === "waiting_review" && turn?.conclusion) {
    return `待审阅 · ${CONCLUSION_LABELS[turn.conclusion] ?? turn.conclusion}`;
  }
  return STATE_LABELS[conversation.state];
}

function mergeEvents(previous: AgentEvent[], incoming: AgentEvent[]): AgentEvent[] {
  const byId = new Map(previous.map((event) => [event.id, event]));
  for (const event of incoming) byId.set(event.id, event);
  return [...byId.values()].sort((a, b) => a.seq - b.seq);
}

async function readFiles(files: FileList | null) {
  return Promise.all(Array.from(files ?? []).map(fileToUpload));
}

/** Tool-use steps are folded into one collapsed group per consecutive run. */
const TOOL_KINDS = new Set(["command", "file_change", "tool", "reasoning"]);

interface TimelineItem {
  key: string;
  turnId: string;
  tools: boolean;
  events: AgentEvent[];
}

function groupTimeline(events: AgentEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  for (const event of events) {
    const tools = TOOL_KINDS.has(event.kind);
    const last = items[items.length - 1];
    if (tools && last?.tools && last.turnId === event.turn_id) {
      last.events.push(event);
    } else {
      // Keyed by the first event so an expanded group stays open while it grows.
      items.push({ key: event.id, turnId: event.turn_id, tools, events: [event] });
    }
  }
  return items;
}

// ─────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────

export function JiraAgentPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const issueKey = searchParams.get("issue") ?? "";
  const conversationParam = searchParams.get("conversation") ?? "";
  const [showMachines, setShowMachines] = useState(false);

  const navigate = useCallback(
    (issue: string, conversation = "", replace = false) => {
      const next: Record<string, string> = {};
      if (issue) next.issue = issue;
      if (conversation) next.conversation = conversation;
      setSearchParams(next, { replace });
    },
    [setSearchParams],
  );

  let main = <div className="panel empty">从左侧选择 JIRA 工单。</div>;
  if (issueKey) {
    main = (
      <IssueView key={issueKey} issueKey={issueKey} conversationParam={conversationParam} onNavigate={navigate} />
    );
  } else if (conversationParam) {
    main = <ConversationRedirect id={conversationParam} onNavigate={navigate} />;
  }

  return (
    <section className="view active jira-agent" data-testid="jira-agent-page">
      <div className="page-toolbar">
        <div>
          <h2 className="jira-agent-title">JIRA agent</h2>
          <p className="hint">
            JIRA assignee 或 RM 把工单交给本组数字员工；agent 分析、复现、修复后在 JIRA 评论结论，由 assignee 决定下一步。
          </p>
        </div>
        <button type="button" className="btn sm" onClick={() => setShowMachines(true)}>
          系统机器
        </button>
      </div>
      {showMachines && <MachinesDialog onClose={() => setShowMachines(false)} />}
      <div className="jira-agent-layout">
        <aside className="jira-agent-sidebar">
          <IssueSearchPanel selectedKey={issueKey} onSelect={(key, conversation) => navigate(key, conversation)} />
        </aside>
        <div className="jira-agent-main">{main}</div>
      </div>
    </section>
  );
}

/** Links in JIRA comments carry only the conversation id. */
function ConversationRedirect({
  id,
  onNavigate,
}: {
  id: string;
  onNavigate: (issue: string, conversation?: string, replace?: boolean) => void;
}) {
  const detailQuery = useQuery({
    queryKey: jiraAgentConversationKey(id),
    queryFn: () => getConversation(id),
  });
  const issueKey = detailQuery.data?.conversation.issue_key;
  useEffect(() => {
    if (issueKey) onNavigate(issueKey, id, true);
  }, [issueKey, id, onNavigate]);
  if (detailQuery.isError) {
    return <div className="panel empty">加载对话失败：{errorMessage(detailQuery.error)}</div>;
  }
  return <div className="panel empty">加载中...</div>;
}

// ─────────────────────────────────────────────────────────────
// Issue search
// ─────────────────────────────────────────────────────────────

type SelectIssue = (key: string, conversation?: string) => void;

/** RM can switch between the search box and every issue the agent has handled. */
function IssueSearchPanel({ selectedKey, onSelect }: { selectedKey: string; onSelect: SelectIssue }) {
  const { user } = useAuth();
  const rm = isRM(user);
  const [view, setView] = useState<"search" | "handled">("search");
  const handled = rm && view === "handled";

  return (
    <div className="panel">
      <div className="panel-head">
        <strong>JIRA 工单</strong>
        {rm && (
          <div className="jira-agent-view-switch" role="group" aria-label="工单列表">
            <button
              type="button"
              className={`btn sm${handled ? "" : " active"}`}
              aria-pressed={!handled}
              onClick={() => setView("search")}
            >
              查找
            </button>
            <button
              type="button"
              className={`btn sm${handled ? " active" : ""}`}
              aria-pressed={handled}
              onClick={() => setView("handled")}
            >
              agent 处理过
            </button>
          </div>
        )}
      </div>
      {handled ? (
        <HandledIssueList selectedKey={selectedKey} onSelect={onSelect} />
      ) : (
        <IssueSearch selectedKey={selectedKey} onSelect={onSelect} />
      )}
    </div>
  );
}

function IssueSearch({ selectedKey, onSelect }: { selectedKey: string; onSelect: SelectIssue }) {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const searchQuery = useQuery({
    queryKey: jiraAgentIssueSearchKey(query),
    queryFn: () => searchIssues(query),
  });

  function search() {
    const next = input.trim();
    if (next === query) void searchQuery.refetch();
    else setQuery(next);
  }

  const result = searchQuery.data;
  return (
    <div className="panel-body jira-agent-search">
      <div className="row">
        <input
          aria-label="JIRA 编号或 JQL"
          placeholder="一个 JIRA 编号或 JQL，留空为默认列表"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") search();
          }}
        />
        <button type="button" className="btn sm" disabled={searchQuery.isFetching} onClick={search}>
          查询
        </button>
      </div>
      {searchQuery.isError && <p className="jira-agent-warning">{errorMessage(searchQuery.error)}</p>}
      {result && (
        <div className="jira-agent-query-hint muted">
          <span>
            {SEARCH_MODE_LABELS[result.mode]} · 共 {result.total} 个
            {result.issues.length < result.total ? `，仅显示前 ${result.issues.length} 个` : ""}
          </span>
          {result.jql && <code>{result.jql}</code>}
          {result.missing.length > 0 && (
            <span className="jira-agent-warning">未找到或无权访问：{result.missing.join("、")}</span>
          )}
        </div>
      )}
      <IssueResults
        issues={result?.issues}
        empty={!searchQuery.isFetching}
        selectedKey={selectedKey}
        onSelect={onSelect}
      />
    </div>
  );
}

function HandledIssueList({ selectedKey, onSelect }: { selectedKey: string; onSelect: SelectIssue }) {
  const [filter, setFilter] = useState("");
  const handledQuery = useQuery({
    queryKey: JIRA_AGENT_HANDLED_ISSUES_KEY,
    queryFn: listHandledIssues,
  });
  const result = handledQuery.data;
  const needle = filter.trim().toLowerCase();
  const issues = result?.issues.filter(
    (issue) =>
      !needle
      || [issue.key, issue.summary, issue.status, issue.assignee?.display_name ?? "", issue.agent?.latest_conversation.owner ?? ""]
        .some((value) => value.toLowerCase().includes(needle)),
  );

  return (
    <div className="panel-body jira-agent-search">
      <div className="row">
        <input
          aria-label="过滤 agent 处理过的工单"
          placeholder="按编号、标题、状态或人员过滤"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <button
          type="button"
          className="btn sm"
          disabled={handledQuery.isFetching}
          onClick={() => void handledQuery.refetch()}
        >
          刷新
        </button>
      </div>
      {handledQuery.isError && <p className="jira-agent-warning">{errorMessage(handledQuery.error)}</p>}
      {result && (
        <div className="jira-agent-query-hint muted">
          <span>
            {SEARCH_MODE_LABELS.handled} · 共 {result.total} 个
            {needle ? `，匹配 ${issues?.length ?? 0} 个` : ""}
            {!needle && result.issues.length + result.missing.length < result.total
              ? `，显示最近 ${result.issues.length + result.missing.length} 个`
              : ""}
          </span>
          {result.missing.length > 0 && (
            <span className="jira-agent-warning">JIRA 中未找到或无权访问：{result.missing.join("、")}</span>
          )}
        </div>
      )}
      <IssueResults issues={issues} empty={!handledQuery.isFetching} selectedKey={selectedKey} onSelect={onSelect} />
    </div>
  );
}

function IssueResults({
  issues,
  empty,
  selectedKey,
  onSelect,
}: {
  issues: IssueSearchItem[] | undefined;
  empty: boolean;
  selectedKey: string;
  onSelect: SelectIssue;
}) {
  if (!issues) return null;
  if (issues.length === 0) return empty ? <p className="muted">没有匹配的工单</p> : null;
  return (
    <div className="jira-agent-issue-results" data-testid="jira-agent-issue-results">
      {issues.map((issue) => {
        const open = issue.open_conversation;
        const agent = issue.agent;
        const latest = agent?.latest_conversation;
        let agentPill = null;
        if (latest) {
          const concluded = (latest.state === "closed" || latest.state === "waiting_review") && latest.conclusion;
          agentPill = (
            <span className={`pill ${STATE_TONE[latest.state]}`}>
              agent · {concluded ? (CONCLUSION_LABELS[latest.conclusion] ?? latest.conclusion) : STATE_LABELS[latest.state]}
            </span>
          );
        } else if (open) {
          agentPill = (
            <span className={`pill ${open.id ? STATE_TONE[open.state] : ""}`}>
              agent · {open.id ? STATE_LABELS[open.state] : `${open.owner} 的对话`}
            </span>
          );
        }
        return (
          <button
            type="button"
            key={issue.key}
            className={`jira-agent-list-item${issue.key === selectedKey ? " active" : ""}`}
            onClick={() => onSelect(issue.key, latest?.id)}
          >
            <span className="jira-agent-list-top">
              <strong>{issue.key}</strong>
              <span className="pill">{issue.status}</span>
            </span>
            <span className="jira-agent-list-summary">{issue.summary}</span>
            <span className="jira-agent-list-meta">
              <span className="muted">{issue.assignee?.display_name ?? "无 assignee"}</span>
              {agentPill}
            </span>
            {agent && latest && (
              <span className="jira-agent-list-meta muted">
                {agent.conversation_count} 个对话 · 最近 {latest.owner} · {agent.last_activity.slice(5, 16)}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Issue
// ─────────────────────────────────────────────────────────────

function IssueView({
  issueKey,
  conversationParam,
  onNavigate,
}: {
  issueKey: string;
  conversationParam: string;
  onNavigate: (issue: string, conversation?: string, replace?: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const previewQuery = useQuery({
    queryKey: jiraAgentIssueKey(issueKey),
    queryFn: () => previewIssue(issueKey),
  });
  const preview = previewQuery.data;
  const conversations = preview?.conversations ?? [];
  const open = preview?.open_conversation ?? null;

  // Explicit selection wins; otherwise open the assignee's live conversation,
  // else start from a hand-over draft.
  let selectedId = conversationParam === DRAFT ? "" : conversationParam;
  if (!conversationParam && open?.owner_is_assignee && conversations.some((c) => c.id === open.id)) {
    selectedId = open.id;
  }

  const detailQuery = useQuery({
    queryKey: jiraAgentConversationKey(selectedId),
    queryFn: () => getConversation(selectedId),
    enabled: Boolean(selectedId),
  });
  const conversation = selectedId ? detailQuery.data?.conversation : undefined;

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: jiraAgentIssueKey(issueKey) });
    void queryClient.invalidateQueries({ queryKey: JIRA_AGENT_ISSUE_SEARCH_KEY });
    if (selectedId) void queryClient.invalidateQueries({ queryKey: jiraAgentConversationKey(selectedId) });
  }, [queryClient, issueKey, selectedId]);

  if (previewQuery.isError) {
    return <div className="panel empty">加载 JIRA 工单失败：{errorMessage(previewQuery.error)}</div>;
  }
  if (!preview) {
    return <div className="panel empty">加载中...</div>;
  }

  async function cancel() {
    if (!(await confirmDialog({ body: "确认取消当前排队或运行中的这一轮？", danger: true, confirmText: "取消本轮" }))) return;
    try {
      await cancelConversationTurn(selectedId);
      toast.info("已请求取消");
      refresh();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const issue = preview.issue;
  const draft = !selectedId;
  const recovering = conversation?.phase === "recovering";
  return (
    <div className="jira-agent-conversation">
      <div className="panel">
        <div className="panel-body jira-agent-header">
          <div className="jira-agent-header-top">
            <div className="jira-agent-title-row">
              <a href={issue.url} target="_blank" rel="noreferrer">
                <strong>{issue.key}</strong>
              </a>
              <span>{issue.summary}</span>
              {conversations.length > 0 && (
                <select
                  aria-label="对话"
                  className="jira-agent-conversation-select"
                  value={selectedId || DRAFT}
                  onChange={(event) => onNavigate(issueKey, event.target.value)}
                >
                  {draft && <option value={DRAFT}>新对话（未交给 agent）</option>}
                  {conversations.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.owner} · {item.created_at.slice(5, 16)} · {item.read_only ? "只读" : STATE_LABELS[item.state]}
                    </option>
                  ))}
                </select>
              )}
            </div>
            {conversation ? (
              <span className={`pill ${STATE_TONE[conversation.state]}`}>{stateText(conversation)}</span>
            ) : (
              draft && <span className="pill">未交给 agent</span>
            )}
          </div>
          <div className="jira-agent-meta muted">
            <span>
              {issue.issue_type} · {issue.status} · {issue.priority}
            </span>
            <span>assignee：{issue.assignee?.display_name ?? "无"}</span>
            <span>组件：{issue.components.join(", ") || "无"}</span>
            <span>
              附件 {issue.attachment_count} · 评论 {issue.comment_count}
            </span>
          </div>
          {conversation && (
            <div className="jira-agent-meta muted">
              <span>owner：{conversation.owner}</span>
              <span>交单人：{conversation.created_by}</span>
              <span>数字员工：{conversation.agent_group}</span>
              <span>
                机器：{conversation.machine ? <code>{conversation.machine}</code> : "agent 从系统机器列表自动选择"}
              </span>
              <span>Thread：{conversation.thread_id || "未创建"}</span>
              <span>
                B 工作目录：<code>{conversation.workspace}</code>
              </span>
            </div>
          )}
          {!preview.can_handover && (
            <p className="jira-agent-warning">只有当前 JIRA assignee 或 RM 可以把工单交给 agent 或与 agent 对话。</p>
          )}
          {conversation?.read_only && (
            <p className="jira-agent-warning">
              {CLOSE_REASON_LABELS[conversation.close_reason] ?? "本对话已结束"}（只读）。
            </p>
          )}
          {draft && open && preview.can_handover && (
            <p className="jira-agent-warning">
              {open.owner_is_assignee
                ? `交给 agent 后，当前对话（${open.owner}）将结束并变为只读，新对话使用新的 Codex 会话和工作目录。`
                : `现有对话属于 ${open.owner}（assignee 已变更），交给 agent 后旧对话结束并新建对话。`}
            </p>
          )}
          {recovering && <p className="jira-agent-warning">{RECOVERING_TEXT}</p>}
          {conversation && (
            <div className="actions">
              {conversation.can_write && (conversation.state === "queued" || conversation.state === "running") && (
                <button type="button" className="btn sm danger" disabled={recovering} onClick={() => void cancel()}>
                  取消本轮
                </button>
              )}
              {preview.can_handover && (
                <button
                  type="button"
                  className="btn sm"
                  disabled={recovering}
                  onClick={() => onNavigate(issueKey, DRAFT)}
                >
                  新建对话
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {selectedId ? (
        <ConversationBody id={selectedId} onChanged={refresh} />
      ) : (
        <HandoverComposer
          preview={preview}
          onCreated={(id) => {
            refresh();
            onNavigate(issueKey, id);
          }}
        />
      )}
    </div>
  );
}

function HandoverComposer({ preview, onCreated }: { preview: IssuePreview; onCreated: (id: string) => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  // "" = the agent picks from the system list; null = the choice is not ready yet
  const [machine, setMachine] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const recovering = Boolean(preview.open_conversation?.recovering);
  const disabled = !preview.can_handover;

  async function submit() {
    const open = preview.open_conversation;
    if (
      open
      && !(await confirmDialog({
        title: "交给 agent",
        body: open.owner_is_assignee
          ? "当前对话将结束并变为只读（运行中会先中断），新对话使用新的 Codex 会话和工作目录。确认交给 agent？"
          : `现有对话属于 ${open.owner}，将结束并新建对话。确认交给 agent？`,
        confirmText: "交给 agent",
      }))
    ) {
      return;
    }
    setBusy(true);
    try {
      const files = await readFiles(fileInput.current?.files ?? null);
      const result = await handoverIssue({
        issue_key: preview.issue.key,
        note: note.trim(),
        files,
        new_conversation: true,
        machine: machine ?? "",
      });
      toast.success("已交给 agent，进入排队");
      onCreated(result.conversation.id);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-body jira-agent-composer">
        <textarea
          aria-label="交单说明"
          rows={3}
          placeholder="交单说明（可选），例如验收标准"
          value={note}
          disabled={disabled}
          onChange={(event) => setNote(event.target.value)}
        />
        <MachineChoice agentGroup={preview.agent_group} disabled={disabled} onChange={setMachine} />
        <div className="actions">
          <input ref={fileInput} type="file" multiple aria-label="附加文件" disabled={disabled} />
          <button
            type="button"
            className="btn primary"
            disabled={busy || disabled || recovering || machine === null}
            onClick={() => void submit()}
          >
            交给 agent
          </button>
        </div>
        {recovering && <p className="jira-agent-warning">{RECOVERING_TEXT}</p>}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Conversation
// ─────────────────────────────────────────────────────────────

function ConversationBody({ id, onChanged }: { id: string; onChanged: () => void }) {
  const detailQuery = useQuery({
    queryKey: jiraAgentConversationKey(id),
    queryFn: () => getConversation(id),
  });
  const detail = detailQuery.data;
  const conversation = detail?.conversation;
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const revRef = useRef(0);

  useEffect(() => {
    if (!detail) return;
    setEvents(detail.events);
    revRef.current = detail.rev;
  }, [detail]);

  const active = isActive(conversation);
  useEffect(() => {
    if (!active || !conversation) return undefined;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const response = await getConversationEvents(id, revRef.current);
        if (stopped) return;
        if (response.events.length > 0) {
          revRef.current = response.rev;
          setEvents((previous) => mergeEvents(previous, response.events));
        }
        const before = conversation.latest_turn;
        const after = response.conversation.latest_turn;
        if (
          response.conversation.state !== conversation.state
          || response.conversation.phase !== conversation.phase
          || after?.id !== before?.id
          || after?.status !== before?.status
          || after?.comment_status !== before?.comment_status
          || after?.queue_position !== before?.queue_position
        ) {
          onChanged();
        }
      } catch {
        // Keep polling; transient errors are expected while the server restarts.
      }
    }, 2000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [active, conversation, id, onChanged]);

  const turnsById = useMemo(() => new Map((detail?.turns ?? []).map((turn) => [turn.id, turn])), [detail]);

  if (detailQuery.isError) {
    return <div className="panel empty">加载对话失败：{errorMessage(detailQuery.error)}</div>;
  }
  if (!detail || !conversation) {
    return <div className="panel empty">加载中...</div>;
  }

  let lastTurnId = "";
  return (
    <>
      <div className="panel">
        <div className="panel-body jira-agent-timeline" data-testid="jira-agent-timeline">
          {groupTimeline(events).map((item) => {
            const turn = turnsById.get(item.turnId);
            const separator = item.turnId && item.turnId !== lastTurnId && turn;
            if (item.turnId) lastTurnId = item.turnId;
            return (
              <div key={item.key}>
                {separator && <TurnSeparator turn={turn} />}
                {item.tools ? (
                  <ToolGroup events={item.events} />
                ) : (
                  <EventRow
                    event={item.events[0]}
                    turn={turn}
                    files={detail.files}
                    canWrite={conversation.can_write}
                    onChanged={onChanged}
                  />
                )}
              </div>
            );
          })}
          {events.length === 0 && <p className="muted">暂无记录</p>}
        </div>
      </div>

      {detail.files.length > 0 && <FilesPanel files={detail.files} />}
      {conversation.can_write && <Composer conversation={conversation} onSent={onChanged} />}
    </>
  );
}

function TurnSeparator({ turn }: { turn: AgentTurn }) {
  const label = turn.trigger === "handover" ? "交单" : "补充要求";
  return (
    <div className="jira-agent-turn-separator">
      <span>
        第 {turn.seq} 轮 · {label} · {turn.created_by} · {turn.created_at}
      </span>
    </div>
  );
}

function ToolGroup({ events }: { events: AgentEvent[] }) {
  const count = (kind: string) => events.filter((event) => event.kind === kind).length;
  const commands = events.filter((event) => event.kind === "command");
  const running = commands.filter((event) => event.payload.status === "inProgress").length;
  const nonZero = commands.filter(
    (event) => event.payload.status !== "inProgress" && event.payload.exit_code !== 0,
  ).length;
  const lastCommand = commands[commands.length - 1];
  const parts = [
    commands.length ? `${commands.length} 条命令` : "",
    count("file_change") ? `${count("file_change")} 次文件修改` : "",
    count("tool") ? `${count("tool")} 次其他工具` : "",
    count("reasoning") ? `${count("reasoning")} 段思考摘要` : "",
  ].filter(Boolean);

  return (
    <details className="jira-agent-event tool-group" data-testid="jira-agent-tool-group">
      <summary>
        <span>工具调用 · {parts.join(" · ")}</span>
        {running > 0 && <span className="pill warn">执行中</span>}
        {nonZero > 0 && <span className="pill bad">{nonZero} 条非 0 退出</span>}
        {lastCommand && (
          <code className="jira-agent-command jira-agent-tool-group-last">
            $ {String(lastCommand.payload.command ?? "")}
          </code>
        )}
      </summary>
      <div className="jira-agent-tool-group-body">
        {events.map((event) => (
          <EventRow
            key={event.id}
            event={event}
            turn={undefined}
            files={[]}
            canWrite={false}
            onChanged={() => undefined}
          />
        ))}
      </div>
    </details>
  );
}

const MODE_LABELS: Record<string, string> = {
  handover: "交单",
  steer: "运行中补充，已直接发给 agent",
  merged: "已合并到排队中的这一轮",
  queued: "新一轮已排队",
};

function EventRow({
  event,
  turn,
  files,
  canWrite,
  onChanged,
}: {
  event: AgentEvent;
  turn: AgentTurn | undefined;
  files: AgentFile[];
  canWrite: boolean;
  onChanged: () => void;
}) {
  const payload = event.payload;
  const text = typeof payload.text === "string" ? payload.text : "";
  switch (event.kind) {
    case "user_message": {
      const attached = Array.isArray(payload.files) ? (payload.files as string[]) : [];
      return (
        <div className="jira-agent-event user">
          <div className="jira-agent-event-head">
            <strong>{String(payload.author ?? "")}</strong>
            <span className="muted">{MODE_LABELS[String(payload.mode)] ?? ""} · {event.created_at}</span>
          </div>
          {text && <div className="jira-agent-pre">{text}</div>}
          {attached.length > 0 && <div className="muted">附件：{attached.join("、")}</div>}
        </div>
      );
    }
    case "agent_message":
      return (
        <div className="jira-agent-event agent">
          <div className="jira-agent-event-head">
            <strong>agent</strong>
            <span className="muted">{event.created_at}</span>
          </div>
          <Markdown value={text} className="md-view jira-agent-md" />
        </div>
      );
    case "reasoning":
      return (
        <details className="jira-agent-event reasoning">
          <summary className="muted">思考摘要</summary>
          <div className="jira-agent-pre">{text}</div>
        </details>
      );
    case "command":
      return <CommandEvent event={event} />;
    case "file_change": {
      const changes = Array.isArray(payload.changes)
        ? (payload.changes as { path: string; kind: string; diff: string }[])
        : [];
      return (
        <details className="jira-agent-event tool">
          <summary>
            修改文件：{changes.map((change) => change.path).join("、")}{" "}
            <span className="muted">{String(payload.status ?? "")}</span>
          </summary>
          {changes.map((change) => (
            <pre key={change.path} className="jira-agent-output">{change.diff}</pre>
          ))}
        </details>
      );
    }
    case "tool":
      return (
        <details className="jira-agent-event tool">
          <summary>
            工具调用：{String(payload.type ?? "")}
          </summary>
          <pre className="jira-agent-output">{String(payload.detail ?? "")}</pre>
        </details>
      );
    case "result":
      return <ResultCard result={payload as unknown as AgentResult} files={files.filter((f) => f.turn_id === event.turn_id && f.source === "artifact")} />;
    case "jira_comment":
      return <CommentEvent event={event} turn={turn} canWrite={canWrite} onChanged={onChanged} />;
    case "error":
      return <div className="jira-agent-event error">{text || "错误"}</div>;
    default:
      return (
        <div className="jira-agent-event status muted">
          {text} <span>· {event.created_at}</span>
        </div>
      );
  }
}

function CommandEvent({ event }: { event: AgentEvent }) {
  const payload = event.payload;
  const status = String(payload.status ?? "");
  const exitCode = payload.exit_code as number | null | undefined;
  let tone = "warn";
  let label = "执行中";
  if (status !== "inProgress") {
    tone = exitCode === 0 ? "ok" : "bad";
    label = exitCode == null ? status : `exit ${exitCode}`;
  }
  const duration = typeof payload.duration_ms === "number" ? ` · ${(payload.duration_ms / 1000).toFixed(1)}s` : "";
  return (
    <details className="jira-agent-event command">
      <summary>
        <code className="jira-agent-command">$ {String(payload.command ?? "")}</code>
        <span className={`pill ${tone}`}>{label}</span>
        <span className="muted">{duration}</span>
      </summary>
      {payload.cwd ? <div className="muted">cwd: {String(payload.cwd)}</div> : null}
      <pre className="jira-agent-output">{String(payload.output ?? "") || "（无输出）"}</pre>
    </details>
  );
}

function ResultCard({ result, files }: { result: AgentResult; files: AgentFile[] }) {
  const ownership = result.ownership ?? { belongs_to_us: "", target_group: "", reasoning: "" };
  return (
    <div className="jira-agent-event result" data-testid="jira-agent-result">
      <div className="jira-agent-event-head">
        <strong>本轮结论</strong>
        <span className="pill accent">{CONCLUSION_LABELS[result.conclusion] ?? result.conclusion}</span>
      </div>
      <dl className="jira-agent-result-grid">
        <dt>问题分类</dt>
        <dd>{result.issue_category || "未分类"}</dd>
        {result.machine && (
          <>
            <dt>执行机器</dt>
            <dd>
              <code>{result.machine}</code>
            </dd>
          </>
        )}
        <dt>归属判断</dt>
        <dd>
          {OWNERSHIP_LABELS[ownership.belongs_to_us] ?? ownership.belongs_to_us}
          {ownership.target_group ? `（${ownership.target_group}）` : ""}
          {ownership.reasoning && <div className="muted">{ownership.reasoning}</div>}
        </dd>
        {result.root_cause && (
          <>
            <dt>根因</dt>
            <dd className="jira-agent-pre">{result.root_cause}</dd>
          </>
        )}
        <dt>复现</dt>
        <dd>
          {result.reproduction?.reproduced ? "已复现" : "未复现"}
          {result.reproduction?.environment ? `（${result.reproduction.environment}）` : ""}
          {result.reproduction?.steps?.length > 0 && (
            <ol>
              {result.reproduction.steps.map((step, index) => (
                <li key={index}><code>{step}</code></li>
              ))}
            </ol>
          )}
        </dd>
        {result.fix?.description && (
          <>
            <dt>修复说明</dt>
            <dd className="jira-agent-pre">{result.fix.description}</dd>
          </>
        )}
        {result.next_steps?.length > 0 && (
          <>
            <dt>下一步建议</dt>
            <dd>
              <ol>
                {result.next_steps.map((step, index) => (
                  <li key={index}>{step}</li>
                ))}
              </ol>
            </dd>
          </>
        )}
        {result.skills_used?.length > 0 && (
          <>
            <dt>使用的 skills</dt>
            <dd>{result.skills_used.join("、")}</dd>
          </>
        )}
      </dl>
      {result.summary && <Markdown value={result.summary} className="md-view jira-agent-md" />}
      {result.evidence?.length > 0 && (
        <div className="jira-agent-evidence">
          <strong>证据</strong>
          {result.evidence.map((item, index) => (
            <div key={index}>
              <div>{item.description}</div>
              <pre className="jira-agent-output">
                {item.command ? `$ ${item.command}\n` : ""}
                {item.result}
              </pre>
            </div>
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="jira-agent-artifacts">
          <strong>产物</strong>
          {files.map((file) => (
            <a key={file.id} href={fileDownloadUrl(file.conversation_id, file.id)}>
              {file.remote_path || file.name}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function CommentEvent({
  event,
  turn,
  canWrite,
  onChanged,
}: {
  event: AgentEvent;
  turn: AgentTurn | undefined;
  canWrite: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const posted = event.payload.status === "posted";

  async function retry() {
    if (!turn) return;
    setBusy(true);
    try {
      const response = await retryTurnComment(turn.id);
      if (response.turn.comment_status === "posted") toast.success("已发布 JIRA 评论");
      else toast.error(response.turn.comment_error || "发布失败");
      onChanged();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`jira-agent-event ${posted ? "comment" : "error"}`}>
      {posted ? (
        <span>已在 JIRA 发布评论（#{String(event.payload.comment_id ?? "")}），等待 assignee 决定下一步。</span>
      ) : (
        <span>JIRA 评论发布失败：{String(event.payload.error ?? "")}</span>
      )}
      {!posted && canWrite && turn?.comment_status === "failed" && (
        <button type="button" className="btn sm" disabled={busy} onClick={() => void retry()}>
          重试发布
        </button>
      )}
      {turn?.comment_body && (
        <details>
          <summary className="muted">评论内容</summary>
          <pre className="jira-agent-output">{turn.comment_body}</pre>
        </details>
      )}
    </div>
  );
}

function FilesPanel({ files }: { files: AgentFile[] }) {
  return (
    <details className="panel jira-agent-files">
      <summary className="panel-head">
        <strong>文件</strong> <span className="muted">{files.length}</span>
      </summary>
      <div className="panel-body">
        <table>
          <tbody>
            {files.map((file) => (
              <tr key={file.id}>
                <td>{SOURCE_LABELS[file.source]}</td>
                <td>
                  {file.downloadable ? (
                    <a href={fileDownloadUrl(file.conversation_id, file.id)}>{file.name}</a>
                  ) : (
                    file.name
                  )}
                </td>
                <td className="muted">{file.remote_path}</td>
                <td className="muted">{file.size} 字节</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function Composer({ conversation, onSent }: { conversation: AgentConversation; onSent: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const recovering = conversation.phase === "recovering";
  let placeholder = "补充信息或新的要求，发送后开始新一轮（例如：继续）";
  if (conversation.state === "running") placeholder = "运行中补充信息，会直接发给 agent";
  if (conversation.state === "queued") placeholder = "会合并到排队中的这一轮";
  if (recovering) placeholder = RECOVERING_TEXT;

  async function send() {
    setBusy(true);
    try {
      const files = await readFiles(fileInput.current?.files ?? null);
      const response = await sendConversationMessage(conversation.id, { text, files });
      toast.success(MODE_LABELS[response.mode] ?? "已发送");
      setText("");
      if (fileInput.current) fileInput.current.value = "";
      onSent();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-body jira-agent-composer">
        <textarea
          aria-label="补充信息"
          rows={3}
          placeholder={placeholder}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <div className="actions">
          <input ref={fileInput} type="file" multiple aria-label="附加文件" />
          <button
            type="button"
            className="btn primary"
            disabled={busy || recovering || !text.trim()}
            onClick={() => void send()}
          >
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
