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

// ---------------------------------------------------------------------------
// F1 / F2 / F3 — decision-sync dialog, copy-from-version, unsaved guard
// ---------------------------------------------------------------------------

import { apiPost } from "../../../api/http";
import { useUiStore } from "../../../store/uiStore";

function twoReleaseSummaries(): ReleaseSummary[] {
  const later: ReleaseSummary = {
    ...makeReleaseSummary(), id: "rel-2", name: "3.1", created_at: "2026-02-01",
  };
  return [makeReleaseSummary(), later];
}

function payloadTwoReleases(): StatePayload {
  return makePayload({ releases: twoReleaseSummaries() });
}

async function enterEditOnApp1(): Promise<void> {
  await waitFor(() => screen.getByTestId("app-row-app1"));
  fireEvent.click(screen.getByTestId("app-row-app1"));
  await waitFor(() => screen.getByText("✎ 修改"));
  fireEvent.click(screen.getByText("✎ 修改"));
  await waitFor(() => screen.getByTestId("field-decision"));
}

describe("AppWorkbenchPage F1 decision-sync dialog", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  it("opens the owner-choice dialog with a gated cicd_only row when decision changes", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes("decision-sync/preview")) {
        return Promise.resolve({
          decision: "stopped",
          releases: [
            { release_id: "rel-2", release_name: "3.1", phase_label: "App 冻结后",
              resulting_decision: "stopped", skipped: false },
          ],
        });
      }
      return Promise.resolve({ snapshot: {}, missing_items: [] });
    });
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "stopped" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => screen.getByTestId("decision-sync-dialog"));
    expect(screen.getByTestId("decision-sync-dialog").textContent).toContain("同步 release 决策到后续 release");
    expect(screen.getByTestId("sync-row-rel-2").textContent).toContain("调整为 stopped");
  });

  it("shows the gated cicd_only downgrade row distinctly", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes("decision-sync/preview")) {
        return Promise.resolve({
          decision: "release",
          releases: [
            { release_id: "rel-2", release_name: "3.1", phase_label: "App 冻结后",
              resulting_decision: "cicd_only", skipped: false },
          ],
        });
      }
      return Promise.resolve({ snapshot: {}, missing_items: [] });
    });
    const qc = makeQueryClient();
    // app1 starts cicd_only so changing to release triggers the gate
    const payload = payloadTwoReleases();
    (payload.release as ReleaseDetail).snapshots.app1.release_decision = "cicd_only";
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "release" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => screen.getByTestId("sync-row-rel-2"));
    expect(screen.getByTestId("sync-row-rel-2").textContent).toContain("冻结期降级");
  });

  it("同步到后续 release posts update with sync_decision=true", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    const postMock = vi.fn((url: string, _body?: unknown) => {
      if (url.includes("decision-sync/preview")) {
        return Promise.resolve({
          decision: "stopped",
          releases: [{ release_id: "rel-2", release_name: "3.1", phase_label: "App 冻结前",
            resulting_decision: "stopped", skipped: false }],
        });
      }
      return Promise.resolve({ snapshot: {}, missing_items: [] });
    });
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(postMock);
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "stopped" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => screen.getByTestId("sync-all"));
    fireEvent.click(screen.getByTestId("sync-all"));
    await waitFor(() => {
      const updateCall = postMock.mock.calls.find((c) => c[0] === "/api/apps/update");
      expect(updateCall).toBeTruthy();
      expect((updateCall![1] as { sync_decision: boolean }).sync_decision).toBe(true);
    });
  });

  it("不同步，仅本 release posts update with sync_decision=false", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    const postMock = vi.fn((url: string, _body?: unknown) => {
      if (url.includes("decision-sync/preview")) {
        return Promise.resolve({
          decision: "stopped",
          releases: [{ release_id: "rel-2", release_name: "3.1", phase_label: "App 冻结前",
            resulting_decision: "stopped", skipped: false }],
        });
      }
      return Promise.resolve({ snapshot: {}, missing_items: [] });
    });
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(postMock);
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "stopped" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => screen.getByTestId("sync-local-only"));
    fireEvent.click(screen.getByTestId("sync-local-only"));
    await waitFor(() => {
      const updateCall = postMock.mock.calls.find((c) => c[0] === "/api/apps/update");
      expect(updateCall).toBeTruthy();
      expect((updateCall![1] as { sync_decision: boolean }).sync_decision).toBe(false);
    });
  });

  it("取消 aborts without posting an update", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    const postMock = vi.fn((url: string, _body?: unknown) => {
      if (url.includes("decision-sync/preview")) {
        return Promise.resolve({
          decision: "stopped",
          releases: [{ release_id: "rel-2", release_name: "3.1", phase_label: "App 冻结前",
            resulting_decision: "stopped", skipped: false }],
        });
      }
      return Promise.resolve({ snapshot: {}, missing_items: [] });
    });
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(postMock);
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "stopped" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => screen.getByTestId("sync-cancel"));
    fireEvent.click(screen.getByTestId("sync-cancel"));
    await waitFor(() => {
      expect(screen.queryByTestId("decision-sync-dialog")).toBeNull();
    });
    expect(postMock.mock.calls.find((c) => c[0] === "/api/apps/update")).toBeFalsy();
  });
});

describe("AppWorkbenchPage F2 copy-from-version", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  it("copies editable fields from the picked release into the form", async () => {
    const base = payloadTwoReleases();
    const other = makePayload({ releases: twoReleaseSummaries() });
    const otherRelease = makeRelease({
      app1: makeSnap("app1", { official_name: "AlphaApp", description: "COPIED-DESC",
        doc: { intro: "COPIED-INTRO", image_usage: "", binary_usage: "", env_setup: "", limitations: "" } }),
    });
    otherRelease.id = "rel-2";
    other.release = otherRelease;

    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((url: string) =>
      Promise.resolve(url.includes("rel-2") ? other : base),
    );
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.click(screen.getByTestId("copy-from-version-btn"));
    await waitFor(() => screen.getByTestId("copy-from-version-dialog"));
    fireEvent.change(screen.getByTestId("copy-source-select"), { target: { value: "rel-2" } });
    fireEvent.click(screen.getByTestId("copy-confirm"));
    await waitFor(() => {
      expect((screen.getByTestId("field-description") as HTMLTextAreaElement).value).toBe("COPIED-DESC");
    });
    expect((screen.getByTestId("field-doc-intro") as HTMLTextAreaElement).value).toBe("COPIED-INTRO");
  });

  it("shows a friendly message when the app is absent in the picked release", async () => {
    const alertSpy = vi.fn();
    vi.stubGlobal("alert", alertSpy);
    const base = payloadTwoReleases();
    const other = makePayload({ releases: twoReleaseSummaries() });
    const otherRelease = makeRelease({}); // no app1
    otherRelease.id = "rel-2";
    other.release = otherRelease;
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((url: string) =>
      Promise.resolve(url.includes("rel-2") ? other : base),
    );
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.click(screen.getByTestId("copy-from-version-btn"));
    await waitFor(() => screen.getByTestId("copy-confirm"));
    fireEvent.click(screen.getByTestId("copy-confirm"));
    await waitFor(() => {
      expect(alertSpy.mock.calls.some((c) => String(c[0]).includes("没有此 app"))).toBe(true);
    });
  });
});

// ---------------------------------------------------------------------------
// W2 — CicdLinkCard + decision-change preview
// ---------------------------------------------------------------------------

import type { CicdTask } from "../../../types";

function makeCicdTask(overrides: Partial<CicdTask> = {}): CicdTask {
  return {
    id: "task-99",
    app_name: "AlphaApp",
    app_version: "1.0",
    owner_username: "alice",
    owner_display: "Alice",
    repo_type: "git",
    repo_name: "repo/app1",   // matches makeApp("app1").git_url
    branch: "main",            // matches makeApp("app1").git_branch
    build_product: ["maca"],
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

function mockApiGetWithCicd(cicdTasks: CicdTask[] = [makeCicdTask()]) {
  (apiGet as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    if (url.includes("/api/cicd/tasks")) {
      return Promise.resolve({ tasks: cicdTasks });
    }
    return Promise.resolve(makePayload());
  });
}

// Helper: click the CICD sub-tab in detail panel
async function clickCicdTab() {
  await waitFor(() => screen.getByTestId("detail-tab-cicd"));
  fireEvent.click(screen.getByTestId("detail-tab-cicd"));
  await waitFor(() => screen.getByTestId("detail-cicd-pane"));
}

describe("AppWorkbenchPage W2 CicdLinkCard", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  it("shows CicdLinkCard with status pill when task matches app identity", async () => {
    mockApiGetWithCicd([makeCicdTask({ status: "Running" })]);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    await clickCicdTab();
    await waitFor(() => {
      expect(screen.getByTestId("cicd-link-card")).toBeInTheDocument();
    });
    expect(screen.getByTestId("cicd-task-link").textContent).toContain("task-99");
    expect(screen.getByTestId("cicd-link-card").textContent).toContain("Running");
  });

  it("shows pending-approval banner in CicdLinkCard when task has_pending", async () => {
    mockApiGetWithCicd([makeCicdTask({ has_pending: true })]);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    await clickCicdTab();
    await waitFor(() => screen.getByTestId("cicd-link-card"));
    expect(screen.getByTestId("cicd-link-card").textContent).toContain("待审批");
  });

  it("does NOT show CicdLinkCard when no task matches app identity", async () => {
    mockApiGetWithCicd([makeCicdTask({ repo_name: "other-repo", branch: "other-branch" })]);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    await clickCicdTab();
    // Give the cicd query time to resolve — no matching task → no cicd-link-card
    await waitFor(() => expect(screen.queryByTestId("cicd-link-card")).not.toBeInTheDocument());
  });

  it("shows 'running/停止由本 app 决策决定' note in CicdLinkCard", async () => {
    mockApiGetWithCicd();
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    await clickCicdTab();
    await waitFor(() => screen.getByTestId("cicd-link-card"));
    expect(screen.getByTestId("cicd-link-card").textContent).toContain("运行/停止由本 app 决策决定");
  });
});

describe("AppWorkbenchPage W2 decision-change CICD preview", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  async function enterEditOnApp1WithCicd() {
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByText("✎ 修改"));
    fireEvent.click(screen.getByText("✎ 修改"));
    await waitFor(() => screen.getByTestId("field-decision"));
  }

  it("shows '待审批：CICD 任务将变为 Stopped' when decision changes to stopped", async () => {
    // app1 starts with release_decision "release" — changing to "stopped" → preview shows Stopped
    mockApiGetWithCicd();
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1WithCicd();
    // Change decision to stopped
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "stopped" } });
    await waitFor(() => {
      expect(screen.getByTestId("cicd-decision-preview")).toBeInTheDocument();
    });
    expect(screen.getByTestId("cicd-decision-preview").textContent).toContain("Stopped");
  });

  it("shows '待审批：CICD 任务将变为 Running' when decision changes from stopped to release", async () => {
    // Override app1 to start at "stopped"
    const stoppedPayload = makePayload();
    stoppedPayload.release!.snapshots.app1.release_decision = "stopped";
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) {
        return Promise.resolve({ tasks: [makeCicdTask({ status: "Stopped" })] });
      }
      return Promise.resolve(stoppedPayload);
    });
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1WithCicd();
    // Change decision from stopped → cicd_only (Running)
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "cicd_only" } });
    await waitFor(() => {
      expect(screen.getByTestId("cicd-decision-preview")).toBeInTheDocument();
    });
    expect(screen.getByTestId("cicd-decision-preview").textContent).toContain("Running");
  });

  it("does NOT show decision preview when no CICD task is linked", async () => {
    // No tasks returned → cicdTask is null → preview not shown
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes("/api/cicd/tasks")) return Promise.resolve({ tasks: [] });
      return Promise.resolve(makePayload());
    });
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1WithCicd();
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "stopped" } });
    await waitFor(() => {
      expect(screen.queryByTestId("cicd-decision-preview")).not.toBeInTheDocument();
    });
  });

  it("does NOT show decision preview when decision hasn't changed from original", async () => {
    // app1 starts "release" and user doesn't change it
    mockApiGetWithCicd();
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1WithCicd();
    // No decision change — preview should NOT appear
    expect(screen.queryByTestId("cicd-decision-preview")).not.toBeInTheDocument();
  });
});

describe("AppWorkbenchPage F3 unsaved-changes guard", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  it("sets the shared dirty flag when the form is edited", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    expect(useUiStore.getState().appDetailDirty).toBe(false);
    fireEvent.change(screen.getByTestId("field-description"), { target: { value: "edited" } });
    await waitFor(() => {
      expect(useUiStore.getState().appDetailDirty).toBe(true);
    });
  });

  it("registers a beforeunload handler that prevents unload while dirty", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.change(screen.getByTestId("field-description"), { target: { value: "edited" } });
    await waitFor(() => expect(useUiStore.getState().appDetailDirty).toBe(true));
    const evt = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(evt);
    expect(evt.defaultPrevented).toBe(true);
  });

  it("confirms before switching app when dirty", async () => {
    const confirmSpy = vi.fn(() => false); // user cancels the switch
    vi.stubGlobal("confirm", confirmSpy);
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.change(screen.getByTestId("field-description"), { target: { value: "edited" } });
    await waitFor(() => expect(useUiStore.getState().appDetailDirty).toBe(true));
    fireEvent.click(screen.getByTestId("app-row-app2"));
    expect(confirmSpy).toHaveBeenCalled();
    // cancelled → still on app1
    expect(useUiStore.getState().selectedApp).toBe("app1");
  });
});

// ---------------------------------------------------------------------------
// W3 — Sub-tabs + CICD-first new-app dialog
// ---------------------------------------------------------------------------


describe("AppWorkbenchPage W3 detail sub-tabs", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  it("shows 文档信息 and CICD sub-tab buttons after selecting an app", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    expect(screen.getByTestId("detail-tab-docs")).toBeInTheDocument();
    expect(screen.getByTestId("detail-tab-cicd")).toBeInTheDocument();
  });

  it("文档信息 tab is active by default and shows edit content", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    // docs tab is active → detail-cicd-pane not present
    expect(screen.queryByTestId("detail-cicd-pane")).not.toBeInTheDocument();
    // the ✎ 修改 button is visible on docs tab
    expect(screen.getByText("✎ 修改")).toBeInTheDocument();
  });

  it("clicking CICD tab shows CICD pane", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    fireEvent.click(screen.getByTestId("detail-tab-cicd"));
    await waitFor(() => screen.getByTestId("detail-cicd-pane"));
    expect(screen.getByTestId("detail-cicd-pane")).toBeInTheDocument();
  });

  it("CICD pane shows 无关联 message when no task matches", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    fireEvent.click(screen.getByTestId("detail-tab-cicd"));
    await waitFor(() => screen.getByTestId("detail-cicd-pane"));
    expect(screen.getByTestId("detail-cicd-pane").textContent).toContain("暂无关联 CICD 任务");
  });

  it("switching to a different app resets to docs tab", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    // Switch to CICD tab
    fireEvent.click(screen.getByTestId("detail-tab-cicd"));
    await waitFor(() => screen.getByTestId("detail-cicd-pane"));
    // Now switch to app2
    fireEvent.click(screen.getByTestId("app-row-app2"));
    await waitFor(() => {
      // detail-tab-docs should be active again (no detail-cicd-pane)
      expect(screen.queryByTestId("detail-cicd-pane")).not.toBeInTheDocument();
    });
  });
});

describe("AppWorkbenchPage W3 CICD-first new-app wizard", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  it("shows CICD-first dialog when new-app button clicked", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("new-app-btn"));
    fireEvent.click(screen.getByTestId("new-app-btn"));
    await waitFor(() => screen.getByTestId("new-app-dialog"));
    expect(screen.getByTestId("new-app-dialog").textContent).toContain("CICD-first");
  });

  it("RM sees direct-create escape-hatch button", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("new-app-btn"));
    fireEvent.click(screen.getByTestId("new-app-btn"));
    await waitFor(() => screen.getByTestId("new-app-dialog"));
    expect(screen.getByTestId("direct-create-btn")).toBeInTheDocument();
  });

  it("step 1 fetch → step 2 confirm → submits official_name to POST /api/cicd/apps/new", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    // Real backend keys from POST /api/cicd/apps/fetch-preview
    const mockPreview = {
      ok: true,
      git_url: "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/myrepo",
      git_branch: "main",
      app_version: "3.7.0",
      x86_chips: "C500",
      arm_chips: "C500",
      python_label: "python3.8",
      pytorch_label: "",
      os: "kylin",
      arch: "x86",
      commit_id: "abc123def456",
      parsed: { version: "3.7.0", x86_chips: "C500", build_os: "kylin" },
    };
    const postMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("fetch-preview")) return mockPreview;
      return { ok: true, app_id: "new-app-1", request_id: 1 };
    });
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(postMock);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("new-app-btn"));
    fireEvent.click(screen.getByTestId("new-app-btn"));
    await waitFor(() => screen.getByTestId("new-app-dialog"));
    // Step 1: fill identity fields
    fireEvent.change(screen.getByTestId("new-app-name"), { target: { value: "MyApp" } });
    const repoInput = screen.getByPlaceholderText("例：sw-metax-open/amber");
    fireEvent.change(repoInput, { target: { value: "myrepo" } });
    const branchInput = screen.getByPlaceholderText("例：master");
    fireEvent.change(branchInput, { target: { value: "main" } });
    // Click fetch
    fireEvent.click(screen.getByTestId("new-app-fetch"));
    // Step 2 preview should appear with fetched data
    await waitFor(() => screen.getByTestId("new-app-preview"));
    // Verify the preview shows the real fetched value (app_version key, not version)
    expect(screen.getByDisplayValue("3.7.0")).toBeTruthy();
    // Click confirm → submits to /api/cicd/apps/new
    fireEvent.click(screen.getByTestId("new-app-submit"));
    await waitFor(() => {
      const call = postMock.mock.calls.find((c) => c[0] === "/api/cicd/apps/new");
      expect(call).toBeTruthy();
      const body = call![1] as Record<string, unknown>;
      expect(body.official_name).toBe("MyApp");
      expect(body.app_name).toBe("MyApp");
      // Must carry parsed blob + commit_id (not app_info) so backend can persist them
      expect(body.app_info_parsed).toEqual(mockPreview.parsed);
      expect(body.app_info_commit_id).toBe("abc123def456");
    });
  });

  it("fetch error → shows skip button → skips to POST /api/cicd/apps/new directly", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const postMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("fetch-preview")) throw new Error("Gerrit not reachable");
      return { ok: true, app_id: "new-app-2", request_id: 2 };
    });
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(postMock);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("new-app-btn"));
    fireEvent.click(screen.getByTestId("new-app-btn"));
    await waitFor(() => screen.getByTestId("new-app-dialog"));
    fireEvent.change(screen.getByTestId("new-app-name"), { target: { value: "ErrApp" } });
    fireEvent.change(screen.getByPlaceholderText("例：sw-metax-open/amber"), { target: { value: "errrepo" } });
    fireEvent.change(screen.getByPlaceholderText("例：master"), { target: { value: "dev" } });
    fireEvent.click(screen.getByTestId("new-app-fetch"));
    // Error state: skip button should appear
    await waitFor(() => screen.getByTestId("new-app-submit"));
    expect(screen.getByTestId("new-app-dialog").textContent).toContain("拉取失败");
    // Click "跳过，直接创建"
    fireEvent.click(screen.getByTestId("new-app-submit"));
    await waitFor(() => {
      const call = postMock.mock.calls.find((c) => c[0] === "/api/cicd/apps/new");
      expect(call).toBeTruthy();
      expect((call![1] as Record<string, string>).official_name).toBe("ErrApp");
    });
  });
});

// ---------------------------------------------------------------------------
// W4 — Wizard derived-identity display
// ---------------------------------------------------------------------------

describe("AppWorkbenchPage W4 wizard derived-identity display", () => {
  const GERRIT_BASE = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC";

  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  async function openWizard() {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("new-app-btn"));
    fireEvent.click(screen.getByTestId("new-app-btn"));
    await waitFor(() => screen.getByTestId("new-app-dialog"));
  }

  it("fetch-error step shows derived git_url@branch for a git-type repo", async () => {
    // Gerrit fetch throws (network unreachable)
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes("fetch-preview")) throw new Error("Gerrit not reachable");
      return { ok: true, app_id: "x1", request_id: 1 };
    });
    await openWizard();

    // Fill git-type repo (default)
    fireEvent.change(screen.getByTestId("new-app-name"), { target: { value: "TestApp" } });
    fireEvent.change(screen.getByPlaceholderText("例：sw-metax-open/amber"), {
      target: { value: "sw-metax-open/myapp" },
    });
    fireEvent.change(screen.getByPlaceholderText("例：master"), { target: { value: "main" } });

    fireEvent.click(screen.getByTestId("new-app-fetch"));

    // Error state should appear with the identity box
    await waitFor(() => screen.getByTestId("derived-identity-box"));
    const box = screen.getByTestId("derived-identity-box");

    // The full SSH URL must be derived offline from the short name
    expect(box.textContent).toContain(`${GERRIT_BASE}/sw-metax-open/myapp`);
    expect(box.textContent).toContain("main");
    // Labeling
    expect(box.textContent).toContain("Gerrit 身份");
  });

  it("fetch-error step shows '需联网解析' for repo-type (manifest needs network)", async () => {
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes("fetch-preview")) throw new Error("Gerrit not reachable");
      return { ok: true, app_id: "x2", request_id: 2 };
    });
    await openWizard();

    fireEvent.change(screen.getByTestId("new-app-name"), { target: { value: "RepoApp" } });
    // Switch to repo type
    fireEvent.change(screen.getByDisplayValue("git"), { target: { value: "repo" } });
    fireEvent.change(screen.getByPlaceholderText("例：sw-metax-open/amber"), {
      target: { value: "manifests/releases/maca-4.0.xml" },
    });

    fireEvent.click(screen.getByTestId("new-app-fetch"));

    // Error state: identity box should say "需联网解析" since repo-type needs network
    await waitFor(() => screen.getByTestId("derived-identity-box"));
    const box = screen.getByTestId("derived-identity-box");
    expect(box.textContent).toContain("需联网解析");
    // Branch (auto-fixed to master for repo-type)
    expect(box.textContent).toContain("master");
  });

  it("preview step shows identity box with git_url from server response", async () => {
    const mockPreview = {
      git_url: `${GERRIT_BASE}/sw-metax-open/previewapp`,
      git_branch: "release/4.0",
      needs_network: false,
      app_info_unavailable: false,
      app_info_error: null,
      app_version: "4.0.1",
      x86_chips: "C500",
      arm_chips: "",
      python_label: "python3.10",
      pytorch_label: "",
      os: "kylin",
      arch: "x86",
      commit_id: "deadbeef1234",
      parsed: { version: "4.0.1" },
    };
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes("fetch-preview")) return mockPreview;
      return { ok: true, app_id: "x3", request_id: 3 };
    });
    await openWizard();

    fireEvent.change(screen.getByTestId("new-app-name"), { target: { value: "PreviewApp" } });
    fireEvent.change(screen.getByPlaceholderText("例：sw-metax-open/amber"), {
      target: { value: "sw-metax-open/previewapp" },
    });
    fireEvent.change(screen.getByPlaceholderText("例：master"), { target: { value: "release/4.0" } });
    fireEvent.click(screen.getByTestId("new-app-fetch"));

    // Preview step: identity box should appear with server-provided URL
    await waitFor(() => screen.getByTestId("new-app-preview"));
    await waitFor(() => screen.getByTestId("derived-identity-box"));
    const box = screen.getByTestId("derived-identity-box");
    expect(box.textContent).toContain(`${GERRIT_BASE}/sw-metax-open/previewapp`);
    expect(box.textContent).toContain("release/4.0");
  });

  it("handles app_info_unavailable=true response: shows identity from server, stays in fetch-error step", async () => {
    // Simulates impl-1 Wave 4 backend: HTTP 200 with soft failure flag;
    // identity (git_url / git_branch) always returned even when Gerrit content unavailable.
    const partialResponse = {
      git_url: `${GERRIT_BASE}/sw-metax-open/partialapp`,
      git_branch: "main",
      needs_network: false,
      app_info_unavailable: true,
      app_info_error: "Gerrit archive fetch failed: 502",
      // No content fields (app_version, x86_chips, etc.) — unavailable
    };
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes("fetch-preview")) return partialResponse;
      return { ok: true, app_id: "x4", request_id: 4 };
    });
    await openWizard();

    fireEvent.change(screen.getByTestId("new-app-name"), { target: { value: "PartialApp" } });
    fireEvent.change(screen.getByPlaceholderText("例：sw-metax-open/amber"), {
      target: { value: "sw-metax-open/partialapp" },
    });
    fireEvent.change(screen.getByPlaceholderText("例：master"), { target: { value: "main" } });
    fireEvent.click(screen.getByTestId("new-app-fetch"));

    // Goes to fetch-error step (not preview) since app_info_unavailable=true
    await waitFor(() => screen.getByTestId("new-app-submit")); // "跳过，直接创建" btn
    // Identity box present with server-provided URL
    await waitFor(() => screen.getByTestId("derived-identity-box"));
    const box = screen.getByTestId("derived-identity-box");
    expect(box.textContent).toContain(`${GERRIT_BASE}/sw-metax-open/partialapp`);
    expect(box.textContent).toContain("main");
    // Should NOT have shown the preview form (content unavailable)
    expect(screen.queryByTestId("new-app-preview")).not.toBeInTheDocument();
  });
});
