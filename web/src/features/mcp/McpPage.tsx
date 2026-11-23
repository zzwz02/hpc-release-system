/**
 * MCP — AI助手 sub-tab for the website's MCP server.
 *
 * 接入配置: personal tokens (shown once), Claude Code / Codex setup and the
 * tool list.  使用统计: totals, daily / tool / user counts and the call log.
 * Lists show up to ten rows with internal scrolling. RM sees everyone; others see only
 * their own usage (the backend enforces this).
 */
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { can } from "../../lib/accessControl";
import mcpHarnesses from "../../../../shared/mcp_harnesses.json" with { type: "json" };
import { confirmDialog } from "../../lib/confirm";
import { toast } from "../../lib/toast";
import { McpPermissionsPane } from "./McpPermissionsPane";
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

type Pane = "setup" | "stats" | "permissions";
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
  const canManagePermissions = can(user?.role, "mcp.permissions.manage");
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
        {canManagePermissions && (
          <button className={`subtab${pane === "permissions" ? " active" : ""}`} onClick={() => setPane("permissions")}>
            权限配置
          </button>
        )}
      </div>
      {infoQuery.error && pane !== "permissions" && <div className="pane-error">{errorMessage(infoQuery.error)}</div>}
      {(pane === "setup" || (pane === "permissions" && !canManagePermissions)) && (
        <SetupPane viewer={viewer} tools={infoQuery.data?.tools ?? []}
          jiraProjects={infoQuery.data?.jira_read_projects ?? null}
          jiraWriteAll={infoQuery.data?.guest_jira_write_all ?? false}
          accessWarnings={infoQuery.data?.access_warnings ?? []} />
      )}
      {pane === "stats" && (
        <StatsPane viewer={viewer} canViewAll={infoQuery.data?.can_view_all ?? false} />
      )}
      {canManagePermissions && (
        <McpPermissionsPane key={viewer} viewer={viewer} active={pane === "permissions"} />
      )}
    </section>
  );
}

// ─────────────────────────────────────────────────────────────
// 接入配置
// ─────────────────────────────────────────────────────────────

function SetupPane({ viewer, tools, jiraProjects, jiraWriteAll, accessWarnings }: {
  viewer: string;
  tools: { name: string; title: string; category: string; read_only: boolean; description: string }[];
  jiraProjects: string[] | null;
  jiraWriteAll: boolean;
  accessWarnings: string[];
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
  const bashSetupCommand = `bash <(curl -fsSL ${shellQuote(`${base}/api/mcp/setup.sh`)})`;
  const powershellSetupCommand = `irm '${base.replace(/'/g, "''")}/api/mcp/setup.ps1' | iex`;

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
          <p className="mt-0">
            在目标机器终端运行对应命令，按提示输入<strong>域账号用户名和密码</strong>。
          </p>
          <div role="group" aria-labelledby="mcp-setup-bash">
            <strong id="mcp-setup-bash">Linux / macOS / WSL（Bash）</strong>
            <CodeBlock text={bashSetupCommand} />
          </div>
          <div role="group" aria-labelledby="mcp-setup-powershell" className="mt-12">
            <strong id="mcp-setup-powershell">Windows（PowerShell）</strong>
            <CodeBlock text={powershellSetupCommand} />
          </div>
          <div className="mcp-token-notice">
            <p><strong>⚠️ 重要提醒（多机共用规则）：</strong></p>
            <ul>
              <li>
                每人仅保留 <strong>1 个有效 Token</strong>，支持多台机器共用；配置新机器时<strong>粘贴已有 Token 即可复用</strong>。
              </li>
              <li>
                若<strong>重新生成 Token</strong>，其他机器上正在使用的旧 Token 将<strong>立即失效</strong>。
              </li>
            </ul>
          </div>
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
            展开对应的客户端，将配置内容合并到指定文件。
            {!created?.token && <>使用已有 MCP token 替换 <code>&lt;你的 token&gt;</code>。</>}
            保存后重启客户端。VS Code 扩展可执行「Developer: Reload Window」。
          </p>
          <details className="mt-12">
            <summary>Claude Code</summary>
            <p className="hint">将 <code>release-system</code> 条目合并到 <code>~/.claude.json</code> 的 <code>mcpServers</code> 中。</p>
            <CodeBlock text={JSON.stringify({
              mcpServers: {
                "release-system": { type: "http", url, headers: { Authorization: `Bearer ${tokenText}` } },
              },
            }, null, 2)} />
          </details>
          <details className="mt-12">
            <summary>Codex</summary>
            <p className="hint">在 <code>~/.codex/config.toml</code> 中添加或替换同名配置段。设置了 <code>CODEX_HOME</code> 时使用该目录。</p>
            <CodeBlock text={[
              "[mcp_servers.release-system]",
              `url = "${url}"`,
              `http_headers = { "Authorization" = "Bearer ${tokenText}" }`,
              "enabled = true", "startup_timeout_sec = 30",
            ].join("\n")} />
            <p className="hint">
              Codex 的 <code>approval_policy = "never"</code> 可能自动拒绝用户名输入表单，导致没有弹窗。
              完全访问下可通过细粒度审批单独允许 MCP 表单。
            </p>
            <details className="mcp-codex-permissions">
              <summary className="small muted">完全访问下保留用户名弹窗</summary>
              <p className="hint">
                在实际运行 Codex 的机器上修改 <code>~/.codex/config.toml</code>（设置了 CODEX_HOME 时使用该目录），
                以下键放在文件顶层、所有表头之前，替换已有同名键：
              </p>
              <CodeBlock text={[
                'sandbox_mode = "danger-full-access"',
                'approval_policy = { granular = { sandbox_approval = false, rules = false, mcp_elicitations = true } }',
              ].join("\n")} />
              <p className="hint">
                仅需恢复弹窗时保留原 <code>sandbox_mode</code>，只调整审批策略。
                重启 Codex 或重载 VS Code 后开启新会话；命令行参数、profile、项目配置及客户端权限预设可能覆盖该设置，
                请核对会话实际生效的策略，避免 <code>--yolo</code> 或“完全访问”预设再次将其改回 <code>never</code>。
                若客户端不支持细粒度策略，可使用 <code>approval_policy = "on-request"</code>。
                已有 MCP 连接和 token 可继续使用。
                <a href="https://developers.openai.com/codex/config-reference/" target="_blank" rel="noreferrer">Codex 官方配置说明</a>
              </p>
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
          {jiraProjects && <p className="hint mt-0">当前账号仅可读取 {jiraProjects.join("、")} 项目的 JIRA，并使用下方列出的工具。</p>}
          {jiraWriteAll && <p className="hint mt-0">当前账号可给任意项目的 JIRA 工单评论和上传附件。</p>}
          {accessWarnings.map(warning => <p className="hint" key={warning}>{warning}</p>)}
          <p className="hint mt-0">
            用自然语言即可，例如「MC3-7672 当前是什么状态」「把 MC3-7672 下载到本地」「搜索我名下未关闭的 JIRA」。
            查询只返回工单信息，不保存本地文件或下载附件；明确要求下载或保存时才落盘，优先使用指定目录，未指定时使用 <code>jira/&lt;KEY&gt;/</code>。
            {tools.some(tool => tool.name === "jira_add_comment") && <>可要求「把这个结论评论到 MC3-7672」。{!jiraWriteAll && "评论需为该单经办人或 RM。"}评论以发布系统 JIRA 账号发出并注明你的名字。</>}
            {tools.some(tool => tool.name === "jira_upload_attachment") && <>可要求「把 build.log 上传到 MC3-7672」。{!jiraWriteAll && "上传附件需为该单经办人或 RM。"}</>}
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

/** Cap each viewport at ten data rows, including rows that wrap onto several lines. */
function StatsRows({ children, className = "table" }: { children: ReactNode; className?: string }) {
  const container = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = container.current;
    if (!element) return;
    function measure() {
      if (!element) return;
      const rows = [...element.querySelectorAll<HTMLElement>("tbody > tr:not(.mcp-log-detail), .mcp-day")].slice(0, 10);
      if (!rows.length) return;
      const rowHeight = rows.reduce((height, row) => height + row.getBoundingClientRect().height, 0);
      if (!rowHeight) return;
      const style = getComputedStyle(element);
      const value = (name: string) => parseFloat(style.getPropertyValue(name)) || 0;
      const header = element.querySelector("thead")?.getBoundingClientRect().height ?? 0;
      const frame = ["padding-top", "padding-bottom", "border-top-width", "border-bottom-width"].reduce((sum, name) => sum + value(name), 0);
      const scrollbar = element.offsetHeight - element.clientHeight - value("border-top-width") - value("border-bottom-width");
      element.style.maxHeight = `${Math.ceil(header + rowHeight * 10 / rows.length + value("row-gap") * 9 + frame + scrollbar)}px`;
    }
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element.firstElementChild ?? element);
    return () => observer.disconnect();
  }, [children]);
  return <div ref={container} className={`${className} mcp-stats-scroll`}>{children}</div>;
}

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
            <div className="stat accent"><div className="num">{stats.totals.calls}</div><div className="lbl">总调用</div><div className="sub">含工具调用与附件下载</div></div>
            <div className="stat"><div className="num">{stats.totals.tool_calls}</div><div className="lbl">工具调用</div></div>
            <div className="stat"><div className="num">{stats.totals.users}</div><div className="lbl">使用人数</div></div>

            <div className={`stat ${stats.totals.errors ? "warn" : "ok"}`}>
              <div className="num">{stats.totals.errors}</div>
              <div className="lbl">失败</div>
              <div className="sub">失败率 {percent(stats.totals.errors, stats.totals.calls)}</div>
            </div>
          </div>

          <div className="mcp-grid">
            <div className="panel">
              <div className="panel-head"><h3>每日调用</h3></div>
              <StatsRows className="panel-body mcp-days">
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
              </StatsRows>
            </div>

            <div className="panel">
              <div className="panel-head"><h3>按工具</h3></div>
              <StatsRows>
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
              </StatsRows>
            </div>

            {canViewAll && (
              <div className="panel">
                <div className="panel-head"><h3>按用户</h3></div>
                <StatsRows>
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
                </StatsRows>
              </div>
            )}


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
    <div className="panel mt-12 mcp-logs">
      <div className="panel-head mcp-log-filters">
        <h3>调用日志</h3>
        <span className="spacer" />
        <select className="select sm" aria-label="按工具筛选调用日志" value={tool} onChange={(event) => resetting(setTool)(event.target.value)}>
          <option value="">全部工具</option>
          {[...new Set(tools)].map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
        <select className="select sm" aria-label="按状态筛选调用日志" value={status} onChange={(event) => resetting(setStatus)(event.target.value)}>
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
      <StatsRows>
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
      </StatsRows>
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
