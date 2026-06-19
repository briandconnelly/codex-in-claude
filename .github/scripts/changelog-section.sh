#!/usr/bin/env bash
# Print the CHANGELOG.md body for a given version section: lines after the
# "## [X.Y.Z]" heading up to (not including) the next "## " heading, with
# surrounding blank lines trimmed. Prints nothing if the section is absent.
set -euo pipefail

version="${1:?usage: changelog-section.sh <version>}"
file="${2:-CHANGELOG.md}"

awk -v ver="$version" '
  index($0, "## [" ver "]") == 1 { capture = 1; next }
  capture && /^## / { exit }
  capture { print }
' "$file" |
  # trim leading blank lines, then trailing blank lines
  awk 'NF { started = 1 } started { print }' |
  awk '{ lines[NR] = $0 } END { last = NR; while (last > 0 && lines[last] ~ /^[[:space:]]*$/) last--; for (i = 1; i <= last; i++) print lines[i] }'
