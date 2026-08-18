/**
 * Route / tab configuration.
 *
 * Each entry maps a path to a tab label + the roles allowed to see/access it.
 * The order here defines the left-to-right tab order.
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

export interface RouteConfig {
  path: string;
  /** The `data-view` key from the legacy tab nav */
  view: RouteView;
  label: string;
  /** Roles that may see this tab.  Empty = nobody (placeholder). */
  roles: readonly Role[];
}

export const ROUTES: RouteConfig[] = [
  { path: "/",          view: "dashboard", label: "总览",       roles: rolesForTab("dashboard") },
  { path: "/init",      view: "init",      label: "周期管理",   roles: rolesForTab("init") },
  { path: "/apps",      view: "apps",      label: "App 工作台", roles: rolesForTab("apps") },
  { path: "/qa",        view: "qa",        label: "QA",         roles: rolesForTab("qa") },
  { path: "/artifacts", view: "artifacts", label: "发布文档",   roles: rolesForTab("artifacts") },
  { path: "/cicd",      view: "cicd",      label: "CICD 工作台", roles: rolesForTab("cicd") },
  { path: "/jenkins-failures", view: "jenkins-failures", label: "jenkins失败查询", roles: rolesForTab("jenkins-failures") },
  { path: "/cicd-assistant",   view: "cicd-assistant",   label: "CICD助手",       roles: rolesForTab("cicd-assistant") },
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
