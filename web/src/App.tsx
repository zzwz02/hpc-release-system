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
import { BrowserRouter } from "react-router-dom";
import { useAuth } from "./api/AuthContext";
import { LoginPage } from "./features/auth/LoginPage";
import { TabNav } from "./routes/TabNav";
import { AppRouter } from "./routes/AppRouter";
import "./App.css";

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
