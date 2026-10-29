import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "../../../api/AuthContext";
import { apiFetch, apiGet, apiPost, apiPostNdjson } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";
import { toast } from "../../../lib/toast";
import { JiraAgentPage } from "../JiraAgentPage";
import { type LearningJob, type LearningProposal } from "../jiraLearningApi";

vi.mock("../../../api/http", () => ({
  apiFetch: vi.fn(),
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPostNdjson: vi.fn(),
}));

vi.mock("../../../lib/toast", () => ({
  toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../lib/confirm", () => ({
  confirmDialog: vi.fn(),
}));

vi.mock("../../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

function mockUser(role: string) {
  vi.mocked(useAuth).mockReturnValue({ user: { username: "alice", role } } as unknown as ReturnType<typeof useAuth>);
}

const turn = {
  id: "jat_1",
  conversation_id: "jac_1",
  seq: 1,
  trigger: "handover",
  created_by: "alice",
  input_text: "",
  status: "completed",
  codex_turn_id: "turn_1",
  result: null,
  conclusion: "fixed_pending_review",
  delivery_status: "complete",
  delivery_errors: [],
  comment_status: "posted",
  post_comment: true,
  comment_id: "99",
  comment_body: "h3. 结论",
  comment_error: "",
  error: "",
  created_at: "2026-09-14 10:00:00",
  started_at: "2026-09-14 10:00:01",
  finished_at: "2026-09-14 10:05:00",
  queue_position: null,
};

const conversation = {
  model_provider: "openai",
  id: "jac_1",
  issue_key: "MC3-7672",
  issue_summary: "saxpy 尾部元素错误",
  agent_group: "HPC",
  owner: "alice",
  created_by: "alice",
  thread_id: "thr_1",
  workspace: "/b/workspaces/MC3-7672-jac_1",
  machine: "",
  machine_used: "hpc@10.2.118.75",
  status: "open",
  close_reason: "",
  created_at: "2026-09-14 10:00:00",
  updated_at: "2026-09-14 10:05:00",
  closed_at: "",
  state: "waiting_review",
  phase: "",
  latest_turn: turn,
  read_only: false,
  can_write: true,
  browse_url: "http://jira/browse/MC3-7672",
};

const oldConversation = {
  ...conversation,
  id: "jac_0",
  status: "closed",
  close_reason: "new_conversation",
  state: "closed",
  read_only: true,
  can_write: false,
  created_at: "2026-09-13 09:00:00",
};

const result = {
  conclusion: "fixed_pending_review",
  action_required: "needs_review",
  assistance: { current_issue: "", owner: "", request: "" },
  issue_category: "结果错误",
  ownership: { belongs_to_us: "yes", target_group: "", reasoning: "应用代码问题" },
  summary: "修复尾部元素",
  root_cause: "向下取整",
  reproduction: { reproduced: true, environment: "A100", steps: ["./saxpy 16777217"] },
  evidence: [{ description: "修复后", command: "./saxpy 16777217", result: "PASS" }],
  fix: { description: "向上取整" },
  artifacts: ["artifacts/fix.patch"],
  next_steps: [],
  skills_used: ["hpc-bug-repro"],
};

const detail = {
  conversation,
  turns: [turn],
  files: [
    {
      id: "jaf_1", conversation_id: "jac_1", turn_id: "jat_1", direction: "output", source: "artifact",
      source_ref: "", name: "fix.patch", size: 20, sha256: "", remote_path: "artifacts/fix.patch",
      created_at: "", downloadable: true, preview: "--- a/saxpy.cu\n+++ b/saxpy.cu\n+fixed\n", preview_truncated: false,
    },
  ],
  events: [
    { id: "e1", conversation_id: "jac_1", turn_id: "jat_1", seq: 1, rev: 1, item_id: "", kind: "user_message",
      payload: { author: "alice", text: "请修复", files: [], mode: "handover" }, created_at: "", updated_at: "" },
    { id: "e2", conversation_id: "jac_1", turn_id: "jat_1", seq: 2, rev: 2, item_id: "cmd", kind: "command",
      payload: { command: "./saxpy 16777217", status: "completed", exit_code: 0, output: "PASS", cwd: "/b" }, created_at: "", updated_at: "" },
    { id: "e3", conversation_id: "jac_1", turn_id: "jat_1", seq: 3, rev: 3, item_id: "", kind: "result",
      payload: result, created_at: "", updated_at: "" },
    { id: "e4", conversation_id: "jac_1", turn_id: "jat_1", seq: 4, rev: 4, item_id: "", kind: "jira_comment",
      payload: { status: "posted", comment_id: "99" }, created_at: "", updated_at: "" },
  ],
  rev: 4,
  issue_conversations: [],
};

const issue = {
  key: "MC3-7672", url: "http://jira/browse/MC3-7672", summary: "saxpy 尾部元素错误", issue_type: "Bug",
  status: "Reopened", priority: "High", assignee: { name: "alice", display_name: "Alice" },
  components: ["PDE_HPC"],
};

function searchResponse(open: boolean) {
  return {
    mode: "mine",
    jql: 'assignee = "alice" AND status != Closed ORDER BY updated DESC',
    total: 1,
    missing: [],
    issues: [
      {
        ...issue, updated: "", can_handover: true,
        open_conversation: open ? { id: "jac_1", owner: "alice", owner_is_assignee: true, state: "waiting_review" } : null,
      },
    ],
  };
}

function handledResponse() {
  return {
    mode: "handled",
    jql: "",
    total: 2,
    missing: ["MC3-1"],
    issues: [
      {
        ...issue, status: "Closed", updated: "", can_handover: true, open_conversation: null,
        agent: {
          conversation_count: 2,
          last_activity: "2026-09-13 09:30:00",
          latest_conversation: { id: "jac_0", owner: "alice", state: "closed", conclusion: "cannot_reproduce" },
        },
      },
      {
        ...issue,
        key: "MC3-8000",
        url: "http://jira/browse/MC3-8000",
        summary: "矩阵计算性能下降",
        status: "Resolved",
        updated: "",
        can_handover: true,
        open_conversation: null,
        agent: {
          conversation_count: 1,
          last_activity: "2026-09-12 08:00:00",
          latest_conversation: { id: "jac_2", owner: "bob", state: "closed", conclusion: "analysis_done" },
        },
      },
    ],
  };
}

function previewResponse(open: boolean) {
  return {
    issue: { ...issue, attachment_count: 2, comment_count: 1 },
    agent_group: "HPC",
    can_handover: true,
    open_conversation: open ? { id: "jac_1", owner: "alice", owner_is_assignee: true } : null,
    conversations: open ? [conversation, oldConversation] : [oldConversation],
  };
}

const learningJob: LearningJob = {
  id: "jlj_1",
  issue_key: "MC3-7672",
  agent_group: "HPC",
  requested_by: "alice",
  parent_job_id: "",
  status: "queued",
  source_available: false,
  can_message: true,
  acquisition_status: "",
  error: "",
  created_at: "2026-09-24 15:00:00",
  started_at: "",
  finished_at: "",
};

const learningProposal: LearningProposal = {
  id: "proposal-1", job_id: learningJob.id, kind: "skill_update", title: "保留受控对照的判断依据",
  summary: "对照结果应能支持结论。", target_path: "SKILL.md", proposed_text: "补充结论与证据的对应关系。",
  patch_text: "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-old\n+new",
  applicability: "回退分析", evidence_ids: ["report"], limitations: "", validation: "检查引用证据。",
  status: "pending", published_commit: "", reviews: [], base_commit: "a".repeat(40), repository_ref: "main",
  files: [{ path: "SKILL.md", previous_text: "old", updated_text: "new", base_hash: "hash" }],
};

const machinesResponse = {
  groups: [{ name: "HPC", display_name: "HPC 数字员工" }],
  machines: [
    {
      id: "jam_1", agent_group: "HPC", ssh_target: "hpc@10.2.118.75", description: "1×A100 40GB",
      created_by: "carol", created_at: "", updated_at: "",
    },
  ],
  can_manage: false,
};

const keyInfoResponse = {
  agent_group: "HPC",
  display_name: "HPC 数字员工",
  fingerprint: "SHA256:lrNsVsx6+k+Hy/d+CeYkXjLtXLZxRxAKqM9wH/H/KuI",
  comment: "hpc-jira-agent@B",
  path: "/home/agent/.ssh/id_ed25519.pub",
};

function mockBackend({
  open = true,
  rm = false,
  jobs = [],
  proposals = [],
}: {
  open?: boolean;
  rm?: boolean;
  jobs?: LearningJob[];
  proposals?: LearningProposal[];
} = {}) {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === "/api/jira-agent/models?group=HPC") return {
      default_provider: "openai", default_model: "default-model", default_effort: "medium", errors: [],
      providers: [{ id: "openai", name: "OpenAI", default_model: "default-model", default_effort: "medium" }], models: ["default-model", "other-model"].map((model) => ({
        model, provider: "openai", displayName: model, defaultReasoningEffort: "medium",
        supportedReasoningEfforts: ["medium", "high"].map((reasoningEffort) => ({ reasoningEffort, description: reasoningEffort })),
      })),
    };
    if (path === "/api/jira-agent/machines") return { ...machinesResponse, can_manage: rm };
    if (path === "/api/jira-agent/ssh-key-info?group=HPC") return keyInfoResponse;
    if (path === "/api/jira-agent/issues?scope=handled") return handledResponse();
    if (path.startsWith("/api/jira-agent/learning/jobs?")) {
      const key = new URL(path, "http://test").searchParams.get("issue_key");
      return { jobs: key ? jobs.filter((job) => job.issue_key === key) : jobs, has_more: false };
    }
    if (path === "/api/jira-agent/learning/issues?group=HPC") return {
      issues: handledResponse().issues.map((issue) => ({ ...issue, learning: jobs.find((job) => job.issue_key === issue.key) })),
      missing: [], total: 2,
    };
    const learning = jobs.find((job) => path === `/api/jira-agent/learning/jobs/${job.id}`);
    if (learning) return {
      job: learning,
      turns: [{
        id: "jlt_1", job_id: learning.id, seq: 1, trigger: "initial",
        created_by: "alice", input_text: "发起知识学习", status: learning.status,
        error: learning.error, created_at: learning.created_at, started_at: "", finished_at: "",
      }],
      events: [
        {
          id: "jle_user", job_id: learning.id, turn_id: "jlt_1", seq: 1,
          kind: "user_message", payload: { author: "alice", text: "发起知识学习", mode: "initial" },
          created_at: learning.created_at,
        },
        {
          id: "jle_status", job_id: learning.id, turn_id: "jlt_1", seq: 2,
          kind: learning.status === "failed" ? "error" : "agent_message",
          payload: { text: learning.error || "已整理候选提案" }, created_at: learning.created_at,
        },
      ],
      proposals: proposals.filter((proposal) => proposal.job_id === learning.id),
      collection_gaps: proposals.length ? [{ source: "archive.zip", reason: "格式不支持" }] : [],
    };
    if (path.includes("/materials?")) return {
      items: [{ id: "report", kind: "agent_artifact", name: "REPORT.md", independent: true }], total: 1, next_offset: null,
    };
    if (path.includes("/material?")) return {
      success: true, contentItems: [{ type: "inputText", text: JSON.stringify({ text: "对照验证的结果", next_offset: null }) }],
    };
    if (path.endsWith("/source")) return {
      sources: [{ id: "report", kind: "agent_artifact", name: "REPORT.md", text: "对照验证的结果", independent: true }],
      collection_gaps: [{ source: "archive.zip", reason: "格式不支持" }],
    };
    if (path === "/api/jira-agent/learning/jobs/jlj_1") return {
      job: learningJob, turns: [], events: [], proposals: [],
    };
    if (path.startsWith("/api/jira-agent/issues?")) return searchResponse(open);
    if (path === "/api/jira-agent/issues/MC3-7672") return previewResponse(open);
    if (path === "/api/jira-agent/conversations/jac_1") return detail;
    if (path === "/api/jira-agent/conversations/jac_0") {
      return { ...detail, conversation: oldConversation, events: [], files: [] };
    }
    throw new Error(`unexpected GET ${path}`);
  });
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.search}</div>;
}

function renderPage(route = "/jira-agent", queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <JiraAgentPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("JiraAgentPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiPostNdjson).mockImplementation(async (_path, body, onItem) => {
      const request = body as { execution: { targets: { target: string }[] } };
      const targets = request.execution.targets.length ? request.execution.targets.map(t => t.target) : ["hpc@10.2.118.75"];
      (onItem as (item: unknown) => void)({ type: "completed", result: { id: "jpf_1", expires_at: Date.now() / 1000 + 600,
        execution: request.execution, snapshot: { results: targets.map(target => ({ target, ssh: { status: "pass", message: "免密登录通过" }, dataset: { status: "pass", message: "数据可读" } })),
        available_targets: targets, can_submit: true, requires_acknowledgement: false, warning_ids: [] } } });
    });
    vi.mocked(confirmDialog).mockResolvedValue(true);
    mockUser("Owner");
  });

  it.each(["Owner", "RM"])("shows cybersecurity lock with %s permissions", async (role) => {
    mockUser(role);
    mockBackend({ rm: role === "RM" });
    const base = vi.mocked(apiGet).getMockImplementation()!;
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/issues/MC3-7672") return {
        ...previewResponse(true), risk_locked: true, can_handover: role === "RM",
      };
      if (path === "/api/jira-agent/conversations/jac_1") return {
        ...detail, conversation: { ...conversation, risk_locked: true, can_write: role === "RM", read_only: role !== "RM" },
      };
      return base(path);
    });
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");
    expect(await screen.findByText(/owner 操作已锁定，仅 RM 可以处理/)).toBeVisible();
    if (role === "RM") {
      expect(await screen.findByLabelText("补充信息")).toBeVisible();
      expect(screen.getByRole("button", { name: "新建对话" })).toBeEnabled();
    } else {
      expect(screen.queryByLabelText("补充信息")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "新建对话" })).not.toBeInTheDocument();
    }
  });

  it.each(["scope_narrowed", "not_our_group"])("keeps reproduction visible for %s handoffs", async (conclusion) => {
    mockBackend();
    const base = vi.mocked(apiGet).getMockImplementation()!;
    const handoff = {
      ...result,
      conclusion,
      action_required: "needs_handoff",
      ownership: { belongs_to_us: "no", target_group: "SDK 维护团队", reasoning: "外部调用路径已确认，内部机制未确认" },
      root_cause: "",
      fix: { description: "" },
      reproduction: { reproduced: true, environment: "正常与异常 SDK", steps: ["./repro --check"] },
      summary: "独立案例已复现同一异常",
    };
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/conversations/jac_1") return {
        ...detail,
        events: detail.events.map((event) => event.kind === "result" ? { ...event, payload: handoff } : event),
      };
      return base(path);
    });
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");
    const card = within(await screen.findByTestId("jira-agent-result"));
    expect(card.getByText("需要转交")).toBeInTheDocument();
    expect(card.getByText(conclusion === "scope_narrowed" ? "范围已缩小" : "建议由其他组处理")).toBeInTheDocument();
    expect(card.getByText("SDK 维护团队")).toBeInTheDocument();
    expect(card.getByText("复现结果")).toBeInTheDocument();
    expect(card.getByText("./repro --check")).toBeInTheDocument();
    expect(card.queryByText("根因")).not.toBeInTheDocument();
    expect(card.queryByText("修复补丁")).not.toBeInTheDocument();
  });

  it("reconciles an uncertain publication without presenting it as a resend", async () => {
    mockBackend();
    const base = vi.mocked(apiGet).getMockImplementation()!;
    const pending = { ...turn, comment_status: "failed", comment_publication_state: "uncertain",
      comment_error: "原请求结果未知", comment_id: "" };
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/conversations/jac_1") return {
        ...detail, conversation: { ...conversation, latest_turn: pending }, turns: [pending],
        events: detail.events.map((event) => event.kind === "jira_comment"
          ? { ...event, payload: { status: "uncertain", error: "原请求结果未知" } } : event),
      };
      return base(path);
    });
    vi.mocked(apiPost).mockResolvedValue({ turn: pending });
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");
    expect(await screen.findByText(/JIRA 评论发布结果待确认/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试发布" })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "核对发布结果" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/turns/jat_1/comment/retry", {}));
    expect(apiPost).toHaveBeenCalledTimes(1);
  });

  it.each([true, false])("retries file delivery with the original comment choice %s", async (post) => {
    mockBackend();
    const base = vi.mocked(apiGet).getMockImplementation()!;
    const current = {
      turn: { ...turn, delivery_status: "incomplete", post_comment: post, comment_status: "",
        delivery_errors: [{ path: "artifacts/report.md", error: "文件不存在" }] },
    };
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/conversations/jac_1") return {
        ...detail,
        conversation: { ...conversation, state: current.turn.delivery_status === "complete" ? "waiting_review" : "delivery_incomplete", latest_turn: current.turn },
        turns: [current.turn],
        events: [...detail.events.filter((event) => event.kind !== "jira_comment"),
          { ...detail.events[3], kind: "delivery", payload: {} }],
      };
      return base(path);
    });
    vi.mocked(apiPost).mockImplementation(async () => {
      current.turn = { ...current.turn, delivery_status: "complete", delivery_errors: [] };
      return { turn: current.turn };
    });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");
    const panel = await screen.findByTestId("jira-agent-delivery");
    expect(within(panel).getByText("artifacts/report.md")).toBeInTheDocument();
    expect(within(panel).getByText(/重试只回收文件，不重新运行分析/)).toBeInTheDocument();
    expect(screen.getByText("分析结论（文件交付尚未完成）")).toBeInTheDocument();
    if (post) expect(within(panel).getByText(/暂停发布 JIRA 评论/)).toBeInTheDocument();
    else expect(within(panel).getByText(/本轮结论仅保留在网站/)).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "重试回收文件" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/turns/jat_1/delivery/retry", {}));
    await waitFor(() => expect(within(panel).getByText("交付文件已全部回收")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "重试回收文件" })).not.toBeInTheDocument();
    expect(apiPost).toHaveBeenCalledTimes(1);
  });

  it("disables follow-up and new conversations during delivery", async () => {
    mockBackend();
    const base = vi.mocked(apiGet).getMockImplementation()!;
    const collecting = { ...turn, delivery_status: "collecting", comment_status: "" };
    const current = { ...conversation, state: "delivering", latest_turn: collecting };
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/conversations/jac_1") return { ...detail, conversation: current, turns: [collecting] };
      if (path.startsWith("/api/jira-agent/conversations/jac_1/events")) return { conversation: current, events: [], rev: 4 };
      return base(path);
    });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");
    await user.type(await screen.findByLabelText("补充信息"), "等回收后继续");
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "新建对话" })).toBeDisabled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("shows Owner a scoped handled list without search, while RM retains search", async () => {
    mockBackend({ open: false });
    const user = userEvent.setup();
    const { unmount } = renderPage();
    await screen.findByTestId("jira-agent-issue-results");
    expect(screen.getByRole("button", { name: "我的工单" })).toBeInTheDocument();
    expect(screen.queryByLabelText("JIRA 编号或 JQL")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "agent 处理过" }));
    expect(apiGet).toHaveBeenCalledWith("/api/jira-agent/issues?scope=handled");
    expect(screen.queryByLabelText("过滤 agent 处理过的工单")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "知识学习" })).not.toBeInTheDocument();
    unmount();

    mockUser("RM");
    renderPage();
    expect(screen.getByLabelText("JIRA 编号或 JQL")).toHaveAttribute(
      "placeholder",
      "一个 JIRA 编号或 JQL，留空为默认列表",
    );
    await user.click(await screen.findByRole("button", { name: "agent 处理过" }));

    const results = await screen.findByTestId("jira-agent-issue-results");
    expect(apiGet).toHaveBeenCalledWith("/api/jira-agent/issues?scope=handled");
    expect(within(results).getByText("Closed")).toBeInTheDocument();
    expect(within(results).getByText("agent · 当前未复现")).toBeInTheDocument();
    expect(within(results).getByText(/2 个对话 · 最近 alice · 09-13 09:30/)).toBeInTheDocument();
    expect(screen.getByText(/JIRA 中未找到或无权访问：MC3-1/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("过滤 agent 处理过的工单"), "nothing");
    expect(await screen.findByText("没有匹配的工单")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("过滤 agent 处理过的工单"));

    await user.click(await screen.findByRole("button", { name: /MC3-7672/ }));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("issue=MC3-7672&conversation=jac_0");
    });
  });

  it("creates one joint learning task and opens it without losing source selection", async () => {
    mockUser("RM");
    const joint = { ...learningJob, issue_keys: ["MC3-7672", "MC3-8000"], title: "对照定位方法" };
    mockBackend({ jobs: [joint] });
    vi.mocked(apiPost).mockResolvedValue({ job: joint, reused: false });
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning");
    const list = await screen.findByTestId("jira-learning-issues");
    expect(screen.getByRole("button", { name: "联合学习" })).toBeDisabled();
    await user.click(await within(list).findByRole("checkbox", { name: "选择 Jira MC3-7672" }));
    await user.click(within(list).getByRole("checkbox", { name: "选择 Jira MC3-8000" }));
    await user.click(screen.getByRole("button", { name: "联合学习（2 张）" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("MC3-8000");
    await user.type(screen.getByLabelText("学习任务名称"), "对照定位方法");
    await user.click(screen.getByRole("button", { name: "开始学习" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/learning/jobs", {
      issue_keys: ["MC3-7672", "MC3-8000"], agent_group: "HPC", instructions: "", repository_only: false,
      title: "对照定位方法", request_id: expect.any(String),
    }));
    expect(await screen.findByRole("heading", { name: "对照定位方法" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "学习任务" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("location")).toHaveTextContent("learning_job=jlj_1");
    await user.click(screen.getByRole("tab", { name: "Jira" }));
    expect(screen.getByRole("checkbox", { name: "选择 Jira MC3-7672" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "选择 Jira MC3-8000" })).not.toBeChecked();
  });

  it("keeps hidden selections after a rejected joint request", async () => {
    mockUser("RM"); mockBackend();
    vi.mocked(apiPost).mockRejectedValue(new Error("MC3-8000：无权访问"));
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning");
    const list = await screen.findByTestId("jira-learning-issues");
    await user.click(await within(list).findByRole("checkbox", { name: "选择 Jira MC3-7672" }));
    await user.click(within(list).getByRole("checkbox", { name: "选择 Jira MC3-8000" }));
    await user.type(screen.getByLabelText("过滤学习工单"), "MC3-7672");
    expect(screen.getByText(/其中 1 张不在当前结果中/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "联合学习（2 张）" }));
    await user.click(screen.getByRole("button", { name: "开始学习" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无权访问");
    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.clear(screen.getByLabelText("过滤学习工单"));
    expect(screen.getByRole("checkbox", { name: "选择 Jira MC3-7672" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "选择 Jira MC3-8000" })).toBeChecked();
  });

  it("restores learning URL and keeps each job's draft separate", async () => {
    mockUser("RM"); mockBackend({ jobs: [learningJob, { ...learningJob, id: "second", issue_key: "MC3-8000" }] });
    const user = userEvent.setup();
    renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1&learning_tab=dialogue");
    await user.type(await screen.findByLabelText("补充学习要求"), "只属于第一张单");
    await user.click(screen.getByRole("button", { name: "查看 Jira MC3-8000 的学习状态" }));
    await user.click(await screen.findByRole("button", { name: /MC3-8000.*排队中/ }));
    await waitFor(() => expect(screen.getByLabelText("补充学习要求")).toHaveValue(""));
    await user.type(screen.getByLabelText("补充学习要求"), "第二张单");
    await user.click(screen.getByRole("tab", { name: "Jira" }));
    await user.click(screen.getByRole("button", { name: "查看 Jira MC3-7672 的学习状态" }));
    await user.click(await screen.findByRole("button", { name: /MC3-7672.*排队中/ }));
    await waitFor(() => expect(screen.getByLabelText("补充学习要求")).toHaveValue("只属于第一张单"));
    const process = screen.getByTestId("jira-learning-timeline").closest("details")!;
    expect(process).toHaveAttribute("open");
    await user.click(within(process).getByText("学习对话"));
    expect(process).not.toHaveAttribute("open");
    expect(screen.getByLabelText("补充学习要求")).toHaveValue("只属于第一张单");
    expect(screen.getByRole("heading", { name: "学习结果" })).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent("learning_job=jlj_1");
  });

  it("shows one proposal at a time, folds evidence and keeps the conversation on the same page", async () => {
    mockUser("RM");
    mockBackend({ jobs: [{ ...learningJob, status: "completed" }], proposals: [learningProposal,
      { ...learningProposal, id: "proposal-2", title: "补充独立诊断流程", kind: "new_skill", status: "approved" }] });
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1");
    const first = await screen.findByRole("button", { name: /保留受控对照的判断依据/ });
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    const process = screen.getByTestId("jira-learning-timeline").closest("details")!;
    expect(process).not.toHaveAttribute("open");
    expect(screen.getByLabelText("补充学习要求")).toBeVisible();
    await user.click(first);
    expect(screen.getByRole("button", { name: "审核通过" })).toBeVisible();
    expect(within(first.closest("article")!).getByText("修改对比").closest("details")).not.toHaveAttribute("open");
    await user.click(screen.getAllByText("来源证据")[0]);
    expect(await screen.findByText(/REPORT.md/)).toBeVisible();
    expect(screen.queryByText("格式不支持")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看学习材料" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /补充独立诊断流程/ }));
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    await user.click(within(process).getByText("学习对话"));
    expect(process).toHaveAttribute("open");
    expect(screen.getByRole("heading", { name: "学习结果" })).toBeVisible();
  });

  it("shows only failed evidence affecting a proposal, not skipped material inventories", async () => {
    mockUser("RM");
    mockBackend({ jobs: [{ ...learningJob, status: "completed" }], proposals: [{ ...learningProposal,
      material_gaps: [{ source_id: "bad-log", name: "bad.log", reason: "文件已不存在", impact: "不能核对异常侧耗时" }] }] });
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1");
    await user.click(await screen.findByRole("button", { name: /保留受控对照的判断依据/ }));
    expect(screen.getByText("影响判断的材料缺失")).toBeVisible();
    expect(screen.getByText(/不能核对异常侧耗时/)).toBeVisible();
    expect(screen.queryByText("格式不支持")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看学习材料" })).not.toBeInTheDocument();
  });

  it.each(["pending", "approved", "published"] as const)("omits %s legacy cases and superseded proposals", async (status) => {
    mockUser("RM");
    mockBackend({ jobs: [{ ...learningJob, status: "completed" }], proposals: [
      { ...learningProposal, kind: "case", title: "旧案例资料", status },
      { ...learningProposal, id: "old", title: "已替代提案", superseded_by: "covered" },
      { ...learningProposal, id: "covered", kind: "do_not_publish", title: "已有规则覆盖", previous_id: "old", files: [], patch_text: "" },
    ] });
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1");
    expect(await screen.findByText("无需更新", { exact: true })).toBeVisible();
    expect(screen.queryByLabelText("提案进度")).not.toBeInTheDocument();
    expect(screen.queryByText("历史提案")).not.toBeInTheDocument();
    expect(screen.queryByText("旧案例资料")).not.toBeInTheDocument();
    expect(screen.queryByText("已替代提案")).not.toBeInTheDocument();
    expect(screen.queryByTestId("jira-learning-proposal")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看上版" })).not.toBeInTheDocument();
    await user.click(screen.getByText("来源证据"));
    expect(await screen.findByText(/REPORT.md/)).toBeVisible();
  });

  it.each(["skill_update", "new_skill"] as const)("shows only actionable %s proposals when some knowledge is already covered", async (kind) => {
    mockUser("RM"); mockBackend({ jobs: [{ ...learningJob, status: "completed" }], proposals: [
      { ...learningProposal, kind },
      { ...learningProposal, id: "covered", kind: "do_not_publish", title: "验收规则已覆盖", summary: "没有必要重复添加验收规则。", files: [], patch_text: "" },
    ] });
    renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1");
    expect(await screen.findByLabelText("提案进度")).toHaveTextContent("1 项待审核");
    expect(screen.getAllByTestId("jira-learning-proposal")).toHaveLength(1);
    expect(screen.queryByTestId("jira-learning-no-update")).not.toBeInTheDocument();
    expect(screen.queryByText("验收规则已覆盖")).not.toBeInTheDocument();
    expect(screen.getByText("没有必要重复添加验收规则。")).not.toBeVisible();
  });

  it("combines no-update reasons and evidence into one outcome without review controls", async () => {
    mockUser("RM"); mockBackend({ jobs: [{ ...learningJob, status: "completed" }], proposals: [
      { ...learningProposal, kind: "do_not_publish", summary: "诊断规则已覆盖。", files: [], patch_text: "" },
      { ...learningProposal, id: "covered", kind: "do_not_publish", summary: "验收规则已覆盖。", files: [], patch_text: "" },
    ] });
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1");
    const result = await screen.findByTestId("jira-learning-no-update");
    expect(screen.getAllByText("无需更新", { exact: true })).toHaveLength(1);
    expect(within(result).getByText("诊断规则已覆盖。 验收规则已覆盖。")).toBeVisible();
    expect(screen.queryByTestId("jira-learning-proposal")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("提案进度")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    await user.click(within(result).getByText("来源证据"));
    expect(await within(result).findByText(/REPORT.md/)).toBeVisible();
    expect(within(result).getAllByText(/REPORT.md/)).toHaveLength(1);
  });

  it.each(["running", "failed", "cancelled"] as const)("does not present a final no-update outcome for a %s learning turn", async (status) => {
    mockUser("RM"); mockBackend({ jobs: [{ ...learningJob, status }], proposals: [
      { ...learningProposal, kind: "do_not_publish", files: [], patch_text: "" },
    ] });
    renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1");
    expect(await screen.findByRole("heading", { name: "学习结果" })).toBeVisible();
    expect(screen.queryByTestId("jira-learning-no-update")).not.toBeInTheDocument();
    expect(screen.queryByTestId("jira-learning-proposal")).not.toBeInTheDocument();
  });

  it("requires feedback to revise and does not publish when approving a proposal", async () => {
    mockUser("RM"); mockBackend({ jobs: [{ ...learningJob, status: "completed" }], proposals: [learningProposal] });
    vi.mocked(apiPost).mockResolvedValue({});
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1");
    await user.click(await screen.findByRole("button", { name: /保留受控对照/ }));
    expect(screen.getByRole("button", { name: "退回并重新整理" })).toBeDisabled();
    await user.type(screen.getByLabelText("审核意见"), "请注明适用条件");
    expect(screen.getByRole("button", { name: "退回并重新整理" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "审核通过" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/learning/proposals/proposal-1/review", { decision: "approved", note: "请注明适用条件" }));
    expect(apiPost).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "登记入库" })).not.toBeInTheDocument();
  });

  it("requires a commit for publication and keeps deployment registration separate", async () => {
    mockUser("RM"); mockBackend({ jobs: [{ ...learningJob, status: "completed" }], proposals: [
      { ...learningProposal, status: "approved" },
      { ...learningProposal, id: "published", title: "已入库的提案", status: "published", published_commit: "b".repeat(40) },
    ] });
    vi.mocked(apiPost).mockResolvedValue({});
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1");
    const handoff = await screen.findByText("人工更新与验证");
    expect(handoff.closest("details")).not.toHaveAttribute("open");
    await user.click(handoff);
    await user.click(screen.getByRole("tab", { name: "登记提交" }));
    await user.click(screen.getByRole("checkbox", { name: /保留受控对照/ }));
    expect(screen.getByRole("button", { name: "核对并登记" })).toBeDisabled();
    await user.type(screen.getByLabelText("统一入库提交 SHA"), "a".repeat(40));
    await user.click(screen.getByRole("button", { name: "核对并登记" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/learning/jobs/jlj_1/publication", { proposal_ids: ["proposal-1"], commit_sha: "a".repeat(40), note: "" }));
    await user.click(screen.getByRole("tab", { name: "登记部署" }));
    expect(screen.getByRole("button", { name: "登记部署结果" })).toBeDisabled();
    await user.type(screen.getByLabelText("部署目标服务"), "分析服务");
    await user.click(screen.getByRole("button", { name: "登记部署结果" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/learning/jobs/jlj_1/deployments", { target: "分析服务", commit_sha: "b".repeat(40), result: "succeeded", note: "" }));
  });

  it("prevents revisions of historical jobs and approval of blocked proposals", async () => {
    mockUser("RM"); mockBackend({ jobs: [{ ...learningJob, status: "completed", readonly: true }], proposals: [
      { ...learningProposal, review_blocker: "基线已发生变化" },
    ] });
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning&issue=MC3-7672&learning_job=jlj_1");
    await user.click(await screen.findByRole("button", { name: /保留受控对照/ }));
    await user.type(screen.getByLabelText("审核意见"), "补充证据");
    expect(screen.getByRole("button", { name: "审核通过" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "退回并重新整理" })).toBeDisabled();
    expect(screen.queryByLabelText("补充学习要求")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新采集并学习" })).not.toBeInTheDocument();
    expect(screen.getByText("历史对话只读，请在最新学习记录中继续。")).toBeVisible();
  });

  it("adds historical Jira without starting a learning task", async () => {
    mockUser("RM"); mockBackend();
    const base = vi.mocked(apiGet).getMockImplementation()!;
    vi.mocked(apiGet).mockImplementation(async (path: string) => path.includes("/learning/issues/preview?")
      ? { total: 1, issues: [{ key: "OLD-1", summary: "历史工单", status: "Closed" }] } : base(path));
    vi.mocked(apiPost).mockResolvedValue({ issues: [{ key: "OLD-1" }], missing: [] });
    const user = userEvent.setup(); renderPage("/jira-agent?view=learning");
    await user.click(await screen.findByRole("button", { name: "添加历史 Jira" }));
    await user.type(screen.getByLabelText("历史 Jira 编号或 JQL"), "OLD-1");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "查询" }));
    await user.click(await screen.findByRole("checkbox", { name: "加入 OLD-1" }));
    await user.click(screen.getByRole("button", { name: "加入列表（1）" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/learning/issues", { issue_keys: ["OLD-1"], agent_group: "HPC" }));
    expect(apiPost).not.toHaveBeenCalledWith("/api/jira-agent/learning/jobs", expect.anything());
  });

  it("lists issues with agent state and opens the live conversation of a clicked issue", async () => {
    mockBackend();
    const user = userEvent.setup();
    renderPage();

    const results = await screen.findByTestId("jira-agent-issue-results");
    expect(apiGet).toHaveBeenCalledWith("/api/jira-agent/issues?q=");
    expect(screen.getByText(/assignee = "alice"/)).toBeInTheDocument();
    expect(within(results).getByText("agent · 待 assignee 审阅")).toBeInTheDocument();
    expect(screen.queryByText("对话", { selector: "strong" })).not.toBeInTheDocument();

    await user.click(within(results).getByRole("button", { name: /MC3-7672/ }));

    const resultCard = await screen.findByTestId("jira-agent-result");
    expect(within(resultCard).getByText("修复已验证")).toBeInTheDocument();
    expect(within(resultCard).getByText("需要审核")).toBeInTheDocument();
    expect(within(resultCard).getByText("待审核")).toBeInTheDocument();
    expect(within(resultCard).getByText("修复补丁")).toBeInTheDocument();
    expect(within(resultCard).getByText(/--- a\/saxpy\.cu/)).toBeInTheDocument();
    expect(within(resultCard).getByRole("link", { name: "下载完整补丁" })).toHaveAttribute(
      "href",
      "/api/jira-agent/conversations/jac_1/files/jaf_1",
    );
    // tool use is folded into a collapsed group; messages and results stay visible
    const toolGroup = screen.getByTestId("jira-agent-tool-group");
    expect(toolGroup).not.toHaveAttribute("open");
    expect(within(toolGroup).getByText("工具调用 · 1 条命令")).toBeInTheDocument();
    expect(within(toolGroup).getAllByText("$ ./saxpy 16777217").length).toBeGreaterThan(0);
    expect(within(toolGroup).getByText("exit 0")).toBeInTheDocument();
    expect(screen.getByText("请修复")).toBeInTheDocument();
    expect(screen.getByText(/机器：/)).toHaveTextContent("机器：hpc@10.2.118.75（agent 选择）");
    expect(screen.getByText(/已在 JIRA 发布评论（#99）/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "fix.patch" })).toHaveAttribute(
      "href",
      "/api/jira-agent/conversations/jac_1/files/jaf_1",
    );
    // old conversations live in a dropdown next to the title
    const select = screen.getByLabelText("对话") as HTMLSelectElement;
    expect(select.value).toBe("jac_1");
    expect(within(select).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "发送" })).toBeInTheDocument();
  });

  it.each(["", "对话已删除，但 B 上目录未清理：10.2.118.75:/b/workspaces/MC3-7672-jac_0，请联系管理员手动清理"])("lets RM delete history and switches conversations with warning %s", async (warning) => {
    mockBackend();
    mockUser("RM");
    const base = vi.mocked(apiGet).getMockImplementation()!;
    let deleted = false;
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (deleted && path === "/api/jira-agent/issues/MC3-7672") return {
        ...previewResponse(true), conversations: [conversation],
      };
      return base(path);
    });
    vi.mocked(apiFetch).mockImplementation(async () => {
      deleted = true;
      return { deleted: true, issue_key: "MC3-7672", warning };
    });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_0");
    await user.click(await screen.findByRole("button", { name: "删除对话" }));
    expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ danger: true, confirmText: "删除对话", body: expect.stringContaining("B 端清理失败也会继续删除对话") }));
    expect(apiFetch).toHaveBeenCalledWith("/api/jira-agent/conversations/jac_0", { method: "DELETE" });
    if (warning) await waitFor(() => expect(toast.info).toHaveBeenCalledWith(warning));
    await waitFor(() => expect(screen.getByLabelText("对话")).toHaveValue("jac_1"));
    expect(within(screen.getByLabelText("对话")).getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "删除对话" })).toBeEnabled();
  });

  it("lets RM delete the latest conversation and returns to the handover form", async () => {
    mockBackend();
    mockUser("RM");
    const base = vi.mocked(apiGet).getMockImplementation()!;
    let deleted = false;
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/issues/MC3-7672") return {
        ...previewResponse(true), conversations: deleted ? [] : [conversation],
        open_conversation: deleted ? null : conversation,
      };
      return base(path);
    });
    vi.mocked(apiFetch).mockImplementation(async () => {
      deleted = true;
      return { deleted: true, issue_key: "MC3-7672", warning: "" };
    });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");
    await user.click(await screen.findByRole("button", { name: "删除对话" }));
    expect(apiFetch).toHaveBeenCalledWith("/api/jira-agent/conversations/jac_1", { method: "DELETE" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "删除对话" })).not.toBeInTheDocument());
    expect(await screen.findByRole("button", { name: "交给 agent" })).toBeInTheDocument();
  });

  it("does not delete history when RM cancels confirmation", async () => {
    mockBackend();
    mockUser("RM");
    vi.mocked(confirmDialog).mockResolvedValue(false);
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_0");
    await user.click(await screen.findByRole("button", { name: "删除对话" }));
    expect(apiFetch).not.toHaveBeenCalled();
    expect(screen.getByLabelText("对话")).toHaveValue("jac_0");
  });

  it("does not offer history deletion to Owner", async () => {
    mockBackend();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_0");
    await screen.findByLabelText("对话");
    expect(screen.queryByRole("button", { name: "删除对话" })).not.toBeInTheDocument();
  });

  it.each([false, true])("RM selects a model and effort for handover/followup: %s", async (followup) => {
    mockUser("RM");
    mockBackend({ open: followup, rm: true });
    vi.mocked(apiPost).mockResolvedValue({ created: true, mode: "queued", conversation });
    const user = userEvent.setup();
    renderPage(`/jira-agent?issue=MC3-7672${followup ? "&conversation=jac_1" : ""}`);
    const model = await screen.findByLabelText("模型");
    await waitFor(() => expect(model).toBeEnabled());
    expect(screen.getByRole("option", { name: "default-model (default)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "medium (default)" })).toBeInTheDocument();
    await user.selectOptions(model, "other-model");
    await user.selectOptions(screen.getByLabelText("effort"), "high");
    await user.type(screen.getByLabelText(followup ? "补充信息" : "交单说明"), "分析问题");
    const submit = screen.getByRole("button", { name: followup ? "发送" : "交给 agent" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(
      followup ? "/api/jira-agent/conversations/jac_1/messages" : "/api/jira-agent/conversations",
      expect.objectContaining({ model: "other-model", effort: "high" }),
    ));
    if (followup) await waitFor(() => expect(screen.getByLabelText("模型")).toHaveValue("other-model"));
  });

  it("Owner cannot see model choices", async () => {
    mockBackend();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");
    await screen.findByLabelText("补充信息");
    expect(screen.queryByLabelText("模型")).not.toBeInTheDocument();
    expect(apiGet).not.toHaveBeenCalledWith("/api/jira-agent/models?group=HPC");
  });

  it.each([false, true])("offers DeepSeek at handover and locks its provider in a conversation: %s", async (followup) => {
    mockUser("RM");
    mockBackend({ open: followup, rm: true });
    const base = vi.mocked(apiGet).getMockImplementation()!;
    const deepseek = {
      ...conversation, model_provider: "deepseek",
      latest_turn: { ...turn, model: "deepseek-flash", effort: "high", post_comment: false },
    };
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/models?group=HPC") {
        const original = await base(path) as { providers: unknown[]; models: unknown[] };
        return {
          ...original,
          providers: [...original.providers, { id: "deepseek", name: "DeepSeek", default_model: "deepseek-flash", default_effort: "high" }],
          models: [...original.models, ...["deepseek-flash", "deepseek-v4-pro"].map((model) => ({
            model, provider: "deepseek", displayName: model, defaultReasoningEffort: "high",
            supportedReasoningEfforts: ["low", "high", "max"].map((reasoningEffort) => ({ reasoningEffort, description: reasoningEffort })),
          }))],
        };
      }
      if (followup && path === "/api/jira-agent/conversations/jac_1") return { ...detail, conversation: deepseek };
      return base(path);
    });
    vi.mocked(apiPost).mockResolvedValue({ created: true, mode: "queued", conversation: deepseek });
    const user = userEvent.setup();
    renderPage(`/jira-agent?issue=MC3-7672${followup ? "&conversation=jac_1" : ""}`);
    const provider = await screen.findByLabelText("服务商");
    if (followup) {
      expect(provider).toBeDisabled();
      expect(provider).toHaveValue("deepseek");
      expect(screen.getByText("切换服务商需新建对话")).toBeInTheDocument();
    } else {
      await waitFor(() => expect(provider).toBeEnabled());
      expect(provider).toHaveValue("openai");
      await user.selectOptions(provider, "deepseek");
    }
    const model = screen.getByLabelText("模型");
    await waitFor(() => expect(model).toBeEnabled());
    expect(within(model).queryByRole("option", { name: "other-model" })).not.toBeInTheDocument();
    expect(within(model).getByRole("option", { name: "deepseek-flash (default)" })).toBeInTheDocument();
    await user.selectOptions(model, "deepseek-v4-pro");
    const effort = screen.getByLabelText("effort");
    expect(within(effort).queryByRole("option", { name: "medium" })).not.toBeInTheDocument();
    await user.selectOptions(effort, "max");
    await user.type(screen.getByLabelText(followup ? "补充信息" : "交单说明"), "使用所选模型分析");
    await user.click(screen.getByRole("button", { name: followup ? "发送" : "交给 agent" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(
      followup ? "/api/jira-agent/conversations/jac_1/messages" : "/api/jira-agent/conversations",
      expect.objectContaining({ model_provider: "deepseek", model: "deepseek-v4-pro", effort: "max" }),
    ));
  });

  it("starts with a hand-over draft when the issue has no live conversation", async () => {
    mockBackend({ open: false });
    vi.mocked(apiPost).mockResolvedValue({ created: true, conversation });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672");

    const note = await screen.findByLabelText("交单说明");
    expect(note).toHaveAttribute("placeholder", "交单说明（可选），例如验收标准");
    expect(screen.queryByTestId("jira-agent-timeline")).not.toBeInTheDocument();

    await user.type(note, "用 A100");
    expect(await screen.findByText(/本组 1 台/)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "交给 agent" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/conversations", {
        issue_key: "MC3-7672",
        note: "用 A100",
        files: [],
        new_conversation: true,
        preflight_id: "",
        request_id: expect.any(String),
        acknowledged_issue_ids: [],
        post_comment: true,
      });
    });
    expect(screen.queryByRole("dialog", { name: "执行环境检查" })).not.toBeInTheDocument();
    expect(apiPostNdjson).not.toHaveBeenCalled();
    expect(confirmDialog).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("conversation=jac_1");
    });
  });

  it("new conversation opens a draft and only hands over after confirmation", async () => {
    mockBackend();
    vi.mocked(apiPost).mockResolvedValue({ created: true, conversation: { ...conversation, id: "jac_2" } });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");

    await user.click(await screen.findByRole("button", { name: "新建对话" }));
    expect(await screen.findByLabelText("交单说明")).toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
    expect(screen.getByText(/当前对话（alice）将结束并变为只读/)).toBeInTheDocument();

    const submit = screen.getByRole("button", { name: "交给 agent" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    expect(vi.mocked(apiPost).mock.calls[0][1]).toMatchObject({ new_conversation: true });
  });

  it("sends a follow-up message in the open conversation", async () => {
    mockBackend();
    vi.mocked(apiPost).mockResolvedValue({ mode: "queued", conversation });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");

    await user.type(await screen.findByLabelText("补充信息"), "补充 N=257 验证");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/conversations/jac_1/messages", {
        text: "补充 N=257 验证",
        files: [],
        post_comment: true,
      });
    });
  });

  it("can keep a follow-up turn's answer on the website instead of commenting on JIRA", async () => {
    mockBackend();
    vi.mocked(apiPost).mockResolvedValue({ mode: "queued", conversation });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");

    await user.type(await screen.findByLabelText("补充信息"), "解释一下根因");
    await user.click(screen.getByRole("checkbox", { name: /本轮结论发布到 JIRA 评论/ }));
    expect(screen.getByText(/只在网页显示/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/conversations/jac_1/messages", {
        text: "解释一下根因",
        files: [],
        post_comment: false,
      });
    });
  });

  it.each(["queued", "running"])("inherits the %s turn's comment choice after completion", async (state) => {
    mockBackend();
    let current = { ...conversation, state, latest_turn: { ...turn, status: state, post_comment: false } };
    const base = vi.mocked(apiGet).getMockImplementation()!;
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/conversations/jac_1") return { ...detail, conversation: current };
      if (path.startsWith("/api/jira-agent/conversations/jac_1/events")) {
        return { conversation: current, events: [], rev: detail.rev };
      }
      return base(path);
    });
    vi.mocked(apiPost).mockImplementation(async () => ({ mode: "merged", conversation: current }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1", client);
    const checkbox = await screen.findByRole("checkbox", { name: /本轮结论发布到 JIRA 评论/ });
    expect(checkbox).not.toBeChecked();
    await user.type(screen.getByLabelText("补充信息"), "补充证据");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith(
      "/api/jira-agent/conversations/jac_1/messages",
      { text: "补充证据", files: [], post_comment: false },
    ));
    expect(checkbox).not.toBeChecked();

    // Refreshes and queued -> running must preserve unsent edits.
    await user.click(checkbox);
    await user.type(screen.getByLabelText("补充信息"), "尚未发送");
    current = { ...current, state: "running", latest_turn: { ...current.latest_turn, status: "running" } };
    await act(async () => { await client.invalidateQueries({ queryKey: ["jira-agent", "conversation", "jac_1"] }); });
    expect(checkbox).toBeChecked();
    await user.click(checkbox);
    current = { ...current, state: "waiting_review", latest_turn: { ...current.latest_turn, status: "completed" } };
    await act(async () => { await client.invalidateQueries({ queryKey: ["jira-agent", "conversation", "jac_1"] }); });
    await waitFor(() => expect(checkbox).not.toBeChecked());
    expect(screen.getByLabelText("补充信息")).toHaveValue("尚未发送");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(apiPost).toHaveBeenLastCalledWith(
      "/api/jira-agent/conversations/jac_1/messages",
      { text: "尚未发送", files: [], post_comment: false },
    ));
  });

  it("disables actions while a restarted site re-attaches to the running turn", async () => {
    const recovering = { ...conversation, state: "running", phase: "recovering" };
    mockBackend();
    const base = vi.mocked(apiGet).getMockImplementation()!;
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/conversations/jac_1") return { ...detail, conversation: recovering };
      if (path.startsWith("/api/jira-agent/conversations/jac_1/events")) {
        return { conversation: recovering, events: [], rev: detail.rev };
      }
      if (path === "/api/jira-agent/issues/MC3-7672") {
        return {
          ...previewResponse(true),
          open_conversation: { id: "jac_1", owner: "alice", owner_is_assignee: true, recovering: true },
          conversations: [recovering, oldConversation],
        };
      }
      return base(path);
    });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");

    expect(await screen.findByText("正在重新接管本轮并核对远端状态，稍后可以操作")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消本轮" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "新建对话" })).toBeDisabled();
    await user.type(screen.getByLabelText("补充信息"), "继续");
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("shows an old conversation read-only from the dropdown", async () => {
    mockBackend();
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672&conversation=jac_1");

    await user.selectOptions(await screen.findByLabelText("对话"), "jac_0");
    expect(await screen.findByText(/已新建对话，本对话已结束/)).toBeInTheDocument();
    expect(screen.queryByLabelText("补充信息")).not.toBeInTheDocument();
  });

  it("every hand-over to a user-given machine uploads the SSH key after the risks are confirmed", async () => {
    mockBackend({ open: false });
    const session = {
      id: "jak_1", agent_group: "HPC", target: "tester@10.0.0.9", fingerprint: keyInfoResponse.fingerprint,
      status: "running", message: "", exit_code: null, output: "password: ", offset: 10,
    };
    const backendGet = vi.mocked(apiGet).getMockImplementation()!;
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path.startsWith("/api/jira-agent/ssh-key-sessions/jak_1")) {
        return { ...session, status: "succeeded", message: "连接测试通过", output: "", offset: 10 };
      }
      return backendGet(path);
    });
    vi.mocked(apiPost).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/ssh-key-sessions") return session;
      if (path === "/api/jira-agent/conversations") return { created: true, conversation };
      throw new Error(`unexpected POST ${path}`);
    });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672");

    await user.click(await screen.findByLabelText(/自填 user@host/));
    const submit = screen.getByRole("button", { name: "交给 agent" });
    await user.type(screen.getByLabelText("自填机器"), "tester@10.0.0.9");
    expect(await screen.findByText(/交单前逐台完成登录授权/)).toBeInTheDocument();
    await waitFor(() => expect(submit).toBeEnabled());

    // closing the dialog without a passed upload hands nothing over
    await user.click(submit);
    await user.click(await screen.findByRole("button", { name: "输入密码并检查" }));
    await user.click(within(screen.getByRole("dialog", { name: "机器登录授权" })).getByRole("button", { name: "关闭" }));
    await user.click(screen.getByRole("button", { name: "返回修改" }));
    expect(apiPost).not.toHaveBeenCalledWith("/api/jira-agent/conversations", expect.anything());

    await user.click(submit);
    await user.click(await screen.findByRole("button", { name: "输入密码并检查" }));
    const dialog = screen.getByRole("dialog", { name: "机器登录授权" });
    expect(within(dialog).getByTestId("jira-agent-key-warning")).toHaveTextContent("请使用专用测试账号，不要使用个人账号");
    expect(within(dialog).getByTestId("jira-agent-key-warning")).toHaveTextContent("免密登录");
    const start = within(dialog).getByRole("button", { name: "开始登录" });
    expect(start).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(start);

    expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/ssh-key-sessions", { agent_group: "HPC", target: "tester@10.0.0.9" });
    expect(within(dialog).getByLabelText("机器登录密码")).toHaveAttribute("type", "password");
    await waitFor(
      () => {
        expect(apiPost).toHaveBeenCalledWith(
          "/api/jira-agent/conversations",
          expect.objectContaining({ preflight_id: "jpf_1", acknowledged_issue_ids: [] }),
        );
      },
      { timeout: 3000 },
    );
    expect(vi.mocked(apiPost).mock.calls.filter(([path]) => path === "/api/jira-agent/conversations")).toHaveLength(1);
  });

  it("everyone can view the system machines, only RM can change them", async () => {
    mockBackend({ open: false });
    const user = userEvent.setup();
    const { unmount } = renderPage();
    await user.click(screen.getByRole("button", { name: "系统机器" }));
    const table = await screen.findByTestId("jira-agent-machine-table");
    expect(within(table).getByText("hpc@10.2.118.75")).toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("新机器")).not.toBeInTheDocument();
    unmount();

    mockUser("RM");
    mockBackend({ open: false, rm: true });
    vi.mocked(apiPost).mockResolvedValue({ machine: {} });
    renderPage();
    await user.click(screen.getByRole("button", { name: "系统机器" }));
    expect(await screen.findByRole("button", { name: "删除" })).toBeInTheDocument();
    const add = screen.getByRole("button", { name: "添加" });
    await user.type(screen.getByLabelText("新机器"), "-oProxyCommand=x@h");
    expect(add).toBeDisabled();
    await user.clear(screen.getByLabelText("新机器"));
    await user.type(screen.getByLabelText("新机器"), "hpc2@10.2.118.76");
    await user.type(screen.getByLabelText("新机器说明"), "2×A100");
    await user.click(add);
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/machines", {
        agent_group: "HPC", ssh_target: "hpc2@10.2.118.76", description: "2×A100",
      });
    });
  });

  it("resolves the issue for conversation links from JIRA comments", async () => {
    mockBackend();
    renderPage("/jira-agent?conversation=jac_1");

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("issue=MC3-7672");
    });
    expect(await screen.findByTestId("jira-agent-result")).toBeInTheDocument();
  });
});
