/**
 * 历史查询 — find the cycle, environment, test scope and test commands behind a
 * delivered image digest, or an App's release history.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../api/http";
import { qaStatusLabels } from "../../lib/labels";
import { entryResultLabel, releasePhaseLabel } from "../../lib/domainMetadata";
import type { App, BaseImage, EnvironmentRow, QaStatus, StatePayload } from "../../types";

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
  scope_tests: Array<{ path: string; command: string; supported_chips: string[] }>;
  test_commands: Array<{ arch: string; test: string; command: string }>;
}

interface DigestResult {
  digest: string;
  entries: LookupRecord[];
  base_images: Array<BaseImage & { release_name: string }>;
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
        <div className="table">
          <table>
            <thead><tr><th>测试项</th><th>Arch</th><th>命令</th></tr></thead>
            <tbody>
              {record.test_commands.length === 0 && record.scope_tests.map((t) => (
                <tr key={t.path}><td className="mono small">{t.path}</td><td /><td className="mono small break">{t.command}</td></tr>
              ))}
              {record.test_commands.map((t, i) => (
                <tr key={i}><td className="mono small">{t.test}</td><td>{t.arch}</td><td className="mono small break">{t.command}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export function LookupPage() {
  const [mode, setMode] = React.useState<"digest" | "app">("digest");
  const [digest, setDigest] = React.useState("");
  const [appId, setAppId] = React.useState("");
  const [digestResult, setDigestResult] = React.useState<DigestResult | null>(null);
  const [appResult, setAppResult] = React.useState<AppResult | null>(null);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const { data: state } = useQuery({
    queryKey: ["state"],
    queryFn: () => apiGet<StatePayload>("/api/state"),
    staleTime: Infinity,
    enabled: mode === "app",
  });
  const apps: App[] = [...(state?.apps ?? [])].sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));

  async function search() {
    setBusy(true);
    setError("");
    try {
      if (mode === "digest") {
        setDigestResult(await apiGet<DigestResult>(`/api/lookup/digest?digest=${encodeURIComponent(digest.trim())}`));
      } else {
        setAppResult(await apiGet<AppResult>(`/api/lookup/app?app_id=${encodeURIComponent(appId)}`));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="view active" data-testid="lookup-page">
      <div className="page-toolbar">
        <h2>历史查询</h2>
      </div>
      <div className="subtabs">
        <button className={`subtab${mode === "digest" ? " active" : ""}`} onClick={() => setMode("digest")}>按镜像 digest</button>
        <button className={`subtab${mode === "app" ? " active" : ""}`} onClick={() => setMode("app")}>按 App</button>
      </div>
      <div className="panel">
        <div className="panel-body">
          <div className="row">
            {mode === "digest" ? (
              <input className="input flex-1 mono" value={digest} onChange={(e) => setDigest(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && digest.trim() && void search()}
                placeholder="sha256:…" aria-label="镜像 digest" data-testid="lookup-digest-input" />
            ) : (
              <select className="select flex-1" value={appId} onChange={(e) => setAppId(e.target.value)} aria-label="选择 App">
                <option value="">请选择 App</option>
                {apps.map((app) => <option key={app.id} value={app.id}>{app.name}（{app.id}）</option>)}
              </select>
            )}
            <button className="btn primary" onClick={() => void search()} disabled={busy || (mode === "digest" ? !digest.trim() : !appId)}
              data-testid="lookup-search">
              查询
            </button>
          </div>
          {error && <p className="lerr">{error}</p>}
        </div>
      </div>

      {mode === "digest" && digestResult && (
        <>
          {digestResult.entries.length === 0 && digestResult.base_images.length === 0 && (
            <div className="banner">没有找到 digest 为 {digestResult.digest} 的交付镜像或基础镜像。</div>
          )}
          {digestResult.base_images.map((image) => (
            <div key={image.id} className="banner">该 digest 是「{image.release_name}」的基础镜像 {image.name}（{image.image}）。</div>
          ))}
          {digestResult.entries.map((record) => <RecordCard key={`${record.release.id}-${record.app_id}`} record={record} />)}
        </>
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
