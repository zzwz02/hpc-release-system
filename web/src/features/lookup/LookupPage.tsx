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
import { RefreshBar } from "../../components/RefreshBar";
import type { DeliveryImage, QaStatus } from "../../types";
import { DeliveryImages } from "../../components/DeliveryImages";
import { recordedImages } from "../../lib/deliveryImages";
import { BatchImageLookupPane } from "./BatchImageLookupPane";

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
              <thead><tr><th>App</th><th>app_id</th><th>App 版本</th><th>完整镜像名（含 digest）</th><th>QA 测试结果</th><th>QA 备注</th></tr></thead>
              <tbody>
                {rows.length === 0 && <tr><td colSpan={6} className="muted">{result.data.apps.length ? "没有匹配的 App。" : "该版本没有发布成功的 App。"}</td></tr>}
                {rows.map((app) => (
                  <tr key={app.app_id} data-testid="published-app-row">
                    <td>{app.app_name}</td>
                    <td>{app.app_id}</td>
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
  const { user } = useAuth();
  const [mode, setMode] = React.useState<"releases" | "digest">("releases");
  return (
    <section className="view active" data-testid="lookup-page">
      <div className="page-toolbar"><h2>历史查询</h2></div>
      <QaTabs />
      <div className="subtabs">
        <button className={`subtab${mode === "releases" ? " active" : ""}`} onClick={() => setMode("releases")}>发布app列表</button>
        <button className={`subtab${mode === "digest" ? " active" : ""}`} onClick={() => setMode("digest")}>按镜像 digest</button>
      </div>
      {mode === "releases" && <PublishedAppsPane />}
      {mode === "digest" && <BatchImageLookupPane key={`${user?.username}:${user?.role}`} />}
    </section>
  );
}
