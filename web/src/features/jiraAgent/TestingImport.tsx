import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, FileText, MessageSquare, Paperclip, X } from "lucide-react";
import { useAuth } from "../../api/AuthContext";
import { downloadTestImport, importTestCase, prepareTestImport, type CaseDraft, type CaseImport } from "./jiraTestingApi";

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

export function TestingImport({ conversation, ready, close }: {
  conversation: string; ready: (draft: CaseDraft) => void; close: () => void;
}) {
  const { user } = useAuth();
  const source = useQuery({ queryKey: ["jira-testing", `${user?.username}:${user?.role}`, "import", conversation],
    queryFn: () => importTestCase(conversation), retry: false, staleTime: 0, refetchOnMount: "always" });
  return <>
    {source.isPending && <p role="status">正在读取评论和文件列表…</p>}
    {source.isError && <div role="alert" className="jira-agent-warning">{errorText(source.error)} <button className="btn sm" onClick={() => void source.refetch()}>重试</button> <button className="btn sm" onClick={close}>取消</button></div>}
    {source.data && <Selection key={source.data.snapshot} source={source.data} conversation={conversation} ready={ready} close={close} />}
  </>;
}

function Selection({ source, conversation, ready, close }: {
  source: CaseImport; conversation: string; ready: (draft: CaseDraft) => void; close: () => void;
}) {
  const [step, setStep] = useState(source.comments.length ? "comments" : "files");
  const [comments, setComments] = useState<string[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [files, setFiles] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [downloads, setDownloads] = useState<string[]>([]);
  const [error, setError] = useState("");
  const empty = !source.comments.length && !source.files.length;
  useEffect(() => {
    if (empty) ready({ title: source.title, input: source.input, provenance: source.provenance, notice: source.notice });
  }, [empty, source, ready]);

  async function finish() {
    setBusy(true); setError("");
    try {
      const draft = await prepareTestImport(conversation, { snapshot: source.snapshot,
        comments: source.comments.filter((item) => comments.includes(item.id)).map((item) => ({ id: item.id, body: edits[item.id] ?? item.body })), file_ids: files });
      ready(draft);
    } catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }
  async function download(file: CaseImport["files"][number]) {
    setDownloads((previous) => [...previous, file.id]); setError("");
    try {
      const blob = await downloadTestImport(conversation, file.id, source.snapshot);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = file.name;
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) { setError(errorText(failure)); }
    finally { setDownloads((previous) => previous.filter((id) => id !== file.id)); }
  }
  const feedback = error ? <p role="alert" className="jira-agent-warning">{error}</p> : busy ? <p role="status" className="muted">正在整理选中的材料…</p> : null;
  if (empty) return null;
  return <>
    <ImportDialog title="选择评论" icon={<MessageSquare size={20} />} open={step === "comments"} busy={busy} close={close}
      description="勾选需要带入的评论；选中后可编辑，去掉其中的答案。"
      footer={<><span className="test-import-count">已选 <strong>{comments.length}</strong> / {source.comments.length} 条</span><div className="actions"><button className="btn" disabled={busy} onClick={close}>取消</button><button className="btn primary" disabled={busy} onClick={() => { setError(""); if (source.files.length) setStep("files"); else void finish(); }}>{busy ? "正在整理…" : source.files.length ? "下一步：选择附件" : "确认评论"}</button></div></>} feedback={feedback}>
      <div className="test-import-comments">{source.comments.map((item, index) => {
        const selected = comments.includes(item.id);
        return <article key={item.id} className={`test-import-comment ${selected ? "selected" : ""}`}>
          <label className="test-import-comment-heading"><input type="checkbox" aria-label={`带入评论 ${index + 1}`} checked={selected} disabled={busy} onChange={(event) => setComments((previous) => event.target.checked ? [...previous, item.id] : previous.filter((id) => id !== item.id))} />
            <span><strong>{item.author || "评论"}</strong><span className="muted">{item.created}</span></span><span className="test-import-number">{index + 1}</span>
          </label>
          {selected ? <div className="test-import-comment-editor"><label htmlFor={`import-comment-${index}`}>带入内容</label><textarea id={`import-comment-${index}`} aria-label={`评论 ${index + 1} 的带入内容`} rows={6} value={edits[item.id] ?? item.body} disabled={busy} onChange={(event) => setEdits((previous) => ({ ...previous, [item.id]: event.target.value }))} /></div>
            : <div className="test-import-comment-body">{item.body || "（无正文）"}</div>}
        </article>;
      })}</div>
    </ImportDialog>
    <ImportDialog title="选择附件" icon={<Paperclip size={20} />} open={step === "files"} busy={busy} close={close}
      description="只勾选复现所需的文件。可先下载查看，下载不会自动选中。"
      footer={<><span className="test-import-count">已选 <strong>{files.length}</strong> / {source.files.length} 个</span><div className="actions">{!!source.comments.length && <button className="btn test-import-back" disabled={busy} onClick={() => { setError(""); setStep("comments"); }}><ArrowLeft size={15} aria-hidden="true" />上一步</button>}<button className="btn" disabled={busy} onClick={close}>取消</button><button className="btn primary" disabled={busy || downloads.length > 0} onClick={() => void finish()}>{busy ? "正在整理…" : "确认附件"}</button></div></>} feedback={feedback}>
      <ul className="test-import-files">{source.files.map((file) => <li key={file.id} className={files.includes(file.id) ? "selected" : ""}>
        <label><input type="checkbox" aria-label={`带入文件 ${file.name}`} checked={files.includes(file.id)} disabled={busy} onChange={(event) => setFiles((previous) => event.target.checked ? [...previous, file.id] : previous.filter((id) => id !== file.id))} />
          <FileText size={22} className="test-import-file-icon" aria-hidden="true" /><span className="test-import-file-name"><strong>{file.name}</strong><small>{file.source === "jira" ? "Jira 附件" : `人工上传${file.turn ? ` · 第 ${file.turn} 轮` : ""}`}<span>·</span>{file.size < 1024 ? `${file.size} B` : file.size < 1024 * 1024 ? `${(file.size / 1024).toFixed(1)} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`}</small></span>
        </label><button className="btn sm test-import-download" aria-label={`下载 ${file.name}`} disabled={busy || downloads.includes(file.id)} onClick={() => void download(file)}><Download size={15} aria-hidden="true" />{downloads.includes(file.id) ? "下载中…" : "下载"}</button>
      </li>)}</ul>
    </ImportDialog>
  </>;
}

function ImportDialog({ title, icon, description, open, busy, close, footer, feedback, children }: {
  title: string; icon: ReactNode; description: string; open: boolean; busy: boolean; close: () => void;
  footer: ReactNode; feedback: ReactNode; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  return <dialog ref={dialog} className="test-dialog test-import-dialog" aria-label={title} onCancel={(event) => { event.preventDefault(); if (!busy) close(); }}>
    <div className="test-import-heading"><div><h3>{icon}{title}</h3><p>{description}</p></div><button className="test-import-close" aria-label={`取消${title}`} disabled={busy} onClick={close}><X size={20} aria-hidden="true" /></button></div>
    <div className="test-import-body">{children}</div>
    <div className="test-import-footer">{feedback}<div>{footer}</div></div>
  </dialog>;
}
