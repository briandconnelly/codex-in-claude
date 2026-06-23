#!/usr/bin/env bash
set -euo pipefail

if [ -z "${CLAUDE_ENV_FILE:-}" ]; then
  echo "session-env.sh: CLAUDE_ENV_FILE not provided; GH_TOKEN not injected" >&2
  exit 1
fi

line='export GH_TOKEN="$($HOME/.claude/bot-shims/bot-token || echo BOT-TOKEN-MINT-FAILED)"'
grep -qxF -- "$line" "$CLAUDE_ENV_FILE" 2>/dev/null || printf '%s\n' "$line" >> "$CLAUDE_ENV_FILE"
