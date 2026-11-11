import { apiGet, apiPost } from "../../api/http";

export const MCP_KEY = ["mcp"] as const;

export interface McpToken {
  id: number;
  username: string;
  name: string;
  token_prefix: string;
  created_at: string;
  last_used_at: string;
  revoked_at: string;
  /** Only on the active token: the client that used it last. */
  last_client?: string | null;
  /** Only in the create response; never shown again. */
  token?: string;
}

export interface McpToolInfo {
  name: string;
  title: string;
  category: string;
  read_only: boolean;
  description: string;
}

export interface McpInfo {
  endpoint: string;
  server_name: string;
  tools: McpToolInfo[];
  can_view_all: boolean;
}

export interface McpStats {
  since: string;
  scope: "all" | "mine";
  totals: { calls: number; errors: number; users: number; sessions: number; tool_calls: number };
  by_tool: { kind: string; category: string; tool: string; calls: number; errors: number; avg_ms: number; users: number }[];
  by_token: {
    token_id: number | null;
    token_name: string;
    token_prefix: string;
    revoked_at: string;
    username: string;
    display_name: string;
    calls: number;
    errors: number;
    tool_calls: number;
    sessions: number;
    clients: string | null;
    last_at: string;
  }[];
  by_category: { category: string; calls: number; errors: number; users: number }[];
  by_user: { username: string; display_name: string; calls: number; errors: number; tool_calls: number; last_at: string }[];
  by_client: { client: string; calls: number; users: number }[];
  by_day: { day: string; calls: number; errors: number; users: number }[];
  top_targets: { target_type: string; target: string; calls: number; users: number; last_at: string }[];
}

export interface McpLog {
  id: number;
  created_at: string;
  username: string;
  display_name: string;
  token_id: number | null;
  token_name: string;
  token_prefix: string;
  client_name: string;
  client_version: string;
  kind: string;
  category: string;
  tool: string;
  target_type: string;
  target: string;
  args_json: string;
  extra_json: string;
  status: "ok" | "error";
  error: string;
  result_summary: string;
  duration_ms: number;
}

export interface LogFilters {
  days: number;
  username: string;
  tool: string;
  status: string;
  target: string;
  token_id: string;
  offset: number;
}

export const getInfo = () => apiGet<McpInfo>("/api/mcp/info");
export const listTokens = () => apiGet<{ tokens: McpToken[]; active: McpToken | null }>("/api/mcp/tokens");
export const createToken = (name: string) =>
  apiPost<{ token: McpToken }>("/api/mcp/tokens", { name }).then((r) => r.token);
export const revokeToken = (id: number) => apiPost<{ token: McpToken }>(`/api/mcp/tokens/${id}/revoke`, {});

export function getStats(days: number, username: string, tokenId: string) {
  const params = new URLSearchParams({ days: String(days) });
  if (username) params.set("username", username);
  if (tokenId) params.set("token_id", tokenId);
  return apiGet<McpStats>(`/api/mcp/stats?${params}`);
}

export const LOG_PAGE_SIZE = 50;

export function getLogs(filters: LogFilters) {
  const params = new URLSearchParams({
    days: String(filters.days),
    limit: String(LOG_PAGE_SIZE),
    offset: String(filters.offset),
  });
  for (const key of ["username", "tool", "status", "target", "token_id"] as const) {
    if (filters[key]) params.set(key, filters[key]);
  }
  return apiGet<{ total: number; items: McpLog[] }>(`/api/mcp/logs?${params}`);
}
