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
import {
  ALL_ROLES,
  routeForView,
  type Role,
  type RouteView,
} from "../routeConfig";

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
vi.mock("../../features/cicdAgent/JenkinsFailuresPage", () => ({
  JenkinsFailuresPage: () => <div data-testid="jenkins-failures-page">jenkins-failures</div>,
}));
vi.mock("../../features/cicdAgent/CicdAssistantPage", () => ({
  CicdAssistantPage: () => <div data-testid="cicd-assistant-page">cicd-assistant</div>,
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

function makeAuthReturn(role: Role) {
  return {
    user: { username: "test", display_name: "Test", role },
    ldapStatus: { enabled: false, uri: "" },
    login: vi.fn(),
    logout: vi.fn(),
    clearUser: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>;
}

function renderAt(role: Role, initialPath = "/") {
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

  for (const { view, testId } of [
    { view: "jenkins-failures", testId: "jenkins-failures-page" },
    { view: "cicd-assistant", testId: "cicd-assistant-page" },
  ] satisfies Array<{ view: RouteView; testId: string }>) {
    const route = routeForView(view);
    it.each(ALL_ROLES)(`%s follows shared access for ${view}`, (role) => {
      renderAt(role, route.path);
      expect(screen.queryByTestId(testId) !== null).toBe(route.roles.includes(role));
    });
  }

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

  // ── Wave 3: CICD access and App fallback derive from routeConfig ────────────

  const cicdRoute = routeForView("cicd");
  const appsRoute = routeForView("apps");
  const appFallbackRoles = ALL_ROLES.filter(
    (role) => appsRoute.roles.includes(role) && !cicdRoute.roles.includes(role),
  );

  it.each(appFallbackRoles)("%s at /cicd is redirected to /apps", (role) => {
    renderAt(role, cicdRoute.path);
    expect(screen.queryByText("cicd")).not.toBeInTheDocument();
    expect(screen.getByText("apps")).toBeInTheDocument();
  });

  it.each(cicdRoute.roles)("%s at /cicd sees the CICD page", (role) => {
    renderAt(role, cicdRoute.path);
    expect(screen.getByText("cicd")).toBeInTheDocument();
  });
});
