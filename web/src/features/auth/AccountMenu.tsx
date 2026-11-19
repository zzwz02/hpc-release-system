import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { KeyRound, LogOut, Settings, UserRound, X } from "lucide-react";
import type { User } from "../../types";
import { ApiTokenDialog } from "./ApiTokenSetting";
import "./accountMenu.css";

export function AccountMenu({ user, logout }: { user: User; logout: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [pane, setPane] = useState<"profile" | "tokens" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  function closePane() {
    setPane(null);
    requestAnimationFrame(() => trigger.current?.focus());
  }

  async function signOut() {
    if (busy) return;
    setBusy(true); setError("");
    try { await logout(); setOpen(false); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "退出失败，请重试"); }
    finally { setBusy(false); }
  }

  return <div className="account-settings" ref={root}>
    <button ref={trigger} className="btn ghost sm icon account-settings-trigger" type="button"
      aria-label="账号设置" title="账号设置" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? "account-settings-menu" : undefined}
      onClick={() => setOpen(value => !value)} onKeyDown={event => {
        if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); }
      }}><Settings size={18} aria-hidden="true" /></button>
    {open && <div className="account-settings-menu" id="account-settings-menu" role="menu" aria-label="账号设置" ref={menu}
      onBlur={event => { if (!root.current?.contains(event.relatedTarget as Node | null)) setOpen(false); }}
      onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); setOpen(false); trigger.current?.focus(); return; }
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not(:disabled)") ?? []);
        const current = items.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
          : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[next]?.focus();
      }}>
      <button type="button" role="menuitem" onClick={() => { setPane("profile"); setOpen(false); }}><UserRound size={16} />账号信息</button>
      <button type="button" role="menuitem" onClick={() => { setPane("tokens"); setOpen(false); }}><KeyRound size={16} />API Token</button>
      <div role="separator" className="account-menu-separator" />
      <button type="button" role="menuitem" disabled={busy} onClick={() => void signOut()}><LogOut size={16} />{busy ? "退出中..." : "退出登录"}</button>
      {error && <p className="action-err small" role="alert">{error}</p>}
    </div>}
    {pane && createPortal(pane === "tokens"
      ? <ApiTokenDialog username={user.username} onClose={closePane} />
      : <AccountProfile user={user} onClose={closePane} />, document.body)}
  </div>;
}

function AccountProfile({ user, onClose }: { user: User; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => { close.current?.focus(); }, []);
  return <div className="dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="dialog-card account-profile" role="dialog" aria-modal="true" aria-labelledby="account-profile-title"
      onKeyDown={event => {
        if (event.key === "Escape") { event.stopPropagation(); onClose(); }
        if (event.key === "Tab") { event.preventDefault(); close.current?.focus(); }
      }}>
      <div className="account-token-heading"><h3 id="account-profile-title">账号信息</h3>
        <button ref={close} className="btn ghost sm icon" type="button" aria-label="关闭账号信息" title="关闭" onClick={onClose}><X size={18} /></button>
      </div>
      <dl className="account-profile-fields">
        <dt>姓名</dt><dd>{user.display_name || "未设置"}</dd>
        <dt>账号</dt><dd>{user.username}</dd>
        <dt>权限角色</dt><dd>{user.role}</dd>
      </dl>
    </div>
  </div>;
}
