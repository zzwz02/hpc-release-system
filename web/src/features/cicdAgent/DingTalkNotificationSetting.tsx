import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Copy, Link2, Pause, Play, RefreshCw, Unlink, X } from "lucide-react";
import { apiGet, apiPost } from "../../api/http";

const ENDPOINT = "/api/cicd-agent/failure-notifications/dingtalk";
type Binding = {
  account: string; available: boolean; bound: boolean; enabled: boolean;
  nickname?: string; user_id_hint?: string; worker_online: boolean; group_name: string;
  last_delivery?: { status: string; report_date: string; error?: string };
};
type Code = { command: string; expires_at: string; group_name: string };
const RECEIPTS: Record<string, string> = {
  sent: "已提交钉钉", failed: "发送失败", uncertain: "发送结果待核实", preparing: "准备中", sending: "发送中", skipped: "未发送",
};

export function DingTalkNotificationSetting({ username }: { username: string }) {
  const [open, setOpen] = useState(false);
  const key = ["dingtalk-binding", username];
  const binding = useQuery({ queryKey: key, queryFn: () => apiGet<Binding>(ENDPOINT),
    enabled: Boolean(username), retry: false, refetchInterval: open ? 3000 : false });
  return <>
    <button type="button" className="btn ghost sm" onClick={() => setOpen(true)}>
      <BellRing size={15} aria-hidden="true" />钉钉提醒
      <span className="muted small">{binding.data?.bound ? binding.data.enabled ? "已开启" : "已暂停" : "未绑定"}</span>
    </button>
    {open && <BindingDialog username={username} binding={binding.data} error={binding.error}
      onRefresh={() => void binding.refetch()} onClose={() => setOpen(false)} />}
  </>;
}

function BindingDialog({ username, binding, error, onRefresh, onClose }: {
  username: string; binding?: Binding; error: Error | null; onRefresh: () => void; onClose: () => void;
}) {
  const client = useQueryClient();
  const panel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const [code, setCode] = useState<Code>();
  const [now, setNow] = useState(Date.now());
  const [copied, setCopied] = useState(false);
  const [confirmUnbind, setConfirmUnbind] = useState(false);
  const issue = useMutation({ mutationFn: () => apiPost<Code>(`${ENDPOINT}/code`, {}),
    onSuccess: value => { setCode(value); setCopied(false); } });
  const update = useMutation({ mutationFn: (action: "enable" | "disable" | "unbind") => apiPost<Binding>(ENDPOINT, { action }),
    onSuccess: value => { client.setQueryData(["dingtalk-binding", username], value); setCode(undefined); setConfirmUnbind(false); } });
  const busy = issue.isPending || update.isPending;
  const remaining = code ? Math.max(0, Math.ceil((Date.parse(code.expires_at) - now) / 1000)) : 0;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(timer); previous?.focus(); };
  }, []);
  useEffect(() => { if (binding?.bound) setCode(undefined); }, [binding?.bound]);
  async function copy() {
    if (!code || !remaining) return;
    try { await navigator.clipboard.writeText(code.command); setCopied(true); }
    catch {
      input.current?.focus(); input.current?.select();
      try { setCopied(document.execCommand("copy")); } catch { setCopied(false); }
    }
  }
  const failure = update.error || issue.error || error;
  return <div className="dialog-backdrop cicd-agent-detail-backdrop" onMouseDown={event => {
    if (event.target === event.currentTarget && !busy) onClose();
  }}>
    <div ref={panel} className="dialog-card cicd-dingtalk-dialog" role="dialog" aria-modal="true"
      aria-labelledby="dingtalk-setting-title" aria-busy={busy} onKeyDown={event => {
        if (event.key === "Escape") { event.stopPropagation(); if (!busy) onClose(); }
        if (event.key !== "Tab") return;
        const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), textarea:not(:disabled)') ?? []);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}>
      <div className="cicd-dingtalk-heading">
        <h3 id="dingtalk-setting-title">钉钉个人提醒</h3>
        <button type="button" className="btn ghost sm icon" title="关闭" aria-label="关闭钉钉设置" disabled={busy} onClick={onClose}><X size={18} /></button>
      </div>
      <div className="dialog-body">
        <dl className="cicd-agent-lifecycle-record">
          <dt>域账号</dt><dd>{username}</dd>
          <dt>接收服务</dt><dd>{binding?.worker_online ? "在线" : "离线"}</dd>
          <dt>提醒时间</dt><dd>工作日 09:45（北京时间）</dd>
          <dt>统计范围</dt><dd>昨日；周一汇总周五至周日</dd>
        </dl>
        {!binding?.available && !error && <p className="muted">钉钉个人提醒尚未启用。</p>}
        {binding?.available && !binding.worker_online && <p className="action-err">接收服务未连接，暂时无法完成新绑定。</p>}
        {binding?.bound ? <>
          <p role="status"><strong>{binding.enabled ? "提醒已开启" : "提醒已暂停"}</strong> · {binding.nickname || username} ({binding.user_id_hint})</p>
          {binding.last_delivery && <p className="muted small">最近提醒：{binding.last_delivery.report_date} · {RECEIPTS[binding.last_delivery.status] || "待核实"}</p>}
          {confirmUnbind ? <div role="alert">
            <p>解除绑定并停止个人钉钉提醒？邮件提醒不受影响。</p>
            <div className="dialog-actions">
              <button className="btn" disabled={busy} onClick={() => setConfirmUnbind(false)}>取消</button>
              <button className="btn danger" disabled={busy} onClick={() => update.mutate("unbind")}><Unlink size={15} />确认解绑</button>
            </div>
          </div> : <div className="dialog-actions">
            <button className="btn" disabled={busy} onClick={() => update.mutate(binding.enabled ? "disable" : "enable")}>
              {binding.enabled ? <Pause size={15} /> : <Play size={15} />}{binding.enabled ? "暂停提醒" : "恢复提醒"}
            </button>
            <button className="btn ghost" disabled={busy} onClick={() => setConfirmUnbind(true)}><Unlink size={15} />解除绑定</button>
          </div>}
        </> : <>
          {code && <div className="cicd-dingtalk-code">
            <p>绑定群：<strong>{code.group_name || "指定群"}</strong> · @机器人后发送</p>
            <textarea ref={input} aria-label="一次性绑定命令" readOnly rows={2} value={code.command} spellCheck={false} />
            <div className="cicd-dingtalk-heading">
              <span className="muted small" role="status">{remaining ? `${Math.floor(remaining / 60)} 分 ${remaining % 60} 秒内有效，使用后作废` : "绑定码已过期"}</span>
              <button className="btn sm" disabled={!remaining} onClick={() => void copy()}><Copy size={14} />{copied ? "已复制" : "复制"}</button>
            </div>
          </div>}
          <div className="dialog-actions">
            {code && <button className="btn ghost" disabled={busy} onClick={() => update.mutate("unbind")}>作废绑定码</button>}
            <button className="btn primary" disabled={busy || !binding?.available || !binding.worker_online}
              onClick={() => issue.mutate()}><Link2 size={15} />{issue.isPending ? "生成中..." : code ? "重新生成" : "生成绑定码"}</button>
          </div>
        </>}
        {failure && <p className="action-err" role="alert">{failure.message}</p>}
        <button className="btn ghost sm" type="button" disabled={busy} onClick={onRefresh}><RefreshCw size={14} />刷新状态</button>
      </div>
    </div>
  </div>;
}
