import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet } from "../../api/http";
import { RefreshBar } from "../../components/RefreshBar";
import { downloadCsv } from "../../lib/csv";
import { publicationManifestSourceLabel, universalComponentMetadata, type PublicationManifestSource } from "../../lib/domainMetadata";

interface ReleaseRow {
  app_id: string;
  app_name: string;
  owners: string[];
  dependencies: string[];
  scope_pending: boolean;
  hpc_version: string;
  app_version: string;
  app_info_commit_id: string;
  manifest_source: PublicationManifestSource;
  os?: string;
  arch?: string;
  chips?: string[];
  GPU?: string;
  image_target?: string;
  python_label?: string;
  pytorch_label?: string;
  delivery_reference?: string;
  path?: string;
  command?: string;
  note: string;
}

const columns = ["App", "app_id", "Owner", "依赖组件", "HPC version", "App version", "发布记录来源", "OS", "CPU 架构", "GPU", "镜像类型", "Python label", "PyTorch label", "完整镜像名", "镜像 app_info commit id", "测试项", "测试命令", "说明"];
const archName = (arch?: string) => arch === "amd64" ? "x86" : arch === "arm64" ? "ARM" : arch || "";
const universalComponents = new Set(Object.keys(universalComponentMetadata));
function cells(row: ReleaseRow): string[] {
  return [row.app_name, row.app_id, row.owners.join(", "), row.dependencies.join(", "),
    row.hpc_version, row.app_version, publicationManifestSourceLabel(row.manifest_source), row.os || "", archName(row.arch), row.GPU || "", row.image_target || "",
    row.python_label || "", row.pytorch_label || "", row.delivery_reference || "", row.app_info_commit_id, row.path || "", row.command || "", row.note];
}

function AppRows({ rows }: { rows: ReleaseRow[] }) {
  const [expanded, setExpanded] = React.useState(false);
  const detailId = React.useId();
  const app = rows[0];
  const imageCount = new Set(rows.map((row) => row.delivery_reference).filter(Boolean)).size;
  const testCount = new Set(rows.filter((row) => row.command).map((row) => row.path)).size;
  const commandCount = rows.filter((row) => row.command).length;
  const versions = [...new Set(rows.map((row) => row.app_version).filter(Boolean))];
  const dependencies = [...new Set(rows.flatMap((row) => row.dependencies))];
  return <>
    <tr data-testid={`compat-app-${app.app_id}`}>
      <td><button type="button" className="dependency-app-toggle" aria-expanded={expanded} aria-controls={detailId}
        aria-label={`${expanded ? "收起" : "展开"} ${app.app_name} 的镜像和测试命令`} onClick={() => setExpanded(!expanded)}>
        <span className={`chev${expanded ? " open" : ""}`} aria-hidden="true">▶</span>
        <span>{app.app_name}</span>
      </button></td>
      <td>{app.app_id}</td>
      <td>{app.owners.join(", ") || "—"}</td>
      <td>{versions.join(", ") || "—"}</td><td>{app.hpc_version || "—"}</td>
      <td className="small">{dependencies.join(", ") || "未声明"}</td>
      <td>{imageCount} 个镜像</td><td>{testCount} 个测试项 / {commandCount} 条命令</td>
    </tr>
    {expanded && <tr id={detailId} data-testid={`compat-detail-${app.app_id}`}><td colSpan={8}>
      <div className="table"><table className="compatibility-detail-table">
        <thead><tr><th>OS</th><th>CPU 架构</th><th>GPU</th><th>镜像配置</th><th>交付镜像:tag@digest</th><th title="该交付镜像对应的 app_info commit id">commit id</th><th>测试项</th><th>测试命令</th><th>说明</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>
          <td>{row.os || "—"}</td><td>{archName(row.arch) || "—"}</td><td>{row.GPU || "—"}</td>
          <td>{[row.image_target, row.python_label, row.pytorch_label].filter(Boolean).join(" · ") || "—"}</td>
          <td className="mono small break">{row.delivery_reference || "—"}</td>
          <td className="mono small" title={row.app_info_commit_id} data-testid="compat-app-info-commit">{row.app_info_commit_id?.slice(0, 12) || "—"}</td>
          <td className="mono small">{row.path || "—"}</td>
          <td><div className="mono small cell-wrap" data-testid="compat-command">{row.command || "—"}</div></td>
          <td className="small break cell-wrap">{row.note || "—"}</td>
        </tr>)}</tbody>
      </table></div>
    </td></tr>}
  </>;
}

function AppsTable({ rows }: { rows: ReleaseRow[] }) {
  const apps = new Map<string, ReleaseRow[]>();
  for (const row of rows) {
    if (!apps.has(row.app_id)) apps.set(row.app_id, []);
    apps.get(row.app_id)!.push(row);
  }
  return <div className="table"><table className="compatibility-apps-table">
    <thead><tr><th>App</th><th>app_id</th><th>Owner</th><th>App 版本</th><th>来源 HPC 发布周期</th><th>依赖组件</th><th>镜像数</th><th>测试项数 / 命令数</th></tr></thead>
    <tbody>{[...apps].map(([id, items]) => <AppRows key={id} rows={items} />)}</tbody>
  </table></div>;
}

const platformFilters = [
  ["arch", "CPU 架构"], ["os", "OS"], ["chip", "GPU 芯片"],
] as const;
type FilterKey = typeof platformFilters[number][0];

export function CompatibilityPane() {
  const { user } = useAuth();
  const [filter, setFilter] = React.useState("");
  const [component, setComponent] = React.useState("");
  const [platform, setPlatform] = React.useState<Record<FilterKey, string>>({ arch: "", os: "", chip: "" });
  const result = useQuery({
    queryKey: ["lookup", user?.username, user?.role, "latest-running"],
    queryFn: () => apiGet<{ app_count: number; rows: ReleaseRow[] }>("/api/lookup/latest-running"),
    staleTime: Infinity,
    refetchOnMount: "always",
  });
  const inventory = result.data?.rows ?? [];
  const components = new Map<string, Set<string>>();
  for (const name of Object.keys(universalComponentMetadata)) {
    components.set(name, new Set(inventory.map((row) => row.app_id)));
  }
  for (const row of inventory) for (const name of row.dependencies) {
    if (universalComponents.has(name.toLowerCase())) continue;
    if (!components.has(name)) components.set(name, new Set());
    components.get(name)!.add(row.app_id);
  }
  const needle = filter.trim().toLocaleLowerCase();
  const rows = inventory.filter((row) =>
    (!component || universalComponents.has(component) || row.dependencies.includes(component))
    && platformFilters.every(([key]) => !platform[key] || (key === "chip" ? row.chips?.includes(platform[key]) : row[key] === platform[key]))
    && (!needle || [row.app_id, ...cells(row)].join(" ").toLocaleLowerCase().includes(needle)));
  const groups = [...components.keys()].sort((a, b) => a.localeCompare(b))
    .filter((name) => !component || name === component)
    .map((name) => ({ name, rows: universalComponents.has(name) ? rows : rows.filter((row) => row.dependencies.includes(name)) }))
    .filter((group) => group.rows.length > 0);
  return <div className="panel" data-testid="compatibility-pane">
    <div className="panel-head">
      <h2>测试 App 清单</h2>
      {result.data && <span className="count">{new Set(rows.map((row) => row.app_id)).size} / {result.data.app_count} 个 App</span>}
      <span className="spacer" />
      <RefreshBar dataUpdatedAt={result.dataUpdatedAt} isFetching={result.isFetching} onRefresh={() => void result.refetch()} />
    </div>
    <div className="panel-body">
      <p className="hint">仅显示 CICD 仍在运行，且成功发布过一次的app。每个 App 选最近一次有效成功发布，并排除最新 app_info 中 enabled=false 的构建。命令使用最新发布版本的 QA report 交付镜像</p>
      <div className="row compatibility-filters">
        <label>组件<select className="select select-inline" value={component} onChange={(event) => setComponent(event.target.value)} aria-label="按组件筛选">
          <option value="">全部组件</option>
          {[...components].sort(([a], [b]) => a.localeCompare(b)).map(([name, apps]) => <option key={name} value={name}>{universalComponentMetadata[name]?.label ?? name}（{apps.size}）</option>)}
        </select></label>
        {platformFilters.map(([key, label]) => {
          const values = [...new Set(inventory.flatMap((row) => key === "chip" ? row.chips ?? [] : [row[key] ?? ""]).filter(Boolean))].sort();
          return <label key={key}>{label}<select className="select select-inline" value={platform[key]}
            onChange={(event) => setPlatform({ ...platform, [key]: event.target.value })} aria-label={`按${label}筛选`}>
            <option value="">全部</option>{values.map((value) => <option key={value} value={value}>{key === "arch" ? archName(value) : value}</option>)}
          </select></label>;
        })}
      </div>
      <div className="row mt-8 compatibility-search">
        <input className="input flex-1" value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="筛选兼容性验证"
          placeholder="筛选 App、Owner、HPC 版本、digest、commit id 或命令…" />
        <button type="button" className="btn sm" disabled={!result.data || result.isFetching || !!result.error}
          onClick={() => downloadCsv("compatibility.csv", columns, rows.map(cells))}>导出当前测试清单 CSV</button>
        <button type="button" className="btn sm" onClick={() => {
          setFilter(""); setComponent(""); setPlatform({ arch: "", os: "", chip: "" });
        }}>清除筛选</button>
      </div>
      {result.error && <p className="lerr" role="alert">{result.error.message}</p>}
      {result.isFetching && <p className="muted" role="status">加载中…</p>}
    </div>
    {!result.error && !result.isFetching && result.data && <>
      {rows.length === 0 && <p className="muted p-12-16">{result.data.app_count ? "没有匹配的记录。" : "暂无 CICD 仍在运行且成功发布过的 App。"}</p>}
      {groups.map((group) => <details key={group.name} className="panel section dependency-group"
        open={!!component} data-testid={`compatibility-group-${group.name}`}>
        <summary className="panel-head"><span className="chev" aria-hidden="true">▶</span><h3>{universalComponentMetadata[group.name]?.label ?? group.name}</h3>
          <span className="count">{new Set(group.rows.map((row) => row.app_id)).size} 个 App</span></summary>
        <div className="panel-body p-0"><AppsTable rows={group.rows} /></div>
      </details>)}
    </>}
  </div>;
}
