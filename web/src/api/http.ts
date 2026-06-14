/**
 * Core HTTP client.
 *
 * Mirrors legacy index.html:1477-1489:
 *   - credentials: "include" for cookie-based hpc_session
 *   - Maps backend {"ok":false,"error":"..."} envelopes to thrown errors
 *   - 401 → calls the registered 401 handler (clear user + show login)
 */

type Handler401 = () => void;

let _on401: Handler401 = () => {
  // Default no-op; replaced by auth context via register401Handler().
};

/** Register the callback invoked on any 401 response (mirrors showLoggedOut). */
export function register401Handler(fn: Handler401): void {
  _on401 = fn;
}

/** Backend error envelope shape. */
interface ApiEnvelope {
  ok?: boolean;
  error?: string;
}

/** Core fetch wrapper. All requests use credentials:'include' for session cookie. */
export async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(path, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  });

  // Parse JSON; fall back to empty object on parse failure (e.g. 204 No Content)
  const data: ApiEnvelope & Record<string, unknown> = await res
    .json()
    .catch(() => ({}));

  if (!res.ok || data.error) {
    if (res.status === 401) _on401();
    throw new Error((data.error as string | undefined) ?? res.statusText);
  }

  return data as T;
}

/** Convenience POST helper (mirrors legacy `post(path, body)`). */
export async function apiPost<T = unknown>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Convenience GET helper. */
export async function apiGet<T = unknown>(path: string): Promise<T> {
  return apiFetch<T>(path);
}
