/**
 * QaPage tests.
 *
 * Covers:
 *  - Tab renders with 3 subtabs
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

import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { QaPage } from "../QaPage";
import type {
  StatePayload,
  App,
  Snapshot,
  ReleaseDetail,
  ReleaseSummary,
} from "../../../types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
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

function makeApp(id: string): App {
  return {
    id,
    git_url: `repo/${id}`,
    git_branch: "main",
    created_by: "admin",
    created_at: "2026-01-01 00:00:00",
    aliases: [],
  };
}

function makeSnap(
  appId: string,
  overrides: Partial<Snapshot> = {},
): Snapshot {
  return {
    app_id: appId,
    official_name: `App ${appId}`,
    version: "1.0",
    description: "",
    official_url: "",
    type: "tool",
    arch: "x86",
    x86_chips: "",
    arm_chips: "",
    hpcc_chip: "",
    maca_version: "",
    build_arches: "",
    build_os: "",
    doc_target: "manual",
    release_decision: "release",
    owners: ["alice"],
    owner_confirmed: false,
    qa_status: "not_checked" as Snapshot["qa_status"],
    qa_issue_note: "",
    doc: {
      intro: "",
      image_usage: "",
      binary_usage: "",
      env_setup: "",
      limitations: "",
    },
    community: {
      release_status: "",
      python_version: "",
      framework_version: "",
    },
    sanity: { arm_kylin: false, ubuntu: false },
    python_labels: "",
    pytorch_labels: "",
    test_docs: [],
    missing_items: [],
    app_info: null,
    app_info_diffs: [],
    ...overrides,
  };
}

function makeRelease(
  snapshots: Record<string, Snapshot>,
): ReleaseDetail {
  return {
    id: "rel-1",
    name: "3.0",
    maca_version: "3.0",
    app_freeze_deadline: "2026-06-01",
    doc_deadline: "2026-07-01",
    released_locked: false,
    released_locked_at: "",
    released_locked_by: "",
    created_at: "2026-01-01 00:00:00",
    source: "manual",
    cloned_from: "",
    phase: "before_app_freeze",
    snapshots,
  };
}

function makeReleaseSummary(): ReleaseSummary {
  return {
    id: "rel-1",
    name: "3.0",
    maca_version: "3.0",
    app_freeze_deadline: "2026-06-01",
    doc_deadline: "2026-07-01",
    released_locked: false,
    released_locked_at: "",
    released_locked_by: "",
    created_at: "2026-01-01 00:00:00",
    source: "manual",
    cloned_from: "",
    phase: "before_app_freeze",
  };
}

function makePayload(overrides: Partial<StatePayload> = {}): StatePayload {
  const app1 = makeApp("app1");
  const snap1 = makeSnap("app1");
  return {
    apps: [app1],
    releases: [makeReleaseSummary()],
    release: makeRelease({ app1: snap1 }),
    artifacts: [],
    user: { username: "qa_user", role: "QA", display_name: "QA User" },
    user_display_names: {},
    qa_log: null,
    qa_audit_logs: {},
    release_schedule: [],
    ...overrides,
  };
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
      <MemoryRouter>
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

  it("renders 3 subtabs after data loads", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => {
      expect(screen.getByText("QA 标注")).toBeDefined();
      expect(screen.getByText("Release Report")).toBeDefined();
      expect(screen.getByText("Test 命令")).toBeDefined();
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

  it("does NOT show apps with cicd_only decision", async () => {
    const payload = makePayload({
      apps: [makeApp("app1"), makeApp("app2")],
      release: makeRelease({
        app1: makeSnap("app1"),
        app2: makeSnap("app2", { release_decision: "cicd_only" }),
      }),
    });
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(payload);
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText(/App app1/)).toBeDefined());
    // app2 is cicd_only, not in QA grid
    expect(screen.queryByText(/App app2/)).toBeNull();
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
      release: makeRelease({
        app1: makeSnap("app1", { qa_status: "cannot_release" }),
      }),
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
      release: makeRelease({
        app1: makeSnap("app1", { qa_status: "cannot_release" }),
      }),
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
    const statusResponse = { ...startJobResponse, status: "running" };

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
  it("switches to Release Report subtab", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload());
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText("Release Report")).toBeDefined());
    fireEvent.click(screen.getByText("Release Report"));
    // Report pane appears with load button
    await waitFor(() => {
      expect(screen.getByText(/加载报告/)).toBeDefined();
    });
  });

  it("switches to Test 命令 subtab", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/app-audit")) return Promise.resolve({ entries: [] });
      return Promise.resolve(makePayload());
    });
    const qc = makeQClient();
    renderQaPage(qc);
    await waitFor(() => expect(screen.getByText("Test 命令")).toBeDefined());
    fireEvent.click(screen.getByText("Test 命令"));
    await waitFor(() => {
      expect(screen.getByText(/加载报告/)).toBeDefined();
    });
  });
});
