/** App display helpers. */

import type { App } from "../types";

/** Human-readable name: official name plus latest app_info version (from the backend). */
export function displayName(app: Pick<App, "name" | "id"> | null | undefined): string {
  return (app?.name ?? "").trim() || app?.id || "(未命名 app)";
}

export function appSortName(app: Pick<App, "name" | "id"> | null | undefined): string {
  return displayName(app).toLowerCase();
}
