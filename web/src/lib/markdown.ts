/**
 * Markdown rendering pipeline.
 *
 * Mirrors index.html:3607-3645 (renderMarkdownDocument) using marked +
 * DOMPurify as proper npm dependencies (not vendored).
 *
 * Pipeline:
 *   1. marked.parse(text, { gfm: true, breaks: false })
 *   2. DOMPurify.sanitize(html, { ADD_ATTR: ["target", "rel", "loading"] })
 *   3. Post-process: set target="_blank" rel="noopener noreferrer" on all <a>
 *   4. Post-process: set loading="lazy" and empty alt on <img> tags
 *   5. Optionally extract heading outline (for wiki outline sidebar)
 *
 * The Markdown component (impl-3) is the SOLE consumer that renders the
 * resulting HTML via dangerouslySetInnerHTML — never render it elsewhere.
 */

import { marked } from "marked";
import DOMPurify from "dompurify";

export interface MarkdownOutlineItem {
  id: string;
  title: string;
  level: number;
}

export interface RenderedMarkdown {
  /** Sanitized HTML string, wrapped in <div class="..."> */
  html: string;
  /** Heading outline, only populated when withOutline=true */
  outline: MarkdownOutlineItem[];
}

/**
 * Generate a slug for a heading (for wiki outline anchors).
 *
 * Mirrors index.html wikiSlug: strips non-alphanumeric, lowercases, uses
 * an index suffix to guarantee uniqueness within a document.
 */
function wikiSlug(text: string, index: number): string {
  const base = text
    .trim()
    .toLowerCase()
    .replace(/[^\w一-鿿-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return base ? `${base}-${index}` : `heading-${index}`;
}

/**
 * Render a Markdown string to sanitized HTML.
 *
 * @param value      Raw Markdown text
 * @param className  CSS class for the wrapper div (default "md-view")
 * @param withOutline  Whether to extract and return a heading outline
 * @returns { html, outline }
 */
export function renderMarkdown(
  value: string | null | undefined,
  className = "md-view",
  withOutline = false,
): RenderedMarkdown {
  const text = (value ?? "").trim();

  if (!text) {
    return {
      html: `<div class="${className} empty">-</div>`,
      outline: [],
    };
  }

  // 1. Parse markdown
  const rawHtml = marked.parse(text, { gfm: true, breaks: false }) as string;

  // 2. Sanitize — allow target/rel/loading attributes so we can set them next
  const sanitized = DOMPurify.sanitize(rawHtml, {
    ADD_ATTR: ["target", "rel", "loading"],
  });

  // 3 & 4. Post-process using a detached <template> (no live DOM side-effects)
  const tpl = document.createElement("template");
  tpl.innerHTML = sanitized;

  tpl.content.querySelectorAll("a[href]").forEach((a) => {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  });

  tpl.content.querySelectorAll("img").forEach((img) => {
    img.setAttribute("loading", "lazy");
    if (!img.getAttribute("alt")) img.setAttribute("alt", "");
  });

  // 5. Extract outline
  const outline: MarkdownOutlineItem[] = [];
  if (withOutline) {
    tpl.content.querySelectorAll("h1,h2,h3,h4").forEach((h, index) => {
      const textContent = h.textContent ?? "";
      const id = wikiSlug(textContent, index);
      h.id = id;
      outline.push({
        id,
        title: textContent,
        level: Number(h.tagName.slice(1)) || 1,
      });
    });
  }

  return {
    html: `<div class="${className}">${tpl.innerHTML}</div>`,
    outline,
  };
}

/**
 * Convenience wrapper for the wiki markdown view (adds "wiki-md-view" class).
 * Mirrors index.html:3648 (renderWikiMarkdown).
 */
export function renderWikiMarkdown(
  value: string | null | undefined,
  withOutline = false,
): RenderedMarkdown {
  return renderMarkdown(value, "md-view wiki-md-view", withOutline);
}
