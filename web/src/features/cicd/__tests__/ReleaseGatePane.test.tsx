import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, apiPost } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";
import { ReleaseGatePane } from "../ReleaseGatePane";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../../lib/confirm", () => ({ confirmDialog: vi.fn() }));
const version = { maca_version: "3.10.2.11", release_id: "r", release_name: "3.10.2", enabled: true,
  note: "release baseline", revision: 4, updated_at: "2026-11-17 10:00:00", updated_by: "rm", released_locked: false };
function mount(manage = true) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ReleaseGatePane canManage={manage} /></QueryClientProvider>);
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(apiGet).mockResolvedValue({ versions: [version], releases: [{ id: "r", name: "3.10.2", released_locked: false }] });
  vi.mocked(apiPost).mockResolvedValue({});
  vi.mocked(confirmDialog).mockResolvedValue(true);
});
describe("Release gate versions", () => {
  it("read-only users see no write controls", async () => {
    mount(false);
    expect(await screen.findByText("3.10.2.11")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "新增" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /编辑/ })).not.toBeInTheDocument();
  });
  it("RM adds an exact release version", async () => {
    mount(); const user = userEvent.setup();
    await screen.findByText("3.10.2.11");
    expect(screen.getByLabelText("MACA_VERSION")).toHaveAttribute("placeholder", "请输入基础镜像版本");
    await user.type(screen.getByLabelText("MACA_VERSION"), "3.10.2.12");
    await user.selectOptions(screen.getByLabelText("发布版本"), "r");
    await user.type(screen.getByLabelText("备注"), "QA approved");
    await user.click(screen.getByRole("button", { name: "新增" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/cicd/release-gate/versions", {
      maca_version: "3.10.2.12", release_id: "r", enabled: true, note: "QA approved", expected_revision: -1,
    }));
  });
  it("offers every unsealed release instead of only one version", async () => {
    vi.mocked(apiGet).mockResolvedValue({ versions: [], releases: [
      { id: "old", name: "3.10.1", released_locked: true },
      { id: "r", name: "3.10.2", released_locked: false },
      { id: "next", name: "3.10.3", released_locked: false },
    ] });
    mount();
    expect(await screen.findByRole("option", { name: "3.10.3" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "3.10.2" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "3.10.1" })).not.toBeInTheDocument();
  });
  it("toggle requires confirmation and carries revision", async () => {
    mount(); const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/cicd/release-gate/versions", {
      maca_version: version.maca_version, release_id: "r", enabled: false, note: version.note, expected_revision: 4,
    }));
    vi.mocked(apiPost).mockClear(); vi.mocked(confirmDialog).mockResolvedValue(false);
    await user.click(screen.getByRole("checkbox"));
    expect(apiPost).not.toHaveBeenCalled();
  });
  it("keeps edit values on a conflict and does not change identity", async () => {
    vi.mocked(apiPost).mockRejectedValue(new Error("准入配置已变化，请刷新后重试"));
    mount(); const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "编辑 3.10.2.11" }));
    expect(screen.getByLabelText("MACA_VERSION")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("准入配置已变化");
    expect(screen.getByLabelText("备注")).toHaveValue(version.note);
  });
});
