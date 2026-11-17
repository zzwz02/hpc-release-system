/** Signup component behavior, independent of its visibility in App details. */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SignupPane } from "../SignupPane";
import { makeApp, makeEntry, makeRelease, makeState } from "../../../test/fixtures";
import type { StatePayload } from "../../../types";

vi.mock("../../../api/http", () => ({ apiPost: vi.fn() }));
vi.mock("../../../lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("../../../lib/confirm", () => ({ confirmDialog: vi.fn() }));
import { apiPost } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";

let state: StatePayload;
function mockApi(next: StatePayload) { state = next; }
function renderSignup() {
  const app = state.apps[0];
  return render(<SignupPane app={app} entry={state.release!.entries.find((entry) => entry.app_id === app.id) ?? null}
    release={state.release!} canEdit onSaved={vi.fn()} onDirtyChange={vi.fn()} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiPost).mockResolvedValue({});
  vi.mocked(confirmDialog).mockResolvedValue(true);
});

describe("signup", () => {
  it("signs up with base image, version, features and a reduced scope", async () => {
    mockApi(makeState([makeApp("a")], makeRelease([])));
    renderSignup();
    fireEvent.change(await screen.findByTestId("signup-base-image"), { target: { value: "1" } });
    fireEvent.change(screen.getByTestId("signup-planned-features"), { target: { value: "new solver" } });
    fireEvent.click(screen.getByLabelText("纳入 hpc.x.t2"));
    fireEvent.click(screen.getByTestId("signup-submit"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/entries/signup", {
        release_id: "rel-1",
        app_id: "a",
        base_image_id: 1,
        target_version: "1.0",
        planned_features: "new solver",
        excluded_tests: ["hpc.x.t2"],
      }),
    );
  });

  it.each([
    ["stopped CICD", { cicd_status: "stopped" as const }, "CICD 已停止"],
    ["open stop request", { open_status_request: { id: 9, target_status: "stopped" as const } }, "#9"],
    ["missing app_info", { latest_app_info: null }, "还没有 app_info"],
  ])("blocks signup for %s", async (_label, overrides, text) => {
    mockApi(makeState([makeApp("a", overrides)], makeRelease([])));
    renderSignup();
    expect(await screen.findByTestId("signup-blocker")).toHaveTextContent(text);
    expect(screen.queryByTestId("signup-submit")).toBeNull();
  });

  it("blocks signup after the deadline", async () => {
    mockApi(makeState([makeApp("a")], makeRelease([], { phase: "in_progress" })));
    renderSignup();
    expect(await screen.findByTestId("signup-blocker")).toHaveTextContent("报名已截止");
  });
});

describe("signed-up entry", () => {
  it("after the deadline only allows removing tests and editing the delivery", async () => {
    const entry = makeEntry("a", { excluded_tests: ["hpc.x.t2"], scope_tests: [
      { path: "hpc.x.t1", command: "run t1", supported_chips: ["C500"], arch_list: [], platforms: [], excluded: false },
      { path: "hpc.x.t2", command: "run t2", supported_chips: ["C500"], arch_list: [], platforms: [], excluded: true },
    ] });
    mockApi(makeState([makeApp("a")], makeRelease([entry], { phase: "in_progress" })));
    renderSignup();
    fireEvent.click(await screen.findByTestId("entry-edit"));
    expect(screen.getByTestId("entry-target-version")).toBeDisabled();
    expect(screen.getByLabelText("纳入 hpc.x.t2")).toBeDisabled();
    const t1 = screen.getByLabelText("纳入 hpc.x.t1");
    expect(t1).not.toBeDisabled();
    fireEvent.click(t1);
    fireEvent.change(screen.getByTestId("entry-actual-delivery"), { target: { value: "shipped A" } });
    fireEvent.click(screen.getByTestId("entry-save"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/entries/update", {
        entry_id: 1,
        changes: { excluded_tests: ["hpc.x.t2", "hpc.x.t1"], actual_delivery: "shipped A", known_issues: "" },
      }),
    );
  });

  it("shows a pending scope request and withdraws after confirmation", async () => {
    const entry = makeEntry("a", {
      pending_scope_request: {
        id: 5, entry_id: 1, base_version_id: 1, target_version_id: 2, additions: ["新增芯片 C600"],
        status: "pending", created_at: "", decided_by: "", decided_at: "", decision_note: "",
      },
    });
    mockApi(makeState([makeApp("a")], makeRelease([entry], { phase: "in_progress" })));
    renderSignup();
    expect(await screen.findByTestId("pending-scope-request")).toHaveTextContent("新增芯片 C600");
    fireEvent.click(screen.getByTestId("entry-withdraw"));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/entries/withdraw", { entry_id: 1 }));
  });

  it("is read-only once published", async () => {
    mockApi(makeState([makeApp("a")], makeRelease([makeEntry("a", { result: "released" })], {
      phase: "released", released_locked: true,
    })));
    renderSignup();
    await screen.findByTestId("signup-pane");
    expect(screen.queryByTestId("entry-edit")).toBeNull();
    expect(screen.queryByTestId("entry-withdraw")).toBeNull();
  });
});
