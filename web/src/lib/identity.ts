/**
 * App identity / display helpers.
 *
 * Mirrors index.html helpers for computing display names and sort keys.
 */

import type { Snapshot } from "../types";

/**
 * Human-readable display name for a snapshot.
 *
 * Mirrors index.html:1666-1669 (displayName):
 *   const official = (snap?.official_name || "").trim();
 *   const ver = (snap?.version || "").trim();
 *   return (ver ? `${official} ${ver}` : official).trim() || "(未命名 app)";
 */
export function displayName(snap: Snapshot | null | undefined): string {
  const official = (snap?.official_name ?? "").trim();
  const ver = (snap?.version ?? "").trim();
  return (ver ? `${official} ${ver}` : official).trim() || "(未命名 app)";
}

/**
 * Lowercase sort key for a snapshot.
 * Mirrors index.html:1736 (appSortName).
 */
export function appSortName(snap: Snapshot | null | undefined): string {
  return displayName(snap).toLowerCase();
}

/**
 * Format a release label: "name (maca_version)" when both are present.
 */
export function releaseLabel(
  name: string | undefined,
  macaVersion: string | undefined,
): string {
  if (!name) return "";
  return macaVersion ? `${name} (${macaVersion})` : name;
}
