import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUp, ArrowDownToLine, Download, RefreshCw } from "lucide-react";
import { apiGet } from "../../api/http";

interface LogPage {
  content: string;
  start_line: number;
  end_line: number;
  total_lines: number;
  previous_before: number | null;
}

export function ReproTaskLogs({ taskId, running, initialName = "" }: { taskId: string; running: boolean; initialName?: string }) {
  const [selected, setSelected] = useState(initialName);
  const [before, setBefore] = useState<number>();
  const base = `/api/cicd-agent/repro-tasks/${encodeURIComponent(taskId)}/logs`;
  const listing = useQuery({
    queryKey: ["repro-logs", taskId], queryFn: () => apiGet<{ logs: { name: string; label: string; bytes: number }[];
      retention?: { expired_at: string } | null }>(base),
    refetchInterval: running ? 5000 : false, retry: false,
  });
  const logs = listing.data?.logs ?? [];
  const name = logs.some((log) => log.name === selected) ? selected
    : logs.filter((log) => log.name.startsWith("build-")).at(-1)?.name ?? logs[0]?.name ?? "";
  const content = useQuery({
    queryKey: ["repro-log", taskId, name, before], enabled: Boolean(name),
    queryFn: () => apiGet<LogPage>(`${base}/${encodeURIComponent(name)}${before !== undefined ? `?before=${before}` : ""}`),
    refetchInterval: running && before === undefined ? 5000 : false, retry: false,
  });
  const page = content.data;
  return <div className="cicd-repro-logs">
    <div className="cicd-repro-log-toolbar">
      <select aria-label="过程日志" value={name} disabled={!logs.length} onChange={(e) => { setSelected(e.target.value); setBefore(undefined); }}>
        {!logs.length && <option value="">暂无日志</option>}
        {logs.map((log) => <option key={log.name} value={log.name}>{log.label}</option>)}
      </select>
      <button type="button" className="btn ghost sm cicd-repro-icon" title="刷新日志" aria-label="刷新日志"
        onClick={() => { void listing.refetch(); if (name) void content.refetch(); }}><RefreshCw size={16} /></button>
      {name && <a className="btn ghost sm cicd-repro-icon" title="下载完整脱敏日志" aria-label="下载完整脱敏日志"
        href={`${base}/${encodeURIComponent(name)}/download`} target="_blank" rel="noreferrer"><Download size={16} /></a>}
    </div>
    {(listing.error || content.error) && <p role="alert" className="error-banner">{String(listing.error || content.error)}</p>}
    {(listing.isLoading || content.isLoading) && <p className="muted">正在读取日志...</p>}
    {!listing.isLoading && !listing.error && !logs.length && <p className="muted">{listing.data?.retention
      ? "过程日志已按保留策略清理，最终报告与补丁仍保留。" : "任务日志尚未生成或已清理。"}</p>}
    {page && <>
      <div className="cicd-repro-log-pagination">
        <span className="muted small">{page.total_lines ? `${page.start_line}–${page.end_line} / ${page.total_lines} 行` : "空日志"} · 已脱敏{running && before === undefined ? " · 更新中" : ""}</span>
        <button type="button" className="btn ghost sm" disabled={page.previous_before === null || content.isFetching}
          onClick={() => setBefore(page.previous_before ?? undefined)}><ArrowUp size={14} />更早日志</button>
        <button type="button" className="btn ghost sm" disabled={before === undefined}
          onClick={() => setBefore(undefined)}><ArrowDownToLine size={14} />最新日志</button>
      </div>
      <pre className="cicd-repro-log-content" aria-label="日志内容">{page.content || "暂无输出"}</pre>
    </>}
  </div>;
}
