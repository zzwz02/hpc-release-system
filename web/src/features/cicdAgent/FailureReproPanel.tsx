import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, RefreshCw, ShieldCheck, CircleAlert } from "lucide-react";
import { apiGet, apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { ReproTaskLogs } from "./ReproTaskLogs";

interface BuildFact {
  attempt: number;
  target: string;
  exit_code: number | null;
  log: string;
  label?: string;
  method?: string;
  no_cache?: boolean;
  pull?: boolean;
}

export interface ReproTask {
  id: string;
  failure_id: number;
  status: string;
  reason: string;
  requested_by: string;
  node_name: string | null;
  created_at: string;
  error: string | null;
  stale: boolean;
  cancel_requested: boolean;
  resolved_by: string | null;
  history: { status: string; at: string; detail: string }[];
  report: null | {
    outcome: string;
    source_commit: string;
    arch: string;
    verified: boolean;
    base_digests_match: boolean;
    environment_error: string;
    suggested_owner_account: string;
    builds: { attempt: number; results: { target: string; exit_code: number | null; log: string }[] }[];
    patches: { patch: string; verified: boolean }[];
    log_review?: {
      decision: "log_sufficient" | "reproduce" | "insufficient_evidence";
      reason: string;
      matched_rule_id: string;
      policy_version: string;
      evidence: { start_line: number; end_line: number; quote: string }[];
    };
    build_skipped_reason?: string;
    verification?: {
      execution_mode?: "historical_logs" | "build";
      build_skipped_reason?: string;
      summary: string;
      passed: boolean;
      original_failure_reproduced: boolean;
      scope: string;
      targets: string[];
      builds: BuildFact[];
      installed_packages: { name: string; version: string }[];
    };
    diagnosis: null | {
      summary: string;
      suggested_role: string;
      responsibility_reason: string;
      action_suggestion: string;
      original_evidence: string;
      reproduction_evidence: string;
      limitations: string[];
    };
  };
}

const LABELS: Record<string, string> = {
  queued: "排队中", analyzing_history: "历史日志分析中", preparing: "准备源码与证据", selecting_node: "选择复现节点",
  reproducing: "复现构建中", diagnosing: "Codex 诊断中", verifying: "验证补丁中",
  completed: "诊断完成，待 RM 审定", blocked: "环境阻塞", failed: "执行失败",
  cancelled: "已取消", interrupted: "执行中断",
};
const OUTCOMES: Record<string, string> = {
  log_only: "仅日志分析", environment_blocked: "复现环境阻塞", not_reproduced: "未复现原错误",
  reproduced: "已复现原错误", different_failure: "出现不同错误，尚未复现", fix_verified: "补丁构建验证通过",
  historical_log_confirmed: "历史日志诊断完成 · 未执行构建", evidence_insufficient: "历史证据不足，待人工复核",
};
const LOG_DECISIONS: Record<string, string> = {
  log_sufficient: "历史日志证据充分", reproduce: "需要复现构建", insufficient_evidence: "历史证据不足",
};
const FINISHED = new Set(["completed", "blocked", "failed", "cancelled", "interrupted"]);

export function FailureReproPanel({ recordId, canResolve, onUseReport }: {
  recordId: number;
  canResolve: boolean;
  onUseReport: (task: ReproTask) => void;
}) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [logsOpen, setLogsOpen] = useState(false);
  const [logName, setLogName] = useState("");
  const logsRef = useRef<HTMLDetailsElement>(null);
  const query = useQuery({
    queryKey: ["cicd-agent", "repro-tasks", recordId],
    queryFn: () => apiGet<{ tasks: ReproTask[]; enabled: boolean; worker_online?: boolean;
      evidence_archive?: { status: string; captured_at?: string; expired_at?: string } }>(`/api/cicd-agent/failures/${recordId}/repro-tasks`),
    refetchOnMount: "always",
    refetchInterval: (q) => q.state.data?.tasks.some((t) => !FINISHED.has(t.status))
      || ["pending", "retry", "capturing"].includes(q.state.data?.evidence_archive?.status ?? "") ? 3000 : false,
    retry: false,
  });
  const tasks = query.data?.tasks ?? [];
  const latest = tasks[0];
  const reviewVersion = latest ? `${latest.id}:${latest.status}:${latest.resolved_by}:${latest.stale}` : "";
  useEffect(() => {
    if (!reviewVersion) return;
    void queryClient.invalidateQueries({ queryKey: ["cicd-agent", "failures"] });
    void queryClient.invalidateQueries({ queryKey: ["cicd-agent", "summary"] });
  }, [queryClient, reviewVersion]);
  const active = tasks.find((task) => !FINISHED.has(task.status));
  const task = tasks.find((item) => item.id === selected) ?? tasks[0];
  const report = task?.report;
  const diagnosis = report?.diagnosis;
  const verification = report?.verification;
  const logReview = report?.log_review;
  const historicalOnly = verification?.execution_mode === "historical_logs"
    || ["historical_log_confirmed", "evidence_insufficient"].includes(report?.outcome ?? "");
  const logOnly = historicalOnly || report?.outcome === "log_only";
  const verified = report?.verified && !logOnly;
  const historyDisplay = logOnly || task?.status === "analyzing_history";
  const outcome = historicalOnly && report?.outcome === "fix_verified"
    ? logReview?.decision === "insufficient_evidence" ? "evidence_insufficient" : "historical_log_confirmed"
    : report?.outcome;
  const skippedReason = report?.build_skipped_reason?.trim() || verification?.build_skipped_reason?.trim()
    || "本次仅分析历史日志";
  const compactSkippedReason = skippedReason.length > 80 || skippedReason === diagnosis?.summary
    || skippedReason === diagnosis?.responsibility_reason
    ? outcome === "historical_log_confirmed" ? "历史日志证据充分"
      : outcome === "evidence_insufficient" ? "历史证据不足，需人工复核" : "本次仅分析历史日志"
    : skippedReason;
  const builds: BuildFact[] = historicalOnly ? []
    : verification?.builds ?? report?.builds.flatMap((b) => b.results.map((r) => ({ ...r, attempt: b.attempt }))) ?? [];
  const scope = verification?.scope || (historicalOnly ? "仅依据历史日志认定，未执行构建或基础镜像核对。"
    : "仅验证已列出的构建 target，不代表完整功能测试通过。");
  const canUseReport = canResolve && task?.status === "completed" && !task.stale && !task.resolved_by
    && ["Code Owner", "DevOps"].includes(diagnosis?.suggested_role ?? "")
    && report?.outcome !== "evidence_insufficient" && logReview?.decision !== "insufficient_evidence";

  function showLog(name: string) {
    setLogName(name);
    setLogsOpen(true);
    requestAnimationFrame(() => logsRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const created = await apiPost<ReproTask>(`/api/cicd-agent/failures/${recordId}/repro-tasks`, { reason });
      setSelected(created.id);
      setReason("");
      await query.refetch();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function cancel() {
    if (!active) return;
    setBusy(true);
    setError("");
    try {
      await apiPost(`/api/cicd-agent/repro-tasks/${active.id}/cancel`, {});
      await query.refetch();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return <section className="cicd-repro-panel" aria-label="复议诊断">
    <div className="section-head-row">
      <h4>复议诊断</h4>
      <button type="button" className="btn ghost sm cicd-repro-icon" aria-label="刷新状态" title="刷新状态" onClick={() => void query.refetch()}><RefreshCw size={16} /></button>
    </div>
    {(error || query.error) && <div role="alert" className="error-banner">{error || String(query.error)}</div>}
    {query.isLoading && <p className="muted">正在加载复议记录...</p>}
    {!query.isLoading && !query.data?.enabled && <p className="muted">复议服务未启用</p>}
    {query.data?.enabled && query.data.worker_online === false && <p className="cicd-repro-warning">复议 Worker 未在线，任务将保留在队列中。</p>}
    {query.data?.evidence_archive?.status === "complete" && <p className="muted small">Jenkins 复现证据已归档</p>}
    {["pending", "capturing", "retry"].includes(query.data?.evidence_archive?.status ?? "") && <p className="muted small">Jenkins 复现证据正在归档</p>}
    {query.data?.evidence_archive?.status === "failed" && <p className="cicd-repro-warning">证据归档失败，尚未具备脱离 Jenkins 日志的复现条件。</p>}
    {query.data?.evidence_archive?.status === "expired" && <p className="cicd-repro-warning">Jenkins 原始日志已按保留策略清理，历史报告仍保留；再次复现需管理员重新获取证据。</p>}
    {active ? <div className="cicd-repro-active" role="status">
      <b>{active.cancel_requested ? "正在取消" : LABELS[active.status] || active.status}</b>
      <span>{active.status === "analyzing_history" ? "正在分析 Jenkins 原始日志" : active.node_name || "等待节点分配"}</span>
      {(canResolve || user?.username === active.requested_by) && <button type="button" className="btn sm"
        disabled={busy || active.cancel_requested} onClick={() => void cancel()}>取消复议</button>}
    </div> : <details className="cicd-repro-new" open={tasks.length === 0 ? true : undefined}>
      <summary>{tasks.length ? "发起新的复议" : "发起复议"}</summary>
      <form className="cicd-repro-form" onSubmit={(e) => void submit(e)}>
      <label htmlFor="repro-reason">复议原因</label>
      <textarea id="repro-reason" rows={2} value={reason} maxLength={4000} required
        onChange={(e) => setReason(e.target.value)} />
      <button className="btn sm cicd-agent-detail-trigger" disabled={busy || !query.data?.enabled || !reason.trim()}>
        {busy ? "提交中..." : tasks.length ? "重新发起复议" : "提交复议"}
      </button>
    </form></details>}
    {tasks.length > 0 && <label className="cicd-repro-history">复议记录
      <select value={task?.id} onChange={(e) => { setSelected(e.target.value); setLogName(""); setLogsOpen(false); }}>
        {tasks.map((t) => <option key={t.id} value={t.id}>{t.created_at.replace("T", " ")} / {t.requested_by} / {t.resolved_by ? "RM 已审定" : t.stale ? "历史报告" : LABELS[t.status] || t.status}</option>)}
      </select>
    </label>}
    {task && <div className="cicd-repro-result">
      <div className="section-head-row">
        <b className={`cicd-repro-outcome ${verified ? "passed" : ""}`}
          style={historyDisplay ? { color: "var(--ink-2)" } : undefined}>
          {historyDisplay ? <FileText size={18} /> : verified ? <ShieldCheck size={18} /> : <CircleAlert size={18} />}
          {outcome ? OUTCOMES[outcome] || outcome : LABELS[task.status]}</b>
        {canUseReport &&
          <button type="button" className="btn sm cicd-agent-resolution-trigger" onClick={() => onUseReport(task)}>带入责任处理</button>}
      </div>
      <p className="muted small">发起人：{task.requested_by} · {task.reason}</p>
      {task.error && <p className="error-banner">{task.error}</p>}
      {task.stale && !task.resolved_by && <p className="cicd-repro-warning">责任认定已变化，此报告不能覆盖新的认定。</p>}
      {task.resolved_by && <p className="muted">已由 {task.resolved_by} 处理</p>}
      {report?.environment_error && <p className="cicd-repro-warning">{report.environment_error}</p>}
      {diagnosis && <>
        <p>{diagnosis.summary}</p>
        <dl className="cicd-repro-facts">
          <dt>Agent 建议</dt><dd><strong>{diagnosis.suggested_role} / {report?.suggested_owner_account || "待人工判断"}</strong>
            <span className="muted small"> · {task.resolved_by ? "已审定" : "待 RM 审定"}</span></dd>
          <dt>认定依据</dt><dd>{diagnosis.responsibility_reason}</dd>
          <dt>处理建议</dt><dd>{diagnosis.action_suggestion}</dd>
        </dl>
      </>}
      {report && <>
        <p className="cicd-repro-boundary">{historicalOnly ? `未执行构建：${compactSkippedReason}` : scope}</p>
        <details><summary>{historicalOnly ? "历史日志与认定证据" : "验证过程与认定证据"}</summary>
          {historicalOnly ? <p>{scope}</p> : verification && <p>{verification.summary}</p>}
          <dl className="cicd-repro-facts">
            {historicalOnly && compactSkippedReason !== skippedReason && <>
              <dt>跳过构建原因</dt><dd>{skippedReason}</dd>
            </>}
            <dt>执行节点</dt><dd>{historicalOnly ? "未分配（已跳过构建）" : task.node_name || "未分配"}</dd>
            <dt>架构</dt><dd>{report.arch || "未知"}</dd>
            <dt>源码 revision</dt><dd>{historicalOnly ? <>
              未检查、未下载（已跳过源码准备）
              {report.source_commit && <div>历史记录：<code>{report.source_commit}</code></div>}
            </> : <code>{report.source_commit || "未知"}</code>}</dd>
          </dl>
          <table className="cicd-repro-process-table">
            <thead><tr>{["步骤", "实际结果", "日志"].map((label) =>
              <th key={label} scope="col" style={{ position: "static" }}>{label}</th>)}</tr></thead>
            <tbody>
              {builds.filter((b) => b.attempt === 0).map((b, i) => <tr key={`base-${i}`}>
                <td>未修改副本构建</td><td>{b.target} · 退出码 {b.exit_code ?? "未返回"}
                  {verification?.original_failure_reproduced && <div>原始错误已重现</div>}</td>
                <td><button className="btn ghost sm cicd-repro-icon" title="查看未修改副本日志" aria-label="查看未修改副本日志" onClick={() => showLog(b.log)}><FileText size={16} /></button></td>
              </tr>)}
              {!historicalOnly && Boolean(report.patches.length) && <tr><td>应用候选补丁</td><td>已应用到独立 candidate 副本，未改原始源码</td><td /></tr>}
              {builds.filter((b) => b.attempt > 0).map((b, i) => <tr key={`fix-${i}`}>
                <td>补丁副本重新构建</td><td>{b.target} · 退出码 {b.exit_code ?? "未返回"}
                  {b.method && <div><code>{b.method}{b.no_cache ? " --no-cache" : ""}{b.pull ? " --pull" : ""}</code></div>}</td>
                <td><button className="btn ghost sm cicd-repro-icon" title="查看补丁构建日志" aria-label="查看补丁构建日志" onClick={() => showLog(b.log)}><FileText size={16} /></button></td>
              </tr>)}
              {!builds.length && <tr><td>构建验证</td><td>{historicalOnly ? "未执行" : "暂无已完成的构建记录"}</td><td /></tr>}
              <tr><td>基础镜像核对</td><td>{historicalOnly ? "未执行" : report.base_digests_match ? "未修改副本与历史 digest 一致" : "尚未验证与历史一致"}
                {!historicalOnly && verification?.passed && <div>补丁副本也与历史 digest 一致</div>}</td><td /></tr>
            </tbody>
          </table>
          <div className="section-head-row">
            <h5>Jenkins 原始证据</h5>
            <button type="button" className="btn ghost sm cicd-repro-icon" title="查看 Jenkins 原始日志" aria-label="查看 Jenkins 原始日志"
              onClick={() => showLog("original.log")}><FileText size={16} /></button>
          </div>
          {!logReview?.evidence.length && <pre>{diagnosis?.original_evidence || "暂无"}</pre>}
          {logReview && <>
            <dl className="cicd-repro-facts">
              <dt>日志判定</dt><dd>{LOG_DECISIONS[logReview.decision]}</dd>
              <dt>判定依据</dt><dd>{logReview.reason}</dd>
              <dt>命中规则</dt><dd>{logReview.matched_rule_id || "无"}</dd>
              <dt>策略版本</dt><dd>{logReview.policy_version || "未知"}</dd>
            </dl>
            {logReview.evidence.length > 0 && <><h5>引用行</h5><ol>{logReview.evidence.map((evidence, i) => <li key={i}>
              <span className="muted small">原始日志第 {evidence.start_line}-{evidence.end_line} 行</span>
              <pre>{evidence.quote}</pre>
            </li>)}</ol></>}
          </>}
          <h5>复现证据</h5><pre>{historicalOnly ? "未执行复现构建" : diagnosis?.reproduction_evidence || "暂无"}</pre>
          {Boolean(diagnosis?.limitations.length) && <><h5>验证边界</h5><ul>{diagnosis?.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></>}
          {!historicalOnly && Boolean(verification?.installed_packages.length) && <details><summary>构建日志中的已安装依赖</summary>
            <div className="cicd-repro-packages">{verification?.installed_packages.map((pkg) => <div key={pkg.name}><code>{pkg.name}</code><span>{pkg.version}</span></div>)}</div>
          </details>}
        </details>
      </>}
      {!historicalOnly && report?.patches.map((patch, i) => <details key={i}><summary>修复补丁 · {patch.verified ? "验证通过" : "未验证通过"}</summary><pre>{patch.patch}</pre></details>)}
      <details ref={logsRef} open={logsOpen} onToggle={(e) => setLogsOpen(e.currentTarget.open)}>
        <summary>过程日志</summary>
        {logsOpen && <ReproTaskLogs key={`${task.id}:${logName}`} taskId={task.id} initialName={logName} running={!FINISHED.has(task.status)} />}
      </details>
      <details><summary>执行记录</summary><ol>{task.history.map((item, i) => <li key={i}>
        {item.at.replace("T", " ")} · {LABELS[item.status] || item.status}{item.detail && ` · ${item.detail}`}
      </li>)}</ol></details>
    </div>}
  </section>;
}
