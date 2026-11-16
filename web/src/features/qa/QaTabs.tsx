import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../api/AuthContext";
import { canAccessTab } from "../../lib/accessControl";
import { pathMatches, routeForView } from "../../routes/routeConfig";
import { QA_PANES, qaPaneFromSearch } from "./qaNavigation";
import { useUiStore } from "../../store/uiStore";
import { confirmDialog } from "../../lib/confirm";

/** One row for all QA tools, including the historical lookup routes. */
export function QaTabs() {
  const { user } = useAuth();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  if (!user) return null;

  const qaRoute = routeForView("qa");
  const activePane = pathMatches(pathname, qaRoute.path) ? qaPaneFromSearch(search) : null;
  const items = [
    ...(canAccessTab(user.role, qaRoute.view) ? QA_PANES.map((pane) => ({
      key: pane.key,
      label: pane.label,
      path: `${qaRoute.path}?tab=${pane.key}`,
      active: activePane === pane.key,
    })) : []),
    ...(["lookup", "dependencies"] as const).map(routeForView)
      .filter((route) => canAccessTab(user.role, route.view))
      .map((route) => ({
        key: route.view,
        label: route.label,
        path: route.path,
        active: pathMatches(pathname, route.path),
      })),
  ];

  return (
    <nav className="qa-tabs" aria-label="QA">
      <div className="subtabs">
        {items.map((item) => (
          <Link key={item.key} to={item.path} className={`subtab${item.active ? " active" : ""}`}
            onClick={event => {
              if (!useUiStore.getState().componentCompatibilityDirty) return;
              event.preventDefault();
              void confirmDialog({ title: "未保存的修改", body: "有未保存的组件兼容性结论，确认放弃并离开？", danger: true, confirmText: "放弃修改" }).then(ok => {
                if (ok) { useUiStore.getState().setComponentCompatibilityDirty(false); navigate(item.path); }
              });
            }}
            aria-current={item.active ? "page" : undefined}>
            {item.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
