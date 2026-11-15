import { useEffect, useRef, useState } from "react";
import { apiPost } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { FlatCommandTable, type CommandRow } from "../../components/FlatCommandTable";
import { parseCsvRows } from "../../lib/csv";
import { stripDockerPullPrefix } from "../../lib/containerImage";

interface ImageSource {
  image: string; commit_id: string; hpc_version: string; git_url: string;
  app_name: string; app_version: string; tags: string[];
}
interface BatchResult {
  images: ImageSource[]; errors: { image: string; error: string }[];
  duplicate_count: number; columns: string[]; rows: CommandRow[];
}

export function BatchImageLookupPane() {
  const { user } = useAuth();
  const [input, setInput] = useState("");
  const [result, setResult] = useState<BatchResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef({ value: 0 }).current;
  useEffect(() => {
    request.value++;
    setResult(null); setInput(""); setError(""); setBusy(false);
    return () => { request.value++; };
  }, [user?.username, user?.role, request]);

  async function search() {
    if (busy) return;
    const id = ++request.value;
    setBusy(true); setError(""); setResult(null);
    const images = input.split(/\r?\n/).map(line => stripDockerPullPrefix(line).trim()).filter(Boolean);
    try {
      const data = await apiPost<BatchResult>("/api/lookup/images", { images });
      if (id === request.value) setResult(data);
    } catch (e) {
      if (id === request.value) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === request.value) setBusy(false);
    }
  }

  async function readFile(file: File) {
    const id = ++request.value;
    setResult(null); setError("");
    try {
      const text = await file.text();
      let lines: string[];
      if (file.name.toLowerCase().endsWith(".csv")) {
        const rows = parseCsvRows(text);
        const headers = ["镜像@digest", "image@digest", "image_reference", "reference", "image"];
        const column = headers.map(header => rows[0]?.findIndex(cell => cell.trim().toLowerCase() === header) ?? -1).find(index => index >= 0);
        lines = column === undefined
          ? rows.flat().filter(cell => /^\S+@sha256:[^\s]+$/.test(stripDockerPullPrefix(cell).trim()))
          : rows.slice(1).map(row => row[column] ?? "");
      } else {
        lines = text.replace(/^\uFEFF/, "").split(/\r?\n/);
      }
      if (!lines.some(line => line.trim())) throw new Error("清单中没有完整镜像@digest 引用");
      if (id === request.value) setInput(lines.map(line => stripDockerPullPrefix(line).trim()).filter(Boolean).join("\n"));
    } catch (e) {
      if (id === request.value) setError(e instanceof Error ? e.message : String(e));
    }
  }

  return <div className="panel">
    <div className="panel-body">
      <label htmlFor="lookup-images">完整镜像名（每行一个，最多 500 个）</label>
      <textarea id="lookup-images" className="input mono" style={{ width: "100%", minHeight: 130 }} value={input} disabled={busy}
        onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && input.trim()) { e.preventDefault(); void search(); } }}
        onChange={e => setInput(e.target.value.split(/\r?\n/).map(stripDockerPullPrefix).join("\n"))}
        placeholder="harbor.sh.mxcr.io/hpc-release/镜像名@sha256:…" aria-label="完整镜像名" data-testid="lookup-image-input" />
      <div className="row mt-8">
        <label>上传镜像清单 <input type="file" accept=".txt,.csv" aria-label="上传镜像清单" disabled={busy} onChange={e => {
          const file = e.target.files?.[0]; e.target.value = ""; if (file) void readFile(file);
        }} /></label>
        <button className="btn primary" disabled={busy || !input.trim()} data-testid="lookup-search" onClick={() => void search()}>{busy ? "查询中…" : "查询"}</button>
      </div>
      <p className="hint mt-8">每次实时读取 Harbor tags → commit → Gerrit app_info，按 OS、CPU 架构、GPU 和测试项展开命令。历史结果按 app_name、git_branch、app_version、arch、OS、GPU、test_name 匹配；没有结果时三列留空。</p>
      {busy && <p role="status">正在批量读取镜像和历史配置…</p>}
      {error && <p className="lerr" role="alert">{error}</p>}
      {result && result.errors.length > 0 && <div role="alert" className="lerr">以下镜像未生成命令：{result.errors.map((item, i) => <p key={i} className="small break">{item.image}：{item.error}</p>)}</div>}
      {!!result?.duplicate_count && <p className="hint">已合并 {result.duplicate_count} 个重复镜像。</p>}
    </div>
    {result && result.images.length > 0 && <div data-testid="lookup-image-result">
      <FlatCommandTable columns={result.columns} rows={result.rows} filename="digest_test_commands.csv" label="测试命令" onRefresh={() => void search()} />
      <div className="panel-body">
        <p className="hint" data-testid="lookup-image-source">根据镜像历史配置实时生成</p>
        {result.images.map(source => <details key={source.image} className="mt-8">
          <summary>{source.app_name} {source.app_version} · HPC {source.hpc_version || "未找到版本 tag"} · Commit {source.commit_id}</summary>
          <p className="mono small break">{source.image}</p>
          <a href={`/api/lookup/app-info?${new URLSearchParams({ image: source.image, commit_id: source.commit_id })}`} target="_blank" rel="noopener noreferrer" data-testid="app-info-source-link">{source.git_url} @ {source.commit_id}</a>
          {source.tags.map(tag => <div className="mono small break mt-4" key={tag}>{tag}</div>)}
        </details>)}
      </div>
    </div>}
  </div>;
}
