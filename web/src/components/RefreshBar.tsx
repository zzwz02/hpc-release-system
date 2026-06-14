/**
 * RefreshBar — shows "刷新于 <time>" derived from TanStack Query's
 * dataUpdatedAt (the section's own content-fetch time, NOT page load),
 * with an explicit refetch button.
 *
 * This is the core of R2: per-section, content-level refresh display.
 * Mirrors the legacy `data-page-refreshed-at` + `updateRefreshMeta` pattern
 * (index.html:1594-1604) and the `&#x21bb; 刷新` button (index.html:588-589).
 *
 * Usage:
 *   <RefreshBar dataUpdatedAt={query.dataUpdatedAt} onRefresh={() => query.refetch()} />
 */

import { formatClientFetchTime } from "../lib/time";

interface RefreshBarProps {
  /**
   * Epoch-ms timestamp from TanStack Query's query.dataUpdatedAt.
   * 0 or undefined means "not yet fetched" → displays "尚未刷新".
   */
  dataUpdatedAt?: number;
  /** Called when the user clicks the refresh button. */
  onRefresh: () => void;
  /** Whether a fetch is currently in progress (disables button + shows spinner). */
  isFetching?: boolean;
  /** Extra CSS class for the wrapper span. */
  className?: string;
}

export function RefreshBar({
  dataUpdatedAt,
  onRefresh,
  isFetching = false,
  className,
}: RefreshBarProps) {
  const metaText = dataUpdatedAt
    ? `刷新于 ${formatClientFetchTime(dataUpdatedAt)}`
    : "尚未刷新";

  return (
    <>
      <span
        className={["page-refresh-meta", className].filter(Boolean).join(" ")}
        data-testid="refresh-meta"
      >
        {metaText}
      </span>
      <button
        className="btn sm"
        onClick={onRefresh}
        disabled={isFetching}
        title="刷新当前页"
        data-testid="refresh-btn"
        aria-label="刷新当前页"
      >
        {isFetching ? "…" : "↻ 刷新"}
      </button>
    </>
  );
}
