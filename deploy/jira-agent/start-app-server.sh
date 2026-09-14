#!/usr/bin/env bash
# Start the HPC JIRA agent's Codex app-server on server B.
#
# This is only a wrapper around `codex app-server`.  Permissions (sandbox and
# approval policy), the knowledge pack and all credentials (Codex auth.json,
# SSH keys for C/D/E) live on B; the website only connects with the token.
# See docs/jira-agent.md.
set -euo pipefail

BIND_IP="${BIND_IP:-127.0.0.1}"
PORT="${PORT:-4510}"
PACK_DIR="${PACK_DIR:-$HOME/hpc-jira-agent}"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.config/hpc-jira-agent/ws-token}"
CODEX_BIN="${CODEX_BIN:-codex}"

if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "missing websocket token file: $TOKEN_FILE" >&2
  echo "generate one with: mkdir -p \"$(dirname "$TOKEN_FILE")\" && python3 -c 'import secrets;print(secrets.token_urlsafe(32),end=\"\")' > \"$TOKEN_FILE\" && chmod 600 \"$TOKEN_FILE\"" >&2
  exit 1
fi
if [[ ! -f "$PACK_DIR/AGENTS.md" ]]; then
  echo "missing knowledge pack: $PACK_DIR/AGENTS.md (run deploy/jira-agent/sync-pack.sh)" >&2
  exit 1
fi
mkdir -p "$PACK_DIR/workspaces"

exec "$CODEX_BIN" app-server \
  --listen "ws://${BIND_IP}:${PORT}" \
  --ws-auth capability-token \
  --ws-token-file "$TOKEN_FILE" \
  -c 'approval_policy="never"' \
  -c 'sandbox_mode="danger-full-access"' \
  -c "projects.\"${PACK_DIR}\".trust_level=\"trusted\""
