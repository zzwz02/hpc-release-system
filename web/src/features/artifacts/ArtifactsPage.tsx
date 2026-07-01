/**
 * ArtifactsPage — 发布文档 tab.
 *
 * Mirrors index.html:738-787 (HTML) + index.html:3344-3575 (JS).
 *
 * Five artifact kinds:
 *   release_note  — Markdown  → source/render toggle + outline
 *   manual        — Markdown  → source/render toggle + outline
 *   ai4sci        — Markdown  → source/render toggle + outline
 *   data          — plain text → source only
 *   manager_review — CSV      → source/table toggle
 *
 * Actions (role-gated):
 *   刷新 (RM/Owner) — POST /api/artifacts/generate then reload active kind
 *   Manager Review CSV (RM) — show field picker pane, POST /api/artifacts/manager-review
 *   Download test-scope.csv (RM) — GET /api/test-scope.csv
 *   Download manager_review CSV — click triggers browser download of current text
 *
 * R2: staleTime:Infinity, no polling.  Explicit refetch only.
 */

import React, { useState, useCallback, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet } from "../../api/http";
import { useUiStore } from "../../store/uiStore";
import { RefreshBar } from "../../components/RefreshBar";
import { Markdown } from "../../components/Markdown";
import type { MarkdownOutlineItem } from "../../lib/markdown";
import { formatServerTime } from "../../lib/time";
import { isRM, canGenerateMarkdown } from "../../lib/roles";
import { downloadCsvText, parseCsvRows } from "../../lib/csv";
import type { ArtifactKind, StatePayload } from "../../types";
import {
  ARTIFACT_KEY,
  TEST_SCOPE_KEY,
  fetchArtifact,
  fetchTestScopeCsv,
  generateArtifacts,
  generateManagerReview,
  type ArtifactResult,
} from "./artifactsApi";

// ---------------------------------------------------------------------------
// Constants (mirrors index.html:1219-1225)
// ---------------------------------------------------------------------------

const MARKDOWN_KINDS = new Set<ArtifactKind>(["release_note", "manual", "ai4sci"]);
const CSV_KINDS = new Set<ArtifactKind>(["manager_review"]);

const KIND_LABELS: Record<ArtifactKind, string> = {
  release_note: "Release Note",
  manual: "HPC Manual",
  ai4sci: "AI4Sci Manual",
  data: "release-data",
  manager_review: "Manager Review CSV",
};

// Short descriptions for the landing empty-state tiles.
const KIND_DESC: Record<ArtifactKind, { icon: string; desc: string }> = {
  release_note: { icon: "📝", desc: "本轮发布的整体说明，Markdown 渲染 / 源码切换 + 大纲。" },
  manual:       { icon: "📘", desc: "HPC 应用使用手册，按 app 聚合，支持渲染与大纲导航。" },
  ai4sci:       { icon: "🤖", desc: "AI4Sci 应用手册，AI4Sci 类型 app 的使用文档。" },
  data:         { icon: "🗃️", desc: "结构化的 release-data 纯文本，便于核对原始字段。" },
  manager_review: { icon: "📊", desc: "管理评审导出：自选字段生成可下载的 CSV 汇总表。" },
};

// Manager review field picker options (mirrors index.html:762-781)
const MANAGER_FIELDS: Array<{ key: string; label: string; defaultChecked: boolean }> = [
  { key: "app_name",            label: "App",           defaultChecked: true  },
  { key: "official_name",       label: "官方名称",       defaultChecked: false },
  { key: "doc_target",          label: "文档类型",       defaultChecked: false },
  { key: "app_type",            label: "App类型",        defaultChecked: false },
  { key: "version",             label: "版本号",         defaultChecked: false },
  { key: "owners",              label: "Owner",          defaultChecked: true  },
  { key: "chip_support",        label: "支持芯片类型",    defaultChecked: true  },
  { key: "qa_issue_note",       label: "QA问题",         defaultChecked: true  },
  { key: "x86_chips",           label: "X86支持芯片",    defaultChecked: false },
  { key: "arm_chips",           label: "ARM支持芯片",    defaultChecked: false },
  { key: "release_decision",    label: "Release决策",    defaultChecked: false },
  { key: "qa_status",           label: "QA状态",         defaultChecked: false },
  { key: "owner_confirmed",     label: "Owner确认",      defaultChecked: false },
  { key: "releasable",          label: "是否可发布",      defaultChecked: true  },
  { key: "not_releasable_reason", label: "不可发布原因",  defaultChecked: true  },
  { key: "known_limitations",   label: "已知限制",       defaultChecked: true  },
  { key: "gerrit_url",          label: "Gerrit URL",    defaultChecked: false },
  { key: "git_branch",          label: "Branch",        defaultChecked: false },
];

// ---------------------------------------------------------------------------
// CsvTable — renders a parsed CSV as an HTML table
// ---------------------------------------------------------------------------

function CsvTable({ text }: { text: string }) {
  const rows = parseCsvRows(text).filter((r) => r.some((c) => c.trim()));
  if (!rows.length) {
    return (
      <div className="artifact-csv-empty muted" style={{ padding: "24px 12px" }}>
        暂无 CSV 内容，请点击刷新生成。
      </div>
    );
  }
  const [header, ...body] = rows;
  return (
    <div className="table" style={{ overflowX: "auto" }}>
      <table className="report-table artifact-csv-report">
        <thead>
          <tr>
            {header.map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, ri) => (
            <tr key={ri}>
              {header.map((_, ci) => (
                <td key={ci}>{row[ci] ?? ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// OutlineNav — sticky narrow ToC for a rendered Markdown doc (mirrors wiki)
// ---------------------------------------------------------------------------

function OutlineNav({ outline }: { outline: MarkdownOutlineItem[] }) {
  return (
    <div className="wiki-outline" data-testid="artifact-outline">
      <div className="wiki-outline-head">目录</div>
      <div className="wiki-outline-list">
        {outline.length === 0 ? (
          <div className="small muted" style={{ padding: "6px 8px" }}>
            这份文档暂无小标题。
          </div>
        ) : (
          <div className="wiki-outline-tree">
            {outline.map((item, idx) => {
              const indent = Math.max(0, Math.min(item.level, 4) - 1) * 10;
              return (
                <div
                  key={idx}
                  className={`wiki-outline-row lv${Math.min(item.level, 4)}`}
                  style={{ "--wiki-outline-indent": `${indent}px` } as React.CSSProperties}
                >
                  <a href={`#${item.id}`} title={item.title}>{item.title}</a>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ArtifactViewer — source/render/table toggle for one kind
// ---------------------------------------------------------------------------

interface ArtifactViewerProps {
  kind: ArtifactKind;
  result: ArtifactResult;
}

function ArtifactViewer({ kind, result }: ArtifactViewerProps) {
  const isMarkdown = MARKDOWN_KINDS.has(kind);
  const isCsv = CSV_KINDS.has(kind);
  const renderable = isMarkdown || isCsv;

  // Default to render mode for renderable kinds; source mode for plain text
  const [mode, setMode] = useState<"source" | "render">(renderable ? "render" : "source");
  const [outline, setOutline] = useState<MarkdownOutlineItem[]>([]);

  // Reset to render when kind changes
  useEffect(() => {
    setMode(renderable ? "render" : "source");
  }, [kind, renderable]);

  const sourceMode = mode === "source";
  const modeText = isMarkdown
    ? sourceMode ? "Markdown 源码" : "Markdown 渲染预览"
    : isCsv
    ? sourceMode ? "CSV 源码" : "CSV 表格预览"
    : "源码";

  const metaText = result.generatedAt
    ? `${modeText} · 存档于 ${formatServerTime(result.generatedAt)}`
    : modeText;

  return (
    <div className="artifact-shell">
      <div className="artifact-bar">
        <div>
          <div className="artifact-title">{KIND_LABELS[kind]}</div>
          <div className="small muted">{metaText}</div>
        </div>
        <span className="spacer" />
        {renderable && (
          <button
            className="btn sm"
            onClick={() => setMode(sourceMode ? "render" : "source")}
          >
            {sourceMode ? (isCsv ? "查看表格" : "查看渲染") : "查看源码"}
          </button>
        )}
      </div>

      {/* Source textarea */}
      {sourceMode && (
        <textarea
          className="artifact"
          readOnly
          value={result.text}
          style={{ display: "block" }}
        />
      )}

      {/* Rendered: Markdown — content wide (first) + sticky narrow outline (G + H) */}
      {!sourceMode && isMarkdown && (
        <div className="artifact-reader-grid">
          <div className="artifact-reader-main">
            <Markdown
              value={result.text}
              className="md-view wiki-md-view"
              withOutline
              onOutline={setOutline}
            />
          </div>
          <OutlineNav outline={outline} />
        </div>
      )}

      {/* Rendered: CSV table */}
      {!sourceMode && isCsv && <CsvTable text={result.text} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ManagerReviewPane — field picker + generate + download
// ---------------------------------------------------------------------------

interface ManagerReviewPaneProps {
  releaseId: string;
  onGenerated: () => void;
}

function ManagerReviewPane({ releaseId, onGenerated }: ManagerReviewPaneProps) {
  const [checkedFields, setCheckedFields] = useState<Set<string>>(
    () => new Set(MANAGER_FIELDS.filter((f) => f.defaultChecked).map((f) => f.key)),
  );
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");

  function toggleField(key: string) {
    setCheckedFields((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function handleGenerate() {
    const fields = [...checkedFields];
    if (!fields.length) {
      setError("请至少选择一个输出字段");
      return;
    }
    setGenerating(true);
    setError("");
    try {
      await generateManagerReview(releaseId, fields);
      onGenerated();
    } catch (e) {
      setError("生成失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="manager-review-pane" style={{ marginTop: 12 }}>
      <p className="hint" style={{ marginTop: 0 }}>
        Manager Review CSV 包含当前 release 的所有 app。请选择需要输出的字段。
      </p>
      <div
        className="form"
        style={{ gridTemplateColumns: "repeat(4,1fr)", marginTop: 12 }}
      >
        {MANAGER_FIELDS.map((f) => (
          <label key={f.key} className="check">
            <input
              type="checkbox"
              checked={checkedFields.has(f.key)}
              onChange={() => toggleField(f.key)}
            />
            {f.label}
          </label>
        ))}
      </div>
      {error && <p className="log" style={{ color: "var(--danger)", marginTop: 8 }}>{error}</p>}
      <div className="row" style={{ marginTop: 13 }}>
        <button
          className="btn primary"
          onClick={() => void handleGenerate()}
          disabled={generating}
        >
          {generating ? "生成中…" : "生成并刷新 CSV"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ArtifactsPage
// ---------------------------------------------------------------------------

type ViewKind = ArtifactKind | null;

export function ArtifactsPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId);
  const setSelectedReleaseId = useUiStore((s) => s.setSelectedReleaseId);

  const releaseId = selectedReleaseId;

  // Releases list for the version selector (shared cache key with the other
  // tabs).  R2: staleTime Infinity, no polling — explicit refetch only.
  const { data: stateData } = useQuery({
    queryKey: ["state", releaseId || ""],
    queryFn: () =>
      apiGet<StatePayload>(
        releaseId ? `/api/state?release_id=${encodeURIComponent(releaseId)}` : "/api/state",
      ),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });
  const releases = stateData?.releases ?? [];
  const rmRole = isRM(user);
  const canGenerate = canGenerateMarkdown(user);  // RM or Owner
  const canManagerReview = rmRole;

  // Which artifact kind is currently shown (null = nothing → picker cards)
  const [activeKind, setActiveKind] = useState<ViewKind>(null);
  // Generating spinner for the main 刷新 button
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState("");

  // Fetch the active artifact (plain text)
  const {
    data: artifactResult,
    isFetching: artifactFetching,
    dataUpdatedAt,
    refetch: refetchArtifact,
    error: artifactError,
  } = useQuery({
    queryKey: activeKind ? ARTIFACT_KEY(releaseId, activeKind) : ["artifacts", "none"],
    queryFn: () => fetchArtifact(releaseId, activeKind!),
    enabled: !!releaseId && !!activeKind,
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  // Pre-fetch test-scope CSV (lazy — only queried when downloaded)
  const { refetch: refetchTestScope } = useQuery({
    queryKey: TEST_SCOPE_KEY(releaseId),
    queryFn: () => fetchTestScopeCsv(releaseId),
    enabled: false,  // fetched on demand only
    staleTime: 0,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: false,
  });

  // When kind changes, scroll to top (best-effort — jsdom silently ignores)
  useEffect(() => {
    try { window.scrollTo({ top: 0 }); } catch { /* no-op in test env */ }
  }, [activeKind]);

  // Cards ARE the picker — selecting a card opens that doc in the viewer.
  function handleSelectKind(kind: ArtifactKind) {
    setActiveKind(kind);
  }

  // Contextual "← 文档列表" — return to the card picker.
  function handleBackToPicker() {
    setActiveKind(null);
  }

  // 刷新 button: generate draft artifacts then reload the active kind.
  // Mirrors refreshArtifactsView().  Stable ref via useCallback.
  const handleRefresh = useCallback(async () => {
    if (!releaseId) return;
    setRefreshing(true);
    setRefreshError("");
    try {
      if (canGenerate) {
        await generateArtifacts(releaseId);
      }
      // Invalidate all artifact queries for this release so next fetch is fresh
      await queryClient.invalidateQueries({ queryKey: ["artifacts", releaseId] });
      if (activeKind) {
        await refetchArtifact();
      }
    } catch (e) {
      setRefreshError("刷新失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setRefreshing(false);
    }
  }, [releaseId, canGenerate, queryClient, activeKind, refetchArtifact]);

  // After manager-review CSV is generated, reload it in the viewer
  async function handleManagerGenerated() {
    await queryClient.invalidateQueries({
      queryKey: ARTIFACT_KEY(releaseId, "manager_review"),
    });
    if (activeKind === "manager_review") {
      await refetchArtifact();
    } else {
      setActiveKind("manager_review");
    }
  }

  // Download test-scope.csv by fetching on demand and triggering a save
  async function handleDownloadTestScope() {
    if (!releaseId) return;
    try {
      const res = await refetchTestScope();
      const text = res.data ?? "";
      downloadCsvText(`test-scope-${releaseId}.csv`, text);
    } catch (e) {
      alert("下载失败：" + (e instanceof Error ? e.message : String(e)));
    }
  }

  // Download the currently displayed manager_review CSV text
  function handleDownloadManagerCsv() {
    if (!artifactResult?.text) return;
    downloadCsvText(artifactResult.name || "manager_review.csv", artifactResult.text);
  }

  const isFetching = artifactFetching || refreshing;

  return (
    <section className="view active">
      <div className="page-toolbar">
        <h2>发布文档</h2>
        {releases.length > 0 && (
          <select
            className="input"
            style={{ width: "auto", minWidth: 160 }}
            value={releaseId || ""}
            onChange={(e) => setSelectedReleaseId(e.target.value)}
            aria-label="选择 release"
          >
            {releases.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
                {r.maca_version ? ` (${r.maca_version})` : ""}
              </option>
            ))}
          </select>
        )}
        <span className="spacer" />
        {refreshError && (
          <span className="small" style={{ color: "var(--danger)" }}>{refreshError}</span>
        )}
        {releaseId && rmRole && (
          <button
            className="btn sm"
            onClick={() => void handleDownloadTestScope()}
          >
            下载 test-scope.csv
          </button>
        )}
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          onRefresh={() => void handleRefresh()}
          isFetching={isFetching}
        />
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>发布文档 / release-data</h2>
        </div>
        <div className="panel-body">
          {!releaseId && (
            <p className="muted">请先在顶部选择一个 Release。</p>
          )}

          {releaseId && (
            <>
              {/* Landing — the 5 doc-kind cards ARE the picker (no redundant
                  button row). Shown until a document is selected. */}
              {!activeKind && (
                <div className="artifact-empty" data-testid="artifact-empty">
                  <div className="artifact-empty-head">
                    <span className="artifact-empty-ic">📂</span>
                    <div>
                      <h3>选择一份发布文档查看</h3>
                      <p className="muted small">
                        点击下方任一文档卡片，在此处渲染对应内容。
                      </p>
                    </div>
                  </div>
                  <div className="artifact-kind-grid">
                    {(["release_note", "manual", "ai4sci", "data"] as ArtifactKind[]).map((kind) => (
                      <button
                        key={kind}
                        type="button"
                        className="artifact-kind-card"
                        onClick={() => handleSelectKind(kind)}
                      >
                        <span className="ak-ic">{KIND_DESC[kind].icon}</span>
                        <span className="ak-title">{KIND_LABELS[kind]}</span>
                        <span className="ak-desc muted small">{KIND_DESC[kind].desc}</span>
                      </button>
                    ))}
                    {canManagerReview && (
                      <button
                        type="button"
                        className="artifact-kind-card"
                        onClick={() => handleSelectKind("manager_review")}
                      >
                        <span className="ak-ic">{KIND_DESC.manager_review.icon}</span>
                        <span className="ak-title">管理评审导出</span>
                        <span className="ak-desc muted small">{KIND_DESC.manager_review.desc}</span>
                      </button>
                    )}
                  </div>
                </div>
              )}

              {/* Document view — contextual actions bar + viewer */}
              {activeKind && (
                <>
                  <div className="artifact-context-bar">
                    <button className="btn sm" onClick={handleBackToPicker}>
                      ← 文档列表
                    </button>
                    <span className="artifact-context-title">{KIND_LABELS[activeKind]}</span>
                    <span className="spacer" />
                    {(canGenerate || canManagerReview) && (
                      <button
                        className="btn sm primary"
                        onClick={() => void handleRefresh()}
                        disabled={refreshing || !releaseId}
                      >
                        {refreshing ? "刷新中…" : "刷新"}
                      </button>
                    )}
                    {activeKind === "manager_review" && artifactResult?.text && (
                      <button className="btn sm" onClick={handleDownloadManagerCsv}>
                        下载 CSV
                      </button>
                    )}
                  </div>

                  {/* Manager review field picker (contextual to the manager kind) */}
                  {activeKind === "manager_review" && canManagerReview && (
                    <ManagerReviewPane
                      releaseId={releaseId}
                      onGenerated={() => void handleManagerGenerated()}
                    />
                  )}

                  <div id="artifactViewer">
                    {artifactError ? (
                      <p className="log" style={{ color: "var(--danger)", marginTop: 12 }}>
                        加载失败：{(artifactError as Error).message}
                      </p>
                    ) : artifactFetching ? (
                      <p className="muted" style={{ marginTop: 12 }}>加载中…</p>
                    ) : artifactResult ? (
                      <ArtifactViewer kind={activeKind} result={artifactResult} />
                    ) : null}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
