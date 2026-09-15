import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "../../../api/AuthContext";
import { apiGet, apiPost } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";
import { JiraAgentPage } from "../JiraAgentPage";

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
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
  comment_status: "posted",
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
  id: "jac_1",
  issue_key: "MC3-7672",
  issue_summary: "saxpy 尾部元素错误",
  agent_group: "HPC",
  owner: "alice",
  created_by: "alice",
  thread_id: "thr_1",
  workspace: "/b/workspaces/MC3-7672-jac_1",
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
  issue_category: "结果错误",
  ownership: { belongs_to_us: "yes", target_group: "", reasoning: "应用代码问题" },
  summary: "修复尾部元素",
  root_cause: "向下取整",
  reproduction: { reproduced: true, environment: "A100", steps: ["./saxpy 16777217"] },
  evidence: [{ description: "修复后", command: "./saxpy 16777217", result: "PASS" }],
  fix: { description: "向上取整" },
  artifacts: ["artifacts/fix.patch"],
  next_steps: ["审阅补丁"],
  skills_used: ["hpc-bug-repro"],
};

const detail = {
  conversation,
  turns: [turn],
  files: [
    {
      id: "jaf_1", conversation_id: "jac_1", turn_id: "jat_1", direction: "output", source: "artifact",
      source_ref: "", name: "fix.patch", size: 20, sha256: "", remote_path: "artifacts/fix.patch",
      created_at: "", downloadable: true,
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
    ],
  };
}

function previewResponse(open: boolean) {
  return {
    issue: { ...issue, attachment_count: 2, comment_count: 1 },
    can_handover: true,
    open_conversation: open ? { id: "jac_1", owner: "alice", owner_is_assignee: true } : null,
    conversations: open ? [conversation, oldConversation] : [oldConversation],
  };
}

function mockBackend({ open = true }: { open?: boolean } = {}) {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === "/api/jira-agent/issues?scope=handled") return handledResponse();
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

function renderPage(route = "/jira-agent") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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
    vi.mocked(confirmDialog).mockResolvedValue(true);
    mockUser("Owner");
  });

  it("only RM can switch to every issue the agent handled, including closed ones", async () => {
    mockBackend({ open: false });
    const user = userEvent.setup();
    const { unmount } = renderPage();
    await screen.findByTestId("jira-agent-issue-results");
    expect(screen.queryByRole("button", { name: "agent 处理过" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("JIRA 编号或 JQL")).toHaveAttribute(
      "placeholder",
      "一个 JIRA 编号或 JQL，留空为默认列表",
    );
    unmount();

    mockUser("RM");
    renderPage();
    await user.click(await screen.findByRole("button", { name: "agent 处理过" }));

    const results = await screen.findByTestId("jira-agent-issue-results");
    expect(apiGet).toHaveBeenCalledWith("/api/jira-agent/issues?scope=handled");
    expect(within(results).getByText("Closed")).toBeInTheDocument();
    expect(within(results).getByText("agent · 无法复现")).toBeInTheDocument();
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

    expect(await screen.findByTestId("jira-agent-result")).toBeInTheDocument();
    // tool use is folded into a collapsed group; messages and results stay visible
    const toolGroup = screen.getByTestId("jira-agent-tool-group");
    expect(toolGroup).not.toHaveAttribute("open");
    expect(within(toolGroup).getByText("工具调用 · 1 条命令")).toBeInTheDocument();
    expect(within(toolGroup).getAllByText("$ ./saxpy 16777217").length).toBeGreaterThan(0);
    expect(within(toolGroup).getByText("exit 0")).toBeInTheDocument();
    expect(screen.getByText("请修复")).toBeInTheDocument();
    expect(screen.getByText(/已在 JIRA 发布评论（#99）/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "artifacts/fix.patch" })[0]).toHaveAttribute(
      "href",
      "/api/jira-agent/conversations/jac_1/files/jaf_1",
    );
    // old conversations live in a dropdown next to the title
    const select = screen.getByLabelText("对话") as HTMLSelectElement;
    expect(select.value).toBe("jac_1");
    expect(within(select).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "发送" })).toBeInTheDocument();
  });

  it("starts with a hand-over draft when the issue has no live conversation", async () => {
    mockBackend({ open: false });
    vi.mocked(apiPost).mockResolvedValue({ created: true, conversation });
    const user = userEvent.setup();
    renderPage("/jira-agent?issue=MC3-7672");

    const note = await screen.findByLabelText("交单说明");
    expect(note).toHaveAttribute("placeholder", "交单说明（可选），例如指定机器或者验收标准");
    expect(screen.queryByTestId("jira-agent-timeline")).not.toBeInTheDocument();

    await user.type(note, "用 A100");
    await user.click(screen.getByRole("button", { name: "交给 agent" }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/conversations", {
        issue_key: "MC3-7672",
        note: "用 A100",
        files: [],
        new_conversation: true,
      });
    });
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

    await user.click(screen.getByRole("button", { name: "交给 agent" }));
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
      });
    });
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

    expect(await screen.findByText("网站刚重启，正在重新接管本轮，几秒后可以操作")).toBeInTheDocument();
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

  it("resolves the issue for conversation links from JIRA comments", async () => {
    mockBackend();
    renderPage("/jira-agent?conversation=jac_1");

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("issue=MC3-7672");
    });
    expect(await screen.findByTestId("jira-agent-result")).toBeInTheDocument();
  });
});
