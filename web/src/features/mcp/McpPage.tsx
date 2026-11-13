/**
 * MCP — AI助手 sub-tab for the website's MCP server.
 *
 * 接入配置: personal tokens (shown once), Claude Code / Codex setup and the
 * tool list.  使用统计: totals, per tool / category / user / client / day,
 * most-used targets and the call log.  RM sees everyone; others see only
 * their own usage (the backend enforces this).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import mcpHarnesses from "../../../../shared/mcp_harnesses.json" with { type: "json" };
import { confirmDialog } from "../../lib/confirm";
import { toast } from "../../lib/toast";
import {
  LOG_PAGE_SIZE,
  createToken,
  getInfo,
  getLogs,
  getStats,
  listTokens,
  revokeToken,
  type LogFilters,
  type McpLog,
  type McpToken,
} from "./mcpApi";

type Pane = "setup" | "stats";
const DAY_OPTIONS = [7, 30, 90, 365];
/** How each harness handles a server-requested confirmation (shared/mcp_harnesses.json). */
const CONFIRM_MARKS: Record<string, { label: string; pill: string }> = {
  form: { label: "支持发布前确认", pill: "ok" },
  none: { label: "不支持用户名确认，无法发评论", pill: "bad" },
  unknown: { label: "未核实确认支持", pill: "warn" },
};
const KIND_LABELS: Record<string, string> = { connection: "连接", tool: "工具", download: "下载" };

function shellQuote(value: string): string {
  return "'" + value.replace(/'/g, "'\\''") + "'";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("已复制");
  } catch {
    toast.error("复制失败，请手动选择复制");
  }
}

function CodeBlock({ text }: { text: string }) {
  return (
    <div className="mcp-code">
      <pre>{text}</pre>
      <button className="btn sm" onClick={() => void copyText(text)}>复制</button>
    </div>
  );
}

export function McpPage() {
  const { user } = useAuth();
  const viewer = `${user?.username ?? ""}:${user?.role ?? ""}`;
  const [pane, setPane] = useState<Pane>("setup");
  const infoQuery = useQuery({ queryKey: ["mcp", "info", viewer], queryFn: getInfo });

  return (
    <section className="view active mcp-page">
      <div className="subtabs">
        <button className={`subtab${pane === "setup" ? " active" : ""}`} onClick={() => setPane("setup")}>
          接入配置
        </button>
        <button className={`subtab${pane === "stats" ? " active" : ""}`} onClick={() => setPane("stats")}>
          使用统计
        </button>
      </div>
      {infoQuery.error && <div className="pane-error">{errorMessage(infoQuery.error)}</div>}
      {pane === "setup" ? (
        <SetupPane viewer={viewer} tools={infoQuery.data?.tools ?? []} />
      ) : (
        <StatsPane viewer={viewer} canViewAll={infoQuery.data?.can_view_all ?? false} />
      )}
    </section>
  );
}

// ─────────────────────────────────────────────────────────────
// 接入配置
// ─────────────────────────────────────────────────────────────

function SetupPane({ viewer, tools }: {
  viewer: string;
  tools: { name: string; title: string; category: string; read_only: boolean; description: string }[];
}) {
  const queryClient = useQueryClient();
  const tokensKey = ["mcp", "tokens", viewer];
  const tokensQuery = useQuery({ queryKey: tokensKey, queryFn: listTokens });
  const [name, setName] = useState("");
  const [created, setCreated] = useState<McpToken | null>(null);
  const tokenText = created?.token ?? "<你的 token>";
  const origin = new URL(window.location.origin);
  origin.protocol = "http:";
  const base = origin.origin;
  const url = `${base}/mcp`;

  const createMutation = useMutation({
    mutationFn: () => createToken(name),
    onSuccess: (token) => {
      setCreated(token);
      setName("");
      void queryClient.invalidateQueries({ queryKey: tokensKey });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const revokeMutation = useMutation({
    mutationFn: (id: number) => revokeToken(id),
    onSuccess: () => {
      toast.success("已吊销");
      void queryClient.invalidateQueries({ queryKey: tokensKey });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const active = tokensQuery.data?.active ?? null;
  const history = (tokensQuery.data?.tokens ?? []).filter((token) => token.revoked_at);
  const setupCommand = `bash <(curl -fsSL ${shellQuote(`${base}/api/mcp/setup.sh`)})`;
  const clientCommand = (client: "claude" | "codex") =>
    `${created?.token ? `MCP_TOKEN=${shellQuote(created.token)} ` : ""}MCP_CLIENTS=${client} ${setupCommand} use-token`;

  async function regenerate() {
    if (active) {
      const ok = await confirmDialog({
        title: "重新生成 token",
        body: `当前 token ${active.token_prefix}… 会立即失效，所有用它配置的机器都需要重新配置。`,
        confirmText: "重新生成",
        danger: true,
      });
      if (!ok) return;
    }
    createMutation.mutate();
  }

  async function revoke(token: McpToken) {
    const ok = await confirmDialog({
      title: "吊销 token",
      body: `吊销 ${token.token_prefix}… 后，所有用它配置的 Claude Code / Codex 都将无法连接。`,
      confirmText: "吊销",
      danger: true,
    });
    if (ok) revokeMutation.mutate(token.id);
  }

  return (
    <div className="mcp-setup">
      <div className="panel">
        <div className="panel-head"><h3>一键配置（推荐，无需登录网站）</h3></div>
        <div className="panel-body">
          <p className="hint mt-0">
            在要使用的机器上运行，输入域账号用户名和密码，按菜单选择：
            1 一键配置本机、2 查询 token、3 重新生成 token、4 仅输出配置方法、5 卸载本机配置。
            脚本直接修改 <code>~/.claude.json</code> 和 <code>~/.codex/config.toml</code>（先备份），
            只装了 VS Code 扩展、没有 claude / codex 命令也能用。
          </p>
          <CodeBlock text={setupCommand} />
          <p className="hint">
            每人只有一个有效 token。在第二台机器上配置时，粘贴已有 token 即可复用，不影响其他机器；
            选择重新生成，则其他机器上的旧 token 立即失效。
            也可以直接带参数运行，例如 <code>{`${setupCommand} status`}</code>。
          </p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h3>我的 token</h3></div>
        <div className="panel-body">
          {tokensQuery.error && <div className="pane-error">{errorMessage(tokensQuery.error)}</div>}
          {active ? (
            <div className="mcp-active-token">
              <code className="mcp-active-prefix">{active.token_prefix}…</code>
              <span className="pill ok">有效</span>
              <span className="muted small">
                {active.name} · 创建于 {active.created_at} · 最近使用 {active.last_used_at || "未使用"}
                {active.last_client ? ` · ${active.last_client}` : ""}
              </span>
            </div>
          ) : (
            <p className="muted mt-0">还没有有效 token。</p>
          )}
          <div className="flex-row gap-8 wrap mt-12">
            <input
              className="input minw-230"
              placeholder="备注（可选）"
              value={name}
              maxLength={60}
              onChange={(event) => setName(event.target.value)}
            />
            <button className="btn primary" disabled={createMutation.isPending} onClick={() => void regenerate()}>
              {active ? "重新生成 token" : "生成 token"}
            </button>
            {active && (
              <button className="btn danger" disabled={revokeMutation.isPending} onClick={() => void revoke(active)}>
                吊销
              </button>
            )}
          </div>
          {created?.token && (
            <div className="mcp-new-token">
              <strong>新 token（只显示这一次，请立即保存；下方配置已自动填入）：</strong>
              <CodeBlock text={created.token} />
            </div>
          )}
          {history.length > 0 && (
            <details className="mt-12">
              <summary className="small muted">已失效的 token（{history.length}）</summary>
              <div className="table mt-12">
                <table>
                  <thead><tr><th>token</th><th>备注</th><th>创建</th><th>最近使用</th><th>失效于</th></tr></thead>
                  <tbody>
                    {history.map((token) => (
                      <tr key={token.id}>
                        <td><code>{token.token_prefix}…</code></td>
                        <td>{token.name}</td>
                        <td className="nowrap">{token.created_at}</td>
                        <td className="nowrap">{token.last_used_at || <span className="muted">未使用</span>}</td>
                        <td className="nowrap">{token.revoked_at}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h3>手动配置（CLI 与 VS Code 扩展通用）</h3></div>
        <div className="panel-body">
          <p className="hint mt-0">
            展开要使用的客户端，复制一条命令在目标机器的 Bash 中运行即可。无需安装客户端 CLI，VS Code 扩展也可用。
            {created?.token ? "命令已填入刚生成的 token。" : "运行后粘贴已有 MCP token，无需再次输入域账号密码。"}
            配置完成后重启客户端；VS Code 扩展执行「Developer: Reload Window」。
          </p>
          <details className="mt-12">
            <summary>Claude Code</summary>
            <p className="hint">自动备份并更新 <code>~/.claude.json</code>，保留其他 MCP 配置。</p>
            <CodeBlock text={clientCommand("claude")} />
            <details>
              <summary className="small muted">查看配置文件内容</summary>
              <CodeBlock text={JSON.stringify(
                { "release-system": { type: "http", url, headers: { Authorization: `Bearer ${tokenText}` } } },
                null, 2,
              )} />
            </details>
          </details>
          <details className="mt-12">
            <summary>Codex</summary>
            <p className="hint">自动备份并更新 <code>~/.codex/config.toml</code>（设置了 CODEX_HOME 时使用该目录），保留其他配置。</p>
            <CodeBlock text={clientCommand("codex")} />
            <details>
              <summary className="small muted">查看配置文件内容</summary>
              <CodeBlock text={[
                "[mcp_servers.release-system]",
                `url = "${url}"`,
                `http_headers = { "Authorization" = "Bearer ${tokenText}" }`,
                "enabled = true", "startup_timeout_sec = 30",
              ].join("\n")} />
            </details>
          </details>
          <details className="mt-12">
            <summary>其他 harness</summary>
            <p className="hint">
              各客户端配置格式不同，请按对应文档添加 HTTP（Streamable HTTP）MCP：
            </p>
            <CodeBlock text={`URL: ${url}\nAuthorization: Bearer ${tokenText}`} />
            <p className="hint">
              发评论前需在 elicitation 表单中输入本人域账号用户名，匹配 token 所属账号才发送；
              <strong>不支持 elicitation 的客户端无法发评论</strong>。附件上传不弹确认框，仍检查上传权限。
            </p>
            <ul className="mcp-harnesses">
              {mcpHarnesses.harnesses.map((harness) => {
                const mark = CONFIRM_MARKS[harness.confirm] ?? CONFIRM_MARKS.unknown;
                return (
                  <li key={harness.url}>
                    <a href={harness.url} target="_blank" rel="noreferrer">{harness.name}</a>
                    <span className={`pill ${mark.pill} ml-4`}>{mark.label}</span>
                    <div className="muted small">{harness.evidence}（核实 {harness.checked}）</div>
                  </li>
                );
              })}
            </ul>
          </details>
        </div>
      </div>


      <div className="panel">
        <div className="panel-head"><h3>可用工具</h3></div>
        <div className="panel-body">
          <p className="hint mt-0">
            用自然语言即可，例如「把 MC3-7672 拉到本地」「搜索我名下未关闭的 JIRA」「把这个结论评论到 MC3-7672」「把 build.log 上传到 MC3-7672」。
            拉取默认保存到当前目录的 <code>jira/&lt;KEY&gt;/</code>；评论和上传附件只限该单 assignee 或 RM，以发布系统 JIRA 账号发出（评论注明你的名字）。
            问到 cu-bridge、CUDA 迁移 MACA 的问题时，客户端会自动调用 <code>skill_load</code> 取内部方法再作答，不必自己提。
          </p>
          <div className="table">
            <table>
              <thead><tr><th>工具</th><th>分类</th><th>类型</th><th>说明</th></tr></thead>
              <tbody>
                {tools.map((tool) => (
                  <tr key={tool.name}>
                    <td className="nowrap"><strong>{tool.title}</strong><div className="muted small"><code>{tool.name}</code></div></td>
                    <td>{tool.category}</td>
                    <td>{tool.read_only ? <span className="pill">只读</span> : <span className="pill warn">写入</span>}</td>
                    <td className="small">{tool.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 使用统计
// ─────────────────────────────────────────────────────────────

/** The token's first characters identify it; the name is only a hint. */
function tokenPrefix(prefix: string, tokenId: number | null) {
  return prefix ? `${prefix}…` : tokenId === null ? "-" : `#${tokenId}`;
}

function tokenLabel(row: { token_id: number | null; token_name: string; token_prefix: string; username: string; display_name: string }) {
  const name = row.token_name ? ` ${row.token_name}` : "";
  return `${tokenPrefix(row.token_prefix, row.token_id)}${name}（${row.display_name || row.username}）`;
}

function percent(part: number, total: number) {
  return total ? `${Math.round((part / total) * 100)}%` : "-";
}

function StatsPane({ viewer, canViewAll }: { viewer: string; canViewAll: boolean }) {
  const [days, setDays] = useState(30);
  const [username, setUsername] = useState("");
  const [tokenId, setTokenId] = useState("");
  const statsQuery = useQuery({
    queryKey: ["mcp", "stats", viewer, days, username, tokenId],
    queryFn: () => getStats(days, username, tokenId),
  });
  const stats = statsQuery.data;
  const maxDay = Math.max(1, ...(stats?.by_day ?? []).map((day) => day.calls));

  return (
    <div className="mcp-stats">
      <div className="page-toolbar">
        <label className="small muted">时间范围</label>
        <select className="select sm" value={days} onChange={(event) => setDays(Number(event.target.value))}>
          {DAY_OPTIONS.map((value) => <option key={value} value={value}>最近 {value} 天</option>)}
        </select>
        {canViewAll && (
          <>
            <label className="small muted ml-8">用户</label>
            <select className="select sm" value={username} onChange={(event) => { setUsername(event.target.value); setTokenId(""); }}>
              <option value="">全部用户</option>
              {(stats?.by_user ?? []).map((row) => (
                <option key={row.username} value={row.username}>{row.display_name || row.username}</option>
              ))}
              {username && !(stats?.by_user ?? []).some((row) => row.username === username) && (
                <option value={username}>{username}</option>
              )}
            </select>
          </>
        )}
        <label className="small muted ml-8">Token</label>
        <select className="select sm" value={tokenId} onChange={(event) => setTokenId(event.target.value)}>
          <option value="">全部 token</option>
          {(stats?.by_token ?? []).filter((row) => row.token_id !== null).map((row) => (
            <option key={row.token_id} value={String(row.token_id)}>{tokenLabel(row)}</option>
          ))}
          {tokenId && !(stats?.by_token ?? []).some((row) => String(row.token_id) === tokenId) && (
            <option value={tokenId}>#{tokenId}</option>
          )}
        </select>
        <span className="spacer" />
        <span className="muted small">{stats?.scope === "mine" ? "仅显示你本人的使用记录" : "全部用户"}</span>
        <button className="btn sm" onClick={() => void statsQuery.refetch()}>刷新</button>
      </div>
      {statsQuery.error && <div className="pane-error">{errorMessage(statsQuery.error)}</div>}
      {stats && (
        <>
          <div className="stats">
            <div className="stat accent"><div className="num">{stats.totals.calls}</div><div className="lbl">总调用</div><div className="sub">含连接、工具与附件下载</div></div>
            <div className="stat"><div className="num">{stats.totals.tool_calls}</div><div className="lbl">工具调用</div></div>
            <div className="stat"><div className="num">{stats.totals.users}</div><div className="lbl">使用人数</div></div>
            <div className="stat"><div className="num">{stats.totals.sessions}</div><div className="lbl">会话数</div><div className="sub">每次 Claude Code / Codex 连接一个</div></div>
            <div className={`stat ${stats.totals.errors ? "warn" : "ok"}`}>
              <div className="num">{stats.totals.errors}</div>
              <div className="lbl">失败</div>
              <div className="sub">失败率 {percent(stats.totals.errors, stats.totals.calls)}</div>
            </div>
          </div>

          <div className="mcp-grid">
            <div className="panel">
              <div className="panel-head"><h3>每日调用</h3></div>
              <div className="panel-body mcp-days">
                {stats.by_day.length === 0 && <div className="muted">暂无记录</div>}
                {stats.by_day.map((day) => (
                  <div key={day.day} className="mcp-day" title={`${day.day}：${day.calls} 次，失败 ${day.errors}，${day.users} 人`}>
                    <span className="mcp-day-label">{day.day.slice(5)}</span>
                    <span className="mcp-day-bar">
                      <span style={{ width: `${(day.calls / maxDay) * 100}%` }} />
                    </span>
                    <span className="mcp-day-num">{day.calls}{day.errors ? <span className="mcp-err"> / {day.errors} 失败</span> : null}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="panel">
              <div className="panel-head"><h3>按工具</h3></div>
              <div className="table">
                <table>
                  <thead><tr><th>分类</th><th>工具</th><th>调用</th><th>失败</th><th>人数</th><th>平均耗时</th></tr></thead>
                  <tbody>
                    {stats.by_tool.map((row) => (
                      <tr key={`${row.kind}:${row.category}:${row.tool}`}>
                        <td>{row.category || KIND_LABELS[row.kind] || row.kind}</td>
                        <td><code>{row.tool}</code></td>
                        <td>{row.calls}</td>
                        <td className={row.errors ? "mcp-err" : ""}>{row.errors}</td>
                        <td>{row.users}</td>
                        <td className="nowrap">{row.avg_ms} ms</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {canViewAll && (
              <div className="panel">
                <div className="panel-head"><h3>按用户</h3></div>
                <div className="table">
                  <table>
                    <thead><tr><th>用户</th><th>调用</th><th>工具调用</th><th>失败</th><th>最近使用</th></tr></thead>
                    <tbody>
                      {stats.by_user.map((row) => (
                        <tr key={row.username}>
                          <td>
                            <button className="link-btn" onClick={() => setUsername(row.username)}>
                              {row.display_name || row.username}
                            </button>
                            <span className="muted small"> {row.username}</span>
                          </td>
                          <td>{row.calls}</td>
                          <td>{row.tool_calls}</td>
                          <td className={row.errors ? "mcp-err" : ""}>{row.errors}</td>
                          <td className="nowrap">{row.last_at}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="panel">
              <div className="panel-head"><h3>按 token</h3></div>
              <div className="table">
                <table>
                  <thead><tr><th>Token</th>{canViewAll && <th>用户</th>}<th>调用</th><th>会话</th><th>失败</th><th>客户端</th><th>最近使用</th></tr></thead>
                  <tbody>
                    {stats.by_token.map((row) => (
                      <tr key={row.token_id ?? "none"}>
                        <td className="nowrap">
                          <button className="link-btn" onClick={() => setTokenId(row.token_id === null ? "" : String(row.token_id))}>
                            <code>{tokenPrefix(row.token_prefix, row.token_id)}</code>
                          </button>
                          <div className="muted small">
                            {row.token_name || "-"}
                            {row.revoked_at && <span className="pill bad ml-4">已吊销</span>}
                          </div>
                        </td>
                        {canViewAll && <td className="nowrap">{row.display_name || row.username}</td>}
                        <td>{row.calls}</td>
                        <td>{row.sessions}</td>
                        <td className={row.errors ? "mcp-err" : ""}>{row.errors}</td>
                        <td className="small">{row.clients || <span className="muted">未知</span>}</td>
                        <td className="nowrap">{row.last_at}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head"><h3>客户端与分类</h3></div>
              <div className="table">
                <table>
                  <thead><tr><th>客户端</th><th>调用</th><th>人数</th></tr></thead>
                  <tbody>
                    {stats.by_client.map((row) => (
                      <tr key={row.client}><td>{row.client}</td><td>{row.calls}</td><td>{row.users}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="table mt-12">
                <table>
                  <thead><tr><th>分类</th><th>调用</th><th>失败</th><th>人数</th></tr></thead>
                  <tbody>
                    {stats.by_category.map((row) => (
                      <tr key={row.category}>
                        <td>{KIND_LABELS[row.category] ?? row.category}</td>
                        <td>{row.calls}</td>
                        <td className={row.errors ? "mcp-err" : ""}>{row.errors}</td>
                        <td>{row.users}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head"><h3>最常操作的对象</h3></div>
              <div className="table">
                <table>
                  <thead><tr><th>对象</th><th>调用</th><th>人数</th><th>最近</th></tr></thead>
                  <tbody>
                    {stats.top_targets.map((row) => (
                      <tr key={`${row.target_type}:${row.target}`}>
                        <td><span className="muted small">{row.target_type} </span>{row.target}</td>
                        <td>{row.calls}</td>
                        <td>{row.users}</td>
                        <td className="nowrap">{row.last_at}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      )}
      <LogsPanel viewer={viewer} days={days} username={username} tokenId={tokenId} tools={(stats?.by_tool ?? []).map((row) => row.tool)} />
    </div>
  );
}

function LogsPanel({ viewer, days, username, tokenId, tools }: {
  viewer: string;
  days: number;
  username: string;
  tokenId: string;
  tools: string[];
}) {
  const [tool, setTool] = useState("");
  const [status, setStatus] = useState("");
  const [target, setTarget] = useState("");
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);
  const filters: LogFilters = { days, username, tool, status, target: target.trim().toUpperCase(), token_id: tokenId, offset };
  const logsQuery = useQuery({ queryKey: ["mcp", "logs", viewer, filters], queryFn: () => getLogs(filters) });
  const total = logsQuery.data?.total ?? 0;
  const resetting = <T,>(setter: (value: T) => void) => (value: T) => { setter(value); setOffset(0); };

  return (
    <div className="panel mt-12">
      <div className="panel-head">
        <h3>调用日志</h3>
        <span className="spacer" />
        <select className="select sm" value={tool} onChange={(event) => resetting(setTool)(event.target.value)}>
          <option value="">全部工具</option>
          {[...new Set(tools)].map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
        <select className="select sm" value={status} onChange={(event) => resetting(setStatus)(event.target.value)}>
          <option value="">全部状态</option>
          <option value="ok">成功</option>
          <option value="error">失败</option>
        </select>
        <input
          className="input"
          placeholder="对象，如 MC3-7672"
          value={target}
          onChange={(event) => resetting(setTarget)(event.target.value)}
        />
      </div>
      {logsQuery.error && <div className="pane-error">{errorMessage(logsQuery.error)}</div>}
      <div className="table">
        <table>
          <thead>
            <tr><th>时间</th><th>用户</th><th>Token</th><th>客户端</th><th>工具</th><th>对象</th><th>结果</th><th>耗时</th></tr>
          </thead>
          <tbody>
            {(logsQuery.data?.items ?? []).map((log) => (
              <LogRow key={log.id} log={log} open={expanded === log.id} onToggle={() => setExpanded(expanded === log.id ? null : log.id)} />
            ))}
            {logsQuery.data && logsQuery.data.items.length === 0 && (
              <tr><td colSpan={8} className="muted">暂无记录</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="flex-row gap-8 mcp-pager">
        <span className="muted small">共 {total} 条</span>
        <span className="spacer" />
        <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LOG_PAGE_SIZE))}>上一页</button>
        <span className="small">{total ? Math.floor(offset / LOG_PAGE_SIZE) + 1 : 0} / {Math.ceil(total / LOG_PAGE_SIZE)}</span>
        <button className="btn sm" disabled={offset + LOG_PAGE_SIZE >= total} onClick={() => setOffset(offset + LOG_PAGE_SIZE)}>下一页</button>
      </div>
    </div>
  );
}

function prettyJson(text: string) {
  if (!text) return "";
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function LogRow({ log, open, onToggle }: { log: McpLog; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="mcp-log-row" onClick={onToggle}>
        <td className="nowrap">{log.created_at}</td>
        <td className="nowrap">{log.display_name || log.username}</td>
        <td className="nowrap small" title={log.token_name}>
          <code>{tokenPrefix(log.token_prefix, log.token_id)}</code>
          {log.token_name && <div className="muted">{log.token_name}</div>}
        </td>
        <td className="nowrap small">{log.client_name || <span className="muted">未知</span>}</td>
        <td className="nowrap"><span className="muted small">{KIND_LABELS[log.kind] ?? log.kind} </span><code>{log.tool}</code></td>
        <td className="nowrap">{log.target}</td>
        <td>
          {log.status === "ok"
            ? <span className="pill ok">成功</span>
            : <span className="pill bad" title={log.error}>失败</span>}
          <span className="small muted"> {log.status === "ok" ? log.result_summary : log.error}</span>
        </td>
        <td className="nowrap">{log.duration_ms} ms</td>
      </tr>
      {open && (
        <tr className="mcp-log-detail">
          <td colSpan={8}>
            <div className="mcp-log-detail-grid">
              <div><strong>参数</strong><pre>{prettyJson(log.args_json) || "-"}</pre></div>
              <div><strong>附加信息</strong><pre>{prettyJson(log.extra_json) || "-"}</pre></div>
              {log.error && <div><strong>错误</strong><pre>{log.error}</pre></div>}
              <div className="muted small">
                Token {tokenPrefix(log.token_prefix, log.token_id)}（{log.token_name || "未命名"}）· 客户端版本 {log.client_version || "-"} · 分类 {log.category || "-"}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
