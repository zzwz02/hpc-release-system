/**
 * 历史查询 — browse published Apps by frozen release or reconstruct tests
 * for an immutable Harbor image.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../api/http";
import { useAuth } from "../../api/AuthContext";
import { qaStatusLabels } from "../../lib/labels";
import { QaTabs } from "../qa/QaTabs";
import { ChipSupportFields } from "../../components/ChipSupportFields";
import { TestScopeTable } from "../../components/TestScopeTable";
import { RefreshBar } from "../../components/RefreshBar";
import { stripDockerPullPrefix } from "../../lib/containerImage";
import type { DeliveryImage, QaStatus, TestPlatform } from "../../types";
import { DeliveryImages } from "../../components/DeliveryImages";
import { recordedImages } from "../../lib/deliveryImages";

type TestCommand = TestPlatform & { test: string; command: string };

function CommandTable({ commands }: { commands: TestCommand[] }) {
  return <TestScopeTable tests={commands.map((command) => ({ path: command.test, command: command.command, platforms: [command] }))} />;
}

interface FrozenRelease {
  id: string;
  name: string;
  release_date: string;
}

interface PublishedApp {
  app_id: string;
  app_name: string;
  app_version: string;
  image_reference: string;
  deliverable_image: string;
  deliverable_digest: string;
  qa_status: QaStatus;
  qa_note: string;
  images?: DeliveryImage[];
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

function PublishedAppsPane() {
  const { user } = useAuth();
  const [selectedId, setSelectedId] = React.useState("");
  const [filter, setFilter] = React.useState("");
  const releases = useQuery({
    queryKey: ["lookup", user?.username, user?.role, "releases"],
    queryFn: () => apiGet<{ releases: FrozenRelease[] }>("/api/lookup/releases"),
    staleTime: Infinity,
    refetchOnMount: "always",
  });
  const options = releases.data?.releases ?? [];
  const releaseId = options.find((release) => release.id === selectedId)?.id ?? options[0]?.id ?? "";
  const result = useQuery({
    queryKey: ["lookup", user?.username, user?.role, "release", releaseId],
    queryFn: () => apiGet<{ release: FrozenRelease; apps: PublishedApp[] }>(`/api/lookup/release?release_id=${encodeURIComponent(releaseId)}`),
    enabled: !!releaseId && !releases.error,
    staleTime: Infinity,
    refetchOnMount: "always",
  });
  const error = releases.error ?? result.error;
  const needle = filter.trim().toLocaleLowerCase();
  const rows = (result.data?.apps ?? []).filter((app) => !needle || [
    app.app_name, app.app_id, app.app_version, app.image_reference,
    app.deliverable_image, app.deliverable_digest, JSON.stringify(app.images), qaStatusLabels[app.qa_status], app.qa_note,
  ].join(" ").toLocaleLowerCase().includes(needle));

  return (
    <div className="panel" data-testid="published-apps-pane">
      <div className="panel-body">
        <div className="row">
          <label htmlFor="lookup-release">发布版本</label>
          <select id="lookup-release" className="select select-inline" value={releaseId}
            disabled={releases.isFetching || options.length === 0}
            onChange={(e) => { setSelectedId(e.target.value); setFilter(""); }}>
            {options.length === 0 && <option value="">暂无已冻结版本</option>}
            {options.map((release) => <option key={release.id} value={release.id}>{release.name}</option>)}
          </select>
          <span className="spacer" />
          <RefreshBar dataUpdatedAt={result.dataUpdatedAt || releases.dataUpdatedAt}
            isFetching={releases.isFetching || result.isFetching}
            onRefresh={() => { void Promise.all([releases.refetch(), ...(releaseId ? [result.refetch()] : [])]); }} />
        </div>
        <p className="hint mt-8">仅展示已发布并冻结的版本，以及该版本发布成功的 App。</p>
        {error && <p className="lerr" role="alert">{error.message}</p>}
        {(releases.isFetching || result.isFetching) && <p className="muted" role="status">加载中…</p>}
        {!error && !releases.isFetching && options.length === 0 && <div className="banner">暂无已冻结版本。</div>}
      </div>
      {!error && !releases.isFetching && !result.isFetching && result.data && (
        <>
          <div className="panel-head">
            <h2>{result.data.release.name} 发布 App</h2>
            <span className="count">{needle ? `${rows.length} / ${result.data.apps.length}` : rows.length} 个 App</span>
            <input className="input flex-1" value={filter} onChange={(e) => setFilter(e.target.value)}
              aria-label="筛选发布 App" placeholder="筛选 App、镜像或 QA 结果…" />
          </div>
          <div className="table" data-testid="published-apps-table">
            <table className="published-apps-table">
              <thead><tr><th>App</th><th>App 版本</th><th>完整镜像名（含 digest）</th><th>QA 测试结果</th><th>QA 备注</th></tr></thead>
              <tbody>
                {rows.length === 0 && <tr><td colSpan={5} className="muted">{result.data.apps.length ? "没有匹配的 App。" : "该版本没有发布成功的 App。"}</td></tr>}
                {rows.map((app) => (
                  <tr key={app.app_id} data-testid="published-app-row">
                    <td>{app.app_name}<div className="small muted">{app.app_id}</div></td>
                    <td>{app.app_version || "—"}</td>
                    <td className="mono small break">
                      <DeliveryImages compact images={recordedImages(app.images, app.deliverable_image, app.deliverable_digest)} />
                    </td>
                    <td><span className={`pill ${app.qa_status === "qa_passed" ? "ok" : "warn"}`}>{qaStatusLabels[app.qa_status] ?? app.qa_status}</span></td>
                    <td className="small break">{app.qa_note || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export function LookupPage() {
  const [mode, setMode] = React.useState<"releases" | "digest">("releases");
  const [image, setImage] = React.useState("");
  const [imageResult, setImageResult] = React.useState<ImageResult | null>(null);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const requestId = React.useRef(0);

  async function search() {
    if (busy) return;
    const currentRequest = ++requestId.current;
    setBusy(true);
    setError("");
    setImageResult(null);
    try {
      const reference = stripDockerPullPrefix(image).trim();
      setImage(reference);
      const result = await apiGet<ImageResult>(`/api/lookup/image?image=${encodeURIComponent(reference)}`);
      if (requestId.current === currentRequest) setImageResult(result);
    } catch (e) {
      if (requestId.current === currentRequest) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (requestId.current === currentRequest) setBusy(false);
    }
  }

  function changeMode(next: "digest" | "releases") {
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
        <button className={`subtab${mode === "releases" ? " active" : ""}`} onClick={() => changeMode("releases")}>发布app列表</button>
        <button className={`subtab${mode === "digest" ? " active" : ""}`} onClick={() => changeMode("digest")}>按镜像 digest</button>
      </div>
      {mode === "releases" && <PublishedAppsPane />}
      {mode === "digest" && (
        <div className="panel">
          <div className="panel-body">
            <div className="row">
              <input className="input flex-1 mono" value={image} onChange={(e) => setImage(stripDockerPullPrefix(e.target.value))} disabled={busy}
                onKeyDown={(e) => e.key === "Enter" && image.trim() && void search()}
                placeholder="harbor.sh.mxcr.io/hpc-release/镜像名@sha256:…" aria-label="完整镜像名" data-testid="lookup-image-input" />
              <button className="btn primary" onClick={() => void search()} disabled={busy || !image.trim()}
                data-testid="lookup-search">
                {busy ? "查询中…" : "查询"}
              </button>
            </div>
            <p className="hint mt-8">输入包含 digest 的完整镜像名，查看该镜像对应的历史测试命令。</p>
            {error && <p className="lerr" role="alert">{error}</p>}
          </div>
        </div>
      )}

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
    </section>
  );
}
