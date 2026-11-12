import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SignupDialog } from "../SignupDialog";
import { makeApp, makeEntry, makeRelease, makeState } from "../../../test/fixtures";
import type { App, ReleaseDetail } from "../../../types";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: { username: "alice", role: "Owner" } }) }));
vi.mock("../../../lib/toast", () => ({ toast: { success: vi.fn() } }));
import { apiGet, apiPost } from "../../../api/http";

const first = makeRelease([], { environment: [
  { component: "Driver", version: "driver-a", note: "driver note" },
  { component: "SDK", version: "sdk-a", note: "" },
  { component: "PyTorch", version: "torch-a", note: "" },
] });
const second = makeRelease([], { id: "rel-2", name: "2027.01", environment: [
  { component: "Driver", version: "driver-b", note: "" },
] });
const onClose = vi.fn();
const onSignedUp = vi.fn();

function setup(apps: App[], appId?: string, releases: ReleaseDetail[] = [first, second], loadRelease?: () => ReleaseDetail) {
  vi.mocked(apiGet).mockImplementation(async (url) => {
    const id = new URL(url, "http://test").searchParams.get("release_id");
    return makeState(apps, loadRelease ? loadRelease() : releases.find((release) => release.id === id) ?? null);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>
    <SignupDialog appId={appId} releases={releases} initialReleaseId={releases[0]?.id}
      onClose={onClose} onSignedUp={onSignedUp} />
  </QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiPost).mockResolvedValue({ entries: [] });
});

describe("signup dialogs", () => {
  it("shows version environment and only owned running Apps; disabled rows explain blockers", async () => {
    setup([
      makeApp("eligible"), makeApp("other", { owners: ["bob"] }), makeApp("stopped", { cicd_status: "stopped" }),
      makeApp("missing", { latest_app_info: null }),
      makeApp("stopping", { open_status_request: { id: 1, target_status: "stopped" } }), makeApp("signed"),
    ], undefined, [{ ...first, entries: [makeEntry("signed")] }]);
    const environment = await screen.findByTestId("signup-environment");
    for (const version of ["driver-a", "sdk-a", "torch-a"]) expect(environment).toHaveTextContent(version);
    expect(screen.queryByLabelText("报名 App other 1.0")).toBeNull();
    expect(screen.queryByLabelText("报名 App stopped 1.0")).toBeNull();
    for (const id of ["missing", "stopping", "signed"]) expect(screen.getByLabelText(`报名 App ${id} 1.0`)).toBeDisabled();
    expect(screen.getByTestId("signup-confirm")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("报名 App eligible 1.0"));
    fireEvent.click(screen.getByTestId("signup-confirm"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/entries/signup-batch", { release_id: "rel-1", app_ids: ["eligible"] }));
    expect(onSignedUp).toHaveBeenCalledWith("rel-1");
  });

  it("clears selection on version changes and never displays the old version environment while loading", async () => {
    setup([makeApp("a")]);
    fireEvent.click(await screen.findByLabelText("报名 App a 1.0"));
    expect(screen.getByTestId("signup-confirm")).not.toBeDisabled();
    fireEvent.change(screen.getByRole("combobox", { name: "报名版本" }), { target: { value: "rel-2" } });
    await waitFor(() => expect(screen.getByTestId("signup-environment")).toHaveTextContent("driver-b"));
    expect(screen.getByTestId("signup-environment")).not.toHaveTextContent("sdk-a");
    expect(screen.getByLabelText("报名 App a 1.0")).not.toBeChecked();
    expect(screen.getByTestId("signup-confirm")).toBeDisabled();
  });

  it("retains selection and shows the server's batch error without closing", async () => {
    setup([makeApp("a"), makeApp("b")]);
    vi.mocked(apiPost).mockRejectedValue(new Error("App b：CICD 已停止"));
    fireEvent.click(await screen.findByRole("button", { name: "全选可报名 App" }));
    fireEvent.click(screen.getByTestId("signup-confirm"));
    expect(await screen.findByRole("alert")).toHaveTextContent("App b：CICD 已停止");
    expect(screen.getByLabelText("报名 App a 1.0")).toBeChecked();
    expect(screen.getByLabelText("报名 App b 1.0")).toBeChecked();
    expect(onSignedUp).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("submits a single App with its app_info version", async () => {
    setup([makeApp("a")], "a");
    expect(await screen.findByTestId("signup-app-summary")).toHaveTextContent("App a 1.0");
    fireEvent.click(screen.getByTestId("signup-confirm"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/entries/signup", {
      release_id: "rel-1", app_id: "a", target_version: "1.0", base_image_id: null, planned_features: "", excluded_tests: [],
    }));
  });

  it("does not write when cancelled", async () => {
    setup([makeApp("a")]);
    fireEvent.click(await screen.findByLabelText("报名 App a 1.0"));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).toHaveBeenCalled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("disables confirmation when the version closed after the page was loaded", async () => {
    setup([makeApp("a")], "a", [first], () => ({ ...first, phase: "in_progress" }));
    expect(await screen.findByTestId("signup-app-summary")).toHaveTextContent("报名已截止");
    expect(screen.getByTestId("signup-confirm")).toBeDisabled();
  });

  it("keeps confirmation disabled on load failure and allows retry", async () => {
    let fail = true;
    setup([makeApp("a")], "a", [first], () => { if (fail) throw new Error("读取失败"); return first; });
    expect(await screen.findByRole("alert")).toHaveTextContent("读取失败");
    expect(screen.getByTestId("signup-confirm")).toBeDisabled();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByTestId("signup-app-summary")).toHaveTextContent("App a 1.0");
    expect(within(screen.getByRole("dialog")).getByTestId("signup-confirm")).not.toBeDisabled();
  });
});
