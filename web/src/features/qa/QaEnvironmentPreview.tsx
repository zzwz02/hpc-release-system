import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet } from "../../api/http";

interface EnvironmentPreview {
  environment: string;
  rows: string[][];
  truncated: boolean;
}

export function QaEnvironmentPreview({ reportId, environment }: { reportId: number; environment: string }) {
  const { user } = useAuth();
  const identity = JSON.stringify([user?.username, user?.role, reportId, environment]);
  const [anchor, setAnchor] = useState<{ identity: string; rect: DOMRect } | null>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const popup = useRef<HTMLDivElement>(null);
  const id = useId();
  const open = anchor?.identity === identity;
  const preview = useQuery({
    queryKey: ["qa-environment-preview", user?.username, user?.role, reportId, environment],
    queryFn: () => apiGet<EnvironmentPreview>(`/api/qa/test-results/${reportId}/environment?${new URLSearchParams({ name: environment })}`),
    enabled: open,
    // Report IDs are immutable; repeated rows can share the same sheet preview.
    staleTime: Infinity, retry: false,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
  });

  useLayoutEffect(() => {
    if (!open || !anchor || !popup.current) return;
    const { width, height } = popup.current.getBoundingClientRect();
    const rect = anchor.rect;
    setPosition({
      left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
      top: Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - height - 12)),
    });
  }, [open, anchor, preview.data, preview.error]);

  useEffect(() => {
    if (!open) return;
    const close = () => setAnchor(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  return <>
    <button type="button" className="qa-env-trigger" aria-describedby={open ? id : undefined}
      onMouseEnter={event => setAnchor({ identity, rect: event.currentTarget.getBoundingClientRect() })}
      onMouseLeave={() => setAnchor(null)}
      onFocus={event => setAnchor({ identity, rect: event.currentTarget.getBoundingClientRect() })}
      onBlur={() => setAnchor(null)} onKeyDown={event => { if (event.key === "Escape") setAnchor(null); }}>
      {environment}
    </button>
    {open && createPortal(<div id={id} ref={popup} className="qa-env-preview" role="tooltip" style={position}>
      <strong>{environment}</strong>
      {preview.isPending && <p className="muted">正在读取环境页…</p>}
      {preview.error && <p className="lerr">{preview.error.message}</p>}
      {preview.data && <>
        <div className="qa-env-preview-content">
          {preview.data.rows.length ? <table><tbody>{preview.data.rows.map((row, index) => <tr key={index}>
            {row.map((value, column) => <td key={column}>{value}</td>)}
          </tr>)}</tbody></table> : <p className="muted">环境页为空。</p>}
        </div>
        <p className="small muted">{preview.data.truncated ? "仅显示部分内容；" : ""}完整环境页可下载原始 Excel 查看。</p>
      </>}
    </div>, document.body)}
  </>;
}
