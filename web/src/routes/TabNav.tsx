/**
 * TabNav — renders the role-gated tab buttons.
 *
 * Mirrors legacy tab nav index.html:569-578 + renderRoleChrome:1769-1784.
 * Tabs whose roles don't include the current user's role are hidden (not
 * disabled) — identical to the legacy `el.hidden = !allowed` behavior.
 *
 * Routes sharing a group (AI助手) render as one tab; while one of them is
 * open, a second row lists the group's sub-tabs the role may open.
 *
 * The CICD tab shows a notification badge (red dot) when count > 0.
 * Mirrors legacy cicdBadge logic (index.html:3940).
 * Badge clears when CicdPage mounts (markCicdVisited + invalidateQueries).
 */
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../api/AuthContext";
import { useUiStore } from "../store/uiStore";
import { confirmDialog } from "../lib/confirm";
import { navEntriesForRole, pathMatches, type Role } from "./routeConfig";
import {
  CICD_NOTIFICATIONS_KEY,
  fetchCicdNotifications,
} from "../features/cicd/cicdApi";

export function TabNav() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { pathname } = useLocation();
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

  const entries = role ? navEntriesForRole(role) : [];
  const activeGroup = entries.find(
    (entry) => entry.children.length > 0
      && entry.paths.some((path) => pathMatches(pathname, path)),
  );

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
    if (["jenkins-failures", "cicd-assistant", "cicd-assistant-v2"].includes(view)) {
      void queryClient.invalidateQueries({ queryKey: ["cicd-agent"] });
    }
    if (view === "jira-agent") {
      void queryClient.invalidateQueries({ queryKey: ["jira-agent"] });
    }
    if (view === "weekly-report") {
      void queryClient.invalidateQueries({ queryKey: ["weekly-report"] });
    }
    if (view === "wiki") {
      void queryClient.invalidateQueries({ queryKey: ["wiki"] });
    }
  }

  function handleNavClick(
    e: React.MouseEvent<HTMLAnchorElement>,
    view: string,
    path: string,
  ) {
    if (useUiStore.getState().appDetailDirty) {
      // Block navigation now; resume it programmatically if confirmed.
      e.preventDefault();
      void confirmDialog({
        title: "未保存的修改",
        body: "有未保存的修改，确认放弃并离开?",
        confirmText: "放弃修改",
        danger: true,
      }).then((ok) => {
        if (!ok) return;
        useUiStore.getState().setAppDetailDirty(false);
        navigate(path);
        refreshRoute(view);
      });
      return;
    }
    refreshRoute(view);
  }

  return (
    <>
      <nav className="tabs">
        {entries.map((entry) => {
          const { route } = entry;
          const groupActive = entry === activeGroup;
          return (
            <NavLink
              key={entry.key}
              to={route.path}
              className={({ isActive }) =>
                isActive || groupActive ? "tab active" : "tab"
              }
              end={route.path === "/"}
              onClick={(e) => {
                handleNavClick(e, route.view, route.path);
              }}
            >
              {route.view === "cicd" && cicdBadge ? (
                <>
                  {entry.label}
                  <span className="badge-dot" />
                </>
              ) : (
                entry.label
              )}
            </NavLink>
          );
        })}
        {/* Keep a hidden placeholder so the nav retains height when empty */}
        {entries.length === 0 && (
          <span className="tab invisible">&nbsp;</span>
        )}
      </nav>
      {activeGroup && (
        <nav className="tab-subnav" aria-label={activeGroup.label}>
          <div className="subtabs">
            {activeGroup.children.map((route) => (
              <NavLink
                key={route.view}
                to={route.path}
                className={({ isActive }) =>
                  isActive ? "subtab active" : "subtab"
                }
                onClick={(e) => {
                  handleNavClick(e, route.view, route.path);
                }}
              >
                {route.label}
              </NavLink>
            ))}
          </div>
        </nav>
      )}
    </>
  );
}
