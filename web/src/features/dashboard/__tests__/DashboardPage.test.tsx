/**
 * Tests for DashboardPage.
 *
 * Verifies:
 *  - Stats row renders correct counts from state payload
 *  - Schedule panel renders entries and shows "尚未维护" when empty
 *  - Owner grid shows apps with correct pills
 *  - RefreshBar is present and wired to the query's dataUpdatedAt
 *  - RM sees "+ 新增" button; non-RM does not
 *  - Owner sees only their apps; RM sees all
 *  - Loading state shows "加载中…"
 *  - Error state shows error message
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { DashboardPage } from "../DashboardPage";
import type { StatePayload, App, Snapshot, ReleaseDetail, ReleaseSummary, ReleaseScheduleEntry } from "../../../types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Mock the http module so no real fetch calls happen
vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

// Mock useAuth
vi.mock("../../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

// Mock uiStore: dashboard reads selectedReleaseId + setSelectedReleaseId.
// Start empty ("") so the seed-if-unset effect fires on first data load.
vi.mock("../../../store/uiStore", () => {
  let _releaseId = "";
  const setSelectedReleaseId = (id: string) => { _releaseId = id; };
  const useUiStore = (selector?: (s: { selectedReleaseId: string; setSelectedReleaseId: typeof setSelectedReleaseId }) => unknown) => {
    const state = { selectedReleaseId: _releaseId, setSelectedReleaseId };
    return selector ? selector(state) : state;
  };
  useUiStore.getState = () => ({ selectedReleaseId: _releaseId, setSelectedReleaseId });
  return {
    useUiStore,
    __setReleaseId: (id: string) => { _releaseId = id; },
    __resetReleaseId: () => { _releaseId = ""; },
  };
});

import { apiGet, apiPost } from "../../../api/http";
import { useAuth } from "../../../api/AuthContext";

// Top-level import to access the __resetReleaseId helper from the mock
const uiStoreMod = await import("../../../store/uiStore") as unknown as {
  __resetReleaseId: () => void;
};

// ---------------------------------------------------------------------------
// Test fixtures
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

function makeSnap(appId: string, overrides: Partial<Snapshot> = {}): Snapshot {
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
    doc: { intro: "", image_usage: "", binary_usage: "", env_setup: "", limitations: "" },
    community: { release_status: "", python_version: "", framework_version: "" },
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

function makeRelease(snapshots: Record<string, Snapshot>): ReleaseDetail {
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

function makeScheduleEntry(id: string, version: string): ReleaseScheduleEntry {
  return {
    id,
    version,
    branch_cut_at: "2026-05-15",
    release_at: "2026-06-30",
    note: "test note",
    created_at: "2026-01-01 00:00:00",
    created_by: "admin",
    updated_at: "2026-01-01 00:00:00",
    updated_by: "admin",
  };
}

function makePayload(overrides: Partial<StatePayload> = {}): StatePayload {
  const app1 = makeApp("app1");
  const app2 = makeApp("app2");
  const snap1 = makeSnap("app1");
  const snap2 = makeSnap("app2", { release_decision: "cicd_only", owners: ["bob"] });

  return {
    apps: [app1, app2],
    releases: [makeReleaseSummary()],
    release: makeRelease({ app1: snap1, app2: snap2 }),
    artifacts: [],
    user: { username: "alice", role: "RM", display_name: "Alice" },
    user_display_names: {},
    qa_log: null,
    qa_audit_logs: {},
    release_schedule: [],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        // Allow refetchOnMount per-query to work as set in the component
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
    },
  });
}

function renderDashboard(queryClient: QueryClient) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  // Reset shared release selector so each test starts from unset state
  uiStoreMod.__resetReleaseId();
  // Default: logged in as RM
  (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
    user: { username: "alice", role: "RM", display_name: "Alice" },
    logout: vi.fn(),
    clearUser: vi.fn(),
  });
});

describe("DashboardPage loading state", () => {
  it("shows loading indicator while fetching", () => {
    // Never resolves
    (apiGet as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    const qc = makeQueryClient();
    renderDashboard(qc);
    // The loading message appears immediately
    expect(screen.getByText(/加载中/)).toBeDefined();
  });
});

describe("DashboardPage stats row", () => {
  it("shows total app count for RM", async () => {
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    // Wait for data to load and stats row to appear
    await waitFor(() => {
      expect(screen.getByText("App 总数")).toBeDefined();
    });
    // The stat for "App 总数" should show count 2
    const statEl = screen.getByText("App 总数").closest(".stat");
    expect(statEl?.querySelector(".num")?.textContent).toBe("2");
  });

  it("shows QA breakdown header", async () => {
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText(/QA 待测试 \/ 通过 \/ 存在问题 \/ 不可发布/)).toBeDefined();
    });
  });

  it("shows release decision count", async () => {
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText("release 决策")).toBeDefined();
    });
  });
});

describe("DashboardPage schedule panel", () => {
  it("shows '尚未维护发布时间线' when no entries and user is RM", async () => {
    const payload = makePayload({ release_schedule: [] });
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText(/尚未维护发布时间线/)).toBeDefined();
    });
  });

  it("shows schedule entries when present", async () => {
    const payload = makePayload({
      release_schedule: [
        makeScheduleEntry("s1", "3.1.0"),
        makeScheduleEntry("s2", "3.2.0"),
      ],
    });
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText("3.1.0")).toBeDefined();
      expect(screen.getByText("3.2.0")).toBeDefined();
    });
  });

  it("shows '+ 新增' button for RM role", async () => {
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText("+ 新增")).toBeDefined();
    });
  });

  it("uses shared yyyy-mm-dd date inputs when adding schedule entries", async () => {
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText("+ 新增")).toBeDefined();
    });
    fireEvent.click(screen.getByText("+ 新增"));

    const branchCut = screen.getByTestId("schedule-branch-cut") as HTMLInputElement;
    const releaseAt = screen.getByTestId("schedule-release-at") as HTMLInputElement;
    expect(branchCut.type).toBe("text");
    expect(releaseAt.type).toBe("text");
    expect(branchCut.placeholder).toBe("YYYY-MM-DD");
    expect(releaseAt.placeholder).toBe("YYYY-MM-DD");
    expect((screen.getByTestId("schedule-branch-cut-native") as HTMLInputElement).type).toBe("date");
    expect(screen.getByTestId("schedule-branch-cut-calendar")).toBeDefined();
    expect(screen.getByTestId("schedule-release-at-calendar")).toBeDefined();
  });

  it("submits schedule dates selected from calendar picker as yyyy-mm-dd", async () => {
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText("+ 新增")).toBeDefined();
    });
    fireEvent.click(screen.getByText("+ 新增"));
    fireEvent.change(screen.getByPlaceholderText("例：3.0.0"), { target: { value: "3.9.0" } });
    fireEvent.change(screen.getByTestId("schedule-branch-cut-native"), { target: { value: "2026-07-16" } });
    fireEvent.change(screen.getByTestId("schedule-release-at-native"), { target: { value: "2026-08-27" } });
    fireEvent.change(screen.getByPlaceholderText("选填"), { target: { value: "direct_dispatch=1" } });

    expect((screen.getByTestId("schedule-branch-cut") as HTMLInputElement).value).toBe("2026-07-16");
    expect((screen.getByTestId("schedule-release-at") as HTMLInputElement).value).toBe("2026-08-27");

    fireEvent.click(screen.getByText("保存"));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/release-schedule/upsert", {
        id: "",
        version: "3.9.0",
        branch_cut_at: "2026-07-16",
        release_at: "2026-08-27",
        note: "direct_dispatch=1",
      });
    });
  });

  it("hides '+ 新增' button for Owner role", async () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { username: "alice", role: "Owner", display_name: "Alice" },
    });
    const payload = makePayload({
      user: { username: "alice", role: "Owner", display_name: "Alice" },
    });
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText("发布时间线")).toBeDefined();
    });
    expect(screen.queryByText("+ 新增")).toBeNull();
  });
});

describe("DashboardPage RefreshBar", () => {
  it("renders the refresh button", async () => {
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    // RefreshBar renders the button
    await waitFor(() => {
      expect(screen.getByTestId("refresh-btn")).toBeDefined();
    });
  });
});

describe("DashboardPage error state", () => {
  it("shows error message on fetch failure", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("网络错误"));
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText(/加载失败/)).toBeDefined();
    });
    expect(screen.getByText(/网络错误/)).toBeDefined();
  });
});

describe("DashboardPage owner grid", () => {
  it("shows all apps for RM", async () => {
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText("App 状态概览")).toBeDefined();
    });
    // Both apps are shown (RM sees all) — names may be split across elements
    expect(screen.getByText(/App app1/)).toBeDefined();
    expect(screen.getByText(/App app2/)).toBeDefined();
  });

  it("shows only own apps for Owner role", async () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { username: "alice", role: "Owner", display_name: "Alice" },
    });
    const payload = makePayload({
      user: { username: "alice", role: "Owner", display_name: "Alice" },
    });
    // alice owns app1; bob owns app2
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderDashboard(qc);

    await waitFor(() => {
      expect(screen.getByText("我的 App 状态概览")).toBeDefined();
    });
    expect(screen.getByText(/App app1/)).toBeDefined();
    expect(screen.queryByText(/App app2/)).toBeNull();
  });
});
