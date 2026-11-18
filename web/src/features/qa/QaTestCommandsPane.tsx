import { useEffect, useRef, useState } from "react";
import { apiGet } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { FlatCommandTable, type CommandRow } from "../../components/FlatCommandTable";
import { QaChipLegend } from "./QaChipLegend";

interface TestCommandsResponse {
  hpc_version: string;
  hpc_versions: string[];
  columns: string[];
  rows: CommandRow[];
  errors: { image: string; error: string }[];
}

function groupCommandGpus(rows: CommandRow[]): CommandRow[] {
  const groups = new Map<string, { row: CommandRow; gpus: Set<string> }>();
  for (const row of rows) {
    // Include hidden build labels so distinct Python/PyTorch builds stay separate.
    const key = JSON.stringify(Object.keys(row).filter(column => column !== "GPU").sort().map(column => [column, row[column]]));
    let group = groups.get(key);
    if (!group) {
      group = { row, gpus: new Set() };
      groups.set(key, group);
    }
    if (row.GPU) group.gpus.add(String(row.GPU));
  }
  return [...groups.values()].map(({ row, gpus }) => ({ ...row, GPU: [...gpus].join(", ") }));
}

export function QaTestCommandsPane({ releaseId }: { releaseId: string }) {
  const { user } = useAuth();
  const [result, setResult] = useState<TestCommandsResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef({ value: 0 }).current;
  useEffect(() => {
    request.value++;
    setResult(null); setError(""); setBusy(false);
    return () => { request.value++; };
  }, [releaseId, user?.username, user?.role, request]);

  async function query() {
    if (busy || !releaseId) return;
    const id = ++request.value;
    setBusy(true); setError(""); setResult(null);
    try {
      const data = await apiGet<TestCommandsResponse>(`/api/qa/test-commands?${new URLSearchParams({
        release_id: releaseId, hpc_version: "latest",
      })}`);
      if (id === request.value) setResult(data);
    } catch (e) {
      if (id === request.value) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === request.value) setBusy(false);
    }
  }

  return <div className="panel qa-test-commands">
    <div className="panel-body">
      <h2>测试命令</h2>
      <form className="row qa-test-command-query" onSubmit={e => { e.preventDefault(); void query(); }}>
        <button className="btn primary" type="submit" disabled={busy || !releaseId}>
          {busy ? "查询中…" : "查询测试命令"}
        </button>
        {result && <span className="qa-test-command-version" role="status">{result.hpc_version === "latest"
          ? result.hpc_versions.length
            ? `已按 App／构建分别选择最新版本；实际 hpc_version：${result.hpc_versions.join("、")}`
            : result.rows.length ? "未找到可用镜像，已显示占位符命令" : "当前发布周期没有测试命令"
          : `实际 hpc_version：${result.hpc_version}`}</span>}
      </form>
      <p className="hint">仅查询 MACA 镜像。latest 为每个 App／构建分别选择当前发布周期内最新的 HPC 版本，按版本数字比较。无可用镜像时保留完整测试命令，镜像 tag 中的 HPC 版本使用 {"<hpc_version>"} 占位符。</p>
      <p className="hint">QA 下载 CSV 后另存为 Excel，填写 QA 列并添加与 qa_env 同名的环境页，再从 QA log 上传。</p>
      <QaChipLegend className="hint" />
      {!releaseId && <p className="muted">请选择发布周期。</p>}
      {busy && <p role="status">正在读取 Harbor 镜像及 Gerrit 历史配置…</p>}
      {error && <p className="lerr" role="alert">{error}</p>}
      {!!result?.errors.length && <div role="alert" className="lerr">以下镜像无法生成命令：{result.errors.map(item =>
        <p className="small break" key={item.image}>{item.image}：{item.error}</p>)}</div>}
    </div>
    {result && <FlatCommandTable columns={result.columns} rows={result.rows}
      hiddenColumns={["py_label", "torch_label"]} groupRows={groupCommandGpus}
      filename={`test_commands_${result.hpc_version}.csv`} label="测试命令" showHeading={false} showSearch={false} onRefresh={() => void query()} />}
  </div>;
}
