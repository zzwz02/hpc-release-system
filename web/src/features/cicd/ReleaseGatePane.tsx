import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pencil, Plus, RefreshCw, Save, X } from "lucide-react";
import { apiGet, apiPost } from "../../api/http";
import { confirmDialog } from "../../lib/confirm";
import { formatServerTime } from "../../lib/time";

interface Version {
  maca_version: string; release_id: string; release_name: string; enabled: boolean;
  note: string; revision: number; updated_at: string; updated_by: string; released_locked: boolean;
}
interface Response {
  versions: Version[];
  releases: { id: string; name: string; released_locked: boolean }[];
}
const endpoint = "/api/cicd/release-gate/versions";

export function ReleaseGatePane({ canManage }: { canManage: boolean }) {
  const query = useQuery({ queryKey: ["cicd", "release-gate"], queryFn: () => apiGet<Response>(endpoint) });
  const [editing, setEditing] = useState<Version | null>(null);
  const [version, setVersion] = useState("");
  const [release, setRelease] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  function reset() { setEditing(null); setVersion(""); setRelease(""); setNote(""); }
  async function save(row?: Version) {
    setBusy(true); setError("");
    try {
      await apiPost(endpoint, { maca_version: row?.maca_version ?? version, release_id: row?.release_id ?? release,
        enabled: row ? !row.enabled : editing ? Boolean(editing.enabled) : true,
        note: row?.note ?? note, expected_revision: row?.revision ?? editing?.revision ?? -1 });
      if (!row) reset();
      await query.refetch();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function toggle(row: Version) {
    if (await confirmDialog({ body: `${row.enabled ? "停用" : "启用"} ${row.maca_version} 的 Release 构建准入？`,
      danger: Boolean(row.enabled), confirmText: row.enabled ? "停用" : "启用" })) await save(row);
  }
  return <div className="cicd-pane active release-gate-pane">
    <div className="panel-head"><h2>Release 构建准入</h2>
      <button className="btn" title="刷新准入版本" aria-label="刷新准入版本" disabled={query.isFetching || busy} onClick={() => void query.refetch()}><RefreshCw size={16} /></button>
    </div>
    {canManage && <form className="cicd-toolbar release-gate-form" onSubmit={e => { e.preventDefault(); void save(); }}>
      <label>MACA_VERSION<input className="input" required pattern="[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+" maxLength={100} placeholder="3.10.2.11" value={version} disabled={busy || !!editing} onChange={e => setVersion(e.target.value)} /></label>
      <label>发布版本<select className="input" aria-label="发布版本" required value={release} disabled={busy || !!editing} onChange={e => setRelease(e.target.value)}>
        <option value="">选择发布版本</option>
        {query.data?.releases.filter(r => !r.released_locked || r.id === editing?.release_id).map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
      </select></label>
      <label>备注<input className="input" maxLength={1000} value={note} disabled={busy} onChange={e => setNote(e.target.value)} /></label>
      <button className="btn primary" disabled={busy || !query.data} type="submit">{editing ? <Save size={16} /> : <Plus size={16} />}{editing ? "保存" : "新增"}</button>
      {editing && <button className="btn" type="button" title="取消编辑" aria-label="取消编辑" disabled={busy} onClick={reset}><X size={16} /></button>}
    </form>}
    {(error || query.error) && <div className="lerr" role="alert">{error || String(query.error)}</div>}
    {query.isLoading ? <p>加载中...</p> : <div className="cicd-table-wrap"><table className="table">
      <thead><tr><th>MACA_VERSION</th><th>发布版本</th><th>准入状态</th><th>备注</th><th>更新人 / 时间</th>{canManage && <th>操作</th>}</tr></thead>
      <tbody>{query.data?.versions.map(row => <tr key={row.maca_version}>
        <td><strong>{row.maca_version}</strong></td><td>{row.release_name}</td>
        <td><label><input type="checkbox" aria-label={`${row.maca_version} 启用准入`} checked={Boolean(row.enabled)} disabled={!canManage || busy || (!row.enabled && !!row.released_locked)} onChange={() => void toggle(row)} /> {row.released_locked ? "版本已封存" : row.enabled ? "已启用" : "已停用"}</label></td>
        <td style={{ maxWidth: 320, overflowWrap: "anywhere", whiteSpace: "normal" }}>{row.note || "-"}</td>
        <td>{row.updated_by}<br /><span className="muted small">{formatServerTime(row.updated_at)}</span></td>
        {canManage && <td><button className="btn" title={`编辑 ${row.maca_version}`} aria-label={`编辑 ${row.maca_version}`} disabled={busy} onClick={() => { setEditing(row); setVersion(row.maca_version); setRelease(row.release_id); setNote(row.note); }}><Pencil size={16} /></button></td>}
      </tr>)}{!query.data?.versions.length && <tr><td colSpan={canManage ? 6 : 5}>暂无准入版本</td></tr>}</tbody>
    </table></div>}
  </div>;
}
