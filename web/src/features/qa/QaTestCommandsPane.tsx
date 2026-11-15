import { useEffect, useRef, useState } from "react";
import { apiGet } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { FlatCommandTable, type CommandRow } from "../../components/FlatCommandTable";

interface TestCommandsResponse {
  hpc_version: string;
  hpc_versions: string[];
  columns: string[];
  rows: CommandRow[];
  errors: { image: string; error: string }[];
}

export function QaTestCommandsPane({ releaseId }: { releaseId: string }) {
  const { user } = useAuth();
  const [version, setVersion] = useState("latest");
  const [result, setResult] = useState<TestCommandsResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef({ value: 0 }).current;
  useEffect(() => {
    request.value++;
    setVersion("latest"); setResult(null); setError(""); setBusy(false);
    return () => { request.value++; };
  }, [releaseId, user?.username, user?.role, request]);

  async function query() {
    if (busy || !releaseId || !version.trim()) return;
    const id = ++request.value;
    setBusy(true); setError(""); setResult(null);
    try {
      const data = await apiGet<TestCommandsResponse>(`/api/qa/test-commands?${new URLSearchParams({
        release_id: releaseId, hpc_version: version.trim(),
      })}`);
      if (id === request.value) setResult(data);
    } catch (e) {
      if (id === request.value) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === request.value) setBusy(false);
    }
  }

  return <div className="panel">
    <div className="panel-body">
      <h2>测试命令</h2>
      <form className="row" onSubmit={e => { e.preventDefault(); void query(); }}>
        <label>hpc_version <input className="input" aria-label="hpc_version" value={version} disabled={busy}
          placeholder="3.10.0.7 或 latest" onChange={e => { setVersion(e.target.value); setResult(null); setError(""); }} /></label>
        <button className="btn primary" type="submit" disabled={busy || !releaseId || !version.trim()}>
          {busy ? "查询中…" : "查询测试命令"}
        </button>
      </form>
      <p className="hint">仅查询 MACA 镜像（release / dbg）。latest 为每个 App／构建分别选择当前发布周期内最新的 HPC 版本，按版本数字比较；各行 hpc_version 显示该镜像的实际版本。按 OS、CPU 架构、Python/PyTorch、release/dbg、GPU 和测试项展开；website_docker_cmd 使用实际镜像:tag@digest。构建无可用镜像时为 no_image；latest 查询中该行 hpc_version 留空。</p>
      <p className="hint">所有 qa_* 列留空。下载 CSV 后另存为 Excel，填写 QA 列并添加与 qa_env 同名的环境页，再从 QA report 上传。</p>
      {!releaseId && <p className="muted">请选择发布周期。</p>}
      {busy && <p role="status">正在读取 Harbor 镜像及 Gerrit 历史配置…</p>}
      {error && <p className="lerr" role="alert">{error}</p>}
      {result && <p role="status">{result.hpc_version === "latest"
        ? `已按 App／构建分别选择最新版本；实际 hpc_version：${result.hpc_versions.join("、")}`
        : `实际 hpc_version：${result.hpc_version}`}</p>}
      {!!result?.errors.length && <div role="alert" className="lerr">以下镜像无法生成命令：{result.errors.map(item =>
        <p className="small break" key={item.image}>{item.image}：{item.error}</p>)}</div>}
    </div>
    {result && <FlatCommandTable columns={result.columns} rows={result.rows}
      filename={`test_commands_${result.hpc_version}.csv`} label="测试命令" onRefresh={() => void query()} />}
  </div>;
}
