/**
 * AppRouter — maps ROUTES to lazy-loaded feature pages.
 *
 * Placeholder components are used here for Wave-1; impl-2/impl-3 replace them
 * in Waves 2-3.  Each tab is wrapped in RequireRole so a URL-bar navigation
 * to a forbidden route shows a fallback instead of crashing.
 */
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../api/AuthContext";
import { RequireRole } from "./RequireRole";
import { ROUTES } from "./routeConfig";

// Placeholder until each feature is implemented by its owner wave.
function Placeholder({ label }: { label: string }) {
  return (
    <section className="view active" style={{ padding: "2rem", color: "var(--muted, #888)" }}>
      <h2>{label}</h2>
      <p className="muted">（功能开发中）</p>
    </section>
  );
}

// Feature imports — replace placeholders as waves progress.
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { ReleaseCyclePage } from "../features/init/ReleaseCyclePage";
import { AppWorkbenchPage } from "../features/appWorkbench/AppWorkbenchPage";
import { QaPage } from "../features/qa/QaPage";
import { ArtifactsPage } from "../features/artifacts/ArtifactsPage";
import { CicdPage } from "../features/cicd/CicdPage";
import { WikiPage } from "../features/wiki/WikiPage";
import { AdminPage } from "../features/admin/AdminPage";

const FEATURE_MAP: Record<string, React.ReactNode> = {
  dashboard: <DashboardPage />,
  init:      <ReleaseCyclePage />,
  apps:      <AppWorkbenchPage />,
  qa:        <QaPage />,
  artifacts: <ArtifactsPage />,
  cicd:      <CicdPage />,
  wiki:      <WikiPage />,
  admin:     <AdminPage />,
};

export function AppRouter() {
  const { user } = useAuth();
  const { pathname } = useLocation();

  // Ruling C: Admin sees ONLY /admin — redirect from any other path after login.
  if (user?.role === "Admin" && !pathname.startsWith("/admin")) {
    return <Navigate to="/admin" replace />;
  }

  return (
    <Routes>
      {ROUTES.map((route) => (
        <Route
          key={route.view}
          path={route.path}
          element={
            <RequireRole
              roles={route.roles}
              fallback={
                <section className="view active" style={{ padding: "2rem" }}>
                  <p className="muted">无权限访问此页面。</p>
                </section>
              }
            >
              {FEATURE_MAP[route.view] ?? <Placeholder label={route.label} />}
            </RequireRole>
          }
        />
      ))}
      {/* Catch-all: redirect to root */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
