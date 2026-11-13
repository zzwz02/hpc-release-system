/**
 * QaPage tests.
 *
 * Covers:
 *  - Tab renders with six peer tabs
 *  - QA mark pane: shows no-release message when no release
 *  - QA mark pane: shows app cards for release-decision=release apps
 *  - QA mark pane: edit button only for writable roles
 *  - QA mark pane: save calls /api/qa/status-batch with changed items only
 *  - AI job poll: interval started on analyze, cleared on unmount (cleanup race)
 *  - AI job poll: interval cleared when release changes
 *  - RefreshBar rendered with dataUpdatedAt
 *  - Loading state shown while fetching
 *  - Error state shown on fetch failure
 */

import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
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
        "QA log", "本期发布app", "测试命令", "开发者社区产物", "历史查询", "组件依赖",
      ]);
      expect(within(nav).getByRole("link", { name: "QA log" })).toHaveAttribute("aria-current", "page");
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
    expect(screen.queryByText(/修改/)).toBeNull();
  });

  it("shows no-log message when qa_log is null", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload({ qa_log: null }));
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      expect(screen.getByText(/暂无 QA log/)).toBeDefined();
    });
  });

  it("shows uploaded log filename when qa_log present", async () => {
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
      expect(screen.getByText(/test\.log/)).toBeDefined();
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
      expect(screen.getByText("必填")).toBeDefined();
      expect(screen.queryByPlaceholderText(/存在问题.*不可发布/)).toBeNull();
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
      expect(screen.getByPlaceholderText(/存在问题.*不可发布/)).toBeDefined();
    });
  });
});

describe("QaPage AI job poll", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts 1s interval when analyze is clicked", async () => {
    // Pre-seed edit mode + matching release so the useEffect doesn't reset it
    uiStoreMod.__setState({
      selectedReleaseId: "rel-1",
      qaEditMode: true,
      qaEditReleaseId: "rel-1",
      qaAiJob: null,
      qaAiSuggestions: {},
    });

    const startJobResponse = {
      job_id: "job-1",
      release_id: "rel-1",
      status: "running",
      started_at: "1000",
      finished_at: null,
      summary: "",
      error: null,
      progress: null,
    };
    const statusResponse = {
      ...startJobResponse,
      status: "running",
      elapsed_seconds: 7,
      llm: {
        calls: 1, finished: 0, sent_chars: 3000, sent_tokens_est: 1000, received: 25, reasoning: 0,
        prompt_tokens: 0, completion_tokens: 0, usage_calls: 0,
      },
    };

    // Log upload present so analyze button is enabled
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
      if (path.includes("/api/qa/analyze-log/status"))
        return Promise.resolve(statusResponse);
      return Promise.resolve(payload);
    });
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue(startJobResponse);

    const qc = makeQClient();
    renderQaPage(qc);

    // Wait for analyze button to appear (real timers for waitFor)
    await waitFor(() =>
      expect(screen.getByTestId("qa-analyze-btn")).toBeDefined(),
    );

    // Click analyze — real timers still so the apiPost resolves normally
    await act(async () => {
      fireEvent.click(screen.getByTestId("qa-analyze-btn"));
    });

    expect(apiPost).toHaveBeenCalledWith(
      "/api/qa/analyze-log/start",
      expect.objectContaining({ release_id: "rel-1" }),
    );

    // The poll setInterval was registered with real timers; wait 1.2s so the
    // 1000ms interval fires at least once, then verify the status call.
    await new Promise<void>((res) => setTimeout(res, 1200));

    expect(apiGet).toHaveBeenCalledWith(
      expect.stringContaining("/api/qa/analyze-log/status"),
    );
  });

  it("shows the job's elapsed time and LLM usage with the shared formatter", async () => {
    uiStoreMod.__setState({
      selectedReleaseId: "rel-1",
      qaEditMode: true,
      qaEditReleaseId: "rel-1",
      qaAiJob: {
        job_id: "job-1", release_id: "rel-1", status: "running", started_at: "1000", finished_at: null,
        summary: "", error: null, progress: null, message: "正在接收 LLM 输出：已收到 25 token",
        elapsed_seconds: 7,
        llm: {
          calls: 1, finished: 0, sent_chars: 3000, sent_tokens_est: 1000, received: 25, reasoning: 0,
          prompt_tokens: 0, completion_tokens: 0, usage_calls: 0,
        },
      } as never,
      qaAiSuggestions: {},
    });
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload({}));
    });

    renderQaPage(makeQClient());

    // Same LLM usage line as every LLM feature (lib/llmProgress).
    await waitFor(() =>
      expect(document.getElementById("qaAiProgress")?.textContent).toContain(
        "已运行 7s；LLM 调用 1 次（进行中 1）；已发送 3,000 字符（约 1,000 token）；已收到 25 token",
      ),
    );
  });

  it("clears poll interval on unmount (cleanup race prevention)", async () => {
    // Pre-seed edit mode so we can access the analyze button directly
    uiStoreMod.__setState({
      selectedReleaseId: "rel-1",
      qaEditMode: true,
      qaEditReleaseId: "rel-1",
      qaAiJob: null,
      qaAiSuggestions: {},
    });

    const startJobResponse = {
      job_id: "job-1",
      release_id: "rel-1",
      status: "running",
      started_at: "1000",
      finished_at: null,
      summary: "",
      error: null,
      progress: null,
    };

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

    let statusCallCount = 0;
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      if (path.includes("/api/qa/analyze-log/status")) {
        statusCallCount++;
        return Promise.resolve({ ...startJobResponse, status: "running" });
      }
      return Promise.resolve(payload);
    });
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue(startJobResponse);

    const qc = makeQClient();
    const { unmount } = renderQaPage(qc);

    // Use real timers for initial data load
    await waitFor(() => expect(screen.getByTestId("qa-analyze-btn")).toBeDefined());

    await act(async () => {
      fireEvent.click(screen.getByTestId("qa-analyze-btn"));
    });

    // Switch to fake timers to control polling
    vi.useFakeTimers();

    // Advance 1 second — one poll fires
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => { await Promise.resolve(); });
    const countAfterFirst = statusCallCount;

    // Unmount — clears the interval
    unmount();

    // Advance more time — no more polls should fire
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    await act(async () => { await Promise.resolve(); });

    // After unmount, no additional status calls should have been made
    expect(statusCallCount).toBe(countAfterFirst);
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
      expect(screen.getByText(/加载报告/)).toBeDefined();
    });
  });

  it("shows perf_golden and perf_unit from each 测试命令 row", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      if (path.includes("/api/qa-reports")) {
        return Promise.resolve({
          release_name: "3.0",
          compare_release_id: "",
          compare_release_name: "",
          release_report: { columns: [], rows: [], rows_meta: [] },
          test_cmd: {
            columns: [
              "app_name",
              "test_name",
              "docker_cmd",
              "perf_golden",
              "perf_unit",
            ],
            rows: [["HPL", "sanity", "docker run hpl", "max", "GFlops"]],
          },
          generated_at: "2026-01-01 00:00:00",
        });
      }
      return Promise.resolve(makePayload());
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText("测试命令")).toBeDefined());
    fireEvent.click(screen.getByText("测试命令"));
    fireEvent.click(await screen.findByText("加载报告"));

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


// ---------------------------------------------------------------------------
// Following an analysis across refreshes, stopping it, saved AI suggestions
// ---------------------------------------------------------------------------

describe("QaPage AI analysis after a refresh", () => {
  const runningJob = {
    job_id: "job-7", release_id: "rel-1", status: "running", started_at: "1000", finished_at: null,
    summary: "", error: null, progress: null, message: "正在等待 LLM 返回结果", user: "rm",
    elapsed_seconds: 30, stopping: false,
  };

  function routeGets(extra: Record<string, unknown>) {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      for (const [prefix, value] of Object.entries(extra)) {
        if (path.startsWith(prefix)) return Promise.resolve(value);
      }
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload({}));
    });
  }

  it("asks the server for the release's running analysis and follows it", async () => {
    uiStoreMod.__setState({
      selectedReleaseId: "rel-1", qaEditMode: false, qaEditReleaseId: "rel-1", qaAiJob: null, qaAiSuggestions: {},
    });
    routeGets({
      "/api/qa/analyze-log/current": { job: runningJob },
      "/api/qa/analyze-log/status": runningJob,
      "/api/qa/ai-suggestions": { suggestions: null },
    });

    renderQaPage(makeQClient());

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith("/api/qa/analyze-log/current?release_id=rel-1"),
    );
    await waitFor(() => expect(uiStoreMod.useUiStore.getState().qaAiJob).toMatchObject({ job_id: "job-7" }));
  });

  it("shows who started a running analysis and lets QA stop it", async () => {
    uiStoreMod.__setState({
      selectedReleaseId: "rel-1", qaEditMode: false, qaEditReleaseId: "rel-1", qaAiJob: runningJob,
      qaAiSuggestions: {},
    });
    routeGets({
      "/api/qa/analyze-log/status": runningJob,
      "/api/qa/ai-suggestions": { suggestions: null },
    });
    vi.mocked(confirmDialog).mockResolvedValue(true);
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ...runningJob, stopping: true });

    renderQaPage(makeQClient());

    await waitFor(() => expect(document.getElementById("qaAiProgress")?.textContent).toContain("rm 发起"));
    fireEvent.click(screen.getByRole("button", { name: "中止分析" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/qa/analyze-log/cancel", { job_id: "job-7" }),
    );
    expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ confirmText: "中止分析", danger: true }));
  });

  it("reloads saved AI suggestions, still marked as AI 建议, and can discard them", async () => {
    uiStoreMod.__setState({
      selectedReleaseId: "rel-1", qaEditMode: false, qaEditReleaseId: "rel-1", qaAiJob: null, qaAiSuggestions: {},
    });
    routeGets({
      "/api/qa/analyze-log/current": { job: null },
      "/api/qa/ai-suggestions": {
        suggestions: {
          log_filename: "qa.log", analyzed_by: "qa_user", analyzed_at: "2026-10-14 15:00:00",
          result: { apps: [{ app_id: "app-a", qa_status: "has_issues", qa_issue_note: "用例失败", test_results: [] }] },
        },
      },
    });
    vi.mocked(confirmDialog).mockResolvedValue(true);
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });

    renderQaPage(makeQClient());

    const banner = await screen.findByTestId("qa-ai-saved");
    expect(banner.textContent).toContain("AI 建议");
    expect(banner.textContent).toContain("qa.log，有 1 个 app 的建议待核对");
    expect(uiStoreMod.useUiStore.getState().qaAiSuggestions).toEqual({
      "app-a": { qa_status: "has_issues", qa_issue_note: "用例失败", test_results: [] },
    });

    fireEvent.click(screen.getByRole("button", { name: "丢弃 AI 建议" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/qa/ai-suggestions/discard", { release_id: "rel-1" }),
    );
    await waitFor(() => expect(uiStoreMod.useUiStore.getState().qaAiSuggestions).toEqual({}));
  });
});
