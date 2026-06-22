/**
 * Markdown — THE single sanitized-HTML sink in the entire app.
 *
 * No other component may use dangerouslySetInnerHTML or set innerHTML
 * for Markdown content.  This component is the only place where the
 * renderMarkdown pipeline output (marked + DOMPurify) is injected into
 * the DOM.
 *
 * Mirrors index.html:3607-3645 (renderMarkdownDocument) but as a React
 * component backed by lib/markdown.ts (impl-2's pipeline).
 *
 * Usage:
 *   <Markdown value={snap.doc.intro} />
 *   <Markdown value={article.body_md} className="wiki-md-view" withOutline
 *             onOutline={setOutline} />
 */

import { useEffect, useMemo } from "react";
import { renderMarkdown } from "../lib/markdown";
import type { MarkdownOutlineItem } from "../lib/markdown";

interface MarkdownProps {
  /** Raw Markdown text to render. */
  value: string | null | undefined;
  /** CSS class for the wrapper div (default: "md-view"). */
  className?: string;
  /** Whether to extract and embed anchor IDs for headings (for wiki outline). */
  withOutline?: boolean;
  /**
   * Called after each render with the extracted heading outline.
   * Only fires when withOutline=true.  Stable reference preferred to avoid
   * unnecessary re-runs (wrap in useCallback or store in a ref).
   */
  onOutline?: (outline: MarkdownOutlineItem[]) => void;
  /** Forwarded to the wrapper div (e.g. for test selectors). */
  "data-testid"?: string;
}

export function Markdown({
  value,
  className = "md-view",
  withOutline = false,
  onOutline,
  "data-testid": testId,
}: MarkdownProps) {
  const { html, outline } = useMemo(
    () => renderMarkdown(value, className, withOutline),
    [value, className, withOutline],
  );

  // Fire onOutline whenever the outline changes (only when withOutline=true).
  useEffect(() => {
    if (withOutline && onOutline) {
      onOutline(outline);
    }
  }, [outline, withOutline, onOutline]);

  // Safe: html is the output of DOMPurify.sanitize() from lib/markdown.ts.
  // This is the ONLY component allowed to set innerHTML for Markdown content.
  return <div dangerouslySetInnerHTML={{ __html: html }} data-testid={testId} />;
}
