import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
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

const historicalTask: ReproTask = {
  ...task, node_name: null, report: {
    ...task.report!, outcome: "historical_log_confirmed", source_commit: "", environment_error: "",
    build_skipped_reason: "历史日志已明确显示依赖版本冲突",
    log_review: {
      decision: "log_sufficient", reason: "固定版本约束不兼容", matched_rule_id: "dependency-conflict", policy_version: "v1",
      evidence: [{ start_line: 42, end_line: 44, quote: "ERROR: ResolutionImpossible" }],
    },
    verification: {
      execution_mode: "historical_logs", build_skipped_reason: "日志证据充分", passed: false,
      summary: "基于 Jenkins 历史日志完成诊断", original_failure_reproduced: false, scope: "",
      targets: [], builds: [], installed_packages: [],
    },
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

  it("shows historical analysis before source preparation without waiting for a node", async () => {
    const analyzing = { ...task, status: "analyzing_history", node_name: null, report: null,
      history: [{ status: "analyzing_history", at: "2026-10-23T10:00:00", detail: "" }] };
    vi.mocked(apiGet).mockResolvedValue({ tasks: [analyzing], enabled: true });
    panel();
    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("历史日志分析中");
    expect(banner).toHaveTextContent("正在分析 Jenkins 原始日志");
    expect(screen.queryByText("等待节点分配")).not.toBeInTheDocument();
    expect(screen.getByText("历史日志分析中", { selector: "b.cicd-repro-outcome" }).querySelector(".lucide-file-text")).toBeInTheDocument();
    await userEvent.click(screen.getByText("执行记录", { selector: "summary" }));
    expect(screen.getByText("2026-10-23 10:00:00 · 历史日志分析中")).toBeVisible();

    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...analyzing, status: "preparing" }], enabled: true });
    await userEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("准备源码与证据"));
    expect(screen.queryByRole("button", { name: "带入责任处理" })).not.toBeInTheDocument();
  });

  it("shows a neutral historical diagnosis with skipped steps and cited original evidence", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [historicalTask], enabled: true });
    const useReport = panel();
    const outcome = await screen.findByText("历史日志诊断完成 · 未执行构建");
    expect(outcome).not.toHaveClass("passed");
    expect(outcome.querySelector(".lucide-file-text")).toBeInTheDocument();
    expect(outcome.querySelector(".lucide-circle-alert, .lucide-shield-check")).not.toBeInTheDocument();
    expect(screen.getByText("Code Owner / owner1")).toBeVisible();
    expect(screen.getByText("pip 冲突")).toBeVisible();
    expect(screen.getByText("调整约束")).toBeVisible();
    expect(screen.getByText("未执行构建：历史日志已明确显示依赖版本冲突")).toBeVisible();
    expect(screen.queryByText(/仅验证已列出的构建 target/)).not.toBeInTheDocument();
    const details = screen.getByText("历史日志与认定证据").closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByText("ERROR: ResolutionImpossible")).not.toBeVisible();
    await userEvent.click(screen.getByText("历史日志与认定证据"));
    expect(within(details).getByText("未分配（已跳过构建）")).toBeVisible();
    expect(within(details).getByText("未检查、未下载（已跳过源码准备）")).toBeVisible();
    expect(within(details).getByText("amd64")).toBeVisible();
    expect(within(details).getByRole("row", { name: "构建验证 未执行" })).toBeVisible();
    expect(within(details).getByRole("row", { name: "基础镜像核对 未执行" })).toBeVisible();
    expect(within(details).getByText("原始日志第 42-44 行")).toBeVisible();
    expect(within(details).getByText("ERROR: ResolutionImpossible")).toBeVisible();
    expect(within(details).getByText("固定版本约束不兼容")).toBeVisible();
    expect(within(details).getByText("dependency-conflict")).toBeVisible();
    expect(within(details).getByText("v1")).toBeVisible();
    expect(screen.queryByText("尚未验证与历史一致")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "带入责任处理" }));
    expect(useReport).toHaveBeenCalledWith(historicalTask);
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("opens original.log on demand even when build logs are also listed", async () => {
    vi.mocked(apiGet).mockImplementation(async (path) => {
      if (path.endsWith("/logs")) return { logs: [
        { name: "build-0-1.log", label: "未修改副本构建", bytes: 10 },
        { name: "original.log", label: "Jenkins 原始日志", bytes: 1234 },
      ] };
      if (path.includes("/logs/")) return { content: "original Jenkins output", start_line: 1, end_line: 44, total_lines: 44, previous_before: null };
      return { tasks: [historicalTask], enabled: true };
    });
    panel();
    await userEvent.click(await screen.findByText("历史日志与认定证据"));
    expect(apiGet).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "查看 Jenkins 原始日志" }));
    expect(await screen.findByText("original Jenkins output")).toBeVisible();
    expect(apiGet).toHaveBeenCalledWith("/api/cicd-agent/repro-tasks/abc/logs/original.log");
    expect(apiGet).not.toHaveBeenCalledWith("/api/cicd-agent/repro-tasks/abc/logs/build-0-1.log");
    expect(screen.getByRole("link", { name: "下载完整脱敏日志" })).toHaveAttribute("href", "/api/cicd-agent/repro-tasks/abc/logs/original.log/download");
  });

  it("shows original evidence when no cited lines are available", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...historicalTask,
      report: { ...historicalTask.report!, log_review: undefined },
    }], enabled: true });
    panel();
    await userEvent.click(await screen.findByText("历史日志与认定证据"));
    expect(screen.getByText("protobuf conflict")).toBeVisible();
    expect(screen.getByRole("button", { name: "查看 Jenkins 原始日志" })).toBeVisible();
  });

  it.each(["Code Owner", "Unresolved"])("does not prefill insufficient evidence for %s", async (suggested_role) => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...historicalTask, report: { ...historicalTask.report!,
      outcome: "evidence_insufficient", build_skipped_reason: "原始日志缺少失败上下文",
      log_review: { ...historicalTask.report!.log_review!, decision: "insufficient_evidence", evidence: [] },
      diagnosis: { ...historicalTask.report!.diagnosis!, suggested_role },
    } }], enabled: true });
    panel();
    expect(await screen.findByText("历史证据不足，待人工复核")).not.toHaveClass("passed");
    expect(screen.getByText("未执行构建：原始日志缺少失败上下文")).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "带入责任处理" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("历史日志与认定证据"));
    expect(screen.getByRole("row", { name: "构建验证 未执行" })).toBeVisible();
  });

  it.each(["historical_log_confirmed", "log_only", "environment_blocked"])("does not prefill Unresolved from %s", async (outcome) => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...task, report: { ...task.report!, outcome,
      diagnosis: { ...task.report!.diagnosis!, suggested_role: "Unresolved" },
    } }], enabled: true });
    panel();
    await screen.findByText("Unresolved / owner1");
    expect(screen.queryByRole("button", { name: "带入责任处理" })).not.toBeInTheDocument();
  });

  it.each([
    { stale: true, resolved_by: null, canResolve: true },
    { stale: false, resolved_by: "rm1", canResolve: true },
    { stale: false, resolved_by: null, canResolve: false },
  ])("retains historical report responsibility gates: %j", async ({ stale, resolved_by, canResolve }) => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...historicalTask, stale, resolved_by }], enabled: true });
    panel(canResolve);
    await screen.findByText("历史日志诊断完成 · 未执行构建");
    expect(screen.queryByRole("button", { name: "带入责任处理" })).not.toBeInTheDocument();
  });

  it("uses the verification skipped reason when the report has none", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...historicalTask,
      report: { ...historicalTask.report!, build_skipped_reason: undefined },
    }], enabled: true });
    panel();
    expect(await screen.findByText("未执行构建：日志证据充分")).toBeVisible();
  });

  it.each([
    ["historical_log_confirmed", "历史日志证据充分"],
    ["evidence_insufficient", "历史证据不足，需人工复核"],
  ])("keeps a long skipped reason collapsed for %s", async (outcome, compactReason) => {
    const reason = "本次复议仅分析已归档的 Jenkins 原始日志，不检查或下载源码，也不分配执行节点。".repeat(3);
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...historicalTask,
      report: { ...historicalTask.report!, outcome, build_skipped_reason: reason },
    }], enabled: true });
    panel();
    expect(await screen.findByText(`未执行构建：${compactReason}`)).toBeVisible();
    expect(screen.getByText(reason)).not.toBeVisible();
    expect(screen.getAllByText(reason)).toHaveLength(1);
    await userEvent.click(screen.getByText("历史日志与认定证据"));
    expect(screen.getByText("跳过构建原因")).toBeVisible();
    expect(screen.getByText(reason)).toBeVisible();
  });

  it("does not repeat the responsibility basis in the main skipped reason", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...historicalTask,
      report: { ...historicalTask.report!, build_skipped_reason: historicalTask.report!.diagnosis!.responsibility_reason },
    }], enabled: true });
    panel();
    expect(await screen.findByText("未执行构建：历史日志证据充分")).toBeVisible();
    expect(screen.queryByText("未执行构建：pip 冲突")).not.toBeInTheDocument();
  });

  it("handles absent optional fields, diagnosis and unknown source metadata", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...historicalTask, report: { ...historicalTask.report!,
      arch: "", log_review: undefined, build_skipped_reason: undefined, verification: undefined, diagnosis: null,
    } }], enabled: true });
    panel();
    expect(await screen.findByText("未执行构建：本次仅分析历史日志")).toBeVisible();
    await userEvent.click(screen.getByText("历史日志与认定证据"));
    expect(screen.getByText("未知")).toBeVisible();
    expect(screen.getByText("未检查、未下载（已跳过源码准备）")).toBeVisible();
    expect(screen.getByRole("row", { name: "基础镜像核对 未执行" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "带入责任处理" })).not.toBeInTheDocument();
  });

  it("never shows build verification success for historical execution", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...historicalTask, report: { ...historicalTask.report!,
      outcome: "fix_verified", verified: true, base_digests_match: true, source_commit: "known-revision",
      patches: [{ patch: "a candidate patch", verified: true }],
      verification: { ...historicalTask.report!.verification!, passed: true,
        builds: [{ attempt: 1, target: "dbg", exit_code: 0, log: "build-1.log" }] },
    } }], enabled: true });
    panel();
    expect(await screen.findByText("历史日志诊断完成 · 未执行构建")).not.toHaveClass("passed");
    expect(screen.queryByText("补丁构建验证通过")).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("历史日志与认定证据"));
    expect(screen.getByText("known-revision")).toBeVisible();
    expect(screen.getByText("未检查、未下载（已跳过源码准备）")).toBeVisible();
    expect(screen.queryByText(/digest 一致/)).not.toBeInTheDocument();
    expect(screen.queryByText("补丁副本重新构建")).not.toBeInTheDocument();
    expect(screen.queryByText("修复补丁 · 验证通过")).not.toBeInTheDocument();
    expect(screen.getByRole("row", { name: "构建验证 未执行" })).toBeVisible();
  });

  it.each([
    ["log_only", "仅日志分析"], ["not_reproduced", "未复现原错误"],
    ["reproduced", "已复现原错误"], ["different_failure", "出现不同错误，尚未复现"],
  ])("preserves the existing %s outcome label", async (outcome, label) => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...task, report: { ...task.report!, outcome } }], enabled: true });
    panel();
    expect(await screen.findByText(label)).toBeVisible();
  });

  it("preserves legacy log-only source and node facts without claiming verified success", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...task, report: { ...task.report!, outcome: "log_only", verified: true } }], enabled: true });
    panel();
    expect(await screen.findByText("仅日志分析")).not.toHaveClass("passed");
    await userEvent.click(screen.getByText("验证过程与认定证据"));
    expect(screen.getByText("local")).toBeVisible();
    expect(screen.getByText("a".repeat(40))).toBeVisible();
    expect(screen.queryByText(/已跳过源码准备/)).not.toBeInTheDocument();
  });

  it("shows a reproduce decision alongside actual build facts", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tasks: [{ ...task, report: { ...task.report!,
      outcome: "reproduced", environment_error: "", log_review: { ...historicalTask.report!.log_review!, decision: "reproduce" },
      verification: { ...historicalTask.report!.verification!, execution_mode: "build", original_failure_reproduced: true,
        builds: [{ attempt: 0, target: "dbg", exit_code: 1, log: "build-0-1.log" }] },
    } }], enabled: true });
    panel();
    await screen.findByText("已复现原错误");
    await userEvent.click(screen.getByText("验证过程与认定证据"));
    expect(screen.getByText("需要复现构建")).toBeVisible();
    expect(screen.getByText("dbg · 退出码 1")).toBeVisible();
    expect(screen.getByText("原始错误已重现")).toBeVisible();
    expect(screen.queryByText(/已跳过源码准备/)).not.toBeInTheDocument();
  });
});
