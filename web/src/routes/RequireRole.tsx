/**
 * RequireRole — renders children only when the current user has one of the
 * allowed roles.  Used to gate both tab content and UI elements.
 *
 * Mirrors legacy renderRoleChrome (index.html:1769-1784) `data-roles` check.
 */
import { useAuth } from "../api/AuthContext";
import type { Role } from "./routeConfig";

interface Props {
  roles: Role[];
  children: React.ReactNode;
  /** Optional fallback rendered when the role check fails. */
  fallback?: React.ReactNode;
}

export function RequireRole({ roles, children, fallback = null }: Props) {
  const { user } = useAuth();

  if (!user) return <>{fallback}</>;
  if (!roles.includes(user.role as Role)) return <>{fallback}</>;

  return <>{children}</>;
}
