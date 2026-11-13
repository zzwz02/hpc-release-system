/** Official compatibility snapshots and a separate development dependency reference. */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet } from "../../api/http";
import { RefreshBar } from "../../components/RefreshBar";
import { DeliveryImages } from "../../components/DeliveryImages";
import { recordedImages } from "../../lib/deliveryImages";
import type { DeliveryImage } from "../../types";
import { QaTabs } from "../qa/QaTabs";

interface DependencyApp {
  app_id: string;
  name: string;
  owners: string[];
  dependencies: string[];
  app_info_version_id: number | null;
  commit_id: string;
  app_version: string;
  official: { release_name: string; image: string; digest: string; images: DeliveryImage[] } | null;
  tests: Array<{
    path: string; command: string; os: string; arch: string; image_target: string;
    python_label: string; pytorch_label: string; chips: string[]; delivery_reference: string;
  }>;
}

interface DependencyOverview {
  components: Array<{ component: string; apps: DependencyApp[] }>;
  undeclared: DependencyApp[];
  snapshot_errors: string[];
}

function AppRow({ app }: { app: DependencyApp }) {
  const [expanded, setExpanded] = React.useState(false);
  const detailId = React.useId();
  const images = recordedImages(app.official?.images, app.official?.image, app.official?.digest);

  return (
    <tr data-testid={`dependency-app-${app.app_id}`}>
      <td>
        <button type="button" className="dependency-app-toggle" aria-expanded={expanded}
          aria-controls={`${detailId}-images ${detailId}-commands`}
          aria-label={`${expanded ? "收起" : "展开"} ${app.name} 的镜像和测试命令`}
          onClick={() => setExpanded((value) => !value)}>
          <span className={`chev${expanded ? " open" : ""}`} aria-hidden="true">▶</span>
          <span>{app.name}<span className="small muted dependency-app-id">{app.app_id}</span></span>
        </button>
      </td>
      <td className="small">{app.owners.join(", ")}</td>
      <td data-testid="dependency-release">{app.official?.release_name || <span className="muted">无</span>}</td>
      <td>
        {!expanded && <span className="small muted">{images.length} 个镜像</span>}
        <div id={`${detailId}-images`} hidden={!expanded}>
          <DeliveryImages compact images={images} />
        </div>
      </td>
      <td className="small">
        {!expanded && <span className="muted">{app.tests.length} 条命令</span>}
        <div id={`${detailId}-commands`} hidden={!expanded}>
          {app.tests.length === 0 ? "—" : (
            <div data-testid="dependency-test-commands">
              {app.tests.map((test, index) => (
                <div key={`${test.path}-${index}`} className="dependency-command">
                  <b className="mono">{test.path}</b>
                  <div className="muted small">
                    {[test.os, test.arch === "amd64" ? "x86" : test.arch === "arm64" ? "ARM" : test.arch,
                      test.image_target, test.python_label, test.pytorch_label, test.chips.join(", ")].filter(Boolean).join(" · ")}
                  </div>
                  {!test.delivery_reference && <div className="muted small">未填写对应交付镜像，命令使用镜像模板。</div>}
                  <div className="mono cell-wrap" data-testid="dependency-docker-command">{test.command}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

function AppsTable({ apps, testId, development = false }: { apps: DependencyApp[]; testId?: string; development?: boolean }) {
  if (development) return (
    <div className="table" data-testid={testId}><table><thead><tr><th>App</th><th>Owner</th><th>开发版 app_info</th><th>依赖组件</th></tr></thead>
      <tbody>{apps.map((app) => <tr key={app.app_id} data-testid={`dependency-app-${app.app_id}`}>
        <td>{app.name}<div className="small muted">{app.app_id}</div></td><td>{app.owners.join(", ")}</td>
        <td>{app.app_version || "—"}<div className="small muted" title={app.commit_id}>{app.commit_id.slice(0, 12)}</div></td>
        <td>{app.dependencies.join(", ") || "未声明"}</td>
      </tr>)}</tbody></table></div>
  );
  return (
    <div className="table" data-testid={testId}>
      <table className="dependencies-table">
        <thead>
          <tr><th>App</th><th>Owner</th><th>最新hpc release版本</th><th>交付镜像 / Digest</th><th>测试命令</th></tr>
        </thead>
        <tbody>
          {apps.map((app) => <AppRow key={app.app_id} app={app} />)}
        </tbody>
      </table>
    </div>
  );
}

export function DependenciesPage() {
  const { user } = useAuth();
  const [source, setSource] = React.useState<"official" | "development">("official");
  const [component, setComponent] = React.useState("");
  const { data, isFetching, dataUpdatedAt, refetch, error } = useQuery({
    queryKey: ["dependencies", user?.username, user?.role, source],
    queryFn: () => apiGet<DependencyOverview>(`/api/dependencies?source=${source}`),
    staleTime: Infinity,
    refetchOnMount: true,
  });

  const groups = (data?.components ?? []).filter((g) => !component || g.component === component);

  return (
    <section className="view active" data-testid="dependencies-page">
      <div className="page-toolbar">
        <h2>组件依赖</h2>
        <select className="select select-inline" value={component} onChange={(e) => setComponent(e.target.value)}
          aria-label="按组件筛选">
          <option value="">全部组件</option>
          {(data?.components ?? []).map((g) => (
            <option key={g.component} value={g.component}>{g.component}（{g.apps.length}）</option>
          ))}
        </select>
        {source === "official" && !data?.snapshot_errors.length && <a className="btn sm" href={`/api/dependencies/compat.csv${component ? `?component=${encodeURIComponent(component)}` : ""}`}
          data-testid="compat-csv-link">
          导出兼容验证清单 CSV
        </a>}
        <span className="spacer" />
        <RefreshBar dataUpdatedAt={dataUpdatedAt} isFetching={isFetching} onRefresh={() => void refetch()} />
      </div>
      <QaTabs />
      <div className="subtabs" role="group" aria-label="依赖数据来源">
        {(["official", "development"] as const).map((value) => <button key={value} type="button"
          className={`subtab${source === value ? " active" : ""}`} aria-pressed={source === value}
          onClick={() => { setSource(value); setComponent(""); }}>
          {value === "official" ? "正式版兼容验证" : "开发态依赖"}
        </button>)}
      </div>
      {error && <div className="lerr p-12-16">加载失败：{(error as Error).message}</div>}
      {data && (
        <>
          <p className="hint">
            {source === "official"
              ? "依赖、交付镜像和测试命令统一来自最新 HPC release 的同一正式版 app_info 快照；仅列运行中且已发布的 App。"
              : "开发态依赖来自最新 app_info，仅供开发参考，不包含正式版镜像、命令，也不用于兼容验证清单。"}
          </p>
          {data.snapshot_errors.map((message) => <div key={message} className="lerr p-12-16" role="alert">{message}；已暂停兼容清单导出。</div>)}
          {groups.length === 0 && <div className="banner">暂无匹配的组件或 PyTorch + Python 组合依赖。</div>}
          {groups.map((group) => (
            <details key={`${source}-${group.component}`} className="panel section dependency-group" data-testid={`dependency-group-${group.component}`}>
              <summary className="panel-head">
                <span className="chev" aria-hidden="true">▶</span>
                <h2>{group.component}</h2>
                <span className="count">{group.apps.length} 个 App</span>
              </summary>
              <div className="panel-body p-0">
                <AppsTable development={source === "development"} apps={group.apps} testId={`component-${group.component}`} />
              </div>
            </details>
          ))}
          {!component && (
            <details className="panel section" data-testid="undeclared-apps">
              <summary className="panel-head">
                <h2>未声明组件依赖的 App</h2>
                <span className="count">{data.undeclared.length} 个</span>
              </summary>
              <div className="panel-body p-0">
                <AppsTable development={source === "development"} apps={data.undeclared} />
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}
