import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../api/AuthContext";
import { DateInput } from "../../components/DateInput";
import { Markdown } from "../../components/Markdown";
import { can } from "../../lib/accessControl";
import { confirmDialog } from "../../lib/confirm";
import { formatLlmProgress } from "../../lib/llmProgress";
import { toast } from "../../lib/toast";
import { useUiStore } from "../../store/uiStore";
import {
  autoDefaults, autoJobs, autoReport, autoReports, cancelAutoJob, downloadAutoReport,
  generateReport, saveAutoReport, watchAutoJob,
  type AutoJob, type AutoOptions, type AutoReport,
} from "./autoWeeklyReportApi";

const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export function AutoWeeklyReportPane() {
  const { user } = useAuth();
  const viewer = `${user?.username}:${user?.role}`;
  const canRun = can(user?.role, "weekly-report.run");
  const dirty = useUiStore(state => state.autoWeeklyReportDirty);
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [options, setOptions] = useState<AutoOptions | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<AutoJob | null>(null);
  const [retry, setRetry] = useState(0);
  const alive = useRef(true);
  const picked = useRef(new Set<string>());
  const defaults = useQuery({ queryKey: ["auto-weekly", "defaults", viewer], queryFn: autoDefaults, refetchOnMount: "always" });
  const reports = useQuery({ queryKey: ["auto-weekly", "reports", viewer], queryFn: autoReports, refetchOnMount: "always" });
  const jobs = useQuery({ queryKey: ["auto-weekly", "jobs", viewer], queryFn: autoJobs, enabled: canRun, refetchOnMount: "always" });
  const selected = params.get("report") || reports.data?.[0]?.id || "";
  const report = useQuery({ queryKey: ["auto-weekly", "report", viewer, selected], queryFn: () => autoReport(selected), enabled: Boolean(selected), refetchOnMount: "always" });
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const next = jobs.data?.find(item => !picked.current.has(item.id));
    if (!jobId && next) { picked.current.add(next.id); setJob(next); setJobId(next.id); }
  }, [jobs.data, jobId]);
  useEffect(() => {
    if (!jobId || !canRun) return;
    const controller = new AbortController();
    let terminal = false;
    void watchAutoJob(jobId, next => {
      if (!alive.current || controller.signal.aborted) return;
      setJob(next);
      if (next.status !== "running") {
        terminal = true;
        setJobId("");
        void client.invalidateQueries({ queryKey: ["auto-weekly", "jobs", viewer] });
        if (next.status === "completed") {
          void client.invalidateQueries({ queryKey: ["auto-weekly", "reports", viewer] });
          void client.invalidateQueries({ queryKey: ["auto-weekly", "report", viewer, next.report_id] });
          if (!useUiStore.getState().autoWeeklyReportDirty) {
            setParams(current => { current.set("report", next.report_id); return current; });
          }
          toast.success("自动周报已生成");
        }
      }
    }, controller.signal).then(() => {
      if (!terminal && !controller.signal.aborted) setError("进度连接已断开，任务仍在后台运行，请重新连接进度。");
    }).catch(err => {
      if (!controller.signal.aborted && alive.current) setError(message(err));
    });
    return () => controller.abort();
  }, [jobId, canRun, retry, client, viewer, setParams]);

  async function select(id: string) {
    if (useUiStore.getState().autoWeeklyReportDirty && !(await confirmDialog({ body: "有未保存的周报修改，确认放弃？", confirmText: "放弃修改", danger: true }))) return;
    setParams(current => { current.set("report", id); return current; });
  }
  async function generate() {
    if (!options && !defaults.data) return;
    setBusy(true); setError("");
    try {
      const next = await generateReport(options ?? defaults.data!);
      if (!alive.current) return;
      picked.current.add(next.id); setJob(next); setJobId(next.id);
    } catch (err) { if (alive.current) setError(message(err)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function stop() {
    try { if (jobId) setJob(await cancelAutoJob(jobId)); }
    catch (err) { if (alive.current) setError(message(err)); }
  }
  async function refresh() {
    if (useUiStore.getState().autoWeeklyReportDirty && !(await confirmDialog({ body: "有未保存的周报修改，确认放弃并刷新？", confirmText: "放弃修改", danger: true }))) return;
    useUiStore.getState().setAutoWeeklyReportDirty(false);
    setRefreshKey(value => value + 1);
    await Promise.all([reports.refetch(), ...(selected ? [report.refetch()] : []), ...(canRun ? [jobs.refetch()] : [])]);
  }
  const [refreshKey, setRefreshKey] = useState(0);
  const form = options ?? defaults.data;
  const running = Boolean(jobId);
  const set = (key: keyof AutoOptions, value: string) => { if (form) setOptions({ ...form, [key]: value }); };
  return (
    <section className="view active weekly-report" data-testid="auto-weekly-report">
      <div className="page-toolbar"><div><h2 className="weekly-report-title">自动周报</h2>
        <p className="hint">根据 JIRA 更新生成本周要点、客户项目和按 Epic 分类的 Weekly Update，附来源与交付说明。时间均为北京时间。</p>
      </div><button className="btn" onClick={() => void refresh()}>刷新</button></div>
      {canRun && form && <div className="panel panel-body auto-weekly-form">
        <label>JIRA 组<input className="input" aria-label="JIRA 组" value={form.group} onChange={e => set("group", e.target.value)} disabled={running} /></label>
        <label>统计起点<input className="input" aria-label="统计起点" type="datetime-local" value={form.since.replace(" ", "T")} onChange={e => set("since", e.target.value)} disabled={running} /></label>
        <label>统计终点（不含）<input className="input" aria-label="统计终点" type="datetime-local" value={form.until.replace(" ", "T")} onChange={e => set("until", e.target.value)} disabled={running} /></label>
        <div><span>周报日期（周五）</span><DateInput value={form.report_date} onChange={value => set("report_date", value)} testId="auto-weekly-date" ariaLabel="周报日期" /></div>
        <button className="btn primary" disabled={busy || running || dirty} onClick={() => void generate()}>{busy ? "提交中…" : "生成周报"}</button>
        <p className="hint auto-weekly-wide">默认上周五 12:00 至本周六 00:00，周六前截止到当前时间。每周一份，重复生成成功后覆盖该周已有周报；失败或中止不保存未完成内容，其他周的历史记录保留。</p>
      </div>}
      {(error || defaults.isError || reports.isError || jobs.isError) && <p role="alert" className="weekly-report-warning">{error || message(defaults.error || reports.error || jobs.error)}</p>}
      {job && <div className="panel panel-body" role="status">
        <strong>{job.message}</strong><p className="hint">{job.user} · {job.options.group} · {job.options.report_date} · {formatLlmProgress(job.elapsed_seconds, job.llm)}</p>
        {running && <><button className="btn" disabled={job.stopping} onClick={() => void stop()}>{job.stopping ? "正在中止…" : "中止生成"}</button>
          {error && <button className="btn" onClick={() => { setError(""); setRetry(n => n + 1); }}>重新连接进度</button>}</>}
      </div>}
      <div className="weekly-report-layout">
        <aside className="panel"><div className="panel-head"><strong>自动周报记录</strong></div>
          <div className="weekly-report-weeks">{reports.data?.map(item => <button key={item.id} className={`weekly-report-week${item.id === selected ? " active" : ""}`} onClick={() => void select(item.id)}>
            <span>{item.report_date} · {item.jira_group}</span><small className="muted">{item.created_by} · {item.created_at}</small>
          </button>)}</div>
          {reports.data?.length === 0 && <p className="empty">还没有自动周报。{canRun ? "选择时间范围后生成。" : "RM 生成后会显示在这里。"}</p>}
        </aside>
        <div className="weekly-report-main">
          {report.isError ? <p role="alert">{message(report.error)}</p> : report.data ?
            <ReportView key={`${viewer}:${selected}:${refreshKey}`} report={report.data} canRun={canRun} onSaved={next => {
              client.setQueryData(["auto-weekly", "report", viewer, selected], next);
              void client.invalidateQueries({ queryKey: ["auto-weekly", "reports", viewer] });
            }} /> : <div className="panel empty">{selected ? "加载中…" : "从左侧选择自动周报。"}</div>}
        </div>
      </div>
    </section>
  );
}

function ReportView({ report, canRun, onSaved }: { report: AutoReport; canRun: boolean; onSaved: (next: AutoReport) => void }) {
  const [draft, setDraft] = useState<{ markdown: string; notes: string; revision: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const dirty = Boolean(draft && (draft.markdown !== report.markdown || draft.notes !== report.notes));
  const setDirty = useUiStore(state => state.setAutoWeeklyReportDirty);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setDirty(dirty); return () => setDirty(false); }, [dirty, setDirty]);
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);
  async function save() {
    if (!draft) return;
    setSaving(true); setError("");
    try {
      const next = await saveAutoReport(report.id, draft.markdown, draft.notes, draft.revision);
      if (!alive.current) return;
      onSaved(next); setDraft(null); toast.success("周报已保存，后续周报将参考这一版分类");
    } catch (err) { if (alive.current) setError(message(err)); }
    finally { if (alive.current) setSaving(false); }
  }
  async function discard() {
    if (dirty && !(await confirmDialog({ body: "确认放弃未保存的周报修改？", confirmText: "放弃修改", danger: true }))) return;
    setDraft(null); setError("");
  }
  return <div className="panel">
    <div className="panel-head"><strong>{report.report_date} · {report.jira_group}</strong><div className="spacer" />
      {!draft && canRun && <button className="btn sm" onClick={() => setDraft({ markdown: report.markdown, notes: report.notes, revision: report.revision })}>编辑周报</button>}
      {!draft && <button className="btn sm" onClick={() => void downloadAutoReport(report).catch(err => setError(message(err)))}>下载 Markdown</button>}
      {draft && <><button className="btn sm primary" disabled={saving || !dirty} onClick={() => void save()}>保存</button><button className="btn sm" disabled={saving} onClick={() => void discard()}>取消</button></>}
    </div>
    <div className="panel-body">
      <p className="hint">统计窗口：{report.since} ～ {report.until}（终点不含） · {report.created_by} · {report.created_at}</p>
      {report.llm && <p className="hint">{formatLlmProgress(undefined, report.llm)}</p>}
      {error && <p role="alert" className="weekly-report-warning">{error}</p>}
      {draft ? <><label>周报正文<textarea className="input auto-weekly-editor" aria-label="周报正文" value={draft.markdown} onChange={e => setDraft({ ...draft, markdown: e.target.value })} /></label>
        <label>交付说明<textarea className="input auto-weekly-editor" aria-label="交付说明" value={draft.notes} onChange={e => setDraft({ ...draft, notes: e.target.value })} /></label></> :
        <><Markdown value={report.markdown} /><details><summary>交付说明与来源</summary><Markdown value={report.notes} />
          {report.evidence.issues?.length > 0 && <><strong>来源工单</strong><ul>{report.evidence.issues.map(issue => <li key={issue.key}>
            {report.jira_browse_url && /^https?:\/\//.test(report.jira_browse_url) ? <a href={`${report.jira_browse_url}${encodeURIComponent(issue.key)}`} target="_blank" rel="noreferrer">{issue.key}</a> : issue.key}
            {" "}{issue.summary}
          </li>)}</ul></>}
        </details></>}
    </div>
  </div>;
}
