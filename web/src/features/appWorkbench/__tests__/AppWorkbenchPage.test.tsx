/**
 * Tests for AppWorkbenchPage.
 *
 * Verifies:
 *  - App list renders rows from state payload
 *  - Search box filters the app list
 *  - Own-only checkbox filters to current user's apps
 *  - Selecting an app shows detail panel
 *  - Non-selected state shows detail-empty placeholder
 *  - RM sees "+ 新增" button; Owner with own apps does not see new-app button when release locked
 *  - Loading state shows "加载中…"
 *  - Error state shows error message
 *  - Detail panel shows release decision select
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AppWorkbenchPage } from "../AppWorkbenchPage";
import type { StatePayload, App, Snapshot, ReleaseDetail, ReleaseSummary } from "../../../types";

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

import { apiGet } from "../../../api/http";
import { useAuth } from "../../../api/AuthContext";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeApp(id: string): App {
  return { id, git_url: `repo/${id}`, git_branch: "main", created_by: "admin", created_at: "2026-01-01", aliases: [] };
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
    qa_status: "not_checked",
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
    created_at: "2026-01-01",
    source: "manual",
    cloned_from: "",
    phase: "before_app_freeze",
    snapshots,
  };
}

function makeReleaseSummary(): ReleaseSummary {
  return {
    id: "rel-1", name: "3.0", maca_version: "3.0",
    app_freeze_deadline: "2026-06-01", doc_deadline: "2026-07-01",
    released_locked: false, released_locked_at: "", released_locked_by: "",
    created_at: "2026-01-01", source: "manual", cloned_from: "",
    phase: "before_app_freeze",
  };
}

function makePayload(overrides: Partial<StatePayload> = {}): StatePayload {
  const app1 = makeApp("app1");
  const app2 = makeApp("app2");
  const snap1 = makeSnap("app1", { official_name: "AlphaApp", owners: ["alice"] });
  const snap2 = makeSnap("app2", { official_name: "BetaTool", release_decision: "cicd_only", owners: ["bob"] });

  return {
    apps: [app1, app2],
    releases: [makeReleaseSummary()],
    release: makeRelease({ app1: snap1, app2: snap2 }),
    artifacts: [],
    user: { username: "alice", role: "RM", display_name: "Alice" },
    user_display_names: { alice: "Alice Smith", bob: "Bob Jones" },
    qa_log: null,
    qa_audit_logs: {},
    release_schedule: [],
    ...overrides,
  };
}

function makeQueryClient() {
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

function renderPage(queryClient: QueryClient) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AppWorkbenchPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
    user: { username: "alice", role: "RM", display_name: "Alice" },
    ldapStatus: { enabled: false, uri: "" },
    login: vi.fn(),
    logout: vi.fn(),
    clearUser: vi.fn(),
  });
});

describe("AppWorkbenchPage", () => {
  it("shows loading state while fetching", () => {
    (apiGet as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    const qc = makeQueryClient();
    renderPage(qc);
    expect(screen.getByText("加载中…")).toBeTruthy();
  });

  it("shows error state when api fails", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("连接失败"));
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => {
      expect(screen.getByText(/加载失败/)).toBeTruthy();
    });
  });

  it("renders app list rows after data loads", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => {
      expect(screen.getByTestId("app-row-app1")).toBeTruthy();
      expect(screen.getByTestId("app-row-app2")).toBeTruthy();
    });
  });

  it("shows correct app count", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => {
      expect(screen.getByTestId("app-count").textContent).toContain("2");
    });
  });

  it("detail empty when no app selected", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => {
      expect(screen.getByTestId("detail-empty")).toBeTruthy();
    });
  });

  it("shows detail panel on app click", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => {
      expect(screen.getByTestId("detail-panel")).toBeTruthy();
    });
  });

  it("shows official name in detail panel header", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => {
      expect(screen.getByTestId("detail-panel").textContent).toContain("AlphaApp");
    });
  });

  it("search box filters app list", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-search"));
    fireEvent.change(screen.getByTestId("app-search"), { target: { value: "alpha" } });
    await waitFor(() => {
      expect(screen.getByTestId("app-row-app1")).toBeTruthy();
      expect(screen.queryByTestId("app-row-app2")).toBeNull();
    });
  });

  it("own-only checkbox visible for Owner role", async () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { username: "alice", role: "Owner", display_name: "Alice" },
      ldapStatus: { enabled: false, uri: "" },
      login: vi.fn(),
      logout: vi.fn(),
      clearUser: vi.fn(),
    });
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(
      makePayload({ user: { username: "alice", role: "Owner", display_name: "Alice" } }),
    );
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => {
      expect(screen.getByTestId("own-only-checkbox")).toBeTruthy();
    });
  });

  it("own-only filters to alice's apps", async () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { username: "alice", role: "Owner", display_name: "Alice" },
      ldapStatus: { enabled: false, uri: "" },
      login: vi.fn(),
      logout: vi.fn(),
      clearUser: vi.fn(),
    });
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(
      makePayload({ user: { username: "alice", role: "Owner", display_name: "Alice" } }),
    );
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("own-only-checkbox"));
    fireEvent.click(screen.getByTestId("own-only-checkbox"));
    await waitFor(() => {
      // alice owns app1 only
      expect(screen.getByTestId("app-row-app1")).toBeTruthy();
      expect(screen.queryByTestId("app-row-app2")).toBeNull();
    });
  });

  it("RM sees + 新增 button when release not locked", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => {
      expect(screen.getByTestId("new-app-btn")).toBeTruthy();
    });
  });

  it("no + 新增 button when release is locked", async () => {
    const lockedRelease = makeRelease({
      app1: makeSnap("app1"),
    });
    lockedRelease.released_locked = true;
    const lockedSummary = { ...makeReleaseSummary(), released_locked: true };
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(
      makePayload({ release: lockedRelease, releases: [lockedSummary] }),
    );
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-table"));
    expect(screen.queryByTestId("new-app-btn")).toBeNull();
  });

  it("release decision shows correct label in decision pill", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-table"));
    // app2 has cicd_only — should show the short label
    const row2 = screen.getByTestId("app-row-app2");
    expect(row2.textContent).toContain("cicd_only");
  });
});
