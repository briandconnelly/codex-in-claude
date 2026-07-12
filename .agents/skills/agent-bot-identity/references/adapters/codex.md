# Codex CLI Adapter

Core setup — App registration, token minting, the credential helper, and `as-me` (Phases 1–3) — is harness-neutral and lives in the [agent-bot-identity SKILL](../../SKILL.md).
This reference is the Codex CLI implementation of the SKILL's Phase 4 routing contract.
Most mechanism claims below trace to recorded probes against `codex-cli 0.143.0`, the profile-selection caveats include focused `0.144.1` probes, and the launcher behavior has automated shell coverage; where the evidence says something is impossible or untested, this doc says so plainly.

## Status

Variant A is **partially verified; the GitHub write path was not exercised**, and it has a documented `as-me` limitation; Variant B is pending.

Checks 1–5, 7, and 8 of the verification list below passed for PATH takeover, static identity env, per-invocation `gh` minting, the credential-helper rewrite, sandbox minting, fail-closed token handling, and the installation access boundary.
One known capability limitation is `as-me` — see the `as-me` limitation section: under this Codex version's sandbox the human-authorship override cannot run, so collaborated authorship happens outside Codex.

Scope of the live verification, stated honestly: no branch was pushed and no `gh pr create` was run under Codex.
Check 4 proved the authenticated HTTPS-rewrite path with `git ls-remote` and check 5 made a local commit; the write path to GitHub (push, PR creation) rides the same token and credential helper but was not exercised end-to-end here.

Variant B (a user-level, automatic, per-command re-decision like Claude Code's) is **pending**, not shipped.
It would require a mechanism that re-decides bot-vs-personal before every command with the same fail-closed properties, and no `$CLAUDE_ENV_FILE` equivalent — a file whose contents are evaluated in each command's own shell and working directory — was found in Codex 0.143.0.
The profile mechanism Variant A uses is decided once per invocation, not per command, so it cannot back Variant B.

## Activation surface — guarded launcher and named `bot` profile

The supported activation surface is `scripts/codex/codex-bot`, installed beside the other bot scripts and invoked by its absolute path on every run.
The launcher requires an absolute executable `REAL_CODEX`, verifies that `$CODEX_HOME/bot.config.toml` exists and is readable, rejects caller-supplied `-p` and `--profile` overrides, and prepends `--profile bot` before invoking the real CLI.
Customize and install it without putting its directory on the personal `PATH`:

```bash
cp agent-bot-identity/scripts/codex/codex-bot ~/.config/acme-agent/bin/codex-bot
chmod +x ~/.config/acme-agent/bin/codex-bot
# edit REAL_CODEX="REPLACE" → the absolute path to the real Codex CLI
/Users/<you>/.config/acme-agent/bin/codex-bot --version
```

Start work with `/Users/<you>/.config/acme-agent/bin/codex-bot sandbox ...` or `/Users/<you>/.config/acme-agent/bin/codex-bot exec ...`.
Do not invoke bare `codex` for bot work, and do not add the install directory to personal `PATH`.

**Read this first — the fail direction is weaker than Claude Variant A.**
Because activation is a per-invocation launcher, **invoking bare `codex` silently runs the personal identity** — commits attribute to the human, `gh` uses the personal login.
That is a weaker opt-in than Claude Code Variant A's per-repo `.claude/settings.local.json` file, which is present-or-absent per repo rather than remembered per command.
Treat the launcher's absolute path as mandatory on every Codex invocation in an enrolled repo; there is no file that makes it automatic.
Codex 0.144.1 silently accepted an unknown profile name and continued without the intended profile values, so a direct `codex --profile <typo>` invocation can produce personal attribution without warning.
The launcher closes that typo path for the supported entry point by checking the exact bot profile file before passing the fixed name.
It remains routing rather than containment because a caller can bypass it and invoke the real CLI directly.

Automatic, project-scoped activation remains **pending**.
In version-specific probes, a project-scoped `.codex/config.toml` at the repo root did not load under `codex sandbox` or `codex exec` on 0.143.0, and it still did not load under `codex sandbox` on 0.144.1 even with a trust-level override.
That observed `codex sandbox` behavior conflicts with the broader current [configuration precedence documentation](https://learn.chatgpt.com/docs/config-file/config-basic#configuration-precedence), which says trusted project `.codex/config.toml` files load above selected profiles.
Treat the probe as version- and surface-specific rather than a general statement that project config never loads, and retain the activation canaries because a higher-precedence project layer could override identity values where current documented behavior applies.

The profile lives at `$CODEX_HOME/bot.config.toml` (default `~/.codex/bot.config.toml`), a **separate file** layered on top of the base `~/.codex/config.toml` by `--profile bot` — the base config is not edited.
`codex exec` in a non-interactive path defaults to `approval: never` and `sandbox: read-only` with no trust prompt, so automation is not blocked by a prompt; the gap is purely the missing per-repo file, not an approval wall.

## Static identity env

Codex applies static identity through the profile's `shell_environment_policy.set` block, which `--profile bot` reads directly (verified: PATH and every `GIT_*` var below took effect under `--profile bot`).
The block mirrors the Claude Variant A env — four identity vars, the `GIT_CONFIG_*` credential-helper reset plus bot helper, org-scoped `insteadOf`, and `commit.gpgsign false`.

Replace `<BOT_UID>` with the bot user ID from Phase 2, `acme` with your org, and `<you>` with your username (keep every path absolute).
These examples use the recommended flat `~/.config/acme-agent/bin/` install location; existing `~/.claude/bot-shims/` installs keep working because the scripts self-locate — just keep every path consistent.

The verified `bot` profile used the inline `set = { … }` table form (a TOML inline table is a single logical line):

```toml
[shell_environment_policy]
set = { PATH = "/Users/<you>/.config/acme-agent/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", GIT_AUTHOR_NAME = "acme-agent[bot]", GIT_AUTHOR_EMAIL = "<BOT_UID>+acme-agent[bot]@users.noreply.github.com", GIT_COMMITTER_NAME = "acme-agent[bot]", GIT_COMMITTER_EMAIL = "<BOT_UID>+acme-agent[bot]@users.noreply.github.com", GIT_CONFIG_COUNT = "4", GIT_CONFIG_KEY_0 = "credential.helper", GIT_CONFIG_VALUE_0 = "", GIT_CONFIG_KEY_1 = "credential.helper", GIT_CONFIG_VALUE_1 = "!$HOME/.config/acme-agent/bin/git-credential-bot", GIT_CONFIG_KEY_2 = "url.https://github.com/acme/.insteadOf", GIT_CONFIG_VALUE_2 = "git@github.com:acme/", GIT_CONFIG_KEY_3 = "commit.gpgsign", GIT_CONFIG_VALUE_3 = "false" }
```

What each part does (identical in intent to the Claude Variant A env):

- `GIT_AUTHOR_*` / `GIT_COMMITTER_*` override global `user.*` so commits attribute to the bot.
- The empty `credential.helper` (`GIT_CONFIG_VALUE_0`) resets the inherited helper list; the next entry installs the bot helper as the only one.
- `insteadOf` rewrites org SSH remotes to HTTPS inside the profile only, so pushes use the bot token instead of the personal SSH key.
  The match is literal and case-sensitive — normalize each enrolled repo's remote to canonical lowercase, and add a second `insteadOf` pair (bumping `GIT_CONFIG_COUNT`) for any `ssh://git@github.com/acme/` form.
- `commit.gpgsign false` keeps bot commits unsigned so the personal GPG key never signs bot-authored work.

The `GIT_CONFIG_VALUE_1` helper value is `!$HOME/.config/acme-agent/bin/git-credential-bot`; `$HOME` there is expanded by git's shell when it runs the helper, not by Codex.
The PATH value, by contrast, must be a literal absolute path — see the next section.

## PATH

Codex's `shell_environment_policy.set.PATH` **REPLACES** PATH; it does not merge, and it performs no `$PATH` expansion (probe-verified — you cannot write `"$SHIMS:$PATH"` and expect the old PATH appended).
So the value must be a complete, literal, absolute PATH with the shim directory first.
The known-good composed value (shim dir first, then the core Homebrew/system dirs) is:

```
/Users/<you>/.config/acme-agent/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
```

A truncated or invalid PATH does not degrade gracefully — it breaks command execution outright.
Probed with a garbage value, the sandbox could not resolve the command interpreter at all:

```
$ codex sandbox -c 'shell_environment_policy.set.PATH="nonexistent-garbage-path"' sh -c 'echo hi'
sandbox-exec: execvp() of 'sh' failed: No such file or directory
(exit 71)
```

So keep every core system directory in the value; the shim dir goes first only so its `gh` shadows the real `gh`.
Do not use `~` or `$HOME` in the PATH value — the OS uses PATH directly for `execvp` with no shell expansion; write the fully-resolved absolute path.

`uv` **must** be on the replacement PATH.
`bot-token` is a `uv run --script` shebang, invoked by the `gh` shim and — through git's `sh -c` — by `git-credential-bot`, so every mint dies without it.
A Homebrew `uv` is already covered by `/opt/homebrew/bin`; the standalone installer puts `uv` in `~/.local/bin`, which the minimal PATH above omits, and a missing `uv` breaks `gh` and every push.
Run `command -v uv` in a personal shell and make sure that directory is in the value.

Per-repo dev tooling may need the PATH extended.
A repo's own pre-commit hook runner (e.g. `prek`, installed via `uv tool install` under `~/.local/bin`) is not on the minimal PATH above; during verification `~/.local/bin` had to be appended for a repo whose `commit-msg`/pre-commit hooks required it (Check 5).
Extend the PATH value with such directories as needed — appended after the core dirs — but keep the shim directory first.

Verify the shim wins before doing any `gh` work: `command -v gh` inside the profile must resolve to the shim path, not `/opt/homebrew/bin/gh`.

## `gh` auth

`gh` auth routes through `scripts/codex/gh`, installed as `gh` first on the Codex-controlled PATH.
The shim mints a fresh installation token on **every invocation** (it calls the shared `bot-token`), exports it as `GH_TOKEN`, then `exec`s the real `gh`.
On mint failure it substitutes the non-empty sentinel `BOT-TOKEN-MINT-FAILED` so `gh` errors loudly with a 401 instead of treating an empty `GH_TOKEN` as unset and silently falling back to the personal stored credentials (verified in Check 8: a non-executable `bot-token` produced a clean `Bad credentials (HTTP 401)`, never the personal account).

Install it and set its one placeholder:

```bash
cp agent-bot-identity/scripts/codex/gh ~/.config/acme-agent/bin/gh
chmod +x ~/.config/acme-agent/bin/gh
# edit REAL_GH="REPLACE" → the absolute path to the real gh (e.g. /opt/homebrew/bin/gh)
```

Scope of the claim: this covers **well-behaved `gh` resolution through the Codex-controlled PATH** — a bare `gh ...` command finds the shim first and acts as the bot.
It does not cover anything that goes around the shim: an absolute path to the real `gh` (`/opt/homebrew/bin/gh`), a shell alias, or calling `bot-token` / the real `gh` directly all bypass it and reach the personal credentials (Check 10 confirmed the absolute-path bypass returns the personal account).

**This buys attribution, not containment.**
The adapter is a PATH-routing convention, not an OS- or sandbox-enforced restriction — the hard boundaries are the App's installation list (Check 7) and the server-side rulesets of the repos it touches (SKILL Phase 6).
Never present it as a sandbox.

## `as-me` limitation

Under `codex-cli 0.143.0`'s sandbox, `.git`-mutating commands succeed **only as a bare, literal `git ...`** invocation.
Any other shape — an env-prefix, a wrapper script (this skill's `as-me`), an inline `git -c alias.…=!… as-me …` form, or command substitution inside the arguments — is **denied** with `fatal: Unable to create '.../.git/index.lock': Operation not permitted`.
This was root-caused by bisection (Check 6 and the follow-up workaround probes): the denial is **not** identity-specific — even a semantically empty `env git commit ...` or an unrelated `FOO=bar git commit ...` fails, while ordinary non-`.git` file writes under the same `workspace-write` session succeed.
Codex grants `.git` internal writes to a command it recognizes as a bare `git` invocation and does not extend that grant through any wrapper.
`.git` writes only ever succeeded under `codex exec` (where the recognition layer runs); under `codex sandbox` even a bare `git commit` is denied, so `codex exec` is the surface for git-mutating work.

Consequence: **`as-me` cannot run through Codex here**, so the human-authorship escape does not work.

The only partial measure that survives the sandbox is a **literal** `--author` flag:

```
git commit --allow-empty --author="Firstname Lastname <you@example.com>" -m "..."
```

This succeeds (verified), and GitHub attributes **authorship** to the human — but the **committer remains the bot**, because `--author` overrides only the author field while the committer still comes from the `GIT_COMMITTER_*` env vars, which cannot be cleared without a wrapper (every wrapper form is denied).
A command-substituted author value (`--author="$(git config user.name) …"`) is also denied — the value must be a literal string.
So `--author` is not equivalent to `as-me`: the commit reads author=human, committer=bot, unsigned.

Therefore, collaborated authorship happens **outside Codex** — in a personal terminal, or through the Claude Code adapter where `as-me` works.
The recovery mirrors the skill's existing guidance: if the agent committed as the bot and the work was collaborated, amend from a personal terminal (`as-me git commit --amend --reset-author`).

## Sandbox profile

The profile ships the minimal sandbox settings needed for the `gh` mint to succeed both cold (no cached token) and warm (cache hit) in one profile:

```toml
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
writable_roots = ["/Users/<you>/.cache/uv", "/Users/<you>/.cache/acme-agent"]
network_access = true
```

Write the roots as fully-resolved **absolute** paths, as shown — Codex may not expand `~` in `writable_roots`, and a `~`-prefixed root silently grants nothing.

What each is for, from the minting probes:

- `network_access = true` is required for the **cold mint** only — the GitHub API call to exchange the App JWT for an installation token.
  A negative control without it failed at DNS resolution (`httpx.ConnectError: nodename nor servname provided`), confirming the sandbox blocks network at the DNS layer.
  A cache hit needs no network, but the profile keeps it enabled because the invocation cannot know cold-vs-warm in advance.
- The `~/.cache/uv` writable root is required on **every** run, cold or warm — the `bot-token` script is a `uv run --script` shebang, and `uv` must write to its own cache (`sdists-v9/.git`) just to resolve the script's environment before the token-cache logic runs.
  Omitting it fails immediately: `failed to open file .../uv/sdists-v9/.git: Operation not permitted`.
- The `~/.cache/acme-agent` writable root is the single leaf directory `bot-token` writes its `token.json` cache into.
  It need not exist beforehand: a cold mint with the directory absent creates it and succeeds (verified).

**Grant these two roots and nothing else.**
Do **not** make the bot *config* directory (`~/.config/acme-agent`) writable: under the SKILL's flat-install recommendation it holds `key.pem` **and** every fail-closed script — `bot-token`, `git-credential-bot`, and this `gh` shim.
A `workspace-write` Codex session with that root could rewrite the App private key and the routing that is supposed to bind it.
Codex's sandbox is the one real boundary in this design; do not punch through it for a cache path.
Verified under the narrowed roots above: the cold and warm mints both succeed, `key.pem` stays readable so minting works, and writes to `~/.config/acme-agent` — including to `key.pem` itself — are denied (`Operation not permitted`).
`writable_roots` is not the whole write surface, though: `workspace-write` also grants writes to the workspace itself and to `/tmp`, verified on 0.143.0.
Narrowing the roots keeps the key and the shims read-only; it does not make the session a sandbox in the containment sense the core skill disclaims.

**Recorded quirk — `--profile bot` does not apply the profile's top-level `sandbox_mode` by itself.**
`--profile bot` reliably applies `shell_environment_policy.set` (PATH and all identity vars), but under `codex sandbox` the profile's top-level `sandbox_mode = "workspace-write"` key is **not** honored on its own — the run stays effectively read-only and the `uv` cache write is denied.
The `[sandbox_workspace_write]` table (`writable_roots`, `network_access`) *does* apply from the profile; only the mode key needs forcing.
Ship the config that handles both surfaces:

- Under `codex sandbox`, force the mode explicitly: `/Users/<you>/.config/acme-agent/bin/codex-bot sandbox -c 'sandbox_mode="workspace-write"' <cmd>`.
- Under `codex exec`, the `-s` flag works directly: `/Users/<you>/.config/acme-agent/bin/codex-bot exec -s workspace-write --skip-git-repo-check <prompt>`.

(There is no `--full-auto` flag for `codex sandbox` — probed, it errors as an unexpected argument.)

## git auth

git auth is unchanged from the harness-neutral core: the org-scoped `insteadOf` SSH→HTTPS rewrite plus the host-gated `git-credential-bot` credential helper, both installed through the `GIT_CONFIG_*` env in the identity block above.
See [SKILL.md Phase 3](../../SKILL.md) for the credential helper.
Verified in Check 4: `GIT_SSH_COMMAND=/usr/bin/false git ls-remote origin` succeeds via HTTPS even with SSH disabled, proving the rewrite-plus-token path is in use and no SSH path is touched.

## Verification

Run these together with the SKILL's Phase 5 checks, from a fresh invocation through the launcher's absolute path.
Skip the SKILL's `GH_TOKEN`-prefix and `as-me` bullets; checks 2 and 6 below replace them.
(A session-level `GH_TOKEN` is an audit smell here, not a pass: the shim exports it per invocation.)
Sandboxed checks use `/Users/<you>/.config/acme-agent/bin/codex-bot sandbox -c 'sandbox_mode="workspace-write"' sh -c '<cmd>'`; the commit checks use `/Users/<you>/.config/acme-agent/bin/codex-bot exec -s workspace-write --skip-git-repo-check '<prompt>'`.
Ten checks were run live; each is one line with its expected result.

1. Launch by the absolute `codex-bot` path, then run `command -v gh` → the shim path (`~/.config/acme-agent/bin/gh`), proving the launcher selected the profile and the shim wins PATH. (PASS)
2. `gh api installation/repositories --jq .total_count` → the count of enrolled repos, proving `gh` acts as the bot. (PASS)
3. `git config --show-scope credential.helper` → bot helper at `command` scope, proving env-scoped with no file changed. (PASS)
4. `GIT_SSH_COMMAND=/usr/bin/false git ls-remote origin` → succeeds via the HTTPS rewrite + bot token, with SSH disabled. (PASS)
5. Real `codex exec` empty commit on a scratch branch → author and committer are the bot, `%G?` is `N` (unsigned), matching `commit.gpgsign=false`. (PASS)
6. `as-me git commit` → **expected FAIL** — this is the documented `as-me` limitation, not a setup error; Codex's sandbox denies `.git` writes for any wrapped/non-bare `git` invocation.
7. Negative boundary: `git ls-remote https://github.com/acme/<private-non-enrolled-repo>.git` → fails (`Repository not found`), proving the installation boundary. (PASS) — the probe repo must be **private and non-enrolled**; a public repo is readable unauthenticated and proves nothing.
8. Fail-closed: make `bot-token` non-executable → `gh` returns a loud `Bad credentials (HTTP 401)`, never a silent fall-through to the personal account; restore the exec bit afterward. (PASS)
9. Untrusted fresh clone (a directory Codex has never trusted) → behaves identically to the enrolled clone; `codex sandbox` never gates on directory trust. **Recorded behavior, not a pass/fail gate.**
10. Bypass smell: `/opt/homebrew/bin/gh api user --jq .login` (real `gh` by absolute path) → returns the **personal** account, confirming routing-not-enforcement. **Recorded behavior, not a pass/fail gate.**

Checks 1–5, 7, and 8 are the pass/fail gate and all pass; Check 6's failure is the documented limitation; Checks 9 and 10 are recorded behaviors, not gates.
Branch push, `gh pr create`, `gh pr checks`, and GitHub-side commit and PR actor verification remain explicit completion gates before this adapter can be labeled fully verified.

Audit smells — any of these means the adapter is mis-wired:

- A stale static `GH_TOKEN` anywhere in Codex config (base `config.toml`, the `bot` profile, or a `-c` override): the token expires hourly and a static one silently rots into `gh` failures or, worse, masks the per-invocation mint.
  `GH_TOKEN` must come only from the `gh` shim.
- A `gh` alias or an absolute-path `gh` invocation (`alias gh=/opt/homebrew/bin/gh`, `/opt/homebrew/bin/gh …`) anywhere — shell config, dotfile, runbook, or agent workflow: each is a shim bypass that reaches the personal credentials (Check 10).
  Flag the alias even in an interactive-only file: it defeats the shim wherever it fires, and its presence signals `gh` routing outside the Codex-controlled PATH.
- Silent mint failure: the shim must fail closed with the non-empty `BOT-TOKEN-MINT-FAILED` sentinel so `gh` errors loudly (`Bad credentials (HTTP 401)`, Check 8) — an empty `GH_TOKEN` is treated as unset and falls back to the personal login.
  A setup with no visible mint-failure mode — or a runbook claiming minting "just works" without the sandbox prerequisites (`network_access = true` for the cold mint; `writable_roots` covering the `uv` and token caches) — is a smell.
- PATH shims installed via shell dotfiles (`.zshrc`, `.zprofile`) instead of the profile's `shell_environment_policy.set.PATH`: dotfile PATH order is not the Codex-controlled routing surface and leaks into personal shells.
- A bare `codex` command, a direct `--profile` invocation, or an alias that bypasses the absolute `codex-bot` path: each avoids the launcher's profile-file and override checks and can run with personal attribution.
- A caller-supplied `-c` or `--config` override that changes `profile` or identity-bearing `shell_environment_policy` values: CLI config overrides outrank the bot profile and can defeat routing canaries.
  The launcher intentionally permits config overrides because the supported `codex sandbox` invocation needs `-c sandbox_mode=...`, so audit their keys rather than treating the launcher as containment.
