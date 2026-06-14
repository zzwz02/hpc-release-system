/**
 * Auth context — provides the current user and auth actions app-wide.
 *
 * Mirrors legacy showLoggedIn/showLoggedOut/login/logout pattern
 * (index.html:5201-5237).  On mount bootstraps from /api/me; on 401 anywhere
 * in the app it clears the user and forces the login screen.
 */
/* eslint-disable react-refresh/only-export-components */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  fetchMe,
  fetchLdapStatus,
  loginLocal,
  loginLdap,
  logoutApi,
  User,
  LdapStatus,
} from "./auth";
import { register401Handler } from "./http";

interface AuthState {
  /** null = loading; undefined = logged out; User = logged in */
  user: User | null | undefined;
  ldapStatus: LdapStatus;
  login: (
    username: string,
    password: string,
    type: "local" | "ldap",
  ) => Promise<void>;
  logout: () => Promise<void>;
  /** Called by http client on 401; also usable directly. */
  clearUser: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // null = still bootstrapping, undefined = logged out, User = logged in
  const [user, setUser] = useState<User | null | undefined>(null);
  const [ldapStatus, setLdapStatus] = useState<LdapStatus>({ enabled: false });

  const clearUser = useCallback(() => {
    setUser(undefined);
  }, []);

  // Register the 401 hook so any apiFetch call can trigger logout
  useEffect(() => {
    register401Handler(clearUser);
  }, [clearUser]);

  // Bootstrap: fetch ldap status and /api/me on mount (mirrors initPage)
  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      // LDAP status failure must not block login (index.html:5231)
      const ls = await fetchLdapStatus();
      if (!cancelled) setLdapStatus(ls);

      try {
        const me = await fetchMe();
        if (!cancelled) setUser(me);
      } catch {
        // 401 or network error → logged out
        if (!cancelled) setUser(undefined);
      }
    }

    bootstrap();
    return () => { cancelled = true; };
  }, []);

  const login = useCallback(
    async (username: string, password: string, type: "local" | "ldap") => {
      if (type === "ldap") {
        await loginLdap(username, password);
      } else {
        await loginLocal(username, password);
      }
      // After successful login, reload the user
      const me = await fetchMe();
      setUser(me);
    },
    [],
  );

  const logout = useCallback(async () => {
    await logoutApi();
    setUser(undefined);
  }, []);

  return (
    <AuthContext.Provider value={{ user, ldapStatus, login, logout, clearUser }}>
      {children}
    </AuthContext.Provider>
  );
}

/** Access auth state; throws if used outside <AuthProvider>. */
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
