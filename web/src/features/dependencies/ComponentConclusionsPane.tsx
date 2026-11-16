import React, { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet, apiPost } from "../../api/http";
import { RefreshBar } from "../../components/RefreshBar";
import { TestScopeTable } from "../../components/TestScopeTable";
import { can } from "../../lib/accessControl";
import { confirmDialog } from "../../lib/confirm";
import { formatServerTime } from "../../lib/time";
import { useUiStore } from "../../store/uiStore";

interface ReportSummary {
  id: number; component: string; component_version: string; created_at: string; created_by: string;
}
interface AppResult {
  app_id: string; app_name: string; app_version: string; hpc_version: string;
  scope_note?: string;
  tests: React.ComponentProps<typeof TestScopeTable>["tests"];
  qa_status: string; qa_comment: string; revision: number; updated_at: string; updated_by: string;
}
interface Report extends ReportSummary { apps: AppResult[] }
interface Draft { qa_status: string; qa_comment: string; revision: number }
const BASE = "/api/component-compatibility";
const COLUMNS = [
  ["app_id", "app_id"], ["app_name", "App"], ["app_version", "app_version"],
  ["hpc_version", "hpc_version"], ["qa_status", "QA 结论"], ["qa_comment", "comment（选填）"],
] as const;

export function ComponentConclusionsPane() {
  const { user } = useAuth();
  return <Workspace key={`${user?.username}:${user?.role}`} />;
}

function Workspace() {
  const { user } = useAuth();
  const editable = can(user?.role, "qa.edit");
  const queries = useQueryClient();
  const listKey = ["component-compatibility", user?.username, user?.role, "reports"];
  const detailKey = (id: number) => ["component-compatibility", user?.username, user?.role, "report", id];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [creating, setCreating] = useState(false);
  const [component, setComponent] = useState("");
  const [version, setVersion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [filter, setFilter] = useState("");
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({});
  const generation = useRef(0);
  const dirty = Object.keys(drafts).length > 0 || (creating && !!(component || version));
  const setDirty = useUiStore(state => state.setComponentCompatibilityDirty);
  useEffect(() => { setDirty(dirty); }, [dirty, setDirty]);
  useEffect(() => () => { generation.current++; setDirty(false); }, [setDirty]);
  useEffect(() => {
    if (!dirty) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);

  const list = useQuery({ queryKey: listKey, queryFn: () => apiGet<{ reports: ReportSummary[] }>(BASE), refetchOnMount: "always" });
  const reports = list.data?.reports ?? [];
  const activeId = selectedId ?? reports[0]?.id;
  const detail = useQuery({ queryKey: detailKey(activeId ?? 0), queryFn: () => apiGet<Report>(`${BASE}/${activeId}`),
    enabled: !!activeId && !list.error, staleTime: 0, refetchOnMount: "always" });
  const choices = useQuery({ queryKey: ["component-compatibility", user?.username, user?.role, "components"],
    queryFn: () => apiGet<{ components: { component: string; app_count: number; all_apps?: boolean }[]; preview_id?: string }>(`${BASE}/components`),
    enabled: creating && editable, refetchOnMount: "always", retry: false });

  async function discard() {
    const current = generation.current;
    if (dirty && !(await confirmDialog({ title: "未保存的修改", body: "有未保存的组件兼容性结论，确认放弃？", confirmText: "放弃修改", danger: true }))) return false;
    if (generation.current !== current) return false;
    setDrafts({}); setDirty(false); return true;
  }
  function resetMessages() { setError(""); setMessage(""); }
  async function selectReport(id: number) {
    if (id === activeId || busy || !(await discard())) return;
    setSelectedId(id); setFilter(""); setColumnFilters({}); resetMessages();
  }
  async function refresh() {
    if (busy || !(await discard())) return;
    resetMessages();
    await list.refetch();
    if (activeId) await detail.refetch();
  }
  async function create() {
    if (!component.trim() || !version.trim() || busy) return;
    const current = generation.current;
    setBusy(true); resetMessages();
    try {
      const report = await apiPost<Report>(BASE, { component, component_version: version.trim(),
        ...(choices.data?.preview_id ? { preview_id: choices.data.preview_id } : {}) });
      if (generation.current !== current) return;
      queries.setQueryData(detailKey(report.id), report);
      await queries.invalidateQueries({ queryKey: listKey });
      if (generation.current !== current) return;
      setSelectedId(report.id); setCreating(false); setComponent(""); setVersion("");
      setFilter(""); setColumnFilters({});
      setMessage(`已创建 ${report.component} / ${report.component_version}，共 ${report.apps.length} 个 App。`);
    } catch (e) { if (generation.current === current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (generation.current === current) setBusy(false); }
  }
  function change(app: AppResult, values: Partial<Draft>) {
    const next = { ...(drafts[app.app_id] ?? { qa_status: app.qa_status, qa_comment: app.qa_comment, revision: app.revision }), ...values };
    setDrafts(old => {
      const updated = { ...old };
      if (next.qa_status === app.qa_status && next.qa_comment === app.qa_comment) delete updated[app.app_id];
      else updated[app.app_id] = next;
      return updated;
    });
    resetMessages();
  }
  async function save(app: AppResult) {
    const draft = drafts[app.app_id];
    if (!draft || busy || !activeId) return;
    if (!draft.qa_status) { setError("请选择 pass 或 fail 后再保存。"); return; }
    const current = generation.current;
    const reportId = activeId;
    setBusy(true); resetMessages();
    try {
      const saved = await apiPost<{ app: AppResult }>(`${BASE}/${reportId}/apps/${encodeURIComponent(app.app_id)}`, draft);
      if (generation.current !== current) return;
      queries.setQueryData<Report>(detailKey(reportId), old => old ? { ...old, apps: old.apps.map(item => item.app_id === app.app_id ? saved.app : item) } : old);
      setDrafts(old => { const next = { ...old }; delete next[app.app_id]; return next; });
      setMessage(`已保存 ${app.app_name} 的兼容性结论。`);
    } catch (e) { if (generation.current === current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (generation.current === current) setBusy(false); }
  }

  const report = !list.error && !detail.error && !detail.isFetching ? detail.data : undefined;
  const rows = report?.apps ?? [];
  const value = (app: AppResult, key: typeof COLUMNS[number][0]) => String(key === "qa_status" || key === "qa_comment"
    ? drafts[app.app_id]?.[key] ?? app[key] : app[key]);
  const shown = rows.filter(app => (!filter.trim() || COLUMNS.some(([key]) => value(app, key).toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase())))
    && COLUMNS.every(([key]) => value(app, key).toLocaleLowerCase().includes((columnFilters[key] ?? "").trim().toLocaleLowerCase())));

  return <div className="panel" data-testid="component-conclusions-pane">
    <div className="panel-head component-conclusion-head"><h2>组件兼容性结论</h2><span className="spacer" />
      <RefreshBar dataUpdatedAt={detail.dataUpdatedAt || list.dataUpdatedAt} isFetching={list.isFetching || detail.isFetching || busy} onRefresh={() => void refresh()} />
      {editable && <button type="button" className="btn primary sm" disabled={busy || creating} onClick={async () => {
        if (!(await discard())) return;
        resetMessages(); setComponent(""); setVersion(""); setCreating(true); void choices.refetch();
      }}>＋ 新建</button>}
    </div>
    {creating && <form className="component-conclusion-create panel-body" onSubmit={event => { event.preventDefault(); void create(); }} aria-label="新建组件兼容性结论">
      <h3>新建组件兼容性结论</h3>
      <div className="form">
        <label>组件<select className="select" value={component} onChange={event => setComponent(event.target.value)} required disabled={busy || choices.isFetching || !!choices.error}>
          <option value="">{choices.isFetching ? "正在查询最新组件…" : "请选择组件"}</option>
          {(choices.data?.components ?? []).map(item => <option key={item.component} value={item.component}>{item.component}（{item.all_apps ? "全部 " : ""}{item.app_count} 个 App）</option>)}
        </select></label>
        <label>组件版本（MACA 版本）<input className="input" value={version} onChange={event => setVersion(event.target.value)} placeholder="例如 3.10.1" maxLength={120} required disabled={busy} /></label>
      </div>
      <p className="hint">driver、sdk 覆盖兼容性验证清单中的全部 App，不要求声明组件依赖；其他组件按依赖筛选。优先使用各 App 最近一次成功发布，创建后保留当时的版本和测试范围；尚无成功发布的 App 单独标注。</p>
      {choices.error && <p className="lerr" role="alert">{choices.error.message}<button type="button" className="btn ghost sm" onClick={() => void choices.refetch()}>重试</button></p>}
      {!choices.isFetching && !choices.error && choices.data?.components.length === 0 && <p className="muted">暂无可用组件，请先确认兼容性验证中的发布记录和依赖声明。</p>}
      {error && <p className="lerr" role="alert">{error}</p>}
      <div className="row mt-10"><button className="btn primary" type="submit" disabled={busy || !component || !version.trim() || choices.isFetching || !!choices.error}>{busy ? "正在创建…" : "创建并生成 App 清单"}</button>
        <button className="btn" type="button" disabled={busy || choices.isFetching} onClick={() => { resetMessages(); void choices.refetch(); }}>刷新组件列表</button>
        <button className="btn" type="button" disabled={busy} onClick={async () => { if (await discard()) { setCreating(false); setComponent(""); setVersion(""); resetMessages(); } }}>取消</button></div>
    </form>}
    {error && !creating && <p className="lerr p-12-16" role="alert">{error}</p>}
    {message && <p className="p-12-16" role="status">{message}</p>}
    {!creating && <>
      <div className="panel-body">
        <label className="component-conclusion-picker">查看已有记录<select className="select" value={activeId ?? ""} disabled={busy || !!list.error || !reports.length} onChange={event => void selectReport(Number(event.target.value))}>
          {!reports.length && <option value="">暂无记录</option>}
          {reports.map(item => <option key={item.id} value={item.id}>{item.component} / {item.component_version}</option>)}
        </select></label>
        {list.error && <p className="lerr" role="alert">{list.error.message}</p>}
        {detail.error && <p className="lerr" role="alert">{detail.error.message}</p>}
        {(list.isPending || detail.isFetching) && <p className="muted" role="status">正在加载组件兼容性记录…</p>}
        {!list.isPending && !list.error && !reports.length && <p className="hint">暂无组件兼容性结论。{editable ? "点击“新建”，选择组件并输入组件版本。" : "等待 QA 创建并填写。"}</p>}
        {report && <><div className="row mt-12 component-conclusion-summary"><b>{report.component} · {report.component_version}</b><span>{rows.length} 个 App</span>
          <span className="count">pass {rows.filter(app => app.qa_status === "pass").length}</span><span className="count">fail {rows.filter(app => app.qa_status === "fail").length}</span><span className="count">未汇报 {rows.filter(app => !app.qa_status).length}</span></div>
          <p className="hint">创建于 {formatServerTime(report.created_at)} · {report.created_by}；测试范围保留创建时的发布配置。</p>
          <input className="input" aria-label="筛选组件兼容性结论" value={filter} onChange={event => setFilter(event.target.value)} placeholder="筛选 App、版本、结论或 comment…" />
          <div className="muted small mt-6">{shown.length} / {rows.length} 个 App</div>
        </>}
      </div>
      {report && <div className="table report-table component-conclusion-table"><table aria-label="组件兼容性结论">
        <colgroup><col style={{ width: "16%" }} /><col style={{ width: "13%" }} /><col style={{ width: "11%" }} /><col style={{ width: "12%" }} /><col style={{ width: "13%" }} /><col style={{ width: "25%" }} /><col style={{ width: "10%" }} /></colgroup>
        <thead><tr>{COLUMNS.map(([key, label]) => <th key={key}>{label}</th>)}<th>操作</th></tr>
          <tr className="colfilter-row">{COLUMNS.map(([key, label]) => <th key={key}><input aria-label={`${label} 列筛选`} placeholder="筛选…" value={columnFilters[key] ?? ""} onChange={event => setColumnFilters(old => ({ ...old, [key]: event.target.value }))} /></th>)}<th /></tr>
        </thead>
        <tbody>{shown.map(app => {
          const draft = drafts[app.app_id];
          return <React.Fragment key={app.app_id}>
            <tr data-testid={`component-result-${app.app_id}`}><td data-label="app_id" className="mono small">{app.app_id}</td><td data-label="App"><b>{app.app_name}</b>{app.scope_note && <div className="small muted mt-6">{app.scope_note}</div>}</td><td data-label="app_version">{app.app_version || "—"}</td><td data-label="hpc_version">{app.hpc_version || "—"}</td>
              <td data-label="QA 结论">{editable ? <select className="select" data-verdict={draft?.qa_status ?? app.qa_status} aria-label={`${app.app_name} 的结论`} value={draft?.qa_status ?? app.qa_status} disabled={busy} onChange={event => change(app, { qa_status: event.target.value })}><option value="">未汇报</option><option value="pass">pass</option><option value="fail">fail</option></select> : app.qa_status || "未汇报"}
                {draft && <div className="small muted mt-6">未保存</div>}</td>
              <td data-label="comment（选填）">{editable ? <textarea className="textarea" aria-label={`${app.app_name} 的 comment`} value={draft?.qa_comment ?? app.qa_comment} rows={2} maxLength={10000} disabled={busy} onChange={event => change(app, { qa_comment: event.target.value })} placeholder="可填写失败项、测试范围或其他说明" /> : <div className="cell-wrap">{app.qa_comment || "—"}</div>}
                {app.updated_at && <div className="small muted mt-6">{app.updated_by} · {formatServerTime(app.updated_at)}</div>}</td>
              <td data-label="操作">{editable ? <button type="button" className="btn sm" aria-label={`保存 ${app.app_name} 的兼容性结论`} disabled={!draft || busy} onClick={() => void save(app)}>保存</button> : "—"}</td>
            </tr>
            <tr className="component-conclusion-scope"><td colSpan={7}><details><summary className="small muted">测试范围 {app.tests.length} 项</summary><TestScopeTable tests={app.tests} showCommand={false} /></details></td></tr>
          </React.Fragment>;
        })}{!shown.length && <tr><td colSpan={7} className="muted">没有匹配的 App</td></tr>}</tbody>
      </table></div>}
    </>}
  </div>;
}
