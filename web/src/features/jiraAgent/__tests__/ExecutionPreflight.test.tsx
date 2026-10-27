import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { MachineChoice } from "../MachineChoice";
import { ExecutionPreflightDialog } from "../ExecutionPreflightDialog";
import { checkExecution, defaultExecution, getSshKeyInfo, listMachines, type ExecutionPreflight } from "../jiraAgentApi";

vi.mock("../jiraAgentApi", async importOriginal => ({ ...await importOriginal<typeof import("../jiraAgentApi")>(),
  checkExecution: vi.fn(), listMachines: vi.fn(), getSshKeyInfo: vi.fn(),
}));
vi.mock("../SshKeyUploadDialog", () => ({ SshKeyUploadDialog: ({ target, onVerified }: { target: string; onVerified: (id: string) => void }) =>
  <button onClick={() => onVerified(`key-${target}`)}>验证 {target}</button> }));

function wrap(element: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{element}</QueryClientProvider>);
}
function receipt(warning = true): ExecutionPreflight {
  return { id: "preflight", execution: defaultExecution(), expires_at: Date.now() / 1000 + 600, snapshot: {
    can_submit: true, requires_acknowledgement: warning, warning_ids: warning ? ["u@host:dataset"] : [], available_targets: ["u@host"],
    dataset_path: "/pde_hpc/dataset", checked_at: "now", results: [{ target: "u@host", checked_at: "now",
      ssh: { status: "pass", code: "connected", message: "连接通过" }, dataset: { status: warning ? "fail" : "pass", code: "read", message: warning ? "共享盘不可读" : "数据可读" } }],
  } };
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listMachines).mockResolvedValue({ groups: [], can_manage: false, machines: [{ id: "1", agent_group: "HPC", ssh_target: "u@host", description: "", created_by: "rm", created_at: "", updated_at: "" }] });
  vi.mocked(getSshKeyInfo).mockResolvedValue({ agent_group: "HPC", display_name: "HPC", fingerprint: "fp", comment: "", path: "/key" });
  vi.mocked(checkExecution).mockImplementation(async (_body, emit) => { emit({ type: "completed", result: receipt() }); });
});

it("adds and removes individual targets and validates their addresses", async () => {
  const onChange = vi.fn(); const user = userEvent.setup();
  wrap(<MachineChoice agentGroup="HPC" disabled={false} onChange={onChange} />);
  await user.click(screen.getByLabelText(/自填 user@host/));
  expect(screen.queryByLabelText("共享数据依赖")).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("自填机器", { exact: true }), "u@Host-a");
  await user.click(screen.getByRole("button", { name: "添加机器" }));
  await user.type(screen.getByLabelText("自填机器 2", { exact: true }), "u@host-b");
  await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ mode: "custom", selection_policy: "as_needed", dataset_requirement: "auto", targets: [{ target: "u@host-a", purpose: "" }, { target: "u@host-b", purpose: "" }] })));
  await user.type(screen.getByLabelText("机器类型 2"), "C600");
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ targets: [{ target: "u@host-a", purpose: "" }, { target: "u@host-b", purpose: "C600" }] }));
  await user.click(screen.getByLabelText("删除机器 1"));
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ targets: [{ target: "u@host-b", purpose: "C600" }] }));
  await user.type(screen.getByLabelText("自填机器", { exact: true }), "@unexpected");
  expect(onChange).toHaveBeenLastCalledWith(null);
  expect(screen.getByLabelText("自填机器", { exact: true })).toHaveValue("u@host-b@unexpected");
});

it("requires a deliberate warning decision and preserves submission identity on retry", async () => {
  const onReady = vi.fn().mockRejectedValueOnce(new Error("网络中断")).mockResolvedValue(undefined);
  const user = userEvent.setup();
  wrap(<ExecutionPreflightDialog agentGroup="HPC" execution={{ ...defaultExecution(), mode: "custom", targets: [{ target: "u@host", purpose: "C500" }] }} context={{ kind: "jira", issue_key: "MC3-1" }} onReady={onReady} onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getByText("输入密码并检查")).toBeEnabled());
  await user.click(screen.getByText("输入密码并检查"));
  await user.click(await screen.findByText("验证 u@host"));
  expect(await screen.findByText("共享盘不可读")).toBeInTheDocument();
  expect(onReady).not.toHaveBeenCalled();
  await user.click(screen.getByText("继续可执行工作"));
  expect(await screen.findByText("网络中断")).toBeInTheDocument();
  await user.click(screen.getByText("继续可执行工作"));
  expect(onReady).toHaveBeenCalledTimes(2);
  expect(onReady.mock.calls[0][0]).toEqual(onReady.mock.calls[1][0]);
  expect(onReady.mock.calls[0][0].acknowledged_issue_ids).toEqual(["u@host:dataset"]);
});

it("validates custom hosts in order before sending all key proofs", async () => {
  const onReady = vi.fn(); const user = userEvent.setup();
  const execution = { ...defaultExecution(), mode: "custom" as const, targets: [{ target: "u@a", purpose: "baseline" }, { target: "u@b", purpose: "reproduce" }] };
  wrap(<ExecutionPreflightDialog agentGroup="HPC" execution={execution} context={{ kind: "suite", case_ids: ["case"] }} onReady={onReady} onClose={vi.fn()} />);
  const start = screen.getByText("输入密码并检查"); await waitFor(() => expect(start).toBeEnabled()); await user.click(start);
  await user.click(await screen.findByText("验证 u@a"));
  expect(checkExecution).not.toHaveBeenCalled();
  await user.click(await screen.findByText("验证 u@b"));
  await waitFor(() => expect(checkExecution).toHaveBeenCalledWith(expect.objectContaining({ execution, key_sessions: { "u@a": "key-u@a", "u@b": "key-u@b" } }), expect.any(Function), expect.any(AbortSignal)));
  expect(onReady).not.toHaveBeenCalled();
});

it("offers no continue action when required SSH targets are unavailable", async () => {
  vi.mocked(checkExecution).mockImplementation(async (_body, emit) => { const value = receipt(); value.snapshot.can_submit = false; emit({ type: "completed", result: value }); });
  const onReady = vi.fn(); const user = userEvent.setup();
  wrap(<ExecutionPreflightDialog agentGroup="HPC" execution={defaultExecution()} context={{ kind: "jira", issue_key: "MC3-1" }} onReady={onReady} onClose={vi.fn()} />);
  await user.click(screen.getByText("开始检查"));
  expect(await screen.findByText(/必要机器的 SSH 检查未通过/)).toBeInTheDocument();
  expect(screen.queryByText("继续可执行工作")).not.toBeInTheDocument();
  expect(onReady).not.toHaveBeenCalled();
});

it("omits dataset controls and results for system machines", async () => {
  vi.mocked(checkExecution).mockImplementation(async (_body, emit) => {
    const value = receipt(false);
    value.snapshot.results[0].dataset = { status: "skipped", code: "system_machine", message: "系统机器不做共享数据预检查" };
    emit({ type: "completed", result: value });
  });
  const onReady = vi.fn(); const user = userEvent.setup();
  wrap(<ExecutionPreflightDialog agentGroup="HPC" execution={defaultExecution()} context={{ kind: "suite" }} onReady={onReady} onClose={vi.fn()} />);
  await user.click(screen.getByText("开始检查"));
  await waitFor(() => expect(onReady).toHaveBeenCalled());
  expect(screen.queryByRole("columnheader", { name: "共享数据" })).not.toBeInTheDocument();
  expect(screen.queryByText(/共享数据/)).not.toBeInTheDocument();
});
