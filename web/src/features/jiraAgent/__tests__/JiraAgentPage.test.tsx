import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, apiPost } from "../../../api/http";
import { JiraAgentPage } from "../JiraAgentPage";

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

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
  issue_conversations: [{ id: "jac_1", owner: "alice", status: "open", close_reason: "", created_at: "2026-09-14 10:00:00" }],
};

function renderPage(route = "/jira-agent") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <JiraAgentPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("JiraAgentPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/conversations") return { conversations: [conversation] };
      if (path === "/api/jira-agent/conversations/jac_1") return detail;
      if (path.startsWith("/api/jira-agent/issues/")) {
        return {
          issue: {
            key: "MC3-7672", url: "http://jira/browse/MC3-7672", summary: "saxpy 尾部元素错误", issue_type: "Bug",
            status: "Open", priority: "High", assignee: { name: "alice", display_name: "Alice" },
            components: ["PDE_HPC"], attachment_count: 2, comment_count: 1,
          },
          can_handover: true,
          open_conversation: null,
          conversations: [],
        };
      }
      throw new Error(`unexpected GET ${path}`);
    });
  });

  it("lists conversations and renders the timeline with result and comment", async () => {
    renderPage("/jira-agent?conversation=jac_1");

    expect(await screen.findByTestId("jira-agent-result")).toBeInTheDocument();
    expect(screen.getAllByText("MC3-7672").length).toBeGreaterThan(0);
    expect(screen.getByText("$ ./saxpy 16777217")).toBeInTheDocument();
    expect(screen.getByText("exit 0")).toBeInTheDocument();
    expect(screen.getAllByText("已修复，方案请审批").length).toBeGreaterThan(0);
    expect(screen.getByText(/已在 JIRA 发布评论（#99）/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "artifacts/fix.patch" })[0]).toHaveAttribute(
      "href",
      "/api/jira-agent/conversations/jac_1/files/jaf_1",
    );
  });

  it("previews an issue and hands it over", async () => {
    vi.mocked(apiPost).mockResolvedValue({ created: true, conversation });
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("JIRA 编号"), "MC3-7672");
    await user.click(screen.getByRole("button", { name: "查询" }));
    await user.type(await screen.findByLabelText("交单说明"), "用 A100");
    await user.click(screen.getByRole("button", { name: "交给 agent" }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/conversations", {
        issue_key: "MC3-7672",
        note: "用 A100",
        files: [],
        new_conversation: false,
      });
    });
    expect(await screen.findByTestId("jira-agent-result")).toBeInTheDocument();
  });

  it("sends a follow-up message in the open conversation", async () => {
    vi.mocked(apiPost).mockResolvedValue({ mode: "queued", conversation });
    const user = userEvent.setup();
    renderPage("/jira-agent?conversation=jac_1");

    await user.type(await screen.findByLabelText("补充信息"), "补充 N=257 验证");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/conversations/jac_1/messages", {
        text: "补充 N=257 验证",
        files: [],
      });
    });
  });

  it("hides the composer for read-only conversations", async () => {
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/jira-agent/conversations") return { conversations: [] };
      return {
        ...detail,
        conversation: { ...conversation, status: "closed", close_reason: "superseded", state: "closed", read_only: true, can_write: false },
      };
    });
    renderPage("/jira-agent?conversation=jac_1");

    expect(await screen.findByText(/JIRA assignee 已变更/)).toBeInTheDocument();
    expect(screen.queryByLabelText("补充信息")).not.toBeInTheDocument();
  });
});
