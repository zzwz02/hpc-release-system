/**
 * 组件依赖 — Apps grouped by the components they declare in app_info
 * (`app_dependency`) and paired PyTorch/Python build labels, with each App's current official version, delivered
 * digest and test commands.  Running Apps without a declaration are listed
 * separately so they can be chased.  The compatibility list (running Apps
 * with an official version) exports as CSV for QA.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
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
  official: { release_name: string; image: string; digest: string; images: DeliveryImage[] } | null;
  tests: Array<{
    path: string; command: string; os: string; arch: string; image_target: string;
    python_label: string; pytorch_label: string; chips: string[]; delivery_reference: string;
  }>;
}

interface DependencyOverview {
  components: Array<{ component: string; apps: DependencyApp[] }>;
  undeclared: DependencyApp[];
}

function AppsTable({ apps, testId }: { apps: DependencyApp[]; testId?: string }) {
  return (
    <div className="table" data-testid={testId}>
      <table className="dependencies-table">
        <thead>
          <tr><th>App</th><th>Owner</th><th>最新hpc release版本</th><th>交付镜像 / Digest</th><th>测试命令</th></tr>
        </thead>
        <tbody>
          {apps.map((app) => (
            <tr key={app.app_id} data-testid={`dependency-app-${app.app_id}`}>
              <td>{app.name}<div className="small muted">{app.app_id}</div></td>
              <td className="small">{app.owners.join(", ")}</td>
              <td data-testid="dependency-release">{app.official?.release_name || <span className="muted">无</span>}</td>
              <td>
                <DeliveryImages compact images={recordedImages(app.official?.images, app.official?.image, app.official?.digest)} />
              </td>
              <td className="small">
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
            依赖来自各 App 最新 app_info 的 <code>app_dependency</code> 字段，以及同一启用构建的 PyTorch label + Python label 组合；只列 CICD 运行中的 App。
            兼容验证清单 = 运行中且已发布 HPC release 的 App；镜像和命令按最新发布时的 OS、CPU 架构和构建配置展示。
          </p>
          {groups.length === 0 && <div className="banner">暂无匹配的组件或 PyTorch + Python 组合依赖。</div>}
          {groups.map((group) => (
            <details key={group.component} className="panel section dependency-group" data-testid={`dependency-group-${group.component}`}>
              <summary className="panel-head">
                <span className="chev" aria-hidden="true">▶</span>
                <h2>{group.component}</h2>
                <span className="count">{group.apps.length} 个 App</span>
              </summary>
              <div className="panel-body p-0">
                <AppsTable apps={group.apps} testId={`component-${group.component}`} />
              </div>
            </details>
          ))}
          {!component && (
            <details className="panel section" data-testid="undeclared-apps">
              <summary className="panel-head">
                <h2>尚未声明 app_dependency 的 App</h2>
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
