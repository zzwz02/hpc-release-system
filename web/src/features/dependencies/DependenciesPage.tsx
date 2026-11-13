/** Official compatibility snapshots and a separate development dependency reference. */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet } from "../../api/http";
import { RefreshBar } from "../../components/RefreshBar";
import { CompatibilityPane } from "./CompatibilityPane";
import { QaTabs } from "../qa/QaTabs";

interface DependencyApp {
  app_id: string;
  name: string;
  owners: string[];
  dependencies: string[];
  app_info_version_id: number | null;
  commit_id: string;
  app_version: string;
}

interface DependencyOverview {
  components: Array<{ component: string; apps: DependencyApp[] }>;
  undeclared: DependencyApp[];
}

function AppsTable({ apps, testId }: { apps: DependencyApp[]; testId?: string }) {
  return (
    <div className="table" data-testid={testId}><table><thead><tr><th>App</th><th>Owner</th><th>开发版 app_info</th><th>依赖组件</th></tr></thead>
      <tbody>{apps.map((app) => <tr key={app.app_id} data-testid={`dependency-app-${app.app_id}`}>
        <td>{app.name}<div className="small muted">{app.app_id}</div></td><td>{app.owners.join(", ")}</td>
        <td>{app.app_version || "—"}<div className="small muted" title={app.commit_id}>{app.commit_id.slice(0, 12)}</div></td>
        <td>{app.dependencies.join(", ") || "未声明"}</td>
      </tr>)}</tbody></table></div>
  );
}

function DevelopmentDependenciesPane() {
  const { user } = useAuth();
  const [component, setComponent] = React.useState("");
  const { data, isFetching, dataUpdatedAt, refetch, error } = useQuery({
    queryKey: ["dependencies", user?.username, user?.role, "development"],
    queryFn: () => apiGet<DependencyOverview>("/api/dependencies?source=development"),
    staleTime: Infinity,
    refetchOnMount: "always",
  });
  const groups = (data?.components ?? []).filter((group) => !component || group.component === component);
  return <>
    <div className="page-toolbar">
      <select className="select select-inline" value={component} onChange={(event) => setComponent(event.target.value)} aria-label="按组件筛选">
        <option value="">全部组件</option>
        {(data?.components ?? []).map((group) => <option key={group.component} value={group.component}>{group.component}（{group.apps.length}）</option>)}
      </select>
      <span className="spacer" />
      <RefreshBar dataUpdatedAt={dataUpdatedAt} isFetching={isFetching} onRefresh={() => void refetch()} />
    </div>
    <p className="hint">开发态依赖来自最新 app_info，仅供开发参考，不包含正式版镜像、命令，也不用于兼容验证清单。</p>
    {error && <div className="lerr p-12-16" role="alert">加载失败：{error.message}</div>}
    {isFetching && <p className="muted" role="status">加载中…</p>}
    {!error && !isFetching && data && <>
      {groups.length === 0 && <div className="banner">暂无匹配的组件或 PyTorch + Python 组合依赖。</div>}
      {groups.map((group) => <details key={group.component} className="panel section dependency-group" data-testid={`dependency-group-${group.component}`}>
        <summary className="panel-head"><span className="chev" aria-hidden="true">▶</span><h2>{group.component}</h2><span className="count">{group.apps.length} 个 App</span></summary>
        <div className="panel-body p-0"><AppsTable apps={group.apps} testId={`component-${group.component}`} /></div>
      </details>)}
      {!component && <details className="panel section" data-testid="undeclared-apps">
        <summary className="panel-head"><h2>未声明组件依赖的 App</h2><span className="count">{data.undeclared.length} 个</span></summary>
        <div className="panel-body p-0"><AppsTable apps={data.undeclared} /></div>
      </details>}
    </>}
  </>;
}

export function DependenciesPage() {
  const [source, setSource] = React.useState<"official" | "development">("official");
  return <section className="view active" data-testid="dependencies-page">
    <div className="page-toolbar"><h2>兼容性验证</h2></div>
    <QaTabs />
    <div className="subtabs" role="group" aria-label="验证与参考">
      <button type="button" className={`subtab${source === "official" ? " active" : ""}`} aria-pressed={source === "official"}
        onClick={() => setSource("official")}>兼容性验证</button>
      <button type="button" className={`subtab${source === "development" ? " active" : ""}`} aria-pressed={source === "development"}
        onClick={() => setSource("development")}>开发态依赖</button>
    </div>
    {source === "official" ? <CompatibilityPane /> : <DevelopmentDependenciesPane />}
  </section>;
}
