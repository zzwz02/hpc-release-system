/** Shared static access-control policy consumed by both frontend and backend. */
import sharedAccessControl from "../../../shared/access_control.json" with { type: "json" };

export type Role = "RM" | "Owner" | "QA" | "Guest" | "Admin" | "SPD";
export type TabView = keyof typeof sharedAccessControl.tabs;
export type Capability = keyof typeof sharedAccessControl.capabilities;

type RoleMap = Record<string, readonly string[]>;

const roles = sharedAccessControl.roles as string[];
const tabs = sharedAccessControl.tabs as RoleMap;
const capabilities = sharedAccessControl.capabilities as RoleMap;

if (!roles.length || new Set(roles).size !== roles.length) {
  throw new Error("Shared access-control roles must be non-empty and unique");
}

const knownRoles = new Set(roles);
function validateRoleMap(name: string, roleMap: RoleMap): void {
  for (const [key, allowedRoles] of Object.entries(roleMap)) {
    if (
      !allowedRoles.length
      || new Set(allowedRoles).size !== allowedRoles.length
      || allowedRoles.some((role) => !knownRoles.has(role))
    ) {
      throw new Error(`Invalid shared access-control entry: ${name}.${key}`);
    }
  }
}

validateRoleMap("tabs", tabs);
validateRoleMap("capabilities", capabilities);

export const ALL_ROLES = Object.freeze([...roles]) as readonly Role[];
export const TAB_VIEWS = Object.freeze(Object.keys(tabs)) as readonly TabView[];

export function rolesForTab(view: TabView): readonly Role[] {
  const allowedRoles = tabs[view];
  if (!allowedRoles) throw new Error(`Missing shared tab permissions for ${view}`);
  return allowedRoles as readonly Role[];
}

export function rolesForCapability(capability: Capability): readonly Role[] {
  const allowedRoles = capabilities[capability];
  if (!allowedRoles) {
    throw new Error(`Missing shared capability permissions for ${capability}`);
  }
  return allowedRoles as readonly Role[];
}

export function canAccessTab(role: string | null | undefined, view: TabView): boolean {
  return !!role && rolesForTab(view).includes(role as Role);
}

export function can(
  role: string | null | undefined,
  capability: Capability,
): boolean {
  return !!role && rolesForCapability(capability).includes(role as Role);
}

export function capabilitiesForRole(role: string | null | undefined): Capability[] {
  if (!role) return [];
  return (Object.keys(capabilities) as Capability[]).filter((capability) =>
    can(role, capability),
  );
}
