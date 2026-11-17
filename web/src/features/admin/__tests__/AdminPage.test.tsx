/**
 * AdminPage tests.
 *
 * Covers:
 *  - Renders two subtabs: 数据库管理 and 成员管理
 *  - DB pane: renders app table from state payload
 *  - DB pane: clear-db button calls POST /api/admin/clear-db
 *  - DB pane: delete-app button calls POST /api/admin/apps/delete after prompt
 *  - Members pane: loaded on tab switch (not on mount)
 *  - Members pane: renders users table
 *  - Members pane: set-role calls POST /api/admin/users/set-role
 *  - Error states for state + users fetch failures
 */

import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AdminPage } from "../AdminPage";
import type { StatePayload } from "../../../types";
import { makeApp, makeRelease, makeState } from "../../../test/fixtures";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("../../../lib/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("../../../lib/confirm", () => ({
  confirmDialog: vi.fn(),
  promptDialog: vi.fn(),
}));

vi.mock("../../../store/uiStore", () => {
  const useUiStore = (selector?: (s: { selectedReleaseId: string }) => unknown) => {
    const state = { selectedReleaseId: "" };
    return selector ? selector(state) : state;
  };
  useUiStore.getState = () => ({ selectedReleaseId: "" });
  return { useUiStore };
});

import { apiGet, apiPost } from "../../../api/http";
import { toast } from "../../../lib/toast";
import { confirmDialog, promptDialog } from "../../../lib/confirm";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeStatePayload(): StatePayload {
  return makeState([makeApp("app1", { name: "App app1 1.0" })], makeRelease([]), {
    user: { username: "admin", role: "Admin", display_name: "Admin" },
  });
}

function makeUsers() {
  return [
    {
      username: "alice",
      role: "RM",
      auth_source: "local",
      display_name: "Alice",
    },
    {
      username: "bob",
      role: "Owner",
      auth_source: "ldap",
      display_name: "Bob",
    },
  ];
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

function renderAdminPage(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AdminPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue(makeStatePayload());
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AdminPage structure", () => {
  it("renders 数据库管理 and 成员管理 subtabs", async () => {
    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() => {
      expect(screen.getByTestId("admin-tab-db")).toBeDefined();
      expect(screen.getByTestId("admin-tab-members")).toBeDefined();
    });
  });

  it("shows app table with app from state", async () => {
    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() => {
      expect(screen.getByTestId("admin-app-table")).toBeDefined();
      expect(screen.getByText("app1")).toBeDefined();
    });
  });

  it("shows RefreshBar", async () => {
    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() => expect(screen.getByTestId("refresh-btn")).toBeDefined());
  });

  it("shows error on state fetch failure", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("state error"),
    );
    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() => {
      expect(screen.getByText(/加载失败/)).toBeDefined();
      expect(screen.getByText(/state error/)).toBeDefined();
    });
  });
});

describe("AdminPage DB pane", () => {
  it("clear-db button calls POST /api/admin/clear-db", async () => {
    vi.mocked(confirmDialog).mockResolvedValue(true);
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      backup: "backup_2026.db",
    });

    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() =>
      expect(screen.getByTestId("clear-db-btn")).toBeDefined(),
    );

    fireEvent.change(screen.getByTestId("clear-confirm-input"), {
      target: { value: "CLEAR_DATABASE" },
    });
    fireEvent.change(screen.getByTestId("clear-password-input"), {
      target: { value: "adminpass" },
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("clear-db-btn"));
    });

    expect(apiPost).toHaveBeenCalledWith(
      "/api/admin/clear-db",
      expect.objectContaining({
        confirm: "CLEAR_DATABASE",
        password: "adminpass",
      }),
    );
  });

  it("clear-db shows a toast when password is empty", async () => {
    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() =>
      expect(screen.getByTestId("clear-db-btn")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("clear-db-btn"));

    expect(toast.info).toHaveBeenCalledWith(
      expect.stringContaining("admin 密码"),
    );
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("delete-app button calls POST /api/admin/apps/delete after prompt", async () => {
    vi.mocked(promptDialog).mockResolvedValue("app1");
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });

    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() =>
      expect(screen.getByTestId("delete-app-app1")).toBeDefined(),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("delete-app-app1"));
    });

    expect(apiPost).toHaveBeenCalledWith(
      "/api/admin/apps/delete",
      expect.objectContaining({ app_id: "app1", confirm: "app1" }),
    );
  });

  it("delete-app does NOT call API when prompt is cancelled", async () => {
    vi.mocked(promptDialog).mockResolvedValue(null);
    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() =>
      expect(screen.getByTestId("delete-app-app1")).toBeDefined(),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("delete-app-app1"));
    });
    expect(apiPost).not.toHaveBeenCalled();
  });
});

describe("AdminPage members pane", () => {
  it("does NOT fetch users on initial mount (DB pane active)", async () => {
    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() =>
      expect(screen.getByTestId("admin-tab-db")).toBeDefined(),
    );
    // Only state was fetched, not users
    expect(apiGet).toHaveBeenCalledWith(expect.stringContaining("/api/state"));
    expect(apiGet).not.toHaveBeenCalledWith("/api/admin/users");
  });

  it("fetches users when switching to 成员管理 subtab", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path === "/api/admin/users")
        return Promise.resolve({ users: makeUsers() });
      return Promise.resolve(makeStatePayload());
    });

    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() =>
      expect(screen.getByTestId("admin-tab-members")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("admin-tab-members"));

    await waitFor(() => {
      expect(screen.getByTestId("members-table")).toBeDefined();
      expect(screen.getByText("Alice (alice)")).toBeDefined();
      expect(screen.getByText("Bob (bob)")).toBeDefined();
    });
  });

  it("save-role calls POST /api/admin/users/set-role", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path === "/api/admin/users")
        return Promise.resolve({ users: makeUsers() });
      return Promise.resolve(makeStatePayload());
    });
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });

    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() =>
      expect(screen.getByTestId("admin-tab-members")).toBeDefined(),
    );
    fireEvent.click(screen.getByTestId("admin-tab-members"));

    await waitFor(() =>
      expect(screen.getByTestId("save-role-alice")).toBeDefined(),
    );

    // Change alice's role to QA
    fireEvent.change(screen.getByTestId("role-select-alice"), {
      target: { value: "QA" },
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("save-role-alice"));
    });

    expect(apiPost).toHaveBeenCalledWith(
      "/api/admin/users/set-role",
      expect.objectContaining({ username: "alice", role: "QA" }),
    );
  });

  it("shows error on users fetch failure", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path === "/api/admin/users")
        return Promise.reject(new Error("users error"));
      return Promise.resolve(makeStatePayload());
    });

    const qc = makeQClient();
    renderAdminPage(qc);
    await waitFor(() =>
      expect(screen.getByTestId("admin-tab-members")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("admin-tab-members"));

    await waitFor(() => {
      expect(screen.getByText(/加载成员失败/)).toBeDefined();
      expect(screen.getByText(/users error/)).toBeDefined();
    });
  });
});
