/**
 * Password terminal that puts server B's SSH public key on a user-given
 * user@host.  ssh-copy-id runs on server B (codex app-server command/exec);
 * this dialog relays what the user types and polls the output.  After
 * ssh-copy-id succeeds the website checks key login from server B, and the
 * succeeded session admits one hand-over (``onVerified``).
 */
import { useEffect, useRef, useState } from "react";
import {
  closeKeySession,
  getKeySession,
  sendKeySessionInput,
  startKeySession,
  type KeySession,
  type KeySessionStatus,
  type SshKeyInfo,
} from "./jiraAgentApi";

// eslint-disable-next-line no-control-regex
const ANSI_RE = /\[[0-9;?]*[A-Za-z]|\][^]*|\r/g;

function stripAnsi(text: string): string {
  return text.replace(ANSI_RE, "");
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const LIVE: KeySessionStatus[] = ["running", "verifying"];

export function SshKeyUploadDialog({
  target,
  keyInfo,
  onVerified,
  onClose,
}: {
  target: string;
  keyInfo: SshKeyInfo;
  onVerified: (sessionId: string) => void;
  onClose: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [session, setSession] = useState<KeySession | null>(null);
  const [output, setOutput] = useState("");
  const [password, setPassword] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const offsetRef = useRef(0);
  const outputRef = useRef<HTMLPreElement>(null);
  const reportedRef = useRef(false);

  const status = session?.status;
  const live = Boolean(status && LIVE.includes(status));

  useEffect(() => {
    if (!session || !LIVE.includes(session.status)) return undefined;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const next = await getKeySession(session.id, offsetRef.current);
        if (stopped) return;
        offsetRef.current = next.offset;
        if (next.output) setOutput((previous) => previous + stripAnsi(next.output));
        if (next.status !== session.status || next.message !== session.message) setSession(next);
      } catch (err) {
        if (!stopped) setError(errorMessage(err));
      }
    }, 700);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [session]);

  useEffect(() => {
    if (session?.status !== "succeeded" || reportedRef.current) return;
    reportedRef.current = true;  // a session admits one hand-over
    onVerified(session.id);
  }, [session, onVerified]);

  useEffect(() => {
    const element = outputRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [output]);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const started = await startKeySession({ agent_group: keyInfo.agent_group, target });
      offsetRef.current = started.offset;
      setOutput(stripAnsi(started.output));
      setSession(started);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function send(data: string) {
    if (!session) return;
    try {
      await sendKeySessionInput(session.id, data);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function close() {
    if (session && live) {
      try {
        await closeKeySession(session.id);
      } catch {
        // The session ends on the server by itself; closing the dialog is enough.
      }
    }
    onClose();
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="上传 SSH 公钥">
      <div className="dialog-card maxw-660">
        <h3>上传 SSH 公钥到 {target}</h3>
        <div className="dialog-body">
          {!session ? (
            <>
              <div className="jira-agent-warning jira-agent-key-warning" data-testid="jira-agent-key-warning">
                <strong>开始前请确认：</strong>
                <ul>
                  <li>
                    将把 {keyInfo.display_name} 在服务器 B 上的 SSH 公钥（{keyInfo.fingerprint}
                    {keyInfo.comment ? `，注释 ${keyInfo.comment}` : ""}）追加到 <code>{target}</code> 的
                    <code>~/.ssh/authorized_keys</code>。
                  </li>
                  <li>
                    之后 agent 可以<strong>免密登录</strong>该账号，并以该账号的权限执行命令。对话结束后公钥不会失效，
                    需要删除 authorized_keys 中这一行才能撤销。
                  </li>
                  <li>
                    <strong>请使用专用测试账号，不要使用个人账号。</strong>
                    否则系统无法防止 agent 以后未经授权使用你的个人账号登录。
                  </li>
                  <li>密码只经网站转发给服务器 B 上的 ssh-copy-id，不会保存或记录。</li>
                  <li>每次把工单交给自填机器都要重新确认并上传；公钥仍在对方机器上时 ssh-copy-id 会跳过，不再要求密码。</li>
                </ul>
              </div>
              <label className="jira-agent-check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                我已了解上述风险，{target} 是专用测试账号，不是个人账号
              </label>
            </>
          ) : (
            <>
              <pre ref={outputRef} className="jira-agent-output jira-agent-terminal" data-testid="jira-agent-key-terminal">
                {output || "正在服务器 B 上运行 ssh-copy-id..."}
              </pre>
              {status === "running" && (
                <>
                  <form
                    className="row jira-agent-terminal-input"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void send(`${password}\n`);
                      setPassword("");
                    }}
                  >
                    <input
                      type="password"
                      aria-label="密码"
                      autoComplete="off"
                      placeholder="出现 password: 提示后输入密码，回车发送"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                    />
                    <button type="submit" className="btn sm">
                      发送密码
                    </button>
                  </form>
                  <form
                    className="row jira-agent-terminal-input"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void send(`${text}\n`);
                      setText("");
                    }}
                  >
                    <input
                      aria-label="其他输入"
                      autoComplete="off"
                      placeholder="其他提示的明文回答（例如 yes），回车发送"
                      value={text}
                      onChange={(event) => setText(event.target.value)}
                    />
                    <button type="submit" className="btn sm">
                      发送
                    </button>
                  </form>
                </>
              )}
              {status === "verifying" && <p className="muted">ssh-copy-id 已完成，正在从服务器 B 测试免密登录...</p>}
              {session.message && (
                <p className={status === "succeeded" ? "jira-agent-ok" : "jira-agent-warning"}>{session.message}</p>
              )}
            </>
          )}
          {error && <p className="jira-agent-warning">{error}</p>}
        </div>
        <div className="dialog-actions">
          {!session && (
            <button type="button" className="btn primary" disabled={!confirmed || busy} onClick={() => void start()}>
              开始上传
            </button>
          )}
          <button type="button" className="btn" onClick={() => void close()}>
            {live ? "结束并关闭" : "关闭"}
          </button>
        </div>
      </div>
    </div>
  );
}
