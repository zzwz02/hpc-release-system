/**
 * Tests for the markdown rendering pipeline.
 *
 * Run under jsdom (configured in vitest.config.ts).
 * The marked + DOMPurify pipeline requires a DOM environment.
 */
import { describe, it, expect } from "vitest";
import { renderMarkdown, renderWikiMarkdown } from "../markdown";

describe("renderMarkdown — empty / falsy input", () => {
  it("returns empty-div HTML for empty string", () => {
    const { html, outline } = renderMarkdown("");
    expect(html).toContain("empty");
    expect(html).toContain("-");
    expect(outline).toEqual([]);
  });

  it("returns empty-div HTML for null", () => {
    const { html } = renderMarkdown(null);
    expect(html).toContain("empty");
  });

  it("returns empty-div HTML for whitespace-only", () => {
    const { html } = renderMarkdown("   ");
    expect(html).toContain("empty");
  });
});

describe("renderMarkdown — basic content", () => {
  it("wraps output in a div with the given className", () => {
    const { html } = renderMarkdown("hello", "my-class");
    expect(html.startsWith('<div class="my-class">')).toBe(true);
    expect(html.endsWith("</div>")).toBe(true);
  });

  it("defaults className to md-view", () => {
    const { html } = renderMarkdown("hello");
    expect(html).toContain('class="md-view"');
  });

  it("renders a heading", () => {
    const { html } = renderMarkdown("# Hello World");
    expect(html).toContain("<h1");
    expect(html).toContain("Hello World");
  });

  it("renders bold text", () => {
    const { html } = renderMarkdown("**bold**");
    expect(html).toContain("<strong>bold</strong>");
  });

  it("renders a paragraph", () => {
    const { html } = renderMarkdown("just text");
    expect(html).toContain("just text");
  });
});

describe("renderMarkdown — link target/rel", () => {
  it("adds target=_blank and rel=noopener to links", () => {
    const { html } = renderMarkdown("[click](https://example.com)");
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });
});

describe("renderMarkdown — img loading=lazy", () => {
  it("adds loading=lazy to images", () => {
    const { html } = renderMarkdown("![alt](https://example.com/img.png)");
    expect(html).toContain('loading="lazy"');
  });
});

describe("renderMarkdown — XSS sanitization", () => {
  it("strips script tags", () => {
    const { html } = renderMarkdown('<script>alert("xss")</script> text');
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("alert");
  });

  it("strips onerror attributes", () => {
    const { html } = renderMarkdown('<img src="x" onerror="alert(1)">');
    expect(html).not.toContain("onerror");
  });
});

describe("renderMarkdown — outline extraction", () => {
  it("returns empty outline when withOutline=false (default)", () => {
    const { outline } = renderMarkdown("# H1\n## H2");
    expect(outline).toEqual([]);
  });

  it("extracts headings with ids when withOutline=true", () => {
    const { html, outline } = renderMarkdown("# Title\n## Subtitle", "md-view", true);
    expect(outline).toHaveLength(2);
    expect(outline[0].title).toBe("Title");
    expect(outline[0].level).toBe(1);
    expect(outline[1].title).toBe("Subtitle");
    expect(outline[1].level).toBe(2);
    // IDs should be injected into the HTML
    expect(html).toContain('id="');
  });
});

describe("renderWikiMarkdown", () => {
  it("adds wiki-md-view class", () => {
    const { html } = renderWikiMarkdown("hello");
    expect(html).toContain("wiki-md-view");
    expect(html).toContain("md-view");
  });

  it("supports outline extraction", () => {
    const { outline } = renderWikiMarkdown("# Top\n### Deep", true);
    expect(outline).toHaveLength(2);
    expect(outline[0].level).toBe(1);
    expect(outline[1].level).toBe(3);
  });
});
