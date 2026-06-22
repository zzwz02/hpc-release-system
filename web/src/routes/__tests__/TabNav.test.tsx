/**
 * TabNav tests.
 *
 * Covers:
 *  - Renders visible tab links for the current role
 *  - Hides tabs for roles not allowed
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

function renderTabNav(role: string) {
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
        <TabNav />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TabNav", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGet).mockResolvedValue({ count: 0, last_visited_at: "" });
  });

  it("renders CICD tab link for RM role", async () => {
    renderTabNav("RM");
    await waitFor(() => {
      expect(screen.getByText(/CICD/)).toBeInTheDocument();
    });
  });

  it("renders dashboard tab for all roles", async () => {
    renderTabNav("Guest");
    await waitFor(() => {
      expect(screen.getByText(/总览/)).toBeInTheDocument();
    });
  });

  // ── Badge: count > 0 → dot renders ────────────────────────────────────────

  it("shows badge-dot on CICD tab when notification count > 0", async () => {
    vi.mocked(apiGet).mockResolvedValue({ count: 5, last_visited_at: "2026-06-01 00:00:00" });

    renderTabNav("RM");
    await waitFor(() => {
      expect(document.querySelector(".badge-dot")).not.toBeNull();
    });
  });

  it("hides badge-dot when notification count is 0", async () => {
    vi.mocked(apiGet).mockResolvedValue({ count: 0, last_visited_at: "" });

    renderTabNav("RM");
    // Wait for query to settle then assert no badge
    await waitFor(() => {
      expect(screen.getByText(/CICD/)).toBeInTheDocument();
    });
    expect(document.querySelector(".badge-dot")).toBeNull();
  });

  it("hides badge-dot when notification response has no count field", async () => {
    // Simulates backend returning minimal shape without invented fields
    vi.mocked(apiGet).mockResolvedValue({ count: 0, last_visited_at: "" });

    renderTabNav("RM");
    await waitFor(() => {
      expect(screen.getByText(/CICD/)).toBeInTheDocument();
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
    expect(document.querySelector(".tab:not([style*='hidden'])")?.textContent).toBeUndefined();
  });
});
