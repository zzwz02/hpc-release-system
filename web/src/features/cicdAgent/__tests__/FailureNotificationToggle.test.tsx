import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, apiPost } from "../../../api/http";
import { FailureNotificationToggle } from "../FailureNotificationToggle";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
const endpoint = "/api/cicd-agent/failure-notifications/preferences";
const preference = { account: "alice", enabled: false, send_time: "09:45", timezone: "Asia/Shanghai" };

function renderToggle() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><FailureNotificationToggle username="alice" /></QueryClientProvider>);
}

describe("FailureNotificationToggle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGet).mockResolvedValue(preference);
    vi.mocked(apiPost).mockImplementation(async (_path, body) => ({ ...preference, ...(body as object) }));
  });

  it("preserves an explicit opt-out without writing or sending anything", async () => {
    renderToggle();
    const toggle = screen.getByRole("checkbox", { name: /每日邮件提醒/ });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).not.toBeChecked();
    expect(apiGet).toHaveBeenCalledWith(endpoint);
    expect(apiPost).not.toHaveBeenCalled();
    expect(toggle.closest("label")).toHaveAttribute("title", expect.stringContaining("09:45"));
    expect(toggle.closest("label")).toHaveAttribute("title", expect.stringContaining("周一至周五"));
    expect(toggle.closest("label")).toHaveAttribute("title", expect.stringContaining("周一汇总周五至周日"));
  });

  it("saves enabled and disabled preferences without accepting an account or recipient", async () => {
    renderToggle();
    const toggle = screen.getByRole("checkbox", { name: /每日邮件提醒/ });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);
    await waitFor(() => expect(toggle).toBeChecked());
    expect(apiPost).toHaveBeenLastCalledWith(endpoint, { enabled: true });
    await userEvent.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(apiPost).toHaveBeenLastCalledWith(endpoint, { enabled: false });
  });

  it("displays default-on for a user with no saved preference without creating one", async () => {
    vi.mocked(apiGet).mockResolvedValue({ ...preference, enabled: true, updated_at: null });
    renderToggle();
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /每日邮件提醒/ })).toBeChecked());
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("shows the requested state while saving and disables repeated clicks", async () => {
    let complete!: (value: typeof preference) => void;
    vi.mocked(apiPost).mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
    renderToggle();
    const toggle = screen.getByRole("checkbox", { name: /每日邮件提醒/ });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    complete({ ...preference, enabled: true });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).toBeChecked();
    expect(apiPost).toHaveBeenCalledTimes(1);
  });

  it("keeps the saved value on write failure and allows retry", async () => {
    vi.mocked(apiPost).mockRejectedValueOnce(new Error("gateway unavailable"));
    renderToggle();
    const toggle = screen.getByRole("checkbox", { name: /每日邮件提醒/ });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);
    expect(await screen.findByRole("alert")).toHaveTextContent("提醒设置保存失败");
    expect(toggle).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "重试提醒设置" }));
    await waitFor(() => expect(toggle).toBeChecked());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("disables the toggle when loading fails and can reload the preference", async () => {
    vi.mocked(apiGet).mockRejectedValueOnce(new Error("unavailable"));
    renderToggle();
    expect(await screen.findByRole("alert")).toHaveTextContent("提醒设置加载失败");
    const toggle = screen.getByRole("checkbox", { name: /每日邮件提醒/ });
    expect(toggle).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "重试提醒设置" }));
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(apiPost).not.toHaveBeenCalled();
  });
});
