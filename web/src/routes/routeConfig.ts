/**
 * Route / tab configuration.
 *
 * Each entry maps a path to a tab label + the roles allowed to see/access it.
 * The order here defines the left-to-right tab order.  Routes sharing a
 * `group` collapse into one top-level tab and show as sub-tabs beneath it;
 * their paths and permissions stay per-route.
 */
import {
  ALL_ROLES,
  TAB_VIEWS,
  rolesForTab,
  type Role,
  type TabView,
} from "../lib/accessControl";

export { ALL_ROLES };
export type { Role };
export type RouteView = TabView;

/** Top-level tabs that hold several routes as sub-tabs. */
export const ROUTE_GROUPS = {
  "ai-assistant": { label: "AI助手" },
} as const;
export type RouteGroupId = keyof typeof ROUTE_GROUPS;

export interface RouteConfig {
  path: string;
  /** The `data-view` key from the legacy tab nav */
  view: RouteView;
  label: string;
  /** Roles that may see this tab.  Empty = nobody (placeholder). */
  roles: readonly Role[];
  /** Hide from the tab bars while keeping direct route access. */
  showInNav?: boolean;
  /** Show as a sub-tab of this group's tab instead of a top-level tab. */
  group?: RouteGroupId;
}

export const ROUTES: RouteConfig[] = [
  { path: "/",          view: "dashboard", label: "总览",       roles: rolesForTab("dashboard") },
  { path: "/init",      view: "init",      label: "周期管理",   roles: rolesForTab("init") },
  { path: "/apps",      view: "apps",      label: "App 工作台", roles: rolesForTab("apps") },
  { path: "/qa",        view: "qa",        label: "QA",         roles: rolesForTab("qa") },
  { path: "/artifacts", view: "artifacts", label: "发布文档",   roles: rolesForTab("artifacts") },
  { path: "/cicd",      view: "cicd",      label: "CICD 工作台", roles: rolesForTab("cicd") },
  { path: "/jenkins-failures", view: "jenkins-failures", label: "Jenkins失败查询", roles: rolesForTab("jenkins-failures"), group: "ai-assistant" },
  { path: "/cicd-assistant",   view: "cicd-assistant",   label: "CICD助手V1",     roles: rolesForTab("cicd-assistant"), showInNav: false, group: "ai-assistant" },
  { path: "/cicd-assistant-v2", view: "cicd-assistant-v2", label: "CICD助手",      roles: rolesForTab("cicd-assistant-v2"), group: "ai-assistant" },
  { path: "/jira-agent", view: "jira-agent", label: "JIRA agent", roles: rolesForTab("jira-agent"), group: "ai-assistant" },
  { path: "/wiki",      view: "wiki",      label: "开发 WIKI",  roles: rolesForTab("wiki") },
  { path: "/admin",     view: "admin",     label: "系统管理",   roles: rolesForTab("admin") },
];

const routeViews = new Set(ROUTES.map((route) => route.view));
const permissionViews = TAB_VIEWS;
if (
  routeViews.size !== permissionViews.length
  || permissionViews.some((view) => !routeViews.has(view as RouteView))
) {
  throw new Error("Shared tab permissions and routeConfig views must match exactly");
}

export function routeForView(view: RouteView): RouteConfig {
  const route = ROUTES.find((candidate) => candidate.view === view);
  if (!route) throw new Error(`Missing route configuration for ${view}`);
  return route;
}

export function canAccessRoute(route: RouteConfig, role: Role): boolean {
  return route.roles.includes(role);
}

export function pathMatches(pathname: string, routePath: string): boolean {
  return pathname === routePath || pathname.startsWith(`${routePath}/`);
}

/** One top-level tab: a single route, or a group with its sub-tabs. */
export interface NavEntry {
  key: string;
  label: string;
  /** Where the tab links: the route itself, or the group's first sub-tab. */
  route: RouteConfig;
  /** Sub-tabs the role may open; empty for a single route. */
  children: RouteConfig[];
  /** Paths that mark this tab active, including nav-hidden group members. */
  paths: string[];
}

function showsInNav(route: RouteConfig, role: Role): boolean {
  return canAccessRoute(route, role) && route.showInNav !== false;
}

/** Top-level tabs for a role, in ROUTES order. A group sits where its first member is listed. */
export function navEntriesForRole(role: Role): NavEntry[] {
  const entries: NavEntry[] = [];
  const seenGroups = new Set<RouteGroupId>();
  for (const route of ROUTES) {
    const group = route.group;
    if (!group) {
      if (showsInNav(route, role)) {
        entries.push({ key: route.view, label: route.label, route, children: [], paths: [route.path] });
      }
      continue;
    }
    if (seenGroups.has(group)) continue;
    seenGroups.add(group);
    const members = ROUTES.filter((candidate) => candidate.group === group);
    const children = members.filter((member) => showsInNav(member, role));
    if (!children.length) continue;
    entries.push({
      key: group,
      label: ROUTE_GROUPS[group].label,
      route: children[0],
      children,
      paths: members.map((member) => member.path),
    });
  }
  return entries;
}
