#!/usr/bin/env bash
set -euo pipefail

BIND_IP="${BIND_IP:-127.0.0.1}"
PORT="${PORT:-4520}"
PACK_DIR="${PACK_DIR:-$HOME/hpc-jira-learning}"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.config/hpc-jira-learning/ws-token}"
CODEX_BIN="${CODEX_BIN:-codex}"
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex-jira-learning}"

if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "missing learning WebSocket token: $TOKEN_FILE" >&2
  exit 1
fi
if [[ ! -f "$PACK_DIR/AGENTS.md" || ! -f "$PACK_DIR/.agents/skills/jira-knowledge-curator/SKILL.md" ]]; then
  echo "missing learning pack: run deploy/jira-learning/sync-pack.sh" >&2
  exit 1
fi
if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
  echo "Codex executable not found: $CODEX_BIN" >&2
  exit 1
fi
mkdir -p "$PACK_DIR/workspaces"
cd "$PACK_DIR"
exec "$CODEX_BIN" app-server \
  --listen "ws://${BIND_IP}:${PORT}" \
  --ws-auth capability-token \
  --ws-token-file "$TOKEN_FILE" \
  -c 'approval_policy="never"' \
  -c 'sandbox_mode="read-only"' \
  -c "projects.\"${PACK_DIR}\".trust_level=\"trusted\""
