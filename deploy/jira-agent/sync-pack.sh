#!/usr/bin/env bash
# Copy the HPC knowledge pack (AGENTS.md + skills) to PACK_DIR on server B.
#
# PACK_DIR is made a git repository so Codex treats it as the project root:
# AGENTS.md and .agents/skills then apply to every task workspace under
# PACK_DIR/workspaces/.  Existing workspaces are left untouched.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/hpc" && pwd)"
PACK_DIR="${PACK_DIR:-$HOME/hpc-jira-agent}"

mkdir -p "$PACK_DIR/workspaces"
cp -a "$SRC/." "$PACK_DIR/"
if [[ ! -d "$PACK_DIR/.git" ]]; then
  git -C "$PACK_DIR" init -q
fi
echo "knowledge pack synced to $PACK_DIR"
