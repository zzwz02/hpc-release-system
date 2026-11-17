/**
 * QaPage — QA tab (index.html:714-728).
 *
 * Roles: RM / Owner / QA / Guest.
 *
 * QA tools:
 *   1. QA report — report failure summary, scope requests, verdicts, delivery images and App comments
 *   2. 本期发布app — filterable/sortable/column-pick/CSV-export table
 *   3. 测试命令 — same table machinery, no column picker
 *   4. QA log — upload Excel results and compare live / uploaded commands
 *   5. 开发者社区产物
 *   历史查询 / 兼容性验证 — independent routes, shared QA tab row
 *
 * Data:
 *   - QA report: GET /api/qa/report (state, failures, delivery tags and audit)
 *   - Release report / command tables: GET /api/qa-reports (fetched on demand, not from state)
 *   - All data refreshes on entry, explicit refresh or a successful write; no polling.
 */

import React, { useState, useRef, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { QaTabs } from "./QaTabs";
import { QaResultsPane } from "./QaResultsPane";
import { QaTestCommandsPane } from "./QaTestCommandsPane";
import { QaFailureDetails, type QaFailureSummary } from "./QaFailureDetails";
import { qaPaneFromSearch } from "./qaNavigation";
import { RefreshBar } from "../../components/RefreshBar";
import { TestScopeTable } from "../../components/TestScopeTable";
import { DeliveryImages } from "../../components/DeliveryImages";
import { recordedImages } from "../../lib/deliveryImages";
import { apiGet, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { useUiStore } from "../../store/uiStore";
import { canEditQa, canViewQa } from "../../lib/roles";
import { displayName } from "../../lib/identity";
import { formatGerritUrl } from "../../lib/git";
import { formatServerTime } from "../../lib/time";
import { downloadCsv } from "../../lib/csv";
import { qaStatusLabels, qaStatusOptions } from "../../lib/labels";
import { shortCommit, versionTag } from "../appWorkbench/helpers";
import { confirmDialog, promptDialog } from "../../lib/confirm";
import { toast } from "../../lib/toast";
import type {
  StatePayload,
  Entry,
  App,
  QaStatus,
  QaReportsResponse,
  QaReportRowMeta,
  ScopeRequest,
  DeliveryImage,
} from "../../types";
import { can } from "../../lib/accessControl";
import { scopeRequestStatusLabel } from "../../lib/domainMetadata";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Columns hidden by default in the release report (mirrors index.html:1253)
const QA_RELEASE_REPORT_DEFAULT_HIDDEN = new Set([
  "描述", "官方URL", "OS", "Python version", "PyTorch version",
  "开发者社区发布包支持python版本", "开发者社区发布包支持的底层框架及版本",
]);

const CHIP_LEGEND =
  "x201系列芯片包括x201, x203 · x301系列芯片 · x302系列芯片 · C500系列芯片包括C500, C550 · C600系列芯片包括C600 · N300系列芯片包括N300";
// ---------------------------------------------------------------------------
// Query key + fetchers
// ---------------------------------------------------------------------------

const STATE_QK = (releaseId: string) => ["state", releaseId];
const QA_REPORT_QK = (username: string | undefined, role: string | undefined, releaseId: string) =>
  ["qa-report", username, role, releaseId];

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
// QA annotation pane: report-derived failures and manually reviewed App verdicts.
// ---------------------------------------------------------------------------

interface QaMarkPaneProps {
  payload: StatePayload;
  onStateRefresh: () => void;
  report?: QaReportData;
  isFetching: boolean;
  dataUpdatedAt: number;
}

interface QaDeliveryImages {
  entries: Record<string, { revision: string; images: Array<DeliveryImage & { reference_error?: string }> }>;
}

interface QaReportData {
  failure_summary: QaFailureSummary | null;
  delivery_images: QaDeliveryImages | null;
  errors: { failure_summary?: string; delivery_images?: string };
}
type QaReportPayload = StatePayload & { qa_report?: QaReportData };

function QaMarkPane({ payload, onStateRefresh, report, isFetching, dataUpdatedAt }: QaMarkPaneProps) {
  const { user } = useAuth();
  const uiStore = useUiStore();
  const release = payload.release;
  const releaseId = release?.id ?? "";
  const writable = canEditQa(user ?? undefined) && !release?.released_locked;
  const editMode = uiStore.qaEditMode;
  const rows = qaRows(payload);
  const [formState, setFormState] = useState<Record<string, QaForm>>({});
  const [saving, setSaving] = useState(false);
  const editingReleaseRef = useRef<string | null>(null);
  const identityRef = useRef(`${user?.username}:${user?.role}`);
  const saveGeneration = useRef({ value: 0 }).current;
  const identity = `${user?.username}:${user?.role}`;

  useEffect(() => {
    saveGeneration.value++;
    setSaving(false);
    if (identityRef.current !== identity || uiStore.qaEditReleaseId !== releaseId || !writable) {
      identityRef.current = identity;
      uiStore.setQaEditReleaseId(releaseId);
      uiStore.setQaEditMode(false);
      setFormState({});
      editingReleaseRef.current = null;
    }
    return () => { saveGeneration.value++; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [releaseId, identity, writable, saveGeneration]);

  useEffect(() => {
    if (!editMode) { editingReleaseRef.current = null; return; }
    const continuing = editingReleaseRef.current === releaseId;
    editingReleaseRef.current = releaseId;
    setFormState(previous => Object.fromEntries(rows.map(({ app, entry }) => {
      const draft = continuing ? previous[app.id] : undefined;
      return [app.id, draft ?? {
        status: entry.qa_status || "not_checked", note: entry.qa_note || "",
        images: recordedImages(entry.deliverable_images, entry.deliverable_image, entry.deliverable_digest),
        revision: entry.delivery_revision ?? "",
      }];
    })));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editMode, releaseId]);

  const summary = { data: report?.failure_summary, isFetching, dataUpdatedAt,
    error: report?.errors.failure_summary ? new Error(report.errors.failure_summary) : null };
  const summaryAvailable = !summary.isFetching && !summary.error && !!summary.data;
  const groups = summaryAvailable ? summary.data?.groups ?? [] : [];
  const failures = new Map(groups.filter(group => group.app_id).map(group => [group.app_id!, group.failures]));
  const signedUp = new Set(rows.map(({ app }) => app.id));
  const unmatched = groups.filter(group => !group.app_id || !signedUp.has(group.app_id));
  const deliveryView = { data: report?.delivery_images, isFetching,
    error: report?.errors.delivery_images ? new Error(report.errors.delivery_images) : null };
  const displayedImages = !deliveryView.isFetching && !deliveryView.error ? deliveryView.data?.entries ?? {} : {};

  function handleCancel() {
    uiStore.setQaEditMode(false);
    setFormState({});
  }

  async function handleSave() {
    if (!writable || saving) return;
    const items = rows.flatMap(({ app, entry }) => {
      const draft = formState[app.id];
      const status = draft?.status ?? (entry.qa_status || "not_checked");
      const note = draft?.note ?? (entry.qa_note || "");
      const images = draft?.images ?? recordedImages(entry.deliverable_images, entry.deliverable_image, entry.deliverable_digest);
      const imagesChanged = JSON.stringify(images) !== JSON.stringify(recordedImages(entry.deliverable_images, entry.deliverable_image, entry.deliverable_digest));
      if (status === entry.qa_status && note === entry.qa_note && !imagesChanged) return [];
      return [{ app_id: app.id, status, issue_note: note,
        delivery_revision: draft?.revision ?? entry.delivery_revision ?? "",
        ...(imagesChanged ? { deliverable_images: images } : {}),
      }];
    });
    if (!items.length) { handleCancel(); return; }
    const generation = ++saveGeneration.value;
    setSaving(true);
    try {
      await apiPost("/api/qa/status-batch", { release_id: releaseId, items });
      if (generation !== saveGeneration.value) return;
      handleCancel();
      onStateRefresh();
      toast.success("QA 状态和 comment 已保存");
    } catch (error) {
      if (generation === saveGeneration.value) toast.error("保存失败，未保存任何改动，请修正后重试：\n\n" +
        (error instanceof Error ? error.message : String(error)));
    } finally {
      if (generation === saveGeneration.value) setSaving(false);
    }
  }

  if (!release) return <div className="panel"><div className="panel-body"><p className="muted">请选择版本。</p></div></div>;
  const qaFieldsWritable = writable && editMode && !saving;
  const actionBar = canEditQa(user ?? undefined) ? <div className="qa-actions">
    {editMode ? <>
      <button className="btn primary qaSaveAll" disabled={!writable || saving} onClick={() => void handleSave()}>保存 QA 状态</button>
      <button className="btn ghost qaCancel" disabled={saving} onClick={handleCancel}>取消编辑</button>
    </> : <button className="btn primary qaEdit" disabled={!writable} onClick={() => uiStore.setQaEditMode(true)}>
      ✎ 修改（填写 QA 结论、交付镜像和 comment）
    </button>}
  </div> : null;

  return <div id="qaPanel">
    <ScopeRequestsPanel payload={payload} onDecided={onStateRefresh} />
    {actionBar}
    {deliveryView.isFetching && <p className="muted small" role="status">正在补全历史交付镜像的 tag…</p>}
    {deliveryView.error && <p className="lerr" role="alert">交付镜像 tag 读取失败：{deliveryView.error.message}</p>}
    <div className="panel" data-testid="qa-failure-summary">
      <div className="panel-head">
        <h2>QA log 失败汇总</h2>
        {summaryAvailable && <span className="count">{summary.data?.failed_rows ?? 0} 条失败 / {summary.data?.total_rows ?? 0} 条结果</span>}
        <span className="spacer" />
        <RefreshBar dataUpdatedAt={summary.dataUpdatedAt} isFetching={summary.isFetching} onRefresh={onStateRefresh} />
      </div>
      <div className="panel-body">
        <p className="hint">来自本版本当前 QA log，按 App、分支、GPU 和测试项汇总 fail 结果，保留具体 HPC/App 版本与 QA log 的 QA comment。汇总不自动修改 App 的 QA 结论；每个 App 的手填 QA comment 位于卡片末尾。</p>
        {summary.error && <p className="lerr" role="alert">失败汇总加载失败：{summary.error.message}</p>}
        {summary.isFetching && <p className="muted" role="status">正在读取 QA log 失败项…</p>}
        {summaryAvailable && !(summary.data?.total_rows) && <p className="muted">本版本尚无 QA log 测试结果，请在 QA log 上传 Excel。</p>}
        {summaryAvailable && !!summary.data?.total_rows && !summary.data.failed_rows && <p className="muted">当前 QA log 没有标为 fail 的测试结果。</p>}
        {unmatched.length > 0 && <details open>
          <summary>未关联本期有效报名的失败记录</summary>
          {unmatched.map(group => <section key={JSON.stringify([group.reported_app_id, group.app_name, group.git_branch])}>
            <h3>{group.app_name} <span className="small muted">{[group.reported_app_id, group.git_branch].filter(Boolean).join(" · ")}</span></h3>
            <QaFailureDetails failures={group.failures} />
          </section>)}
        </details>}
      </div>
    </div>
    {editMode && <div className="banner warnp mt-14">编辑中 · 刷新前请先保存或取消。QA 状态、交付镜像和手填 comment 在保存后生效。</div>}
    {rows.length === 0 ? <div className="banner mt-14">本版本暂无报名的 App。</div> : <div className="qa-grid">
      {rows.map(({ app, entry }) => {
        const draft = editMode ? formState[app.id] : undefined;
        const status = (draft?.status ?? entry.qa_status ?? "not_checked") as QaStatus;
        const inScope = entry.scope_tests.filter(test => !test.excluded);
        const failed = failures.get(app.id) ?? [];
        const imageView = displayedImages[app.id];
        const matchingImages = imageView && imageView.revision === entry.delivery_revision ? imageView.images : null;
        const pillClass = status === "qa_passed" ? "ok" : status === "has_issues" ? "warnp" : status === "cannot_release" ? "bad" : "";
        return <div key={app.id} className="qa-card" data-qa-app={app.id}>
          <div className="qa-card-h">
            <span className="app-ico">{initials(displayName(app))}</span>
            <div><div className="name">{displayName(app)}{" "}
              {entry.target_version && <span className="ver-tag">{versionTag(entry.target_version)}</span>}{" "}
              {entry.pending_scope_request && <span className="pill warnp">范围扩大待审批</span>}
            </div><div className="sub">{usersLabel(app.owners, payload.user_display_names ?? {})}</div></div>
            <span className="qa-cur"><span className={`pill ${pillClass}`}>{qaStatusLabels[status] ?? status}</span></span>
          </div>
          <div className="qa-card-b">
            <label>QA 状态<select className="select" disabled={!qaFieldsWritable} value={status}
              onChange={event => setFormState(previous => ({ ...previous, [app.id]: { ...previous[app.id], status: event.target.value } }))}
              data-testid={`qa-status-${app.id}`}>
              {qaStatusOptions.map(value => <option key={value} value={value}>{qaStatusLabels[value]}</option>)}
            </select></label>
            {summaryAvailable && <details className="mt-6 mb-6" data-testid={`qa-app-failures-${app.id}`}>
              <summary><b>QA log 失败项（{failed.length}）</b></summary>
              {failed.length ? <QaFailureDetails failures={failed} /> : <p className="muted small">QA log 未记录本 App 的失败项。</p>}
            </details>}
            <div className="qa-delivery-images"><b>交付镜像（按报名 app_info 的 app_build）</b>
              <DeliveryImages images={!editMode && matchingImages ? matchingImages : draft?.images ?? recordedImages(entry.deliverable_images, entry.deliverable_image, entry.deliverable_digest)}
                builds={entry.delivery_builds} onChange={qaFieldsWritable ? images => setFormState(previous => ({ ...previous, [app.id]: { ...previous[app.id], images } })) : undefined} />
              {!editMode && matchingImages?.filter(image => image.reference_error).map(image => <p className="lerr small" key={image.build_key}>
                {image.os || "未指定构建"} · {image.arch} · {image.image_target}：交付镜像 tag 未能补全，{image.reference_error}
              </p>)}
              {!editMode && imageView && !matchingImages && <p className="muted small">交付镜像记录已变化，请刷新当前页。</p>}
            </div>
            <details className="mt-6"><summary className="small muted">测试范围 {inScope.length} 项{entry.planned_features ? " · 计划 feature" : ""}</summary>
              {entry.planned_features && <p className="small">{entry.planned_features}</p>}
              <TestScopeTable tests={inScope} />
            </details>
            <p className="small muted">Gerrit: {formatGerritUrl(app.git_url)} {app.git_branch || ""}</p>
            <label>QA comment (可选)
              <textarea aria-label={`${app.official_name?.trim() || displayName(app)} QA comment`} disabled={!qaFieldsWritable}
                placeholder={qaFieldsWritable ? "可填写测试结论或其他说明" : ""}
                value={draft?.note ?? entry.qa_note ?? ""}
                onChange={event => setFormState(previous => ({ ...previous, [app.id]: { ...previous[app.id], note: event.target.value } }))} />
            </label>
          </div>
        </div>;
      })}
    </div>}
    {actionBar}
    <QaChangeLogPanel payload={payload} rows={rows} />
  </div>;
}

// ---------------------------------------------------------------------------
// QA audit change log (index.html:2281-2330)
// ---------------------------------------------------------------------------

interface QaChangeLogPanelProps {
  payload: StatePayload;
  rows: Array<{ app: App; entry: Entry }>;
}

function QaChangeLogPanel({ payload, rows }: QaChangeLogPanelProps) {
  const auditLogs = payload.qa_audit_logs ?? {};

  // Flatten + sort all entries by timestamp descending
  const allEntries = rows.flatMap(({ app }) =>
    (auditLogs[app.id] ?? []).map((e) => ({
      ...e,
      app_label: displayName(app),
    })),
  ).sort((a, b) => {
    const at = new Date(a.ts || "").getTime() || 0;
    const bt = new Date(b.ts || "").getTime() || 0;
    return bt - at;
  });

  const countText = `${allEntries.length} 条`;

  return (
    <details className="section qa-change-log-panel" id="qaChangeLogPanel">
      <summary>
        <span className="chev">▶</span> QA 变更记录（本版本）
        <span className="badge">{countText}</span>
      </summary>
      <div className="section-body">
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
        className={`cl-row ${hasDetail ? "cl-clickable pointer" : ""}`}
        onClick={hasDetail ? () => setOpen((v) => !v) : undefined}
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

  const columnsKey = (kind === "release" ? data?.release_report.columns : data?.test_cmd.columns)?.join("\0") ?? "";
  const previousColumns = useRef("");
  useEffect(() => {
    if (previousColumns.current && columnsKey && previousColumns.current !== columnsKey) {
      setFilterState((previous) => ({ ...previous, colFilters: {}, sort: { col: -1, dir: 1 } }));
    }
    if (columnsKey) previousColumns.current = columnsKey;
  }, [columnsKey]);

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
          <div className="lerr mb-8">
            加载失败：{error instanceof Error ? error.message : String(error)}
          </div>
        )}
        {!rawData && !isFetching && (
          <>
            <p className="muted small">{hint}</p>
            <div className="row mt-9">
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
    rows_meta?: QaReportRowMeta[];
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
            <span className="muted small fs-12">
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

      <div className="table report-table sticky-first-col" data-testid={`qa-report-table-${kind}`}>
        <table>
          <thead>
            <tr>
              {visibleIndexes.map((i) => {
                const c = columns[i];
                const sorted = filterState.sort.col === i;
                const cls = [
                  "sortable",
                  sorted ? "sorted" : "",
                  kind === "release" && String(c).startsWith("开发者社区") ? "col-comm" : "",
                ].filter(Boolean).join(" ");
                return (
                  <th key={i} className={cls} onClick={() => handleSortClick(i)}>
                    <span className="th-text">
                      {c}
                      <span className="sort-ind" aria-hidden="true">
                        {sorted ? (filterState.sort.dir > 0 ? "▲" : "▼") : "▲"}
                      </span>
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
                const trCls = [
                  meta && meta.signed_up === false ? "row-not-release" : "",
                ].filter(Boolean).join(" ");
                return (
                  <tr key={ri} className={trCls}>
                    {visibleIndexes.map((i) => {
                      const v = String(row[i] ?? "");
                      const c = columns[i];
                      if (kind === "test" && c === "docker_cmd")
                        return <td key={i} className="cmd">{v}</td>;
                      if (kind === "test" && c === "commit_id")
                        return <td key={i} title={v}>{shortCommit(v)}</td>;
                      if (kind === "test" && c === "cicd_status" && v)
                        return <td key={i}><span className="pill warnp">{v}</span></td>;
                      if (kind === "release") {
                        if (c === "CICD状态" && v)
                          return <td key={i}><span className="pill warnp">{v}</span></td>;
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
// Developer-community artifacts pane
// ---------------------------------------------------------------------------

interface CommunityArtifactFile {
  filename: string;
  content: unknown;
}

interface CommunityArtifactsResponse {
  sdkversion: string;
  category: string;
  files: CommunityArtifactFile[];
}

interface CommunityArtifactsPaneProps {
  defaultSdkversion: string;
}

function CommunityArtifactsPane({ defaultSdkversion }: CommunityArtifactsPaneProps) {
  const [sdkversion, setSdkversion] = useState(defaultSdkversion || "");
  const [data, setData] = useState<CommunityArtifactsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const previousDefaultRef = useRef(defaultSdkversion);

  useEffect(() => {
    const previousDefault = previousDefaultRef.current;
    previousDefaultRef.current = defaultSdkversion;
    setSdkversion((current) => {
      if (!current || current === previousDefault) return defaultSdkversion || "";
      return current;
    });
    setData(null);
    setError("");
  }, [defaultSdkversion]);

  async function handleFetch() {
    const version = sdkversion.trim();
    if (!version) {
      toast.info("请输入完整 SDK 版本号");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const result = await apiPost<CommunityArtifactsResponse>(
        "/api/qa/community-artifacts",
        { sdkversion: version, category: "ai4s" },
      );
      setData(result);
      toast.success(`已获取 ${result.files.length} 个 JSON 文件`);
    } catch (ex) {
      const message = ex instanceof Error ? ex.message : String(ex);
      setError(message);
      toast.error("获取失败：" + message);
    } finally {
      setLoading(false);
    }
  }

  function handleDownloadAll() {
    if (!data?.files.length) return;
    downloadCommunityArtifactsZip(data);
  }

  return (
    <div className="panel community-artifacts-panel">
      <div className="panel-head">
        <h2>开发者社区产物</h2>
        {data && <span className="count">{data.files.length} 个文件</span>}
      </div>
      <div className="panel-body">
        <div className="community-artifact-toolbar">
          <label className="community-version-field">
            <span className="muted small">SDK 版本</span>
            <input
              className="input"
              value={sdkversion}
              placeholder="请输入具体版本"
              onChange={(e) => setSdkversion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !loading) void handleFetch();
              }}
            />
          </label>
          <button className="btn primary" disabled={loading} onClick={() => void handleFetch()}>
            {loading ? "获取中…" : "获取最新产物"}
          </button>
          <button
            className="btn"
            disabled={!data?.files.length || loading}
            onClick={handleDownloadAll}
          >
            下载全部
          </button>
        </div>

        {error && <div className="lerr mt-9">加载失败：{error}</div>}
        {!data && !loading && !error && (
          <p className="muted small mt-9">暂无数据。</p>
        )}
        {loading && <p className="muted small mt-9">加载中…</p>}
        {data && (
          <div className="community-artifact-list">
            {data.files.map((file) => (
              <details key={file.filename} className="community-artifact-file" open>
                <summary>
                  <span>{file.filename}</span>
                  <span className="badge">JSON</span>
                </summary>
                <pre>{JSON.stringify(file.content, null, 4)}</pre>
              </details>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

let crcTable: Uint32Array | null = null;

function getCrcTable(): Uint32Array {
  if (crcTable) return crcTable;
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  crcTable = table;
  return table;
}

function crc32(bytes: Uint8Array): number {
  const table = getCrcTable();
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc = table[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function createZipBlob(files: CommunityArtifactFile[]): Blob {
  const encoder = new TextEncoder();
  const chunks: Uint8Array[] = [];
  const centralChunks: Uint8Array[] = [];
  let offset = 0;

  for (const file of files) {
    const nameBytes = encoder.encode(file.filename);
    const dataBytes = encoder.encode(`${JSON.stringify(file.content, null, 4)}\n`);
    const crc = crc32(dataBytes);

    const local = new Uint8Array(30 + nameBytes.length);
    const localView = new DataView(local.buffer);
    localView.setUint32(0, 0x04034b50, true);
    localView.setUint16(4, 20, true);
    localView.setUint16(6, 0x0800, true);
    localView.setUint16(8, 0, true);
    localView.setUint16(10, 0, true);
    localView.setUint16(12, 0, true);
    localView.setUint32(14, crc, true);
    localView.setUint32(18, dataBytes.length, true);
    localView.setUint32(22, dataBytes.length, true);
    localView.setUint16(26, nameBytes.length, true);
    localView.setUint16(28, 0, true);
    local.set(nameBytes, 30);
    chunks.push(local, dataBytes);

    const central = new Uint8Array(46 + nameBytes.length);
    const centralView = new DataView(central.buffer);
    centralView.setUint32(0, 0x02014b50, true);
    centralView.setUint16(4, 20, true);
    centralView.setUint16(6, 20, true);
    centralView.setUint16(8, 0x0800, true);
    centralView.setUint16(10, 0, true);
    centralView.setUint16(12, 0, true);
    centralView.setUint16(14, 0, true);
    centralView.setUint32(16, crc, true);
    centralView.setUint32(20, dataBytes.length, true);
    centralView.setUint32(24, dataBytes.length, true);
    centralView.setUint16(28, nameBytes.length, true);
    centralView.setUint16(30, 0, true);
    centralView.setUint16(32, 0, true);
    centralView.setUint16(34, 0, true);
    centralView.setUint16(36, 0, true);
    centralView.setUint32(38, 0, true);
    centralView.setUint32(42, offset, true);
    central.set(nameBytes, 46);
    centralChunks.push(central);

    offset += local.length + dataBytes.length;
  }

  const centralStart = offset;
  const centralSize = centralChunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  endView.setUint32(0, 0x06054b50, true);
  endView.setUint16(4, 0, true);
  endView.setUint16(6, 0, true);
  endView.setUint16(8, files.length, true);
  endView.setUint16(10, files.length, true);
  endView.setUint32(12, centralSize, true);
  endView.setUint32(16, centralStart, true);
  endView.setUint16(20, 0, true);

  return new Blob(
    [...chunks, ...centralChunks, end].map((chunk) => {
      const copy = new Uint8Array(chunk.length);
      copy.set(chunk);
      return copy.buffer;
    }),
    { type: "application/zip" },
  );
}

function downloadCommunityArtifactsZip(data: CommunityArtifactsResponse) {
  const blob = createZipBlob(data.files);
  const safeVersion = data.sdkversion.replace(/[^A-Za-z0-9_.-]/g, "_");
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `developer-community-artifacts_${safeVersion}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ---------------------------------------------------------------------------
// Helpers shared within this file
// ---------------------------------------------------------------------------

interface QaForm {
  status: string;
  note: string;
  images: DeliveryImage[];
  revision: string;
}

/** Active signups of the selected cycle with their App. */
function qaRows(payload: StatePayload): Array<{ app: App; entry: Entry }> {
  const apps = new Map((payload.apps ?? []).map((app) => [app.id, app]));
  return (payload.release?.entries ?? [])
    .filter((entry) => entry.status === "active" && apps.has(entry.app_id))
    .map((entry) => ({ app: apps.get(entry.app_id)!, entry }))
    .sort((a, b) => displayName(a.app).localeCompare(displayName(b.app), "zh-CN"));
}

// ---------------------------------------------------------------------------
// Scope-expansion requests (QA approves or rejects)
// ---------------------------------------------------------------------------

function ScopeRequestsPanel({ payload, onDecided }: { payload: StatePayload; onDecided: () => void }) {
  const { user } = useAuth();
  const [showAll, setShowAll] = useState(false);
  const requests: ScopeRequest[] = payload.release?.scope_requests ?? [];
  const canDecide = can(user?.role, "app.app_info.expand_qa_scope")
    && ["in_progress", "qa_closed"].includes(payload.release?.phase ?? "");
  const pending = requests.filter((r) => r.status === "pending");
  const shown = showAll ? requests : pending;
  if (!requests.length) return null;

  async function decide(request: ScopeRequest, approve: boolean) {
    let note = "";
    if (!approve) {
      const reason = await promptDialog({
        title: "拒绝测试范围扩大",
        body: `${request.app_name}：拒绝后本期仍按原 app_info 测试。请填写理由。`,
        confirmText: "拒绝",
        danger: true,
      });
      if (reason === null) return;
      note = reason;
    } else if (!(await confirmDialog({
      title: "同意测试范围扩大",
      body: `${request.app_name}：同意后报名条目改用新的 app_info，测试范围扩大为：\n${request.additions.join("\n")}`,
      confirmText: "同意",
    }))) {
      return;
    }
    try {
      await apiPost("/api/scope-requests/decide", { request_id: request.id, approve, note });
      toast.success(approve ? "已同意" : "已拒绝");
      onDecided();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="panel" data-testid="scope-requests-panel">
      <div className="panel-head">
        <h2>测试范围扩大申请</h2>
        <span className="count">待处理 {pending.length} 条</span>
        <span className="flex-1" />
        <label className="check small">
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
          显示已处理
        </label>
      </div>
      <div className="panel-body">
        <p className="hint mt-0">报名截止后 app_info 扩大了测试范围（新增芯片、测试项、构建等）时自动生成；同意前报名条目按原版本测试。</p>
        {shown.length === 0 ? (
          <p className="muted small">没有待处理的申请。</p>
        ) : (
          <div className="table">
            <table>
              <thead><tr><th>#</th><th>App</th><th>扩大内容</th><th>时间</th><th>状态</th><th /></tr></thead>
              <tbody>
                {shown.map((request) => (
                  <tr key={request.id} data-testid={`scope-request-${request.id}`}>
                    <td>{request.id}</td>
                    <td>{request.app_name}</td>
                    <td><ul className="small m-0">{request.additions.map((a) => <li key={a}>{a}</li>)}</ul></td>
                    <td className="small">{formatServerTime(request.created_at)}</td>
                    <td className="small">
                      {scopeRequestStatusLabel(request.status)}
                      {request.decided_by && <div className="muted">{request.decided_by} {request.decision_note}</div>}
                    </td>
                    <td className="nowrap">
                      {request.status === "pending" && canDecide && (
                        <>
                          <button className="btn primary sm" onClick={() => void decide(request, true)}>同意</button>
                          <button className="btn ghost sm" onClick={() => void decide(request, false)}>拒绝</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
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

export function QaPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId);
  const setSelectedReleaseId = useUiStore((s) => s.setSelectedReleaseId);
  const { search } = useLocation();
  const subtab = qaPaneFromSearch(search);

  const { data, isFetching, dataUpdatedAt, refetch, error } = useQuery<QaReportPayload>({
    queryKey: subtab === "mark" ? QA_REPORT_QK(user?.username, user?.role, selectedReleaseId) : STATE_QK(selectedReleaseId),
    queryFn: () => subtab === "mark"
      ? apiGet<QaReportPayload>(`/api/qa/report?release_id=${encodeURIComponent(selectedReleaseId)}`)
      : fetchState(selectedReleaseId),
    enabled: !!user && (subtab === "mark" || !!selectedReleaseId),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: "always",
  });

  // Seed selectedReleaseId from first data load if not set
  useEffect(() => {
    if (data?.release?.id && !selectedReleaseId) {
      if (subtab === "mark") {
        queryClient.setQueryData(QA_REPORT_QK(user?.username, user?.role, data.release.id), data);
      }
      setSelectedReleaseId(data.release.id);
    }
    if (data?.release?.id && subtab !== "mark") {
      // Populate cache for other tabs too
      queryClient.setQueryData(STATE_QK(data.release.id), data);
    }
  }, [data, selectedReleaseId, setSelectedReleaseId, queryClient, subtab, user?.username, user?.role]);

  function handleRefresh() {
    void refetch();
    if (subtab === "mark") {
      void queryClient.invalidateQueries({ queryKey: STATE_QK(selectedReleaseId), refetchType: "none" });
    }
    if (subtab === 'report') {
      void queryClient.invalidateQueries({ queryKey: ['qa-test-results', user?.username, user?.role, selectedReleaseId] });
    }
  }

  const canView = canViewQa(user ?? undefined);

  if (!canView) {
    return (
      <section className="view active">
        <div className="page-toolbar">
          <h2>QA</h2>
        </div>
        <p className="muted p-1r">
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
          />
        )}
        <span className="spacer" />
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          onRefresh={handleRefresh}
          isFetching={isFetching}
        />
      </div>

      <QaTabs />
      {subtab === "report" && <QaResultsPane key={`${user?.username}:${user?.role}:${selectedReleaseId}`}
        releaseId={selectedReleaseId || data?.release?.id || ''}
        releaseName={data?.releases.find(release => release.id === selectedReleaseId)?.name || data?.release?.name || ''} />}

      {error && (
        <div className="lerr p-12-16">
          加载失败：{error instanceof Error ? error.message : String(error)}
        </div>
      )}

      {subtab !== "report" && !data && isFetching && (
        <div className="muted p-1r">
          加载中…
        </div>
      )}

      {data && (
        <>
          {subtab === "mark" && (
            <div id="qaMarkPane" className="qa-pane active">
              <QaMarkPane payload={data} report={error ? undefined : data.qa_report} onStateRefresh={handleRefresh}
                isFetching={isFetching} dataUpdatedAt={dataUpdatedAt} />
            </div>
          )}
          {subtab === "release" && (
            <div id="qaReleaseReportPane" className="qa-pane active">
              <ReportPane
                releaseId={selectedReleaseId || data.release?.id || ""}
                kind="release"
                title="本期发布app"
                hint="本版本报名 App 的发布信息汇总（列结构参考 release_report.csv，系统未跟踪的列留空）。"
                releases={data.releases ?? []}
              />
            </div>
          )}
          {subtab === "test" && (
            <div id="qaTestCmdPane" className="qa-pane active">
              <QaTestCommandsPane releaseId={selectedReleaseId || data.release?.id || ""} />
            </div>
          )}
          {subtab === "community" && (
            <div id="qaCommunityArtifactsPane" className="qa-pane active">
              <CommunityArtifactsPane
                defaultSdkversion={
                  data.release?.environment.find((row) => /maca|sdk/i.test(row.component))?.version ?? ""
                }
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
}

function ReleaseSelector({
  releases,
  selectedId,
  onChange,
}: ReleaseSelectorProps) {
  if (!releases.length) return <span className="muted small">暂无版本</span>;
  return (
    <select
      className="input select-inline"
      value={selectedId ?? ""}
      onChange={(e) => onChange(e.target.value)}
      aria-label="选择 release"
    >
      {releases.map((r) => (
        <option key={r.id} value={r.id}>
          {r.name}
        </option>
      ))}
    </select>
  );
}
