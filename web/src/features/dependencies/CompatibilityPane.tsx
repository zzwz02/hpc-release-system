import React from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet } from "../../api/http";
import { RefreshBar } from "../../components/RefreshBar";
import { downloadCsv } from "../../lib/csv";

interface ReleaseRow {
  app_id: string;
  app_name: string;
  owners: string[];
  dependencies: string[];
  scope_pending: boolean;
  hpc_version: string;
  app_version: string;
  app_info_commit_id: string;
  os?: string;
  arch?: string;
  chips?: string[];
  image_target?: string;
  python_label?: string;
  pytorch_label?: string;
  delivery_reference?: string;
  path?: string;
  command?: string;
  note: string;
}

const columns = ["App", "Owner", "依赖组件", "范围状态", "HPC version", "App version", "OS", "CPU 架构", "支持芯片", "镜像类型", "Python label", "PyTorch label", "完整镜像名", "app_info commit id", "测试项", "测试命令", "说明"];
const archName = (arch?: string) => arch === "amd64" ? "x86" : arch === "arm64" ? "ARM" : arch || "";
function cells(row: ReleaseRow): string[] {
  return [row.app_name, row.owners.join(", "), row.dependencies.join(", "), row.scope_pending ? "范围待确认" : "已声明",
    row.hpc_version, row.app_version, row.os || "", archName(row.arch), (row.chips || []).join(", "), row.image_target || "",
    row.python_label || "", row.pytorch_label || "", row.delivery_reference || "", row.app_info_commit_id, row.path || "", row.command || "", row.note];
}

function AppRows({ rows }: { rows: ReleaseRow[] }) {
  const [expanded, setExpanded] = React.useState(false);
  const detailId = React.useId();
  const app = rows[0];
  const imageCount = new Set(rows.map((row) => row.delivery_reference).filter(Boolean)).size;
  const testCount = new Set(rows.filter((row) => row.command).map((row) => row.path)).size;
  const commandCount = rows.filter((row) => row.command).length;
  return <>
    <tr data-testid={`compat-app-${app.app_id}`}>
      <td><button type="button" className="dependency-app-toggle" aria-expanded={expanded} aria-controls={detailId}
        aria-label={`${expanded ? "收起" : "展开"} ${app.app_name} 的镜像和测试命令`} onClick={() => setExpanded(!expanded)}>
        <span className={`chev${expanded ? " open" : ""}`} aria-hidden="true">▶</span>
        <span>{app.app_name}<span className="small muted dependency-app-id">{app.app_id}</span></span>
      </button></td>
      <td>{app.owners.join(", ") || "—"}</td>
      <td>{app.app_version || "—"}</td><td>{app.hpc_version || "—"}</td>
      <td className="small">{app.dependencies.join(", ") || "未声明"}</td>
      <td>{imageCount} 个镜像</td><td>{testCount} 个测试项 / {commandCount} 条命令</td>
    </tr>
    {expanded && <tr id={detailId} data-testid={`compat-detail-${app.app_id}`}><td colSpan={7}>
      <div className="table"><table className="compatibility-detail-table">
        <thead><tr><th>OS</th><th>CPU 架构</th><th>支持芯片</th><th>镜像配置</th><th>完整镜像名 / Digest</th><th title="该次发布的 app_info commit id">commit id</th><th>测试项</th><th>测试命令</th><th>说明</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>
          <td>{row.os || "—"}</td><td>{archName(row.arch) || "—"}</td><td>{row.chips?.join(", ") || "—"}</td>
          <td>{[row.image_target, row.python_label, row.pytorch_label].filter(Boolean).join(" · ") || "—"}</td>
          <td className="mono small break">{row.delivery_reference || "—"}</td>
          <td className="mono small" title={row.app_info_commit_id} data-testid="compat-app-info-commit">{row.app_info_commit_id?.slice(0, 12) || "—"}</td>
          <td className="mono small">{row.path || "—"}</td>
          <td><div className="mono small cell-wrap" data-testid="compat-command">{row.command || "—"}</div></td>
          <td className="small break">{row.note || "—"}</td>
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
    <thead><tr><th>App</th><th>Owner</th><th>App 版本</th><th>来源 HPC 发布周期</th><th>依赖组件</th><th>镜像数</th><th>测试项数 / 命令数</th></tr></thead>
    <tbody>{[...apps].map(([id, items]) => <AppRows key={id} rows={items} />)}</tbody>
  </table></div>;
}

const platformFilters = [
  ["arch", "CPU 架构"], ["os", "OS"], ["chip", "GPU 芯片"], ["python_label", "Python 配置"], ["pytorch_label", "PyTorch 配置"],
] as const;
type FilterKey = typeof platformFilters[number][0];

export function CompatibilityPane() {
  const { user } = useAuth();
  const [filter, setFilter] = React.useState("");
  const [component, setComponent] = React.useState("");
  const [platform, setPlatform] = React.useState<Record<FilterKey, string>>({ arch: "", os: "", chip: "", python_label: "", pytorch_label: "" });
  const result = useQuery({
    queryKey: ["lookup", user?.username, user?.role, "latest-running"],
    queryFn: () => apiGet<{ app_count: number; rows: ReleaseRow[] }>("/api/lookup/latest-running"),
    staleTime: Infinity,
    refetchOnMount: "always",
  });
  const inventory = result.data?.rows ?? [];
  const components = new Map<string, Set<string>>();
  for (const row of inventory) for (const name of row.dependencies) {
    if (!components.has(name)) components.set(name, new Set());
    components.get(name)!.add(row.app_id);
  }
  const needle = filter.trim().toLocaleLowerCase();
  const rows = inventory.filter((row) =>
    (!component || row.dependencies.includes(component) || row.scope_pending)
    && platformFilters.every(([key]) => !platform[key] || (key === "chip" ? row.chips?.includes(platform[key]) : row[key] === platform[key]))
    && (!needle || [row.app_id, ...cells(row)].join(" ").toLocaleLowerCase().includes(needle)));
  const confirmed = rows.filter((row) => !row.scope_pending);
  const pending = rows.filter((row) => row.scope_pending);
  return <div className="panel" data-testid="compatibility-pane">
    <div className="panel-head">
      <h2>测试 App 清单</h2>
      {result.data && <span className="count">{new Set(rows.map((row) => row.app_id)).size} / {result.data.app_count} 个 App</span>}
      <span className="spacer" />
      <RefreshBar dataUpdatedAt={result.dataUpdatedAt} isFetching={result.isFetching} onRefresh={() => void result.refetch()} />
    </div>
    <div className="panel-body">
      <p className="hint">每个运行中 App 选最近一次有效成功发布；后续失败、未报名或撤回不替换此前成功版本。依赖、镜像和测试范围均来自该次发布；展开 App 查看平台与完整命令。</p>
      <div className="row compatibility-filters">
        <label>组件<select className="select select-inline" value={component} onChange={(event) => setComponent(event.target.value)} aria-label="按组件筛选">
          <option value="">全部组件</option>
          {[...components].sort(([a], [b]) => a.localeCompare(b)).map(([name, apps]) => <option key={name} value={name}>{name}（{apps.size}）</option>)}
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
          setFilter(""); setComponent(""); setPlatform({ arch: "", os: "", chip: "", python_label: "", pytorch_label: "" });
        }}>清除筛选</button>
      </div>
      {result.error && <p className="lerr" role="alert">{result.error.message}</p>}
      {result.isFetching && <p className="muted" role="status">加载中…</p>}
    </div>
    {!result.error && !result.isFetching && result.data && <>
      {rows.length === 0 && <p className="muted p-12-16">{result.data.app_count ? "没有匹配的记录。" : "暂无 CICD 运行中的 App。"}</p>}
      {confirmed.length > 0 && <AppsTable rows={confirmed} />}
      {pending.length > 0 && <section data-testid="compatibility-pending">
        <div className="panel-body"><h3>范围待确认（{new Set(pending.map((row) => row.app_id)).size} 个 App）</h3>
          <p className="hint">未声明组件依赖、依赖为空或尚无有效发布快照的 App。选择组件时仍保留供 QA 确认；平台和关键词筛选照常生效，导出包含此部分并标注范围状态。</p>
        </div>
        <AppsTable rows={pending} />
      </section>}
    </>}
  </div>;
}
