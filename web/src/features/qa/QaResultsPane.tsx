import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { can } from "../../lib/accessControl";
import { FlatCommandTable, type CommandRow } from "../../components/FlatCommandTable";
import { formatServerTime } from "../../lib/time";

interface ResultResponse { total: number; rows: CommandRow[] }
const columns = ["app_name", "git_branch", "app_version", "hpc_version", "arch", "OS", "GPU", "commit_id", "test_name", "Python label", "PyTorch label", "perf_golden", "perf_unit", "test_result", "test_digest", "test_env", "自动生成的命令", "QA Excel 测试命令", "Excel 提交时间", "提交人", "原始 Excel"];

async function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取 Excel 文件"));
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.readAsDataURL(file);
  });
}

export function QaResultsPane() {
  const { user } = useAuth();
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [uploadError, setUploadError] = useState("");
  const generation = useRef({ value: 0 }).current;
  const query = useQuery({
    queryKey: ["qa-test-results", user?.username, user?.role, search, offset],
    queryFn: () => apiGet<ResultResponse>(`/api/qa/test-results?${new URLSearchParams({ search, offset: String(offset), limit: "50" })}`),
    refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  useEffect(() => {
    generation.value++;
    setInput(""); setSearch(""); setOffset(0); setMessage(""); setUploadError(""); setBusy(false);
    return () => { generation.value++; };
  }, [user?.username, user?.role, generation]);

  async function upload(file: File) {
    const id = ++generation.value;
    setBusy(true); setMessage(""); setUploadError("");
    try {
      if (file.size > 20 * 1024 * 1024) throw new Error("Excel 文件不能超过 20 MB");
      const content_base64 = await fileBase64(file);
      if (id !== generation.value) return;
      const data = await apiPost<{ rows: number; replaced: number; duplicate_rows: number }>("/api/qa/test-results/upload", { filename: file.name, content_base64 });
      if (id !== generation.value) return;
      setMessage(`已保存 ${data.rows} 行，其中覆盖 ${data.replaced} 行。${data.duplicate_rows ? `合并 ${data.duplicate_rows} 个重复测试行。` : ""}`);
      setOffset(0);
      void query.refetch();
    } catch (e) {
      if (id === generation.value) setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === generation.value) setBusy(false);
    }
  }

  const rows = (query.data?.rows ?? []).map(row => ({
    ...row, "自动生成的命令": row.auto_command || row.command_error,
    "QA Excel 测试命令": row.docker_cmd, "Excel 提交时间": row.uploaded_at,
    "提交人": row.uploaded_by, "原始 Excel": row.filename,
  }));
  return <div className="panel" data-testid="qa-results-pane">
    <div className="panel-body">
      <h2>QA report</h2>
      <p className="hint">上传 Excel 的第一个页签作为测试结果，后续环境页原样保留。相同 app_name、git_branch、app_version、arch、OS、GPU、test_name 的结果由本次上传覆盖；同一文件的重复行以最后一行为准。未填写结果、digest 和环境的行跳过。保存前实时校验 digest 与测试行，hpc_version、commit_id 由系统解析。</p>
      {can(user?.role, "qa.edit") && <label>上传测试结果 Excel <input type="file" accept=".xlsx" aria-label="上传测试结果 Excel" disabled={busy} onChange={e => {
        const file = e.target.files?.[0]; e.target.value = ""; if (file) void upload(file);
      }} /></label>}
      {busy && <p role="status">正在校验镜像和测试行，全部通过后保存…</p>}
      {message && <p role="status">{message}</p>}
      {uploadError && <p className="lerr" role="alert">{uploadError}</p>}
      <form className="row mt-8" onSubmit={e => { e.preventDefault(); setSearch(input); setOffset(0); if (search === input && offset === 0) void query.refetch(); }}>
        <input className="input flex-1" value={input} onChange={e => setInput(e.target.value)} aria-label="查询 QA 结果" placeholder="App、HPC version、GPU、测试项、digest、环境…" />
        <button className="btn" type="submit" disabled={query.isFetching}>查询结果</button>
        <button className="btn" type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新结果</button>
      </form>
      {query.isFetching && <p className="muted" role="status">正在查询结果并实时生成命令…</p>}
      {query.error && <p className="lerr" role="alert">{query.error.message}</p>}
      <p className="hint">自动命令始终通过 digest → Harbor tags → commit → Gerrit app_info 生成。Excel 命令保留上传内容；环境页可从原始 Excel 下载查看。</p>
    </div>
    {!query.isFetching && !query.error && query.data && <>
      <FlatCommandTable columns={columns} rows={rows} filename="qa_test_results.csv" label="QA 测试结果" onRefresh={() => void query.refetch()} renderCell={(column, row) => {
        if (column === "原始 Excel") return <a href={`/api/qa/test-results/${row.report_id}/download`}>{String(row.filename)}</a>;
        if (column === "Excel 提交时间") return formatServerTime(String(row.uploaded_at));
        if (column === "自动生成的命令") return row.command_error ? <span className="lerr">{String(row.command_error)}</span> : String(row.auto_command);
        if (column === "QA Excel 测试命令") return String(row.docker_cmd);
        return String(row[column] ?? "");
      }} />
      <div className="panel-body row">
        <span>共 {query.data.total} 行，当前 {query.data.total ? offset + 1 : 0}–{Math.min(offset + 50, query.data.total)}</span>
        <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button>
        <button className="btn" disabled={offset + 50 >= query.data.total} onClick={() => setOffset(offset + 50)}>下一页</button>
      </div>
    </>}
  </div>;
}
