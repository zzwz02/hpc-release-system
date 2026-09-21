/**
 * lazy() for code-split pages, hardened against deploys.
 *
 * Every build rewrites the hashed chunk filenames and deletes the old ones, so
 * a tab left open across a deploy asks for a chunk that no longer exists and
 * the page fails to load.  Reload once instead: the fresh index.html points at
 * the chunks that do exist.  A sessionStorage flag (it has to survive the
 * reload) keeps a genuinely broken chunk from reloading in a loop; without
 * session storage the error is surfaced rather than risking that loop.
 */
import { lazy, type ComponentType } from "react";

const FLAG_PREFIX = "chunk-reloaded:";

function readFlag(key: string): boolean | null {
  try {
    return window.sessionStorage.getItem(FLAG_PREFIX + key) !== null;
  } catch {
    return null;
  }
}

function writeFlag(key: string): void {
  try {
    window.sessionStorage.setItem(FLAG_PREFIX + key, "1");
  } catch {
    /* no session storage: recoverFromChunkError already declined to reload */
  }
}

function clearFlag(key: string): void {
  try {
    window.sessionStorage.removeItem(FLAG_PREFIX + key);
  } catch {
    /* nothing to clear */
  }
}

/**
 * Reload once after a chunk fails to load, or re-raise if we already tried.
 *
 * On the reload path the returned promise stays pending on purpose — the page
 * is being replaced, so settling it would only flash an error first.
 */
export function recoverFromChunkError<T>(
  key: string,
  error: unknown,
  reload: () => void = () => window.location.reload(),
): Promise<T> {
  const reloaded = readFlag(key);
  if (reloaded === null || reloaded) return Promise.reject(error);
  writeFlag(key);
  reload();
  return new Promise<T>(() => {});
}

/** lazy() whose chunk load survives a deploy that replaced the chunk. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- mirrors React.lazy's own bound
export function lazyWithReload<T extends ComponentType<any>>(
  key: string,
  factory: () => Promise<{ default: T }>,
) {
  return lazy(() =>
    factory().then(
      (module) => {
        clearFlag(key);
        return module;
      },
      (error) => recoverFromChunkError<{ default: T }>(key, error),
    ),
  );
}
