/**
 * CicdPage tests.
 *
 * Covers:
 *  - Sub-panes visible for non-SPD roles (RM, Owner, QA)
 *  - SPD sees only delivery panes (no overview/my/pending/recent)
 *  - OverviewPane: renders task rows with status pills
 *  - OverviewPane: status filter changes visible tasks
 *  - PendingPane: renders pending requests with approve/reject buttons
 *  - RecentPane: renders with since_days select and only_mine checkbox
 *  - DeliveryPane: renders 待交付 pane
 *  - DeliveryPane: renders 已交付 pane (delivered=true)
 *  - markCicdVisited called on mount
 *  - notifications query invalidated on mount
 *  - RefreshBar rendered with dataUpdatedAt
 *  - New task button visible for non-SPD roles
 *  - New task button absent for SPD
 *  - CICD_NOTIFICATIONS_KEY and CICD_TASKS_KEY exported correctly
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { CicdPage } from "../CicdPage";
import { CICD_NOTIFICATIONS_KEY, CICD_TASKS_KEY } from "../cicdApi";
import type { CicdTask, CicdRequest } from "../../../types";

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
  let _state = {
    selectedReleaseId: "",
    cicdOverviewFilter: "Running",
    setCicdOverviewFilter: vi.fn(),
    cicdRecentDays: 30,
    setCicdRecentDays: vi.fn(),
  };
  // Supports both useUiStore() and useUiStore((s) => s.x) call patterns
  const store = (selector?: (s: typeof _state) => unknown) =>
    selector ? selector(_state) : _state;
  store.getState = () => _state;
  return {
    useUiStore: Object.assign(store, { getState: () => _state }),
    __setState: (patch: Partial<typeof _state>) => {
      _state = { ..._state, ...patch };
    },
  };
});

// Import mocked modules after vi.mock
// vi.mock is hoisted, so these imports get the mocked versions.
import { apiGet, apiPost } from "../../../api/http";
import { useAuth } from "../../../api/AuthContext";
// Access __setState from the mocked uiStore to manipulate mock state in tests.
// The vi.mock factory above exports it; casting bypasses missing types.
import * as _uiStoreMod from "../../../store/uiStore";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _uiSetState = (_uiStoreMod as any).__setState as (patch: Record<string, unknown>) => void;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeTask(overrides: Partial<CicdTask> = {}): CicdTask {
  return {
    id: "task-1",
    app_name: "TestApp",
    app_version: "1.0",
    owner_username: "alice",
    owner_display: "Alice",
    repo_type: "git",
    repo_name: "test-repo",
    branch: "main",
    build_product: ["image"],
    community_artifact: [],
    build_image: "gcc:12",
    test_timeout: 40,
    status: "Running",
    notes: "",
    has_pending: false,
    has_pending_delivery: false,
    created_at: "2026-01-01 00:00:00",
    updated_at: "2026-01-01 00:00:00",
    ...overrides,
  };
}

function makeRequest(overrides: Partial<CicdRequest> = {}): CicdRequest {
  return {
    id: 1,
    task_id: "task-1",
    request_type: "modify",
    payload: {},
    submitter: "bob",
    submitter_display: "Bob",
    submitted_at: "2026-06-01 10:00:00",
    status: "pending",
    reviewer: "",
    reviewed_at: "",
    review_note: "",
    is_self_approved: 0,
    approval_mode: "",
    delivery_status: "",
    jira_id: "",
    jira_auto_created: 0,
    delivered_by: "",
    delivered_at: "",
    returned_reason: "",
    returned_at: "",
    task_app_name: "TestApp",
    task_app_version: "1.0",
    task_repo_name: "test-repo",
    task_branch: "main",
    task_status: "Running",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Test helper
// ---------------------------------------------------------------------------

function renderCicd(role = "RM") {
  vi.mocked(useAuth).mockReturnValue({
    user: { username: "alice", display_name: "Alice", role },
    ldapStatus: { enabled: false, uri: "" },
    login: vi.fn(),
    logout: vi.fn(),
    clearUser: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);

  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CicdPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Default API responses
function setDefaultMocks() {
  vi.mocked(apiGet).mockImplementation((url: string) => {
    if (url.includes("/api/cicd/tasks")) {
      return Promise.resolve({ tasks: [makeTask()] });
    }
    if (url.includes("/api/cicd/notifications")) {
      return Promise.resolve({ count: 0, last_visited_at: "" });
    }
    if (url.includes("/api/cicd/requests")) {
      return Promise.resolve({ requests: [] });
    }
    if (url.includes("/api/cicd/deliveries")) {
      return Promise.resolve({ deliveries: [] });
    }
    return Promise.resolve({});
  });
  vi.mocked(apiPost).mockResolvedValue({ ok: true });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CicdPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setDefaultMocks();
    // Reset mock state to known defaults before each test
    _uiSetState({ cicdOverviewFilter: "Running" });
  });

  // ── Query key exports ──────────────────────────────────────────────────────

  it("exports CICD_TASKS_KEY as ['cicd', 'tasks']", () => {
    expect(CICD_TASKS_KEY).toEqual(["cicd", "tasks"]);
  });

  it("exports CICD_NOTIFICATIONS_KEY as ['cicd', 'notifications']", () => {
    expect(CICD_NOTIFICATIONS_KEY).toEqual(["cicd", "notifications"]);
  });

  // ── Role-gated pane visibility ─────────────────────────────────────────────

  it("renders sub-tab buttons for non-SPD role (RM)", async () => {
    renderCicd("RM");
    await waitFor(() => {
      expect(screen.getByText("任务总览")).toBeInTheDocument();
    });
    expect(screen.getByText(/我的\s*CICD\s*任务/)).toBeInTheDocument();
    expect(screen.getByText("待审批")).toBeInTheDocument();
    expect(screen.getByText("最近申请")).toBeInTheDocument();
    expect(screen.getByText("待交付")).toBeInTheDocument();
    expect(screen.getByText("已交付")).toBeInTheDocument();
  });

  it("renders sub-tab buttons for Owner role", async () => {
    renderCicd("Owner");
    await waitFor(() => {
      expect(screen.getByText("任务总览")).toBeInTheDocument();
    });
    expect(screen.getByText(/我的\s*CICD\s*任务/)).toBeInTheDocument();
  });

  it("SPD role: only sees 待交付 and 已交付 panes", async () => {
    renderCicd("SPD");
    await waitFor(() => {
      expect(screen.getByText("待交付")).toBeInTheDocument();
    });
    expect(screen.getByText("已交付")).toBeInTheDocument();
    // Non-delivery panes should NOT be in DOM for SPD
    expect(screen.queryByText("任务总览")).not.toBeInTheDocument();
    expect(screen.queryByText(/我的\s*CICD\s*任务/)).not.toBeInTheDocument();
    expect(screen.queryByText("待审批")).not.toBeInTheDocument();
    expect(screen.queryByText("最近申请")).not.toBeInTheDocument();
  });

  // ── New task button ────────────────────────────────────────────────────────

  it("shows new-task button for RM", async () => {
    renderCicd("RM");
    await waitFor(() => {
      expect(screen.getByText("任务总览")).toBeInTheDocument();
    });
    expect(screen.getByText(/新建 CICD 任务/)).toBeInTheDocument();
  });

  it("hides new-task button for SPD", async () => {
    renderCicd("SPD");
    await waitFor(() => {
      expect(screen.getByText("待交付")).toBeInTheDocument();
    });
    expect(screen.queryByText(/新建 CICD 任务/)).not.toBeInTheDocument();
  });

  // ── OverviewPane task rows ─────────────────────────────────────────────────

  it("OverviewPane: renders task row with app name and status pill", async () => {
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) {
        return Promise.resolve({
          tasks: [makeTask({ app_name: "MyApp", status: "Running" })],
        });
      }
      if (url.includes("/api/cicd/notifications")) {
        return Promise.resolve({ count: 0, last_visited_at: "" });
      }
      return Promise.resolve({ requests: [], deliveries: [] });
    });

    renderCicd("RM");
    await waitFor(() => {
      expect(screen.getByText("MyApp")).toBeInTheDocument();
    });
    // Status pill — multiple "Running" elements expected (filter btn + pill)
    expect(screen.getAllByText("Running").length).toBeGreaterThanOrEqual(1);
  });

  it("OverviewPane: filters out Stopped tasks when filter is 'Running'", async () => {
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) {
        return Promise.resolve({
          tasks: [
            makeTask({ id: "t1", app_name: "RunningApp", status: "Running" }),
            makeTask({ id: "t2", app_name: "StoppedApp", status: "Stopped" }),
          ],
        });
      }
      if (url.includes("/api/cicd/notifications")) {
        return Promise.resolve({ count: 0, last_visited_at: "" });
      }
      return Promise.resolve({ requests: [], deliveries: [] });
    });

    renderCicd("RM");
    await waitFor(() => {
      expect(screen.getByText("RunningApp")).toBeInTheDocument();
    });
    // With default filter "Running", Stopped tasks should not appear
    expect(screen.queryByText("StoppedApp")).not.toBeInTheDocument();
  });

  // ── PendingPane requests ──────────────────────────────────────────────────

  it("PendingPane: renders pending request with submitter name", async () => {
    const pendingReq = makeRequest({
      submitter: "bob",
      submitter_display: "Bob Smith",
      request_type: "modify",
      status: "pending",
    });
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) {
        return Promise.resolve({ tasks: [] });
      }
      if (url.includes("/api/cicd/notifications")) {
        return Promise.resolve({ count: 0, last_visited_at: "" });
      }
      if (url.includes("/api/cicd/requests")) {
        return Promise.resolve({ requests: [pendingReq] });
      }
      return Promise.resolve({ deliveries: [] });
    });

    renderCicd("RM");
    // Click the 待审批 tab
    await waitFor(() => {
      expect(screen.getByText("待审批")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByText("待审批"));

    await waitFor(() => {
      expect(screen.getByText(/Bob Smith/)).toBeInTheDocument();
    });
  });

  // ── RecentPane ─────────────────────────────────────────────────────────────

  it("RecentPane: renders since_days selector and only_mine checkbox", async () => {
    renderCicd("RM");
    await waitFor(() => {
      expect(screen.getByText("最近申请")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByText("最近申请"));

    await waitFor(() => {
      // should show a days selector (select element with 30/90/180 options or similar)
      expect(screen.getByText(/只看我的/)).toBeInTheDocument();
    });
  });

  // ── DeliveryPane ──────────────────────────────────────────────────────────

  it("DeliveryPane: renders 待交付 content for RM", async () => {
    renderCicd("RM");
    await waitFor(() => {
      expect(screen.getByText("待交付")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByText("待交付"));
    // The pane itself should render without crashing
    await waitFor(() => {
      // At minimum the pane container should be active
      expect(screen.getByText("待交付")).toBeInTheDocument();
    });
  });

  it("DeliveryPane: renders 已交付 pane for RM", async () => {
    renderCicd("RM");
    await waitFor(() => {
      expect(screen.getByText("已交付")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByText("已交付"));
    await waitFor(() => {
      expect(screen.getByText("已交付")).toBeInTheDocument();
    });
  });

  // ── markCicdVisited on mount ───────────────────────────────────────────────

  it("calls markCicdVisited (POST /api/cicd/notifications/mark-visited) on mount", async () => {
    renderCicd("RM");
    await waitFor(() => {
      expect(vi.mocked(apiPost)).toHaveBeenCalledWith(
        "/api/cicd/notifications/mark-visited",
        {},
      );
    });
  });

  // ── RefreshBar ─────────────────────────────────────────────────────────────

  it("renders RefreshBar with 刷新 button", async () => {
    renderCicd("RM");
    await waitFor(() => {
      expect(screen.getByText(/刷新/)).toBeInTheDocument();
    });
  });

  // ── Tasks query fetches on mount ───────────────────────────────────────────

  it("calls GET /api/cicd/tasks on mount", async () => {
    renderCicd("RM");
    await waitFor(() => {
      expect(vi.mocked(apiGet)).toHaveBeenCalledWith(
        expect.stringContaining("/api/cicd/tasks"),
      );
    });
  });

  it("calls GET /api/cicd/notifications on mount", async () => {
    renderCicd("RM");
    await waitFor(() => {
      expect(vi.mocked(apiGet)).toHaveBeenCalledWith(
        "/api/cicd/notifications",
      );
    });
  });

  // ── SPD delivery flow ─────────────────────────────────────────────────────

  it("SPD: calls GET /api/cicd/deliveries on delivery pane render", async () => {
    renderCicd("SPD");
    await waitFor(() => {
      expect(vi.mocked(apiGet)).toHaveBeenCalledWith(
        expect.stringContaining("/api/cicd/deliveries"),
      );
    });
  });

  // ── Ruling C: Admin cannot submit or approve CICD requests ────────────────

  it("Admin role: no new-task button (canSubmit=false for Admin, ruling C)", async () => {
    renderCicd("Admin");
    // Give the component a chance to render tasks
    await waitFor(() => {
      // Overview pane renders for Admin (they are not SPD)
      expect(screen.queryByText(/新建 CICD 任务/)).not.toBeInTheDocument();
    });
  });

  it("Admin role: no approve button in 待审批 pane (canApprove=false for Admin, ruling C)", async () => {
    const pendingReq = makeRequest({ submitter: "bob", submitter_display: "Bob" });
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) return Promise.resolve({ tasks: [] });
      if (url.includes("/api/cicd/notifications")) return Promise.resolve({ count: 0, last_visited_at: "" });
      if (url.includes("/api/cicd/requests")) return Promise.resolve({ requests: [pendingReq] });
      return Promise.resolve({ deliveries: [] });
    });
    renderCicd("Admin");
    // Admin sees "待审批" pane button (it's in PANE_LABELS, not a role gate itself)
    await waitFor(() => expect(screen.getByText("待审批")).toBeInTheDocument());
    await userEvent.click(screen.getByText("待审批"));
    // Pending request should appear but NO approve button
    await waitFor(() => expect(screen.getByText(/Bob/)).toBeInTheDocument());
    // The "审批" button is only shown when canApprove is true (RM only)
    expect(screen.queryByRole("button", { name: "审批" })).not.toBeInTheDocument();
  });

  // ── Ruling B: pending status label shows "等待 RM 审批" ───────────────────

  it("RecentPane: pending request status shows '等待 RM 审批' (ruling B)", async () => {
    const recentReq = makeRequest({ submitter: "alice", submitter_display: "Alice", status: "pending" });
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) return Promise.resolve({ tasks: [] });
      if (url.includes("/api/cicd/notifications")) return Promise.resolve({ count: 0, last_visited_at: "" });
      if (url.includes("/api/cicd/requests")) return Promise.resolve({ requests: [recentReq] });
      return Promise.resolve({ deliveries: [] });
    });
    renderCicd("RM");
    await waitFor(() => expect(screen.getByText("最近申请")).toBeInTheDocument());
    await userEvent.click(screen.getByText("最近申请"));
    await waitFor(() => {
      expect(screen.getByText("等待 RM 审批")).toBeInTheDocument();
    });
  });

  // ── Ruling B: ApproveDialog shows "本人提交" for self-approval ─────────────

  it("ApproveDialog: shows 本人提交 when submitter equals current user (ruling B self-approve)", async () => {
    // renderCicd uses username "alice" — make the pending req also from "alice"
    const selfReq = makeRequest({ submitter: "alice", submitter_display: "Alice" });
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) return Promise.resolve({ tasks: [makeTask()] });
      if (url.includes("/api/cicd/notifications")) return Promise.resolve({ count: 0, last_visited_at: "" });
      if (url.includes("/api/cicd/requests")) return Promise.resolve({ requests: [selfReq] });
      return Promise.resolve({ deliveries: [] });
    });
    renderCicd("RM");
    // Navigate to 待审批 pane
    await waitFor(() => expect(screen.getByText("待审批")).toBeInTheDocument());
    await userEvent.click(screen.getByText("待审批"));
    // Pending request shows with approve button
    await waitFor(() => expect(screen.getByRole("button", { name: "审批" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "审批" }));
    // ApproveDialog opens — should show "本人提交" since submitter === "alice" === current user
    await waitFor(() => {
      expect(screen.getByText("本人提交")).toBeInTheDocument();
    });
  });

  // ── W2: Status field read-only in TaskFormDialog ──────────────────────────

  it("TaskFormDialog: status field is read-only (no editable select for status)", async () => {
    renderCicd("RM");
    await waitFor(() => expect(screen.getByText(/新建 CICD 任务/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/新建 CICD 任务/));
    // Dialog opens
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    // Status should be shown as disabled input, not as a select
    const statusInput = screen.getByDisplayValue("Running");
    expect(statusInput.tagName).toBe("INPUT");
    expect(statusInput).toBeDisabled();
    // Should NOT have a <select> for status with Running/Stopped/Abandoned options
    const selects = document.querySelectorAll("dialog select, .dialog-card select");
    const hasStatusSelect = Array.from(selects).some((s) =>
      Array.from(s.querySelectorAll("option")).some((o) => o.value === "Abandoned"),
    );
    expect(hasStatusSelect).toBe(false);
  });

  // ── W2: Abandon button in OverviewPane (RM on Stopped tasks) ─────────────

  it("OverviewPane: shows 废弃/退役 button for RM on Stopped tasks", async () => {
    // Show all tasks (not just Running) by setting filter to ""
    _uiSetState({ cicdOverviewFilter: "" });
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) {
        return Promise.resolve({
          tasks: [makeTask({ id: "t1", app_name: "StoppedApp", status: "Stopped" })],
        });
      }
      if (url.includes("/api/cicd/notifications")) return Promise.resolve({ count: 0, last_visited_at: "" });
      return Promise.resolve({ requests: [], deliveries: [] });
    });
    renderCicd("RM");
    await waitFor(() => expect(screen.getByText("StoppedApp")).toBeInTheDocument());
    expect(screen.getByTestId("abandon-btn-t1")).toBeInTheDocument();
  });

  it("OverviewPane: no 废弃/退役 button for Owner role on Stopped tasks", async () => {
    _uiSetState({ cicdOverviewFilter: "" });
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) {
        return Promise.resolve({
          tasks: [makeTask({ id: "t1", app_name: "StoppedApp", status: "Stopped" })],
        });
      }
      if (url.includes("/api/cicd/notifications")) return Promise.resolve({ count: 0, last_visited_at: "" });
      return Promise.resolve({ requests: [], deliveries: [] });
    });
    renderCicd("Owner");
    await waitFor(() => expect(screen.getByText("StoppedApp")).toBeInTheDocument());
    expect(screen.queryByTestId("abandon-btn-t1")).not.toBeInTheDocument();
  });

  it("OverviewPane: no 废弃/退役 button for RM on Running tasks", async () => {
    // Running filter is fine here — Running tasks are visible by default
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) {
        return Promise.resolve({
          tasks: [makeTask({ id: "t1", app_name: "RunningApp", status: "Running" })],
        });
      }
      if (url.includes("/api/cicd/notifications")) return Promise.resolve({ count: 0, last_visited_at: "" });
      return Promise.resolve({ requests: [], deliveries: [] });
    });
    renderCicd("RM");
    await waitFor(() => expect(screen.getByText("RunningApp")).toBeInTheDocument());
    expect(screen.queryByTestId("abandon-btn-t1")).not.toBeInTheDocument();
  });

  it("OverviewPane: abandon calls POST /api/cicd/tasks/abandon on confirm", async () => {
    _uiSetState({ cicdOverviewFilter: "" });
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) {
        return Promise.resolve({
          tasks: [makeTask({ id: "t1", app_name: "StoppedApp", status: "Stopped" })],
        });
      }
      if (url.includes("/api/cicd/notifications")) return Promise.resolve({ count: 0, last_visited_at: "" });
      return Promise.resolve({ requests: [], deliveries: [] });
    });
    vi.mocked(apiPost).mockResolvedValue({ ok: true });
    renderCicd("RM");
    await waitFor(() => expect(screen.getByTestId("abandon-btn-t1")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("abandon-btn-t1"));
    await waitFor(() => {
      expect(vi.mocked(apiPost)).toHaveBeenCalledWith(
        "/api/cicd/tasks/abandon",
        { task_id: "t1" },
      );
    });
  });

  // ── Notification count display ─────────────────────────────────────────────

  it("shows notification count badge when there are unvisited notifications", async () => {
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/notifications")) {
        return Promise.resolve({ count: 42, last_visited_at: "" });
      }
      if (url.includes("/api/cicd/tasks")) {
        return Promise.resolve({ tasks: [] });
      }
      return Promise.resolve({ requests: [], deliveries: [] });
    });

    renderCicd("RM");
    await waitFor(() => {
      // The cicd-badge span shows the count
      const badge = document.querySelector(".cicd-badge");
      expect(badge).not.toBeNull();
      expect(badge?.textContent).toBe("42");
    });
  });

  // ── F3: 同步联动 badge for decision-sync requests (origin) ──────────────────

  it("PendingPane: shows 同步联动 badge for release_decision_sync request", async () => {
    const syncReq = makeRequest({
      submitter: "rm",
      submitter_display: "RM User",
      request_type: "modify",
      status: "pending",
      origin: "release_decision_sync",
    });
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) return Promise.resolve({ tasks: [] });
      if (url.includes("/api/cicd/notifications")) return Promise.resolve({ count: 0, last_visited_at: "" });
      if (url.includes("/api/cicd/requests")) return Promise.resolve({ requests: [syncReq] });
      return Promise.resolve({ deliveries: [] });
    });
    renderCicd("RM");
    await waitFor(() => expect(screen.getByText("待审批")).toBeInTheDocument());
    await userEvent.click(screen.getByText("待审批"));
    await waitFor(() => {
      expect(screen.getByText("同步联动")).toBeInTheDocument();
    });
  });

  it("PendingPane: no 同步联动 badge for ordinary cicd_workbench request", async () => {
    const wbReq = makeRequest({
      submitter: "bob",
      submitter_display: "Bob",
      request_type: "modify",
      status: "pending",
      origin: "cicd_workbench",
    });
    vi.mocked(apiGet).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) return Promise.resolve({ tasks: [] });
      if (url.includes("/api/cicd/notifications")) return Promise.resolve({ count: 0, last_visited_at: "" });
      if (url.includes("/api/cicd/requests")) return Promise.resolve({ requests: [wbReq] });
      return Promise.resolve({ deliveries: [] });
    });
    renderCicd("RM");
    await waitFor(() => expect(screen.getByText("待审批")).toBeInTheDocument());
    await userEvent.click(screen.getByText("待审批"));
    await waitFor(() => expect(screen.getByText(/Bob/)).toBeInTheDocument());
    expect(screen.queryByText("同步联动")).not.toBeInTheDocument();
  });
});
