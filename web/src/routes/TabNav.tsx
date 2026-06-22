/**
 * TabNav — renders the 8 role-gated tab buttons.
 *
 * Mirrors legacy tab nav index.html:569-578 + renderRoleChrome:1769-1784.
 * Tabs whose roles don't include the current user's role are hidden (not
 * disabled) — identical to the legacy `el.hidden = !allowed` behavior.
 *
 * The CICD tab shows a notification badge (red dot) when count > 0.
 * Mirrors legacy cicdBadge logic (index.html:3940).
 * Badge clears when CicdPage mounts (markCicdVisited + invalidateQueries).
 */
import { NavLink } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../api/AuthContext";
import { useUiStore } from "../store/uiStore";
import { ROUTES, type Role } from "./routeConfig";
import {
  CICD_NOTIFICATIONS_KEY,
  fetchCicdNotifications,
} from "../features/cicd/cicdApi";

export function TabNav() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const role = user?.role as Role | undefined;

  // R2: staleTime:Infinity, no focus-refetch.  Badge refreshes via
  // invalidateQueries called by CicdPage on mount (after mark-visited).
  const { data: notifData } = useQuery({
    queryKey: CICD_NOTIFICATIONS_KEY,
    queryFn: fetchCicdNotifications,
    enabled: !!user,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: true,
    refetchOnReconnect: false,
    refetchInterval: 1000,
  });

  // Backend /api/cicd/notifications returns {count, last_visited_at}
  // (cicd_repo.py:456).  Badge shows whenever count > 0.
  const cicdBadge = (notifData?.count ?? 0) > 0;

  const visibleRoutes = role
    ? ROUTES.filter((r) => r.roles.includes(role))
    : [];

  // F3: when the App 工作台 detail form has unsaved edits, confirm before
  // navigating to another tab. Reads the shared store at click time so it
  // never interferes with navigation when the form is clean.
  function refreshRoute(view: string) {
    if (view === "artifacts") return;
    if (["dashboard", "init", "apps", "qa", "admin"].includes(view)) {
      void queryClient.invalidateQueries({ queryKey: ["state"] });
    }
    if (view === "qa") {
      void queryClient.invalidateQueries({ queryKey: ["qa-reports"] });
    }
    if (view === "cicd") {
      void queryClient.invalidateQueries({ queryKey: ["cicd"] });
    }
    if (view === "wiki") {
      void queryClient.invalidateQueries({ queryKey: ["wiki"] });
    }
  }

  function handleNavClick(e: React.MouseEvent<HTMLAnchorElement>, view: string) {
    if (useUiStore.getState().appDetailDirty) {
      if (!window.confirm("有未保存的修改，确认放弃并离开?")) {
        e.preventDefault();
        return;
      }
    }
    refreshRoute(view);
  }

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
            onClick={(e) => {
              handleNavClick(e, route.view);
            }}
          >
            {route.view === "cicd" && cicdBadge ? (
              <>
                {route.label}
                <span
                  className="badge-dot"
                  style={{
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: "var(--danger, #e53e3e)",
                    marginLeft: 4,
                    verticalAlign: "middle",
                  }}
                />
              </>
            ) : (
              route.label
            )}
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
