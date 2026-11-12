/**
 * 历史查询 — reconstruct tests for an immutable Harbor image, or read an App's
 * release history.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../api/http";
import { qaStatusLabels } from "../../lib/labels";
import { entryResultLabel, releasePhaseLabel } from "../../lib/domainMetadata";
import { QaTabs } from "../qa/QaTabs";
import { ChipSupportFields } from "../../components/ChipSupportFields";
import { TestScopeTable } from "../../components/TestScopeTable";
import { stripDockerPullPrefix } from "../../lib/containerImage";
import type { App, AppInfoTest, BaseImage, EnvironmentRow, QaStatus, StatePayload, TestPlatform } from "../../types";

type TestCommand = TestPlatform & { test: string; command: string };

function CommandTable({ commands }: { commands: TestCommand[] }) {
  return <TestScopeTable tests={commands.map((command) => ({ path: command.test, command: command.command, platforms: [command] }))} />;
}

interface LookupRecord {
  app_id: string;
  app_name: string;
  release: { id: string; name: string; phase: string; release_date: string };
  environment: EnvironmentRow[];
  base_image: BaseImage | null;
  entry: {
    status: string;
    target_version: string;
    actual_delivery: string;
    known_issues: string;
    qa_status: QaStatus;
    qa_note: string;
    result: string;
    deliverable_image: string;
    deliverable_digest: string;
    deliverable_commit: string;
  };
  app_info: { id: number; commit_id: string; app_version: string; x86_chips: string[]; arm_chips: string[] } | null;
  scope_tests: AppInfoTest[];
  test_commands: TestCommand[];
}

interface ImageResult {
  image: string;
  digest: string;
  tags: string[];
  commit_id: string;
  commit_tags: string[];
  git_url: string;
  app_name: string;
  app_version: string;
  x86_chips: string[];
  arm_chips: string[];
  test_commands: TestCommand[];
}

interface AppResult {
  app_id: string;
  app_name: string;
  cicd_status: string;
  entries: LookupRecord[];
}

function RecordCard({ record }: { record: LookupRecord }) {
  const e = record.entry;
  return (
    <div className="panel" data-testid="lookup-record">
      <div className="panel-head">
        <h2>{record.app_name} · {record.release.name}</h2>
        <span className="pill">{releasePhaseLabel(record.release.phase)}</span>
        {e.status === "withdrawn" ? <span className="pill">已撤回</span>
          : e.result ? <span className={`pill ${e.result === "released" ? "ok" : "bad"}`}>{entryResultLabel(e.result)}</span>
          : null}
        <span className="pill">QA {qaStatusLabels[e.qa_status] ?? e.qa_status}</span>
      </div>
      <div className="panel-body">
        <div className="form form-compact">
          <label>目标版本<input className="input" value={e.target_version} disabled /></label>
          <label>app_info<input className="input" disabled
            value={record.app_info ? `${record.app_info.app_version} · ${record.app_info.commit_id.slice(0, 12)}` : "—"} /></label>
          <label>基础镜像<input className="input" disabled
            value={record.base_image ? `${record.base_image.name} ${record.base_image.digest}` : "—"} /></label>
          <label>交付镜像<input className="input mono" value={e.deliverable_image} disabled /></label>
          <label>Digest<input className="input mono" value={e.deliverable_digest} disabled /></label>
          <ChipSupportFields x86={record.app_info?.x86_chips ?? []} arm={record.app_info?.arm_chips ?? []} />
        </div>
        {(e.qa_note || e.known_issues) && (
          <p className="small mt-8">{e.qa_note && <>QA 备注：{e.qa_note}<br /></>}{e.known_issues && <>已知问题：{e.known_issues}</>}</p>
        )}
        <h4 className="mt-12">当月环境</h4>
        <div className="table">
          <table>
            <thead><tr><th>组件</th><th>版本</th><th>说明</th></tr></thead>
            <tbody>
              {record.environment.length === 0 && <tr><td colSpan={3} className="muted">未填写</td></tr>}
              {record.environment.map((row, i) => <tr key={i}><td>{row.component}</td><td>{row.version}</td><td>{row.note}</td></tr>)}
            </tbody>
          </table>
        </div>
        <h4 className="mt-12">测试范围与命令</h4>
        {record.test_commands.length ? <CommandTable commands={record.test_commands} /> : <TestScopeTable tests={record.scope_tests} />}
      </div>
    </div>
  );
}

export function LookupPage() {
  const [mode, setMode] = React.useState<"digest" | "app">("digest");
  const [image, setImage] = React.useState("");
  const [appId, setAppId] = React.useState("");
  const [imageResult, setImageResult] = React.useState<ImageResult | null>(null);
  const [appResult, setAppResult] = React.useState<AppResult | null>(null);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const requestId = React.useRef(0);

  const { data: state } = useQuery({
    queryKey: ["state"],
    queryFn: () => apiGet<StatePayload>("/api/state"),
    staleTime: Infinity,
    enabled: mode === "app",
  });
  const apps: App[] = [...(state?.apps ?? [])].sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));

  async function search() {
    if (busy) return;
    const currentRequest = ++requestId.current;
    setBusy(true);
    setError("");
    if (mode === "digest") setImageResult(null);
    else setAppResult(null);
    try {
      if (mode === "digest") {
        const reference = stripDockerPullPrefix(image).trim();
        setImage(reference);
        const result = await apiGet<ImageResult>(`/api/lookup/image?image=${encodeURIComponent(reference)}`);
        if (requestId.current === currentRequest) setImageResult(result);
      } else {
        const result = await apiGet<AppResult>(`/api/lookup/app?app_id=${encodeURIComponent(appId)}`);
        if (requestId.current === currentRequest) setAppResult(result);
      }
    } catch (e) {
      if (requestId.current === currentRequest) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (requestId.current === currentRequest) setBusy(false);
    }
  }

  function changeMode(next: "digest" | "app") {
    requestId.current++;
    setMode(next);
    setError("");
    setBusy(false);
  }

  const sourceUrl = imageResult ? `/api/lookup/app-info?${new URLSearchParams({
    image: imageResult.image, commit_id: imageResult.commit_id,
  })}` : null;

  return (
    <section className="view active" data-testid="lookup-page">
      <div className="page-toolbar">
        <h2>历史查询</h2>
      </div>
      <QaTabs />
      <div className="subtabs">
        <button className={`subtab${mode === "digest" ? " active" : ""}`} onClick={() => changeMode("digest")}>按镜像 digest</button>
        <button className={`subtab${mode === "app" ? " active" : ""}`} onClick={() => changeMode("app")}>按 App</button>
      </div>
      <div className="panel">
        <div className="panel-body">
          <div className="row">
            {mode === "digest" ? (
              <input className="input flex-1 mono" value={image} onChange={(e) => setImage(stripDockerPullPrefix(e.target.value))} disabled={busy}
                onKeyDown={(e) => e.key === "Enter" && image.trim() && void search()}
                placeholder="harbor.sh.mxcr.io/hpc-release/镜像名@sha256:…" aria-label="完整镜像名" data-testid="lookup-image-input" />
            ) : (
              <select className="select flex-1" value={appId} onChange={(e) => setAppId(e.target.value)} aria-label="选择 App">
                <option value="">请选择 App</option>
                {apps.map((app) => <option key={app.id} value={app.id}>{app.name}（{app.id}）</option>)}
              </select>
            )}
            <button className="btn primary" onClick={() => void search()} disabled={busy || (mode === "digest" ? !image.trim() : !appId)}
              data-testid="lookup-search">
              {busy ? "查询中…" : "查询"}
            </button>
          </div>
          {mode === "digest" && <p className="hint mt-8">输入包含 digest 的完整镜像名，查看该镜像对应的历史测试命令。</p>}
          {error && <p className="lerr" role="alert">{error}</p>}
        </div>
      </div>

      {mode === "digest" && imageResult && (
        <div className="panel" data-testid="lookup-image-result">
          <div className="panel-head">
            <h2>{imageResult.app_name} {imageResult.app_version}</h2>
            <span className="pill">Commit {imageResult.commit_id}</span>
            <span className="count">{imageResult.test_commands.length} 条测试命令</span>
          </div>
          <div className="panel-body">
            <p className="mono small break">{imageResult.image}</p>
            <p className="small break">app_info 来源：
              {sourceUrl ? (
                <a href={sourceUrl} target="_blank" rel="noopener noreferrer"
                  data-testid="app-info-source-link">
                  {imageResult.git_url} @ {imageResult.commit_id}
                </a>
              ) : <>{imageResult.git_url} @ {imageResult.commit_id}</>}
            </p>
            <p className="muted small">该镜像构建支持的芯片</p>
            <div className="form form-compact">
              <ChipSupportFields x86={imageResult.x86_chips} arm={imageResult.arm_chips} />
            </div>
            <details className="mt-8">
              <summary>镜像 tags（{imageResult.tags.length}）</summary>
              {imageResult.tags.map((tag) => <div key={tag} className="mono small break mt-4">{tag}</div>)}
            </details>
            <CommandTable commands={imageResult.test_commands} />
          </div>
        </div>
      )}
      {mode === "app" && appResult && (
        <>
          {appResult.entries.length === 0 && <div className="banner">{appResult.app_name} 还没有报名记录。</div>}
          {appResult.entries.map((record) => <RecordCard key={record.release.id} record={record} />)}
        </>
      )}
    </section>
  );
}
