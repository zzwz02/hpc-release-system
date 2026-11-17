/**
 * Tests for DashboardPage.
 *
 * Verifies:
 *  - Stats row renders correct counts from state payload
 *  - Selected cycle component information replaces the timeline
 *  - Owner grid shows apps with correct pills
 *  - RefreshBar is present and wired to the query's dataUpdatedAt
 *  - Component information follows cycle selection and explicit refresh
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

import { apiGet, apiPost } from "../../../api/http";
import { useAuth } from "../../../api/AuthContext";

import { useUiStore } from "../../../store/uiStore";

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
  useUiStore.setState({ selectedReleaseId: "", selectedApp: "", appDetailDirty: false });
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
    useUiStore.setState({ selectedReleaseId: "rel-1" });
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

describe("DashboardPage selected cycle components", () => {
  it("replaces the timeline with component versions and notes from the selected cycle", async () => {
    const payload = makePayload({ release_schedule: [makeScheduleEntry("s1", "old-timeline-version")] });
    payload.release!.environment = [
      { component: "Driver", version: "driver-3.11", note: "驱动说明" },
      { component: "SDK", version: "sdk-3.11", note: "" },
      { component: "PyTorch", version: "torch-3.11", note: "框架说明" },
    ];
    vi.mocked(apiGet).mockResolvedValue(payload);
    renderDashboard(makeQueryClient());
    const panel = await screen.findByTestId("dashboard-components");
    expect(panel).toHaveTextContent("2026.12");
    for (const value of ["driver-3.11", "sdk-3.11", "torch-3.11", "驱动说明", "框架说明"]) {
      expect(panel).toHaveTextContent(value);
    }
    expect(screen.queryByText("发布时间线")).toBeNull();
    expect(screen.queryByText("old-timeline-version")).toBeNull();
    expect(screen.queryByRole("button", { name: "+ 新增" })).toBeNull();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("updates components when a different cycle is selected", async () => {
    const first = makePayload();
    const nextRelease = makeRelease([], { id: "rel-2", name: "2027.01", environment: [
      { component: "SDK", version: "sdk-4.0", note: "新版本说明" },
    ] });
    const releases = [first.release!, nextRelease];
    vi.mocked(apiGet).mockImplementation(async (url) => url.includes("release_id=rel-2")
      ? { ...first, releases, release: nextRelease }
      : { ...first, releases });
    renderDashboard(makeQueryClient());
    const panel = await screen.findByTestId("dashboard-components");
    expect(panel).toHaveTextContent("MACA SDK3.3");
    fireEvent.change(screen.getByRole("combobox", { name: "选择版本" }), { target: { value: "rel-2" } });
    await waitFor(() => expect(screen.getByTestId("dashboard-components")).toHaveTextContent("sdk-4.0"));
    expect(screen.getByTestId("dashboard-components")).toHaveTextContent("2027.01");
    expect(screen.getByTestId("dashboard-components")).not.toHaveTextContent("3.3");
  });

  it("reads current component information after an explicit refresh", async () => {
    const payload = makePayload();
    vi.mocked(apiGet).mockResolvedValue(payload);
    renderDashboard(makeQueryClient());
    await screen.findByTestId("dashboard-components");
    vi.mocked(apiGet).mockResolvedValue({ ...payload, release: {
      ...payload.release!, environment: [{ component: "Driver", version: "driver-updated", note: "更新说明" }],
    } });
    fireEvent.click(screen.getByTestId("refresh-btn"));
    await waitFor(() => expect(screen.getByTestId("dashboard-components")).toHaveTextContent("driver-updated"));
  });

  it("explains when the selected cycle has no component information", async () => {
    const payload = makePayload();
    payload.release!.environment = [];
    vi.mocked(apiGet).mockResolvedValue(payload);
    renderDashboard(makeQueryClient());
    expect(await screen.findByTestId("dashboard-components")).toHaveTextContent("RM 尚未填写该版本的组件信息");
  });

  it("explains when no cycle exists", async () => {
    vi.mocked(apiGet).mockResolvedValue(makePayload({ release: null, releases: [] }));
    renderDashboard(makeQueryClient());
    expect(await screen.findByTestId("dashboard-components")).toHaveTextContent("还没有版本");
  });

  it.each(["RM", "Owner", "QA", "Guest"])("shows read-only component information for %s", async (role) => {
    vi.mocked(useAuth).mockReturnValue({ user: { username: "alice", role, display_name: "Alice" } } as ReturnType<typeof useAuth>);
    vi.mocked(apiGet).mockResolvedValue(makePayload());
    renderDashboard(makeQueryClient());
    const panel = await screen.findByTestId("dashboard-components");
    expect(panel.querySelector("button, input, select")).toBeNull();
    expect(panel).toHaveTextContent("MACA SDK");
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
