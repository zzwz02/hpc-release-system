import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { Markdown } from "../../components/Markdown";
import { readLearningMaterial, type LearningSource } from "./jiraLearningApi";

const LABELS: Record<string, string> = { description: "Jira 正文", comment: "Jira 评论", custom_field: "补充字段", owner_message: "人工补充", agent_message: "Agent 过程说明", agent_tool: "命令与工具输出", jira_attachment: "Jira 附件", agent_artifact: "分析文件", agent_analysis_path: "分析路径", agent_comment: "分析评论", agent_result: "分析结果", owner_upload: "用户上传" };

export function MaterialList({ sources, showIssue = false, jobId }: { sources: LearningSource[]; showIssue?: boolean; jobId: string }) {
  const [expanded, setExpanded] = useState("");
  const [page, setPage] = useState(0);
  const current = Math.min(page, Math.max(0, Math.ceil(sources.length / 20) - 1));
  return <div className="learning-material-list">
    {sources.slice(current * 20, current * 20 + 20).map((source) => <details key={source.id} open={expanded === source.id}>
      <summary onClick={(event) => { event.preventDefault(); setExpanded(expanded === source.id ? "" : source.id); }}>
        <span>{showIssue && source.issue_key && <strong>{source.issue_key} · </strong>}{source.name || LABELS[source.kind] || "材料"}</span>
        {(source.author || source.name) && <span className="muted">{source.author || LABELS[source.kind] || "文件"}</span>}
      </summary>
      {expanded === source.id && <div className="learning-material-content">
        {!!(source.author || source.created_at || source.turn_seq) && <p className="muted">{source.author} {source.created_at}{source.turn_seq ? ` · 第 ${source.turn_seq} 轮` : ""}{source.turn_status === "failed" ? " · 未完成" : ""}</p>}
        {source.range && <p className="muted">读取范围：{source.range.start}–{source.range.end}{source.range.unit === "page" ? " 页（从 0 起）" : source.range.unit === "character" ? " 字符" : ""}</p>}
        {source.archive && <p className="muted">来自 {source.archive.name} / {source.archive.member}</p>}
        {!!source.copies?.length && <details><summary>相同内容的其他来源（{source.copies.length} 份）</summary><ul>{source.copies.map((copy) => <li key={copy.id}>{copy.name || "同内容副本"}{copy.turn_id && <span className="muted"> · 分析产物</span>}</li>)}</ul></details>}
        <MaterialBody key={source.id} jobId={jobId} source={source} />
      </div>}
    </details>)}
    {!sources.length && <p className="muted learning-material-empty">没有匹配的材料</p>}
    {sources.length > 20 && <div className="learning-material-pagination"><span>{current * 20 + 1}–{Math.min(current * 20 + 20, sources.length)} / {sources.length}</span><button className="btn sm" disabled={!current} onClick={() => setPage(current - 1)}>上一页</button><button className="btn sm" disabled={(current + 1) * 20 >= sources.length} onClick={() => setPage(current + 1)}>下一页</button></div>}
  </div>;
}

function MaterialBody({ jobId, source }: { jobId: string; source: LearningSource }) {
  const { user } = useAuth();
  const [offset, setOffset] = useState(0);
  const [history, setHistory] = useState<number[]>([]);
  const body = useQuery({ queryKey: ["jira-learning", user?.username, "body", jobId, source.id, offset], queryFn: () => readLearningMaterial(jobId, source.id, offset), staleTime: Infinity });
  if (body.isError) return <p role="alert">读取失败：{String(body.error)}</p>;
  if (!body.data) return <p className="muted">正在读取正文…</p>;
  const content = body.data.contentItems.find((item) => item.type === "inputText")?.text || "{}";
  const result = JSON.parse(content) as { error?: string; message?: string; text?: string; items?: LearningSource[]; total?: number; next_offset?: number | null; range?: { unit: string; start: number; end: number } };
  return <>
    {result.error && <p role="alert">{result.error}</p>}
    {result.message && <p className="muted">{result.message}</p>}
    {result.range && <p className="muted">{result.range.unit === "page" ? `第 ${result.range.start + 1} 页` : result.range.unit === "image" ? "图片" : `字符 ${result.range.start + 1}–${result.range.end}`}</p>}
    {result.items ? <><p className="muted">包内文件 · 共 {result.total} 项</p><MaterialList sources={result.items} jobId={jobId} /></> : result.text && (/\.md$/i.test(source.name || "") || ["owner_message", "agent_message", "agent_analysis_path"].includes(source.kind) ? <Markdown value={result.text} /> : <pre className={["description", "comment", "agent_comment"].includes(source.kind) ? "learning-source-prose" : undefined}>{result.text}</pre>)}
    {body.data.contentItems.filter((item) => item.type === "inputImage" && item.imageUrl).map((item, index) => <img key={index} src={item.imageUrl} alt={source.name || "材料图片"} style={{ maxWidth: "100%" }} />)}
    {(history.length > 0 || result.next_offset != null) && <div className="learning-material-pagination"><button className="btn sm" disabled={!history.length} onClick={() => { setOffset(history.at(-1) || 0); setHistory(history.slice(0,-1)); }}>上一段</button><button className="btn sm" disabled={result.next_offset == null} onClick={() => { setHistory([...history, offset]); setOffset(result.next_offset!); }}>下一段</button></div>}
  </>;
}
