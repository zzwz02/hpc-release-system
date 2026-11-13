/**
 * App details, basic information, sync feedback, CICD and new-App requests.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AppWorkbenchPage } from "../AppWorkbenchPage";
import { makeApp, makeAppInfo, makeEntry, makeRelease, makeState } from "../../../test/fixtures";
import type { StatePayload } from "../../../types";

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));
vi.mock("../../../api/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("../../../lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("../../../lib/confirm", () => ({ confirmDialog: vi.fn(), promptDialog: vi.fn() }));

import { apiGet, apiPost } from "../../../api/http";
import { useAuth } from "../../../api/AuthContext";
import { confirmDialog } from "../../../lib/confirm";
import { useUiStore } from "../../../store/uiStore";
import { toast } from "../../../lib/toast";
import { appTypeOptionsForTarget } from "../../../lib/domainMetadata";

function setUser(username: string, role: string) {
  vi.mocked(useAuth).mockReturnValue({
    user: { username, role, display_name: username },
    ldapStatus: { enabled: false, uri: "" },
    login: vi.fn(),
    logout: vi.fn(),
    clearUser: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);
}

function mockApi(state: StatePayload) {
  vi.mocked(apiGet).mockImplementation(async (url: string) => {
    if (url.startsWith("/api/state")) return state;
    if (url.startsWith("/api/cicd/requests")) return { requests: [] };
    if (url.startsWith("/api/cicd/deliveries")) return { deliveries: [] };
    if (url.startsWith("/api/apps/detail")) return { app_info_versions: [], official_versions: [] };
    if (url.startsWith("/api/app-audit")) return { entries: [] };
    throw new Error(`unexpected GET ${url}`);
  });
  vi.mocked(apiPost).mockResolvedValue({});
}

function renderPage(selectedApp = "") {
  useUiStore.setState({ selectedReleaseId: "", selectedApp, appDetailDirty: false });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><AppWorkbenchPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(confirmDialog).mockResolvedValue(true);
  setUser("alice", "Owner");
});

describe("App list", () => {
  it("shows signup and CICD state and filters to the Owner's Apps", async () => {
    const apps = [
      makeApp("a", { name: "Alpha 1.0" }),
      makeApp("b", { name: "Beta 1.0", cicd_status: "stopped" }),
      makeApp("c", { name: "Gamma 1.0", owners: ["bob"] }),
    ];
    mockApi(makeState(apps, makeRelease([makeEntry("a")])));
    renderPage();
    const rowA = await screen.findByTestId("app-row-a");
    expect(within(rowA).getByText("已报名")).toBeInTheDocument();
    expect(within(screen.getByTestId("app-row-b")).getByText("CICD 已停止")).toBeInTheDocument();
    expect(screen.queryByTestId("app-row-c")).toBeNull();
    fireEvent.click(screen.getByTestId("own-only-checkbox"));
    expect(await screen.findByTestId("app-row-c")).toBeInTheDocument();
  });
});

describe("basic information and app_info", () => {
  it("withdraws an active signup only after confirmation and refreshes its state", async () => {
    const state = makeState([makeApp("a")], makeRelease([makeEntry("a")]));
    mockApi(state);
    renderPage("a");
    const button = await screen.findByTestId("app-withdraw-btn");
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    fireEvent.click(button);
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(apiPost).not.toHaveBeenCalled();
    vi.mocked(apiPost).mockImplementation(async () => {
      state.release!.entries[0] = { ...state.release!.entries[0], status: "withdrawn" };
      return {};
    });
    fireEvent.click(button);
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/entries/withdraw", { entry_id: 1 }));
    await waitFor(() => expect(screen.queryByTestId("app-withdraw-btn")).toBeNull());
  });

  it("does not offer withdrawal for a published cycle", async () => {
    mockApi(makeState([makeApp("a")], makeRelease([makeEntry("a")], { released_locked: true, phase: "released" })));
    renderPage("a");
    await screen.findByTestId("app-detail-header");
    expect(screen.queryByTestId("app-withdraw-btn")).toBeNull();
  });

  it("lists one final publication per cycle including entry-only history", async () => {
    mockApi(makeState([makeApp("a")], null));
    const baseGet = vi.mocked(apiGet).getMockImplementation()!;
    let records = [
      { id: 8, entry_id: 3, release_name: "3.10.0", version: "1.0", image: "hpc/a:latest", digest: "sha256:latest", released_at: "2026-11-12 10:00:00" },
      { id: null, entry_id: 2, release_name: "3.9.0", version: "1.0", image: "hpc/a:3.9", digest: "sha256:39", released_at: "2026-09-24 10:00:00" },
      { id: null, entry_id: 1, release_name: "3.8.0", version: "0.9", image: "", digest: "", released_at: "2026-08-11 10:00:00" },
    ];
    vi.mocked(apiGet).mockImplementation(async (url) => url.startsWith("/api/apps/detail")
      ? { app_info_versions: [], official_versions: records } : baseGet(url));
    renderPage("a");
    const table = await screen.findByTestId("official-version-table");
    const rows = within(table).getAllByTestId("official-version-row");
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => within(row).getByTestId("official-release-name").textContent)).toEqual(["3.10.0", "3.9.0", "3.8.0"]);
    expect(rows[1]).toHaveTextContent("sha256:39");
    // Manual refresh updates history even when latest app_info did not change.
    records = records.slice(1);
    fireEvent.click(screen.getByTestId("refresh-btn"));
    await waitFor(() => expect(within(table).getAllByTestId("official-version-row")).toHaveLength(2));
  });

  it("opens basic information first and hides the signup tab", async () => {
    mockApi(makeState([makeApp("a")], null));
    renderPage("a");
    const pane = await screen.findByTestId("app-info-pane");
    expect(pane.firstElementChild).toBe(screen.getByTestId("basic-info-section"));
    expect(screen.getByTestId("detail-tab-app_info")).toHaveTextContent("基本信息和app_info");
    expect(screen.queryByTestId("detail-tab-signup")).toBeNull();
    expect(screen.queryByTestId("signup-pane")).toBeNull();
    for (const label of ["官方名称", "Owner", "类型", "App 类型"]) {
      expect(within(screen.getByTestId("basic-info-section")).getByLabelText(label)).toBeDisabled();
    }
    fireEvent.click(screen.getByTestId("detail-tab-profile"));
    expect(screen.queryByLabelText("官方名称")).toBeNull();
    expect(screen.getByLabelText("官方 URL")).toBeInTheDocument();
  });

  it("lets RM save the four fields without submitting manual content", async () => {
    setUser("rm", "RM");
    mockApi(makeState([makeApp("a")], makeRelease([])));
    renderPage("a");
    fireEvent.click(await screen.findByRole("button", { name: "✎ 修改" }));
    fireEvent.change(screen.getByLabelText("官方名称"), { target: { value: "New name" } });
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "alice, bob" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/apps/profile", {
      app_id: "a", fields: { official_name: "New name", owners: "alice, bob", doc_target: "manual", type: "性能基准测试" },
    }));
  });

  it("lets an Owner edit types, keeps RM fields disabled and retains a rejected save", async () => {
    mockApi(makeState([makeApp("a")], makeRelease([])));
    vi.mocked(apiPost).mockRejectedValue(new Error("保存冲突，请刷新后重试"));
    renderPage("a");
    fireEvent.click(await screen.findByRole("button", { name: "✎ 修改" }));
    expect(screen.getByLabelText("官方名称")).toBeDisabled();
    expect(screen.getByLabelText("Owner")).toBeDisabled();
    const nextType = appTypeOptionsForTarget("manual").find((type) => type !== "性能基准测试")!;
    fireEvent.change(screen.getByLabelText("App 类型"), { target: { value: nextType } });
    expect(screen.getByTestId("app-info-sync")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存冲突");
    expect(screen.getByLabelText("App 类型")).toHaveValue(nextType);
    expect(apiPost).toHaveBeenCalledWith("/api/apps/profile", {
      app_id: "a", fields: { doc_target: "manual", type: nextType },
    });
    vi.mocked(confirmDialog).mockResolvedValue(false);
    fireEvent.click(screen.getByTestId("detail-tab-profile"));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(screen.queryByTestId("profile-pane")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByLabelText("App 类型")).toHaveValue("性能基准测试");
    expect(screen.getByTestId("app-info-sync")).not.toBeDisabled();
  });

  it("keeps basic information read-only for a different Owner", async () => {
    mockApi(makeState([makeApp("a", { owners: ["bob"], allowed_actions: [] })], makeRelease([])));
    renderPage("a");
    await screen.findByTestId("basic-info-section");
    expect(screen.queryByRole("button", { name: "✎ 修改" })).toBeNull();
    expect(screen.queryByTestId("app-info-sync")).toBeNull();
  });

  it("discards the draft after a confirmed refresh even when server data is unchanged", async () => {
    setUser("rm", "RM");
    mockApi(makeState([makeApp("a")], makeRelease([])));
    renderPage("a");
    fireEvent.click(await screen.findByRole("button", { name: "✎ 修改" }));
    fireEvent.change(screen.getByLabelText("官方名称"), { target: { value: "Draft name" } });
    fireEvent.click(screen.getByTestId("refresh-btn"));
    await waitFor(() => expect(screen.getByLabelText("官方名称")).toHaveValue("App a"));
    expect(screen.getByLabelText("官方名称")).toBeDisabled();
    expect(useUiStore.getState().appDetailDirty).toBe(false);
  });

  it("syncs beside the latest app_info heading and refreshes the scope warning without collapsing it", async () => {
    const state = makeState([makeApp("a")], makeRelease([makeEntry("a")], { phase: "in_progress" }));
    mockApi(state);
    vi.mocked(apiPost).mockImplementation(async () => {
      state.apps[0] = { ...state.apps[0], latest_app_info: makeAppInfo({ id: 2 }) };
      state.release!.entries[0] = makeEntry("a", { behind_latest: true, pending_scope_request: {
        id: 5, entry_id: 1, base_version_id: 1, target_version_id: 2, additions: ["新增芯片 C600"],
        status: "pending", created_at: "", decided_by: "", decided_at: "", decision_note: "",
      } });
      return { status: "updated" };
    });
    renderPage("a");
    const header = await screen.findByTestId("app-detail-header");
    expect(within(header).queryByTestId("app-info-sync")).toBeNull();
    const section = screen.getByTestId("latest-app-info-section");
    expect(within(section.querySelector("summary")!).getByRole("button", { name: "立即同步app_info" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("detail-tab-profile"));
    expect(screen.queryByTestId("app-info-sync")).toBeNull();
    fireEvent.click(screen.getByTestId("detail-tab-app_info"));
    fireEvent.click(screen.getByRole("button", { name: "立即同步app_info" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/app-info/sync", { app_id: "a", force: true }));
    expect(await screen.findByTestId("app-scope-warning")).toHaveTextContent("新增芯片 C600");
    expect(screen.getByTestId("app-scope-warning")).toHaveTextContent("最新 app_info 已同步");
    expect(screen.getByTestId("latest-app-info-section")).toHaveAttribute("open");
    expect(toast.success).toHaveBeenCalledWith("已同步到新的 app_info");
  });

  it("keeps synchronization available before the first app_info is fetched", async () => {
    mockApi(makeState([makeApp("a", { latest_app_info: null })], null));
    renderPage("a");
    const section = await screen.findByTestId("latest-app-info-section");
    expect(within(section).getByText("尚未取得 app_info。")).toBeInTheDocument();
    fireEvent.click(within(section).getByRole("button", { name: "立即同步app_info" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/app-info/sync", { app_id: "a", force: true }));
  });

  it("shows a persistent sync failure and a scope warning after QA rejects the expansion", async () => {
    mockApi(makeState([makeApp("a", { sync_error: "Gerrit 不可达" })],
      makeRelease([makeEntry("a", { behind_latest: true })], { phase: "in_progress" })));
    vi.mocked(apiPost).mockResolvedValue({ status: "error", error: "Gerrit 不可达" });
    renderPage("a");
    expect(await screen.findByTestId("app-info-sync-error")).toHaveTextContent("Gerrit 不可达");
    expect(screen.getByTestId("app-scope-warning")).toHaveTextContent("未获 QA 同意");
    fireEvent.click(screen.getByTestId("app-info-sync"));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("同步失败：Gerrit 不可达"));
    fireEvent.click(screen.getByTestId("detail-tab-cicd"));
    expect(screen.getByTestId("app-info-sync-error")).toHaveTextContent("Gerrit 不可达");
    expect(screen.getByTestId("app-scope-warning")).toBeInTheDocument();
  });

  it("refreshes the stored sync error when the HTTP wrapper throws for an error envelope", async () => {
    const state = makeState([makeApp("a")], makeRelease([]));
    mockApi(state);
    vi.mocked(apiPost).mockImplementation(async () => {
      state.apps[0] = { ...state.apps[0], sync_error: "Gerrit 不可达" };
      throw new Error("Gerrit 不可达");
    });
    renderPage("a");
    fireEvent.click(await screen.findByTestId("app-info-sync"));
    expect(await screen.findByTestId("app-info-sync-error")).toHaveTextContent("Gerrit 不可达");
    expect(toast.error).toHaveBeenCalledWith("同步失败：Gerrit 不可达");
  });

  it("does not report historical version differences as a pending scope expansion", async () => {
    mockApi(makeState([makeApp("a")], makeRelease([makeEntry("a", { behind_latest: true })], { phase: "released" })));
    renderPage("a");
    await screen.findByTestId("app-info-pane");
    expect(screen.queryByTestId("app-scope-warning")).toBeNull();
  });
});

describe("CICD tab", () => {
  it("disables stopping while the App is signed up", async () => {
    mockApi(makeState([makeApp("a", { open_signups: ["2026.12"] })], makeRelease([makeEntry("a")])));
    renderPage("a");
    fireEvent.click(await screen.findByTestId("detail-tab-cicd"));
    const stop = await screen.findByTestId("cicd-stop-request");
    expect(stop).toBeDisabled();
    expect(stop).toHaveAttribute("title", expect.stringContaining("撤回报名"));
  });

  it("submits a start request for a stopped App", async () => {
    mockApi(makeState([makeApp("a", { cicd_status: "stopped" })], makeRelease([])));
    renderPage("a");
    fireEvent.click(await screen.findByTestId("detail-tab-cicd"));
    fireEvent.click(await screen.findByTestId("cicd-start-request"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/cicd/requests/submit", {
        task_id: "a",
        request_type: "modify",
        payload: { status: { old: "stopped", new: "running" } },
        source: "app_workbench",
      }),
    );
  });
});

describe("new App", () => {
  it("submits a create request instead of creating the App", async () => {
    setUser("rm", "RM");
    mockApi(makeState([], makeRelease([])));
    vi.mocked(apiPost).mockResolvedValue({ ok: true, request: { id: 42 } });
    renderPage();
    fireEvent.click(await screen.findByTestId("new-app-btn"));
    fireEvent.change(screen.getByTestId("new-app-name"), { target: { value: "AMBER" } });
    fireEvent.change(screen.getByTestId("new-app-doc-target"), { target: { value: "manual" } });
    const typeSelect = screen.getByTestId("new-app-type") as HTMLSelectElement;
    fireEvent.change(typeSelect, { target: { value: typeSelect.options[1].value } });
    fireEvent.change(screen.getByTestId("new-app-repo-name"), { target: { value: "hpc_amber" } });
    fireEvent.change(screen.getByTestId("new-app-branch"), { target: { value: "maca" } });
    fireEvent.click(screen.getByTestId("new-app-submit"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/cicd/apps/new", expect.objectContaining({
        official_name: "AMBER", repo_name: "hpc_amber", branch: "maca", doc_target: "manual",
      })),
    );
  });
});
