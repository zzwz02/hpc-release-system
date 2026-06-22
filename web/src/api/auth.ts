/**
 * Auth API wrappers — mirrors legacy index.html:5112-5130, 5186-5198.
 *
 * L2: User and LdapStatus are the canonical types from types/index.ts.
 * Re-exported here for convenience so callers don't need two imports.
 */
import { apiGet, apiPost } from "./http";
import type { User, LdapStatusResponse } from "../types";

// Re-export so legacy callers of `import { User } from "./auth"` still work.
export type { User, LdapStatusResponse as LdapStatus };

/** GET /api/ldap/status — never throws; returns {enabled:false} on failure. */
export async function fetchLdapStatus(): Promise<LdapStatusResponse> {
  try {
    return await apiGet<LdapStatusResponse>("/api/ldap/status");
  } catch {
    return { enabled: false, uri: "" };
  }
}

/** GET /api/me — returns the current session user, or throws if not logged in.
 *
 * The backend returns {"user": null} (HTTP 200) when no session exists — NOT a
 * 401.  We normalise that to a thrown error so callers can treat it uniformly
 * as "not authenticated".
 */
export async function fetchMe(): Promise<User> {
  const data = await apiGet<{ user: User | null }>("/api/me");
  if (!data.user) throw new Error("not authenticated");
  return data.user;
}

/** POST /api/login */
export async function loginLocal(username: string, password: string): Promise<void> {
  await apiPost("/api/login", { username, password });
}

/** POST /api/login/ldap */
export async function loginLdap(username: string, password: string): Promise<void> {
  await apiPost("/api/login/ldap", { username, password });
}

/** POST /api/logout */
export async function logoutApi(): Promise<void> {
  await apiPost("/api/logout", {});
}
