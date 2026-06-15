/**
 * Route / tab configuration — mirrors tab nav index.html:570-577.
 *
 * Each entry maps a path to a tab label + the roles allowed to see/access it.
 * The order here defines the left-to-right tab order.
 */

export type Role = "RM" | "Owner" | "QA" | "Guest" | "Admin" | "SPD";

export interface RouteConfig {
  path: string;
  /** The `data-view` key from the legacy tab nav */
  view: string;
  label: string;
  /** Roles that may see this tab.  Empty = nobody (placeholder). */
  roles: Role[];
}

export const ROUTES: RouteConfig[] = [
  { path: "/",          view: "dashboard", label: "总览",      roles: ["RM", "Owner", "QA", "Guest"] },
  { path: "/init",      view: "init",      label: "周期管理",  roles: ["RM"] },
  { path: "/apps",      view: "apps",      label: "App 工作台",roles: ["RM", "Owner", "QA", "Guest"] },
  { path: "/qa",        view: "qa",        label: "QA",        roles: ["RM", "Owner", "QA", "Guest"] },
  { path: "/artifacts", view: "artifacts", label: "发布文档",  roles: ["RM", "Owner", "Guest"] },
  { path: "/cicd",      view: "cicd",      label: "CICD 工作台",roles: ["RM", "SPD"] },
  { path: "/wiki",      view: "wiki",      label: "开发 WIKI", roles: ["RM", "Owner"] },
  { path: "/admin",     view: "admin",     label: "系统管理",  roles: ["Admin"] },
];
