import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, apiGet, apiPost } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";
import { AccountMenu } from "../AccountMenu";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn(), apiFetch: vi.fn() }));
vi.mock("../../../lib/confirm", () => ({ confirmDialog: vi.fn() }));
const token = { id: 1, name: "Jenkins", token_hint: "rsapi_...abcdef", created_at: "2026-11-18 09:00:00",
  last_used_at: "", last_operation: "", revoked_at: "" };
function mount(username = "hajiang") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return { ...render(<QueryClientProvider client={client}><AccountMenu user={{ username, role: "Owner", display_name: "" }} logout={vi.fn()} /></QueryClientProvider>), client };
}
async function openTokens() {
  await userEvent.click(screen.getByRole("button", { name: "账号设置" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "API Token" }));
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(apiGet).mockResolvedValue({ tokens: [], ci_allowed: true });
  vi.mocked(confirmDialog).mockResolvedValue(true);
});

describe("Account API tokens", () => {
  it("loads only when opened and does not request MCP or Jira", async () => {
    mount();
    expect(apiGet).not.toHaveBeenCalled();
    await openTokens();
    expect(await screen.findByText("暂无 Token")).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet).toHaveBeenCalledWith("/api/account/tokens");
    expect(screen.getByText("hajiang")).toBeInTheDocument();
  });
  it("shows the generated secret once without putting it in caches", async () => {
    const { client } = mount();
    vi.mocked(apiPost).mockResolvedValue({ token: { ...token, token: "rsapi_one-time-secret" } });
    const user = userEvent.setup();
    await openTokens();
    await user.click(await screen.findByRole("button", { name: "生成 Token" }));
    expect(await screen.findByLabelText("完整 API Token")).toHaveValue("rsapi_one-time-secret");
    expect(apiPost).toHaveBeenCalledWith("/api/account/tokens", { name: "Jenkins" });
    expect(JSON.stringify(client.getQueryData(["account-api-tokens", "hajiang"]))).not.toContain("one-time-secret");
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "已保存" }));
    expect(screen.queryByLabelText("完整 API Token")).not.toBeInTheDocument();
  });
  it("confirms closing an unsaved secret and forgets it after closing", async () => {
    mount(); const user = userEvent.setup();
    vi.mocked(apiPost).mockResolvedValue({ token: { ...token, token: "rsapi_one-time-secret" } });
    await openTokens();
    await user.click(await screen.findByRole("button", { name: "生成 Token" }));
    await screen.findByLabelText("完整 API Token");
    vi.mocked(confirmDialog).mockResolvedValue(false);
    await user.click(screen.getByRole("button", { name: "关闭 Token 管理" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    vi.mocked(confirmDialog).mockResolvedValue(true);
    await user.click(screen.getByRole("button", { name: "关闭 Token 管理" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await openTokens();
    expect(screen.queryByLabelText("完整 API Token")).not.toBeInTheDocument();
  });
  it("revocation requires confirmation and refreshes metadata", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tokens: [token], ci_allowed: true });
    vi.mocked(apiPost).mockResolvedValue({ token: { ...token, revoked_at: "now" } });
    mount(); const user = userEvent.setup();
    await openTokens();
    vi.mocked(confirmDialog).mockResolvedValue(false);
    await user.click(await screen.findByRole("button", { name: "撤销 Jenkins" }));
    expect(apiPost).not.toHaveBeenCalled();
    vi.mocked(confirmDialog).mockResolvedValue(true);
    await user.click(screen.getByRole("button", { name: "撤销 Jenkins" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/account/tokens/1/revoke", {}));
    expect(screen.getByText("rsapi_...abcdef")).toBeInTheDocument();
  });
  it("shows errors and role status without granting new capabilities", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tokens: [], ci_allowed: false });
    vi.mocked(apiPost).mockRejectedValue(new Error("生成失败，请重试"));
    mount("guest"); const user = userEvent.setup();
    await openTokens();
    expect(await screen.findByText("当前账号角色未开放 CI 自动化接口。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "生成 Token" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("生成失败，请重试");
  });
  it("only offers deletion for revoked tokens and keeps it on cancellation or failure", async () => {
    vi.mocked(apiGet).mockResolvedValue({ tokens: [token, { ...token, id: 2, name: "Old", revoked_at: "now" }], ci_allowed: true });
    mount(); const user = userEvent.setup();
    await openTokens();
    expect(await screen.findByRole("button", { name: "删除 Old" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除 Jenkins" })).not.toBeInTheDocument();
    vi.mocked(confirmDialog).mockResolvedValue(false);
    await user.click(screen.getByRole("button", { name: "删除 Old" }));
    expect(apiFetch).not.toHaveBeenCalled();
    vi.mocked(confirmDialog).mockResolvedValue(true);
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("删除失败"));
    await user.click(screen.getByRole("button", { name: "删除 Old" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("删除失败");
    expect(screen.getByRole("button", { name: "删除 Old" })).toBeInTheDocument();
    vi.mocked(apiFetch).mockResolvedValue({ ok: true });
    vi.mocked(apiGet).mockResolvedValue({ tokens: [token], ci_allowed: true });
    await user.click(screen.getByRole("button", { name: "删除 Old" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "删除 Old" })).not.toBeInTheDocument());
    expect(apiFetch).toHaveBeenLastCalledWith("/api/account/tokens/2", { method: "DELETE" });
  });
});
