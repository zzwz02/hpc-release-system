#!/usr/bin/env bash
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/pack" && pwd -P)"
PACK_DIR="${PACK_DIR:-$HOME/hpc-jira-learning}"
mkdir -p "$PACK_DIR"
PACK_DIR="$(cd "$PACK_DIR" && pwd -P)"
if [[ "$PACK_DIR" == "$SRC" || "$PACK_DIR" == "$SRC/"* ]]; then
  echo "PACK_DIR must be outside the source pack" >&2
  exit 1
fi
cp -a "$SRC/." "$PACK_DIR/"
mkdir -p "$PACK_DIR/workspaces"
if [[ ! -e "$PACK_DIR/.git" ]]; then
  git -C "$PACK_DIR" init -q
fi
echo "learning pack copied to $PACK_DIR"
