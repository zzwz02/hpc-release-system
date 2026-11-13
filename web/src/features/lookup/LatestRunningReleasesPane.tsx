import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet } from "../../api/http";
import { RefreshBar } from "../../components/RefreshBar";
import { downloadCsv } from "../../lib/csv";

interface ReleaseRow {
  app_id: string;
  app_name: string;
  hpc_version: string;
  app_version: string;
  os?: string;
  arch?: string;
  chips?: string[];
  image_target?: string;
  python_label?: string;
  pytorch_label?: string;
  delivery_reference?: string;
  digest?: string;
  path?: string;
  command?: string;
  note: string;
}

const columns = ["App", "HPC version", "App version", "OS", "CPU 架构", "支持芯片", "镜像类型", "Python label", "PyTorch label", "完整镜像名", "Digest", "测试项", "测试命令", "说明"];
const archName = (arch?: string) => arch === "amd64" ? "x86" : arch === "arm64" ? "ARM" : arch || "";
function cells(row: ReleaseRow): string[] {
  return [row.app_name, row.hpc_version, row.app_version, row.os || "", archName(row.arch), (row.chips || []).join(", "), row.image_target || "",
    row.python_label || "", row.pytorch_label || "", row.delivery_reference || "", row.digest || "",
    row.path || "", row.command || "", row.note];
}

export function LatestRunningReleasesPane() {
  const { user } = useAuth();
  const [filter, setFilter] = React.useState("");
  const result = useQuery({
    queryKey: ["lookup", user?.username, user?.role, "latest-running"],
    queryFn: () => apiGet<{ app_count: number; rows: ReleaseRow[] }>("/api/lookup/latest-running"),
    staleTime: Infinity,
    refetchOnMount: "always",
  });
  const needle = filter.trim().toLocaleLowerCase();
  const rows = (result.data?.rows ?? []).filter((row) => !needle || [row.app_id, ...cells(row)].join(" ").toLocaleLowerCase().includes(needle));
  return <div className="panel" data-testid="latest-running-releases-pane">
    <div className="panel-head">
      <h2>兼容性测试基线</h2>
      {result.data && <span className="count">{result.data.app_count} 个 App</span>}
      <span className="spacer" />
      <RefreshBar dataUpdatedAt={result.dataUpdatedAt} isFetching={result.isFetching} onRefresh={() => void result.refetch()} />
    </div>
    <div className="panel-body">
      <p className="hint">每个 CICD 运行中的 App 取最后一次成功发布；后续发布失败、未发布或已撤销时保留此前成功版本。所有镜像与命令取自该次发布快照。</p>
      <div className="row">
        <input className="input flex-1" value={filter} onChange={(event) => setFilter(event.target.value)}
          aria-label="筛选最新成功发布" placeholder="筛选 App、HPC 版本、OS、架构、芯片、digest 或命令…" />
        <button type="button" className="btn sm" disabled={!result.data || result.isFetching || !!result.error}
          onClick={() => downloadCsv("latest-running-releases.csv", columns, rows.map(cells))}>导出 CSV</button>
      </div>
      {result.error && <p className="lerr" role="alert">{result.error.message}</p>}
      {result.isFetching && <p className="muted" role="status">加载中…</p>}
    </div>
    {!result.error && !result.isFetching && result.data && <div className="table" data-testid="latest-running-releases-table">
      <table className="latest-running-releases-table">
        <thead><tr><th>App</th><th>HPC version</th><th>App version</th><th>OS</th><th>CPU 架构</th><th>支持芯片</th><th>镜像配置</th><th>完整镜像名 / Digest</th><th>测试项</th><th>测试命令</th><th>说明</th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={11} className="muted">{result.data.app_count ? "没有匹配的记录。" : "暂无 CICD 运行中的 App。"}</td></tr>}
          {rows.map((row, index) => <tr key={`${row.app_id}-${index}`} data-testid="latest-running-release-row">
            <td>{row.app_name}<div className="muted small">{row.app_id}</div></td>
            <td>{row.hpc_version || "—"}</td><td>{row.app_version || "—"}</td><td>{row.os || "—"}</td><td>{archName(row.arch) || "—"}</td>
            <td>{row.chips?.join(", ") || "—"}</td>
            <td>{[row.image_target, row.python_label, row.pytorch_label].filter(Boolean).join(" · ") || "—"}</td>
            <td className="mono small break">{row.delivery_reference || row.digest || "—"}</td>
            <td className="mono small break">{row.path || "—"}</td><td className="mono small cell-wrap">{row.command || "—"}</td>
            <td className="small break">{row.note || "—"}</td>
          </tr>)}
        </tbody>
      </table>
    </div>}
  </div>;
}
