import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, apiPost } from "../../../api/http";
import { FailureReproPanel, type ReproTask } from "../FailureReproPanel";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: { username: "owner1", role: "RM" } }) }));

const task: ReproTask = {
  id: "abc", failure_id: 2188, status: "completed", reason: "验证依赖", requested_by: "owner1",
  node_name: "local", created_at: "2026-10-16T09:00:00", error: null, stale: false, resolved_by: null,
  cancel_requested: false, history: [], report: {
    outcome: "environment_blocked", source_commit: "a".repeat(40), arch: "amd64", verified: false,
    base_digests_match: false, environment_error: "镜像不存在", suggested_owner_account: "owner1", builds: [], patches: [],
    diagnosis: { summary: "原始日志显示依赖版本冲突", suggested_role: "Code Owner", responsibility_reason: "pip 冲突",
      action_suggestion: "调整约束", original_evidence: "protobuf conflict", reproduction_evidence: "", limitations: ["未实际复现"] },
  },
};

function panel(canResolve = true) {
  const onUseReport = vi.fn();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <FailureReproPanel recordId={2188} canResolve={canResolve} onUseReport={onUseReport} />
  </QueryClientProvider>);
  return onUseReport;
}

describe("FailureReproPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGet).mockResolvedValue({ tasks: [], enabled: true });
  });

  it("submits only a reason, without client controlled identity", async () => {
    vi.mocked(apiPost).mockResolvedValue({ ...task, status: "queued" });
    panel();
    await userEvent.type(await screen.findByLabelText("复议原因"), "检查依赖冲突");
    await userEvent.click(screen.getByRole("button", { name: "提交复议" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/cicd-agent/failures/2188/repro-tasks", { reason: "检查依赖冲突" }));
  });

  it("distinguishes environment blocking and only prefills RM resolution", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [task], enabled: true });
    const useReport = panel();
    expect(await screen.findByText("复现环境阻塞")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "带入责任处理" }));
    expect(useReport).toHaveBeenCalledWith(task);
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("prevents using stale reports", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...task, stale: true }], enabled: true });
    panel();
    expect(await screen.findByText("责任认定已变化，此报告不能覆盖新的认定。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "带入责任处理" })).not.toBeInTheDocument();
  });

  it("does not offer RM actions to other roles", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [task], enabled: true });
    panel(false);
    await screen.findByText("复现环境阻塞");
    expect(screen.queryByRole("button", { name: "带入责任处理" })).not.toBeInTheDocument();
  });

  it("shows archived evidence without adding another details panel", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [], enabled: true, evidence_archive: { status: "complete" } });
    panel();
    expect(await screen.findByText("Jenkins 复现证据已归档")).toBeInTheDocument();
  });

  it("distinguishes expired logs from missing task output", async () => {
    vi.mocked(apiGet).mockImplementation(async (path) => path.endsWith("/logs")
      ? { logs: [], retention: { expired_at: "2026-10-21" } }
      : { tasks: [task], enabled: true, evidence_archive: { status: "expired" } });
    panel();
    expect(await screen.findByText(/Jenkins 原始日志已按保留策略清理/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("过程日志", { selector: "summary" }));
    expect(await screen.findByText("过程日志已按保留策略清理，最终报告与补丁仍保留。")).toBeInTheDocument();
  });

  it("loads logs only on expansion, supports older pages and download", async () => {
    vi.mocked(apiGet).mockImplementation(async (path) => {
      if (path.endsWith("/logs")) return { logs: [{ name: "build-0-1.log", label: "未修改副本构建", bytes: 9000 }] };
      if (path.includes("/logs/")) return { content: path.includes("before=") ? "earlier evidence" : "latest evidence", start_line: 301, end_line: 600, total_lines: 600, previous_before: path.includes("before=") ? null : 300 };
      return { tasks: [task], enabled: true };
    });
    panel();
    await screen.findByText("复现环境阻塞");
    expect(apiGet).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByText("过程日志", { selector: "summary" }));
    expect(await screen.findByText("latest evidence")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载完整脱敏日志" })).toHaveAttribute("href", "/api/cicd-agent/repro-tasks/abc/logs/build-0-1.log/download");
    await userEvent.click(screen.getByRole("button", { name: "更早日志" }));
    expect(await screen.findByText("earlier evidence")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "最新日志" }));
    expect(await screen.findByText("latest evidence")).toBeInTheDocument();
  });

  it("shows executor facts and keeps details collapsed by default", async () => {
    const verified: ReproTask = { ...task, report: { ...task.report!, outcome: "fix_verified", verified: true,
      base_digests_match: true, environment_error: "", diagnosis: { ...task.report!.diagnosis!, summary: "补丁已重新构建通过", limitations: ["未执行运行测试"] },
      verification: { passed: true, summary: "原始错误已重现，补丁构建通过", original_failure_reproduced: true,
        scope: "仅验证 dbg 构建", targets: ["dbg"], installed_packages: [{ name: "grpcio-tools", version: "1.48.2" }],
        builds: [{ attempt: 0, target: "dbg", exit_code: 1, log: "build-0-1.log" },
          { attempt: 3, target: "dbg", exit_code: 0, log: "build-3-1.log", method: "docker buildx build", no_cache: true, pull: true }] } } };
    vi.mocked(apiGet).mockResolvedValue({ tasks: [verified], enabled: true });
    panel();
    expect(await screen.findByText("补丁构建验证通过")).toBeInTheDocument();
    expect(screen.getByText("验证过程与认定证据").closest("details")).not.toHaveAttribute("open");
    await userEvent.click(screen.getByText("验证过程与认定证据"));
    expect(screen.getByText("docker buildx build --no-cache --pull")).toBeVisible();
    expect(screen.getByText("dbg · 退出码 0")).toBeVisible();
    expect(screen.getByText("仅验证 dbg 构建")).toBeVisible();
  });
});
