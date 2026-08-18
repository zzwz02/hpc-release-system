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

/**
 * Streaming NDJSON POST. Each complete JSON line is delivered immediately;
 * chunk boundaries may split or combine lines. Used for long-running batch
 * mutations that need real-time progress without polling.
 */
export async function apiPostNdjson<T = unknown>(
  path: string,
  body: unknown,
  onItem: (item: T) => void,
): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    body: JSON.stringify(body),
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/x-ndjson",
    },
  });

  if (!res.ok) {
    if (res.status === 401) _on401();
    const raw = await res.text().catch(() => "");
    let message = raw || res.statusText;
    try {
      const parsed = JSON.parse(raw) as ApiEnvelope;
      message = parsed.error || message;
    } catch {
      // Keep the plain-text response as the error message.
    }
    throw new Error(message || `HTTP ${res.status}`);
  }
  if (!res.body) {
    throw new Error("服务器未返回批量拉取进度流");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lineNumber = 0;

  const emitLine = (rawLine: string) => {
    const line = rawLine.trim();
    if (!line) return;
    lineNumber += 1;
    try {
      onItem(JSON.parse(line) as T);
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error(`批量拉取进度流第 ${lineNumber} 行不是有效 JSON`);
      }
      throw error;
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) emitLine(line);
    if (done) break;
  }
  emitLine(buffer);
}

/** Convenience GET helper. */
export async function apiGet<T = unknown>(path: string): Promise<T> {
  return apiFetch<T>(path);
}

/**
 * Raw text GET — for endpoints that return plain text or CSV (not JSON envelopes).
 * Returns the response text plus selected headers.
 * On 401 clears the session; on non-OK throws.
 */
export async function apiGetText(path: string): Promise<{
  text: string;
  headers: Headers;
}> {
  const res = await fetch(path, { credentials: "include" });
  if (res.status === 401) {
    _on401();
    throw new Error("Login required");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText);
    throw new Error(body || res.statusText);
  }
  const text = await res.text();
  return { text, headers: res.headers };
}
