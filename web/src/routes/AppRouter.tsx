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
import {
  ROUTES,
  canAccessRoute,
  routeForView,
  type Role,
} from "./routeConfig";

// Placeholder until each feature is implemented by its owner wave.
function Placeholder({ label }: { label: string }) {
  return (
    <section className="view active p-2r muted">
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
import { CicdAssistantPage } from "../features/cicdAgent/CicdAssistantPage";
import { JenkinsFailuresPage } from "../features/cicdAgent/JenkinsFailuresPage";
import { WikiPage } from "../features/wiki/WikiPage";
import { AdminPage } from "../features/admin/AdminPage";

const FEATURE_MAP: Record<string, React.ReactNode> = {
  dashboard: <DashboardPage />,
  init:      <ReleaseCyclePage />,
  apps:      <AppWorkbenchPage />,
  qa:        <QaPage />,
  artifacts: <ArtifactsPage />,
  cicd:      <CicdPage />,
  "jenkins-failures": <JenkinsFailuresPage />,
  "cicd-assistant":   <CicdAssistantPage />,
  wiki:      <WikiPage />,
  admin:     <AdminPage />,
};

function pathMatches(pathname: string, routePath: string): boolean {
  return pathname === routePath || pathname.startsWith(`${routePath}/`);
}

export function AppRouter() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const role = user?.role as Role | undefined;
  const adminRoute = routeForView("admin");
  const cicdRoute = routeForView("cicd");
  const appsRoute = routeForView("apps");

  // Ruling C keeps Admin confined to system management. Direct navigation to
  // every other page, including CICD Agent diagnostics, returns to /admin.
  if (role && canAccessRoute(adminRoute, role) && !pathMatches(pathname, adminRoute.path)) {
    return <Navigate to={adminRoute.path} replace />;
  }

  // Users who can access App 工作台 but not CICD 工作台 are bounced to the
  // former. Both permission checks consume the shared route matrix.
  if (
    role
    && pathMatches(pathname, cicdRoute.path)
    && !canAccessRoute(cicdRoute, role)
    && canAccessRoute(appsRoute, role)
  ) {
    return <Navigate to={appsRoute.path} replace />;
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
                <section className="view active p-2r">
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
