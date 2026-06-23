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

import { apiGet, apiPost } from "../../../api/http";
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

  it("RM can bulk fetch app_info from Gerrit for the selected release", async () => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation((_url: string, body?: { app_id?: string }) => {
      if (body?.app_id === "app2") return Promise.reject(new Error("Gerrit unreachable"));
      return Promise.resolve({ commit_id: "abc", source: "repo/app1 main:app_info.json" });
    });

    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("fetch-all-app-info-btn"));
    fireEvent.click(screen.getByTestId("fetch-all-app-info-btn"));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/app-info/fetch", { release_id: "rel-1", app_id: "app1" });
      expect(apiPost).toHaveBeenCalledWith("/api/app-info/fetch", { release_id: "rel-1", app_id: "app2" });
    });
    expect(window.alert).toHaveBeenCalledWith(expect.stringContaining("成功 1，失败 1"));
  });

  it("shows an in-progress dialog while fetching one app_info from Gerrit", async () => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    let resolveFetch: ((value: { commit_id: string; source: string }) => void) | undefined;
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url === "/api/app-info/fetch") {
        return new Promise((resolve) => {
          resolveFetch = resolve as (value: { commit_id: string; source: string }) => void;
        });
      }
      return Promise.resolve({});
    });

    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.click(screen.getByText("从 Gerrit 拉取"));

    await waitFor(() => screen.getByTestId("app-info-fetch-progress"));
    expect(screen.getByTestId("app-info-fetch-progress").textContent).toContain("正在从 Gerrit 拉取 app_info");

    resolveFetch?.({ commit_id: "abc", source: "repo/app1 main:app_info.json" });
    await waitFor(() => {
      expect(screen.queryByTestId("app-info-fetch-progress")).toBeNull();
    });
  });

  it("Owner cannot see the bulk Gerrit app_info fetch button", async () => {
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
    await waitFor(() => screen.getByTestId("app-table"));
    expect(screen.queryByTestId("fetch-all-app-info-btn")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// F1 / F2 / F3 — decision-sync dialog, copy-from-version, unsaved guard
// ---------------------------------------------------------------------------

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

  it("opens a forced sync dialog when decision crosses Running/Stopped", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes("decision-sync/preview")) {
        return Promise.resolve({
          decision: "stopped",
          forced: true,
          scope: "all_unlocked",
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
    expect(screen.getByTestId("decision-sync-dialog").textContent).toContain("必须同步 release 决策");
    expect(screen.getByTestId("decision-sync-dialog").textContent).toContain("所有未锁定 release");
    expect(screen.queryByTestId("sync-local-only")).toBeNull();
    expect(screen.getByTestId("sync-row-rel-2").textContent).toContain("调整为 stopped");
  });

  it("opens a forced sync dialog when stopped is raised to Running", async () => {
    const payload = payloadTwoReleases();
    (payload.release as ReleaseDetail).snapshots.app1.release_decision = "stopped";
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.includes("decision-sync/preview")) {
        return Promise.resolve({
          decision: "cicd_only",
          forced: true,
          scope: "all_unlocked",
          releases: [
            { release_id: "rel-2", release_name: "3.1", phase_label: "App 冻结前",
              resulting_decision: "cicd_only", skipped: false },
          ],
        });
      }
      return Promise.resolve({ snapshot: {}, missing_items: [] });
    });
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "cicd_only" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => screen.getByTestId("decision-sync-dialog"));
    expect(screen.getByTestId("decision-sync-dialog").textContent).toContain("必须同步 release 决策");
    expect(screen.getByTestId("decision-sync-dialog").textContent).toContain("所有未锁定 release");
    expect(screen.queryByTestId("sync-local-only")).toBeNull();
    expect(screen.getByTestId("sync-row-rel-2").textContent).toContain("调整为 cicd_only");
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
          forced: true,
          scope: "all_unlocked",
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
          decision: "cicd_only",
          forced: false,
          scope: "later",
          releases: [{ release_id: "rel-2", release_name: "3.1", phase_label: "App 冻结前",
            resulting_decision: "cicd_only", skipped: false }],
        });
      }
      return Promise.resolve({ snapshot: {}, missing_items: [] });
    });
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(postMock);
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "cicd_only" } });
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
// W2 — App CICD config pane + decision-change preview
// ---------------------------------------------------------------------------

function mockApiGetForAppCicd(payload: StatePayload = makePayload()) {
  (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
}

// Helper: click the CICD sub-tab in detail panel
async function clickCicdTab() {
  await waitFor(() => screen.getByTestId("detail-tab-cicd"));
  fireEvent.click(screen.getByTestId("detail-tab-cicd"));
  await waitFor(() => screen.getByTestId("detail-cicd-pane"));
}

describe("AppWorkbenchPage W2 App CICD config pane", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  it("shows App CICD config with status derived from release decision", async () => {
    const payload = makePayload({
      apps: [
        { ...makeApp("app1"), cicd_repo_type: "git", cicd_community_artifact: "image", cicd_build_image: "gcc:12", cicd_test_timeout: "40", cicd_notes: "nightly only" },
        makeApp("app2"),
      ],
    });
    mockApiGetForAppCicd(payload);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    await clickCicdTab();
    await waitFor(() => {
      expect(screen.getByTestId("cicd-link-card")).toBeInTheDocument();
    });
    const pane = screen.getByTestId("cicd-link-card");
    expect(pane.textContent).toContain("App CICD 配置");
    expect(pane.textContent).toContain("Running");
    expect((screen.getByTestId("field-cicd-repo-type") as HTMLSelectElement).value).toBe("git");
    expect(screen.getByLabelText("镜像")).toBeChecked();
    expect((screen.getByTestId("field-cicd-build-image") as HTMLInputElement).value).toBe("gcc:12");
    expect((screen.getByTestId("field-cicd-timeout") as HTMLInputElement).value).toBe("40");
    expect((screen.getByTestId("field-cicd-notes") as HTMLInputElement).value).toBe("nightly only");
  });

  it("shows brief CICD pending hints in app list and detail header", async () => {
    const payload = makePayload();
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.startsWith("/api/cicd/requests")) {
        return Promise.resolve({
          requests: [{
            id: 5,
            task_id: "app1",
            app_id: "app1",
            request_type: "modify",
            payload: { notes: { old: "", new: "x" } },
            submitter: "alice",
            submitter_display: "Alice",
            submitted_at: "2026-06-18 12:00:00",
            status: "pending",
            reviewer: "",
            reviewed_at: "",
            review_note: "",
            is_self_approved: 0,
            approval_mode: "dispatch_spd",
            delivery_status: "pending",
            jira_id: "SPD-1615",
            jira_auto_created: 1,
            delivered_by: "",
            delivered_at: "",
            returned_reason: "",
            returned_at: "",
            task_app_name: "AlphaApp",
            task_app_version: "1.0",
            task_repo_name: "repo/app1",
            task_branch: "main",
            task_status: "Running",
            origin: "cicd_workbench",
          }],
        });
      }
      if (url.startsWith("/api/cicd/deliveries")) {
        return Promise.resolve({ deliveries: [] });
      }
      return Promise.resolve(payload);
    });

    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    await waitFor(() => {
      expect(screen.getByTestId("app-row-app1").textContent).toContain("CICD 待处理 1");
    });

    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => {
      expect(screen.getByTestId("detail-panel").textContent).toContain("CICD 待处理 1");
    });
  });

  it("does not mix pending CICD hints between apps sharing Gerrit URL with different branches", async () => {
    const payload = makePayload();
    payload.apps[0] = {
      ...payload.apps[0],
      id: "app-main",
      git_url: "hpc_abacus",
      git_branch: "main",
    };
    payload.apps[1] = {
      ...payload.apps[1],
      id: "app-release",
      git_url: "hpc_abacus",
      git_branch: "release-3.8",
    };
    payload.release = makeRelease({
      "app-main": makeSnap("app-main", { official_name: "ABACUS main", owners: ["alice"] }),
      "app-release": makeSnap("app-release", { official_name: "ABACUS release", owners: ["alice"] }),
    });

    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.startsWith("/api/cicd/requests")) {
        return Promise.resolve({
          requests: [{
            id: 12,
            task_id: "legacy-cicd-12",
            request_type: "modify",
            payload: { notes: { old: "", new: "branch only" } },
            submitter: "alice",
            submitter_display: "Alice",
            submitted_at: "2026-06-18 12:00:00",
            status: "pending",
            reviewer: "",
            reviewed_at: "",
            review_note: "",
            is_self_approved: 0,
            approval_mode: "dispatch_spd",
            delivery_status: "",
            jira_id: "",
            jira_auto_created: 0,
            delivered_by: "",
            delivered_at: "",
            returned_reason: "",
            returned_at: "",
            task_app_name: "ABACUS main",
            task_app_version: "1.0",
            task_repo_name: "hpc_abacus",
            task_branch: "main",
            task_status: "Running",
          }],
        });
      }
      if (url.startsWith("/api/cicd/deliveries")) {
        return Promise.resolve({ deliveries: [] });
      }
      return Promise.resolve(payload);
    });

    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app-main"));
    await waitFor(() => {
      expect(screen.getByTestId("app-row-app-main").textContent).toContain("CICD 待处理 1");
    });
    expect(screen.getByTestId("app-row-app-release").textContent).not.toContain("CICD 待处理");

    fireEvent.click(screen.getByTestId("app-row-app-release"));
    await waitFor(() => {
      expect(screen.getByTestId("detail-panel").textContent).not.toContain("CICD 待处理 1");
    });
  });

  it("shows Stopped status when release decision is stopped", async () => {
    const payload = makePayload();
    payload.release!.snapshots.app1.release_decision = "stopped";
    mockApiGetForAppCicd(payload);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    await clickCicdTab();
    await waitFor(() => screen.getByTestId("cicd-link-card"));
    expect(screen.getByTestId("cicd-link-card").textContent).toContain("Stopped");
  });

  it("shows rejected CICD-first status and reason in the app workbench", async () => {
    const payload = makePayload();
    payload.apps[0] = {
      ...payload.apps[0],
      cicd_onboarding_status: "rejected_create",
      cicd_onboarding_request_id: 42,
      cicd_onboarding_review_note: "镜像配置不符合要求",
      cicd_onboarding_reviewed_at: "2026-06-18 12:00:00",
    };
    payload.release!.snapshots.app1.release_decision = "stopped";
    mockApiGetForAppCicd(payload);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));

    expect(screen.getByTestId("app-row-app1").textContent).toContain("CICD 创建被拒绝");
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("app-detail-rejected-banner"));
    expect(screen.getByTestId("app-detail-rejected-banner").textContent).toContain("镜像配置不符合要求");

    await clickCicdTab();
    expect(screen.getByTestId("app-cicd-rejected-banner").textContent).toContain("镜像配置不符合要求");
    expect(screen.getByTestId("cicd-link-card").textContent).toContain("Stopped");
  });

  it("shows rejected CICD request review notes in App CICD history", async () => {
    const payload = makePayload();
    const rejectedReq = {
      id: 77,
      task_id: "app1",
      app_id: "app1",
      request_type: "modify",
      payload: { notes: { old: "", new: "bad config" } },
      submitter: "alice",
      submitter_display: "Alice",
      submitted_at: "2026-06-18 11:00:00",
      status: "rejected",
      reviewer: "rm",
      reviewed_at: "2026-06-18 12:00:00",
      review_note: "构建镜像不存在",
      is_self_approved: 0,
      approval_mode: "immediate",
      delivery_status: "",
      jira_id: "",
      jira_auto_created: 0,
      delivered_by: "",
      delivered_at: "",
      returned_reason: "",
      returned_at: "",
      task_app_name: "AlphaApp",
      task_app_version: "1.0",
      task_repo_name: "repo/app1",
      task_branch: "main",
      task_status: "Running",
      origin: "cicd_workbench",
    };
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.startsWith("/api/cicd/requests?status=pending")) return Promise.resolve({ requests: [] });
      if (url.startsWith("/api/cicd/deliveries")) return Promise.resolve({ deliveries: [] });
      if (url.startsWith("/api/cicd/requests?since_days=")) {
        return Promise.resolve({ requests: [rejectedReq] });
      }
      return Promise.resolve(payload);
    });
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await clickCicdTab();

    await waitFor(() => {
      expect(screen.getByTestId("detail-cicd-pane").textContent).toContain("构建镜像不存在");
    });
  });

  it("submits Gerrit identity changes as a pending CICD request", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, request: { id: 1 } });
    const qc = makeQueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    renderPage(qc);
    await enterEditOnApp1();
    expect(screen.getByTestId("field-git-url")).toBeDisabled();
    expect(screen.getByText("Gerrit 身份属于 CICD 配置，请在本 App 的 CICD tab 中修改。")).toBeInTheDocument();
    await clickCicdTab();
    fireEvent.change(screen.getByTestId("field-cicd-git-url"), { target: { value: "repo/app1-renamed" } });
    fireEvent.change(screen.getByTestId("field-cicd-git-branch"), { target: { value: "release/4.0" } });
    fireEvent.click(screen.getByText("提交 CICD 变更申请"));
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/cicd/requests/submit", expect.anything());
    });
    expect(apiPost).toHaveBeenCalledWith("/api/cicd/requests/submit", expect.objectContaining({
      task_id: "app1",
      request_type: "modify",
      source: "app_workbench",
      payload: expect.objectContaining({
        repo_name: { old: "repo/app1", new: "repo/app1-renamed" },
        branch: { old: "main", new: "release/4.0" },
      }),
    }));
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["cicd", "tasks"] });
  });

  it("shows App CICD config even without any legacy CICD task", async () => {
    mockApiGetForAppCicd();
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    await clickCicdTab();
    await waitFor(() => expect(screen.getByTestId("cicd-link-card")).toBeInTheDocument());
    expect(screen.getByTestId("detail-cicd-pane").textContent).not.toContain("暂无关联 CICD 任务");
  });

  it("submits owner-editable App CICD config fields as a pending request", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payloadTwoReleases());
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, request: { id: 2 } });
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    await clickCicdTab();
    fireEvent.change(screen.getByTestId("field-cicd-git-url"), { target: { value: "repo/app1-cicd" } });
    fireEvent.change(screen.getByTestId("field-cicd-git-branch"), { target: { value: "dev" } });
    fireEvent.click(screen.getByLabelText("镜像"));
    fireEvent.change(screen.getByTestId("field-cicd-build-image"), { target: { value: "gcc:12" } });
    fireEvent.change(screen.getByTestId("field-cicd-timeout"), { target: { value: "60" } });
    fireEvent.change(screen.getByTestId("field-cicd-notes"), { target: { value: "manual fill" } });
    fireEvent.click(screen.getByText("提交 CICD 变更申请"));
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/api/cicd/requests/submit", expect.objectContaining({
        task_id: "app1",
        request_type: "modify",
        source: "app_workbench",
        payload: expect.objectContaining({
          repo_name: { old: "repo/app1", new: "repo/app1-cicd" },
          branch: { old: "main", new: "dev" },
          community_artifact: { old: [], new: ["image"] },
          build_image: { old: "", new: "gcc:12" },
          test_timeout: { old: 40, new: 60 },
          notes: { old: "", new: "manual fill" },
        }),
      }));
    });
  });

  it("shows 'running/停止由本 app 决策决定' note in App CICD config", async () => {
    mockApiGetForAppCicd();
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    await clickCicdTab();
    await waitFor(() => screen.getByTestId("cicd-link-card"));
    expect(screen.getByTestId("cicd-link-card").textContent).toContain("运行/停止由本 app 决策决定");
  });

  it("disables community fields when developer community artifact is empty", async () => {
    mockApiGetForAppCicd();
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    expect(screen.getByLabelText("开发者社区发布情况")).toBeDisabled();
    expect(screen.getByLabelText("社区包支持 Python 版本")).toBeDisabled();
    expect(screen.getByLabelText("社区包支持框架及版本")).toBeDisabled();
    expect(screen.getByText("CICD 中没有选择开发者社区产物，社区发布情况、Python 版本和框架版本暂不可填写。")).toBeInTheDocument();
    expect(screen.getAllByText("不可填写：CICD 中没有选择开发者社区产物。")).toHaveLength(3);
  });

  it("enables community fields after developer community artifact is filled", async () => {
    mockApiGetForAppCicd();
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1();
    await clickCicdTab();
    fireEvent.click(screen.getByLabelText("镜像"));
    fireEvent.click(screen.getByTestId("detail-tab-docs"));
    await waitFor(() => {
      expect(screen.getByLabelText("开发者社区发布情况")).not.toBeDisabled();
    });
    expect(screen.getByLabelText("社区包支持 Python 版本")).not.toBeDisabled();
    expect(screen.getByLabelText("社区包支持框架及版本")).not.toBeDisabled();
  });
});

describe("AppWorkbenchPage W2 decision-change App CICD preview", () => {
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

  it("shows 'App CICD 状态将显示为 Stopped' when decision changes to stopped", async () => {
    // app1 starts with release_decision "release" — changing to "stopped" → preview shows Stopped
    mockApiGetForAppCicd();
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

  it("shows 'App CICD 状态将显示为 Running' when decision changes from stopped to release", async () => {
    // Override app1 to start at "stopped"
    const stoppedPayload = makePayload();
    stoppedPayload.release!.snapshots.app1.release_decision = "stopped";
    mockApiGetForAppCicd(stoppedPayload);
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

  it("shows decision preview without any legacy CICD task", async () => {
    mockApiGetForAppCicd();
    const qc = makeQueryClient();
    renderPage(qc);
    await enterEditOnApp1WithCicd();
    fireEvent.change(screen.getByTestId("field-decision"), { target: { value: "stopped" } });
    await waitFor(() => {
      expect(screen.getByTestId("cicd-decision-preview")).toBeInTheDocument();
    });
  });

  it("does NOT show decision preview when decision hasn't changed from original", async () => {
    // app1 starts "release" and user doesn't change it
    mockApiGetForAppCicd();
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

  it("CICD pane shows App CICD config from the app table", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("detail-panel"));
    fireEvent.click(screen.getByTestId("detail-tab-cicd"));
    await waitFor(() => screen.getByTestId("detail-cicd-pane"));
    await waitFor(() => {
      expect(screen.getByTestId("detail-cicd-pane").textContent).toContain("App CICD 配置");
      expect(screen.getByTestId("detail-cicd-pane").textContent).not.toContain("暂无关联 CICD 任务");
    });
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

describe("AppWorkbenchPage lifecycle actions", () => {
  beforeEach(() => {
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
    useUiStore.getState().setSelectedApp("");
    useUiStore.getState().setAppDetailDirty(false);
  });

  it("rejected CICD-first app shows re-apply button and opens prefilled wizard", async () => {
    const payload = makePayload();
    payload.apps[0] = {
      ...payload.apps[0],
      cicd_onboarding_status: "rejected_create",
      cicd_onboarding_review_note: "镜像配置不符合要求",
    };
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderPage(qc);

    await waitFor(() => screen.getByTestId("app-row-app1"));
    fireEvent.click(screen.getByTestId("app-row-app1"));
    await waitFor(() => screen.getByTestId("retry-create-btn"));
    expect(screen.queryByText("✎ 修改")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("retry-create-btn"));
    await waitFor(() => screen.getByTestId("new-app-dialog"));
    expect(screen.getByTestId("new-app-name")).toHaveValue("AlphaApp");
    expect(screen.getByLabelText(/仓库名 /)).toHaveValue("repo/app1");
    expect(screen.getByLabelText(/分支 /)).toHaveValue("main");
  });

  it("doc deadline freezes document fields and only allows cicd_only or stopped decisions", async () => {
    const payload = makePayload();
    const release = payload.release!;
    payload.release = {
      ...release,
      doc_deadline: "2000-01-01",
      phase: "released",
      released_locked: false,
    };
    payload.releases = [{ ...payload.releases[0], doc_deadline: "2000-01-01", phase: "released", released_locked: false }];
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const qc = makeQueryClient();
    renderPage(qc);

    await waitFor(() => screen.getByTestId("app-row-app2"));
    fireEvent.click(screen.getByTestId("app-row-app2"));
    await waitFor(() => screen.getByText("✎ 修改"));
    fireEvent.click(screen.getByText("✎ 修改"));

    await waitFor(() => screen.getByTestId("field-decision"));
    const decision = screen.getByTestId("field-decision") as HTMLSelectElement;
    expect(decision).not.toBeDisabled();
    expect(Array.from(decision.options).map((option) => option.value)).toEqual(["cicd_only", "stopped"]);
    expect(screen.getByTestId("field-description")).toBeDisabled();
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
    const repoInput = screen.getByLabelText(/仓库名 /);
    fireEvent.change(repoInput, { target: { value: "myrepo" } });
    const branchInput = screen.getByLabelText(/分支 /);
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
    fireEvent.change(screen.getByLabelText(/仓库名 /), { target: { value: "errrepo" } });
    fireEvent.change(screen.getByLabelText(/分支 /), { target: { value: "dev" } });
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

  it("duplicate Gerrit identity blocks skip creation", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makePayload());
    const postMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("fetch-preview")) {
        throw new Error("该 Gerrit URL + branch 已存在 app（aaa），请使用 aaa 名称重新申请，不能重复创建");
      }
      return { ok: true, app_id: "new-app-3", request_id: 3 };
    });
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(postMock);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("new-app-btn"));
    fireEvent.click(screen.getByTestId("new-app-btn"));
    await waitFor(() => screen.getByTestId("new-app-dialog"));

    fireEvent.change(screen.getByTestId("new-app-name"), { target: { value: "bbb" } });
    fireEvent.change(screen.getByLabelText(/仓库名 /), { target: { value: "hpc_aa" } });
    fireEvent.change(screen.getByLabelText(/分支 /), { target: { value: "main" } });
    fireEvent.click(screen.getByTestId("new-app-fetch"));

    await waitFor(() => {
      expect(screen.getByTestId("new-app-dialog").textContent).toContain("无法继续创建");
    });
    expect(screen.getByTestId("new-app-dialog").textContent).toContain("已存在 app（aaa）");
    expect(screen.queryByTestId("new-app-submit")).not.toBeInTheDocument();
    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it("duplicate rejected app with same name fetches preview before re-apply", async () => {
    const payload = makePayload();
    payload.apps[0] = {
      ...payload.apps[0],
      cicd_onboarding_status: "rejected_create",
      cicd_onboarding_review_note: "上次被拒绝",
    };
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(payload);
    const postMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("fetch-preview")) {
        return {
          git_url: "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_aa",
          git_branch: "main",
          needs_network: false,
          app_info_unavailable: false,
          app_info_error: null,
          app_version: "1.0",
          x86_chips: "C500",
          arm_chips: "",
          python_label: "3.10",
          pytorch_label: "",
          os: "ubuntu20.04",
          arch: "amd64",
          commit_id: "abc123",
          parsed: { app_version: "1.0" },
          retry_existing_app_id: "app1",
          retry_existing_app_name: "AlphaApp",
          retry_onboarding_status: "rejected_create",
          retry_review_note: "上次被拒绝",
        };
      }
      return { ok: true, app_id: "app1", request_id: 3 };
    });
    (apiPost as ReturnType<typeof vi.fn>).mockImplementation(postMock);
    const qc = makeQueryClient();
    renderPage(qc);
    await waitFor(() => screen.getByTestId("new-app-btn"));
    fireEvent.click(screen.getByTestId("new-app-btn"));
    await waitFor(() => screen.getByTestId("new-app-dialog"));

    fireEvent.change(screen.getByTestId("new-app-name"), { target: { value: "AlphaApp" } });
    fireEvent.change(screen.getByLabelText(/仓库名 /), { target: { value: "hpc_aa" } });
    fireEvent.change(screen.getByLabelText(/分支 /), { target: { value: "main" } });
    fireEvent.click(screen.getByTestId("new-app-fetch"));

    await waitFor(() => screen.getByTestId("new-app-submit"));
    expect(screen.getByTestId("new-app-dialog").textContent).toContain("曾提交新建 App 申请");
    expect(screen.getByTestId("new-app-dialog").textContent).toContain("重新申请");
    fireEvent.click(screen.getByTestId("new-app-submit"));
    await waitFor(() => {
      const createCall = postMock.mock.calls.find((call) => call[0] === "/api/cicd/apps/new");
      expect(createCall).toBeTruthy();
      expect((createCall![1] as Record<string, unknown>).app_info_commit_id).toBe("abc123");
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
    fireEvent.change(screen.getByLabelText(/仓库名 /), {
      target: { value: "sw-metax-open/myapp" },
    });
    fireEvent.change(screen.getByLabelText(/分支 /), { target: { value: "main" } });

    fireEvent.click(screen.getByTestId("new-app-fetch"));

    // Error state should appear with the identity box
    await waitFor(() => screen.getByTestId("derived-identity-box"));
    const box = screen.getByTestId("derived-identity-box");

    expect(box.textContent).toContain("sw-metax-open/myapp");
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
    fireEvent.change(screen.getByLabelText(/仓库名 /), {
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
    fireEvent.change(screen.getByLabelText(/仓库名 /), {
      target: { value: "sw-metax-open/previewapp" },
    });
    fireEvent.change(screen.getByLabelText(/分支 /), { target: { value: "release/4.0" } });
    fireEvent.click(screen.getByTestId("new-app-fetch"));

    // Preview step: identity box should appear with server-provided URL
    await waitFor(() => screen.getByTestId("new-app-preview"));
    await waitFor(() => screen.getByTestId("derived-identity-box"));
    const box = screen.getByTestId("derived-identity-box");
    expect(box.textContent).toContain("sw-metax-open/previewapp");
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
    fireEvent.change(screen.getByLabelText(/仓库名 /), {
      target: { value: "sw-metax-open/partialapp" },
    });
    fireEvent.change(screen.getByLabelText(/分支 /), { target: { value: "main" } });
    fireEvent.click(screen.getByTestId("new-app-fetch"));

    // Goes to fetch-error step (not preview) since app_info_unavailable=true
    await waitFor(() => screen.getByTestId("new-app-submit")); // "跳过，直接创建" btn
    // Identity box present with server-provided URL
    await waitFor(() => screen.getByTestId("derived-identity-box"));
    const box = screen.getByTestId("derived-identity-box");
    expect(box.textContent).toContain("sw-metax-open/partialapp");
    expect(box.textContent).toContain("main");
    // Should NOT have shown the preview form (content unavailable)
    expect(screen.queryByTestId("new-app-preview")).not.toBeInTheDocument();
  });
});
