#!/usr/bin/env bash
# Verifies installation selection and the per-installation token cache:
# bot-token honors BOT_INSTALL_ID, refuses non-numeric ids, never reads the
# legacy unkeyed cache, and bot-env resolves, propagates, and clears the
# selection. No GitHub API access anywhere: bot-token positives are cache
# hits, negatives fail before or at the (absent) key read, and bot-env runs
# against a stub bot-token that echoes the selection it saw. The real
# bot-token is a `uv run` script, so a cold uv cache may resolve its
# pyjwt/httpx dependencies from the package index once.
set -euo pipefail
# A Git hook may export repository context that makes scratch `git -C` commands
# target the real repository instead of the fixture.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_PREFIX
unset BOT_INSTALL_ID || true
FAIL=0
DIR="$(cd -- "$(mktemp -d)" >/dev/null 2>&1 && pwd -P)"
trap 'rm -rf "$DIR"' EXIT
SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)/scripts"

FAKE_HOME="$DIR/home"
CACHE_DIR="$FAKE_HOME/.cache/acme-agent"
mkdir -p "$CACHE_DIR"
future="$(($(date +%s) + 3000))"
printf '{"token":"ghs_cached123","exp":%s}\n' "$future" > "$CACHE_DIR/token-123.json"
printf '{"token":"ghs_legacy","exp":%s}\n' "$future" > "$CACHE_DIR/token.json"

# 1. The cache is keyed by the selected installation id.
out="$(HOME="$FAKE_HOME" BOT_INSTALL_ID=123 "$SRC/bot-token")" || { echo "FAIL: keyed cache hit did not succeed"; FAIL=1; }
[ "$out" = "ghs_cached123" ] || { echo "FAIL: BOT_INSTALL_ID=123 did not read token-123.json (got: $out)"; FAIL=1; }

# 2. Another installation must never be served that cached token.
out="$(HOME="$FAKE_HOME" BOT_INSTALL_ID=456 "$SRC/bot-token" 2>/dev/null)" && { echo "FAIL: BOT_INSTALL_ID=456 minted with no key present"; FAIL=1; }
[ "$out" = "ghs_cached123" ] && { echo "FAIL: BOT_INSTALL_ID=456 was served installation 123's token"; FAIL=1; }

# 3. The legacy unkeyed token.json is never read.
out="$(HOME="$FAKE_HOME" BOT_INSTALL_ID=789 "$SRC/bot-token" 2>/dev/null)" && { echo "FAIL: mint unexpectedly succeeded with no key present"; FAIL=1; }
case "$out" in *ghs_legacy*) echo "FAIL: the legacy unkeyed token.json was served"; FAIL=1 ;; esac

# 4. A non-numeric selector is refused before any cache read or mint.
err="$(HOME="$FAKE_HOME" BOT_INSTALL_ID='123/../456' "$SRC/bot-token" 2>&1 1>"$DIR/stdout4")" && { echo "FAIL: path-shaped BOT_INSTALL_ID accepted"; FAIL=1; }
case "$err" in
  *"not numeric"*) ;;
  *) echo "FAIL: no numeric refusal for a path-shaped id: $err"; FAIL=1 ;;
esac
[ -s "$DIR/stdout4" ] && { echo "FAIL: a refused selector still printed output"; FAIL=1; }

# 5. The unreplaced INSTALL_ID placeholder is refused, never sent to the API.
err="$(HOME="$FAKE_HOME" "$SRC/bot-token" 2>&1 1>"$DIR/stdout5")" && { echo "FAIL: placeholder INSTALL_ID accepted"; FAIL=1; }
case "$err" in
  *"not numeric"*) ;;
  *) echo "FAIL: no numeric refusal for the placeholder: $err"; FAIL=1 ;;
esac

# --- bot-env: map-driven selection. Two mapped accounts; stub bot-token
# --- echoes the BOT_INSTALL_ID it received.
cp "$SRC/git-credential-bot" "$DIR/"
awk '{ if ($0 == "acme:REPLACE") { print "acme:111"; print "beta:222" } else print }' "$SRC/bot-env" > "$DIR/bot-env"
printf '#!/usr/bin/env bash\necho "ghs_stub-${BOT_INSTALL_ID:-none}"\n' > "$DIR/bot-token"
chmod +x "$DIR"/git-credential-bot "$DIR"/bot-env "$DIR"/bot-token

# 6. Mapped account: the mint sees the account's id and the env exports it,
#    even when a stale selection from another repo is still exported.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:acme/scratch.git
out="$(cd "$REPO" && BOT_INSTALL_ID=999 "$DIR/bot-env")"
echo "$out" | grep -qF "GH_TOKEN='ghs_stub-111'" || { echo "FAIL: mint did not receive the mapped id (or a stale selection leaked in)"; FAIL=1; }
echo "$out" | grep -qF "export BOT_INSTALL_ID='111'" || { echo "FAIL: mapped account's id not exported"; FAIL=1; }
rm -rf "$REPO"

# 7. A second mapped account selects its own installation.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:beta/scratch.git
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -qF "GH_TOKEN='ghs_stub-222'" || { echo "FAIL: second account's mint did not receive its id"; FAIL=1; }
echo "$out" | grep -qF "export BOT_INSTALL_ID='222'" || { echo "FAIL: second account's id not exported"; FAIL=1; }
rm -rf "$REPO"

# 8. Personal verdict clears the selection and exports nothing.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:notacme/scratch.git
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -q '^unset BOT_INSTALL_ID$' || { echo "FAIL: personal verdict did not unset BOT_INSTALL_ID"; FAIL=1; }
echo "$out" | grep -q 'export BOT_INSTALL_ID' && { echo "FAIL: personal verdict exported BOT_INSTALL_ID"; FAIL=1; }
rm -rf "$REPO"

# 9. Ambiguous-toward-bot (no remotes): default installation, stale selection
#    cleared for the mint and in the emitted env, insteadOf pairs for every
#    mapped account.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
out="$(cd "$REPO" && BOT_INSTALL_ID=999 "$DIR/bot-env" 2>/dev/null)"
echo "$out" | grep -qF "GH_TOKEN='ghs_stub-none'" || { echo "FAIL: ambiguous mint did not fall back to the default installation"; FAIL=1; }
echo "$out" | grep -q '^unset BOT_INSTALL_ID$' || { echo "FAIL: ambiguous verdict did not clear a stale selection"; FAIL=1; }
echo "$out" | grep -qF "GIT_CONFIG_VALUE_2='git@github.com:acme/'" || { echo "FAIL: ambiguous verdict missing the first mapped account's rewrite pair"; FAIL=1; }
echo "$out" | grep -qF "GIT_CONFIG_VALUE_4='git@github.com:beta/'" || { echo "FAIL: ambiguous verdict missing the second mapped account's rewrite pair"; FAIL=1; }
echo "$out" | grep -q '^export GIT_CONFIG_COUNT=5$' || { echo "FAIL: ambiguous verdict emitted a wrong GIT_CONFIG_COUNT"; FAIL=1; }
rm -rf "$REPO"

# 10. Remotes spanning two mapped accounts have no single right installation:
#     abort, emitting no env.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:acme/scratch.git
git -C "$REPO" remote add upstream git@github.com:beta/scratch.git
ERR="$(mktemp)"
out="$(cd "$REPO" && BOT_INSTALL_ID=999 "$DIR/bot-env" 2>"$ERR")" && { echo "FAIL: two mapped accounts in one repo did not abort"; FAIL=1; }
[ -z "$out" ] || { echo "FAIL: the two-account abort still emitted env lines"; FAIL=1; }
grep -q 'more than one mapped account' "$ERR" || { echo "FAIL: two-account abort gave no explanation"; FAIL=1; }
rm -rf "$REPO"
rm -f "$ERR"

# 11. git-credential-bot propagates the exported selection to its sibling mint.
out="$(printf 'protocol=https\nhost=github.com\n\n' | BOT_INSTALL_ID=111 "$DIR/git-credential-bot" get)"
echo "$out" | grep -q '^password=ghs_stub-111$' || { echo "FAIL: git-credential-bot did not pass the selection through to bot-token"; FAIL=1; }

# 12. A mapped account plus an unmapped one (the fork/upstream shape) keeps the
#     mapped account's selection — the installation boundary, not the gate, is
#     what fails loudly for the unmapped remote. Pins intended semantics.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:acme/scratch.git
git -C "$REPO" remote add upstream git@github.com:oss-project/scratch.git
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -qF "export BOT_INSTALL_ID='111'" || { echo "FAIL: mapped+unmapped remotes did not keep the mapped selection"; FAIL=1; }
rm -rf "$REPO"

# 13. Duplicate map keys are a config error that must abort, not a silent
#     first-entry win — a typo'd duplicate would mint a valid token for the
#     wrong installation.
awk '{ if ($0 == "acme:REPLACE") { print "acme:111"; print "acme:222" } else print }' "$SRC/bot-env" > "$DIR/bot-env-dup"
chmod +x "$DIR/bot-env-dup"
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:notacme/scratch.git
ERR="$(mktemp)"
out="$(cd "$REPO" && "$DIR/bot-env-dup" 2>"$ERR")" && { echo "FAIL: duplicate map keys did not abort"; FAIL=1; }
[ -z "$out" ] || { echo "FAIL: duplicate-map abort still emitted env lines"; FAIL=1; }
grep -q 'duplicate' "$ERR" || { echo "FAIL: duplicate-map abort gave no explanation"; FAIL=1; }
rm -rf "$REPO"
rm -f "$ERR"

# 14a. An empty map is a config error that must abort — with zero entries the
#      ambiguous-toward-bot paths would otherwise emit a broken rewrite pair
#      (`url.https://github.com//.insteadOf`).
awk '$0 != "acme:REPLACE"' "$SRC/bot-env" > "$DIR/bot-env-empty"
chmod +x "$DIR/bot-env-empty"
ERR="$(mktemp)"
out="$(cd "$DIR" && "$DIR/bot-env-empty" 2>"$ERR")" && { echo "FAIL: empty ORG_INSTALLS did not abort"; FAIL=1; }
[ -z "$out" ] || { echo "FAIL: empty-map abort still emitted env lines"; FAIL=1; }
grep -q 'ORG_INSTALLS' "$ERR" || { echo "FAIL: empty-map abort gave no explanation"; FAIL=1; }
rm -f "$ERR"

# 14. A malformed map entry (no id, or an uppercase/unsafe account name) is a
#     config error that must abort.
for bad in 'acme' 'Acme:111' 'acme:bad id'; do
  awk -v bad="$bad" '{ if ($0 == "acme:REPLACE") print bad; else print }' "$SRC/bot-env" > "$DIR/bot-env-bad"
  chmod +x "$DIR/bot-env-bad"
  ERR="$(mktemp)"
  out="$(cd "$DIR" && "$DIR/bot-env-bad" 2>"$ERR")" && { echo "FAIL: malformed map entry [$bad] did not abort"; FAIL=1; }
  [ -z "$out" ] || { echo "FAIL: malformed-map abort still emitted env lines ([$bad])"; FAIL=1; }
  grep -q 'ORG_INSTALLS' "$ERR" || { echo "FAIL: malformed map entry [$bad] gave no explanation"; FAIL=1; }
  rm -f "$ERR"
done

[ "$FAIL" -eq 0 ] && echo "selector-cache-test: PASS"
exit "$FAIL"
