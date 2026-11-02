import { useEffect, useRef, useState, type FormEvent } from "react";
import { RotateCcw, Trash2 } from "lucide-react";
import type { FailureLifecycleAction, FailureRecordListItem } from "./cicdAgentApi";

const LABELS = { trash: "移入回收站", restore: "恢复记录", purge: "彻底删除" };

export function FailureLifecycleDialog({ records, batch = false, action, onSubmit, onRefresh, onClose }: {
  records: FailureRecordListItem[];
  batch?: boolean;
  action: FailureLifecycleAction;
  onSubmit: (reason: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLFormElement>(null);
  const isPurge = action === "purge";
  const record = records[0];
  const title = `${batch ? "批量" : ""}${LABELS[action]}`;
  const confirmationText = batch ? `彻底删除 ${records.length} 条` : `彻底删除 #${record.id}`;
  const disabled = submitting || (isPurge && confirmation !== confirmationText);
  const Icon = action === "restore" ? RotateCcw : Trash2;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.querySelector<HTMLElement>("textarea, input, button")?.focus();
    return () => previous?.focus();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(reason.trim());
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  async function refresh() {
    setSubmitting(true);
    try {
      await onRefresh();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  return <div className="dialog-backdrop cicd-agent-detail-backdrop cicd-agent-lifecycle-backdrop"
    onMouseDown={(event) => {
      if (event.target === event.currentTarget && !submitting) {
        event.preventDefault();
        onClose();
      }
    }}>
    <form ref={dialogRef} className="dialog-card maxw-660 cicd-agent-lifecycle-dialog"
      role="alertdialog" aria-modal="true" aria-labelledby="failure-lifecycle-title"
      aria-describedby="failure-lifecycle-description" aria-busy={submitting}
      onSubmit={(event) => void submit(event)}
      onKeyDown={(event) => {
        if (event.key === "Escape") { event.stopPropagation(); if (!submitting) onClose(); }
        if (event.key !== "Tab") return;
        const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
        ) ?? []);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}>
      <h3 id="failure-lifecycle-title">{title}{batch ? `（${records.length} 条）` : ""}</h3>
      <div className="dialog-body">
        <p id="failure-lifecycle-description">
          {action === "trash" && "记录将移入回收站，RM 可在回收站恢复。"}
          {action === "restore" && "记录将恢复到正常失败列表。"}
          {isPurge && "彻底删除后无法在界面恢复，记录将不再可见；归档仍按保留策略留存，不会立即清除。"}
        </p>
        {batch ? <div className="cicd-agent-batch-records" tabIndex={0} aria-label="本次选中的记录">
          <table className="data-table">
            <thead><tr><th>ID</th><th>Job / Build</th><th>Stage</th></tr></thead>
            <tbody>{records.map(item => <tr key={item.id}>
              <td>#{item.id}</td><td>{item.job_name}<small>#{item.build_number}</small></td>
              <td>{item.normalized_stage || item.failed_stage || "N/A"}</td>
            </tr>)}</tbody>
          </table>
        </div> : <dl className="cicd-agent-lifecycle-record">
          <dt>记录</dt><dd>#{record.id}</dd>
          <dt>Job</dt><dd>{record.job_name}</dd>
          <dt>Build</dt><dd>#{record.build_number}</dd>
          <dt>Stage</dt><dd>{record.normalized_stage || record.failed_stage || "N/A"}</dd>
        </dl>}
        <label>备注（选填）
          <textarea value={reason} onChange={(event) => setReason(event.target.value)}
            rows={3} maxLength={4000} disabled={submitting} />
        </label>
        {isPurge && <label>输入“{confirmationText}”确认
          <input type="text" value={confirmation} onChange={(event) => setConfirmation(event.target.value)}
            autoComplete="off" disabled={submitting} />
        </label>}
        {error && <p className="action-err" role="alert">{error}</p>}
      </div>
      <div className="dialog-actions">
        {error && <button className="btn" type="button" disabled={submitting} onClick={() => void refresh()}>刷新记录</button>}
        <button className="btn" type="button" disabled={submitting} onClick={onClose}>取消</button>
        <button className={isPurge ? "btn danger" : "btn primary"} type="submit" disabled={disabled}>
          <Icon size={14} aria-hidden="true" />{submitting ? "处理中..." : title}
        </button>
      </div>
    </form>
  </div>;
}
