#!/usr/bin/env bash
# Verifies shared scripts resolve siblings from an arbitrary install dir.
set -euo pipefail
# A Git hook may export repository context that makes scratch `git -C` commands
# target the real repository instead of the fixture.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_PREFIX
FAIL=0
DIR="$(cd -- "$(mktemp -d)" >/dev/null 2>&1 && pwd -P)"
trap 'rm -rf "$DIR"' EXIT
SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)/scripts"

cp "$SRC/git-credential-bot" "$SRC/bot-env" "$DIR/"
# Stub bot-token: prints a fake token so no network or key is needed.
printf '#!/usr/bin/env bash\necho ghs_stubtoken\n' > "$DIR/bot-token"
chmod +x "$DIR"/git-credential-bot "$DIR"/bot-env "$DIR"/bot-token

# 1. git-credential-bot must call the SIBLING bot-token, not ~/.claude/bot-shims.
out="$(printf 'protocol=https\nhost=github.com\n\n' | "$DIR/git-credential-bot" get)"
echo "$out" | grep -q '^password=ghs_stubtoken$' || { echo "FAIL: git-credential-bot did not use sibling bot-token"; FAIL=1; }

# 2. bot-env (bot verdict, org repo) must emit sibling paths and the stub token.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:acme/scratch.git
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -qF "GIT_CONFIG_VALUE_1='!$DIR/git-credential-bot'" || { echo "FAIL: bot-env credential-helper path not self-located"; FAIL=1; }
echo "$out" | grep -qF "GH_TOKEN='ghs_stubtoken'" || { echo "FAIL: bot-env did not mint via sibling bot-token"; FAIL=1; }
rm -rf "$REPO"

# 3. session-env.sh must embed its own dir's bot-token in the env-file line.
cp "$SRC/claude/session-env.sh" "$SRC/claude/bot-env-hook.sh" "$DIR/"
chmod +x "$DIR"/session-env.sh "$DIR"/bot-env-hook.sh
ENVF="$(mktemp)"
CLAUDE_ENV_FILE="$ENVF" "$DIR/session-env.sh"
grep -qF "$DIR/bot-token" "$ENVF" || { echo "FAIL: session-env.sh line not self-located"; FAIL=1; }
# The installed line must actually work: eval it and check the stub token landed.
unset GH_TOKEN || true
eval "$(cat "$ENVF")"
[ "${GH_TOKEN:-}" = "ghs_stubtoken" ] || { echo "FAIL: session-env.sh installed line did not mint"; FAIL=1; }
rm -f "$ENVF"

# 4. bot-env-hook.sh must embed its own dir's bot-env; the guard must fail closed.
ENVF="$(mktemp)"
CLAUDE_ENV_FILE="$ENVF" "$DIR/bot-env-hook.sh"
grep -qF "$DIR/bot-env" "$ENVF" || { echo "FAIL: bot-env-hook.sh guard not self-located"; FAIL=1; }
# Fail-closed: with bot-env made non-executable, running the guard must abort.
chmod -x "$DIR/bot-env"
if bash -c "$(cat "$ENVF"); echo reached"; then
  echo "FAIL: guard did not abort with non-executable bot-env"; FAIL=1
fi
chmod +x "$DIR/bot-env"
rm -f "$ENVF"

# 5. Glue metachar guard: every metacharacter the guard names must be refused,
#    with the refusal on stderr, and nothing written to the env file.
for meta in '"' '$' '`' '\'; do
  EVIL="$DIR/evil${meta}dir"
  mkdir -p "$EVIL"
  cp "$DIR/session-env.sh" "$DIR/bot-env-hook.sh" "$EVIL/"
  chmod +x "$EVIL"/session-env.sh "$EVIL"/bot-env-hook.sh
  ENVF="$(mktemp)"
  for glue in session-env.sh bot-env-hook.sh; do
    err="$(CLAUDE_ENV_FILE="$ENVF" "$EVIL/$glue" 2>&1 1>/dev/null)" && { echo "FAIL: $glue accepted an install path containing [$meta]"; FAIL=1; }
    case "$err" in
      *"shell metacharacter"*) ;;
      *) echo "FAIL: $glue gave no metacharacter refusal for [$meta]: $err"; FAIL=1 ;;
    esac
  done
  [ -s "$ENVF" ] && { echo "FAIL: refused install still wrote to the env file ([$meta])"; FAIL=1; }
  rm -f "$ENVF"
done

# 5b. bot-env's install-path allowlist: the GIT_CONFIG_VALUE_1 it emits is a
#     `!`-prefixed credential.helper that git re-parses through `sh -c`, so a
#     space breaks the command and `$(...)` in the path executes.
for bad in 'space dir' 'inj$(id -un)' "quote'dir"; do
  BAD="$DIR/$bad"
  mkdir -p "$BAD"
  cp "$SRC/bot-env" "$BAD/"
  chmod +x "$BAD/bot-env"
  err="$("$BAD/bot-env" 2>&1 1>/dev/null)" && { echo "FAIL: bot-env accepted install path [$bad]"; FAIL=1; }
  case "$err" in
    *"unsafe character"*) ;;
    *) echo "FAIL: bot-env gave no unsafe-character refusal for [$bad]: $err"; FAIL=1 ;;
  esac
done

# 6. codex gh shim: sibling mint, fail-closed sentinel, exec of the real gh.
mkdir -p "$DIR/real"
printf '#!/usr/bin/env bash\necho "REAL_GH_SAW_TOKEN=$GH_TOKEN args=$*"\n' > "$DIR/real/gh"
chmod +x "$DIR/real/gh"
sed "s|^REAL_GH=.*|REAL_GH=\"$DIR/real/gh\"|" "$SRC/codex/gh" > "$DIR/gh"
chmod +x "$DIR/gh"
out="$("$DIR/gh" pr status)"
echo "$out" | grep -qF 'REAL_GH_SAW_TOKEN=ghs_stubtoken args=pr status' || { echo "FAIL: codex gh shim did not mint via sibling and exec real gh"; FAIL=1; }
# Fail-closed: crash the sibling bot-token; shim must pass the sentinel, not empty.
printf '#!/usr/bin/env bash\nexit 1\n' > "$DIR/bot-token"
out="$("$DIR/gh" api user)"
echo "$out" | grep -qF 'REAL_GH_SAW_TOKEN=BOT-TOKEN-MINT-FAILED' || { echo "FAIL: codex gh shim fell open on mint failure"; FAIL=1; }
printf '#!/usr/bin/env bash\necho ghs_stubtoken\n' > "$DIR/bot-token"

# 7. An empty-but-successful mint is as dangerous as a crash: gh reads an empty
#    GH_TOKEN as unset and falls back to the personal stored credentials.
printf '#!/usr/bin/env bash\nexit 0\n' > "$DIR/bot-token"
out="$("$DIR/gh" api user)"
echo "$out" | grep -qF 'REAL_GH_SAW_TOKEN=BOT-TOKEN-MINT-FAILED' || { echo "FAIL: codex gh shim fell open on an empty successful mint"; FAIL=1; }
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:acme/scratch.git
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -qF "GH_TOKEN='BOT-TOKEN-MINT-FAILED'" || { echo "FAIL: bot-env fell open on an empty successful mint"; FAIL=1; }
echo "$out" | grep -qF "GH_TOKEN=''" && { echo "FAIL: bot-env emitted an empty GH_TOKEN"; FAIL=1; }
rm -rf "$REPO"
printf '#!/usr/bin/env bash\necho ghs_stubtoken\n' > "$DIR/bot-token"

# 8. git-credential-bot: silent on a wrong host, and sentinel on a bad mint.
out="$(printf 'protocol=https\nhost=evil.com\n\n' | "$DIR/git-credential-bot" get)"
[ -z "$out" ] || { echo "FAIL: git-credential-bot answered for a non-github.com host"; FAIL=1; }
printf '#!/usr/bin/env bash\nexit 1\n' > "$DIR/bot-token"
out="$(printf 'protocol=https\nhost=github.com\n\n' | "$DIR/git-credential-bot" get)"
echo "$out" | grep -q '^username=x-access-token$' || { echo "FAIL: git-credential-bot omitted the sentinel username after a crashed mint"; FAIL=1; }
echo "$out" | grep -q '^password=BOT-TOKEN-MINT-FAILED$' || { echo "FAIL: git-credential-bot omitted the sentinel password after a crashed mint"; FAIL=1; }
printf '#!/usr/bin/env bash\nexit 0\n' > "$DIR/bot-token"
out="$(printf 'protocol=https\nhost=github.com\n\n' | "$DIR/git-credential-bot" get)"
echo "$out" | grep -q '^password=BOT-TOKEN-MINT-FAILED$' || { echo "FAIL: git-credential-bot omitted the sentinel after an empty mint"; FAIL=1; }

# A complete sentinel must stop git before an IDE-provided askpass helper can
# supply personal credentials.
ASKPASS_CANARY="$DIR/askpass-canary"
ASKPASS_CALLED="$DIR/askpass-called"
printf '#!/usr/bin/env bash\ntouch "$ASKPASS_CALLED"\necho personal-secret\n' > "$ASKPASS_CANARY"
chmod +x "$ASKPASS_CANARY"
export ASKPASS_CALLED
out="$(printf 'protocol=https\nhost=github.com\n\n' | GIT_ASKPASS="$ASKPASS_CANARY" GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c "credential.helper=!$DIR/git-credential-bot" credential fill)"
echo "$out" | grep -q '^password=BOT-TOKEN-MINT-FAILED$' || { echo "FAIL: git credential fill did not accept the sentinel"; FAIL=1; }
[ ! -e "$ASKPASS_CALLED" ] || { echo "FAIL: git invoked askpass after receiving the sentinel"; FAIL=1; }
unset ASKPASS_CALLED
printf '#!/usr/bin/env bash\necho ghs_stubtoken\n' > "$DIR/bot-token"

# 9. bot-env personal verdict: explicit unsets, and never an exported GH_TOKEN.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:notacme/scratch.git
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -q '^unset GH_TOKEN$' || { echo "FAIL: bot-env personal verdict did not unset GH_TOKEN"; FAIL=1; }
echo "$out" | grep -q '^unset GIT_AUTHOR_NAME ' || { echo "FAIL: bot-env personal verdict did not unset the identity vars"; FAIL=1; }
echo "$out" | grep -q 'export GH_TOKEN' && { echo "FAIL: bot-env personal verdict exported GH_TOKEN"; FAIL=1; }
rm -rf "$REPO"

# 10. bot-env classifies only raw local url/pushurl values, before insteadOf.
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:acme/scratch.git
git -C "$REPO" config url.ssh://git@mirror.invalid/acme/.insteadOf git@github.com:acme/
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -q '^export GH_TOKEN=' || { echo "FAIL: rewritten raw org remote did not get the bot verdict"; FAIL=1; }
rm -rf "$REPO"

REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@mirror.invalid:notacme/scratch.git
git -C "$REPO" config url.git@github.com:acme/.insteadOf git@mirror.invalid:notacme/
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -q '^unset GH_TOKEN$' || { echo "FAIL: rewritten raw non-org remote did not get the personal verdict"; FAIL=1; }
rm -rf "$REPO"

REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:notacme/scratch.git
git -C "$REPO" remote set-url --add --push origin SSH://git@GITHUB.COM/ACME/scratch.git
git -C "$REPO" remote set-url --add --push origin SSH://git@GITHUB.COM/ACME/scratch.git
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -q '^export GH_TOKEN=' || { echo "FAIL: mixed-case raw org pushurl did not get the bot verdict"; FAIL=1; }
echo "$out" | grep -q '^export GIT_CONFIG_COUNT=5$' || { echo "FAIL: duplicate raw pushurls emitted duplicate rewrite pairs"; FAIL=1; }
[ "$(echo "$out" | grep -c "GIT_CONFIG_VALUE_4='SSH://git@GITHUB.COM/ACME/'")" -eq 1 ] || { echo "FAIL: mixed-case raw pushurl rewrite was missing or duplicated"; FAIL=1; }
rm -rf "$REPO"

REPO="$(mktemp -d)"
git -C "$REPO" init -q
out="$(cd "$REPO" && "$DIR/bot-env" 2>/dev/null)"
echo "$out" | grep -q '^export GH_TOKEN=' || { echo "FAIL: missing raw remotes did not fail toward the bot verdict"; FAIL=1; }
rm -rf "$REPO"

REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" config --add remote.origin.url ""
git -C "$REPO" config --add remote.origin.pushurl ""
printf '[remote "valueless"]\n\turl\n' >> "$REPO/.git/config"
ERR="$(mktemp)"
out="$(cd "$REPO" && "$DIR/bot-env" 2>"$ERR")"
echo "$out" | grep -q '^export GH_TOKEN=' || { echo "FAIL: empty or valueless raw remote records did not fail toward the bot verdict"; FAIL=1; }
[ "$(grep -c 'empty raw remote URL' "$ERR")" -eq 1 ] || { echo "FAIL: empty or valueless raw remote records did not emit exactly one warning"; FAIL=1; }
rm -rf "$REPO"
rm -f "$ERR"

REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:acme/scratch.git
git -C "$REPO" config --add remote.empty.url ""
ERR="$(mktemp)"
out="$(cd "$REPO" && "$DIR/bot-env" 2>"$ERR")"
echo "$out" | grep -q '^export GH_TOKEN=' || { echo "FAIL: org remote plus empty value did not get the bot verdict"; FAIL=1; }
grep -q 'empty raw remote URL' "$ERR" && { echo "FAIL: empty raw remote value warned after an org remote established the bot verdict"; FAIL=1; }
rm -rf "$REPO"
rm -f "$ERR"

REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" config extensions.worktreeConfig true
git -C "$REPO" config remote.origin.url git@github.com:notacme/scratch.git
git -C "$REPO" config --worktree remote.origin.url git@github.com:acme/scratch.git
out="$(cd "$REPO" && "$DIR/bot-env")"
echo "$out" | grep -q '^export GH_TOKEN=' || { echo "FAIL: raw org remote in worktree config did not get the bot verdict"; FAIL=1; }
rm -rf "$REPO"

# Force only the raw-config query to fail while leaving the work-tree probe
# intact; an undetermined affiliation must resolve toward the bot.
mkdir -p "$DIR/git-fail-bin"
REAL_GIT="$(command -v git)"
sed "s|REAL_GIT_REPLACE|$REAL_GIT|" > "$DIR/git-fail-bin/git" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == config && "${2:-}" == --local ]]; then
  exit 2
fi
exec REAL_GIT_REPLACE "$@"
EOF
chmod +x "$DIR/git-fail-bin/git"
REPO="$(mktemp -d)"
git -C "$REPO" init -q
git -C "$REPO" remote add origin git@github.com:notacme/scratch.git
out="$(cd "$REPO" && PATH="$DIR/git-fail-bin:$PATH" "$DIR/bot-env" 2>/dev/null)"
echo "$out" | grep -q '^export GH_TOKEN=' || { echo "FAIL: failed raw-remote query did not resolve toward bot"; FAIL=1; }
rm -rf "$REPO"

# 11. codex-bot validates its real CLI and profile before forcing profile bot.
mkdir -p "$DIR/codex-home" "$DIR/other-codex-home"
: > "$DIR/codex-home/bot.config.toml"
: > "$DIR/other-codex-home/bot.config.toml"
printf '#!/usr/bin/env bash\nprintf "CODEX_HOME=%%s args=" "$CODEX_HOME"\nprintf "<%%s>" "$@"\nprintf "\\n"\n' > "$DIR/real/codex"
chmod +x "$DIR/real/codex"
sed "s|^REAL_CODEX=.*|REAL_CODEX=\"$DIR/real/codex\"|" "$SRC/codex/codex-bot" > "$DIR/codex-bot"
chmod +x "$DIR/codex-bot"
out="$(CODEX_HOME="$DIR/codex-home" "$DIR/codex-bot" exec hello)"
echo "$out" | grep -qF 'args=<--profile><bot><exec><hello>' || { echo "FAIL: codex-bot did not prepend the bot profile"; FAIL=1; }
out="$(CODEX_HOME="$DIR/other-codex-home" "$DIR/codex-bot" --version)"
echo "$out" | grep -qF "CODEX_HOME=$DIR/other-codex-home" || { echo "FAIL: codex-bot did not preserve custom CODEX_HOME"; FAIL=1; }
if CODEX_HOME="$DIR/missing-home" "$DIR/codex-bot" exec true >/dev/null 2>&1; then
  echo "FAIL: codex-bot accepted a missing bot profile"; FAIL=1
fi
for args in '-p other' '-pother' '--profile other' '--profile=other'; do
  if CODEX_HOME="$DIR/codex-home" "$DIR/codex-bot" $args >/dev/null 2>&1; then
    echo "FAIL: codex-bot accepted profile override [$args]"; FAIL=1
  fi
done
sed 's|^REAL_CODEX=.*|REAL_CODEX="codex"|' "$SRC/codex/codex-bot" > "$DIR/codex-relative"
chmod +x "$DIR/codex-relative"
if CODEX_HOME="$DIR/codex-home" "$DIR/codex-relative" >/dev/null 2>&1; then
  echo "FAIL: codex-bot accepted a relative REAL_CODEX"; FAIL=1
fi
sed "s|^REAL_CODEX=.*|REAL_CODEX=\"$DIR/real/not-executable\"|" "$SRC/codex/codex-bot" > "$DIR/codex-nonexec"
: > "$DIR/real/not-executable"
chmod +x "$DIR/codex-nonexec"
if CODEX_HOME="$DIR/codex-home" "$DIR/codex-nonexec" >/dev/null 2>&1; then
  echo "FAIL: codex-bot accepted a non-executable REAL_CODEX"; FAIL=1
fi

[ "$FAIL" -eq 0 ] && echo "selflocate-test: PASS"
exit "$FAIL"
