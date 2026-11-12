/**
 * 组件依赖 — Apps grouped by the components they declare in app_info
 * (`app_dependency`), with each App's current official version, delivered
 * digest and test commands.  Running Apps without a declaration are listed
 * separately so they can be chased.  The compatibility list (running Apps
 * with an official version) exports as CSV for QA.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../api/http";
import { RefreshBar } from "../../components/RefreshBar";
import { QaTabs } from "../qa/QaTabs";

interface DependencyApp {
  app_id: string;
  name: string;
  owners: string[];
  official: { release_name: string; version: string; image: string; digest: string } | null;
  tests: Array<{ path: string; command: string }>;
}

interface DependencyOverview {
  components: Array<{ component: string; apps: DependencyApp[] }>;
  undeclared: DependencyApp[];
}

function AppsTable({ apps, testId }: { apps: DependencyApp[]; testId?: string }) {
  return (
    <div className="table" data-testid={testId}>
      <table>
        <thead>
          <tr><th>App</th><th>Owner</th><th>当前正式版</th><th>交付镜像 / Digest</th><th>测试命令</th></tr>
        </thead>
        <tbody>
          {apps.map((app) => (
            <tr key={app.app_id}>
              <td>{app.name}<div className="small muted">{app.app_id}</div></td>
              <td className="small">{app.owners.join(", ")}</td>
              <td>{app.official ? `${app.official.version}（${app.official.release_name}）` : <span className="muted">无</span>}</td>
              <td className="mono small break">
                {app.official?.image || "—"}
                {app.official?.digest && <div>{app.official.digest}</div>}
              </td>
              <td className="small">
                {app.tests.length === 0 ? "—" : (
                  <details>
                    <summary>{app.tests.length} 项</summary>
                    {app.tests.map((test) => (
                      <div key={test.path} className="mt-4">
                        <span className="mono">{test.path}</span>
                        <div className="mono break muted">{test.command}</div>
                      </div>
                    ))}
                  </details>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DependenciesPage() {
  const [component, setComponent] = React.useState("");
  const { data, isFetching, dataUpdatedAt, refetch, error } = useQuery({
    queryKey: ["dependencies"],
    queryFn: () => apiGet<DependencyOverview>("/api/dependencies"),
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
        <a className="btn sm" href={`/api/dependencies/compat.csv${component ? `?component=${encodeURIComponent(component)}` : ""}`}
          data-testid="compat-csv-link">
          导出兼容验证清单 CSV
        </a>
        <span className="spacer" />
        <RefreshBar dataUpdatedAt={dataUpdatedAt} isFetching={isFetching} onRefresh={() => void refetch()} />
      </div>
      <QaTabs />
      {error && <div className="lerr p-12-16">加载失败：{(error as Error).message}</div>}
      {data && (
        <>
          <p className="hint">
            依赖来自各 App 最新 app_info 的 <code>app_dependency</code> 字段；只列 CICD 运行中的 App。
            兼容验证清单 = 运行中且有当前正式版的 App。
          </p>
          {groups.length === 0 && <div className="banner">还没有 App 声明依赖组件。</div>}
          {groups.map((group) => (
            <div key={group.component} className="panel">
              <div className="panel-head">
                <h2>{group.component}</h2>
                <span className="count">{group.apps.length} 个 App</span>
              </div>
              <div className="panel-body p-0">
                <AppsTable apps={group.apps} testId={`component-${group.component}`} />
              </div>
            </div>
          ))}
          {!component && (
            <details className="panel section" data-testid="undeclared-apps">
              <summary className="panel-head">
                <h2>尚未声明依赖的 App</h2>
                <span className="count">{data.undeclared.length} 个</span>
              </summary>
              <div className="panel-body p-0">
                <AppsTable apps={data.undeclared} />
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}
