/**
 * TabNav — renders the 8 role-gated tab buttons.
 *
 * Mirrors legacy tab nav index.html:569-578 + renderRoleChrome:1769-1784.
 * Tabs whose roles don't include the current user's role are hidden (not
 * disabled) — identical to the legacy `el.hidden = !allowed` behavior.
 */
import { NavLink } from "react-router-dom";
import { useAuth } from "../api/AuthContext";
import { ROUTES, type Role } from "./routeConfig";

export function TabNav() {
  const { user } = useAuth();
  const role = user?.role as Role | undefined;

  const visibleRoutes = role
    ? ROUTES.filter((r) => r.roles.includes(role))
    : [];

  return (
    <nav className="tabs">
      {ROUTES.map((route) => {
        const visible = role ? route.roles.includes(role) : false;
        if (!visible) return null;
        return (
          <NavLink
            key={route.view}
            to={route.path}
            className={({ isActive }) =>
              isActive ? "tab active" : "tab"
            }
            end={route.path === "/"}
          >
            {route.label}
          </NavLink>
        );
      })}
      {/* Keep a hidden placeholder so the nav retains height when empty */}
      {visibleRoutes.length === 0 && (
        <span className="tab" style={{ visibility: "hidden" }}>&nbsp;</span>
      )}
    </nav>
  );
}
