/**
 * App — top-level shell.
 *
 * Mirrors the legacy three-state pattern (index.html):
 *   - loadingPage (null user = still bootstrapping from /api/me)
 *   - loginPage (undefined user = logged out, 401)
 *   - main shell + tabs (User = logged in)
 *
 * The header + sessionBox are rendered here; TabNav provides the tab bar.
 */
import { useEffect } from "react";
import { BrowserRouter } from "react-router-dom";
import { useAuth } from "./api/AuthContext";
import { apiGet } from "./api/http";
import { useUiStore } from "./store/uiStore";
import type { StatePayload } from "./types";
import { LoginPage } from "./features/auth/LoginPage";
import { TabNav } from "./routes/TabNav";
import { AppRouter } from "./routes/AppRouter";
import "./App.css";

/**
 * Seeds the shared `selectedReleaseId` from the current release once after
 * login, regardless of which tab is the entry point. Without this, a hard
 * refresh / direct nav to a release-dependent tab that gates its /api/state
 * query on a release id (QA, 发布文档) deadlocks — the id is never set because
 * the query never runs, and the query never runs because the id is unset —
 * leaving the page blank below the header. One-shot, only while unset, no
 * polling (R2-compliant).
 */
function ReleaseSeeder() {
  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId);
  const setSelectedReleaseId = useUiStore((s) => s.setSelectedReleaseId);
  useEffect(() => {
    if (selectedReleaseId) return;
    let cancelled = false;
    apiGet<StatePayload>("/api/state")
      .then((s) => {
        if (!cancelled && s?.release?.id) setSelectedReleaseId(s.release.id);
      })
      .catch(() => {
        /* no current release / not authed — pages keep their own empty state */
      });
    return () => {
      cancelled = true;
    };
  }, [selectedReleaseId, setSelectedReleaseId]);
  return null;
}

function AppShell() {
  const { user, logout } = useAuth();

  // null = bootstrapping
  if (user === null) {
    return (
      <div id="loadingPage" style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh" }}>
        <span className="muted">加载中…</span>
      </div>
    );
  }

  // undefined = logged out
  if (user === undefined) {
    return <LoginPage />;
  }

  // Logged in
  const label = user.display_name
    ? `${user.display_name} (${user.username})`
    : user.username;
  const avatar = (user.display_name || user.username).charAt(0).toUpperCase();

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="logo">HPC</span>
          <span>发布信息协作系统</span>
        </div>
        <div className="spacer" />
        <span id="sessionBox" className="sessionbox">
          <span className="userchip">
            <span className="avatar" id="meAvatar">{avatar}</span>
            <span id="meBox">{user.role} · {label}</span>
          </span>
          <button
            className="btn ghost sm"
            onClick={() => void logout()}
          >
            退出
          </button>
        </span>
      </header>

      <ReleaseSeeder />
      <TabNav />

      <main className="page">
        <AppRouter />
      </main>
    </>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}
