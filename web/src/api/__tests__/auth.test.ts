/**
 * Tests for api/auth.ts.
 *
 * Key case: /api/me returns 200 {user:null} when logged out (NOT a 401).
 * fetchMe() must throw so the bootstrap catch-block transitions to logged-out
 * instead of leaving user=null (stuck loading).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fetchMe, fetchLdapStatus } from "../auth";

// Mock the http module
vi.mock("../http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

import { apiGet } from "../http";
const mockApiGet = vi.mocked(apiGet);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("fetchMe", () => {
  it("returns the user when /api/me returns a populated user object", async () => {
    mockApiGet.mockResolvedValue({
      user: { username: "rm", role: "RM", display_name: "" },
    });
    const user = await fetchMe();
    expect(user.username).toBe("rm");
    expect(user.role).toBe("RM");
  });

  it("throws when /api/me returns {user: null} — logged-out path (H1 fix)", async () => {
    // This is what the backend sends for unauthenticated requests: HTTP 200
    // with {user: null}.  fetchMe must throw so the caller treats it as
    // logged-out rather than leaving user=null (permanent loading state).
    mockApiGet.mockResolvedValue({ user: null });
    await expect(fetchMe()).rejects.toThrow("not authenticated");
  });

  it("throws when /api/me returns {user: undefined}", async () => {
    mockApiGet.mockResolvedValue({});
    await expect(fetchMe()).rejects.toThrow("not authenticated");
  });

  it("propagates errors from apiGet (e.g. 401, network error)", async () => {
    mockApiGet.mockRejectedValue(new Error("network error"));
    await expect(fetchMe()).rejects.toThrow("network error");
  });
});

describe("fetchLdapStatus", () => {
  it("returns enabled:true when backend says so", async () => {
    mockApiGet.mockResolvedValue({ enabled: true, uri: "ldap://..." });
    const status = await fetchLdapStatus();
    expect(status.enabled).toBe(true);
  });

  it("returns {enabled:false} on any error — must not block login", async () => {
    mockApiGet.mockRejectedValue(new Error("connection refused"));
    const status = await fetchLdapStatus();
    expect(status.enabled).toBe(false);
    expect(status.uri).toBe("");
  });

  it("returns {enabled:false} on network failure without throwing", async () => {
    mockApiGet.mockRejectedValue(new TypeError("fetch failed"));
    const status = await fetchLdapStatus();
    expect(status.enabled).toBe(false);
    expect(status.uri).toBe("");
  });
});
