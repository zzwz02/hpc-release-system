/**
 * QaPage — QA tab (index.html:714-728).
 *
 * Roles: RM / Owner / QA / Guest.
 *
 * Three subtabs (index.html:721-728):
 *   1. QA 标注  — log upload, AI analyze (1 s poll while running), status-batch editor
 *   2. Release Report — filterable/sortable/column-pick/CSV-export table
 *   3. Test 命令 — same table machinery, no column picker
 *
 * Data:
 *   - QA annotation + log: from GET /api/state (via shared uiStore selectedReleaseId)
 *   - QA reports: GET /api/qa-reports (fetched on demand, not from state)
 *   - AI job: POST /api/qa/analyze-log/start → GET /api/qa/analyze-log/status (1 s poll)
 *
 * R2: the 1 s interval is the ONLY allowed poll in the entire app.
 *     It is cancelled on unmount AND when the release changes.
 */

import React, { useState, useRef, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshBar } from "../../components/RefreshBar";
import { apiGet, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { useUiStore } from "../../store/uiStore";
import { isRM, isOwner, canEditQa } from "../../lib/roles";
import { displayName } from "../../lib/identity";
import { formatGerritUrl } from "../../lib/git";
import { formatServerTime } from "../../lib/time";
import { downloadCsv } from "../../lib/csv";
import { qaStatusLabels, qaStatusOptions } from "../../lib/labels";
import type {
  StatePayload,
  Snapshot,
  App,
  QaStatus,
  QaAnalysisJob,
  QaReportsResponse,
} from "../../types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Columns hidden by default in the release report (mirrors index.html:1253)
const QA_RELEASE_REPORT_DEFAULT_HIDDEN = new Set([
  "描述", "官方URL", "OS", "Python version", "PyTorch version",
]);

const CHIP_LEGEND =
  "x201系列芯片包括x201, x203 · x301系列芯片 · x302系列芯片 · C500系列芯片包括C500, C550 · C600系列芯片包括C600 · N300系列芯片包括N300";
const QA_NOTE_REQUIRED_STATUSES = new Set<QaStatus>(["has_issues", "cannot_release"]);

function qaIssueNoteRequired(status: string): boolean {
  return QA_NOTE_REQUIRED_STATUSES.has(status as QaStatus);
}

// ---------------------------------------------------------------------------
// Query key + fetchers
// ---------------------------------------------------------------------------

const STATE_QK = (releaseId: string) => ["state", releaseId];

async function fetchState(releaseId: string): Promise<StatePayload> {
  return apiGet<StatePayload>(`/api/state?release_id=${encodeURIComponent(releaseId)}`);
}

async function fetchQaReports(
  releaseId: string,
  compareId: string,
): Promise<QaReportsResponse> {
  const cmp = compareId
    ? `&compare_release_id=${encodeURIComponent(compareId)}`
    : "";
  return apiGet<QaReportsResponse>(
    `/api/qa-reports?release_id=${encodeURIComponent(releaseId)}${cmp}`,
  );
}

// ---------------------------------------------------------------------------
// AI job progress display (mirrors index.html:1504-1519)
// ---------------------------------------------------------------------------

interface AiProgressProps {
  job: QaAnalysisJob;
}

function AiProgress({ job }: AiProgressProps) {
  const statusLabel =
    job.status === "completed" ? "完成" :
    job.status === "failed" ? "失败" : "进行中";
  const pillClass =
    job.status === "completed" ? "pill ok" :
    job.status === "failed" ? "pill bad" : "pill accent";
  const progressClass =
    job.status === "completed" ? "qa-ai-progress ok" :
    job.status === "failed" ? "qa-ai-progress bad" : "qa-ai-progress";
  const message = (job as unknown as Record<string, string>)["error"] ||
    (job as unknown as Record<string, string>)["message"] || "等待进度更新";
  const stage = (job as unknown as Record<string, string>)["stage"] || job.status || "";
  const started = Number((job as unknown as Record<string, string>)["started_at"] || 0);
  const updated = Number((job as unknown as Record<string, string>)["updated_at"] || 0);
  const endTime = job.status === "running" ? Date.now() / 1000 : updated;
  const elapsed = started && endTime ? Math.max(0, Math.round(endTime - started)) : 0;
  const tokenCount = Number((job as unknown as Record<string, string>)["token_count"] || 0);
  const tokenText = tokenCount ? `；已收到 ${tokenCount} token` : "";

  return (
    <div id="qaAiProgress" className={progressClass}>
      <div className="row">
        <span className={pillClass}>{statusLabel}</span>
        <b>{message}</b>
        <span className="stage">{stage}</span>
      </div>
      <div className="small muted" style={{ marginTop: 4 }}>
        已运行 {elapsed}s{tokenText}；进度会在当前提示中更新。
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// QA test result row
// ---------------------------------------------------------------------------

interface TestResult {
  test?: string;
  arch?: string;
  status?: string;
  perf?: string;
  note?: string;
}

function qaTestResultPillClass(status?: string): string {
  const s = (status || "unknown").toLowerCase();
  return s === "pass" ? "pill ok" : s === "fail" ? "pill bad" : s === "skip" ? "pill warnp" : "pill";
}

function qaTestResultLabel(status?: string): string {
  const s = (status || "unknown").toLowerCase();
  return ({ pass: "PASS", fail: "FAIL", skip: "SKIP", unknown: "?" } as Record<string, string>)[s] ?? s;
}

// ---------------------------------------------------------------------------
// QA annotation pane (subtab 1)
// ---------------------------------------------------------------------------

interface QaMarkPaneProps {
  payload: StatePayload;
  onStateRefresh: () => void;
}

function QaMarkPane({ payload, onStateRefresh }: QaMarkPaneProps) {
  const { user } = useAuth();
  const uiStore = useUiStore();
  const release = payload.release;
  const releaseId = release?.id ?? "";
  const releaseLocked = !!release?.released_locked;
  const writable = canEditQa(user ?? undefined) && !releaseLocked;

  // ── Local state ──────────────────────────────────────────────────────────
  // Reset edit state when release changes (mirrors index.html:2580)
  useEffect(() => {
    if (releaseId && uiStore.qaEditReleaseId !== releaseId) {
      uiStore.setQaEditReleaseId(releaseId);
      uiStore.setQaEditMode(false);
      uiStore.clearQaAiSuggestions();
      uiStore.setQaAiJob(null);
    }
    if (!writable) {
      uiStore.setQaEditMode(false);
      uiStore.clearQaAiSuggestions();
      uiStore.setQaAiJob(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [releaseId, writable]);

  const editMode = uiStore.qaEditMode;
  const aiJob = uiStore.qaAiJob;
  const aiSuggestions = uiStore.qaAiSuggestions;

  // ── Per-card editable state ───────────────────────────────────────────────
  // Local form state: map from app_id → { status, note }
  const rows = qaRows(payload);
  const [formState, setFormState] = useState<
    Record<string, { status: string; note: string }>
  >({});

  // Sync form state when entering edit mode or when AI suggestions arrive
  useEffect(() => {
    if (!editMode) return;
    const next: Record<string, { status: string; note: string }> = {};
    for (const { app, snap } of rows) {
      const suggestion = aiSuggestions[app.id];
      next[app.id] = {
        status: suggestion?.qa_status ?? (snap.qa_status || "not_checked"),
        note: suggestion?.qa_issue_note ?? (snap.qa_issue_note || ""),
      };
    }
    setFormState(next);
  // Run only when editMode turns on or suggestions change
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editMode, aiSuggestions]);

  // ── AI poll ───────────────────────────────────────────────────────────────
  // Refs so the interval callback always sees current values without re-creation
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const releaseIdRef = useRef(releaseId);
  useEffect(() => { releaseIdRef.current = releaseId; }, [releaseId]);

  const stopPoll = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Cancel poll on unmount (critical cleanup)
  useEffect(() => () => stopPoll(), [stopPoll]);

  // Cancel poll when release changes
  useEffect(() => {
    stopPoll();
  }, [releaseId, stopPoll]);

  const startPoll = useCallback(
    (jobId: string) => {
      stopPoll();
      // This is THE ONLY allowed 1 s interval in the entire app (phase3_brief §1)
      pollRef.current = setInterval(async () => {
        try {
          const job = await apiGet<QaAnalysisJob>(
            `/api/qa/analyze-log/status?job_id=${encodeURIComponent(jobId)}`,
          );
          // Stale if release changed or job replaced
          const currentJob = useUiStore.getState().qaAiJob;
          if (!currentJob || currentJob.job_id !== jobId) {
            stopPoll();
            return;
          }
          if (job.release_id && job.release_id !== releaseIdRef.current) {
            stopPoll();
            return;
          }
          uiStore.setQaAiJob(job);

          if (job.status === "running") return; // keep polling

          stopPoll();
          if (job.status === "completed") {
            // Apply suggestions to store and enter edit mode
            const res = (job as unknown as Record<string, unknown>)["result"] as
              | { apps?: Array<{ app_id: string; qa_status?: string; qa_issue_note?: string; test_results?: unknown[] }> }
              | undefined;
            const suggestions: Record<string, { qa_status: string; qa_issue_note: string; test_results: unknown[] }> = {};
            for (const item of (res?.apps ?? [])) {
              suggestions[item.app_id] = {
                qa_status: item.qa_status || "not_checked",
                qa_issue_note: item.qa_issue_note || "",
                test_results: item.test_results || [],
              };
            }
            uiStore.setQaAiSuggestions(suggestions);
            uiStore.setQaEditMode(true);
            const n = Object.keys(suggestions).length;
            const truncNote = (res as Record<string, unknown> | undefined)?.["log_truncated"]
              ? "\n注意：log 过长已截断中段送给 LLM。"
              : "";
            alert(
              `AI 分析完成：已为 ${n} 个 app 预填 QA 状态，请核对后点「保存 QA 状态」${truncNote}`,
            );
          } else {
            const errMsg =
              (job as unknown as Record<string, string>)["error"] ||
              (job as unknown as Record<string, string>)["message"] ||
              "未知错误";
            alert("AI 分析失败：" + errMsg);
          }
        } catch (e) {
          stopPoll();
          uiStore.setQaAiJob({
            job_id: "",
            release_id: releaseId,
            status: "failed",
            started_at: "",
            finished_at: null,
            summary: "",
            error: e instanceof Error ? e.message : String(e),
            progress: null,
          });
        }
      }, 1000);
    },
    [releaseId, stopPoll, uiStore],
  );

  // ── Handlers ─────────────────────────────────────────────────────────────

  async function handleUpload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const fileInput = form.elements.namedItem("qaLogFile") as HTMLInputElement;
    const file = fileInput?.files?.[0];
    if (!file) { alert("请选择 log 文件"); return; }
    const bytes = new Uint8Array(await file.arrayBuffer());
    let bin = "";
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
    }
    const b64 = btoa(bin);
    try {
      await apiPost("/api/qa/upload-log", {
        release_id: releaseId,
        filename: file.name,
        content_base64: b64,
      });
      uiStore.setQaAiJob(null);
      uiStore.clearQaAiSuggestions();
      onStateRefresh();
      alert("QA log 上传成功");
    } catch (ex) {
      alert("上传失败：" + (ex instanceof Error ? ex.message : String(ex)));
    }
  }

  async function handleAnalyze() {
    if (!payload.qa_log) { alert("请先上传 QA log"); return; }
    if (aiJob?.status === "running") return;
    try {
      const job = await apiPost<QaAnalysisJob>(
        "/api/qa/analyze-log/start",
        { release_id: releaseId },
      );
      uiStore.setQaAiJob(job);
      startPoll(job.job_id);
    } catch (ex) {
      uiStore.setQaAiJob({
        job_id: "",
        release_id: releaseId,
        status: "failed",
        started_at: "",
        finished_at: null,
        summary: "",
        error: ex instanceof Error ? ex.message : String(ex),
        progress: null,
      });
      alert("AI 分析失败：" + (ex instanceof Error ? ex.message : String(ex)));
    }
  }

  function handleEnterEdit() {
    uiStore.setQaEditMode(true);
  }

  function handleCancel() {
    stopPoll();
    uiStore.setQaEditMode(false);
    uiStore.clearQaAiSuggestions();
    uiStore.setQaAiJob(null);
  }

  async function handleSave() {
    const items = rows
      .map(({ app, snap }) => {
        const f = formState[app.id];
        return {
          app_id: app.id,
          status: f?.status ?? (snap.qa_status || "not_checked"),
          issue_note: f?.note ?? (snap.qa_issue_note || ""),
        };
      })
      .filter(item => {
        const snap = (payload.release?.snapshots?.[item.app_id] ?? {}) as Snapshot;
        return (
          (snap.qa_status || "not_checked") !== item.status ||
          (snap.qa_issue_note || "") !== item.issue_note
        );
      });

    if (!items.length) {
      uiStore.setQaEditMode(false);
      uiStore.clearQaAiSuggestions();
      uiStore.setQaAiJob(null);
      return;
    }
    const missingNote = items.find(
      (item) => qaIssueNoteRequired(item.status) && !item.issue_note.trim(),
    );
    if (missingNote) {
      alert(
        `标注「${qaStatusLabels[missingNote.status as QaStatus] ?? missingNote.status}」时必须填写问题说明`,
      );
      return;
    }
    try {
      await apiPost("/api/qa/status-batch", { release_id: releaseId, items });
      uiStore.setQaEditMode(false);
      uiStore.clearQaAiSuggestions();
      uiStore.setQaAiJob(null);
      onStateRefresh();
      alert("QA 状态已保存");
    } catch (ex) {
      alert(
        "保存失败，未保存任何改动，请修正后重试：\n\n" +
          (ex instanceof Error ? ex.message : String(ex)),
      );
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (!release) {
    return (
      <div className="panel">
        <div className="panel-body">
          <p className="muted">请选择 release。</p>
        </div>
      </div>
    );
  }

  const aiJobRunning = aiJob?.status === "running";
  const canAnalyze = writable && !!payload.qa_log && !aiJobRunning;
  const qaFieldsWritable = writable && editMode;

  const actionBar = canEditQa(user ?? undefined) ? (
    <div className="qa-actions">
      {editMode ? (
        <>
          <button className="btn primary qaSaveAll" disabled={!writable} onClick={handleSave}>
            保存 QA 状态
          </button>
          <button className="btn ghost qaCancel" onClick={handleCancel}>
            取消编辑
          </button>
        </>
      ) : (
        <button className="btn primary qaEdit" disabled={!writable} onClick={handleEnterEdit}>
          ✎ 修改（上传 log / 填写 QA 状态）
        </button>
      )}
    </div>
  ) : null;

  return (
    <div id="qaPanel">
      {actionBar}

      {/* Log file panel */}
      <div className="panel">
        <div className="panel-head">
          <h2>QA log 文件</h2>
          <span className="count">{editMode ? "编辑中 · 单个 · 覆盖式上传" : "查看模式"}</span>
        </div>
        <div className="panel-body">
          {payload.qa_log ? (
            <div className="row">
              <span className="pill ok">已上传：{payload.qa_log.filename}</span>
              <span className="muted small">
                {formatServerTime(payload.qa_log.uploaded_at)} · {payload.qa_log.uploaded_by}
              </span>
              <a
                href={`/api/qa-log/download?release_id=${encodeURIComponent(releaseId)}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                <button className="btn ghost sm">下载</button>
              </a>
            </div>
          ) : (
            <p className="muted small">本 release 暂无 QA log。</p>
          )}

          {editMode && (
            <form onSubmit={handleUpload} style={{ marginTop: 9 }}>
              <div className="row">
                <input id="qaLogFile" name="qaLogFile" type="file" className="input" />
                <button
                  type="submit"
                  className="btn primary"
                  disabled={aiJobRunning}
                >
                  上传 log（覆盖）
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={!canAnalyze}
                  title={
                    canAnalyze
                      ? "调用 LLM 分析已上传的 log，自动填写各 app 的 QA 状态"
                      : aiJobRunning
                      ? "AI 分析进行中"
                      : "需先上传 log"
                  }
                  onClick={handleAnalyze}
                  data-testid="qa-analyze-btn"
                >
                  {aiJobRunning ? "AI 分析中…" : "🤖 AI 分析 log"}
                </button>
              </div>
            </form>
          )}

          {aiJob && <AiProgress job={aiJob} />}
        </div>
      </div>

      {editMode && (
        <div className="banner warnp" style={{ marginTop: 14 }}>
          编辑中 · 刷新前请先保存或取消。上传 log、AI 分析、改 QA 状态都在保存后才会生效。
        </div>
      )}

      {/* App QA cards */}
      {rows.length === 0 ? (
        <div className="banner" style={{ marginTop: 14 }}>
          本 release 暂无 release 决策为 release 的 app。
        </div>
      ) : (
        <div className="qa-grid">
          {rows.map(({ app, snap }) => {
            const suggestion = aiSuggestions[app.id];
            const useSuggestion = editMode && !!suggestion;
            const f = formState[app.id];
            const status: QaStatus =
              (f?.status as QaStatus) ??
              (useSuggestion
                ? (suggestion.qa_status as QaStatus)
                : ((snap.qa_status as QaStatus) || "not_checked"));
            const note =
              f?.note ??
              (useSuggestion ? (suggestion.qa_issue_note || "") : (snap.qa_issue_note || ""));
            const testResults: TestResult[] = useSuggestion
              ? ((suggestion.test_results as TestResult[]) ?? [])
              : [];

            const pillCls =
              status === "qa_passed"
                ? "ok"
                : status === "has_issues"
                ? "warnp"
                : status === "cannot_release"
                ? "bad"
                : "";

            return (
              <div key={app.id} className="qa-card" data-qa-app={app.id}>
                <div className="qa-card-h">
                  <span className="app-ico">
                    {initials(displayName(snap))}
                  </span>
                  <div>
                    <div className="name">
                      {displayName(snap)}{" "}
                      {useSuggestion && (
                        <span
                          className="pill"
                          style={{ background: "#eef", border: "1px solid #99c", color: "#225" }}
                        >
                          AI 建议
                        </span>
                      )}
                    </div>
                    <div className="sub">
                      {usersLabel(snap.owners, payload.user_display_names ?? {})}
                    </div>
                  </div>
                  <span className="qa-cur">
                    <span className={`pill ${pillCls}`}>{qaStatusLabels[status] ?? status}</span>
                  </span>
                </div>
                <div className="qa-card-b">
                  <label>
                    QA 状态
                    <select
                      className="select"
                      disabled={!qaFieldsWritable}
                      value={f?.status ?? status}
                      onChange={(e) =>
                        setFormState((prev) => ({
                          ...prev,
                          [app.id]: { ...prev[app.id], status: e.target.value },
                        }))
                      }
                      data-testid={`qa-status-${app.id}`}
                    >
                      {qaStatusOptions.map((s) => (
                        <option key={s} value={s}>
                          {qaStatusLabels[s]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    问题说明{" "}
                    {qaIssueNoteRequired(status) ? (
                      <span className="req">必填</span>
                    ) : (
                      "(可选)"
                    )}
                    <textarea
                      disabled={!qaFieldsWritable}
                      placeholder={qaFieldsWritable ? "标注「存在问题」或「不可发布」时必填；保存后会写入 Release Note" : ""}
                      value={f?.note ?? note}
                      onChange={(e) =>
                        setFormState((prev) => ({
                          ...prev,
                          [app.id]: { ...prev[app.id], note: e.target.value },
                        }))
                      }
                    />
                  </label>
                  <p className="small muted">
                    Gerrit: {formatGerritUrl(app.git_url)} {app.git_branch || ""}
                  </p>
                  {testResults.length > 0 && (
                    <details className="qa-test-results" style={{ marginTop: 6 }}>
                      <summary className="small muted">
                        测试结果（{testResults.length}）{useSuggestion ? " · 来自 AI 分析" : ""}
                      </summary>
                      <table
                        className="qa-test-results-tbl"
                        style={{
                          width: "100%",
                          borderCollapse: "collapse",
                          marginTop: 4,
                          fontSize: 12,
                        }}
                      >
                        <thead>
                          <tr>
                            <th style={{ textAlign: "left" }}>测试</th>
                            <th>arch</th>
                            <th>状态</th>
                            <th>性能</th>
                            <th style={{ textAlign: "left" }}>说明</th>
                          </tr>
                        </thead>
                        <tbody>
                          {testResults.map((r, i) => (
                            <tr key={i}>
                              <td>{r.test || ""}</td>
                              <td style={{ textAlign: "center" }}>{r.arch || ""}</td>
                              <td style={{ textAlign: "center" }}>
                                <span className={qaTestResultPillClass(r.status)}>
                                  {qaTestResultLabel(r.status)}
                                </span>
                              </td>
                              <td style={{ textAlign: "center" }}>{r.perf || ""}</td>
                              <td>{r.note || ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </details>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {actionBar}

      {/* QA change log (audit) panel — loaded lazily per app */}
      <QaChangeLogPanel payload={payload} rows={rows} releaseId={releaseId} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// QA audit change log (index.html:2281-2330)
// ---------------------------------------------------------------------------

interface QaChangeLogPanelProps {
  payload: StatePayload;
  rows: Array<{ app: App; snap: Snapshot }>;
  releaseId: string;
}

function QaChangeLogPanel({ payload, rows, releaseId }: QaChangeLogPanelProps) {
  // Lazy-load audit entries per app (mirrors loadMissingQaChangeLogs)
  const [auditLogs, setAuditLogs] = useState<
    Record<string, Array<{ ts: string; app_id: string; user: string; role: string; message: string; detail: Array<{ field: string; label: string; old: unknown; new: unknown }> }>>
  >({});
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Seed from state payload's qa_audit_logs if present
  useEffect(() => {
    const stateAudit = payload.qa_audit_logs ?? {};
    setAuditLogs((prev) => ({ ...stateAudit, ...prev }));
  }, [payload.qa_audit_logs]);

  // Fetch missing logs
  useEffect(() => {
    for (const { app } of rows) {
      const appId = app.id;
      if (appId in auditLogs || loading.has(appId)) continue;
      setLoading((prev) => new Set(prev).add(appId));
      apiGet<{ entries: Array<{ ts: string; app_id?: string; user: string; role: string; event: string; message: string; detail: unknown[] }> }>(
        `/api/app-audit?app_id=${encodeURIComponent(appId)}&release_id=${encodeURIComponent(releaseId)}`,
      )
        .then((data) => {
          const entries = (data.entries || [])
            .filter((e) => e.event === "qa_set_status")
            .map((e) => ({
              ...e,
              app_id: appId,
              detail: (e.detail as Array<{ field: string; label: string; old: unknown; new: unknown }>) || [],
            }));
          setAuditLogs((prev) => ({ ...prev, [appId]: entries }));
        })
        .catch((err) => {
          setErrors((prev) => ({
            ...prev,
            [appId]: err instanceof Error ? err.message : String(err),
          }));
        })
        .finally(() => {
          setLoading((prev) => {
            const s = new Set(prev);
            s.delete(appId);
            return s;
          });
        });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.map((r) => r.app.id).join(","), releaseId]);

  // Flatten + sort all entries by timestamp descending
  const allEntries = rows.flatMap(({ app, snap }) =>
    (auditLogs[app.id] ?? []).map((e) => ({
      ...e,
      app_label: displayName(snap),
    })),
  ).sort((a, b) => {
    const at = new Date(a.ts || "").getTime() || 0;
    const bt = new Date(b.ts || "").getTime() || 0;
    return bt - at;
  });

  const loadingCount = loading.size;
  const countText = loadingCount
    ? `加载中 · 已有 ${allEntries.length} 条`
    : `${allEntries.length} 条`;

  const errorApps = rows.filter(({ app }) => errors[app.id]);

  return (
    <details className="section qa-change-log-panel" id="qaChangeLogPanel">
      <summary>
        <span className="chev">▶</span> QA 变更记录（本 release）
        <span className="badge">{countText}</span>
      </summary>
      <div className="section-body">
        {errorApps.length > 0 && (
          <div className="banner bad" style={{ marginBottom: 12 }}>
            部分变更记录加载失败：
            {errorApps
              .map(({ app, snap }) => `${displayName(snap)}: ${errors[app.id]}`)
              .join("；")}
          </div>
        )}
        <div className="table">
          <table>
            <thead>
              <tr>
                <th>时间（北京时间）</th>
                <th>App</th>
                <th>操作人</th>
                <th>角色</th>
                <th>事件</th>
              </tr>
            </thead>
            <tbody>
              {allEntries.length === 0 ? (
                <tr>
                  <td colSpan={5} className="muted">
                    无数据
                  </td>
                </tr>
              ) : (
                allEntries.map((e, i) => (
                  <ChangeLogRow key={i} entry={e} />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </details>
  );
}

interface ChangeLogRowProps {
  entry: {
    ts: string;
    app_label: string;
    user: string;
    role: string;
    message: string;
    detail: Array<{ field: string; label: string; old: unknown; new: unknown }>;
  };
}

function ChangeLogRow({ entry }: ChangeLogRowProps) {
  const [open, setOpen] = useState(false);
  const hasDetail = entry.detail.length > 0;
  return (
    <>
      <tr
        className={`cl-row ${hasDetail ? "cl-clickable" : ""}`}
        onClick={hasDetail ? () => setOpen((v) => !v) : undefined}
        style={hasDetail ? { cursor: "pointer" } : undefined}
      >
        <td>{formatServerTime(entry.ts)}</td>
        <td>{entry.app_label}</td>
        <td>{entry.user}</td>
        <td>{entry.role}</td>
        <td>
          {entry.message}
          {hasDetail && <span className="cl-toggle">{open ? " ▾ 详情" : " ▸ 详情"}</span>}
        </td>
      </tr>
      {hasDetail && open && (
        <tr className="cl-detail">
          <td colSpan={5}>
            <table className="cl-detail-table">
              <thead>
                <tr>
                  <th>字段</th>
                  <th>原值</th>
                  <th>新值</th>
                </tr>
              </thead>
              <tbody>
                {entry.detail.map((d, i) => (
                  <tr key={i}>
                    <td>{d.label || d.field || ""}</td>
                    <td>{auditDetailValue(d.old)}</td>
                    <td>{auditDetailValue(d.new)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}

function auditDetailValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// ---------------------------------------------------------------------------
// Report pane (subtabs 2 + 3)
// ---------------------------------------------------------------------------

type ReportKind = "release" | "test";

interface ReportPaneProps {
  releaseId: string;
  kind: ReportKind;
  title: string;
  hint: string;
  releases: StatePayload["releases"];
}

interface ReportFilterState {
  filter: string;
  colFilters: Record<number, string>;
  sort: { col: number; dir: 1 | -1 };
  visibleCols: Set<string> | null; // null = default
  compareId: string;
}

function defaultFilter(): ReportFilterState {
  return {
    filter: "",
    colFilters: {},
    sort: { col: -1, dir: 1 },
    visibleCols: null,
    compareId: "",
  };
}

function ReportPane({ releaseId, kind, title, hint, releases }: ReportPaneProps) {
  const [filterState, setFilterState] = useState<ReportFilterState>(defaultFilter);

  // Reset when release changes
  useEffect(() => {
    setFilterState(defaultFilter());
  }, [releaseId]);

  const { data, isFetching, error, refetch } = useQuery({
    queryKey: ["qa-reports", releaseId, filterState.compareId],
    queryFn: () => fetchQaReports(releaseId, filterState.compareId),
    enabled: false, // only fetch when user requests
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });

  // Re-fetch when compare changes (if data already loaded)
  const prevCompareRef = useRef(filterState.compareId);
  useEffect(() => {
    if (prevCompareRef.current !== filterState.compareId && data) {
      void refetch();
    }
    prevCompareRef.current = filterState.compareId;
  }, [filterState.compareId, data, refetch]);

  if (!releaseId) {
    return (
      <div className="panel">
        <div className="panel-body">
          <p className="muted">请选择 release。</p>
        </div>
      </div>
    );
  }

  const rawData = data
    ? kind === "release"
      ? data.release_report
      : data.test_cmd
    : null;

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
      </div>
      <div className="panel-body">
        {error && (
          <div className="lerr" style={{ marginBottom: 8 }}>
            加载失败：{error instanceof Error ? error.message : String(error)}
          </div>
        )}
        {!rawData && !isFetching && (
          <>
            <p className="muted small">{hint}</p>
            <div className="row" style={{ marginTop: 9 }}>
              <button
                className="btn primary"
                onClick={() => void refetch()}
                disabled={isFetching}
              >
                加载报告
              </button>
            </div>
          </>
        )}
        {isFetching && <p className="muted small">加载中…</p>}
        {rawData && data && (
          <ReportTable
            kind={kind}
            data={rawData}
            filterState={filterState}
            onFilterChange={setFilterState}
            releases={releases}
            releaseId={releaseId}
            generatedAt={data.generated_at}
            releaseName={data.release_name}
            onRefresh={() => void refetch()}
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Report table with filter/sort/column-pick/CSV export
// ---------------------------------------------------------------------------

interface ReportTableProps {
  kind: ReportKind;
  data: {
    columns: string[];
    rows: string[][];
    rows_meta?: Array<{
      is_release: boolean;
      release_decision: string;
    }>;
  };
  filterState: ReportFilterState;
  onFilterChange: React.Dispatch<React.SetStateAction<ReportFilterState>>;
  releases: StatePayload["releases"];
  releaseId: string;
  generatedAt: string;
  releaseName: string;
  onRefresh: () => void;
}

function ReportTable({
  kind,
  data,
  filterState,
  onFilterChange,
  releases,
  releaseId,
  generatedAt,
  releaseName,
  onRefresh,
}: ReportTableProps) {
  const { columns, rows, rows_meta } = data;

  // Compute visible column indexes (mirrors qaReportVisibleIndexes)
  const visibleIndexes = (() => {
    if (kind !== "release") {
      return columns.map((_, i) => i);
    }
    const colSet = filterState.visibleCols;
    const idxs = columns
      .map((c, i) => {
        const visible = colSet
          ? colSet.has(c)
          : !QA_RELEASE_REPORT_DEFAULT_HIDDEN.has(c);
        return visible ? i : -1;
      })
      .filter((i) => i >= 0);
    return idxs.length ? idxs : [0];
  })();

  // Apply global filter + per-column filters + sort
  const filteredRows = (() => {
    const q = filterState.filter.trim().toLowerCase();
    const colEntries = Object.entries(filterState.colFilters)
      .map(([k, v]) => [Number(k), v.trim().toLowerCase()] as [number, string])
      .filter(([, v]) => v !== "");
    const meta = rows_meta ?? [];
    let rs = rows.map((row, idx) => ({ row, meta: meta[idx] ?? null }));
    if (q) rs = rs.filter((x) => x.row.some((c) => String(c ?? "").toLowerCase().includes(q)));
    if (colEntries.length) {
      rs = rs.filter((x) =>
        colEntries.every(([i, v]) => String(x.row[i] ?? "").toLowerCase().includes(v)),
      );
    }
    const { col, dir } = filterState.sort;
    if (col >= 0) {
      rs.sort((a, b) =>
        dir *
        String(a.row[col] ?? "").localeCompare(String(b.row[col] ?? ""), "zh-CN", {
          numeric: true,
        }),
      );
    }
    return rs;
  })();

  function handleSortClick(colIdx: number) {
    onFilterChange((prev) => {
      const s = prev.sort;
      return {
        ...prev,
        sort:
          s.col === colIdx
            ? { col: colIdx, dir: (s.dir === 1 ? -1 : 1) as 1 | -1 }
            : { col: colIdx, dir: 1 },
      };
    });
  }

  function handleColFilterChange(colIdx: number, value: string) {
    onFilterChange((prev) => {
      const next = { ...prev.colFilters };
      if (value) next[colIdx] = value;
      else delete next[colIdx];
      return { ...prev, colFilters: next };
    });
  }

  function handleColToggle(colName: string, checked: boolean) {
    onFilterChange((prev) => {
      const current =
        prev.visibleCols ??
        new Set(columns.filter((c) => !QA_RELEASE_REPORT_DEFAULT_HIDDEN.has(c)));
      const next = new Set(current);
      if (checked) next.add(colName);
      else {
        next.delete(colName);
        // Ensure at least one visible
        if (next.size === 0) return prev;
      }
      return { ...prev, visibleCols: next };
    });
  }

  function handleCsvExport() {
    const selCols = visibleIndexes.map((i) => columns[i]);
    const selRows = filteredRows.map(({ row }) => visibleIndexes.map((i) => row[i]));
    const ts = generatedAt
      ? new Date(generatedAt)
          .toISOString()
          .replace(/[-:]/g, "")
          .replace("T", "_")
          .slice(0, 15)
      : new Date()
          .toISOString()
          .replace(/[-:]/g, "")
          .replace("T", "_")
          .slice(0, 15);
    const filename =
      kind === "release"
        ? `release_report_${releaseName || ""}_${ts}.csv`
        : `test_cmd_${releaseName || ""}_${ts}.csv`;
    downloadCsv(filename, selCols, selRows);
  }

  const visibleColSet =
    filterState.visibleCols ??
    new Set(columns.filter((c) => !QA_RELEASE_REPORT_DEFAULT_HIDDEN.has(c)));

  return (
    <>
      <div className="qa-report-bar">
        <button className="btn ghost sm" onClick={onRefresh}>
          ↻ 刷新
        </button>
        <button className="btn primary sm" onClick={handleCsvExport}>
          导出 CSV
        </button>

        {kind === "release" && (
          <details className="qa-col-picker">
            <summary>
              列 <span>{visibleIndexes.length}/{columns.length}</span>
            </summary>
            <div className="qa-col-menu">
              {columns.map((c, i) => (
                <label key={i} className="check">
                  <input
                    type="checkbox"
                    checked={visibleColSet.has(c)}
                    onChange={(e) => handleColToggle(c, e.target.checked)}
                  />
                  {c}
                </label>
              ))}
            </div>
          </details>
        )}

        <input
          className="input qa-report-search"
          placeholder="全表关键词筛选…"
          value={filterState.filter}
          onChange={(e) =>
            onFilterChange((prev) => ({ ...prev, filter: e.target.value }))
          }
        />

        <span className="muted small qa-report-time">
          {generatedAt ? `生成于 ${formatServerTime(generatedAt)}` : ""}
        </span>
        <span className="muted small count">
          {filteredRows.length === rows.length
            ? `${rows.length} 行`
            : `${filteredRows.length} / ${rows.length} 行`}
        </span>

        {kind === "release" && (
          <span className="qa-report-compare">
            <span className="muted small" style={{ fontSize: 12 }}>
              对比版本
            </span>
            <select
              className="select sm"
              value={filterState.compareId}
              onChange={(e) =>
                onFilterChange((prev) => ({ ...prev, compareId: e.target.value }))
              }
            >
              <option value="">— 不对比 —</option>
              {releases
                .filter((r) => r.id !== releaseId)
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
            </select>
          </span>
        )}
      </div>

      {kind === "release" && (
        <p className="qa-chip-legend">芯片系列说明：{CHIP_LEGEND}</p>
      )}

      <div className="table report-table" data-testid={`qa-report-table-${kind}`}>
        <table>
          <thead>
            <tr>
              {visibleIndexes.map((i) => {
                const c = columns[i];
                const ind =
                  filterState.sort.col === i
                    ? filterState.sort.dir > 0
                      ? " ▲"
                      : " ▼"
                    : "";
                const cls =
                  kind === "release" && String(c).startsWith("开发者社区")
                    ? "sortable col-comm"
                    : "sortable";
                return (
                  <th key={i} className={cls} onClick={() => handleSortClick(i)}>
                    <span className="th-text">
                      {c}
                      {ind}
                    </span>
                  </th>
                );
              })}
            </tr>
            <tr className="colfilter-row">
              {visibleIndexes.map((i) => (
                <th key={i}>
                  <input
                    placeholder="筛选…"
                    value={filterState.colFilters[i] ?? ""}
                    onChange={(e) => handleColFilterChange(i, e.target.value)}
                    autoComplete="off"
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredRows.length === 0 ? (
              <tr>
                <td colSpan={visibleIndexes.length} className="muted">
                  无匹配数据
                </td>
              </tr>
            ) : (
              filteredRows.map(({ row, meta }, ri) => {
                const trCls = meta && meta.is_release === false ? "row-not-release" : "";
                return (
                  <tr key={ri} className={trCls}>
                    {visibleIndexes.map((i) => {
                      const v = String(row[i] ?? "");
                      const c = columns[i];
                      if (kind === "test" && c === "docker_cmd")
                        return <td key={i} className="cmd">{v}</td>;
                      if (kind === "release") {
                        if (c === "描述")
                          return (
                            <td key={i} className="clamp" title={v}>
                              <div className="clamp-box">{v}</div>
                            </td>
                          );
                        if (["X86支持芯片系列", "ARM支持芯片类型"].includes(c))
                          return (
                            <td key={i} className="col-chip">
                              <div className="cell-wrap">
                                {v.replace(/,/g, ",​")}
                              </div>
                            </td>
                          );
                        if (["git_url", "git_branch"].includes(c))
                          return (
                            <td key={i} className="col-git">
                              <div className="cell-wrap">{c === "git_url" ? formatGerritUrl(v) : v}</div>
                            </td>
                          );
                        if (c === "对比")
                          return (
                            <td key={i} className="col-compare">
                              <div className="cell-wrap">{v}</div>
                            </td>
                          );
                      }
                      return <td key={i}>{v}</td>;
                    })}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Helpers shared within this file
// ---------------------------------------------------------------------------

function qaRows(
  payload: StatePayload,
): Array<{ app: App; snap: Snapshot }> {
  return (payload.apps ?? [])
    .map((app) => ({ app, snap: payload.release?.snapshots?.[app.id] ?? null }))
    .filter(
      (x): x is { app: App; snap: Snapshot } =>
        x.snap !== null && x.snap.release_decision === "release",
    )
    .sort((a, b) =>
      displayName(a.snap).localeCompare(displayName(b.snap), "zh-CN"),
    );
}

function initials(name: string): string {
  const s = String(name || "").replace(/[^a-zA-Z0-9一-龥]/g, "");
  return (s.slice(0, 2) || "··").toUpperCase();
}

function usersLabel(
  usernames: string[] | null | undefined,
  displayNames: Record<string, string>,
): string {
  const items = (usernames ?? [])
    .map((u) => {
      const dn = displayNames[u];
      return dn && dn !== u ? dn : u;
    })
    .filter(Boolean);
  return items.join(",") || "无 owner";
}

// ---------------------------------------------------------------------------
// QaPage — top-level component
// ---------------------------------------------------------------------------

type QaSubtab = "mark" | "release" | "test";

export function QaPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId);
  const setSelectedReleaseId = useUiStore((s) => s.setSelectedReleaseId);
  const [subtab, setSubtab] = useState<QaSubtab>("mark");

  const { data, isFetching, dataUpdatedAt, refetch, error } = useQuery({
    queryKey: STATE_QK(selectedReleaseId),
    queryFn: () => fetchState(selectedReleaseId),
    enabled: !!selectedReleaseId,
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  // Seed selectedReleaseId from first data load if not set
  useEffect(() => {
    if (data?.release?.id && !selectedReleaseId) {
      setSelectedReleaseId(data.release.id);
    }
    if (data?.release?.id) {
      // Populate cache for other tabs too
      queryClient.setQueryData(STATE_QK(data.release.id), data);
    }
  }, [data, selectedReleaseId, setSelectedReleaseId, queryClient]);

  function handleRefresh() {
    void refetch();
  }

  const userIsRM = isRM(user ?? undefined);
  const userIsOwner = isOwner(user ?? undefined);

  const canView = user
    ? ["QA", "RM", "Owner", "Guest"].includes(user.role)
    : false;

  if (!canView) {
    return (
      <section className="view active">
        <div className="page-toolbar">
          <h2>QA</h2>
        </div>
        <p className="muted" style={{ padding: "1rem" }}>
          无权限访问此页面。
        </p>
      </section>
    );
  }

  return (
    <section className="view active">
      <div className="page-toolbar">
        <h2>QA</h2>
        {data && (
          <ReleaseSelector
            releases={data.releases ?? []}
            selectedId={selectedReleaseId || data.release?.id || null}
            onChange={(id) => {
              setSelectedReleaseId(id);
            }}
            userIsRM={userIsRM}
            userIsOwner={userIsOwner}
          />
        )}
        <span className="spacer" />
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          onRefresh={handleRefresh}
          isFetching={isFetching}
        />
      </div>

      {error && (
        <div className="lerr" style={{ padding: "12px 16px" }}>
          加载失败：{error instanceof Error ? error.message : String(error)}
        </div>
      )}

      {!data && isFetching && (
        <div className="muted" style={{ padding: "1rem" }}>
          加载中…
        </div>
      )}

      {data && (
        <>
          <div className="subtabs">
            <button
              className={`subtab ${subtab === "mark" ? "active" : ""}`}
              onClick={() => setSubtab("mark")}
            >
              QA 标注
            </button>
            <button
              className={`subtab ${subtab === "release" ? "active" : ""}`}
              onClick={() => setSubtab("release")}
            >
              Release Report
            </button>
            <button
              className={`subtab ${subtab === "test" ? "active" : ""}`}
              onClick={() => setSubtab("test")}
            >
              Test 命令
            </button>
          </div>

          {subtab === "mark" && (
            <div id="qaMarkPane" className="qa-pane active">
              <QaMarkPane payload={data} onStateRefresh={handleRefresh} />
            </div>
          )}
          {subtab === "release" && (
            <div id="qaReleaseReportPane" className="qa-pane active">
              <ReportPane
                releaseId={selectedReleaseId || data.release?.id || ""}
                kind="release"
                title="Release Report"
                hint="本 release 全部 app 的发布信息汇总（列结构参考 release_report.csv，系统未跟踪的列留空）。"
                releases={data.releases ?? []}
              />
            </div>
          )}
          {subtab === "test" && (
            <div id="qaTestCmdPane" className="qa-pane active">
              <ReportPane
                releaseId={selectedReleaseId || data.release?.id || ""}
                kind="test"
                title="Test 命令"
                hint="由各 app 已上传的 app_info 生成的测试命令清单（输出对应 get_release_report_test_cmd.py）。"
                releases={data.releases ?? []}
              />
            </div>
          )}
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Inline release selector (same pattern as DashboardPage)
// ---------------------------------------------------------------------------

interface ReleaseSelectorProps {
  releases: StatePayload["releases"];
  selectedId: string | null;
  onChange: (id: string) => void;
  userIsRM: boolean;
  userIsOwner: boolean;
}

function ReleaseSelector({
  releases,
  selectedId,
  onChange,
}: ReleaseSelectorProps) {
  if (!releases.length) return <span className="muted small">暂无 release</span>;
  return (
    <select
      className="input"
      style={{ width: "auto", minWidth: 160 }}
      value={selectedId ?? ""}
      onChange={(e) => onChange(e.target.value)}
      aria-label="选择 release"
    >
      {releases.map((r) => (
        <option key={r.id} value={r.id}>
          {r.name}
          {r.maca_version ? ` (${r.maca_version})` : ""}
        </option>
      ))}
    </select>
  );
}
