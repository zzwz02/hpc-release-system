/** Small status pills and read-only field renderers shared by the release pages. */
import React from "react";
import type { App, AppAuditEntry, Entry } from "../../types";
import { Markdown } from "../../components/Markdown";
import { cicdStatusLabel, entryResultLabel } from "../../lib/domainMetadata";
import { qaStatusLabels } from "../../lib/labels";
import { formatServerTime } from "../../lib/time";
import { qaDotClass, qaDotTitle } from "./helpers";

export function QaPill({ status }: { status?: string }) {
  const s = status ?? "not_checked";
  const cls = s === "qa_passed" ? "ok" : s === "has_issues" ? "warnp" : s === "cannot_release" ? "bad" : "";
  return <span className={`pill ${cls}`}>QA {qaStatusLabels[s as keyof typeof qaStatusLabels] ?? s}</span>;
}

export function QaDot({ entry }: { entry: Entry | null }) {
  return <span className={`app-dot ${qaDotClass(entry)}`} title={qaDotTitle(entry)} />;
}

export function CicdStatusPill({ app }: { app: App }) {
  const running = app.cicd_status === "running";
  return (
    <span className={`pill ${running ? "ok" : "bad"}`} title="CICD 每日构建状态">
      CICD {cicdStatusLabel(app.cicd_status)}
    </span>
  );
}

/** Signup state of an App in the selected cycle. */
export function EntryPill({ entry }: { entry: Entry | null }) {
  if (!entry) return <span className="pill">未报名</span>;
  if (entry.status === "withdrawn") return <span className="pill">已撤回</span>;
  if (entry.result) {
    return (
      <span className={`pill ${entry.result === "released" ? "ok" : "bad"}`}>{entryResultLabel(entry.result)}</span>
    );
  }
  return <span className="pill accent">已报名</span>;
}

export function CicdPendingPill({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span className="pill warnp" title="此 App 有待审批或待交付的 CICD 流程" data-testid="app-cicd-pending-pill">
      CICD 待处理 {count}
    </span>
  );
}

/** Markdown (or verbatim code) in a capped-height, scrolling box. */
export function ReadField({ value, code = false }: { value?: string | null; code?: boolean }) {
  const v = (value ?? "").trim();
  if (!v) return <div className="readfield empty">（空）</div>;
  if (code) return <div className="readfield code-block"><pre>{v}</pre></div>;
  return (
    <div className="readfield">
      <Markdown value={value} className="md-view" />
    </div>
  );
}

function auditValueText(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.join(", ") || "(空)";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value).trim() || "(空)";
}

export function ChangeLogTable({ entries, loading }: { entries: AppAuditEntry[] | null; loading: boolean }) {
  const [openRows, setOpenRows] = React.useState<Record<number, boolean>>({});
  if (loading) return <p className="muted small">加载中…</p>;
  if (!entries) return <p className="muted small">展开后自动加载</p>;
  if (!entries.length) return <p className="muted small">暂无变更记录</p>;
  return (
    <div className="table" data-testid="changelog-table">
      <table>
        <thead>
          <tr><th>时间</th><th>操作人</th><th>角色</th><th>事件</th></tr>
        </thead>
        <tbody>
          {entries.map((e, i) => {
            const detail = Array.isArray(e.detail) ? e.detail : [];
            const hasDetail = detail.length > 0;
            return (
              <React.Fragment key={i}>
                <tr
                  className={hasDetail ? "cl-clickable pointer" : ""}
                  onClick={() => hasDetail && setOpenRows((rows) => ({ ...rows, [i]: !rows[i] }))}
                >
                  <td>{formatServerTime(e.ts)}</td>
                  <td>{e.user}</td>
                  <td>{e.role}</td>
                  <td>{e.message}{hasDetail ? <span className="small muted"> ▸ 详情</span> : null}</td>
                </tr>
                {hasDetail && openRows[i] && (
                  <tr className="cl-detail">
                    <td colSpan={4}>
                      <table className="cl-detail-table">
                        <thead><tr><th>字段</th><th>原值</th><th>新值</th></tr></thead>
                        <tbody>
                          {detail.map((d, j) => (
                            <tr key={j}>
                              <td>{d.label || d.field}</td>
                              <td>{auditValueText(d.old)}</td>
                              <td>{auditValueText(d.new)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
