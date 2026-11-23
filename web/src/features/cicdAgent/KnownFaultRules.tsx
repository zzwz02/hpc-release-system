import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, History, Pencil, Plus, RefreshCw, Save, Search, X } from "lucide-react";
import { apiFetch, apiGet, apiPost } from "../../api/http";
import { confirmDialog, getActiveConfirm } from "../../lib/confirm";
import { formatServerTime } from "../../lib/time";
import { todayString, type FailureRecordListItem } from "./cicdAgentApi";
import "./knownFaultRules.css";

type Config = {
  name: string; mode: "signature" | "dispatch"; enabled: boolean; starts_at: string; ends_at: string;
  stages: string[]; apps: string[]; jobs: string[]; chips: string[]; maca_versions: string[];
  error_patterns: string[]; ignore_versions: boolean; owner_role: "Code Owner" | "DevOps";
  owner_account: string; summary: string; action: string; root_team: string; provisional: boolean;
};
type Rule = { id: number; revision: number; config: Config; state: string; updated_by: string; updated_at: string };
type Assignment = { role: string; account: string | null; summary: string };
type PreviewRow = { id: number; app: string; job: string; build: number; before: Assignment; after: Assignment;
  eligible: boolean; manual: boolean; skip_reason: string; evidence: { source: string; quotes: string[] } | null };
type Preview = { preview_id: string; rule_id: number; revision: number; rule_active: boolean; expires_at: string; items: PreviewRow[] };
type RuleHistory = { hit_count: number; versions: { revision: number; config: Config; actor: string; created_at: string }[];
  hits: { failure_id: number; revision: number; actor: string; created_at: string }[] };
const endpoint = "/api/cicd-agent/known-fault-rules";
const queryKey = ["cicd", "known-fault-rules"];
const stateLabel: Record<string, string> = { active: "生效中", disabled: "已停用", expired: "已到期", scheduled: "待生效" };
const split = (value: string) => value.split(/[\n,，]/).map(s => s.trim()).filter(Boolean);
const errorText = (e: unknown) => e instanceof Error ? e.message : String(e);
const localTime = (date: Date) => date.toLocaleString("sv-SE", { timeZone: "Asia/Shanghai" }).replace(" ", "T").slice(0, 16);

function initialConfig(records: FailureRecordListItem[]): Config {
  const first = records[0];
  const dispatch = first?.record_source === "test_case_result";
  return { name: "", mode: dispatch ? "dispatch" : "signature", enabled: false,
    starts_at: localTime(new Date()), ends_at: localTime(new Date(Date.now() + 3 * 86400000)),
    stages: first?.normalized_stage ? [first.normalized_stage] : [], apps: [], jobs: [],
    chips: dispatch ? [...new Set(records.flatMap(r => split(r.chip || "")))] : [], maca_versions: [],
    error_patterns: (first?.key_error_snippet || "").split("\n").filter(Boolean).slice(0, 3).map(s => s.slice(0, 300)),
    ignore_versions: false, owner_role: dispatch ? "DevOps" : "Code Owner", owner_account: dispatch ? "m00930" : "",
    summary: "", action: "", root_team: "", provisional: true };
}

export function KnownFaultRules({ selectedRecords = [], onApplied, showHeading = true }: {
  selectedRecords?: FailureRecordListItem[]; onApplied?: () => void; showHeading?: boolean;
}) {
  const client = useQueryClient();
  const query = useQuery({ queryKey, queryFn: () => apiGet<{ items: Rule[] }>(endpoint) });
  const [editor, setEditor] = useState<{ rule?: Rule; config: Config } | null>(() => selectedRecords.length ? { config: initialConfig(selectedRecords) } : null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [target, setTarget] = useState<Rule | null>(null);
  const [dateFrom, setDateFrom] = useState(todayString(-6));
  const [dateTo, setDateTo] = useState(todayString());
  const [useSelected, setUseSelected] = useState(selectedRecords.length > 0);
  const [previewSelectedOnSave, setPreviewSelectedOnSave] = useState(selectedRecords.length > 0);
  const [overrideManual, setOverrideManual] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [checked, setChecked] = useState<number[]>([]);
  const [history, setHistory] = useState<{ rule: Rule; data: RuleHistory } | null>(null);
  const [filter, setFilter] = useState("");

  async function run(task: () => Promise<void>) {
    setError(""); setMessage(""); setBusy(true);
    try { await task(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  function update<K extends keyof Config>(key: K, value: Config[K]) {
    setEditor(previous => previous ? { ...previous, config: { ...previous.config, [key]: value } } : previous);
  }
  function clearPreview() { setPreview(null); setChecked([]); setConfirmation(""); }
  async function previewRule(rule: Rule, selected = useSelected) {
    clearPreview(); setTarget(rule); setHistory(null);
    const result = await apiPost<Preview>(`${endpoint}/${rule.id}/preview`, {
      ...(selected && selectedRecords.length ? { record_ids: selectedRecords.map(r => r.id) } : { date_from: dateFrom, date_to: dateTo }),
      override_manual: overrideManual,
    });
    setPreview(result); setChecked(result.items.filter(r => r.eligible).slice(0, 100).map(r => r.id));
  }
  async function save() {
    if (!editor) return;
    await run(async () => {
      const rule = await apiFetch<Rule>(editor.rule ? `${endpoint}/${editor.rule.id}` : endpoint, {
        method: editor.rule ? "PUT" : "POST", body: JSON.stringify({ config: { ...editor.config,
          error_patterns: editor.config.error_patterns.map(p => p.trim()).filter(Boolean) }, expected_revision: editor.rule?.revision }),
      });
      setEditor(null); clearPreview(); setTarget(rule); await query.refetch();
      setMessage(`已保存 #${rule.id} v${rule.revision}，${stateLabel[rule.state]}`);
      if (previewSelectedOnSave && selectedRecords.length) await previewRule(rule, true);
    });
  }
  async function toggle(rule: Rule) {
    if (!await confirmDialog({ body: `${rule.config.enabled ? "停用" : "启用"}「${rule.config.name}」？已有历史处理结果保持不变。`,
      confirmText: rule.config.enabled ? "停用" : "启用" })) return;
    await run(async () => {
      await apiFetch(`${endpoint}/${rule.id}`, { method: "PUT", body: JSON.stringify({
        config: { ...rule.config, enabled: !rule.config.enabled }, expected_revision: rule.revision }) });
      clearPreview(); setTarget(null); await query.refetch();
    });
  }
  async function applyPreview() {
    if (!preview) return;
    if (!await confirmDialog({ body: `确认应用规则 #${preview.rule_id} v${preview.revision} 到 ${checked.length} 条记录？将更新当前跟进责任和摘要，保留原诊断，不重发已发送的通知。`, confirmText: "确认应用" })) return;
    await run(async () => {
      const result = await apiPost<{ count: number }>(`${endpoint}/${preview.rule_id}/apply`, {
        preview_id: preview.preview_id, record_ids: checked, confirm_manual: confirmation,
      });
      clearPreview(); setMessage(`已应用到 ${result.count} 条记录`);
      await client.invalidateQueries({ predicate: q => q.queryKey[0] === "cicd-agent" });
      onApplied?.();
    });
  }
  const c = editor?.config;
  const selectedManual = preview?.items.some(r => checked.includes(r.id) && r.manual);

  return <section className="known-fault-rules">
    <div className="panel-head">{showHeading && <h2>已知故障规则</h2>}<div className="known-fault-actions">
      <button className="btn primary" disabled={busy} onClick={() => { setEditor({ config: initialConfig(selectedRecords) }); clearPreview(); setHistory(null); }}><Plus size={16} />新增规则</button>
      <button className="btn" disabled={busy} aria-label="刷新规则" title="刷新规则" onClick={() => void query.refetch()}><RefreshCw size={16} /></button>
    </div></div>
    {(error || query.error) && <div className="error-banner" role="alert">{error || errorText(query.error)}</div>}
    {message && <div className="known-fault-notice" role="status"><Check size={16} />{message}</div>}
    {editor && c && <form key={`${editor.rule?.id || 'new'}:${editor.rule?.revision || 0}:${c.mode}`} className="known-fault-editor" onSubmit={e => { e.preventDefault(); void save(); }}>
      <div className="known-fault-form-grid">
        <label>规则名称<input className="input" required maxLength={120} value={c.name} onChange={e => update("name", e.target.value)} /></label>
        <label>规则类型<select className="input" value={c.mode} onChange={e => { const mode = e.target.value as Config["mode"]; setEditor({ ...editor, config: { ...c, mode, error_patterns: [], stages: [], provisional: true } }); }}>
          <option value="signature">关键错误匹配</option><option value="dispatch">测试超时临时分派</option></select></label>
        <label>生效时间（北京时间）<input className="input" type="datetime-local" required value={c.starts_at.slice(0, 16)} onChange={e => update("starts_at", e.target.value)} /></label>
        <label>到期时间（北京时间）<input className="input" type="datetime-local" required value={c.ends_at.slice(0, 16)} onChange={e => update("ends_at", e.target.value)} /></label>
        {([['chips', 'Chip'], ['apps', 'APP'], ['jobs', 'Job'], ['stages', '失败阶段'], ['maca_versions', '基础镜像版本（可选）']] as const).map(([key, label]) =>
          <label key={key}>{label}<input className="input" defaultValue={c[key].join(", ")} placeholder="不限" title="多个值以逗号分隔；支持 * 和 ? 通配符" onChange={e => update(key, split(e.target.value))} /></label>)}
        <label>根因关联团队（可选）<input className="input" maxLength={120} value={c.root_team} onChange={e => update("root_team", e.target.value)} /></label>
        <label>跟进角色<select className="input" value={c.owner_role} onChange={e => { const role = e.target.value as Config['owner_role']; setEditor({ ...editor, config: { ...c, owner_role: role, owner_account: role === "DevOps" ? "m00930" : "" } }); }}>
          <option value="Code Owner">各 APP 的 Code Owner</option><option value="DevOps">DevOps</option></select></label>
        {c.owner_role === "DevOps" && <label>跟进域账号<input className="input" required value={c.owner_account} onChange={e => update("owner_account", e.target.value)} /></label>}
      </div>
      {c.mode === "signature" && <><label>关键错误（全部匹配）<textarea className="input" required rows={4} value={c.error_patterns.join("\n")}
        title="每行一条；支持 * 和 ? 通配符，匹配实际失败步骤；历史记录无步骤日志时使用已保存的关键错误" onChange={e => update("error_patterns", e.target.value.split("\n"))} /></label>
        <label className="known-fault-check"><input type="checkbox" checked={c.ignore_versions} onChange={e => update("ignore_versions", e.target.checked)} />忽略错误中的数字版本</label></>}
      <div className="known-fault-form-grid"><label>原因摘要<textarea className="input" required rows={3} maxLength={2000} value={c.summary} onChange={e => update("summary", e.target.value)} /></label>
        <label>处理建议<textarea className="input" required rows={3} maxLength={2000} value={c.action} onChange={e => update("action", e.target.value)} /></label></div>
      <div className="known-fault-actions"><label className="known-fault-check"><input type="checkbox" checked={c.provisional} disabled={c.mode === "dispatch"} onChange={e => update("provisional", e.target.checked)} />临时挂靠</label>
        <label className="known-fault-check"><input type="checkbox" checked={c.enabled} onChange={e => update("enabled", e.target.checked)} />启用规则</label>
        {selectedRecords.length > 0 && <label className="known-fault-check"><input type="checkbox" checked={previewSelectedOnSave} onChange={e => setPreviewSelectedOnSave(e.target.checked)} />保存后预览已选 {selectedRecords.length} 条</label>}
        <button className="btn primary" disabled={busy} type="submit"><Save size={16} />保存{editor.rule ? ` v${editor.rule.revision + 1}` : "规则"}</button>
        <button className="btn" disabled={busy} type="button" title="取消编辑" aria-label="取消编辑" onClick={() => setEditor(null)}><X size={16} /></button>
      </div>
    </form>}
    <div className="known-fault-actions"><label>状态 <select className="input" value={filter} onChange={e => setFilter(e.target.value)}><option value="">全部</option>{Object.entries(stateLabel).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
    <div className="known-fault-table"><table className="table"><thead><tr><th>规则 / 版本</th><th>影响范围</th><th>处理安排</th><th>有效期</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>{query.data?.items.filter(r => !filter || r.state === filter).map(rule => <tr key={rule.id}>
        <td><strong>{rule.config.name}</strong><div className="muted small">#{rule.id} · v{rule.revision} · {rule.updated_by}</div></td>
        <td>{rule.config.mode === "dispatch" ? "测试 Timeout" : "关键错误"}<div className="muted small">{[...rule.config.chips, ...rule.config.apps, ...rule.config.jobs, ...rule.config.maca_versions].join(" / ") || "未限定版本 / APP"}</div></td>
        <td>{rule.config.owner_role === "Code Owner" ? "各 APP Code Owner" : `DevOps / ${rule.config.owner_account}`}<div className="muted small">{rule.config.provisional ? "临时挂靠" : "已确认处理要求"}</div></td>
        <td className="small">{formatServerTime(rule.config.starts_at)}<br />{formatServerTime(rule.config.ends_at)}</td>
        <td><label className="known-fault-check"><input type="checkbox" aria-label={`${rule.config.name} 启用`} checked={rule.config.enabled} disabled={busy} onChange={() => void toggle(rule)} /><span className={`known-fault-state ${rule.state}`}>{stateLabel[rule.state]}</span></label></td>
        <td><div className="known-fault-actions"><button className="btn sm" disabled={busy} title={`编辑 ${rule.config.name}`} aria-label={`编辑 ${rule.config.name}`} onClick={() => { setEditor({ rule, config: structuredClone(rule.config) }); clearPreview(); }}><Pencil size={15} /></button>
          <button className="btn sm" disabled={busy} onClick={() => { setTarget(rule); clearPreview(); setHistory(null); }}><Search size={15} />历史匹配</button>
          <button className="btn sm" disabled={busy} title={`历史 ${rule.config.name}`} aria-label={`历史 ${rule.config.name}`} onClick={() => void run(async () => { setHistory({ rule, data: await apiGet<RuleHistory>(`${endpoint}/${rule.id}/history`) }); setTarget(null); clearPreview(); })}><History size={15} /></button></div></td>
      </tr>)}{!query.data?.items.length && <tr><td colSpan={6}>{query.isLoading ? "加载中..." : "暂无规则"}</td></tr>}</tbody></table></div>
    {target && <section className="known-fault-preview"><h3>历史匹配 · {target.config.name}</h3>
      <div className="known-fault-actions">
        {selectedRecords.length > 0 && <label className="known-fault-check"><input type="checkbox" disabled={busy} checked={useSelected} onChange={e => { setUseSelected(e.target.checked); clearPreview(); }} />仅已选 {selectedRecords.length} 条</label>}
        {(!useSelected || !selectedRecords.length) && <><label>开始日期<input className="input" type="date" disabled={busy} value={dateFrom} onChange={e => { setDateFrom(e.target.value); clearPreview(); }} /></label>
          <label>结束日期<input className="input" type="date" disabled={busy} value={dateTo} onChange={e => { setDateTo(e.target.value); clearPreview(); }} /></label></>}
        <label className="known-fault-check"><input type="checkbox" disabled={busy} checked={overrideManual} onChange={e => { setOverrideManual(e.target.checked); clearPreview(); }} />包含已人工处理</label>
        <button className="btn" disabled={busy} onClick={() => void run(() => previewRule(target))}><Search size={16} />预览匹配</button>
      </div>
      {preview && <><div className="known-fault-preview-summary">匹配预览 {preview.items.length} 条 · 已选 {checked.length} 条 · 有效至 {formatServerTime(preview.expires_at)}
        {!preview.rule_active && <span className="known-fault-warning">规则未生效，不能应用</span>}</div>
        <div className="known-fault-table"><table className="table"><thead><tr><th><input type="checkbox" aria-label="选择可处理记录（最多100条）" checked={checked.length > 0 && checked.length === Math.min(100, preview.items.filter(r => r.eligible).length)} onChange={e => setChecked(e.target.checked ? preview.items.filter(r => r.eligible).slice(0, 100).map(r => r.id) : [])} /></th><th>记录</th><th>当前责任</th><th>应用后</th><th>证据 / 跳过原因</th></tr></thead>
          <tbody>{preview.items.map(row => <tr key={row.id}><td><input type="checkbox" aria-label={`选择记录 ${row.id}`} checked={checked.includes(row.id)} disabled={!row.eligible || busy || (!checked.includes(row.id) && checked.length >= 100)} onChange={e => setChecked(prev => e.target.checked ? [...prev, row.id] : prev.filter(id => id !== row.id))} /></td>
            <td><a href={`/jenkins-failures?failure_id=${row.id}`} target="_blank" rel="noreferrer">#{row.id} {row.app}</a><div className="small muted">{row.job} #{row.build}</div></td>
            <td>{row.before.role} / {row.before.account || "未指定"}{row.manual && <div className="known-fault-warning">已人工处理</div>}</td>
            <td>{row.after.role} / {row.after.account || "未指定"}<div className="small">{row.after.summary}</div></td>
            <td>{row.skip_reason || <details><summary>匹配证据 · {row.evidence?.source === "saved_key_error" ? "已保存关键错误" : row.evidence?.source === "structured_test_result" ? "测试结果" : "失败步骤日志"}</summary><pre>{row.evidence?.quotes.join("\n")}</pre></details>}</td>
          </tr>)}</tbody></table></div>
        {selectedManual && <label className="known-fault-warning">覆盖人工裁定确认<input className="input" placeholder="覆盖人工裁定" value={confirmation} onChange={e => setConfirmation(e.target.value)} /></label>}
        <div className="known-fault-actions"><button className="btn primary" disabled={busy || !checked.length || !preview.rule_active || (!!selectedManual && confirmation !== "覆盖人工裁定")} onClick={() => void applyPreview()}><Check size={16} />确认应用 {checked.length} 条</button></div>
      </>}
    </section>}
    {history && <section className="known-fault-preview"><h3>{history.rule.config.name} · 变更与命中历史</h3>
      <p>累计命中 {history.data.hit_count} 条记录</p>
      {history.data.versions.map(version => <details key={version.revision}><summary>v{version.revision} · {version.actor} · {formatServerTime(version.created_at)}</summary>
        <dl><dt>原因摘要</dt><dd>{version.config.summary}</dd><dt>处理建议</dt><dd>{version.config.action}</dd><dt>生效 / 到期</dt><dd>{formatServerTime(version.config.starts_at)} / {formatServerTime(version.config.ends_at)}</dd></dl>
        <pre>{JSON.stringify(version.config, null, 2)}</pre></details>)}
      <div className="known-fault-actions">{history.data.hits.map((hit, i) => <a key={`${hit.failure_id}-${i}`} href={`/jenkins-failures?failure_id=${hit.failure_id}`} target="_blank" rel="noreferrer">#{hit.failure_id} · v{hit.revision}</a>)}</div>
    </section>}
  </section>;
}

export function KnownFaultDialog({ records, onClose, onApplied }: { records: FailureRecordListItem[]; onClose: () => void; onApplied: () => void }) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (getActiveConfirm()) return;
      if (e.key === "Escape") { e.stopImmediatePropagation(); onClose(); }
      if (e.key === "Tab") {
        const nodes = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]') || []);
        if (!nodes.length) return;
        if (e.shiftKey && (document.activeElement === nodes[0] || document.activeElement === dialog.current)) { e.preventDefault(); nodes.at(-1)?.focus(); }
        else if (!e.shiftKey && document.activeElement === nodes.at(-1)) { e.preventDefault(); nodes[0].focus(); }
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);
  return <div className="dialog-backdrop known-fault-backdrop" role="dialog" aria-modal="true" aria-label="已知故障规则">
    <div className="known-fault-dialog" ref={dialog} tabIndex={-1}>
      <div className="known-fault-dialog-header">
        <h2>已知故障规则</h2>
        <button className="btn known-fault-close" type="button" title="关闭规则管理" aria-label="关闭规则管理" onClick={onClose}><X size={20} aria-hidden="true" /></button>
      </div>
      <div className="known-fault-dialog-body">
        <KnownFaultRules selectedRecords={records} onApplied={onApplied} showHeading={false} />
      </div>
    </div>
  </div>;
}
