import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Plus, RefreshCw, ShieldX, Trash2, X } from "lucide-react";
import { apiFetch, apiGet, apiPost } from "../../api/http";
import { confirmDialog } from "../../lib/confirm";
import "./apiTokens.css";

type ApiToken = {
  id: number; name: string; token_hint: string; created_at: string;
  last_used_at: string; last_operation: string; revoked_at: string;
};
type TokenList = { tokens: ApiToken[]; ci_allowed: boolean };
const ENDPOINT = "/api/account/tokens";
const OPERATIONS: Record<string, string> = {
  owner_lookup: "Owner 查询", release_check: "构建准入检查", failure_report: "构建失败上报",
  test_results: "测试结果上报", release_gate_failure: "准入失败上报",
};

export function ApiTokenDialog({ username, onClose }: { username: string; onClose: () => void }) {
  const client = useQueryClient();
  const queryKey = ["account-api-tokens", username];
  const query = useQuery({ queryKey, queryFn: () => apiGet<TokenList>(ENDPOINT), retry: false, staleTime: 0 });
  const panel = useRef<HTMLDivElement>(null);
  const secretField = useRef<HTMLTextAreaElement>(null);
  const [name, setName] = useState("Jenkins");
  const [created, setCreated] = useState<(ApiToken & { token: string }) | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const tokens = query.data?.tokens ?? [];
  const activeCount = tokens.filter(token => !token.revoked_at).length;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => previous?.focus();
  }, []);

  async function close() {
    if (busy) return;
    if (created && !await confirmDialog({ title: "关闭 Token 管理", body: "完整 Token 关闭后无法再次查看。确认已保存？", confirmText: "已保存，关闭" })) return;
    onClose();
  }

  async function create() {
    if (busy || !name.trim()) return;
    setBusy(true); setError(""); setCopied(false);
    try {
      // Keep the one-time secret out of query/mutation caches and browser storage.
      const result = await apiPost<{ token: ApiToken & { token: string } }>(ENDPOINT, { name: name.trim() });
      setCreated(result.token);
      await client.invalidateQueries({ queryKey });
    } catch (failure) { setError(failure instanceof Error ? failure.message : "生成失败"); }
    finally { setBusy(false); }
  }

  async function revoke(token: ApiToken) {
    if (busy || !await confirmDialog({ title: "撤销 API Token", body: `撤销「${token.name}」后，使用它的自动化调用将立即失效。`, danger: true, confirmText: "确认撤销" })) return;
    setBusy(true); setError("");
    try {
      await apiPost(`${ENDPOINT}/${token.id}/revoke`, {});
      if (created?.id === token.id) setCreated(null);
      await client.invalidateQueries({ queryKey });
    } catch (failure) { setError(failure instanceof Error ? failure.message : "撤销失败"); }
    finally { setBusy(false); }
  }

  async function copy() {
    if (!created) return;
    try { await navigator.clipboard.writeText(created.token); setCopied(true); }
    catch {
      secretField.current?.focus(); secretField.current?.select();
      try { setCopied(document.execCommand("copy")); } catch { setCopied(false); }
    }
  }

  async function remove(token: ApiToken) {
    if (busy || !token.revoked_at || !await confirmDialog({ title: "删除已撤销的 Token", body: `删除「${token.name}」后列表不再显示，审计记录仍保留。此操作无法恢复。`, danger: true, confirmText: "确认删除" })) return;
    setBusy(true); setError("");
    try {
      await apiFetch(`${ENDPOINT}/${token.id}`, { method: "DELETE" });
      await client.invalidateQueries({ queryKey });
    } catch (failure) { setError(failure instanceof Error ? failure.message : "删除失败"); }
    finally { setBusy(false); }
  }

  return <div className="dialog-backdrop account-token-backdrop" onMouseDown={event => {
    if (event.target === event.currentTarget) void close();
  }}>
    <div className="dialog-card account-token-dialog" ref={panel} role="dialog" aria-modal="true"
      aria-labelledby="account-token-title" aria-busy={busy} onKeyDown={event => {
        if (event.key === "Escape") { event.stopPropagation(); void close(); }
        if (event.key !== "Tab") return;
        const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), textarea") ?? []);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}>
      <div className="account-token-heading">
        <div><h3 id="account-token-title">API Token</h3><span className="muted small">{username}</span></div>
        <button className="btn ghost sm icon" type="button" aria-label="关闭 Token 管理" title="关闭" disabled={busy} onClick={() => void close()}><X size={18} /></button>
      </div>
      <div className="dialog-body">
        {query.isPending && <p role="status">加载中...</p>}
        {(error || query.error) && <p className="action-err" role="alert">{error || query.error?.message}</p>}
        {query.data && <>
          {!query.data.ci_allowed && <p className="muted">当前账号角色未开放 CI 自动化接口。</p>}
          {created ? <section className="account-token-secret" aria-label="新生成的 Token">
            <strong>完整 Token 仅显示本次，请妥善保存</strong>
            <textarea ref={secretField} aria-label="完整 API Token" value={created.token} readOnly rows={2} spellCheck={false} />
            <div className="account-token-secret-actions">
              <button className="btn sm" type="button" onClick={() => void copy()}><Copy size={14} />{copied ? "已复制" : "复制 Token"}</button>
              <button className="btn ghost sm" type="button" onClick={() => setCreated(null)}>已保存</button>
            </div>
          </section> : <form className="account-token-create" onSubmit={event => { event.preventDefault(); void create(); }}>
            <label>名称<input className="input" value={name} onChange={event => setName(event.target.value)} maxLength={60} autoComplete="off" disabled={busy} /></label>
            <button className="btn primary" type="submit" disabled={busy || !name.trim() || activeCount >= 5}><Plus size={15} />{busy ? "处理中..." : "生成 Token"}</button>
          </form>}
          <div className="account-token-list-heading"><strong>有效 {activeCount} / 5</strong><span className="muted small">长期有效，撤销后失效</span></div>
          {!tokens.length && <p className="muted">暂无 Token</p>}
          <div className="account-token-list">
            {tokens.map(token => <article key={token.id} className={`account-token-row${token.revoked_at ? " revoked" : ""}`}>
              <div className="account-token-summary"><strong>{token.name}</strong><code>{token.token_hint}</code><span className="small">{token.revoked_at ? "已撤销" : "有效"}</span></div>
              <div className="account-token-dates small muted">
                <span>创建：{token.created_at}</span>
                <span>最近使用：{token.last_used_at || "尚未使用"}{token.last_operation ? ` · ${OPERATIONS[token.last_operation] || token.last_operation}` : ""}</span>
              </div>
              {!token.revoked_at && <button className="btn ghost sm account-token-revoke" type="button" title={`撤销 ${token.name}`} aria-label={`撤销 ${token.name}`} disabled={busy} onClick={() => void revoke(token)}><ShieldX size={15} />撤销</button>}
              {token.revoked_at && <button className="btn ghost sm account-token-revoke" type="button" title={`删除 ${token.name}`} aria-label={`删除 ${token.name}`} disabled={busy} onClick={() => void remove(token)}><Trash2 size={15} />删除</button>}
            </article>)}
          </div>
        </>}
      </div>
      <div className="dialog-actions"><button className="btn ghost sm" disabled={busy || query.isFetching} type="button" onClick={() => void query.refetch()}><RefreshCw size={14} />刷新</button></div>
    </div>
  </div>;
}
