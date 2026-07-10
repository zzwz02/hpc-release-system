/**
 * WikiPage — 开发 WIKI tab (index.html:3692+, renderWiki, renderWikiList,
 * renderWikiDetail, openWikiEditor, submitWikiEditor).
 *
 * Roles: RM (read + write), Owner (read only).
 *   canEditWiki() → RM only (mirrors index.html:1664).
 *
 * Modes (wikiUi.mode in uiStore):
 *   "list"  — card grid of all articles with search filter
 *   "view"  — detail pane: article body (Markdown) + outline sidebar
 *   "edit"  — inline editor dialog (new or existing)
 *   "new"   — inline editor dialog (new article)
 *
 * Data:
 *   GET /api/wiki/articles            → list
 *   GET /api/wiki/articles/{id}       → single article
 *   POST /api/wiki/articles/save      → create/update
 *   POST /api/wiki/articles/pin       → toggle pin
 *   POST /api/wiki/articles/delete    → delete
 *   POST /api/wiki/images/upload      → paste-image upload (base64)
 *
 * Markdown sole sink: <Markdown> component is the only allowed innerHTML sink.
 */

import React, { useState, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshBar } from "../../components/RefreshBar";
import { Markdown } from "../../components/Markdown";
import { apiGet, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { canEditWiki } from "../../lib/roles";
import { formatServerTime } from "../../lib/time";
import type { MarkdownOutlineItem } from "../../lib/markdown";
import { confirmDialog } from "../../lib/confirm";
import type {
  WikiArticleSummary,
  WikiArticle,
  WikiArticlesResponse,
  WikiArticleResponse,
  WikiSaveResponse,
  WikiPinResponse,
  WikiImage,
  WikiImageUploadResponse,
} from "../../types";

// ---------------------------------------------------------------------------
// Query keys + fetchers
// ---------------------------------------------------------------------------

const ARTICLES_QK = ["wiki", "articles"] as const;
const ARTICLE_QK = (id: string) => ["wiki", "article", id] as const;

async function fetchArticles(): Promise<WikiArticlesResponse> {
  return apiGet<WikiArticlesResponse>("/api/wiki/articles");
}

async function fetchArticle(id: string): Promise<WikiArticleResponse> {
  return apiGet<WikiArticleResponse>(`/api/wiki/articles/${encodeURIComponent(id)}`);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function wikiTime(value: string | null | undefined): string {
  // Mirrors index.html:3583 wikiTime — display as local time, no +8 math.
  if (!value) return "";
  return formatServerTime(value);
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve(String(reader.result ?? "").split(",", 2)[1] ?? "");
    reader.onerror = () =>
      reject(reader.error ?? new Error("读取图片失败"));
    reader.readAsDataURL(file);
  });
}

// ---------------------------------------------------------------------------
// Article card (list view)
// ---------------------------------------------------------------------------

interface ArticleCardProps {
  article: WikiArticleSummary;
  onClick: () => void;
}

function ArticleCard({ article, onClick }: ArticleCardProps) {
  return (
    <article
      className="wiki-card pointer"
      onClick={onClick}
      data-testid={`wiki-card-${article.id}`}
    >
      <div className={`wiki-card-title${article.pinned ? " pinned" : ""}`}>
        {article.pinned && <span className="pill accent">置顶</span>}{" "}
        {article.title}
      </div>
      <div className="wiki-card-excerpt">{article.excerpt || "暂无摘要"}</div>
      <div className="wiki-card-meta">
        {article.updated_by || article.created_by} ·{" "}
        {wikiTime(article.updated_at || article.created_at)}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Article list pane
// ---------------------------------------------------------------------------

interface ArticleListPaneProps {
  articles: WikiArticleSummary[];
  filter: string;
  onFilterChange: (v: string) => void;
  onSelect: (id: string) => void;
  canEdit: boolean;
  onNew: () => void;
}

function ArticleListPane({
  articles,
  filter,
  onFilterChange,
  onSelect,
  canEdit,
  onNew,
}: ArticleListPaneProps) {
  const q = filter.trim().toLowerCase();
  const rows = q
    ? articles.filter((a) =>
        (a.title || "").toLowerCase().includes(q),
      )
    : articles;

  return (
    <div>
      <div className="wiki-list-head">
        <input
          className="input searchbox"
          placeholder="搜索文章标题…"
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
          data-testid="wiki-search"
        />
        <span className="muted small">
          {rows.length ? `共 ${rows.length} 篇` : ""}
        </span>
        {canEdit && (
          <button
            className="btn primary"
            onClick={onNew}
            data-testid="wiki-new-btn"
          >
            + 新建文章
          </button>
        )}
      </div>
      <div className="wiki-list-grid">
        {rows.length > 0 ? (
          rows.map((a) => (
            <ArticleCard key={a.id} article={a} onClick={() => onSelect(a.id)} />
          ))
        ) : (
          <div
            className="empty p-44-12 grid-span-all"
          >
            暂无文章
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Outline sidebar
// ---------------------------------------------------------------------------

interface OutlineItem {
  level: number;
  id: string;
  title: string;
}

interface OutlineSidebarProps {
  outline: OutlineItem[];
}

function OutlineSidebar({ outline }: OutlineSidebarProps) {
  if (outline.length === 0) {
    return (
      <div className="wiki-outline">
        <div className="wiki-outline-head">目录</div>
        <div className="wiki-outline-list">
          <div className="small muted p-6-8">
            这篇文章还没有标题。
          </div>
        </div>
      </div>
    );
  }

  // Build indent levels (mirrors index.html:3653-3672 buildWikiOutlineRows)
  const rows = outline.map((item, idx) => {
    const indent = Math.max(0, Math.min(item.level, 4) - 1) * 10;
    return (
      <div
        key={idx}
        className={`wiki-outline-row lv${Math.min(item.level, 4)}`}
        style={{ "--wiki-outline-indent": `${indent}px` } as React.CSSProperties}
      >
        <a href={`#${item.id}`} title={item.title}>
          {item.title}
        </a>
      </div>
    );
  });

  return (
    <div className="wiki-outline">
      <div className="wiki-outline-head">目录</div>
      <div className="wiki-outline-list">
        <div className="wiki-outline-tree">{rows}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Article detail pane
// ---------------------------------------------------------------------------

interface ArticleDetailPaneProps {
  article: WikiArticle;
  canEdit: boolean;
  onBack: () => void;
  onEdit: () => void;
  onPin: () => void;
  onDelete: () => void;
  pinning: boolean;
  deleting: boolean;
}

function ArticleDetailPane({
  article,
  canEdit,
  onBack,
  onEdit,
  onPin,
  onDelete,
  pinning,
  deleting,
}: ArticleDetailPaneProps) {
  // Outline is populated via the Markdown component's onOutline callback.
  const [outline, setOutline] = useState<MarkdownOutlineItem[]>([]);

  return (
    <div className="wiki-reader-grid">
      {/* Article body — wide column (content first) */}
      <div className="wiki-reader-main">
        <div className="wiki-detail-head">
          <div>
            <h2 className="wiki-title-line">
              {article.pinned && <span className="pill accent">置顶</span>}
              <span>{article.title}</span>
            </h2>
            <div className="wiki-meta">
              {article.updated_by || article.created_by} ·{" "}
              {wikiTime(article.updated_at || article.created_at)}
            </div>
          </div>
          <div className="wiki-detail-actions flex-row gap-8 wrap">
            <button className="btn sm" onClick={onBack}>
              ← 列表
            </button>
            {canEdit && (
              <>
                <button
                  className="btn sm"
                  onClick={onPin}
                  disabled={pinning}
                  data-testid="wiki-pin-btn"
                >
                  {article.pinned ? "取消置顶" : "置顶"}
                </button>
                <button
                  className="btn sm primary"
                  onClick={onEdit}
                  data-testid="wiki-edit-btn"
                >
                  编辑
                </button>
                <button
                  className="btn sm danger"
                  onClick={onDelete}
                  disabled={deleting}
                  data-testid="wiki-delete-btn"
                >
                  删除
                </button>
              </>
            )}
          </div>
        </div>
        <div className="wiki-detail-body">
          {/* <Markdown> is the sole sanitized-HTML sink; onOutline feeds the sidebar. */}
          <Markdown
            value={article.body_md}
            className="md-view wiki-md-view"
            withOutline
            onOutline={setOutline}
          />
        </div>
      </div>

      {/* Outline sidebar — narrow column on the right */}
      <OutlineSidebar outline={outline} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Editor (new + edit article, image paste-upload)
// ---------------------------------------------------------------------------

interface EditorPaneProps {
  article: WikiArticle | null;  // null = new article
  onSaved: (savedArticle: WikiArticle) => void;
  onCancel: () => void;
}

function EditorPane({ article, onSaved, onCancel }: EditorPaneProps) {
  const isNew = !article;
  const [title, setTitle] = useState(article?.title ?? "");
  const [body, setBody] = useState(article?.body_md ?? "");
  const [pinned, setPinned] = useState(article?.pinned ?? false);
  const [log, setLog] = useState("");
  const [saving, setSaving] = useState(false);
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  // Insert text at cursor in the textarea (mirrors index.html:3798)
  const insertAtCursor = useCallback((text: string) => {
    const el = bodyRef.current;
    if (!el) return;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? el.value.length;
    const next = el.value.slice(0, start) + text + el.value.slice(end);
    setBody(next);
    // Restore cursor after state update
    requestAnimationFrame(() => {
      if (!bodyRef.current) return;
      const pos = start + text.length;
      bodyRef.current.selectionStart = bodyRef.current.selectionEnd = pos;
      bodyRef.current.focus();
    });
  }, []);

  // Image paste handler (mirrors index.html:3816-3838)
  const handlePaste = useCallback(
    async (ev: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const items = [...(ev.clipboardData?.items ?? [])];
      const images = items
        .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
        .map((item) => item.getAsFile())
        .filter((f): f is File => f !== null);
      if (!images.length) return;
      ev.preventDefault();
      try {
        for (const file of images) {
          setLog(`正在上传图片 ${file.name || ""}...`);
          const content_base64 = await fileToBase64(file);
          const res = await apiPost<WikiImageUploadResponse>(
            "/api/wiki/images/upload",
            {
              filename: file.name || "pasted-image.png",
              content_type: file.type || "image/png",
              content_base64,
            },
          );
          const image: WikiImage = res.image;
          insertAtCursor(
            `\n![${image.filename || "image"}](${image.url})\n`,
          );
        }
        setLog(`已插入 ${images.length} 张图片`);
      } catch (e) {
        setLog("图片上传失败：" + (e instanceof Error ? e.message : String(e)));
      }
    },
    [insertAtCursor],
  );

  async function handleSave() {
    if (!title.trim()) {
      setLog("标题不能为空");
      return;
    }
    setSaving(true);
    setLog("保存中...");
    try {
      const res = await apiPost<WikiSaveResponse>("/api/wiki/articles/save", {
        id: article?.id ?? "",
        title: title.trim(),
        body_md: body,
        pinned,
      });
      setLog("");
      onSaved(res.article);
    } catch (e) {
      setLog("保存失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex-col-fill">
      {/* Editor toolbar */}
      <div className="wiki-editor-top">
        <div className="wiki-editor-titlebar">
          <label className="flex-1">
            <span className="small muted">标题</span>
            <input
              className="input w-full mt-4"
              placeholder="文章标题"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              data-testid="wiki-title-input"
            />
          </label>
          <label className="flex-row items-center gap-6 mb-3">
            <input
              type="checkbox"
              checked={pinned}
              onChange={(e) => setPinned(e.target.checked)}
              data-testid="wiki-pinned-checkbox"
            />
            置顶
          </label>
        </div>
        <div className="flex-row gap-8 mb-3">
          <button
            className="btn primary"
            onClick={handleSave}
            disabled={saving}
            data-testid="wiki-save-btn"
          >
            保存
          </button>
          <button className="btn" onClick={onCancel} disabled={saving}>
            取消
          </button>
        </div>
      </div>
      <div className="small muted minh-18 mb-6">
        {log}
      </div>
      <div className="wiki-editor-grid wiki-editor-grid2">
        {/* Left: editor */}
        <label className="flex-col-min0">
          <span className="small muted mb-4">
            Markdown 编辑（支持粘贴图片）
          </span>
          <textarea
            ref={bodyRef}
            className="input wiki-editor-body"
            placeholder="用 Markdown 写文章……"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            onPaste={handlePaste}
            data-testid="wiki-body-textarea"
          />
        </label>
        {/* Right: live preview */}
        <div className="wiki-editor-preview">
          <span className="small muted mb-4">
            预览
          </span>
          {/* <Markdown> is the sole sanitized-HTML sink. */}
          <div data-testid="wiki-preview">
            <Markdown value={body} className="md-view wiki-md-view" />
          </div>
        </div>
      </div>
      {!isNew && (
        <div className="small muted mt-6">
          提示：在正文编辑框中粘贴图片可自动上传并插入 Markdown 链接。
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// WikiPage
// ---------------------------------------------------------------------------

export function WikiPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const editable = canEditWiki(user ?? undefined);

  // Local UI state (mode: list | view | edit | new)
  const [mode, setMode] = useState<"list" | "view" | "edit" | "new">("list");
  const [selectedId, setSelectedId] = useState<string>("");
  const [filter, setFilter] = useState<string>("");

  // ── Article list query ────────────────────────────────────────────────────
  const {
    data: listData,
    isFetching: listFetching,
    dataUpdatedAt,
    refetch: refetchList,
    error: listError,
  } = useQuery({
    queryKey: ARTICLES_QK,
    queryFn: fetchArticles,
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  const articles = listData?.articles ?? [];

  // ── Single article query (only when viewing) ──────────────────────────────
  const {
    data: articleData,
    isFetching: articleFetching,
    error: articleError,
  } = useQuery({
    queryKey: ARTICLE_QK(selectedId),
    queryFn: () => fetchArticle(selectedId),
    enabled: mode === "view" && !!selectedId,
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const article = articleData?.article ?? null;

  // ── Pin mutation ──────────────────────────────────────────────────────────
  const pinMutation = useMutation({
    mutationFn: (vars: { id: string; pinned: boolean }) =>
      apiPost<WikiPinResponse>("/api/wiki/articles/pin", vars),
    onSuccess: (res) => {
      // Update the article cache + invalidate list
      queryClient.setQueryData(ARTICLE_QK(res.article.id), { article: res.article });
      void queryClient.invalidateQueries({ queryKey: ARTICLES_QK });
    },
  });

  // ── Delete mutation ───────────────────────────────────────────────────────
  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiPost("/api/wiki/articles/delete", { id }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ARTICLES_QK });
      setMode("list");
      setSelectedId("");
    },
  });

  // ── Handlers ──────────────────────────────────────────────────────────────

  function handleSelect(id: string) {
    setSelectedId(id);
    setMode("view");
  }

  function handleBack() {
    setMode("list");
    setSelectedId("");
  }

  function handleNew() {
    if (!editable) return;
    setSelectedId("");
    setMode("new");
  }

  function handleEdit() {
    if (!editable || !article) return;
    setMode("edit");
  }

  function handlePin() {
    if (!article) return;
    pinMutation.mutate({ id: article.id, pinned: !article.pinned });
  }

  async function handleDelete() {
    if (!article) return;
    if (!(await confirmDialog({
      body: `确认删除 WIKI 文章「${article.title}」？`,
      danger: true,
      confirmText: "删除",
    }))) return;
    deleteMutation.mutate(article.id);
  }

  function handleSaved(savedArticle: WikiArticle) {
    // Update article cache, invalidate list, then navigate to view mode
    queryClient.setQueryData(ARTICLE_QK(savedArticle.id), {
      article: savedArticle,
    });
    void queryClient.invalidateQueries({ queryKey: ARTICLES_QK });
    setSelectedId(savedArticle.id);
    setMode("view");
  }

  function handleCancelEdit() {
    // Return to list if this was a new article; back to view otherwise
    if (mode === "new") {
      setMode("list");
    } else {
      setMode("view");
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  const isFetching = listFetching || articleFetching;

  const editingArticle =
    mode === "edit"
      ? article  // edit existing
      : null;    // new article

  return (
    <section className="view active">
      <div className="page-toolbar">
        <h2>开发 WIKI</h2>
        <span className="spacer" />
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          isFetching={isFetching}
          onRefresh={() => void refetchList()}
        />
      </div>

      {/* Error banner */}
      {listError && (
        <div className="error-banner pane-error">
          加载失败：{listError instanceof Error ? listError.message : String(listError)}
        </div>
      )}
      {articleError && mode === "view" && (
        <div className="error-banner pane-error">
          文章加载失败：{articleError instanceof Error ? articleError.message : String(articleError)}
        </div>
      )}

      {/* Content by mode */}
      {(mode === "list" || mode === "view") && (
        <>
          {mode === "list" && (
            <ArticleListPane
              articles={articles}
              filter={filter}
              onFilterChange={setFilter}
              onSelect={handleSelect}
              canEdit={editable}
              onNew={handleNew}
            />
          )}
          {mode === "view" && (
            <>
              {articleFetching && !article && (
                <div className="muted p-2r">
                  加载中…
                </div>
              )}
              {article && (
                <ArticleDetailPane
                  article={article}
                  canEdit={editable}
                  onBack={handleBack}
                  onEdit={handleEdit}
                  onPin={handlePin}
                  onDelete={handleDelete}
                  pinning={pinMutation.isPending}
                  deleting={deleteMutation.isPending}
                />
              )}
            </>
          )}
        </>
      )}

      {(mode === "edit" || mode === "new") && (
        <EditorPane
          article={editingArticle}
          onSaved={handleSaved}
          onCancel={handleCancelEdit}
        />
      )}
    </section>
  );
}
