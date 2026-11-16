/**
 * QaPage tests.
 *
 * Covers:
 *  - Tab renders with six peer tabs
 *  - QA mark pane: shows no-release message when no release
 *  - QA mark pane: shows app cards for release-decision=release apps
 *  - QA mark pane: edit button only for writable roles
 *  - QA mark pane: save calls /api/qa/status-batch with changed items only
 *  - QA log failures grouped per App/GPU, isolated across cycles and identities
 *  - Manual App comments save without resubmitting unchanged delivery images
 *  - RefreshBar rendered with dataUpdatedAt
 *  - Loading state shown while fetching
 *  - Error state shown on fetch failure
 */

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { QaPage } from "../QaPage";
import type { StatePayload, Entry } from "../../../types";
import { makeApp as baseApp, makeEntry, makeRelease as baseRelease, makeState } from "../../../test/fixtures";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("../../../lib/confirm", () => ({
  confirmDialog: vi.fn(),
  promptDialog: vi.fn(),
}));

vi.mock("../../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

vi.mock("../../../store/uiStore", () => {
  // Full zustand-like store mock: includes all fields and setters used by QaPage.
  // Setters mutate _state in place so selector re-reads see the new value.
  let _state = {
    // shared release selector
    selectedReleaseId: "rel-1",
    setSelectedReleaseId: (id: string) => { _state = { ..._state, selectedReleaseId: id }; },
    // QA section
    qaEditMode: false,
    setQaEditMode: (v: boolean) => { _state = { ..._state, qaEditMode: v }; },
    qaEditReleaseId: "",
    setQaEditReleaseId: (id: string) => { _state = { ..._state, qaEditReleaseId: id }; },
    qaAiJob: null as null | { job_id: string; status: string; release_id: string; started_at: string; finished_at: null; summary: string; error: null; progress: null },
    setQaAiJob: (job: unknown) => { _state = { ..._state, qaAiJob: job as null }; },
    qaAiSuggestions: {} as Record<string, unknown>,
    setQaAiSuggestions: (v: Record<string, unknown>) => { _state = { ..._state, qaAiSuggestions: v }; },
    clearQaAiSuggestions: () => { _state = { ..._state, qaAiSuggestions: {} }; },
    // Report state (used in ReportPane)
    qaReportFilters: { release: { filter: "", colFilters: {}, sort: { col: -1, dir: 1 as const } }, test: { filter: "", colFilters: {}, sort: { col: -1, dir: 1 as const } }, manager: { filter: "", colFilters: {}, sort: { col: -1, dir: 1 as const } } },
    setQaReportFilter: (_k: string, _v: string) => {},
    setQaReportColFilter: (_k: string, _col: string, _v: string) => {},
    setQaReportSort: (_k: string, _col: number, _dir: 1 | -1) => {},
    resetQaReportState: (_k: string) => {},
    qaReportVisibleColumns: { release: null as Set<string> | null, test: null as Set<string> | null, manager: null as Set<string> | null },
    setQaReportVisibleColumns: (_k: string, _v: Set<string> | null) => {},
    qaReportsReleaseId: "",
    setQaReportsReleaseId: (_id: string) => {},
    qaReportCompareId: "",
    setQaReportCompareId: (_id: string) => {},
  };

  // useUiStore(selector) — returns selected slice; useUiStore() — returns full store
  const useUiStore = (selector?: (s: typeof _state) => unknown) =>
    selector ? selector(_state) : _state;
  useUiStore.getState = () => _state;

  return {
    useUiStore,
    // Expose setter to allow tests to override state before renders
    __setState: (patch: Partial<typeof _state>) => {
      _state = { ..._state, ...patch };
    },
  };
});

import { apiGet, apiPost } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";
import { useAuth } from "../../../api/AuthContext";
import { useUiStore } from "../../../store/uiStore";

// The vi.mock factory above exports __setState; cast to access it in tests.
const uiStoreMod = await import("../../../store/uiStore") as unknown as {
  useUiStore: typeof useUiStore;
  __setState: (patch: object) => void;
};

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeApp(id: string) {
  return baseApp(id, { name: `App ${id} 1.0` });
}

function makeRelease(entries: Entry[]) {
  return baseRelease(entries, { phase: "in_progress" });
}

function makePayload(overrides: Partial<StatePayload> = {}): StatePayload {
  return makeState([makeApp("app1")], makeRelease([makeEntry("app1")]), {
    user: { username: "qa_user", role: "QA", display_name: "QA User" },
    ...overrides,
  });
}

function makeQClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
    },
  });
}

function renderQaPage(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/qa"]}>
        <QaPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  // Default: logged in as QA
  (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
    user: { username: "qa_user", role: "QA", display_name: "QA User" },
    logout: vi.fn(),
    clearUser: vi.fn(),
  });

  // Default uiStore state
  uiStoreMod.__setState({
    selectedReleaseId: "rel-1",
    qaEditMode: false,
    qaEditReleaseId: "",
    qaAiJob: null,
    qaAiSuggestions: {},
  });

  // Default apiGet mock for /api/app-audit (change log)
  (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
    if (path.includes("/api/app-audit")) {
      return Promise.resolve({ entries: [] });
    }
    return Promise.resolve(makePayload());
  });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("QaPage structure", () => {
  it("renders page heading", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText("QA")).toBeDefined());
  });

  it("renders six peer tabs after data loads", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      const nav = screen.getByRole("navigation", { name: "QA" });
      expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual([
        "QA report", "本期发布app", "测试命令", "QA log", "开发者社区产物", "历史查询", "兼容性验证",
      ]);
      expect(within(nav).getByRole("link", { name: "QA report" })).toHaveAttribute("aria-current", "page");
    });
  });

  it("shows RefreshBar button", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      expect(screen.getByTestId("refresh-btn")).toBeDefined();
    });
  });

  it("shows loading indicator while fetching", () => {
    (apiGet as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    const qc = makeQClient();
    renderQaPage(qc);
    expect(screen.getByText(/加载中/)).toBeDefined();
  });

  it("shows error on fetch failure", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("net error"));
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      expect(screen.getByText(/加载失败/)).toBeDefined();
      expect(screen.getByText(/net error/)).toBeDefined();
    });
  });
});

describe("QaPage mark pane", () => {
  it("shows app cards for release-decision=release apps", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload());
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      expect(screen.getByText(/App app1/)).toBeDefined();
    });
  });

  it("shows only active signups", async () => {
    const payload = makePayload({
      apps: [makeApp("app1"), makeApp("app2"), makeApp("app3")],
      release: makeRelease([makeEntry("app1"), makeEntry("app2", { id: 2, status: "withdrawn" })]),
    });
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(payload);
    });
    renderQaPage(makeQClient());
    await waitFor(() => expect(screen.getByText(/App app1/)).toBeDefined());
    expect(screen.queryByText(/App app2/)).toBeNull();
    expect(screen.queryByText(/App app3/)).toBeNull();
  });

  it("saves the verdict together with the delivered image digest", async () => {
    uiStoreMod.__setState({
      selectedReleaseId: "rel-1", qaEditMode: true, qaEditReleaseId: "rel-1", qaAiJob: null, qaAiSuggestions: {},
    });
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      if (path.includes("/api/qa/")) return Promise.resolve({ suggestions: null, job: null });
      return Promise.resolve(makePayload());
    });
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    renderQaPage(makeQClient());
    fireEvent.change(await screen.findByTestId("qa-status-app1"), { target: { value: "qa_passed" } });
    const reference = "harbor.sh.mxcr.io/hpc-release/testapp-maca@sha256:" + "a".repeat(64);
    fireEvent.change(screen.getByTestId("delivery-reference"), { target: { value: reference } });
    fireEvent.click(screen.getAllByText("保存 QA 状态")[0]);
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/qa/status-batch", {
      release_id: "rel-1",
      items: [{
        app_id: "app1", status: "qa_passed", issue_note: "",
        deliverable_images: [expect.objectContaining({ build_key: "ubuntu22.04_amd64", reference })],
        delivery_revision: "revision-1",
      }],
    }));
  });

  it("lets QA approve a pending scope request", async () => {
    const payload = makePayload();
    payload.release!.scope_requests = [{
      id: 7, entry_id: 1, app_id: "app1", app_name: "App app1", base_version_id: 1, target_version_id: 2,
      additions: ["新增测试 hpc.x.t3"], status: "pending", created_at: "", decided_by: "", decided_at: "", decision_note: "",
    }];
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(payload);
    });
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    vi.mocked(confirmDialog).mockResolvedValue(true);
    renderQaPage(makeQClient());
    const row = await screen.findByTestId("scope-request-7");
    expect(row).toHaveTextContent("新增测试 hpc.x.t3");
    fireEvent.click(within(row).getByText("同意"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/scope-requests/decide", {
      request_id: 7, approve: true, note: "",
    }));
  });

  it("shows 编辑 button for QA/RM (writable roles)", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload());
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      // There may be one edit button per app card; getAllByText handles multiple matches.
      expect(screen.getAllByText(/修改/).length).toBeGreaterThan(0);
    });
  });

  it("hides edit button for Guest role (read-only)", async () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { username: "guest", role: "Guest", display_name: "Guest" },
    });
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(
        makePayload({ user: { username: "guest", role: "Guest", display_name: "Guest" } }),
      );
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText(/App app1/)).toBeDefined());
    expect(screen.queryByRole("button", { name: /修改/ })).toBeNull();
  });

  it("shows the missing-report message when there is no QA log", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload({ qa_log: null }));
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      expect(screen.getByText(/本版本尚无 QA log 测试结果/)).toBeDefined();
    });
  });

  it("removes the old log upload and AI controls even when a legacy log exists", async () => {
    const payload = makePayload({
      qa_log: {
        release_id: "rel-1",
        filename: "test.log",
        uploaded_at: "2026-06-01 10:00:00",
        uploaded_by: "qa_user",
        size_bytes: 1024,
        has_analysis: false,
        analysis_summary: "",
      },
    });
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(payload);
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "QA log 失败汇总" })).toBeVisible();
      expect(screen.queryByText(/test\.log/)).toBeNull();
      expect(document.querySelector('input[type="file"]')).toBeNull();
      expect(screen.queryByTestId("qa-analyze-btn")).toBeNull();
      expect(vi.mocked(apiGet).mock.calls.some(([path]) => /analyze-log|ai-suggestions/.test(path))).toBe(false);
    });
  });

  it("hides the issue-note placeholder in browse mode", async () => {
    const payload = makePayload({
      release: makeRelease([makeEntry("app1", { qa_status: "cannot_release" })]),
    });
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(payload);
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      expect(screen.getByText("QA comment (可选)")).toBeDefined();
      expect(screen.queryByText("必填")).toBeNull();
      expect(screen.queryByPlaceholderText(/可填写测试结论或其他说明/)).toBeNull();
    });
  });

  it("shows issue-note placeholder in edit mode", async () => {
    uiStoreMod.__setState({
      selectedReleaseId: "rel-1",
      qaEditMode: true,
      qaEditReleaseId: "rel-1",
      qaAiJob: null,
      qaAiSuggestions: {},
    });
    const payload = makePayload({
      release: makeRelease([makeEntry("app1", { qa_status: "cannot_release" })]),
    });
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(payload);
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/可填写测试结论或其他说明/)).toBeDefined();
    });
  });
});

describe("QaPage report failure summary", () => {
  const failed = {
    app_version: "v3.0.1", hpc_version: "3.10.0.7", arch: "x86", OS: "ubuntu22.04", GPU: "C600",
    py_label: "py312", torch_label: "torch2.8", test_name: "af3_cp1x1_unitest", qa_comment: "测试失败", qa_env: "server",
  };
  const summary = { release_id: "rel-1", release_name: "3.10.0", total_rows: 10, failed_rows: 2,
    groups: [{ app_id: "app1", app_name: "AlphaFold3 CP", git_branch: "maca", failures: [failed, { ...failed, test_name: "af3_cp2x2_unitest" }] }] };

  function routeSummary(summaryValue: () => unknown, payload = makePayload()) {
    vi.mocked(apiGet).mockImplementation(async path => {
      if (path.startsWith("/api/qa/failure-summary")) return summaryValue();
      if (path.startsWith("/api/app-audit")) return { entries: [] };
      return payload;
    });
  }

  it("groups identical GPU/platform/reason failures and keeps the submitted versions visible", async () => {
    routeSummary(() => summary);
    renderQaPage(makeQClient());
    const failures = await screen.findByTestId("qa-app-failures-app1");
    await waitFor(() => expect(failures).toHaveTextContent("af3_cp1x1_unitest、af3_cp2x2_unitest"));
    expect(failures).not.toHaveAttribute("open");
    expect(screen.getByTestId("qa-status-app1").compareDocumentPosition(failures) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const tests = within(failures).getByText("af3_cp1x1_unitest、af3_cp2x2_unitest");
    expect(tests).not.toBeVisible();
    fireEvent.click(within(failures).getByText("QA log 失败项（2）"));
    expect(tests).toBeVisible();
    expect(failures).toHaveTextContent("C600");
    expect(failures).toHaveTextContent("测试失败");
    expect(failures).toHaveTextContent("HPC 3.10.0.7 · App v3.0.1");
    expect(screen.getByTestId("qa-failure-summary")).toHaveTextContent("2 条失败 / 10 条结果");
    expect(screen.getByTestId("qa-status-app1")).toHaveValue("not_checked");
  });

  it("saves an App comment for a passed verdict without submitting unchanged images", async () => {
    uiStoreMod.__setState({ qaEditMode: true, qaEditReleaseId: "rel-1" });
    routeSummary(() => summary, makePayload({ release: makeRelease([makeEntry("app1", { qa_status: "qa_passed" })]) }));
    vi.mocked(apiPost).mockResolvedValue({ ok: true });
    renderQaPage(makeQClient());
    const comment = await screen.findByRole("textbox", { name: /QA comment/ });
    fireEvent.change(comment, { target: { value: "QA 手工确认：保留此备注" } });
    fireEvent.click(screen.getAllByRole("button", { name: /^保存 QA 状态$/ })[0]);
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/qa/status-batch", {
      release_id: "rel-1", items: [{ app_id: "app1", status: "qa_passed", issue_note: "QA 手工确认：保留此备注", delivery_revision: "revision-1" }],
    }));
  });

  it.each(["not_checked", "qa_passed", "has_issues", "cannot_release"])("saves %s with an empty optional comment", async status => {
    uiStoreMod.__setState({ qaEditMode: true, qaEditReleaseId: "rel-1" });
    routeSummary(() => summary, makePayload({ release: makeRelease([makeEntry("app1", {
      qa_status: status === "not_checked" ? "qa_passed" : "not_checked",
    })]) }));
    vi.mocked(apiPost).mockResolvedValue({ ok: true });
    renderQaPage(makeQClient());
    fireEvent.change(await screen.findByTestId("qa-status-app1"), { target: { value: status } });
    expect(screen.getByRole("textbox", { name: /QA comment/ })).toHaveValue("");
    expect(screen.getByText("QA comment (可选)")).toBeVisible();
    fireEvent.click(screen.getAllByRole("button", { name: /^保存 QA 状态$/ })[0]);
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/qa/status-batch", {
      release_id: "rel-1", items: [{ app_id: "app1", status, issue_note: "", delivery_revision: "revision-1" }],
    }));
  });

  it("hides old failure details on refresh errors while preserving an App comment draft", async () => {
    let fail = false;
    routeSummary(() => { if (fail) throw new Error("统计读取失败"); return summary; });
    uiStoreMod.__setState({ qaEditMode: true, qaEditReleaseId: "rel-1" });
    renderQaPage(makeQClient());
    await screen.findByText("af3_cp1x1_unitest、af3_cp2x2_unitest");
    fireEvent.change(screen.getByRole("textbox", { name: /QA comment/ }), { target: { value: "尚未保存的 comment" } });
    fail = true;
    fireEvent.click(within(screen.getByTestId("qa-failure-summary")).getByRole("button", { name: "刷新当前页" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("统计读取失败");
    expect(screen.queryByText("af3_cp1x1_unitest、af3_cp2x2_unitest")).toBeNull();
    expect(screen.getByRole("textbox", { name: /QA comment/ })).toHaveValue("尚未保存的 comment");
  });

  it("clears cached failure details immediately when the viewer changes", async () => {
    let first = true;
    routeSummary(() => first ? summary : new Promise(() => {}));
    const qc = makeQClient();
    const view = renderQaPage(qc);
    await screen.findByText("af3_cp1x1_unitest、af3_cp2x2_unitest");
    first = false;
    vi.mocked(useAuth).mockReturnValue({ user: { username: "owner", role: "Owner" } } as ReturnType<typeof useAuth>);
    view.rerender(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/qa"]}><QaPage /></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(screen.queryByText("af3_cp1x1_unitest、af3_cp2x2_unitest")).toBeNull());
    expect(screen.getByRole("textbox", { name: /QA comment/ })).toBeDisabled();
  });
});

describe("QaPage delivery image display", () => {
  const repository = "harbor.sh.mxcr.io/hpc-release/testapp-maca";
  const digest = "sha256:" + "a".repeat(64);
  const bare = `${repository}@${digest}`;
  const tagged = `${repository}:1.0-3.10.0.7-ubuntu22.04-amd64@${digest}`;
  const image = { ...makeEntry("app1").delivery_builds![0], image: repository, digest, reference: bare };
  function routeImages(response: () => unknown, builds = makeEntry("app1").delivery_builds) {
    const payload = makePayload({ release: makeRelease([makeEntry("app1", { deliverable_images: [image], delivery_builds: builds })]) });
    vi.mocked(apiGet).mockImplementation(async path => {
      if (path.startsWith("/api/qa/delivery-images")) return response();
      if (path.startsWith("/api/qa/failure-summary")) return { total_rows: 0, failed_rows: 0, groups: [] };
      if (path.startsWith("/api/app-audit")) return { entries: [] };
      return payload;
    });
  }

  it("keeps unfilled locked builds visible outside edit mode", async () => {
    const build = makeEntry("app1").delivery_builds![0];
    routeImages(() => ({ entries: { app1: { revision: "revision-1", images: [{ ...image, reference: tagged }] } } }), [
      build,
      { ...build, build_key: "ubuntu22.04_amd64_dbg", image_target: "dbg" },
      { ...build, build_key: "ubuntu22.04_amd64_py312", python_label: "py312" },
    ]);
    renderQaPage(makeQClient());
    expect(await screen.findByText(tagged)).toBeVisible();
    const table = screen.getByTestId("delivery-images");
    expect(within(table).getAllByText("未填写交付镜像")).toHaveLength(2);
    expect(within(table).getByRole("cell", { name: "dbg" })).toBeVisible();
    expect(within(table).getByRole("cell", { name: "py312" })).toBeVisible();
    expect(within(table).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows actual tagged references without using display values in a comment-only save", async () => {
    routeImages(() => ({ entries: { app1: { revision: "revision-1", images: [{ ...image, reference: tagged }] } } }));
    vi.mocked(apiPost).mockResolvedValue({ ok: true });
    const qc = makeQClient();
    const view = renderQaPage(qc);
    expect(await screen.findByText(tagged)).toBeVisible();
    uiStoreMod.__setState({ qaEditMode: true, qaEditReleaseId: "rel-1" });
    view.rerender(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/qa"]}><QaPage /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByTestId("delivery-reference")).toHaveValue(bare);
    fireEvent.change(screen.getByRole("textbox", { name: /QA comment/ }), { target: { value: "comment only" } });
    fireEvent.click(screen.getAllByRole("button", { name: /^保存 QA 状态$/ })[0]);
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/qa/status-batch", { release_id: "rel-1",
      items: [{ app_id: "app1", status: "not_checked", issue_note: "comment only", delivery_revision: "revision-1" }] }));
  });

  it("never applies a display reference from a different entry revision", async () => {
    routeImages(() => ({ entries: { app1: { revision: "newer-revision", images: [{ ...image, reference: tagged }] } } }));
    renderQaPage(makeQClient());
    expect(await screen.findByText("交付镜像记录已变化，请刷新当前页。")).toBeVisible();
    expect(screen.getByText(bare)).toBeVisible();
    expect(screen.queryByText(tagged)).toBeNull();
  });

  it("keeps the original delivery visible and reports lookup failures", async () => {
    routeImages(() => { throw new Error("Harbor 不可达"); });
    renderQaPage(makeQClient());
    expect(await screen.findByRole("alert")).toHaveTextContent("交付镜像 tag 读取失败：Harbor 不可达");
    expect(screen.getByText(bare)).toBeVisible();
  });
});

describe("QaPage subtab navigation", () => {
  it("switches to 本期发布app subtab", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload());
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText("本期发布app")).toBeDefined());
    fireEvent.click(screen.getByText("本期发布app"));
    // Report pane appears with load button
    await waitFor(() => {
      expect(screen.getByText(/加载报告/)).toBeDefined();
    });
  });

  it("switches to 测试命令 subtab", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload());
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText("测试命令")).toBeDefined());
    fireEvent.click(screen.getByText("测试命令"));
    await waitFor(() => {
      expect(screen.getByText(/查询测试命令/)).toBeDefined();
    });
  });

  it("shows perf_golden and perf_unit from each 测试命令 row", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      if (path.includes("/api/qa/test-commands")) {
        return Promise.resolve({
          hpc_version: "3.10.0.7", errors: [],
            columns: [
              "app_id",
              "test_name",
              "website_docker_cmd",
              "perf_golden",
              "perf_unit",
            ],
            rows: [{ app_id: "hpl", test_name: "sanity", website_docker_cmd: "docker run hpl", perf_golden: "max", perf_unit: "GFlops" }],
          generated_at: "2026-01-01 00:00:00",
        });
      }
      return Promise.resolve(makePayload());
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText("测试命令")).toBeDefined());
    fireEvent.click(screen.getByText("测试命令"));
    fireEvent.click(await screen.findByText("查询测试命令"));

    expect(await screen.findByText("perf_golden")).toBeDefined();
    expect(screen.getByText("perf_unit")).toBeDefined();
    expect(screen.getByText("max")).toBeDefined();
    expect(screen.getByText("GFlops")).toBeDefined();
  });

  it("does not include the current release in the compare selector", async () => {
    const previousRelease = { ...makeRelease([]), id: "rel-0", name: "2.9" };
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      if (path.includes("/api/qa-reports")) {
        return Promise.resolve({
          release_name: "3.0",
          compare_release_id: "",
          compare_release_name: "",
          release_report: {
            columns: ["名称", "对比"],
            rows: [["ABACUS", ""]],
            rows_meta: [{ app_id: "abacus", signed_up: true }],
          },
          test_cmd: { columns: [], rows: [] },
          generated_at: "2026-01-01 00:00:00",
        });
      }
      return Promise.resolve(makePayload({ releases: [makeRelease([]), previousRelease] }));
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText("本期发布app")).toBeDefined());
    fireEvent.click(screen.getByText("本期发布app"));
    fireEvent.click(await screen.findByText("加载报告"));

    const compareSelect = await screen.findByDisplayValue("— 不对比 —");
    const optionTexts = Array.from(compareSelect.querySelectorAll("option")).map(
      (option) => option.textContent,
    );
    expect(optionTexts).toEqual(["— 不对比 —", "2.9"]);
  });

});


it("shows the shared release selector for QA log and scopes its result request", async () => {
  vi.mocked(apiGet).mockImplementation(async path => path.startsWith("/api/state")
    ? makePayload() : { total: 0, rows: [], columns: [], revision: "empty" });
  render(<QueryClientProvider client={makeQClient()}>
    <MemoryRouter initialEntries={["/qa?tab=report"]}><QaPage /></MemoryRouter>
  </QueryClientProvider>);
  expect(await screen.findByTestId("qa-results-pane")).toBeVisible();
  expect(await screen.findByLabelText("选择 release")).toHaveValue("rel-1");
  await waitFor(() => expect(vi.mocked(apiGet).mock.calls.some(([url]) => url.includes("/api/qa/test-results?release_id=rel-1&"))).toBe(true));
});
