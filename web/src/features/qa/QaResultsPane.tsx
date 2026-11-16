import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiGetBlob, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { can } from "../../lib/accessControl";
import { FlatCommandTable, type CommandRow, type CommandTableFilters } from "../../components/FlatCommandTable";
import { formatServerTime } from "../../lib/time";
import { triggerBlobDownload } from "../../lib/csv";
import { confirmDialog } from "../../lib/confirm";
import { QaEnvironmentPreview } from "./QaEnvironmentPreview";

interface ResultResponse { total: number; rows: CommandRow[]; columns: string[]; revision?: string }
interface UploadSummary {
  release_name: string;
  release_test_count: number;
  total_rows: number;
  rows: number;
  skipped_rows: number;
  duplicate_rows: number;
  skipped_apps: { app_id: string; git_branch: string; rows: number }[];
}
const EMPTY_FILTERS: CommandTableFilters = { filter: "", columnFilters: {}, sort: null };

function resultParams(releaseId: string, search: string, filters: CommandTableFilters) {
  const params = new URLSearchParams({ release_id: releaseId, search });
  if (filters.filter) params.set("filter", filters.filter);
  const columns = Object.fromEntries(Object.entries(filters.columnFilters).filter(([, value]) => value.trim()));
  if (Object.keys(columns).length) params.set("column_filters", JSON.stringify(columns));
  if (filters.sort) {
    params.set("sort_by", filters.sort.column);
    params.set("sort_direction", filters.sort.direction === 1 ? "asc" : "desc");
  }
  return params;
}

async function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取 Excel 文件"));
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.readAsDataURL(file);
  });
}

export function QaResultsPane({ releaseId, releaseName }: { releaseId: string; releaseName: string }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [uploadSummary, setUploadSummary] = useState<UploadSummary | null>(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState(EMPTY_FILTERS);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const [loadedColumns, setLoadedColumns] = useState<string[] | null>(null);
  const filterPending = filters !== appliedFilters;
  const generation = useRef({ value: 0 }).current;
  useEffect(() => {
    const timer = window.setTimeout(() => setAppliedFilters(filters), 350);
    return () => window.clearTimeout(timer);
  }, [filters]);
  const query = useQuery({
    queryKey: ["qa-test-results", user?.username, user?.role, releaseId, search, appliedFilters, offset],
    queryFn: () => {
      const params = resultParams(releaseId, search, appliedFilters);
      params.set("offset", String(offset)); params.set("limit", "50");
      return apiGet<ResultResponse>(`/api/qa/test-results?${params}`);
    },
    enabled: !!releaseId && !filterPending,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === user?.username && previousQuery?.queryKey[2] === user?.role && previousQuery?.queryKey[3] === releaseId ? previous : undefined,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
    refetchOnMount: 'always',
  });
  useEffect(() => {
    generation.value++;
    setInput(""); setSearch(""); setOffset(0); setMessage(""); setUploadError(""); setUploadSummary(null); setBusy(false);
    setFilters(EMPTY_FILTERS); setAppliedFilters(EMPTY_FILTERS); setExporting(false); setExportError(""); setLoadedColumns(null);
    return () => { generation.value++; };
  }, [user?.username, user?.role, releaseId, generation]);
  useEffect(() => {
    if (query.data) setLoadedColumns(query.data.columns);
  }, [query.data]);

  async function upload(file: File) {
    const id = ++generation.value;
    setBusy(true); setMessage(""); setUploadError(""); setUploadSummary(null); setExporting(false);
    try {
      if (file.size > 20 * 1024 * 1024) throw new Error("Excel 文件不能超过 20 MB");
      const confirmed = await confirmDialog({
        title: `整份替换 ${releaseName} 的 QA log`,
        body: `将上传「${file.name}」。全部校验通过后，${releaseName} 发布周期下所有旧 QA log 结果（含各补丁版本）和旧 Excel 文件都会被清除，仅保留本次文件中填写了 QA 列的行。未出现在本次文件中的旧测试项也会删除，不与旧报告合并。\n覆盖后无法恢复旧报告；其他发布周期不受影响，校验失败则保留原报告。`,
        danger: true,
        confirmText: "确认整份替换",
        cancelText: "取消上传",
      });
      if (!confirmed || id !== generation.value) return;
      const content_base64 = await fileBase64(file);
      if (id !== generation.value) return;
      const data = await apiPost<{ replaced: number } & UploadSummary>("/api/qa/test-results/upload", {
        filename: file.name, content_base64, release_id: releaseId,
        ...(query.data?.revision ? { revision: query.data.revision } : {}),
      });
      void queryClient.invalidateQueries({ queryKey: ["qa-test-results", user?.username, user?.role, releaseId] });
      void queryClient.invalidateQueries({ queryKey: ["qa-failure-summary", user?.username, user?.role, releaseId] });
      if (id !== generation.value) return;
      setMessage(`已保存 ${releaseName} 的 ${data.rows} 行，清除旧结果 ${data.replaced} 行。${data.duplicate_rows ? `合并 ${data.duplicate_rows} 个重复测试行。` : ""}`);
      setUploadSummary(data);
      setOffset(0);
      setInput(''); setSearch(''); setFilters(EMPTY_FILTERS); setAppliedFilters(EMPTY_FILTERS);
    } catch (e) {
      if (id === generation.value) setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === generation.value) setBusy(false);
    }
  }

  async function exportResults() {
    const id = generation.value;
    setExporting(true); setExportError("");
    try {
      const blob = await apiGetBlob(`/api/qa/test-results/export?${resultParams(releaseId, search, appliedFilters)}`);
      if (id === generation.value) triggerBlobDownload("qa_test_results.csv", blob);
    } catch (e) {
      if (id === generation.value) setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === generation.value) setExporting(false);
    }
  }

  const tableBusy = filterPending || query.isFetching || query.isPlaceholderData;
  const tableColumns = query.data?.columns ?? loadedColumns;
  const total = query.data?.total ?? 0;

  const rows = (query.data?.rows ?? []).map(row => ({
    ...row, "Excel 提交时间": row.uploaded_at,
    "提交人": row.uploaded_by, "原始 Excel": row.filename,
  }));
  return <div className="panel" data-testid="qa-results-pane">
    <div className="panel-body">
      <h2>QA log{releaseName && ` · ${releaseName}`}</h2>
      <p className="hint">每个发布周期只保留一份 QA log。文件中的 hpc_version 必须属于所选周期。</p>
      <p className="hint">上传测试命令 CSV 另存的 Excel，第一个页签为测试详表，后续环境页原样保留。填写 QA 列后须提供 qa_digest 和与环境页同名的 qa_env；qa_result 支持数字或文字，qa_status 仅允许 pass / fail 或留空。已填写的 QA 行仍严格校验 git_branch。QA 列全空的行跳过，上传后显示数量和 App／分支明细；后续页签内容不参与测试行校验。</p>
      <p className="hint">版本测试总数按有效报名的锁定范围展开（含 GPU 组合和缺镜像项）；文件条数为第一个页签的非空测试行，不含表头和环境页。重复组合仅保存最后一行，被覆盖的重复行计入跳过数。</p>
      {!releaseId && <p className="muted">请选择发布周期。</p>}
      {can(user?.role, "qa.edit") && <p className="lerr"><strong>整份替换/覆盖：</strong>上传成功将清空{releaseName ? ` ${releaseName} ` : "所选"}周期的全部旧结果（含各补丁版本）和旧 Excel，仅保留本次文件的数据。未包含的旧测试项也会删除，覆盖后无法恢复；其他周期不受影响。选择文件后需确认，校验失败保留原报告。</p>}
      {can(user?.role, "qa.edit") && <label>上传测试结果 Excel <input type="file" accept=".xlsx" aria-label="上传测试结果 Excel" disabled={busy || !releaseId || query.isPending || query.isFetching} onChange={e => {
        const file = e.target.files?.[0]; e.target.value = ""; if (file) void upload(file);
      }} /></label>}
      {busy && <p role="status">正在处理上传，全部校验通过后整份替换…</p>}
      {message && <p role="status">{message}</p>}
      {uploadSummary && <div className={uploadSummary.skipped_rows + uploadSummary.duplicate_rows ? "warnp" : "hint"} data-testid="qa-upload-summary">
        <p role="status">{uploadSummary.release_name} 版本共 {uploadSummary.release_test_count} 条测试；文件含 {uploadSummary.total_rows} 条；保存 {uploadSummary.rows} 条；跳过 {uploadSummary.skipped_rows + uploadSummary.duplicate_rows} 条。</p>
        <p>跳过明细：QA 列全空 {uploadSummary.skipped_rows} 条，重复组合 {uploadSummary.duplicate_rows} 条。QA 列全空行未校验、未入库。</p>
        {!!uploadSummary.skipped_rows && <details>
          <summary>查看跳过明细（{uploadSummary.skipped_apps.length} 个 App／分支）</summary>
          <ul>{uploadSummary.skipped_apps.map(item => <li key={JSON.stringify([item.app_id, item.git_branch])}>
            {item.app_id || "未填写 app_id"} · {item.git_branch || "未填写 git_branch"}：{item.rows} 行
          </li>)}</ul>
        </details>}
      </div>}
      {uploadError && <p className="lerr" role="alert">{uploadError}</p>}
      <form className="row mt-8" onSubmit={e => { e.preventDefault(); setSearch(input); setOffset(0); if (search === input && offset === 0) void query.refetch(); }}>
        <input className="input flex-1" value={input} onChange={e => setInput(e.target.value)} aria-label="查询 QA 结果" placeholder="App、HPC version、GPU、测试项、digest、环境…" />
        <button className="btn" type="submit" disabled={tableBusy}>查询结果</button>
        <button className="btn" type="button" disabled={tableBusy} onClick={() => void query.refetch()}>刷新结果</button>
      </form>
      {query.isFetching && <p className="muted" role="status">正在查询结果并实时生成命令…</p>}
      {query.error && <p className="lerr" role="alert">{query.error.message}</p>}
      {exportError && <p className="lerr" role="alert">{exportError}</p>}
      <p className="hint">全表关键词、逐列筛选和排序均作用于全部结果，再按每页 50 行显示；导出 CSV 包含全部筛选命中的记录。</p>
      <p className="hint">website_docker_cmd 始终通过 qa_digest → Harbor tags → commit → Gerrit app_info 实时生成，不采用上传文件中该列的命令。qa_docker_cmd 保留 QA 上传原文；鼠标移到 qa_env 可预览该条结果所属 Excel 的同名环境页，完整内容可下载原始 Excel 查看。</p>
      <p className="hint">上传时逐项校验前 12 列：app_id、git_branch、hpc_version、app_version、arch、OS、GPU、py_label、torch_label、test_name、perf_golden、perf_unit，并核对 qa_docker_cmd 的镜像 tag/digest。app_id 使用系统生成的唯一标识，不能填写官方名称。与镜像实际来源不符时，整份文件不入库，并提示行号和字段。</p>
    </div>
    {tableColumns && <>
      <FlatCommandTable columns={[...tableColumns, "Excel 提交时间", "提交人", "原始 Excel"]} rows={query.error ? [] : rows} filename="qa_test_results.csv" label="QA 测试结果" onRefresh={() => void query.refetch()} remote={{
        filters, onChange: next => { setFilters(next); setOffset(0); setExportError(""); },
        total: query.error ? 0 : total, busy: tableBusy, exporting, onExport: () => void exportResults(),
      }} renderCell={(column, row) => {
        if (column === "qa_env" && row.qa_env && row.report_id) return <QaEnvironmentPreview reportId={Number(row.report_id)} environment={String(row.qa_env)} />;
        if (column === "原始 Excel") return <a href={`/api/qa/test-results/${row.report_id}/download`}>{String(row.filename)}</a>;
        if (column === "Excel 提交时间") return formatServerTime(String(row.uploaded_at));
        if (column === "website_docker_cmd") return row.command_error ? <span className="lerr">{String(row.command_error)}</span> : String(row.website_docker_cmd);
        return String(row[column] ?? "");
      }} />
      <div className="panel-body row">
        <span>共 {total} 行，当前 {total ? offset + 1 : 0}–{Math.min(offset + 50, total)}</span>
        <button className="btn" disabled={tableBusy || !!query.error || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button>
        <button className="btn" disabled={tableBusy || !!query.error || offset + 50 >= total} onClick={() => setOffset(offset + 50)}>下一页</button>
      </div>
    </>}
  </div>;
}
