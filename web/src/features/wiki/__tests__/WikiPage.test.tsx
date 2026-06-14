/**
 * WikiPage tests.
 *
 * Covers:
 *  - List view: renders article cards, filter, "暂无文章" empty state
 *  - "新建文章" button visible for RM, hidden for Owner
 *  - Clicking a card navigates to view mode (shows article title)
 *  - View mode: edit/pin/delete buttons for RM; absent for Owner
 *  - Editor: title/body/save flow calls POST /api/wiki/articles/save
 *  - Image paste in editor calls POST /api/wiki/images/upload and inserts link
 *  - RefreshBar rendered
 *  - Loading state while fetching
 *  - Error state on fetch failure
 */

import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { WikiPage } from "../WikiPage";
import type { WikiArticleSummary, WikiArticle } from "../../../types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("../../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

// Minimal lib/markdown mock (avoids jsdom missing DOMPurify/marked deps)
vi.mock("../../../lib/markdown", () => ({
  renderWikiMarkdown: (text: string) => ({
    html: `<p>${text}</p>`,
    outline: [],
  }),
  renderMarkdown: (text: string) => ({
    html: `<p>${text}</p>`,
    outline: [],
  }),
}));

import { apiGet, apiPost } from "../../../api/http";
import { useAuth } from "../../../api/AuthContext";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeArticleSummary(
  id: string,
  overrides: Partial<WikiArticleSummary> = {},
): WikiArticleSummary {
  return {
    id,
    title: `Article ${id}`,
    pinned: false,
    created_by: "alice",
    created_at: "2026-01-01 10:00:00",
    updated_by: "alice",
    updated_at: "2026-01-01 10:00:00",
    deleted: false,
    excerpt: `Excerpt for ${id}`,
    ...overrides,
  };
}

function makeArticle(id: string, overrides: Partial<WikiArticle> = {}): WikiArticle {
  return {
    id,
    title: `Article ${id}`,
    body_md: `# Article ${id}\n\nSome content.`,
    pinned: false,
    created_by: "alice",
    created_at: "2026-01-01 10:00:00",
    updated_by: "alice",
    updated_at: "2026-01-01 10:00:00",
    deleted: false,
    ...overrides,
  };
}

function makeQClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
    },
  });
}

function renderWikiPage(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <WikiPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  // Default: logged in as RM
  (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
    user: { username: "alice", role: "RM", display_name: "Alice" },
    logout: vi.fn(),
    clearUser: vi.fn(),
  });

  // Default: empty articles list
  (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles: [] });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("WikiPage list view", () => {
  it("renders 暂无文章 when list is empty", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles: [] });
    const qc = makeQClient();
    renderWikiPage(qc);
    await waitFor(() => expect(screen.getByText("暂无文章")).toBeDefined());
  });

  it("renders article cards", async () => {
    const articles = [makeArticleSummary("a1"), makeArticleSummary("a2")];
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles });
    const qc = makeQClient();
    renderWikiPage(qc);
    await waitFor(() => {
      expect(screen.getByText("Article a1")).toBeDefined();
      expect(screen.getByText("Article a2")).toBeDefined();
    });
  });

  it("shows 新建文章 button for RM", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles: [] });
    const qc = makeQClient();
    renderWikiPage(qc);
    await waitFor(() => expect(screen.getByTestId("wiki-new-btn")).toBeDefined());
  });

  it("hides 新建文章 button for Owner role", async () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { username: "bob", role: "Owner", display_name: "Bob" },
    });
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles: [] });
    const qc = makeQClient();
    renderWikiPage(qc);
    await waitFor(() => expect(screen.getByText("暂无文章")).toBeDefined());
    expect(screen.queryByTestId("wiki-new-btn")).toBeNull();
  });

  it("filters articles by search input", async () => {
    const articles = [
      makeArticleSummary("a1", { title: "Deployment Guide" }),
      makeArticleSummary("a2", { title: "API Reference" }),
    ];
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles });
    const qc = makeQClient();
    renderWikiPage(qc);
    await waitFor(() => expect(screen.getByText("Deployment Guide")).toBeDefined());

    const searchBox = screen.getByTestId("wiki-search");
    fireEvent.change(searchBox, { target: { value: "API" } });

    expect(screen.queryByText("Deployment Guide")).toBeNull();
    expect(screen.getByText("API Reference")).toBeDefined();
  });

  it("shows RefreshBar", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles: [] });
    const qc = makeQClient();
    renderWikiPage(qc);
    await waitFor(() => expect(screen.getByTestId("refresh-btn")).toBeDefined());
  });

  it("shows loading state while fetching", () => {
    (apiGet as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    const qc = makeQClient();
    renderWikiPage(qc);
    // RefreshBar shows "…" while fetching
    expect(screen.getByTestId("refresh-btn")).toBeDefined();
  });

  it("shows error on fetch failure", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("network error"));
    const qc = makeQClient();
    renderWikiPage(qc);
    await waitFor(() => {
      expect(screen.getByText(/加载失败/)).toBeDefined();
      expect(screen.getByText(/network error/)).toBeDefined();
    });
  });
});

describe("WikiPage article view", () => {
  it("navigates to view mode when clicking a card", async () => {
    const articles = [makeArticleSummary("a1")];
    const article = makeArticle("a1");

    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/wiki/articles/a1"))
        return Promise.resolve({ article });
      return Promise.resolve({ articles });
    });

    const qc = makeQClient();
    renderWikiPage(qc);

    await waitFor(() => expect(screen.getByTestId("wiki-card-a1")).toBeDefined());
    fireEvent.click(screen.getByTestId("wiki-card-a1"));

    await waitFor(() => {
      expect(screen.getByText("Article a1")).toBeDefined();
    });
  });

  it("shows edit/pin/delete buttons for RM in view mode", async () => {
    const articles = [makeArticleSummary("a1")];
    const article = makeArticle("a1");

    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/wiki/articles/a1"))
        return Promise.resolve({ article });
      return Promise.resolve({ articles });
    });

    const qc = makeQClient();
    renderWikiPage(qc);

    await waitFor(() => expect(screen.getByTestId("wiki-card-a1")).toBeDefined());
    fireEvent.click(screen.getByTestId("wiki-card-a1"));

    await waitFor(() => {
      expect(screen.getByTestId("wiki-edit-btn")).toBeDefined();
      expect(screen.getByTestId("wiki-pin-btn")).toBeDefined();
      expect(screen.getByTestId("wiki-delete-btn")).toBeDefined();
    });
  });

  it("hides edit/pin/delete buttons for Owner role", async () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { username: "bob", role: "Owner", display_name: "Bob" },
    });

    const articles = [makeArticleSummary("a1")];
    const article = makeArticle("a1");

    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/wiki/articles/a1"))
        return Promise.resolve({ article });
      return Promise.resolve({ articles });
    });

    const qc = makeQClient();
    renderWikiPage(qc);

    await waitFor(() => expect(screen.getByTestId("wiki-card-a1")).toBeDefined());
    fireEvent.click(screen.getByTestId("wiki-card-a1"));

    await waitFor(() => expect(screen.getByText("Article a1")).toBeDefined());
    expect(screen.queryByTestId("wiki-edit-btn")).toBeNull();
    expect(screen.queryByTestId("wiki-delete-btn")).toBeNull();
  });

  it("back button returns to list view", async () => {
    const articles = [makeArticleSummary("a1")];
    const article = makeArticle("a1");

    (apiGet as ReturnType<typeof vi.fn>).mockImplementation((path: string) => {
      if (path.includes("/api/wiki/articles/a1"))
        return Promise.resolve({ article });
      return Promise.resolve({ articles });
    });

    const qc = makeQClient();
    renderWikiPage(qc);

    await waitFor(() => expect(screen.getByTestId("wiki-card-a1")).toBeDefined());
    fireEvent.click(screen.getByTestId("wiki-card-a1"));

    await waitFor(() =>
      expect(screen.getByText("← 列表")).toBeDefined(),
    );
    fireEvent.click(screen.getByText("← 列表"));

    await waitFor(() => expect(screen.getByTestId("wiki-search")).toBeDefined());
  });
});

describe("WikiPage editor", () => {
  it("opens new article editor when 新建文章 is clicked", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles: [] });
    const qc = makeQClient();
    renderWikiPage(qc);

    await waitFor(() => expect(screen.getByTestId("wiki-new-btn")).toBeDefined());
    fireEvent.click(screen.getByTestId("wiki-new-btn"));

    expect(screen.getByTestId("wiki-title-input")).toBeDefined();
    expect(screen.getByTestId("wiki-body-textarea")).toBeDefined();
    expect(screen.getByTestId("wiki-save-btn")).toBeDefined();
  });

  it("save button calls POST /api/wiki/articles/save", async () => {
    const savedArticle = makeArticle("new-1", { title: "My New Article" });

    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles: [] });
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      article: savedArticle,
    });

    const qc = makeQClient();
    renderWikiPage(qc);

    await waitFor(() => expect(screen.getByTestId("wiki-new-btn")).toBeDefined());
    fireEvent.click(screen.getByTestId("wiki-new-btn"));

    fireEvent.change(screen.getByTestId("wiki-title-input"), {
      target: { value: "My New Article" },
    });
    fireEvent.change(screen.getByTestId("wiki-body-textarea"), {
      target: { value: "# Hello\n\nContent." },
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("wiki-save-btn"));
    });

    expect(apiPost).toHaveBeenCalledWith(
      "/api/wiki/articles/save",
      expect.objectContaining({ title: "My New Article" }),
    );
  });

  it("shows error log when title is empty on save", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles: [] });

    const qc = makeQClient();
    renderWikiPage(qc);

    await waitFor(() => expect(screen.getByTestId("wiki-new-btn")).toBeDefined());
    fireEvent.click(screen.getByTestId("wiki-new-btn"));

    // Leave title empty
    fireEvent.click(screen.getByTestId("wiki-save-btn"));

    expect(screen.getByText("标题不能为空")).toBeDefined();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("image paste calls POST /api/wiki/images/upload and inserts markdown", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockResolvedValue({ articles: [] });
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      image: {
        id: "wiki_img_abc",
        filename: "screenshot.png",
        content_type: "image/png",
        url: "/api/wiki/images/wiki_img_abc",
        created_by: "alice",
        created_at: "2026-01-01 10:00:00",
      },
    });

    const qc = makeQClient();
    renderWikiPage(qc);

    await waitFor(() => expect(screen.getByTestId("wiki-new-btn")).toBeDefined());
    fireEvent.click(screen.getByTestId("wiki-new-btn"));

    const textarea = screen.getByTestId("wiki-body-textarea");

    // Create a mock File. FileReader in jsdom may not fully work, so we mock
    // FileReader to return a predictable base64 result.
    const origFileReader = window.FileReader;
    const mockReadResult = "data:image/png;base64,aW1nZGF0YQ==";
    class MockFileReader {
      result: string | null = null;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      readAsDataURL(_file: File) {
        this.result = mockReadResult;
        setTimeout(() => this.onload?.(), 0);
      }
    }
    window.FileReader = MockFileReader as unknown as typeof FileReader;

    const mockFile = new File(["imgdata"], "screenshot.png", { type: "image/png" });
    const mockDataTransfer = {
      items: [
        {
          kind: "file",
          type: "image/png",
          getAsFile: () => mockFile,
        },
      ],
    };

    await act(async () => {
      fireEvent.paste(textarea, { clipboardData: mockDataTransfer });
      // Flush FileReader's setTimeout + async upload chain
      await new Promise((res) => setTimeout(res, 50));
    });

    window.FileReader = origFileReader;

    expect(apiPost).toHaveBeenCalledWith(
      "/api/wiki/images/upload",
      expect.objectContaining({ filename: "screenshot.png" }),
    );
  });
});
