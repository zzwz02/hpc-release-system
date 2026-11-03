import { useEffect, useState } from "react";
import { apiFetch, apiPost } from "../../api/http";
import { createPortal } from "react-dom";
import { confirmDialog } from "../../lib/confirm";
import { formatServerTime } from "../../lib/time";
import { ArtifactLink } from "./ArtifactPreview";
import type { AgentFile, AgentTurn } from "./jiraAgentApi";

interface Publication {
  body: string;
  frozen: boolean;
  revision: number;
  selected_file_ids: string[];
  draft_status: string;
  draft_error: string;
  uploads: { file_id: string; name: string; state: string; error: string }[];
  turn: AgentTurn;
}
interface Revision { revision: number; source: string; body: string; file_ids: string[]; editor: string; created_at: string }
interface History { items: Revision[]; next_before: number }

export function CommentPublication({ turn, files: allFiles, onChanged, canWrite = true, browseUrl = "" }: {
  turn: AgentTurn; files: AgentFile[]; onChanged: () => void; canWrite?: boolean; browseUrl?: string;
}) {
  const files = allFiles.filter(file => file.turn_id === turn.id && file.source === "artifact");
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [publication, setPublication] = useState<Publication>();
  const [history, setHistory] = useState<History>();
  const frozen = publication?.frozen ?? false;
  const posted = publication?.turn.comment_status === "posted" || turn.comment_status === "posted";
  const waiting = turn.status !== "completed" || ["collecting", "incomplete"].includes(turn.delivery_status);
  const editedBy = publication?.turn.comment_edited_by ?? turn.comment_edited_by;
  const commentId = publication?.turn.comment_id || turn.comment_id;
  const href = posted && commentId && /^https?:\/\//i.test(browseUrl)
    ? `${browseUrl}?focusedCommentId=${encodeURIComponent(commentId)}#comment-${encodeURIComponent(commentId)}` : "";
  const base = `/api/jira-agent/turns/${encodeURIComponent(turn.id)}/comment`;
  useEffect(() => {
    function protect(event: BeforeUnloadEvent) {
      if (dirty || busy) { event.preventDefault(); event.returnValue = ""; }
    }
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty, busy]);

  function load(value: Publication) {
    setPublication(value); setBody(value.body); setSelected(value.selected_file_ids ?? []); setDirty(false); setConflict(false);
  }

  function change(nextBody: string, nextFiles: string[]) {
    setBody(nextBody); setSelected(nextFiles); setDirty(true);
  }

  async function show() {
    setPublication(undefined); setBody(""); setSelected([]); setOpen(true); setBusy("正在读取草稿…"); setError(""); setHistory(undefined);
    try { load(await apiFetch<Publication>(base)); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(""); }
  }

  function close() {
    if (busy) return;
    setOpen(false); setDirty(false);
  }

  async function loadLatest() {
    if (dirty && !await confirmDialog({ title: "载入最新版本", body: "载入后将替换当前尚未保存的编辑，是否继续？" })) return;
    try { load(await apiFetch<Publication>(base)); setError(""); }
    catch (err) { setError(String(err)); }
  }

  async function send() {
    setBusy("正在上传附件并发送评论…"); setError("");
    try {
      const response = await apiPost<Publication>(`${base}/send`, { body, file_ids: selected, revision: publication?.revision });
      load(response); setHistory(undefined);
      if (response.turn.comment_status !== "posted") setError(response.turn.comment_error || "发送未完成，请核对后重试");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      try {
        const current = await apiFetch<Publication>(base);
        if (current.frozen) load(current);
        else if (current.revision !== publication?.revision) setConflict(true);
      } catch { /* Preserve edits when connectivity is unavailable. */ }
      onChanged();
    } finally { setBusy(""); }
  }

  async function readHistory(before = 0) {
    try {
      const response = await apiFetch<History>(`${base}/history${before ? `?before=${before}` : ""}`);
      setHistory(previous => before && previous ? { ...response, items: [...previous.items, ...response.items] } : response);
    } catch (err) { setError(String(err)); }
  }

  return <>
    <span className="jira-comment-publication" onClick={event => event.stopPropagation()}>
      {posted && <><span>{editedBy ? <>已由<strong>{editedBy}</strong>编辑并发送到 JIRA</> : "已发布到 Jira"}</span><span> · </span>{href ? <a href={href} target="_blank" rel="noopener noreferrer">#{commentId}</a> : <span>#{commentId}</span>}</>}
      <button type="button" className="btn sm" disabled={Boolean(busy) || waiting}
        onClick={event => { event.preventDefault(); void show(); }}>{posted ? "查看编辑历史" : canWrite ? "发送到 JIRA" : "查看评论及编辑历史"}</button>
    </span>
    {open && createPortal(<div onClick={event => event.stopPropagation()} className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="发送到 JIRA">
      <div className="dialog-card jira-comment-dialog">
        <div className="panel-head"><strong>{posted ? "已发送评论" : "发送到 JIRA"}</strong><button type="button" className="btn sm" disabled={Boolean(busy)} onClick={() => void close()}>关闭</button></div>
        <div className="dialog-body">
          {busy && <p role="status">{busy}</p>}
          {error && <p role="alert" className="error">{error}</p>}
          {posted && <p role="status">已发送 JIRA 评论（#{publication?.turn.comment_id || turn.comment_id}），本轮不能再次发送。</p>}
          {frozen && !posted && <p>已提交的文字和附件已锁定。重试会核对已有上传及发布结果。</p>}
          <label>评论内容（JIRA 格式）<textarea aria-label="JIRA 评论内容" rows={12} value={body} disabled={Boolean(busy) || frozen || !canWrite}
            onChange={e => change(e.target.value, selected)} /></label>
          {!frozen && canWrite && <p className="muted">仅确认发送时保存，关闭后不保留未发送的修改。</p>}
          {conflict && <button className="btn" onClick={() => void loadLatest()}>载入最新版本</button>}
          <fieldset><legend>选择要上传到 JIRA 的产物</legend>
            {files.length === 0 && <p className="muted">本轮没有产物。</p>}
            {files.map(file => <div className="jira-comment-file" key={file.id}>
              <label><input type="checkbox" checked={selected.includes(file.id)} disabled={Boolean(busy) || frozen || !canWrite || !file.downloadable}
                onChange={e => change(body, e.target.checked ? [...selected, file.id] : selected.filter(id => id !== file.id))} />{file.name}</label>
              <ArtifactLink file={file} label="预览" />
            </div>)}
          </fieldset>
          {publication?.uploads.map(upload => <p className="muted" key={upload.file_id}>
            {upload.name}：{upload.state === "posted" ? "已上传" : upload.error || "等待上传"}</p>)}
          <button type="button" className="btn sm" onClick={() => void readHistory()}>查看编辑历史</button>
          {history && <div className="jira-comment-history">
            {history.items.length === 0 && <p className="muted">暂无已记录的编辑。</p>}
            {history.items.map(item => <details key={item.revision}>
              <summary>版本 {item.revision} · {item.source === "generated" ? "自动生成" : item.source === "sent" ? "确认发送" : "用户编辑"} · {item.editor} · {formatServerTime(item.created_at)}</summary>
              <pre className="artifact-text">{item.body}</pre>
              <p>选择的产物：{item.file_ids.map(id => files.find(file => file.id === id)?.name || id).join("、") || "无"}</p>
            </details>)}
            {history.next_before > 0 && <button className="btn sm" onClick={() => void readHistory(history.next_before)}>更早的版本</button>}
          </div>}
        </div>
        <div className="dialog-actions">
          {!posted && canWrite && <button className="btn primary" disabled={Boolean(busy) || !publication || !body.trim() || conflict} onClick={() => void send()}>
            {frozen ? "核对并重试发送" : `确认发送${selected.length ? `并上传 ${selected.length} 个附件` : ""}`}
          </button>}
        </div>
      </div>
    </div>, document.body)}
  </>;
}
