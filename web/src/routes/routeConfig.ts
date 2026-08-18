/**
 * Route / tab configuration.
 *
 * Each entry maps a path to a tab label + the roles allowed to see/access it.
 * The order here defines the left-to-right tab order.
 */
import sharedTabPermissions from "../../../shared/tab_permissions.json" with { type: "json" };

export type Role = "RM" | "Owner" | "QA" | "Guest" | "Admin" | "SPD";
export type RouteView =
  | "dashboard"
  | "init"
  | "apps"
  | "qa"
  | "artifacts"
  | "cicd"
  | "jenkins-failures"
  | "cicd-assistant"
  | "wiki"
  | "admin";

export const ALL_ROLES: readonly Role[] = ["RM", "Owner", "QA", "Guest", "Admin", "SPD"];

export interface RouteConfig {
  path: string;
  /** The `data-view` key from the legacy tab nav */
  view: RouteView;
  label: string;
  /** Roles that may see this tab.  Empty = nobody (placeholder). */
  roles: Role[];
}

const rawTabPermissions = sharedTabPermissions as Record<string, string[]>;

function rolesFor(view: RouteView): Role[] {
  const roles = rawTabPermissions[view];
  if (!roles?.length) {
    throw new Error(`Missing shared tab permissions for ${view}`);
  }
  const invalidRoles = roles.filter((role) => !ALL_ROLES.includes(role as Role));
  if (invalidRoles.length > 0 || new Set(roles).size !== roles.length) {
    throw new Error(`Invalid shared tab permissions for ${view}`);
  }
  return roles as Role[];
}

export const ROUTES: RouteConfig[] = [
  { path: "/",          view: "dashboard", label: "总览",       roles: rolesFor("dashboard") },
  { path: "/init",      view: "init",      label: "周期管理",   roles: rolesFor("init") },
  { path: "/apps",      view: "apps",      label: "App 工作台", roles: rolesFor("apps") },
  { path: "/qa",        view: "qa",        label: "QA",         roles: rolesFor("qa") },
  { path: "/artifacts", view: "artifacts", label: "发布文档",   roles: rolesFor("artifacts") },
  { path: "/cicd",      view: "cicd",      label: "CICD 工作台", roles: rolesFor("cicd") },
  { path: "/jenkins-failures", view: "jenkins-failures", label: "jenkins失败查询", roles: rolesFor("jenkins-failures") },
  { path: "/cicd-assistant",   view: "cicd-assistant",   label: "CICD助手",       roles: rolesFor("cicd-assistant") },
  { path: "/wiki",      view: "wiki",      label: "开发 WIKI",  roles: rolesFor("wiki") },
  { path: "/admin",     view: "admin",     label: "系统管理",   roles: rolesFor("admin") },
];

const routeViews = new Set(ROUTES.map((route) => route.view));
const permissionViews = Object.keys(rawTabPermissions);
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
