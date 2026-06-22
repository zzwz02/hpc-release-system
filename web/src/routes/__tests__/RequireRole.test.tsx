/**
 * Tests for routes/RequireRole.tsx
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { RequireRole } from "../RequireRole";

// Mock useAuth
vi.mock("../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

import { useAuth } from "../../api/AuthContext";
const mockUseAuth = vi.mocked(useAuth);

function setup(role: string | null | undefined) {
  const user =
    role == null
      ? role
      : { username: "u", display_name: "", role, auth_source: "local" as const };
  // @ts-expect-error mock partial
  mockUseAuth.mockReturnValue({ user, ldapStatus: { enabled: false }, login: vi.fn(), logout: vi.fn(), clearUser: vi.fn() });
}

describe("RequireRole", () => {
  it("renders children when user has a matching role", () => {
    setup("RM");
    render(
      <RequireRole roles={["RM", "Owner"]}>
        <span>allowed</span>
      </RequireRole>,
    );
    expect(screen.getByText("allowed")).toBeInTheDocument();
  });

  it("renders fallback when user role is not in the allowed list", () => {
    setup("Guest");
    render(
      <RequireRole roles={["RM", "Owner"]} fallback={<span>forbidden</span>}>
        <span>allowed</span>
      </RequireRole>,
    );
    expect(screen.queryByText("allowed")).toBeNull();
    expect(screen.getByText("forbidden")).toBeInTheDocument();
  });

  it("renders fallback when user is null (loading)", () => {
    setup(null);
    render(
      <RequireRole roles={["RM"]} fallback={<span>loading</span>}>
        <span>content</span>
      </RequireRole>,
    );
    expect(screen.queryByText("content")).toBeNull();
    expect(screen.getByText("loading")).toBeInTheDocument();
  });

  it("renders fallback when user is undefined (logged out)", () => {
    setup(undefined);
    render(
      <RequireRole roles={["RM"]} fallback={<span>logged-out</span>}>
        <span>content</span>
      </RequireRole>,
    );
    expect(screen.queryByText("content")).toBeNull();
    expect(screen.getByText("logged-out")).toBeInTheDocument();
  });

  it("renders nothing by default when role doesn't match (no fallback prop)", () => {
    setup("QA");
    const { container } = render(
      <RequireRole roles={["RM"]}>
        <span>admin only</span>
      </RequireRole>,
    );
    expect(screen.queryByText("admin only")).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  it("Admin role gated — renders for Admin", () => {
    setup("Admin");
    render(
      <RequireRole roles={["Admin"]}>
        <span>admin panel</span>
      </RequireRole>,
    );
    expect(screen.getByText("admin panel")).toBeInTheDocument();
  });

  it("SPD can access cicd tab", () => {
    setup("SPD");
    render(
      <RequireRole roles={["RM", "Owner", "SPD"]}>
        <span>cicd</span>
      </RequireRole>,
    );
    expect(screen.getByText("cicd")).toBeInTheDocument();
  });
});
