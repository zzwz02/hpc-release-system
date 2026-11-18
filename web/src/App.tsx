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
import { BrowserRouter, useLocation } from "react-router-dom";
import { useAuth } from "./api/AuthContext";
import { apiGet } from "./api/http";
import { useUiStore } from "./store/uiStore";
import type { StatePayload } from "./types";
import { LoginPage } from "./features/auth/LoginPage";
import { AccountMenu } from "./features/auth/AccountMenu";
import { TabNav } from "./routes/TabNav";
import { AppRouter } from "./routes/AppRouter";
import { Toaster } from "./components/Toaster";
import { ConfirmHost } from "./components/ConfirmHost";
import { qaPaneFromSearch } from "./features/qa/qaNavigation";
import { pathMatches, routeForView } from "./routes/routeConfig";
import "./App.css";

/**
 * Seeds the shared `selectedReleaseId` from the current release once after
 * login, regardless of which tab is the entry point. Without this, a hard
 * refresh / direct nav to a release-dependent tab that gates its /api/state
 * query on a release id (QA, 发布文档) deadlocks — the id is never set because
 * the query never runs, and the query never runs because the id is unset —
 * leaving the page blank below the header. One-shot, only while unset, no
 * polling (R2-compliant). QA report selects its cycle through its aggregate
 * response and skips this extra state read.
 */
function ReleaseSeeder() {
  const { pathname, search } = useLocation();
  const qaReportSelectsRelease = pathMatches(pathname, routeForView("qa").path) && qaPaneFromSearch(search) === "mark";
  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId);
  const setSelectedReleaseId = useUiStore((s) => s.setSelectedReleaseId);
  useEffect(() => {
    if (selectedReleaseId || qaReportSelectsRelease) return;
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
  }, [selectedReleaseId, setSelectedReleaseId, qaReportSelectsRelease]);
  return null;
}

function AppShell() {
  const { user, logout } = useAuth();

  // null = bootstrapping
  if (user === null) {
    return (
      <div id="loadingPage" className="fullscreen-center">
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
      <header className="topbar has-account-settings">
        <div className="brand">
          <span className="logo">HPC</span>
          <span>发布信息协作系统</span>
        </div>
        <div className="spacer" />
        <div id="sessionBox" className="sessionbox">
          <span className="userchip">
            <span className="avatar" id="meAvatar">{avatar}</span>
            <span id="meBox" title={label}>{label}</span>
          </span>
          <AccountMenu key={user.username} user={user} logout={logout} />
        </div>
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
      <Toaster />
      <ConfirmHost />
    </BrowserRouter>
  );
}
