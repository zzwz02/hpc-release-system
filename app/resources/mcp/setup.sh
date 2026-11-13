#!/usr/bin/env bash
# MCP setup for the HPC release system (served by GET /api/mcp/setup.sh).
#
#   bash <(curl -fsSL __BASE_URL__/api/mcp/setup.sh)            # menu
#   bash <(curl -fsSL __BASE_URL__/api/mcp/setup.sh) configure  # or status / regenerate / print / uninstall
#
#   MCP_CLIENTS=claude bash <(curl -fsSL __BASE_URL__/api/mcp/setup.sh) use-token
#   use-token prompts for an existing token, or accepts MCP_TOKEN.
#
# Each user has ONE active token.  Configuring another machine reuses it
# (found in this machine's config, or pasted); regenerating it makes every
# machine that still has the old one stop working.
#
# Config files are edited directly (no claude/codex CLI needed), so a machine
# with only the VS Code extensions works too:
#   Claude Code  ~/.claude.json            mcpServers.release-system
#   Codex        ~/.codex/config.toml      [mcp_servers.release-system]
# The password goes to curl on stdin, never on a command line.
set -euo pipefail

BASE_URL="__BASE_URL__"
MCP_URL="$BASE_URL/mcp"
SERVER_NAME="release-system"
CLAUDE_JSON="$HOME/.claude.json"
CODEX_TOML="${CODEX_HOME:-$HOME/.codex}/config.toml"
TOKEN_NAME="${MCP_TOKEN_NAME:-$(hostname -s 2>/dev/null || hostname) 一键配置}"

if { : </dev/tty; } 2>/dev/null; then tty_in=/dev/tty; else tty_in=/dev/stdin; fi
say() { printf '%s\n' "$*" >&2; }
die() { say "✘ $*"; exit 1; }
ask() { printf '%s' "$1" >&2; local reply=""; read -r reply <"$tty_in" || true; printf '%s' "$reply"; }
ask_secret() { printf '%s' "$1" >&2; local reply=""; read -rs reply <"$tty_in" || true; printf '\n' >&2; printf '%s' "$reply"; }
yes_no() {  # yes_no "question" default(y|n)
  local reply; reply="$(ask "$1")"; reply="${reply:-$2}"
  case "$reply" in [Yy]*) return 0 ;; *) return 1 ;; esac
}
command -v python3 >/dev/null 2>&1 || die "需要 python3 来修改配置文件；可改用选项 4 输出配置后手动修改"

# ── server API (username + password) ─────────────────────────
username=""
password=""
credentials() {
  [ -n "$username" ] && return 0
  local default_user="${MCP_USER:-${USER:-}}"
  username="$(ask "网站 / 域账号用户名 [$default_user]: ")"
  username="${username:-$default_user}"
  password="$(ask_secret '密码: ')"
  [ -n "$username" ] && [ -n "$password" ] || die "用户名和密码不能为空"
}

api() {  # api ACTION → plain-text body on stdout (call credentials first, outside $(...))
  [ -n "$username" ] || die "内部错误：未输入用户名"
  local body status
  body="$(mktemp)"
  status="$(printf '%s' "$password" | curl -sS -o "$body" -w '%{http_code}' -H 'Accept: text/plain' \
    --data-urlencode "action=$1" --data-urlencode "username=$username" \
    --data-urlencode "name=$TOKEN_NAME" --data-urlencode "password@-" \
    "$BASE_URL/api/mcp/account")" || { rm -f "$body"; die "无法连接 $BASE_URL"; }
  if [ "$status" != "200" ]; then
    local message; message="$(cat "$body")"; rm -f "$body"
    die "失败（HTTP $status）：$message"
  fi
  cat "$body"; rm -f "$body"
}

server_status() {  # sets active_prefix / active_line
  credentials  # prompt here: api runs in a subshell, which cannot keep the answers
  local line; line="$(api status | head -n 1)"
  active_prefix=""; active_line=""
  if [ "${line%%$'\t'*}" = "active" ]; then
    IFS=$'\t' read -r _ active_prefix name created last_used last_client <<<"$line"
    active_line="$active_prefix…（$name，创建于 $created，最近使用 $last_used，客户端 $last_client）"
  fi
}

token_works() {  # token_works TOKEN → 0 when this server accepts it
  [ "$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$MCP_URL" -H 'Content-Type: application/json' \
      -H "Authorization: Bearer $1" -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' 2>/dev/null)" = "200" ]
}

# ── local config files ───────────────────────────────────────
config() {  # config find|write|remove [TOKEN] [CLIENTS...]
  SERVER_NAME="$SERVER_NAME" MCP_URL="$MCP_URL" CLAUDE_JSON="$CLAUDE_JSON" CODEX_TOML="$CODEX_TOML" \
    python3 - "$@" <<'PY'
import json, os, re, shutil, sys, tempfile

name, url = os.environ["SERVER_NAME"], os.environ["MCP_URL"]
claude_path, codex_path = os.environ["CLAUDE_JSON"], os.environ["CODEX_TOML"]
op, token, clients = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else ""), set(sys.argv[3:])
section = re.compile(r"^\s*\[\s*mcp_servers\." + re.escape(name) + r"(\.[^\]]*)?\s*\]\s*$")
header = re.compile(r"^\s*\[")

def save(path, text):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak-release-mcp")
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".release-mcp-")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def claude_load():
    if not os.path.exists(claude_path):
        return {}
    with open(claude_path, encoding="utf-8") as fh:
        text = fh.read().strip()
    return json.loads(text) if text else {}

def codex_strip(text):
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        if header.match(line):
            skipping = bool(section.match(line))
        if not skipping:
            kept.append(line)
    return "".join(kept).rstrip("\n")

def codex_token(text):
    inside = False
    for line in text.splitlines():
        if header.match(line):
            inside = bool(section.match(line))
        elif inside:
            found = re.search(r'Bearer\s+(rsmcp_[A-Za-z0-9_\-]+)', line)
            if found:
                return found.group(1)
    return ""

if op == "find":
    try:
        auth = claude_load().get("mcpServers", {}).get(name, {}).get("headers", {}).get("Authorization", "")
        if auth.startswith("Bearer "):
            print("claude", auth[7:])
    except (ValueError, AttributeError):
        print("claude-unreadable", "-")
    if os.path.exists(codex_path):
        found = codex_token(open(codex_path, encoding="utf-8").read())
        if found:
            print("codex", found)
elif op in ("write", "remove"):
    if "claude" in clients:
        data = claude_load()
        servers = data.setdefault("mcpServers", {})
        if op == "write":
            servers[name] = {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"}}
        if op == "write" or servers.pop(name, None) is not None:
            save(claude_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if "codex" in clients:
        text = open(codex_path, encoding="utf-8").read() if os.path.exists(codex_path) else ""
        stripped = codex_strip(text)
        if op == "write":
            block = (f"[mcp_servers.{name}]\nurl = \"{url}\"\n"
                     f"http_headers = {{ \"Authorization\" = \"Bearer {token}\" }}\n"
                     "enabled = true\nstartup_timeout_sec = 30\n")
            save(codex_path, (stripped + "\n\n" if stripped else "") + block)
        elif stripped != text.rstrip("\n"):
            save(codex_path, stripped + "\n" if stripped else "")
PY
}

has_extension() {
  local dir
  for dir in "$HOME/.vscode-server/extensions" "$HOME/.vscode/extensions" "$HOME/.cursor-server/extensions"; do
    compgen -G "$dir/$1*" >/dev/null 2>&1 && return 0
  done
  return 1
}

detect_clients() {  # MCP_CLIENTS=claude,codex overrides detection
  if [ -n "${MCP_CLIENTS:-}" ]; then printf '%s\n' ${MCP_CLIENTS//,/ }; return; fi
  if command -v claude >/dev/null 2>&1 || [ -e "$CLAUDE_JSON" ] || [ -d "$HOME/.claude" ] \
     || has_extension anthropic.claude-code; then echo claude; fi
  if command -v codex >/dev/null 2>&1 || [ -d "$(dirname "$CODEX_TOML")" ] || has_extension openai.chatgpt; then
    echo codex
  fi
}

local_token() {  # first token found in this machine's config files
  config find | awk '$1=="claude"||$1=="codex"{print $2; exit}'
}

other_harnesses() {  # other_harnesses TOKEN → doc links for clients this script does not configure
  cat <<EOF
# 其他 harness 请按各自文档添加 HTTP MCP 服务器，
# 地址 $MCP_URL，请求头 Authorization: Bearer ${1:-<你的 token>}
# 发评论必须在 elicitation 表单中输入与 token 账号匹配的用户名；不支持的客户端无法发评论。附件上传不弹确认框，仍检查上传权限。
EOF
  cat <<'LINKS'
__HARNESS_LINKS__
LINKS
}

label() { case "$1" in claude) echo "Claude Code" ;; codex) echo "Codex" ;; esac; }

write_local() {  # write_local TOKEN
  local clients; clients="$(detect_clients | tr '\n' ' ')"
  if [ -z "${clients// /}" ]; then
    say "未检测到 Claude Code 或 Codex（CLI 或 VS Code 扩展）。"
    if yes_no "仍为两者写入配置？[y/N] " n; then clients="claude codex"; else print_config "$1"; return; fi
  fi
  if [ "$username" != "${USER:-}" ] && [ -n "$username" ]; then
    say "注意：配置写入 $HOME，此系统账号（${USER:-?}）下运行的 Claude/Codex 都将以 $username 的身份调用。"
  fi
  # shellcheck disable=SC2086
  config write "$1" $clients
  local client
  for client in $clients; do printf '✔ %-11s 已配置\n' "$(label "$client")" >&2; done
  say "请重启 Claude Code / Codex（VS Code 中执行 Developer: Reload Window），在对话里输入 /mcp 确认 $SERVER_NAME 已连接。"
  if [[ " $clients " == *" claude "* ]] && pgrep -u "$(id -u)" -x claude >/dev/null 2>&1; then
    say "提示：检测到正在运行的 Claude Code，它退出时可能覆盖 ~/.claude.json；若重启后看不到 $SERVER_NAME，关闭后重新运行本脚本。"
  fi
}

print_config() {  # print_config TOKEN
  local token="${1:-<你的 token>}"
  cat <<EOF

# ── Claude Code（CLI 与 VS Code 扩展通用）：~/.claude.json 顶层 "mcpServers" 中加入：
"$SERVER_NAME": {
  "type": "http",
  "url": "$MCP_URL",
  "headers": { "Authorization": "Bearer $token" }
}
# 或使用 CLI：
claude mcp remove $SERVER_NAME -s user >/dev/null 2>&1; claude mcp add --transport http --scope user $SERVER_NAME $MCP_URL --header "Authorization: Bearer $token"

# ── Codex（CLI 与 VS Code 扩展通用）：~/.codex/config.toml 中加入（先删除旧的同名段落）：
[mcp_servers.$SERVER_NAME]
url = "$MCP_URL"
http_headers = { "Authorization" = "Bearer $token" }
enabled = true
startup_timeout_sec = 30

EOF
  other_harnesses "$token"
}

# ── actions ──────────────────────────────────────────────────
do_use_token() {
  local token="${MCP_TOKEN:-}"
  if [ -z "$token" ]; then token="$(ask_secret '粘贴你的 MCP token: ')"; fi
  [[ "$token" =~ ^rsmcp_[A-Za-z0-9_-]+$ ]] || die "MCP token 格式不正确"
  case "${MCP_CLIENTS:-}" in
    claude|codex|claude,codex) ;;
    *) die "请指定 MCP_CLIENTS=claude 或 MCP_CLIENTS=codex" ;;
  esac
  token_works "$token" || die "MCP token 无效、已吊销或无法连接网站"
  say "MCP URL：$MCP_URL"
  write_local "$token"
}

do_configure() {
  server_status
  say "MCP URL：$MCP_URL"
  local token=""
  if [ -n "$active_prefix" ]; then
    local found; found="$(local_token || true)"
    if [ -n "$found" ] && [ "${found:0:${#active_prefix}}" = "$active_prefix" ] && token_works "$found"; then
      token="$found"; say "复用本机已有的 token $active_prefix…"
    else
      say "你已有有效 token：$active_line"
      local pasted; pasted="$(ask_secret '粘贴该 token 复用（不影响其他机器）；直接回车则重新生成（其他机器上的旧 token 会失效）: ')"
      if [ -n "$pasted" ]; then
        [ "${pasted:0:${#active_prefix}}" = "$active_prefix" ] && token_works "$pasted" \
          || die "粘贴的 token 不是当前有效的 $active_prefix…"
        token="$pasted"
      fi
    fi
  fi
  if [ -z "$token" ]; then
    token="$(api regenerate | head -n 1)"
    say "已生成 token ${token:0:12}…（可运行选项 4 查看完整 token）"
  fi
  write_local "$token"
}

do_status() {
  server_status
  if [ -n "$active_prefix" ]; then say "服务器上的有效 token：$active_line"; else say "服务器上没有有效 token"; fi
  local found=0 client token
  while read -r client token; do
    [ -n "$client" ] || continue
    found=1
    case "$client" in
      claude-unreadable) say "本机 Claude Code：~/.claude.json 无法解析" ;;
      *) local state="已失效"
         [ -n "$active_prefix" ] && [ "${token:0:${#active_prefix}}" = "$active_prefix" ] && state="有效"
         say "本机 $(label "$client")：${token:0:12}…（$state）" ;;
    esac
  done < <(config find)
  [ "$found" = 1 ] || say "本机未配置 $SERVER_NAME"
}

do_regenerate() {
  server_status
  say "MCP URL：$MCP_URL"
  if [ -n "$active_prefix" ]; then
    say "当前有效 token：$active_line"
    yes_no "重新生成后它在所有机器上立即失效，其他机器需重新配置。继续？[y/N] " n || die "已取消"
  fi
  local token; token="$(api regenerate | head -n 1)"
  say "已生成新 token ${token:0:12}…（可运行选项 4 查看完整 token）"
  if yes_no "写入本机配置？[Y/n] " y; then write_local "$token"; else print_config "$token"; fi
}

do_print() {
  local token; token="$(local_token || true)"
  if [ -n "$token" ] && token_works "$token"; then
    say "使用本机配置中的 token ${token:0:12}…"
  else
    token="$(ask_secret '粘贴你的 token（直接回车则输出占位符）: ')"
  fi
  print_config "$token"
}

do_uninstall() {
  config remove "" claude codex
  say "✔ 已从 ~/.claude.json 和 Codex config.toml 移除 $SERVER_NAME（原文件备份为 *.bak-release-mcp）"
  say "token 本身仍然有效；要作废请选 3 重新生成，或在网站「AI助手 → MCP」吊销。"
}

action="${1:-}"
if [ -z "$action" ]; then
  say "HPC 发布系统 MCP"
  say "  1) 一键配置本机        2) 查询 token（仅显示前几位）"
  say "  3) 重新生成 token      4) 仅输出配置方法"
  say "  5) 卸载本机配置"
  action="$(ask '请选择 [1]: ')"
  action="${action:-1}"
fi
case "$action" in
  use-token)   do_use_token ;;
  1|configure)  do_configure ;;
  2|status)     do_status ;;
  3|regenerate) do_regenerate ;;
  4|print)      do_print ;;
  5|uninstall)  do_uninstall ;;
  *) die "未知操作：$action（可用：configure use-token status regenerate print uninstall）" ;;
esac
