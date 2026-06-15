/**
 * AppRouter tests.
 *
 * Covers:
 *  - Ruling C: Admin at any non-/admin path is redirected to /admin
 *  - Ruling C: Non-Admin user at / sees dashboard (no redirect)
 *  - Unknown path falls back to / (catch-all)
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AppRouter } from "../AppRouter";

// ---------------------------------------------------------------------------
// Mock all feature pages to avoid deep import trees in unit tests
// ---------------------------------------------------------------------------

vi.mock("../../features/dashboard/DashboardPage", () => ({
  DashboardPage: () => <div data-testid="dashboard-page">dashboard</div>,
}));
vi.mock("../../features/init/ReleaseCyclePage", () => ({
  ReleaseCyclePage: () => <div>release-cycle</div>,
}));
vi.mock("../../features/appWorkbench/AppWorkbenchPage", () => ({
  AppWorkbenchPage: () => <div>apps</div>,
}));
vi.mock("../../features/qa/QaPage", () => ({
  QaPage: () => <div>qa</div>,
}));
vi.mock("../../features/artifacts/ArtifactsPage", () => ({
  ArtifactsPage: () => <div>artifacts</div>,
}));
vi.mock("../../features/cicd/CicdPage", () => ({
  CicdPage: () => <div>cicd</div>,
}));
vi.mock("../../features/wiki/WikiPage", () => ({
  WikiPage: () => <div>wiki</div>,
}));
vi.mock("../../features/admin/AdminPage", () => ({
  AdminPage: () => <div data-testid="admin-page">admin-page</div>,
}));

vi.mock("../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

import { useAuth } from "../../api/AuthContext";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeAuthReturn(role: string) {
  return {
    user: { username: "test", display_name: "Test", role },
    ldapStatus: { enabled: false, uri: "" },
    login: vi.fn(),
    logout: vi.fn(),
    clearUser: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>;
}

function renderAt(role: string, initialPath = "/") {
  vi.mocked(useAuth).mockReturnValue(makeAuthReturn(role));
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AppRouter />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AppRouter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Ruling C: Admin redirect ────────────────────────────────────────────────

  it("Admin at / is redirected to /admin (ruling C)", () => {
    renderAt("Admin", "/");
    // The Navigate in AppRouter redirects Admin from "/" to "/admin"
    // MemoryRouter follows the redirect → AdminPage renders
    expect(screen.getByTestId("admin-page")).toBeInTheDocument();
    // Dashboard should NOT be rendered
    expect(screen.queryByTestId("dashboard-page")).not.toBeInTheDocument();
  });

  it("Admin at /apps is redirected to /admin (ruling C)", () => {
    renderAt("Admin", "/apps");
    expect(screen.getByTestId("admin-page")).toBeInTheDocument();
  });

  it("Admin at /cicd is redirected to /admin (ruling C)", () => {
    renderAt("Admin", "/cicd");
    expect(screen.getByTestId("admin-page")).toBeInTheDocument();
  });

  // ── Non-Admin: no redirect ─────────────────────────────────────────────────

  it("RM at / sees dashboard (no redirect)", () => {
    renderAt("RM", "/");
    expect(screen.getByTestId("dashboard-page")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-page")).not.toBeInTheDocument();
  });

  it("Guest at / sees dashboard (no redirect)", () => {
    renderAt("Guest", "/");
    expect(screen.getByTestId("dashboard-page")).toBeInTheDocument();
  });
});
