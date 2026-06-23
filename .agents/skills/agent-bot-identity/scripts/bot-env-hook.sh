#!/usr/bin/env bash
set -euo pipefail

if [ -z "${CLAUDE_ENV_FILE:-}" ]; then
  echo "bot-env-hook.sh: CLAUDE_ENV_FILE not provided; identity guard not installed" >&2
  exit 1
fi

line='if [ ! -x "$HOME/.claude/bot-shims/bot-env" ]; then echo "bot-env missing or not executable — refusing to run with undetermined identity" >&2; exit 1; fi; __bot_env="$("$HOME/.claude/bot-shims/bot-env")" || { echo "bot-env failed — refusing to run with undetermined identity" >&2; exit 1; }; eval "$__bot_env" || { echo "bot-env emitted invalid shell — refusing to run with undetermined identity" >&2; exit 1; }; unset __bot_env'
grep -qxF -- "$line" "$CLAUDE_ENV_FILE" 2>/dev/null || printf '%s\n' "$line" >> "$CLAUDE_ENV_FILE"
