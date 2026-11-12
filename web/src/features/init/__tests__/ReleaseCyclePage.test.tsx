/**
 * 周期管理: new monthly version with default dates, environment editing,
 * publish/unlock, app_info sync status and notification settings.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ReleaseCyclePage } from "../ReleaseCyclePage";
import { makeApp, makeEntry, makeRelease, makeState } from "../../../test/fixtures";
import type { ReleaseDetail, StatePayload } from "../../../types";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../../api/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("../../../lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("../../../lib/confirm", () => ({ confirmDialog: vi.fn(), promptDialog: vi.fn() }));

import { apiGet, apiPost } from "../../../api/http";
import { useAuth } from "../../../api/AuthContext";
import { confirmDialog, promptDialog } from "../../../lib/confirm";
import { useUiStore } from "../../../store/uiStore";

const CHANNELS = {
  channels: [
    {
      kind: "scope_request", label: "测试范围扩大申请", enabled: true, configured: true,
      access_token_mask: "****abcd", secret_mask: "****wxyz", at_user_ids: ["u1"], updated_at: "", updated_by: "",
    },
  ],
};

function mockApi(state: StatePayload) {
  vi.mocked(apiGet).mockImplementation(async (url: string) => {
    if (url.startsWith("/api/state")) return state;
    if (url.startsWith("/api/releases/defaults")) {
      return { signup_deadline: "2027-01-01 23:59", qa_deadline: "2027-01-26 23:59", release_date: "2027-01-31" };
    }
    if (url.startsWith("/api/app-info/sync-status")) {
      return {
        apps: [
          { app_id: "a", name: "Alpha", cicd_status: "running", git_url: "hpc_a", git_branch: "maca",
            head_commit: "abc", checked_at: "2026-11-12 02:00:00", error: "Permission denied" },
          { app_id: "b", name: "Beta", cicd_status: "running", git_url: "hpc_b", git_branch: "maca",
            head_commit: "def", checked_at: "2026-11-12 02:00:00", error: "" },
        ],
        job: { running: false, started_at: "", started_by: "", finished_at: "", summary: null },
        daily: { time: "02:00", enabled: true, last_run: "" },
      };
    }
    if (url.startsWith("/api/notifications/channels")) return CHANNELS;
    throw new Error(`unexpected GET ${url}`);
  });
  vi.mocked(apiPost).mockResolvedValue({ release_id: "rel-new" });
}

function renderPage(release: ReleaseDetail) {
  mockApi(makeState([makeApp("a")], release));
  useUiStore.setState({ selectedReleaseId: "" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><ReleaseCyclePage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(confirmDialog).mockResolvedValue(true);
  vi.mocked(useAuth).mockReturnValue({ user: { username: "rm", role: "RM", display_name: "RM" } } as ReturnType<typeof useAuth>);
});

describe("new version", () => {
  it("prefills default dates for the month and requires a manual name", async () => {
    renderPage(makeRelease([]));
    fireEvent.change(await screen.findByTestId("new-release-month"), { target: { value: "2027-01" } });
    await waitFor(() => expect(screen.getByTestId("new-qa-deadline")).toHaveValue("2027-01-26"));
    expect(screen.getByTestId("new-signup-deadline")).toHaveValue("2027-01-01");
    fireEvent.click(screen.getByTestId("create-release-btn"));
    expect(await screen.findByTestId("create-err")).toHaveTextContent("请填写版本名");
    fireEvent.change(screen.getByTestId("new-release-name"), { target: { value: "2027.01" } });
    fireEvent.click(screen.getByTestId("create-release-btn"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/releases/create", {
        name: "2027.01", signup_deadline: "2027-01-01", qa_deadline: "2027-01-26", release_date: "2027-01-31",
      }),
    );
  });
});

describe("current version", () => {
  it("edits the environment only during signup", async () => {
    renderPage(makeRelease([]));
    const table = await screen.findByTestId("environment-table");
    fireEvent.change(within(table).getAllByLabelText("版本")[0], { target: { value: "3.4" } });
    fireEvent.click(screen.getByTestId("save-environment-btn"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/releases/environment", {
        release_id: "rel-1", rows: [{ component: "MACA SDK", version: "3.4", note: "" }],
      }),
    );
  });

  it("freezes the environment after the signup deadline", async () => {
    renderPage(makeRelease([], { phase: "in_progress" }));
    const table = await screen.findByTestId("environment-table");
    expect(within(table).getAllByLabelText("版本")[0]).toBeDisabled();
    expect(screen.queryByTestId("save-environment-btn")).toBeNull();
  });

  it("publishes only after the QA deadline", async () => {
    renderPage(makeRelease([makeEntry("a", { qa_status: "qa_passed" })], { phase: "in_progress" }));
    expect(await screen.findByTestId("final-lock-btn")).toBeDisabled();
  });

  it("publishes after confirmation and summarises the counts", async () => {
    const release = makeRelease(
      [makeEntry("a", { qa_status: "qa_passed" }), makeEntry("b", { id: 2, qa_status: "not_checked" })],
      { phase: "qa_closed" },
    );
    renderPage(release);
    vi.mocked(apiPost).mockResolvedValue({ released: ["a"], failed: ["b"] });
    fireEvent.click(await screen.findByTestId("final-lock-btn"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/releases/publish", { release_id: "rel-1" }));
    expect(vi.mocked(confirmDialog).mock.calls[0][0].body).toContain("将发布 1 个 App");
  });

  it("unlocks a published version with a reason", async () => {
    vi.mocked(promptDialog).mockResolvedValue("fix QA verdict");
    renderPage(makeRelease([], { phase: "released", released_locked: true }));
    fireEvent.click(await screen.findByTestId("final-unlock-btn"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/releases/unlock", { release_id: "rel-1", reason: "fix QA verdict" }),
    );
  });
});

describe("sub-panes", () => {
  it("lists failed app_info syncs first and filters to failures by default", async () => {
    renderPage(makeRelease([]));
    fireEvent.click(await screen.findByTestId("subtab-sync"));
    const pane = await screen.findByTestId("sync-status-pane");
    await waitFor(() => expect(pane).toHaveTextContent("Permission denied"));
    expect(pane).not.toHaveTextContent("Beta");
  });

  it("saves a notification channel without resending unchanged secrets", async () => {
    renderPage(makeRelease([]));
    fireEvent.click(await screen.findByTestId("subtab-notifications"));
    const row = await screen.findByTestId("channel-scope_request");
    expect(within(row).getByLabelText("ACCESS_TOKEN")).toHaveAttribute("placeholder", "****abcd");
    fireEvent.click(within(row).getByText("保存"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/notifications/channels/save", {
        kind: "scope_request", enabled: true, access_token: null, secret: null, at_user_ids: ["u1"],
      }),
    );
  });
});
