import { useEffect, useState } from "react";
import { apiFetch } from "../../api/http";
import { Markdown } from "../../components/Markdown";
import { fileDownloadUrl, type AgentFile } from "./jiraAgentApi";

interface Preview {
  kind: "markdown" | "code" | "text" | "binary";
  language: string;
  text: string;
  truncated: boolean;
}

export function ArtifactLink({ file, label }: { file: AgentFile; label?: string }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className="artifact-link" aria-label={label ? `预览 ${file.name}` : undefined} onClick={() => setOpen(true)}>{label || file.remote_path || file.name}</button>
    {open && <ArtifactPreview file={file} onClose={() => setOpen(false)} />}
  </>;
}

function ArtifactPreview({ file, onClose }: { file: AgentFile; onClose: () => void }) {
  const [preview, setPreview] = useState<Preview>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void apiFetch<Preview>(`${fileDownloadUrl(file.conversation_id, file.id)}/preview`, { signal: controller.signal })
      .then(setPreview).catch((err: unknown) => { if (!controller.signal.aborted) setError(String(err)); });
    return () => controller.abort();
  }, [file.conversation_id, file.id]);
  useEffect(() => {
    function escape(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);
  // A longer fence prevents code containing Markdown fences from escaping the block.
  const fence = "`".repeat((preview?.text.match(/`+/g) ?? []).reduce((longest, run) => Math.max(longest, run.length + 1), 3));
  return <div className="dialog-backdrop artifact-preview-backdrop" role="dialog" aria-modal="true" aria-label={`预览 ${file.name}`}>
    <div className="dialog-card artifact-preview-dialog">
      <div className="panel-head"><strong>{file.name}</strong><button type="button" className="btn sm" onClick={onClose} autoFocus>关闭预览</button></div>
      <div className="dialog-body artifact-preview-body">
        {error && <p role="alert">{error}</p>}
        {!preview && !error && <p>正在读取文件…</p>}
        {preview?.kind === "binary" && <p>二进制文件或不支持的文本编码，无法预览。请下载后查看。</p>}
        {preview?.kind === "markdown" && <Markdown value={preview.text} />}
        {preview?.kind === "code" && <Markdown value={`${fence}${preview.language}\n${preview.text}\n${fence}`} />}
        {preview?.kind === "text" && <pre className="artifact-text">{preview.text}</pre>}
        {preview?.truncated && <p className="muted">仅预览前 1 MiB，请下载查看完整文件。</p>}
      </div>
      <div className="dialog-actions"><a className="btn primary" href={fileDownloadUrl(file.conversation_id, file.id)}>下载此文件</a></div>
    </div>
  </div>;
}
