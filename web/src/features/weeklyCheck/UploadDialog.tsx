/**
 * RM flow: pick the weekly .xlsx → review what was recognised → analyse.
 *
 * The page owns the running job, so this dialog can be closed while the
 * analysis continues; `open` only hides it, keeping the upload for a retry.
 */
import { useState } from "react";
import { DateInput } from "../../components/DateInput";
import { confirmDialog } from "../../lib/confirm";
import { toast } from "../../lib/toast";
import {
  cancelJob,
  formatFileSize,
  isoWeekKey,
  startAnalysis,
  uploadReport,
  type AnalysisJob,
  type UploadPreview,
  type WeekListItem,
} from "./weeklyCheckApi";

export function MappingTable({
  rows,
}: {
  rows: { sheet: string; username: string; display_name: string; note: string }[];
}) {
  return (
    <div className="table-scroll weekly-check-mapping-table">
      <table data-testid="weekly-check-mapping">
        <thead>
          <tr>
            <th>周报页</th>
            <th>姓名</th>
            <th>账号</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.sheet}>
              <td>{row.sheet}</td>
              <td>{row.display_name}</td>
              <td>{row.username || "—"}</td>
              <td>{row.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function UploadDialog({
  open,
  weeks,
  jobId,
  job,
  onStarted,
  onClose,
}: {
  open: boolean;
  weeks: WeekListItem[];
  /** The analysis started from this dialog; "" before the first one. */
  jobId: string;
  job: AnalysisJob | undefined;
  onStarted: (jobId: string) => void;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [reportDate, setReportDate] = useState("");
  const [activeHours, setActiveHours] = useState("");
  const [consistency, setConsistency] = useState(true);
  const running = Boolean(jobId) && (!job || job.status === "running");
  const [stopRequested, setStopRequested] = useState("");
  const stopping = running && (Boolean(job?.stopping) || stopRequested === jobId);

  async function stop() {
    const ok = await confirmDialog({
      title: "中止分析",
      body: "中止后本次分析不会保存，已有结果不变；进行中的 LLM / JIRA 请求返回后才会停下。确认中止？",
      confirmText: "中止分析",
      danger: true,
    });
    if (!ok) return;
    try {
      await cancelJob(jobId);
      setStopRequested(jobId);
    } catch (cancelError) {
      toast.error(errorMessage(cancelError));
    }
  }

  async function choose(file: File | undefined) {
    if (!file) return;
    setError("");
    setPreview(null);
    onStarted("");
    setUploading(true);
    try {
      const next = await uploadReport(file);
      setPreview(next);
      setReportDate(next.report_date);
      setActiveHours(String(next.active_hours));
    } catch (uploadError) {
      setError(errorMessage(uploadError));
    } finally {
      setUploading(false);
    }
  }

  async function analyze() {
    if (!preview) return;
    setError("");
    try {
      const started = await startAnalysis(preview.upload_id, {
        report_date: reportDate,
        active_hours: Number(activeHours),
        consistency,
      });
      onStarted(started.job_id);
    } catch (startError) {
      setError(errorMessage(startError));
    }
  }

  const weekKey = isoWeekKey(reportDate);
  const existing = weeks.find((week) => week.week_key === weekKey);
  const hoursValid = /^\d+$/.test(activeHours) && Number(activeHours) > 0;
  const failed = job?.status === "failed" ? job.error || job.message : "";
  const cancelled = job?.status === "cancelled";

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="上传周报" hidden={!open}>
      <div className="dialog-card maxw-660">
        <h3>上传周报</h3>
        <div className="dialog-body">
          <label className="weekly-check-field">
            <span>周报文件（PDE HPC Project Plan .xlsx，不超过 20 MB；只在内存中解析，不保存）</span>
            <input
              type="file"
              aria-label="周报文件"
              accept=".xlsx"
              disabled={uploading || running}
              onChange={(event) => void choose(event.target.files?.[0])}
            />
          </label>
          {uploading && <p className="muted">正在上传并解析...</p>}
          {error && <p className="weekly-check-warning" role="alert">{error}</p>}
          {preview && (
            <>
              <dl className="weekly-check-facts" data-testid="weekly-check-preview">
                <dt>文件</dt>
                <dd>{preview.filename}（{formatFileSize(preview.size)}）</dd>
                <dt>识别到的周</dt>
                <dd>{preview.week ? preview.week.label : "未能识别，请填写报告日期"}</dd>
                <dt>检查人数</dt>
                <dd>{preview.people_count} 人</dd>
                {preview.unmapped_sheets.length > 0 && (
                  <>
                    <dt>找不到对应的用户</dt>
                    <dd>
                      {preview.unmapped_sheets.join("、")}
                      <div className="hint">
                        这些页在“手工对应”里没有登记，只检查是否写了 JIRA ID；可先去“手工对应”补上再分析。
                      </div>
                    </dd>
                  </>
                )}
                {preview.undated_sheets.length > 0 && (
                  <>
                    <dt>没有日期的页</dt>
                    <dd>{preview.undated_sheets.join("、")}（A 列没有日期，不检查）</dd>
                  </>
                )}
              </dl>
              <details className="weekly-check-mapping">
                <summary>周报页与账号的对应（{preview.mapping.length} 页）</summary>
                <MappingTable
                  rows={preview.mapping.map((row) => ({
                    ...row,
                    note: row.status === "skip" ? `不检查：${row.reason}` : row.reason,
                  }))}
                />
              </details>
              <div className="weekly-check-options">
                <label className="weekly-check-field">
                  <span>报告日期</span>
                  <DateInput
                    value={reportDate}
                    onChange={setReportDate}
                    testId="weekly-check-report-date"
                    ariaLabel="报告日期"
                    size="sm"
                  />
                </label>
                <label className="weekly-check-field">
                  <span>JIRA 更新窗口（小时）</span>
                  <input
                    className="input sm"
                    inputMode="numeric"
                    aria-label="JIRA 更新窗口（小时）"
                    value={activeHours}
                    disabled={running}
                    onChange={(event) => setActiveHours(event.target.value)}
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={consistency}
                    disabled={running}
                    onChange={(event) => setConsistency(event.target.checked)}
                  />
                  分析 JIRA 更新与周报是否一致
                </label>
              </div>
              {!weekKey && <p className="weekly-check-warning">报告日期格式应为 YYYY-MM-DD。</p>}
              {existing && !jobId && (
                <p className="weekly-check-warning" data-testid="weekly-check-overwrite">
                  将覆盖 {existing.label} 的现有结果。
                </p>
              )}
            </>
          )}
          {jobId && (
            <div className="weekly-check-progress" data-testid="weekly-check-progress">
              {failed ? (
                <p className="weekly-check-warning" role="alert">
                  分析失败：{failed}
                  <br />
                  已有结果没有改动，可以修正后重新点“分析”。
                </p>
              ) : cancelled ? (
                <p className="weekly-check-warning" role="status">
                  分析已中止，已有结果没有改动；可以重新点“分析”。
                </p>
              ) : (
                <>
                  <div className="bar">
                    <span style={{ width: `${job?.total ? Math.round((job.done / job.total) * 100) : 0}%` }} />
                  </div>
                  <div className="weekly-check-progress-row">
                    <span className="muted">
                      {job?.total ? `第 ${job.done} 人 / 共 ${job.total} 人 · ` : ""}
                      {stopping && !job?.stopping ? "正在中止" : job?.message ?? "分析任务已提交"}
                    </span>
                    {running && (
                      <button type="button" className="btn sm danger" disabled={stopping} onClick={() => void stop()}>
                        {stopping ? "正在中止…" : "中止分析"}
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            {running ? "在后台继续" : "关闭"}
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={!preview || running || !weekKey || !hoursValid}
            onClick={() => void analyze()}
          >
            分析
          </button>
        </div>
      </div>
    </div>
  );
}
