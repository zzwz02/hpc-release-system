/**
 * App 工作台 on the monthly signup model: list, signup, phase rules on the
 * signup pane, withdraw, CICD start/stop rules and the new-App request.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AppWorkbenchPage } from "../AppWorkbenchPage";
import { makeApp, makeEntry, makeRelease, makeState } from "../../../test/fixtures";
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

describe("signup", () => {
  it("signs up with base image, version, features and a reduced scope", async () => {
    mockApi(makeState([makeApp("a")], makeRelease([])));
    renderPage("a");
    fireEvent.change(await screen.findByTestId("signup-base-image"), { target: { value: "1" } });
    fireEvent.change(screen.getByTestId("signup-planned-features"), { target: { value: "new solver" } });
    fireEvent.click(screen.getByLabelText("纳入 hpc.x.t2"));
    fireEvent.click(screen.getByTestId("signup-submit"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/entries/signup", {
        release_id: "rel-1",
        app_id: "a",
        base_image_id: 1,
        target_version: "1.0",
        planned_features: "new solver",
        excluded_tests: ["hpc.x.t2"],
      }),
    );
  });

  it.each([
    ["stopped CICD", { cicd_status: "stopped" as const }, "CICD 已停止"],
    ["open stop request", { open_status_request: { id: 9, target_status: "stopped" as const } }, "#9"],
    ["missing app_info", { latest_app_info: null }, "还没有 app_info"],
  ])("blocks signup for %s", async (_label, overrides, text) => {
    mockApi(makeState([makeApp("a", overrides)], makeRelease([])));
    renderPage("a");
    expect(await screen.findByTestId("signup-blocker")).toHaveTextContent(text);
    expect(screen.queryByTestId("signup-submit")).toBeNull();
  });

  it("blocks signup after the deadline", async () => {
    mockApi(makeState([makeApp("a")], makeRelease([], { phase: "in_progress" })));
    renderPage("a");
    expect(await screen.findByTestId("signup-blocker")).toHaveTextContent("报名已截止");
  });
});

describe("signed-up entry", () => {
  it("after the deadline only allows removing tests and editing the delivery", async () => {
    const entry = makeEntry("a", { excluded_tests: ["hpc.x.t2"], scope_tests: [
      { path: "hpc.x.t1", command: "run t1", supported_chips: ["C500"], arch_list: [], excluded: false },
      { path: "hpc.x.t2", command: "run t2", supported_chips: ["C500"], arch_list: [], excluded: true },
    ] });
    mockApi(makeState([makeApp("a")], makeRelease([entry], { phase: "in_progress" })));
    renderPage("a");
    fireEvent.click(await screen.findByTestId("entry-edit"));
    expect(screen.getByTestId("entry-target-version")).toBeDisabled();
    expect(screen.getByLabelText("纳入 hpc.x.t2")).toBeDisabled();
    const t1 = screen.getByLabelText("纳入 hpc.x.t1");
    expect(t1).not.toBeDisabled();
    fireEvent.click(t1);
    fireEvent.change(screen.getByTestId("entry-actual-delivery"), { target: { value: "shipped A" } });
    fireEvent.click(screen.getByTestId("entry-save"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/entries/update", {
        entry_id: 1,
        changes: { excluded_tests: ["hpc.x.t2", "hpc.x.t1"], actual_delivery: "shipped A", known_issues: "" },
      }),
    );
  });

  it("shows a pending scope request and withdraws after confirmation", async () => {
    const entry = makeEntry("a", {
      pending_scope_request: {
        id: 5, entry_id: 1, base_version_id: 1, target_version_id: 2, additions: ["新增芯片 C600"],
        status: "pending", created_at: "", decided_by: "", decided_at: "", decision_note: "",
      },
    });
    mockApi(makeState([makeApp("a")], makeRelease([entry], { phase: "in_progress" })));
    renderPage("a");
    expect(await screen.findByTestId("pending-scope-request")).toHaveTextContent("新增芯片 C600");
    fireEvent.click(screen.getByTestId("entry-withdraw"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/entries/withdraw", { entry_id: 1 }));
  });

  it("is read-only once published", async () => {
    mockApi(makeState([makeApp("a")], makeRelease([makeEntry("a", { result: "released" })], {
      phase: "released", released_locked: true,
    })));
    renderPage("a");
    await screen.findByTestId("signup-pane");
    expect(screen.queryByTestId("entry-edit")).toBeNull();
    expect(screen.queryByTestId("entry-withdraw")).toBeNull();
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
