/**
 * Release phase helpers.
 *
 * Mirrors index.html:1671-1675:
 *   function releaseLocked()     { return !!state.release?.released_locked; }
 *   function phase()             { return state.release?.phase || ""; }
 *   function beforeAppFreeze()   { return phase() === "before_app_freeze"; }
 *   function beforeDocDeadline() { return phase() === "before_app_freeze" || phase() === "after_app_freeze"; }
 *
 * All functions take a ReleaseDetail (or ReleaseSummary) so they can be
 * called from any component without reading global state.
 */

import type { ReleaseSummary, ReleaseDecision } from "../types";

/** True when the release has been final-locked. */
export function releaseLocked(
  release: ReleaseSummary | null | undefined,
): boolean {
  return !!release?.released_locked;
}

/** Return the phase string, or "" when no release is selected. */
export function releasePhase(
  release: ReleaseSummary | null | undefined,
): string {
  return release?.phase ?? "";
}

/**
 * True when we are before the app-freeze deadline.
 * Mirrors index.html:1673.
 */
export function beforeAppFreeze(
  release: ReleaseSummary | null | undefined,
): boolean {
  return releasePhase(release) === "before_app_freeze";
}

/**
 * True when we are before the doc deadline (either phase 1 or 2).
 * Mirrors index.html:1674.
 */
export function beforeDocDeadline(
  release: ReleaseSummary | null | undefined,
): boolean {
  const p = releasePhase(release);
  return p === "before_app_freeze" || p === "after_app_freeze";
}

/**
 * Decision options available when creating a new app.
 *
 * Before the app-freeze deadline all three options are available; after the
 * freeze only cicd_only and stopped are allowed.
 * Mirrors index.html:1675 (newAppDecisionOptions).
 */
export function newAppDecisionOptions(
  release: ReleaseSummary | null | undefined,
): ReleaseDecision[] {
  if (beforeAppFreeze(release)) {
    return ["release", "cicd_only", "stopped"];
  }
  return ["cicd_only", "stopped"];
}
