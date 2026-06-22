/**
 * Tests for api/http.ts — the core fetch wrapper.
 *
 * Uses vitest + jsdom; fetch is provided by jsdom/undici.
 * All tests mock global.fetch to avoid any real network calls.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiFetch, apiPost, apiGet, register401Handler } from "../http";

// Helper to create a mock Response
function mockResponse(
  body: unknown,
  status = 200,
): Response {
  const json = JSON.stringify(body);
  return new Response(json, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns parsed JSON on 200 ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockResponse({ foo: "bar" })));
    const result = await apiFetch<{ foo: string }>("/api/foo");
    expect(result.foo).toBe("bar");
  });

  it("always sends credentials: include", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse({}));
    vi.stubGlobal("fetch", fetchMock);
    await apiFetch("/api/foo");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/foo",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("throws on backend error envelope {ok:false,error:'msg'}", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(mockResponse({ ok: false, error: "不允许" }, 400)),
    );
    await expect(apiFetch("/api/foo")).rejects.toThrow("不允许");
  });

  it("throws on non-ok response with no envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 500, statusText: "Internal Server Error" })),
    );
    await expect(apiFetch("/api/foo")).rejects.toThrow();
  });

  it("calls registered 401 handler and throws on 401", async () => {
    const handler = vi.fn();
    register401Handler(handler);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockResponse({ error: "未登录" }, 401),
      ),
    );

    await expect(apiFetch("/api/foo")).rejects.toThrow("未登录");
    expect(handler).toHaveBeenCalledOnce();

    // Restore to no-op to avoid polluting other tests
    register401Handler(() => {});
  });

  it("does not call 401 handler on 400", async () => {
    const handler = vi.fn();
    register401Handler(handler);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(mockResponse({ error: "bad request" }, 400)),
    );

    await expect(apiFetch("/api/foo")).rejects.toThrow("bad request");
    expect(handler).not.toHaveBeenCalled();

    register401Handler(() => {});
  });

  it("falls back to empty object when JSON parse fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("not json", { status: 200 })),
    );
    // An empty parsed body with res.ok=true and no .error should return {}
    const result = await apiFetch("/api/foo");
    expect(result).toEqual({});
  });
});

describe("apiPost", () => {
  it("sends POST with JSON body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await apiPost("/api/login", { username: "u", password: "p" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/login",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ username: "u", password: "p" }),
      }),
    );
  });
});

describe("apiGet", () => {
  it("sends GET", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse({ data: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await apiGet<{ data: number }>("/api/state");
    expect(result.data).toBe(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/state",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});
