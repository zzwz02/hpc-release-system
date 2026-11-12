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
import type { StatePayload, ReleaseScheduleEntry } from "../../../types";
import { makeApp, makeEntry, makeRelease, makeState } from "../../../test/fixtures";

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
  __setReleaseId: (id: string) => void;
};

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

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
  const apps = [
    makeApp("app1", { name: "App app1 1.0" }),
    makeApp("app2", { name: "App app2 1.0", owners: ["bob"] }),
    makeApp("app3", { name: "App app3 1.0", owners: ["bob"], cicd_status: "stopped", doc_target: "ai4sci" }),
  ];
  return makeState(apps, makeRelease([makeEntry("app1")]), {
    user: { username: "alice", role: "RM", display_name: "Alice" },
    ...overrides,
  });
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
  it("shows the selected cycle dates and updates after refresh", async () => {
    uiStoreMod.__setReleaseId("rel-1");
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({ user: { username: "alice", role: "Owner", display_name: "Alice" } });
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    renderDashboard(makeQueryClient());
    const dates = await screen.findByRole("region", { name: "当前版本时间节点" });
    expect(dates.closest(".page-toolbar")).not.toBeNull();
    expect(dates).toHaveTextContent("报名截止2026-12-01 23:59:00");
    expect(dates).toHaveTextContent("QA 截止2026-12-26 23:59:00");
    expect(dates).toHaveTextContent("发布日2026-12-31");
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ ...payload,
      release: { ...payload.release!, qa_deadline: "2026-12-25 23:59" },
    });
    fireEvent.click(screen.getByRole("button", { name: /刷新/ }));
    await waitFor(() => expect(dates).toHaveTextContent("2026-12-25 23:59:00"));
  });

  it("counts running Apps and signups", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    renderDashboard(makeQueryClient());
    const running = (await screen.findByText("CICD 运行中 App")).closest(".stat");
    expect(running?.querySelector(".num")).toHaveTextContent("2");
    expect(running).toHaveTextContent("已停止 1");
    expect(screen.getByText("本期报名").closest(".stat")?.querySelector(".num")).toHaveTextContent("1");
    expect(screen.getByText(/QA 待测试 \/ 通过 \/ 存在问题 \/ 不可发布/)).toBeDefined();
  });

  it("counts pending scope requests", async () => {
    const payload = makePayload();
    payload.release!.entries = [makeEntry("app1", {
      pending_scope_request: {
        id: 1, entry_id: 1, base_version_id: 1, target_version_id: 2, additions: ["新增芯片 C600"],
        status: "pending", created_at: "", decided_by: "", decided_at: "", decision_note: "",
      },
    })];
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    renderDashboard(makeQueryClient());
    const stat = (await screen.findByText("范围扩大待 QA 审批")).closest(".stat");
    expect(stat?.querySelector(".num")).toHaveTextContent("1");
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
  it("shows all apps with CICD, signup and official version for RM", async () => {
    const payload = makePayload();
    payload.apps[1].official = { release_id: "r0", release_name: "3.10.0", version: "0.9", image: "", digest: "", released_at: "" };
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    renderDashboard(makeQueryClient());
    const row1 = await screen.findByTestId("dashboard-app-row-app1");
    expect(row1).toHaveTextContent("已报名");
    expect(screen.getByTestId("dashboard-app-row-app2")).toHaveTextContent("0.9（3.10.0）");
    expect(screen.getByTestId("dashboard-app-row-app3")).toHaveTextContent("CICD 已停止");
  });

  it("shows only own apps for Owner role", async () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { username: "alice", role: "Owner", display_name: "Alice" },
    });
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    renderDashboard(makeQueryClient());
    await screen.findByText("我的 App 状态概览");
    expect(screen.getByTestId("dashboard-app-row-app1")).toBeDefined();
    expect(screen.queryByTestId("dashboard-app-row-app2")).toBeNull();
  });

  it("filters by signup state", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    renderDashboard(makeQueryClient());
    const filter = await screen.findByTestId("dashboard-app-status-filter") as HTMLSelectElement;
    fireEvent.change(filter, { target: { value: "signed_up" } });
    await waitFor(() => expect(screen.queryByTestId("dashboard-app-row-app2")).toBeNull());
    expect(screen.getByTestId("dashboard-app-row-app1")).toBeDefined();
    fireEvent.change(filter, { target: { value: "stopped" } });
    await waitFor(() => expect(screen.getByTestId("dashboard-app-row-app3")).toBeDefined());
    expect(screen.queryByTestId("dashboard-app-row-app1")).toBeNull();
  });
});
