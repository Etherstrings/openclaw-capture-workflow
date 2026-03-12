#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_SRC="$ROOT_DIR/openclaw-skill/knowledge-capture"
SKILL_DEST="$HOME/.openclaw/skills/knowledge-capture"

mkdir -p "$HOME/.openclaw/skills"
rm -rf "$SKILL_DEST"
cp -R "$SKILL_SRC" "$SKILL_DEST"

echo "Installed skill to $SKILL_DEST"
