/**
 * Monthly cycle phase helpers.
 *
 * The backend computes ``phase`` (signup → in_progress → qa_closed →
 * released); these helpers only read it.
 */

import type { ReleaseSummary } from "../types";
import { releasePhaseLabel } from "./domainMetadata";

export { releasePhaseLabel };

export function releaseLocked(release: ReleaseSummary | null | undefined): boolean {
  return !!release?.released_locked;
}

export function releasePhase(release: ReleaseSummary | null | undefined): string {
  return release?.phase ?? "";
}

/** Signup is open (before the signup deadline). */
export function signupOpen(release: ReleaseSummary | null | undefined): boolean {
  return releasePhase(release) === "signup";
}

/** Not yet published. */
export function releaseOpen(release: ReleaseSummary | null | undefined): boolean {
  return !!release && !release.released_locked;
}
