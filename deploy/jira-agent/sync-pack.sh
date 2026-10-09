#!/usr/bin/env bash
# Copy the HPC knowledge pack into the agent project directory.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/hpc" && pwd -P)"
PACK_DIR="${PACK_DIR:-$HOME/hpc-jira-agent}"
mkdir -p "$PACK_DIR"
PACK_DIR="$(cd "$PACK_DIR" && pwd -P)"
if [[ "$PACK_DIR" == "$SRC" || "$PACK_DIR" == "$SRC/"* ]]; then
  echo "PACK_DIR must be outside the source knowledge pack" >&2
  exit 1
fi
if [[ ! -f "$SRC/AGENTS.md" || ! -f "$SRC/.gitignore" || ! -f "$SRC/.agents/skills/hpc-jira-agent/SKILL.md" ]]; then
  echo "incomplete source knowledge pack: $SRC" >&2
  exit 1
fi

mkdir -p "$PACK_DIR/workspaces"
cp -a "$SRC/." "$PACK_DIR/"
if [[ ! -e "$PACK_DIR/.git" ]]; then
  git -C "$PACK_DIR" init -q
fi
printf 'knowledge pack copied to %s\n' "$PACK_DIR"
