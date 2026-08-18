/**
 * Role predicate helpers.
 *
 * Mirrors index.html:1657-1665 and index.html:1661-1664.
 *
 * All predicates take a User (or a role string) rather than reading from
 * a global.  The zustand uiStore holds the current user; callers destructure
 * what they need.
 */

import type { User, Snapshot } from "../types";
import { can, canAccessTab } from "./accessControl";

/** True when the user's role is RM. */
export function isRM(user: User | null | undefined): boolean {
  return user?.role === "RM";
}

/** True when the user's role is Owner. */
export function isOwner(user: User | null | undefined): boolean {
  return user?.role === "Owner";
}

/** True when the user's role is QA. */
export function isQA(user: User | null | undefined): boolean {
  return user?.role === "QA";
}

/** True when the user's role is Guest. */
export function isGuest(user: User | null | undefined): boolean {
  return user?.role === "Guest";
}

/** True when the user's role is SPD. */
export function isSPD(user: User | null | undefined): boolean {
  return user?.role === "SPD";
}

/** True when the user's role is Admin. */
export function isAdmin(user: User | null | undefined): boolean {
  return user?.role === "Admin";
}

/**
 * True when the user can view the QA tab.
 * Mirrors index.html:1661.
 */
export function canViewQa(user: User | null | undefined): boolean {
  return canAccessTab(user?.role, "qa");
}

/**
 * True when the user can edit QA annotations.
 * Mirrors index.html:1662.
 */
export function canEditQa(user: User | null | undefined): boolean {
  return can(user?.role, "qa.edit");
}

/**
 * True when the user can generate Markdown artifacts.
 * Mirrors index.html:1663.
 */
export function canGenerateMarkdown(user: User | null | undefined): boolean {
  return can(user?.role, "artifact.generate");
}

/** True when the user can create app entries from App 工作台. */
export function canCreateApp(user: User | null | undefined): boolean {
  return can(user?.role, "app.create");
}

/**
 * True when the user can edit wiki articles.
 * Mirrors index.html:1664.
 */
export function canEditWiki(user: User | null | undefined): boolean {
  return can(user?.role, "wiki.edit");
}

/**
 * True when the user can edit a snapshot (app entry).
 * RM can edit any; Owner can only edit apps they own.
 * Mirrors index.html:1665.
 */
export function canEdit(
  user: User | null | undefined,
  snap: Snapshot | null | undefined,
): boolean {
  if (!user) return false;
  if (snap?.allowed_actions) return snap.allowed_actions.includes("app.edit");
  if (can(user.role, "app.edit.any")) return true;
  if (can(user.role, "app.edit.owned")) {
    return (snap?.owners ?? []).includes(user.username);
  }
  return false;
}

/** True when the user may edit RM-only App metadata fields. */
export function canEditRmFields(user: User | null | undefined): boolean {
  return can(user?.role, "app.edit.rm_fields");
}

/**
 * True when the user is listed as an owner of the snapshot.
 * Mirrors index.html:1737-1739 (isOwnApp).
 */
export function isOwnApp(
  user: User | null | undefined,
  snap: Snapshot | null | undefined,
): boolean {
  if (!user?.username) return false;
  return (snap?.owners ?? []).includes(user.username);
}
