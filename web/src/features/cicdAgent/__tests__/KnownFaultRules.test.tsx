import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, apiGet, apiPost } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";
import { KnownFaultDialog, KnownFaultRules } from "../KnownFaultRules";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn(), apiFetch: vi.fn() }));
vi.mock("../../../lib/confirm", () => ({ confirmDialog: vi.fn(), getActiveConfirm: vi.fn() }));
const config = { name: "通信库冲突", mode: "signature", enabled: true, starts_at: "2026-11-23T09:00:00", ends_at: "2026-11-26T09:00:00",
  stages: ["build image"], apps: [], jobs: [], chips: [], maca_versions: [], error_patterns: ["libibumad-dev*Depends:*libibumad3", "held broken packages"],
  ignore_versions: false, owner_role: "Code Owner", owner_account: "", summary: "通信库调整，暂挂 Owner", action: "联系通信库团队", root_team: "通信库", provisional: true };
const rule = { id: 1, revision: 3, config, state: "active", updated_by: "rm", updated_at: "2026-11-23T09:00:00" };
const preview = { preview_id: "a".repeat(32), rule_id: 1, revision: 3, rule_active: true, expires_at: "2026-11-23T09:15:00", items: [
  { id: 10, app: "APP", job: "Test", build: 1, before: { role: "DevOps", account: "m00930" }, after: { role: "Code Owner", account: "alice", summary: config.summary }, eligible: true, manual: false, evidence: { source: "saved_key_error", quotes: ["held broken packages"] } },
  { id: 11, app: "APP2", job: "Test2", build: 1, before: { role: "DevOps", account: "m00930" }, after: { role: "Code Owner", account: "bob" }, eligible: false, manual: true, skip_reason: "已人工处理" },
] };
function mount() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><KnownFaultRules /></QueryClientProvider>);
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(apiGet).mockResolvedValue({ items: [rule] });
  vi.mocked(apiFetch).mockResolvedValue(rule);
  vi.mocked(apiPost).mockResolvedValue(preview);
  vi.mocked(confirmDialog).mockResolvedValue(true);
});
describe("known fault rules", () => {
  it("keeps the close control outside scrolling content and closes on click", async () => {
    const onClose = vi.fn();
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <KnownFaultDialog records={[]} onClose={onClose} onApplied={vi.fn()} />
    </QueryClientProvider>);
    expect(screen.getAllByRole("heading", { name: "已知故障规则" })).toHaveLength(1);
    const close = screen.getByRole("button", { name: "关闭规则管理" });
    expect(close.closest(".known-fault-dialog-header")).not.toBeNull();
    expect(close.closest(".known-fault-dialog-body")).toBeNull();
    await userEvent.setup().click(close);
    expect(onClose).toHaveBeenCalledOnce();
  });
  it("edits optional version and comma-separated scope with revision", async () => {
    mount(); const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "编辑 通信库冲突" }));
    expect(screen.getByLabelText("基础镜像版本（可选）")).toHaveValue("");
    await user.type(screen.getByLabelText("Chip"), "n300, c500");
    await user.click(screen.getByRole("button", { name: "保存 v4" }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const options = vi.mocked(apiFetch).mock.calls[0][1];
    const body = JSON.parse(options?.body as string);
    expect(body.expected_revision).toBe(3);
    expect(body.config.chips).toEqual(["n300", "c500"]);
    expect(body.config.maca_versions).toEqual([]);
  });
  it("previews before applying and never selects protected records", async () => {
    mount(); const user=userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "历史匹配" }));
    await user.click(screen.getByRole("button", { name: "预览匹配" }));
    expect(await screen.findByLabelText("选择记录 10")).toBeChecked();
    expect(screen.getByLabelText("选择记录 11")).toBeDisabled();
    expect(vi.mocked(apiPost).mock.calls[0][0]).toBe("/api/cicd-agent/known-fault-rules/1/preview");
    vi.mocked(apiPost).mockResolvedValue({count:1});
    await user.click(screen.getByRole("button", { name: "确认应用 1 条" }));
    await waitFor(() => expect(apiPost).toHaveBeenLastCalledWith("/api/cicd-agent/known-fault-rules/1/apply", { preview_id: "a".repeat(32), record_ids:[10],confirm_manual:"" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已应用到 1 条记录");
  });
  it("changing preview scope invalidates the old selection", async () => {
    mount(); const user=userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "历史匹配" }));
    await user.click(screen.getByRole("button", { name: "预览匹配" }));
    await screen.findByLabelText("选择记录 10");
    await user.click(screen.getByLabelText("包含已人工处理"));
    expect(screen.queryByRole("button", { name: "确认应用 1 条" })).not.toBeInTheDocument();
  });
  it("disabled rules can be previewed but cannot be applied", async () => {
    vi.mocked(apiPost).mockResolvedValue({...preview,rule_active:false});
    mount(); const user=userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "历史匹配" }));
    await user.click(screen.getByRole("button", { name: "预览匹配" }));
    expect(await screen.findByRole("button", { name: "确认应用 1 条" })).toBeDisabled();
  });
  it("keeps edit values on optimistic conflict", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("规则已被修改"));
    mount(); const user=userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "编辑 通信库冲突" }));
    await user.click(screen.getByRole("button", { name: "保存 v4" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("规则已被修改");
    expect(screen.getByLabelText("规则名称")).toHaveValue(config.name);
  });
});
