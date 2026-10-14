/**
 * 周报 — the weekly reports' JIRA update check (AI助手 sub-tab); generating
 * the reports from JIRA is meant to join it later.
 *
 * Left: analysed weeks, newest first.  Right: one week's record, summary and
 * findings with export.  RM uploads the weekly .xlsx, starts the analysis
 * and sets manual sheet ↔ account overrides; Owner only reads.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../api/AuthContext";
import { can } from "../../lib/accessControl";
import { formatDuration, formatLlmProgress } from "../../lib/llmProgress";
import { toast } from "../../lib/toast";
import { MembersDialog } from "./MembersDialog";
import { MappingTable, UploadDialog } from "./UploadDialog";
import {
  downloadExport,
  formatFileSize,
  getJob,
  getWeek,
  listWeeks,
  weeklyReportWeekKey,
  weeklyReportWeeksKey,
  type Finding,
  type WeekDetail,
  type WeekListItem,
} from "./weeklyReportApi";

const JIRA_KEY_RE = /^[A-Z][A-Z0-9]+-\d+$/;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function WeeklyReportPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const viewer = `${user?.username ?? ""}:${user?.role ?? ""}`;
  const canRun = can(user?.role, "weekly-report.run");
  const [searchParams, setSearchParams] = useSearchParams();
  const [upload, setUpload] = useState({ mounted: false, open: false });
  const [showMembers, setShowMembers] = useState(false);
  const [jobId, setJobId] = useState("");

  const weeksQuery = useQuery({ queryKey: weeklyReportWeeksKey(viewer), queryFn: listWeeks });
  const weeks = useMemo(() => weeksQuery.data ?? [], [weeksQuery.data]);
  const selectedKey = searchParams.get("week") || weeks[0]?.week_key || "";
  const selectWeek = useCallback(
    (weekKey: string) => setSearchParams(weekKey ? { week: weekKey } : {}),
    [setSearchParams],
  );

  // The analysis runs in the background; its progress is polled while it runs.
  const jobQuery = useQuery({
    queryKey: ["weekly-report", "job", jobId],
    queryFn: () => getJob(jobId),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (!query.state.data || query.state.data.status === "running" ? 1000 : false),
  });
  const job = jobId ? jobQuery.data : undefined;
  const finishedWeek = job?.status === "completed" ? job.week_key : "";
  useEffect(() => {
    if (!finishedWeek) return;
    setJobId("");
    setUpload({ mounted: false, open: false });
    void queryClient.invalidateQueries({ queryKey: ["weekly-report", "weeks"] });
    void queryClient.invalidateQueries({ queryKey: ["weekly-report", "week"] });
    selectWeek(finishedWeek);
    toast.success(`${finishedWeek} 分析完成`);
  }, [finishedWeek, queryClient, selectWeek]);

  return (
    <section className="view active weekly-report" data-testid="weekly-report-page">
      <div className="page-toolbar">
        <div>
          <h2 className="weekly-report-title">周报</h2>
          <p className="hint">
            检查每人的周报是否写了 JIRA ID、对应的 JIRA 本周有没有更新。网站对 JIRA 只读，不自动通知任何人。
          </p>
        </div>
      </div>
      <div className="weekly-report-layout">
        <aside className="panel">
          <div className="panel-head">
            <strong>周列表</strong>
            <div className="spacer" />
            {canRun && (
              <>
                <button type="button" className="btn sm" onClick={() => setShowMembers(true)}>
                  手工对应
                </button>
                <button
                  type="button"
                  className="btn sm primary"
                  onClick={() => setUpload({ mounted: true, open: true })}
                >
                  上传周报
                </button>
              </>
            )}
          </div>
          {job && job.status !== "completed" && !upload.open && (
            <button
              type="button"
              className="weekly-report-job"
              onClick={() => setUpload({ mounted: true, open: true })}
            >
              {job.status === "failed"
                ? "分析失败，点击查看"
                : job.status === "cancelled"
                  ? "分析已中止，点击查看"
                  : job.stopping
                    ? "正在中止分析…"
                    : `正在分析：第 ${job.done} 人 / 共 ${job.total} 人 · 已运行 ${formatDuration(job.elapsed_seconds ?? 0)}`}
            </button>
          )}
          {weeksQuery.isError && <p className="weekly-report-warning">{errorMessage(weeksQuery.error)}</p>}
          <WeekList weeks={weeks} selectedKey={selectedKey} onSelect={selectWeek} />
          {weeksQuery.data && weeks.length === 0 && (
            <div className="empty">
              还没有分析过的周报。{canRun ? "点“上传周报”开始。" : "RM 上传并分析后会显示在这里。"}
            </div>
          )}
        </aside>
        <div className="weekly-report-main">
          {selectedKey ? (
            <WeekView key={selectedKey} weekKey={selectedKey} viewer={viewer} />
          ) : (
            <div className="panel empty">{weeksQuery.isLoading ? "加载中..." : "从左侧选择一周。"}</div>
          )}
        </div>
      </div>
      {upload.mounted && (
        <UploadDialog
          open={upload.open}
          weeks={weeks}
          jobId={jobId}
          job={job}
          onStarted={setJobId}
          onClose={() => setUpload({ mounted: Boolean(jobId), open: false })}
        />
      )}
      {showMembers && <MembersDialog onClose={() => setShowMembers(false)} />}
    </section>
  );
}

function WeekList({
  weeks,
  selectedKey,
  onSelect,
}: {
  weeks: WeekListItem[];
  selectedKey: string;
  onSelect: (weekKey: string) => void;
}) {
  return (
    <div className="weekly-report-weeks" data-testid="weekly-report-weeks">
      {weeks.map((week) => (
        <button
          key={week.week_key}
          type="button"
          className={`weekly-report-week${week.week_key === selectedKey ? " active" : ""}`}
          aria-current={week.week_key === selectedKey}
          onClick={() => onSelect(week.week_key)}
        >
          <span>{week.label}</span>
          <small className="muted">{week.finding_count} 条问题</small>
        </button>
      ))}
    </div>
  );
}

function WeekView({ weekKey, viewer }: { weekKey: string; viewer: string }) {
  const weekQuery = useQuery({
    queryKey: weeklyReportWeekKey(weekKey, viewer),
    queryFn: () => getWeek(weekKey),
  });
  const [person, setPerson] = useState("");
  const [problem, setProblem] = useState("");

  if (weekQuery.isError) {
    return <div className="panel empty">加载失败：{errorMessage(weekQuery.error)}</div>;
  }
  if (!weekQuery.data) return <div className="panel empty">加载中...</div>;

  const { week, findings } = weekQuery.data;
  const summary = week.summary;
  const shown = findings.filter(
    (finding) => (!person || finding.person === person) && (!problem || finding.problem === problem),
  );
  const notes = summary.people.filter((entry) => entry.note);

  async function exportAs(format: "md" | "csv") {
    try {
      await downloadExport(week, format);
    } catch (error) {
      toast.error(`导出失败：${errorMessage(error)}`);
    }
  }

  return (
    <div className="weekly-report-week-view">
      <div className="panel">
        <div className="panel-head">
          <strong>{week.label}</strong>
          <span className="muted small">报告日期 {week.report_date}</span>
        </div>
        <dl className="panel-body weekly-report-facts" data-testid="weekly-report-record">
          <dt>上传</dt>
          <dd>
            {week.uploaded_by} · {week.xlsx_name} · {formatFileSize(week.xlsx_size)} · {week.uploaded_at}
          </dd>
          <dt>分析</dt>
          <dd>
            {week.analyzed_by} · {week.analyzed_at}
            {summary.elapsed_seconds != null && ` · 耗时 ${formatDuration(summary.elapsed_seconds)}`}
            {summary.llm && (
              <div className="hint" data-testid="weekly-report-llm-totals">
                {formatLlmProgress(undefined, summary.llm)}
              </div>
            )}
          </dd>
          <dt>范围</dt>
          <dd>
            {summary.people_count} 人；JIRA 更新窗口 {summary.window_start} 起（{week.active_hours} 小时）
            {summary.consistency ? "" : "；未做一致性分析"}
          </dd>
        </dl>
      </div>

      <div className="panel">
        <div className="panel-head">
          <strong>汇总</strong>
          <span className="count">{summary.total}</span>
        </div>
        <div className="panel-body weekly-report-summary" data-testid="weekly-report-summary">
          <div>
            <div className="hint">按问题类型（点击筛选）</div>
            <div className="pill-group">
              {summary.by_problem.map((entry) => (
                <button
                  key={entry.problem}
                  type="button"
                  className={`weekly-report-chip${problem === entry.problem ? " active" : ""}`}
                  aria-pressed={problem === entry.problem}
                  onClick={() => setProblem(problem === entry.problem ? "" : entry.problem)}
                >
                  {entry.problem} <b>{entry.count}</b>
                </button>
              ))}
              {summary.total === 0 && <span className="muted">本周没有发现问题</span>}
            </div>
          </div>
          {summary.by_person.length > 0 && (
            <div>
              <div className="hint">按人</div>
              <div className="pill-group">
                {summary.by_person.map((entry) => (
                  <button
                    key={entry.person}
                    type="button"
                    className={`weekly-report-chip${person === entry.person ? " active" : ""}`}
                    aria-pressed={person === entry.person}
                    onClick={() => setPerson(person === entry.person ? "" : entry.person)}
                  >
                    {entry.person} <b>{entry.count}</b>
                  </button>
                ))}
              </div>
            </div>
          )}
          {notes.length > 0 && (
            <div>
              <div className="hint">备注</div>
              <ul className="weekly-report-notes">
                {notes.map((entry) => (
                  <li key={entry.sheet}>
                    {entry.sheet}：{entry.note}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <details className="weekly-report-mapping">
            <summary>周报页与账号的对应（{summary.people.length} 人）</summary>
            <MappingTable rows={summary.people} />
          </details>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head weekly-report-table-head">
          <strong>问题清单</strong>
          <span className="muted small">
            {shown.length === findings.length ? `${findings.length} 条` : `${shown.length} / ${findings.length} 条`}
          </span>
          <select aria-label="按人筛选" value={person} onChange={(event) => setPerson(event.target.value)}>
            <option value="">全部人员</option>
            {summary.people.map((entry) => (
              <option key={entry.sheet} value={entry.sheet}>
                {entry.sheet}
              </option>
            ))}
          </select>
          <select aria-label="按问题类型筛选" value={problem} onChange={(event) => setProblem(event.target.value)}>
            <option value="">全部问题类型</option>
            {week.problem_types.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
          <div className="spacer" />
          <button type="button" className="btn sm" onClick={() => void exportAs("md")}>
            导出 Markdown
          </button>
          <button type="button" className="btn sm" onClick={() => void exportAs("csv")}>
            导出 CSV
          </button>
        </div>
        <FindingsTable findings={shown} week={week} />
      </div>
    </div>
  );
}

function JiraKeys({ value, browseUrl }: { value: string; browseUrl: string }) {
  const keys = value.split(/,\s*/).filter(Boolean);
  return (
    <>
      {keys.map((key, index) => (
        <span key={key}>
          {index > 0 && ", "}
          {browseUrl && JIRA_KEY_RE.test(key) ? (
            <a href={`${browseUrl}${key}`} target="_blank" rel="noreferrer">
              {key}
            </a>
          ) : (
            key
          )}
        </span>
      ))}
    </>
  );
}

function FindingsTable({ findings, week }: { findings: Finding[]; week: WeekDetail }) {
  return (
    <div className="table-scroll weekly-report-table-wrap">
      <table className="weekly-report-table" data-testid="weekly-report-findings">
        <thead>
          <tr>
            <th>姓名</th>
            <th>周报日期</th>
            <th>问题</th>
            <th>工作项</th>
            <th>JIRA</th>
            <th>JIRA状态</th>
            <th>assignee</th>
            <th>最后更新时间</th>
            <th>最后更新人</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((finding) => (
            <tr key={finding.id}>
              <td>{finding.person}</td>
              <td className="nowrap">{finding.sheet_date}</td>
              <td className="nowrap">{finding.problem}</td>
              <td>{finding.item}</td>
              <td className="nowrap">
                <JiraKeys value={finding.jira_key} browseUrl={week.jira_browse_url} />
              </td>
              <td>{finding.jira_status}</td>
              <td>{finding.assignee}</td>
              <td className="nowrap">{finding.last_updated}</td>
              <td>{finding.last_updater}</td>
              <td>{finding.note}</td>
            </tr>
          ))}
          {findings.length === 0 && (
            <tr className="empty-row">
              <td colSpan={10}>没有符合条件的问题</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
