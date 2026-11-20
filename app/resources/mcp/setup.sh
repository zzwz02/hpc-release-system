#!/usr/bin/env bash
# MCP setup for the HPC release system (served by GET /api/mcp/setup.sh).
#
#   bash <(curl -fsSL __BASE_URL__/api/mcp/setup.sh)            # menu
#   bash <(curl -fsSL __BASE_URL__/api/mcp/setup.sh) configure  # or status / regenerate / uninstall
#
#   MCP_CLIENTS=claude,cursor bash <(curl -fsSL __BASE_URL__/api/mcp/setup.sh) use-token
#   Valid MCP_CLIENTS: claude, codex, cursor, opencode, codebuddy, dsh (alias: deepseek-harness), all
#   use-token prompts for an existing token, or accepts MCP_TOKEN.
#
# Each user has ONE active token. Configuring another machine reuses it
# (found in this machine's config, or pasted); regenerating it makes every
# machine that still has the old one stop working.
#
# Config files are edited directly (no client CLI needed), so machines
# with only VS Code extensions work too:
#   Claude Code       ~/.claude.json                                    mcpServers.release-system
#   Codex             ~/.codex/config.toml                              [mcp_servers.release-system]
#   Cursor            ~/.cursor/mcp.json                                mcpServers.release-system
#   OpenCode          ~/.config/opencode/opencode.jsonc (or existing)   mcp.release-system
#   CodeBuddy         ~/.codebuddy/.mcp.json + VS Code globalStorage    mcpServers.release-system
#   DeepSeek Harness  ~/.dsh/cordis.patch.yml                           @deepseek-ai/dsh-mcp-client
# The password goes to curl on stdin, never on a command line.
set -euo pipefail

BASE_URL="__BASE_URL__"
MCP_URL="$BASE_URL/mcp"
SERVER_NAME="release-system"
CLAUDE_JSON="$HOME/.claude.json"
CODEX_TOML="${CODEX_HOME:-$HOME/.codex}/config.toml"
CURSOR_JSON="$HOME/.cursor/mcp.json"
OPENCODE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
OPENCODE_JSON="${OPENCODE_CONFIG:-$OPENCODE_DIR/opencode.jsonc}"
DSH_HOME_DIR="${DSH_HOME:-$HOME/.dsh}"
DSH_PATCH_YML="$DSH_HOME_DIR/cordis.patch.yml"
ALL_CLIENTS="claude codex cursor opencode codebuddy dsh"
TOKEN_NAME="${MCP_TOKEN_NAME:-$(hostname -s 2>/dev/null || hostname) 一键配置}"

if { : </dev/tty; } 2>/dev/null; then tty_in=/dev/tty; else tty_in=/dev/stdin; fi
# Keep prompts on stderr and API/config output on stdout, without ANSI bytes.
COLOR_RESET="" COLOR_INFO="" COLOR_SUCCESS="" COLOR_WARNING="" COLOR_ERROR="" COLOR_BOLD="" COLOR_DIM=""
if [ -z "${NO_COLOR+x}" ] && { [ -t 2 ] || [ "${FORCE_COLOR:-0}" != 0 ]; }; then
  COLOR_RESET=$'\033[0m' COLOR_INFO=$'\033[36m' COLOR_SUCCESS=$'\033[1;32m'
  COLOR_WARNING=$'\033[1;33m' COLOR_ERROR=$'\033[1;31m' COLOR_BOLD=$'\033[1;97m'
  COLOR_DIM=$'\033[2m'
fi
colored() { local color="$1"; shift; printf '%s%s%s\n' "$color" "$*" "$COLOR_RESET" >&2; }
say() { colored "$COLOR_INFO" "$*"; }
dim() { colored "$COLOR_DIM" "$*"; }
success() { colored "$COLOR_SUCCESS" "$*"; }
warn() { colored "$COLOR_WARNING" "$*"; }
die() { colored "$COLOR_ERROR" "✘ 错误：$*"; exit 1; }
ask() { printf '%s%s%s' "$COLOR_BOLD" "$1" "$COLOR_RESET" >&2; local reply=""; read -r reply <"$tty_in" || true; printf '%s' "$reply"; }
ask_secret() { printf '%s%s%s' "$COLOR_BOLD" "$1" "$COLOR_RESET" >&2; local reply=""; read -rs reply <"$tty_in" || true; printf '\n' >&2; printf '%s' "$reply"; }

reuse_notice() {
  printf '\n' >&2
  warn '━━━━━━━━━━━━━━━━━━━━━━ ⚠️  重要提示 ━━━━━━━━━━━━━━━━━━━━━━'
  if [ -n "${1:-}" ]; then
    colored "$COLOR_BOLD" '🔑 完整 MCP Token（请妥善保存，服务端仅保存摘要）：'
    colored "$COLOR_SUCCESS" "   $1"
    printf '\n' >&2
  fi
  warn '┃ 1. 每个账号仅保留【一个】有效 Token，支持多台机器共用。'
  warn '┃ 2. 配置其他机器时，直接粘贴此 Token 即可复用。'
  warn '┃ 3. 若选择 [重新生成]，其他机器上的旧 Token 将立即失效！'
  warn '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
}

show_token() {
  reuse_notice "$1"
}

yes_no() {  # yes_no "question" default(y|n)
  local reply; reply="$(ask "$1")"; reply="${reply:-$2}"
  case "$reply" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# ── server API (username + password) ─────────────────────────
username=""
password=""
credentials() {
  [ -n "$username" ] && return 0
  local default_user="${MCP_USER:-${USER:-}}"
  say "请输入 HPC 发布系统账号进行验证："
  username="$(ask "  网站 / 域账号用户名 [$default_user]: ")"
  username="${username:-$default_user}"
  password="$(ask_secret '  密码（输入时不显示）: ')"
  [ -n "$username" ] && [ -n "$password" ] || die "用户名和密码不能为空"
}

api() {  # api ACTION → plain-text body on stdout (call credentials first, outside $(...))
  [ -n "$username" ] || die "内部错误：未输入用户名"
  local body status
  body="$(mktemp)"
  status="$(printf '%s' "$password" | curl -sS -o "$body" -w '%{http_code}' -H 'Accept: text/plain' \
    --data-urlencode "action=$1" --data-urlencode "username=$username" \
    --data-urlencode "name=$TOKEN_NAME" --data-urlencode "password@-" \
    "$BASE_URL/api/mcp/account")" || { rm -f "$body"; die "无法连接服务器：$BASE_URL"; }
  if [ "$status" != "200" ]; then
    local message; message="$(cat "$body")"; rm -f "$body"
    die "请求失败（HTTP $status）：$message"
  fi
  cat "$body"; rm -f "$body"
}

server_status() {  # sets active_prefix / active_line
  credentials  # prompt here: api runs in a subshell, which cannot keep the answers
  local line; line="$(api status | head -n 1)"
  active_prefix=""; active_line=""
  if [ "${line%%$'\t'*}" = "active" ]; then
    IFS=$'\t' read -r _ active_prefix name created last_used last_client <<<"$line"
    active_line="$active_prefix…（名称：$name｜创建于：$created｜最近使用：$last_used｜客户端：$last_client）"
  fi
}

token_works() {  # token_works TOKEN → 0 when this server accepts it
  [ "$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$MCP_URL" -H 'Content-Type: application/json' \
      -H "Authorization: Bearer $1" -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' 2>/dev/null)" = "200" ]
}

# ── local config files ───────────────────────────────────────
config() {  # config find|write|remove [TOKEN] [CLIENTS...]
  if ! command -v python3 >/dev/null 2>&1; then
    warn "未检测到 python3，无法读取或修改本机配置文件。"
    return 127
  fi
  SERVER_NAME="$SERVER_NAME" MCP_URL="$MCP_URL" \
  CLAUDE_JSON="$CLAUDE_JSON" CODEX_TOML="$CODEX_TOML" \
  CURSOR_JSON="$CURSOR_JSON" OPENCODE_DIR="$OPENCODE_DIR" \
  OPENCODE_JSON="$OPENCODE_JSON" OPENCODE_CONFIG="${OPENCODE_CONFIG:-}" \
  DSH_PATCH_YML="$DSH_PATCH_YML" \
    python3 - "$@" <<'PY'
import json, os, re, shutil, sys, tempfile

home = os.path.expanduser("~")
name, url = os.environ["SERVER_NAME"], os.environ["MCP_URL"]
claude_path = os.environ["CLAUDE_JSON"]
codex_path = os.environ["CODEX_TOML"]
cursor_path = os.environ["CURSOR_JSON"]
opencode_path = os.environ["OPENCODE_JSON"]
dsh_path = os.environ["DSH_PATCH_YML"]
op = sys.argv[1]
token = sys.argv[2] if len(sys.argv) > 2 else ""
clients = set(sys.argv[3:])

codex_section = re.compile(r"^\s*\[\s*mcp_servers\." + re.escape(name) + r"(\.[^\]]*)?\s*\]\s*$")
codex_header = re.compile(r"^\s*\[")
token_re = re.compile(r"Bearer\s+(rsmcp_[A-Za-z0-9_\-]+)")
dsh_begin = f"# BEGIN {name}-mcp (managed by hpc-release-system)"
dsh_end = f"# END {name}-mcp"

def save(path, text, client):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak-release-mcp")
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".release-mcp-")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    print(client, path.replace("\r", "\\r").replace("\n", "\\n"))

def json_load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        text = fh.read().strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data

JSONC_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\r\n]*|/\*[\s\S]*?\*/|[{}\[\]:,]|[^\s{}\[\]:,/"\ufeff]+|\S')

def read_jsonc(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as source:
            text = source.read()
    else:
        text = "{}\n"

    def tokenize(value):
        return [match for match in JSONC_TOKEN.finditer(value)
                if not match.group().startswith(("//", "/*")) and match.group() != "\ufeff"]

    tokens = tokenize(text)
    if not tokens:
        text += ("" if text.endswith(("\r", "\n")) or not text else "\n") + "{}\n"
        tokens = tokenize(text)
    parts = [match.group() for match in tokens]
    data = json.loads(" ".join(
        part for index, part in enumerate(parts)
        if not (part == "," and index > 0 and index + 1 < len(parts)
                and parts[index - 1] not in ("{", "[") and parts[index + 1] in ("}", "]"))
    ))
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return text, tokens, data

def jsonc_properties(tokens, opening):
    properties = {}
    key = opening + 1
    while tokens[key].group() != "}":
        first = last = key + 2
        if tokens[first].group() in ("{", "["):
            depth = 1
            while depth:
                last += 1
                part = tokens[last].group()
                depth += (part in ("{", "[")) - (part in ("}", "]"))
        properties[json.loads(tokens[key].group())] = (key, first, last)
        key = last + 1
        if tokens[key].group() == ",":
            key += 1
    return properties

def render_jsonc(value, level, newline):
    return json.dumps(value, ensure_ascii=False, indent=2).replace("\n", newline + "  " * level)

def insert_jsonc_properties(text, opening, properties, additions, level, newline):
    rendered = render_jsonc(additions, level, newline)
    insertion = rendered[1:rendered.rfind(newline)]
    if properties:
        insertion += ","
    tail = text[opening:]
    if not tail.startswith(("\r", "\n")):
        insertion += newline + "  " * (level + bool(properties))
    return text[:opening] + insertion + tail

def edit_mcp_jsonc(path, section, client, entry):
    if entry is None and not os.path.exists(path):
        return
    text, tokens, data = read_jsonc(path)
    newline = "\r\n" if "\r\n" in text else "\n"
    root = jsonc_properties(tokens, 0)
    if section not in root:
        if entry is None:
            return
        additions = {}
        if not data and client == "opencode":
            additions["$schema"] = "https://opencode.ai/config.json"
        additions[section] = {name: entry}
        updated = insert_jsonc_properties(text, tokens[0].end(), root, additions, 0, newline)
    else:
        if not isinstance(data[section], dict):
            if entry is None:
                return
            raise ValueError(f"{section} must be a JSON object")
        opening = root[section][1]
        servers = jsonc_properties(tokens, opening)
        if name not in servers:
            if entry is None:
                return
            updated = insert_jsonc_properties(
                text, tokens[opening].end(), servers, {name: entry}, 1, newline,
            )
        else:
            key, first, last = servers[name]
            if entry is not None:
                updated = text[:tokens[first].start()] + render_jsonc(entry, 2, newline) + text[tokens[last].end():]
            else:
                # Remove one separator, leaving surrounding whitespace and comments in place.
                following = tokens[last + 1]
                preceding = tokens[key - 1]
                start, end = tokens[key].start(), tokens[last].end()
                if following.group() == ",":
                    updated = text[:start] + text[end:following.start()] + text[following.end():]
                elif preceding.group() == ",":
                    updated = text[:preceding.start()] + text[preceding.end():start] + text[end:]
                else:
                    updated = text[:start] + text[end:]
    save(path, updated, client)

def extract_bearer(auth):
    if isinstance(auth, str) and auth.startswith("Bearer "):
        return auth[7:]
    return ""

def codex_strip(text):
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        if codex_header.match(line):
            skipping = bool(codex_section.match(line))
        if not skipping:
            kept.append(line)
    return "".join(kept).rstrip("\n")

def codex_token(text):
    inside = False
    for line in text.splitlines():
        if codex_header.match(line):
            inside = bool(codex_section.match(line))
        elif inside:
            found = token_re.search(line)
            if found:
                return found.group(1)
    return ""

def codebuddy_cli_candidates():
    return [
        os.path.join(home, ".codebuddy", ".mcp.json"),
        os.path.join(home, ".codebuddy", "mcp.json"),
        os.path.join(home, ".codebuddy.json"),
    ]

def first_existing_config(paths):
    return next((path for path in paths if os.path.exists(path)), paths[0])

def opencode_global_paths():
    directory = os.environ["OPENCODE_DIR"]
    return [
        os.path.join(directory, filename) for filename in ("opencode.jsonc", "opencode.json", "config.json")
    ]

def opencode_config_path():
    if os.environ.get("OPENCODE_CONFIG"):
        return opencode_path
    return first_existing_config(opencode_global_paths())

def codebuddy_ext_paths():
    roots = [
        os.path.join(home, ".config", "Code", "User", "globalStorage"),
        os.path.join(home, ".config", "CodeBuddy", "User", "globalStorage"),
        os.path.join(home, ".config", "Cursor", "User", "globalStorage"),
        os.path.join(home, ".vscode-server", "data", "User", "globalStorage"),
        os.path.join(home, ".cursor-server", "data", "User", "globalStorage"),
        os.path.join(home, "Library", "Application Support", "Code", "User", "globalStorage"),
        os.path.join(home, "Library", "Application Support", "CodeBuddy", "User", "globalStorage"),
        os.path.join(home, "Library", "Application Support", "Cursor", "User", "globalStorage"),
    ]
    ext_ids = ("tencent-cloud.coding-copilot", "tencent.planning-genie")
    paths = []
    for root in roots:
        for ext_id in ext_ids:
            ext_dir = os.path.join(root, ext_id)
            if os.path.isdir(ext_dir):
                paths.append(os.path.join(ext_dir, "settings", "codebuddy_mcp_settings.json"))
    return paths

def codebuddy_config_paths(all_cli_files=False):
    paths = codebuddy_cli_candidates()
    if not all_cli_files:
        paths = [first_existing_config(paths)]
    return dict.fromkeys(paths + codebuddy_ext_paths())

def dsh_strip(text):
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == dsh_begin:
            skipping = True
            continue
        if skipping:
            if stripped == dsh_end:
                skipping = False
            continue
        kept.append(line)
    return "".join(kept).rstrip("\n")

def dsh_is_empty_list(text):
    non_comment = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return "".join(non_comment) == "[]"

def dsh_block(tok):
    esc_url = url.replace("'", "''")
    esc_tok = tok.replace("'", "''")
    return (
        f"{dsh_begin}\n"
        f"- insert:\n"
        f"    - id: mcp-{name}\n"
        f"      name: '@deepseek-ai/dsh-mcp-client'\n"
        f"      config:\n"
        f"        serverName: {name}\n"
        f"        transport: streamable-http\n"
        f"        url: '{esc_url}'\n"
        f"        headers:\n"
        f"          Authorization: 'Bearer {esc_tok}'\n"
        f"{dsh_end}\n"
    )

def dsh_token(text):
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == dsh_begin:
            in_block = True
            continue
        if in_block:
            if stripped == dsh_end:
                in_block = False
                continue
            found = token_re.search(line)
            if found:
                return found.group(1)
    # Fallback: manually configured entry with id: mcp-<name>
    in_manual = False
    for line in text.splitlines():
        if re.search(r"\bid\s*:\s*['\"]?mcp-" + re.escape(name) + r"['\"]?\s*$", line):
            in_manual = True
            continue
        if in_manual:
            if re.match(r"^\s*-\s+id\s*:", line) or re.match(r"^-\s+insert\s*:", line):
                in_manual = False
                continue
            found = token_re.search(line)
            if found:
                return found.group(1)
    return ""

def find_json_mcp_servers(client_key, path, section="mcpServers", jsonc=False):
    if not os.path.exists(path):
        return None
    try:
        data = read_jsonc(path)[2] if jsonc else json_load(path)
        auth = data.get(section, {}).get(name, {}).get("headers", {}).get("Authorization", "")
        tok = extract_bearer(auth)
        return (client_key, tok) if tok else None
    except (ValueError, AttributeError, TypeError):
        return (f"{client_key}-unreadable", "-")

if op == "find":
    seen = set()
    def emit(k, v):
        if (k, v) not in seen:
            seen.add((k, v))
            print(k, v)

    res = find_json_mcp_servers("claude", claude_path)
    if res:
        emit(*res)

    if os.path.exists(codex_path):
        found = codex_token(open(codex_path, encoding="utf-8").read())
        if found:
            emit("codex", found)

    res = find_json_mcp_servers("cursor", cursor_path)
    if res:
        emit(*res)

    res = find_json_mcp_servers("opencode", opencode_config_path(), "mcp", jsonc=True)
    if res:
        emit(*res)

    for cb_path in codebuddy_config_paths():
        res = find_json_mcp_servers("codebuddy", cb_path, jsonc=True)
        if res:
            emit(*res)

    if os.path.exists(dsh_path):
        found = dsh_token(open(dsh_path, encoding="utf-8").read())
        if found:
            emit("dsh", found)

elif op in ("write", "remove"):
    if "claude" in clients:
        if op == "write" or os.path.exists(claude_path):
            data = json_load(claude_path)
            servers = data.setdefault("mcpServers", {})
            if op == "write":
                servers[name] = {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"}}
                save(claude_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", "claude")
            elif servers.pop(name, None) is not None:
                save(claude_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", "claude")

    if "codex" in clients:
        if op == "write" or os.path.exists(codex_path):
            text = open(codex_path, encoding="utf-8").read() if os.path.exists(codex_path) else ""
            stripped = codex_strip(text)
            if op == "write":
                block = (f"[mcp_servers.{name}]\nurl = \"{url}\"\n"
                         f"http_headers = {{ \"Authorization\" = \"Bearer {token}\" }}\n"
                         "enabled = true\nstartup_timeout_sec = 30\n")
                save(codex_path, (stripped + "\n\n" if stripped else "") + block, "codex")
            elif stripped != text.rstrip("\n"):
                save(codex_path, stripped + "\n" if stripped else "", "codex")

    if "cursor" in clients:
        if op == "write" or os.path.exists(cursor_path):
            data = json_load(cursor_path)
            servers = data.setdefault("mcpServers", {})
            if op == "write":
                servers[name] = {"url": url, "headers": {"Authorization": f"Bearer {token}"}}
                save(cursor_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", "cursor")
            elif servers.pop(name, None) is not None:
                save(cursor_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", "cursor")

    if "opencode" in clients:
        entry = {
            "type": "remote",
            "url": url,
            "enabled": True,
            "oauth": False,
            "timeout": 30000,
            "headers": {"Authorization": f"Bearer {token}"},
        }
        if op == "write":
            edit_mcp_jsonc(opencode_config_path(), "mcp", "opencode", entry)
        else:
            for path in dict.fromkeys([opencode_path] + opencode_global_paths()):
                edit_mcp_jsonc(path, "mcp", "opencode", None)

    if "codebuddy" in clients:
        entry = {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"}}
        for cb_path in codebuddy_config_paths(all_cli_files=op == "remove"):
            edit_mcp_jsonc(cb_path, "mcpServers", "codebuddy", entry if op == "write" else None)

    if "dsh" in clients:
        if op == "write" or os.path.exists(dsh_path):
            text = open(dsh_path, encoding="utf-8").read() if os.path.exists(dsh_path) else ""
            stripped = dsh_strip(text)
            if op == "write":
                base = "" if (not stripped.strip() or dsh_is_empty_list(stripped)) else stripped + "\n\n"
                save(dsh_path, base + dsh_block(token), "dsh")
            elif stripped != text.rstrip("\n"):
                save(dsh_path, stripped + "\n" if stripped.strip() else "[]\n", "dsh")
PY
}

has_extension() {
  (
    shopt -s nocaseglob nullglob
    local dir matches
    for dir in "$HOME/.vscode-server/extensions" "$HOME/.vscode/extensions" \
               "$HOME/.cursor-server/extensions" "$HOME/.cursor/extensions"; do
      [ -d "$dir" ] || continue
      matches=("$dir/$1"*)
      [ "${#matches[@]}" -gt 0 ] && exit 0
    done
    exit 1
  )
}

has_codebuddy_storage() {
  local root ext
  for root in "$HOME/.config/Code/User/globalStorage" \
              "$HOME/.config/CodeBuddy/User/globalStorage" \
              "$HOME/.config/Cursor/User/globalStorage" \
              "$HOME/.vscode-server/data/User/globalStorage" \
              "$HOME/.cursor-server/data/User/globalStorage" \
              "$HOME/Library/Application Support/Code/User/globalStorage" \
              "$HOME/Library/Application Support/CodeBuddy/User/globalStorage" \
              "$HOME/Library/Application Support/Cursor/User/globalStorage"; do
    for ext in "tencent-cloud.coding-copilot" "tencent.planning-genie"; do
      [ -d "$root/$ext" ] && return 0
    done
  done
  return 1
}

normalize_clients() {  # normalize_clients "claude,deepseek-harness" → space-separated canonical list
  local raw="${1//,/ }" item canon out=""
  for item in $raw; do
    canon="$(printf '%s' "$item" | tr '[:upper:]' '[:lower:]')"
    case "$canon" in
      all)
        printf '%s\n' $ALL_CLIENTS
        return 0 ;;
      deepseek-harness) canon="dsh" ;;
      claude|codex|cursor|opencode|codebuddy|dsh) ;;
      *) return 1 ;;
    esac
    case " $out " in
      *" $canon "*) ;;
      *) out="$out $canon" ;;
    esac
  done
  [ -n "${out// /}" ] || return 1
  printf '%s\n' $out
}

detect_clients() {  # MCP_CLIENTS=claude,codex,cursor,opencode,codebuddy,dsh overrides detection
  if [ -n "${MCP_CLIENTS:-}" ]; then
    normalize_clients "$MCP_CLIENTS"
    return $?
  fi
  if command -v claude >/dev/null 2>&1 || [ -e "$CLAUDE_JSON" ] || [ -d "$HOME/.claude" ] \
     || has_extension anthropic.claude-code; then echo claude; fi
  if command -v codex >/dev/null 2>&1 || [ -d "$(dirname "$CODEX_TOML")" ] \
     || has_extension openai.chatgpt; then echo codex; fi
  if command -v cursor >/dev/null 2>&1 || command -v cursor-agent >/dev/null 2>&1 \
     || [ -d "$HOME/.cursor" ] || [ -d "$HOME/.cursor-server" ]; then echo cursor; fi
  if command -v opencode >/dev/null 2>&1 || [ -n "${OPENCODE_CONFIG:-}" ] \
     || [ -d "$(dirname "$OPENCODE_JSON")" ] || [ -d "$HOME/.opencode" ] \
     || has_extension sst-dev.opencode || has_extension koarthur.opencode; then echo opencode; fi
  if command -v codebuddy >/dev/null 2>&1 || [ -d "$HOME/.codebuddy" ] || [ -e "$HOME/.codebuddy.json" ] \
     || has_extension tencent-cloud.coding-copilot || has_extension tencent.planning-genie \
     || has_codebuddy_storage; then echo codebuddy; fi
  if command -v dsh >/dev/null 2>&1 || [ -d "$DSH_HOME_DIR" ] \
     || has_extension jager.dsh-vscode; then echo dsh; fi
}

local_token() {  # first token found in this machine's config files
  config find | awk '$1=="claude"||$1=="codex"||$1=="cursor"||$1=="opencode"||$1=="codebuddy"||$1=="dsh"{print $2; exit}'
}

other_harnesses() {  # other_harnesses TOKEN → doc links for clients this script does not configure
  cat <<EOF
# 其他客户端（如 Windsurf / Cline 等）请按各自文档添加 HTTP MCP 服务器：
#   地址：$MCP_URL
#   请求头：Authorization: Bearer ${1:-<你的 token>}
# 注意：发评论必须在 elicitation 表单中输入与 Token 账号匹配的用户名（不支持的客户端无法发评论）；附件上传不弹确认框，但仍校验权限。
EOF
  cat <<'LINKS'
__HARNESS_LINKS__
LINKS
}

label() {
  case "$1" in
    claude)    echo "Claude Code" ;;
    codex)     echo "Codex" ;;
    cursor)    echo "Cursor" ;;
    opencode)  echo "OpenCode" ;;
    codebuddy) echo "CodeBuddy" ;;
    dsh)       echo "DeepSeek Harness" ;;
    *)         echo "$1" ;;
  esac
}

show_config_changes() {  # show_config_changes ACTION RECORDS
  local entry message
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    printf '%s✔%s ' "$COLOR_SUCCESS" "$COLOR_RESET" >&2
    printf -v message '%-16s %s  %s' "$(label "${entry%% *}")" "$1" "${entry#* }"
    say "$message"
  done <<< "$2"
}

write_local() {  # write_local TOKEN
  local clients written_files
  if ! clients="$(detect_clients | tr '\n' ' ')"; then
    show_token "$1"
    die "客户端列表无效或未能检测本机客户端（支持：claude, codex, cursor, opencode, codebuddy, dsh, all）。请保存上方 Token，之后可直接复用。"
  fi
  if [ -z "${clients// /}" ]; then
    warn "未在本机检测到 Claude Code / Codex / Cursor / OpenCode / CodeBuddy / DeepSeek Harness。"
    if yes_no "是否仍为全部 6 款工具预先写入配置文件？[y/N] " n; then
      clients="$ALL_CLIENTS"
    else
      print_config "$1"
      return
    fi
  fi
  if [ "$username" != "${USER:-}" ] && [ -n "$username" ]; then
    printf '\n' >&2
    warn "⚠️  账号不一致提醒："
    warn "   配置将写入当前系统用户（${USER:-?}）的目录 $HOME 下，"
    warn "   后续该用户运行的 Coding 工具都将以【$username】的身份调用发布系统！"
  fi
  # shellcheck disable=SC2086
  if ! written_files="$(config write "$1" $clients)"; then
    show_token "$1"
    die "本机配置写入失败。请保存上方 Token，之后可直接复用。"
  fi
  printf '\n' >&2
  show_config_changes '配置已写入' "$written_files"
  if [[ " $clients " == *" cursor "* ]]; then
    say 'Cursor：IDE 与 Cursor CLI (agent) 共用 ~/.cursor/mcp.json；若未立即出现工具，请在 Cursor Settings → MCP 中刷新或启用。'
  fi
  if [[ " $clients " == *" dsh "* ]]; then
    say "DeepSeek Harness：已写入全局 patch（$DSH_PATCH_YML），重启 dsh / dsh web 后生效（VS Code 扩展会自动重连），工具名为 mcp__${SERVER_NAME}__*。"
  fi
  if [[ " $clients " == *" claude "* ]] && pgrep -u "$(id -u)" -x claude >/dev/null 2>&1; then
    printf '\n' >&2
    warn "⚠️  进程冲突提醒：检测到 Claude Code 正在运行！"
    warn "   它退出时可能会覆盖 ~/.claude.json。若重启后未看到 $SERVER_NAME，"
    warn "   请先彻底关闭 Claude Code，再重新运行本脚本。"
  fi
  show_token "$1"
}

print_config() {  # print_config TOKEN
  local token="${1:-<你的 token>}"
  if [[ "$token" =~ ^rsmcp_[A-Za-z0-9_-]+$ ]]; then show_token "$token"; fi
  cat <<EOF

# ── 1. Claude Code（CLI 与 VS Code 扩展通用）──────────────────────────
# 在 ~/.claude.json 顶层 "mcpServers" 中加入：
"$SERVER_NAME": {
  "type": "http",
  "url": "$MCP_URL",
  "headers": { "Authorization": "Bearer $token" }
}
# 或直接执行 CLI 命令：
claude mcp remove $SERVER_NAME -s user >/dev/null 2>&1; claude mcp add --transport http --scope user $SERVER_NAME $MCP_URL --header "Authorization: Bearer $token"

# ── 2. Codex（CLI 与 VS Code 扩展通用）────────────────────────────────
# 在 ~/.codex/config.toml 中加入（如已存在同名段落请先删除）：
# 若完全访问下没有用户名弹窗，将下面审批键放在文件顶层、所有表头之前，替换已有同名键：
# approval_policy = { granular = { sandbox_approval = false, rules = false, mcp_elicitations = true } }
# 保留原 sandbox_mode；需要完全访问时可设为 "danger-full-access"。
# 重启后开启新会话，核对实际策略；--yolo 或客户端“完全访问”预设可能再次覆盖为 never。
[mcp_servers.$SERVER_NAME]
url = "$MCP_URL"
http_headers = { "Authorization" = "Bearer $token" }
enabled = true
startup_timeout_sec = 30

# ── 3. Cursor（IDE 与 Cursor CLI 通用）────────────────────────────────
# 在 ~/.cursor/mcp.json 的 "mcpServers" 中加入：
"$SERVER_NAME": {
  "url": "$MCP_URL",
  "headers": { "Authorization": "Bearer $token" }
}

# ── 4. OpenCode（CLI 与 VS Code 扩展通用）─────────────────────────────
# 在 OpenCode 生效的全局配置（例如 ~/.config/opencode/opencode.jsonc）的 "mcp" 中加入：
"$SERVER_NAME": {
  "type": "remote",
  "url": "$MCP_URL",
  "enabled": true,
  "oauth": false,
  "timeout": 30000,
  "headers": { "Authorization": "Bearer $token" }
}

# ── 5. CodeBuddy（CLI 与 VS Code 扩展 / CodeBuddy IDE）────────────────
# CLI：在 ~/.codebuddy/.mcp.json 的 "mcpServers" 中加入（VS Code 扩展则在 codebuddy_mcp_settings.json）：
"$SERVER_NAME": {
  "type": "http",
  "url": "$MCP_URL",
  "headers": { "Authorization": "Bearer $token" }
}
# 或执行 CLI 命令：
codebuddy mcp add-json --scope user $SERVER_NAME '{"type":"http","url":"$MCP_URL","headers":{"Authorization":"Bearer $token"}}'

# ── 6. DeepSeek Harness（CLI / Web / VS Code 扩展通用）────────────────
# 合并到 ~/.dsh/cordis.patch.yml（对全部 profile 生效，重启 dsh 后加载）：
- insert:
    - id: mcp-$SERVER_NAME
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: $SERVER_NAME
        transport: streamable-http
        url: '$MCP_URL'
        headers:
          Authorization: 'Bearer $token'

EOF
  other_harnesses "$token"
}

# ── actions ──────────────────────────────────────────────────
confirm_regenerate() {
  yes_no '其他机器上的旧 Token 将立即失效，是否继续？[y/N]: ' n || die "已取消操作"
}

do_use_token() {
  local token="${MCP_TOKEN:-}"
  if [ -z "$token" ]; then token="$(ask_secret '请粘贴你的 MCP Token（输入时不显示）: ')"; fi
  [[ "$token" =~ ^rsmcp_[A-Za-z0-9_-]+$ ]] || die "Token 格式不正确（应以 rsmcp_ 开头）"
  normalize_clients "${MCP_CLIENTS:-}" >/dev/null 2>&1 \
    || die "请通过环境变量指定有效目标客户端，例如：MCP_CLIENTS=claude,cursor（支持：claude / codex / cursor / opencode / codebuddy / dsh / all）"
  token_works "$token" || die "Token 无效、已吊销，或无法连接到服务"
  say "服务地址：$MCP_URL"
  write_local "$token"
}

do_configure() {
  server_status
  say "服务地址：$MCP_URL"
  local token=""
  if [ -n "$active_prefix" ]; then
    local found; found="$(local_token || true)"
    if [ -n "$found" ] && [ "${found:0:${#active_prefix}}" = "$active_prefix" ] && token_works "$found"; then
      token="$found"
      success "✔ 检测到本机已有有效 Token，将直接复用"
    else
      printf '\n' >&2
      warn "⚠️  检测到服务端已存在有效 Token："
      warn "   $active_line"
      warn "   • 若你在其他机器配置过，请粘贴该 Token 以实现多机共用；"
      warn "   • 若直接回车重新生成，其他机器上的旧 Token 将立即失效！"
      local pasted; pasted="$(ask_secret '请粘贴已有 Token 复用（或直接回车重新生成）: ')"
      if [ -n "$pasted" ]; then
        [ "${pasted:0:${#active_prefix}}" = "$active_prefix" ] && token_works "$pasted" \
          || die "粘贴的 Token 与当前服务端有效前缀（$active_prefix…）不匹配或已失效"
        token="$pasted"
      fi
    fi
  fi
  if [ -z "$token" ]; then
    if [ -n "$active_prefix" ]; then confirm_regenerate; fi
    token="$(api regenerate | head -n 1)"
    success "✔ 已生成新 Token"
  fi
  write_local "$token"
}

do_status() {
  server_status
  printf '\n' >&2
  if [ -n "$active_prefix" ]; then
    say "☁️  服务端有效 Token：$active_line"
  else
    warn "☁️  服务端状态：当前没有任何有效 Token"
  fi
  local found=0 client token current_token="" local_config
  local_config="$(config find)" || die "无法读取本机配置，无法查询本机 Token。"
  while read -r client token; do
    [ -n "$client" ] || continue
    found=1
    case "$client" in
      *-unreadable)
        warn "💻 本机 $(label "${client%-unreadable}")：配置文件 JSON 格式错误，无法解析" ;;
      *) local state="已失效"
         [ -n "$active_prefix" ] && [ "${token:0:${#active_prefix}}" = "$active_prefix" ] && state="有效"
         if [ "$state" = "有效" ]; then
           success "💻 本机 $(label "$client")：（$state）"
           current_token="$token"
         else
           warn "💻 本机 $(label "$client")：$token（$state）"
         fi ;;
    esac
  done <<<"$local_config"
  if [ "$found" != 1 ]; then
    say "💻 本机状态：尚未配置 $SERVER_NAME"
    dim "   提示：服务端仅保存摘要，无法反查完整 Token；如需复用请从其他已配置机器复制。"
  elif [ -n "$current_token" ]; then
    show_token "$current_token"
  fi
}

do_regenerate() {
  server_status
  say "服务地址：$MCP_URL"
  if [ -n "$active_prefix" ]; then
    printf '\n' >&2
    warn "🚨 高危操作提醒：重新生成 Token"
    warn "   当前有效 Token：$active_line"
    warn "   一旦重新生成，上述旧 Token 将在【所有已配置机器】上立即失效！"
    confirm_regenerate
  fi
  local token; token="$(api regenerate | head -n 1)"
  success "✔ 已生成新 Token"
  if yes_no "是否立即写入本机配置？[Y/n] " y; then write_local "$token"; else print_config "$token"; fi
}

do_uninstall() {
  local removed_files remove_status=0
  # shellcheck disable=SC2086
  removed_files="$(config remove "" $ALL_CLIENTS)" || remove_status=$?
  show_config_changes '配置已移除' "$removed_files"
  if (( remove_status != 0 )); then
    colored "$COLOR_ERROR" "✘ 错误：本机配置移除失败。"
    return "$remove_status"
  fi
  if [ -n "$removed_files" ]; then
    dim "  （被修改的原配置文件已自动备份为 *.bak-release-mcp）"
  else
    say "本机配置中未找到 $SERVER_NAME"
  fi
  printf '\n' >&2
  warn "ℹ️  说明：此操作仅移除本机配置，Token 本身依然有效。"
  warn "   如需彻底作废 Token，请重新运行脚本选择「3) 重新生成 token」，或在网站「AI助手 → MCP」页面手动吊销。"
}

action="${1:-}"
if [ -z "$action" ]; then
  printf '\n' >&2
  colored "$COLOR_BOLD" "══════════════ HPC 发布系统 MCP 配置工具 ══════════════"
  say "  1) 一键配置本机      （自动检测客户端并写入配置）"
  say "  2) 查询 Token 状态   （对比服务端与本机配置，显示本机完整 Token）"
  say "  3) 重新生成 Token    （⚠️ 会使其他机器的旧 Token 失效）"
  say "  4) 卸载本机配置      （移除本机配置文件中的 MCP 项）"
  colored "$COLOR_BOLD" "═══════════════════════════════════════════════════════"
  action="$(ask '请选择操作编号 [默认 1]: ')"
  action="${action:-1}"
fi
case "$action" in
  use-token)    do_use_token ;;
  1|configure)  do_configure ;;
  2|status)     do_status ;;
  3|regenerate) do_regenerate ;;
  4|uninstall)  do_uninstall ;;
  *) die "未知操作：$action（可用参数：configure / use-token / status / regenerate / uninstall）" ;;
esac
