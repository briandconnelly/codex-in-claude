#!/usr/bin/env bash
set -euo pipefail

if [ -z "${CLAUDE_ENV_FILE:-}" ]; then
  echo "session-env.sh: CLAUDE_ENV_FILE not provided; GH_TOKEN not injected" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
case "$SCRIPT_DIR" in
  *'"'* | *'$'* | *'`'* | *'\'* )
    echo "session-env.sh: install path contains a shell metacharacter; refusing to install an unquotable line" >&2
    exit 1 ;;
esac
# Fails closed on both a crashed mint and a mint that succeeds with empty output:
# gh treats an empty GH_TOKEN as unset and falls back to personal credentials.
line='__t="$("'"$SCRIPT_DIR"'/bot-token")" || __t=""; export GH_TOKEN="${__t:-BOT-TOKEN-MINT-FAILED}"; unset __t'
grep -qxF -- "$line" "$CLAUDE_ENV_FILE" 2>/dev/null || printf '%s\n' "$line" >> "$CLAUDE_ENV_FILE"
