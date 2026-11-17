import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, apiPost } from "../../../api/http";
import { DingTalkNotificationSetting } from "../DingTalkNotificationSetting";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
const endpoint = "/api/cicd-agent/failure-notifications/dingtalk";
const binding = { account: "alice", bound: false, enabled: false, available: true, worker_online: true, group_name: "test" };
function renderSetting() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><DingTalkNotificationSetting username="alice" /></QueryClientProvider>);
  return client;
}

describe("DingTalk binding", () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(apiGet).mockResolvedValue(binding); });
  it("generates a code only on demand and never supplies an account or recipient", async () => {
    vi.mocked(apiPost).mockResolvedValue({ command: "绑定提醒 abc", expires_at: new Date(Date.now() + 600000).toISOString(), group_name: "test" });
    renderSetting();
    await userEvent.click(screen.getByRole("button", { name: /钉钉提醒/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成绑定码" })).toBeEnabled());
    expect(apiPost).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "生成绑定码" }));
    expect(await screen.findByLabelText("一次性绑定命令")).toHaveValue("绑定提醒 abc");
    expect(apiPost).toHaveBeenCalledWith(`${endpoint}/code`, {});
    await userEvent.click(screen.getByRole("button", { name: "关闭钉钉设置" }));
    await userEvent.click(screen.getByRole("button", { name: /钉钉提醒/ }));
    expect(screen.queryByLabelText("一次性绑定命令")).not.toBeInTheDocument();
  });
  it("shows a successful binding without an extra confirmation and supports pause / confirmed unbind", async () => {
    const client = renderSetting();
    await userEvent.click(screen.getByRole("button", { name: /钉钉提醒/ }));
    client.setQueryData(["dingtalk-binding", "alice"], { ...binding, bound: true, enabled: true, nickname: "Alice" });
    expect(await screen.findByText("提醒已开启")).toBeInTheDocument();
    vi.mocked(apiPost).mockResolvedValue({ ...binding, bound: true, enabled: false });
    await userEvent.click(screen.getByRole("button", { name: "暂停提醒" }));
    expect(await screen.findByText("提醒已暂停")).toBeInTheDocument();
    expect(apiPost).toHaveBeenLastCalledWith(endpoint, { action: "disable" });
    await userEvent.click(screen.getByRole("button", { name: "解除绑定" }));
    expect(apiPost).toHaveBeenCalledTimes(1);
    vi.mocked(apiPost).mockResolvedValue(binding);
    await userEvent.click(screen.getByRole("button", { name: "确认解绑" }));
    await waitFor(() => expect(apiPost).toHaveBeenLastCalledWith(endpoint, { action: "unbind" }));
  });
  it("disables binding while receiver is offline and reports failures", async () => {
    vi.mocked(apiGet).mockResolvedValue({ ...binding, worker_online: false });
    renderSetting();
    await userEvent.click(screen.getByRole("button", { name: /钉钉提醒/ }));
    expect(await screen.findByText("接收服务未连接，暂时无法完成新绑定。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成绑定码" })).toBeDisabled();
  });
});
