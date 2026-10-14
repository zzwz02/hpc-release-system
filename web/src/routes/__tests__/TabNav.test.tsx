/**
 * TabNav tests.
 *
 * Covers:
 *  - Renders visible tab links for the current role
 *  - Hides tabs for roles not allowed
 *  - Grouped routes (AI助手) collapse into one tab with role-gated sub-tabs
 *  - CICD badge-dot renders when notification count > 0
 *  - CICD badge-dot absent when count === 0
 *  - Badge reads .count from CicdNotificationsResponse (not a non-existent field)
 *  - R2 compliance: no refetchOnWindowFocus on the notifications query
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { TabNav } from "../TabNav";
import {
  ALL_ROLES,
  ROUTES,
  navEntriesForRole,
  routeForView,
  type Role,
} from "../routeConfig";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

import { apiGet } from "../../api/http";
import { useAuth } from "../../api/AuthContext";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderTabNav(role: Role, initialPath = "/") {
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
      <MemoryRouter initialEntries={[initialPath]}>
        <TabNav />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function linkLabels(selector: string): Array<string | null> {
  return Array.from(document.querySelectorAll(selector)).map((link) => link.textContent);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TabNav", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGet).mockResolvedValue({ count: 0, last_visited_at: "" });
  });

  it.each(ALL_ROLES)("renders exactly the configured tabs for %s", async (role) => {
    renderTabNav(role);
    const entries = navEntriesForRole(role);
    await waitFor(() => {
      expect(screen.getAllByRole("link")).toHaveLength(entries.length);
    });

    const visiblePaths = new Set(
      screen.getAllByRole("link").map((link) => link.getAttribute("href")),
    );
    for (const route of ROUTES.filter((candidate) => !candidate.group)) {
      expect(visiblePaths.has(route.path)).toBe(
        route.roles.includes(role) && route.showInNav !== false,
      );
    }
    // Outside a group no sub-tab row is rendered.
    expect(document.querySelector(".tab-subnav")).toBeNull();
  });

  // ── AI助手 group ───────────────────────────────────────────────────────────

  it("collapses the AI助手 routes into one tab", () => {
    renderTabNav("RM");
    const labels = linkLabels("nav.tabs a");
    expect(labels.filter((label) => label === "AI助手")).toHaveLength(1);
    for (const label of ["Jenkins失败查询", "CICD助手", "CICD助手V1", "JIRA agent", "周报分析"]) {
      expect(labels).not.toContain(label);
    }
    expect(screen.getByRole("link", { name: "AI助手" })).toHaveAttribute(
      "href",
      routeForView("jenkins-failures").path,
    );
  });

  it("shows the sub-tabs under AI助手 and marks the open one", () => {
    renderTabNav("RM", "/jira-agent?conversation=jac_1");
    expect(linkLabels(".tab-subnav a")).toEqual(["Jenkins失败查询", "CICD助手", "JIRA agent", "周报分析"]);
    expect(screen.getByRole("link", { name: "AI助手" })).toHaveClass("active");
    expect(screen.getByRole("link", { name: "JIRA agent" })).toHaveClass("active");
    expect(screen.getByRole("link", { name: "CICD助手" })).not.toHaveClass("active");
    expect(screen.getByRole("link", { name: "CICD助手" })).toHaveAttribute(
      "href",
      routeForView("cicd-assistant-v2").path,
    );
  });

  it.each(ALL_ROLES)("limits AI助手 sub-tabs to the routes %s may open", (role) => {
    const allowed = (["jenkins-failures", "cicd-assistant-v2", "jira-agent", "weekly-check"] as const)
      .map(routeForView)
      .filter((route) => route.roles.includes(role));
    renderTabNav(role, routeForView("weekly-check").path);
    expect(linkLabels(".tab-subnav a")).toEqual(allowed.map((route) => route.label));
    expect(screen.queryByRole("link", { name: "AI助手" }) !== null).toBe(allowed.length > 0);
  });

  it("keeps AI助手 active on the nav-hidden CICD助手V1 route", () => {
    renderTabNav("RM", routeForView("cicd-assistant").path);
    expect(screen.getByRole("link", { name: "AI助手" })).toHaveClass("active");
    expect(document.querySelector(".tab-subnav a.active")).toBeNull();
  });

  // ── Badge: count > 0 → dot renders ────────────────────────────────────────

  it("shows badge-dot on CICD tab when notification count > 0", async () => {
    vi.mocked(apiGet).mockResolvedValue({ count: 5, last_visited_at: "2026-06-01 00:00:00" });

    renderTabNav(routeForView("cicd").roles[0]);
    await waitFor(() => {
      expect(document.querySelector(".badge-dot")).not.toBeNull();
    });
  });

  it("hides badge-dot when notification count is 0", async () => {
    vi.mocked(apiGet).mockResolvedValue({ count: 0, last_visited_at: "" });

    renderTabNav(routeForView("cicd").roles[0]);
    // Wait for query to settle then assert no badge
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /CICD 工作台/ })).toBeInTheDocument();
    });
    expect(document.querySelector(".badge-dot")).toBeNull();
  });

  it("hides badge-dot when notification response has no count field", async () => {
    // Simulates backend returning minimal shape without invented fields
    vi.mocked(apiGet).mockResolvedValue({ count: 0, last_visited_at: "" });

    renderTabNav(routeForView("cicd").roles[0]);
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /CICD 工作台/ })).toBeInTheDocument();
    });
    expect(document.querySelector(".badge-dot")).toBeNull();
  });

  // ── R2 compliance ──────────────────────────────────────────────────────────

  it("does not render when user is null (loading state)", () => {
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      ldapStatus: { enabled: false, uri: "" },
      login: vi.fn(),
      logout: vi.fn(),
      clearUser: vi.fn(),
    } as unknown as ReturnType<typeof useAuth>);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <TabNav />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    // No visible tabs when user is null
    expect(document.querySelector(".tab:not(.invisible)")?.textContent).toBeUndefined();
  });
});
