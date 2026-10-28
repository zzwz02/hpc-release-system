#!/usr/bin/env bash
# Deploy one explicit revision of the independent jira_agent_skill repository.
set -euo pipefail
: "${SKILL_REPO:?set SKILL_REPO to the independent jira_agent_skill checkout}"
: "${SKILL_COMMIT:?set SKILL_COMMIT to an exact commit SHA}"
if [[ ! "$SKILL_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo 'SKILL_COMMIT must be a full commit SHA' >&2
  exit 1
fi
SRC="$(cd "$SKILL_REPO" && pwd -P)"
PACK_DIR="${PACK_DIR:-$HOME/hpc-jira-agent}"
mkdir -p "$PACK_DIR"
PACK_DIR="$(cd "$PACK_DIR" && pwd -P)"
if [[ "$PACK_DIR" == "$SRC" || "$PACK_DIR" == "$SRC/"* || "$SRC" == "$PACK_DIR/"* ]]; then
  echo 'PACK_DIR and SKILL_REPO must be separate directories' >&2
  exit 1
fi
COMMIT="$(git -C "$SRC" rev-parse --verify "$SKILL_COMMIT^{commit}")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git -C "$SRC" archive "$COMMIT" AGENTS.md .agents .gitignore | tar -x -C "$STAGE"
if [[ ! -f "$STAGE/AGENTS.md" || ! -f "$STAGE/.agents/skills/hpc-jira-agent/SKILL.md" ]]; then
  echo 'incomplete knowledge pack' >&2
  exit 1
fi
mkdir -p "$PACK_DIR/workspaces"
cp -a "$STAGE/." "$PACK_DIR/"
printf '{"commit":"%s"}\n' "$COMMIT" > "$PACK_DIR/.skill-version.json"
if [[ ! -e "$PACK_DIR/.git" ]]; then git -C "$PACK_DIR" init -q; fi
printf 'knowledge pack %s copied to %s\n' "$COMMIT" "$PACK_DIR"
