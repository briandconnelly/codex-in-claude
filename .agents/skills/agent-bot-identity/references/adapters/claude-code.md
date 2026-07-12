# Claude Code Adapter

Core setup — App registration, token minting, the credential helper, and `as-me` (Phases 1–3) — is harness-neutral and lives in the [agent-bot-identity SKILL](../../SKILL.md).
This reference is the Claude Code implementation of the SKILL's Phase 4 routing contract.

## Glue scripts

Two Claude Code glue scripts sit on top of the shared scripts:

- `scripts/claude/session-env.sh` is Claude Code glue for Variant A.
- `scripts/claude/bot-env-hook.sh` and `scripts/bot-env` are Claude Code glue for Variant B.

Install them flat in the same directory as the shared scripts (the SKILL's Phase 3 install note recommends `~/.config/acme-agent/bin/`).
The scripts self-locate, so existing `~/.claude/bot-shims/` installs keep working unchanged; the examples below use the recommended neutral location, but any single flat directory works as long as every path stays consistent.

## Local routing: choose a variant

Two Claude Code adapters satisfy the routing contract; pick one, never both (two sources of truth drift).

- **Variant A — per-project opt-in**: a hand-created `.claude/settings.local.json` in each enrolled repo.
  Choose it when enrollment should be an explicit, visible, per-repo act — e.g. the agent also works in org repos that are deliberately not enrolled.
- **Variant B — user-level, automatic in org repos**: one user-level hook installs a per-command guard that activates the bot in any repo whose remotes match the org.
  Choose it to eliminate the per-repo step entirely — Variant A's failure mode is forgetting the file in an enrolled repo, which silently attributes agent work to the human (the headline failure); Variant B inverts that to a loud push failure in repos not yet on the installation list.
  The cost of that loudness is a wider blast radius: a broken guard aborts every command in every session machine-wide (detailed under Variant B below), where a broken Variant A hook degrades only the one enrolled repo.

### Variant A — per-project opt-in

Create `.claude/settings.local.json` in each target repo; it affects only the agent's sessions in that project, and no shell dotfile is touched.
Claude Code auto-gitignores this file only when it creates it itself — if you create it by hand, gitignore it yourself so the bot config never lands in the repo.
Replace `<BOT_UID>` with the user ID from Phase 2, `acme` with your org, and `<you>` in the hook command with your username — keep it an absolute path.
(These examples use the recommended flat `~/.config/acme-agent/bin/` install location; existing `~/.claude/bot-shims/` installs keep working because the scripts self-locate — just keep every path consistent.)

```json
{
  "env": {
    "GIT_AUTHOR_NAME": "acme-agent[bot]",
    "GIT_AUTHOR_EMAIL": "<BOT_UID>+acme-agent[bot]@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "acme-agent[bot]",
    "GIT_COMMITTER_EMAIL": "<BOT_UID>+acme-agent[bot]@users.noreply.github.com",
    "GIT_CONFIG_COUNT": "4",
    "GIT_CONFIG_KEY_0": "credential.helper",
    "GIT_CONFIG_VALUE_0": "",
    "GIT_CONFIG_KEY_1": "credential.helper",
    "GIT_CONFIG_VALUE_1": "!$HOME/.config/acme-agent/bin/git-credential-bot",
    "GIT_CONFIG_KEY_2": "url.https://github.com/acme/.insteadOf",
    "GIT_CONFIG_VALUE_2": "git@github.com:acme/",
    "GIT_CONFIG_KEY_3": "commit.gpgsign",
    "GIT_CONFIG_VALUE_3": "false"
  },
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "/Users/<you>/.config/acme-agent/bin/session-env.sh", "args": [] } ] }
    ]
  }
}
```

What each part does:

- `GIT_AUTHOR_*` / `GIT_COMMITTER_*` override global `user.*` so commits attribute to the bot.
- The empty `credential.helper` resets the inherited helper list (osxkeychain, gh helpers); the next entry installs the bot helper as the only one.
- `insteadOf` rewrites SSH remotes to HTTPS inside agent sessions only, so pushes use the bot token instead of the personal SSH key; scoping it to the org prefix leaves other remotes alone.
  The colon form covers `git@github.com:acme/` remotes; if any remote uses the `ssh://git@github.com/acme/` form, add a second `insteadOf` pair and bump the count.
  Normalize each enrolled repo's remote to canonical lowercase (`git remote set-url origin git@github.com:acme/<repo>.git`) before relying on the rewrite: `insteadOf` matching is literal and case-sensitive while GitHub accepts any case, so `git@github.com:Acme/` silently misses the rewrite and pushes over the personal SSH key with the bot as author.
  The Phase 5 `GIT_SSH_COMMAND=/usr/bin/false` check catches a miss.
- `commit.gpgsign false` prevents bot-authored commits being signed with the personal GPG key — a signature from the human on a bot-authored commit is an attribution mismatch.
- The `SessionStart` hook injects `GH_TOKEN` for `gh` (the adapter's `session-env.sh`).
  The first time it runs, Claude Code prompts to approve the hook; approve it.
  The hook entry uses exec form (`args: []`) so the absolute script path is passed directly instead of shell-tokenized.

These env keys are static, so they live in `settings.local.json`.
`GH_TOKEN` is dynamic (1h expiry), so it cannot be a static value here — that is why it goes through the hook + `$CLAUDE_ENV_FILE` instead.

Static also means the env follows the session, not the directory: commands that leave the project mid-session (a scratch clone, an unrelated repo) still carry the bot author env, so commits there are bot-attributed until the work moves to its own session.
That is the recoverable direction (amendable, and the org-scoped rewrite and host-gated helper do not activate elsewhere), but know it is Variant A behavior; Variant B re-decides per command instead.

### Variant B — user-level guard, automatic in org repos

One mechanism fact makes this variant work, now documented in the Claude Code hooks reference ("Persist environment variables") and originally verified empirically on 2.1.172: **the contents of `$CLAUDE_ENV_FILE` are evaluated before every Bash command, in that command's shell and working directory** — not once at session start.
So instead of static per-repo env, a user-level SessionStart hook installs a single *unevaluated* guard line, and the guard re-decides bot-vs-personal per command from the directory the command actually runs in.
Mid-session directory changes flip identity on the next command; there is no session-level verdict to go stale, and no `CwdChanged` plumbing is needed.

Do **not** centralize by moving the Variant A `env` block into user-level `~/.claude/settings.json`: settings `env` values are static strings and cannot be conditional, so the bot identity would activate in every project — other orgs, OSS, personal repos — which is exactly the ungated-global antipattern.

Register the hook in `~/.claude/settings.json` (applies to all projects; replace `<you>`, keep the path absolute):

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "/Users/<you>/.config/acme-agent/bin/bot-env-hook.sh", "args": [] } ] }
    ]
  }
}
```

The hook entry uses exec form (`args: []`) so the absolute script path is passed directly instead of shell-tokenized.
Install `scripts/claude/bot-env-hook.sh` and `scripts/bot-env` from this skill's bundled resources.
Customize `bot-env` with the org, bot name, and bot noreply email.

`bot-env-hook.sh` installs one unevaluated guard line into `$CLAUDE_ENV_FILE`.
The guard line itself checks that `bot-env` exists and is executable before every Bash command, then captures `bot-env` output, aborts on script failure, and only then evaluates the emitted shell.
This placement is load-bearing because Claude Code treats `SessionStart` hook failures as non-blocking; a hook-time preflight that exits before installing the guard can still leave later Bash commands running with personal credentials.
A bare `eval "$(bot-env)"` also fails open because a crashed or missing script substitutes an empty string, the eval succeeds, and the command silently runs personal in an enrolled repo.
The bundled guard uses capture-then-eval plus explicit pre-command checks so missing, non-executable, crashed, or malformed `bot-env` stops the Bash command instead.
The `grep -qxF` guard keeps re-fires idempotent, and `>>` preserves other hooks' lines.
Do not also run the per-repo `session-env.sh` hook.

That fail-closed loudness has a blast radius worth stating plainly: the guard runs before *every* Bash command in *every* project, so a broken `bot-env` aborts every command in every Claude Code session on the machine until it is fixed.
That is the deliberate cost of never failing open.
`bot-env` makes no network calls of its own — the only network step is the token mint delegated to `bot-token`; keep it that way.
Re-run the Phase 5 Variant B checks after any change to `bot-env` before further agent work.
The guard also owns the identity env wholesale inside agent Bash commands: a personal verdict unsets `GH_TOKEN`, the `GIT_AUTHOR_*`/`GIT_COMMITTER_*` vars, and command-scope `GIT_CONFIG_*` even where you exported them yourself for unrelated purposes — user-set values of those variables do not survive into agent commands.
Variant A's blast radius is narrower, which is one more reason to prefer A when automatic enrollment is not worth this machine-wide coupling.

`bot-env` emits the complete Variant A-style bot env on a bot verdict.
It emits explicit `unset`s on a personal verdict so no bot env leaks if a harness ever reuses a shell across commands.
It reads null-delimited raw repository-local and enabled worktree-local `remote.*.url` and `remote.*.pushurl` values rather than `git remote -v`, whose output has already passed through `insteadOf` rewriting.
The raw host and organization path segment determine the verdict and any extra `insteadOf` pairs, so an existing rewrite cannot hide an enrolled affiliation or manufacture one.
Raw repository affiliation intentionally controls identity even if another `insteadOf` rule changes the effective transport target, because ambiguity must resolve toward bot attribution rather than silent human attribution.
The emitted block mirrors the Variant A env, with one improvement over the static block: `bot-env` derives extra `insteadOf` pairs from the matched raw remote values themselves, so `ssh://` forms and case variants are rewritten without manual pairs.
That derivation is load-bearing, not cosmetic — git's `insteadOf` match is literal and case-sensitive while GitHub accepts `git@github.com:Acme/`, so the canonical pair alone would take the bot verdict (the org match is deliberately case-insensitive) yet let the push silently ride the personal SSH key.
`GH_TOKEN` carries the freshly minted value because `bot-env` itself runs per command, with the same `BOT-TOKEN-MINT-FAILED` fail-closed sentinel.
Personal-repo commands pay only local git queries.
The credential helper also returns a complete invalid sentinel credential for an eligible GitHub request after a crashed or empty mint, preventing Git from consulting IDE askpass or terminal credentials.
Wrong-host requests remain silent.
Do not clear askpass or terminal-prompt variables globally because a personal verdict cannot safely restore caller- or IDE-provided values.

The decision rules and their fail direction:

| Situation | Verdict | Why |
| --- | --- | --- |
| Not a git repo (probe exits 128, git's definitive answer) | Personal | Unambiguous — nothing to attribute |
| Probe fails any other way (git missing, broken PATH) | Bot, stderr warning | Ambiguous — cannot rule out org work; only a definitive "not a repository" may resolve personal |
| Raw local remote URLs exist, none in the org | Personal | Unambiguous, even if `insteadOf` makes an effective URL appear enrolled |
| Any raw local remote URL or push URL is in the org | Bot | The raw remote is the repo-intrinsic signal and cannot be hidden by `insteadOf` output rewriting |
| Git repo with zero remotes | Bot, stderr warning | Ambiguous — could be org work just initialized |
| Any raw local remote URL or push URL is empty | Bot if otherwise undetermined, one stderr warning | Ambiguous — an empty configured value cannot establish non-org affiliation |
| A raw config record is valueless or malformed | Bot if otherwise undetermined, one stderr warning | Ambiguous — a record without the key/value separator cannot establish non-org affiliation |
| Raw local remote query fails | Bot, stderr warning | Ambiguous — cannot rule out org work |
| `bot-env` is missing, non-executable, crashes, or emits invalid shell after the guard is installed | Command aborts | Undetermined identity must stop the Bash command, not fall through to personal credentials |
| Token mint fails | Bot env with invalid sentinel | `gh` and pushes fail loudly; never fall through to personal credentials |

Every ambiguous case resolves toward the bot because the two wrong outcomes are not symmetric: wrong-way-bot fails loudly (bot-authored commits are amendable, pushes 403 against the installation boundary) while wrong-way-personal is silent misattribution in the human's name.
Two expected, harmless quirks of running per command: the ambiguity warnings (`no remotes`, `git probe failed`) print on *every* command in such a directory, not once — that repetition is the signal, kept stateless deliberately; and inside a bare repo or a `.git` directory `--is-inside-work-tree` exits 0 rather than 128, so the decision falls through to the remote check (an org-remoted bare repo still resolves to bot), which is why the table's "exits 128" row is the *definitive* not-a-repo answer rather than the only non-repo state.
For the same reason, do not gate on a local repo allowlist: an enrolled repo missing from the list silently works as the human.
Do not check the installation list per command over the network: slow, flaky, and redundant — the token already enforces it server-side, and a not-yet-installed org repo simply fails at first push, which is the "enroll me" signal.

One gap the guard cannot see: the verdict binds to the directory a command *starts* in, so a compound command that crosses repos — `cd <org-repo> && git commit` from a personal directory, or `git -C <org-repo> ...` — carries the starting directory's identity into the target repo, and the personal→org direction of that is silent human attribution.
Keep cross-repo git commands out of agent sessions: change directory in one command, commit in the next, and the per-command re-decision covers it.

Migrating from Variant A: delete the bot stanza from every per-repo `.claude/settings.local.json` (the whole file if that is all it held) and retire the per-repo `session-env.sh` hook registration — leftovers would pin a stale static identity regardless of what the guard decides.
The idempotence check matches the exact guard-line text, so after changing `bot-env-hook.sh` start fresh sessions: a resumed session appends the new line while the old one still runs.

### Why a hook and not a PATH-shimmed `gh`

The obvious approach — drop a `gh` wrapper on `PATH` via a shell rc file — fails under Claude Code and wastes hours.
Claude Code builds a *shell snapshot* at session start by sourcing `$HOME/.zshrc` from a **non-login** shell, then **freezes `PATH`** into that snapshot and replays it for every Bash command.

Consequences that defeat the rc-file approach:

- `.zprofile` is never sourced (non-login shell).
- `ZDOTDIR` is ignored — the snapshot hardcodes `$HOME/.zshrc`, not `$ZDOTDIR/.zshrc`.
- Even a correctly-placed `PATH` prepend is frozen at snapshot time, before the per-project `env` (and any marker it sets) is applied.

`CLAUDE_ENV_FILE` is the supported escape hatch: Claude Code provides it to `SessionStart`/`CwdChanged`/`Setup`/`FileChanged` hooks and sources the file's contents before every Bash command, *after* the snapshot — so an `export GH_TOKEN=...` there reliably reaches `gh`.
This per-command evaluation is now documented in the Claude Code hooks reference ("Persist environment variables"); it was originally verified empirically on Claude Code 2.1.172 with an env file exporting `"$PWD"`, which matched each command's own `pwd`.

Only fall back to a PATH shim if the harness sources a predictable rc file without freezing `PATH`.

## Verification — Claude Code specifics

Run these together with the SKILL's Phase 5 checks; the Phase 5 activation check ("run your adapter's activation checks first") resolves here.

In a fresh agent session in an opted-in repo, the hook approval prompt appears on first run.

- `echo "${GH_TOKEN:0:4}"` → `ghs_`, proving the SessionStart hook injected the installation token via `$CLAUDE_ENV_FILE`.

Variant B additionally (the gate and its fail direction):

- Agent session in a non-org repo → `echo "${GH_TOKEN:-unset}"` → `unset`; `git config --show-scope credential.helper` → osxkeychain at `global` scope; test commit authored as you and signed — the guard emitted only `unset`s, so no bot env leaks in.
- Collaborated path under the guard (the interaction worth proving for Variant B, since the guard re-sets the bot identity every command): in an org repo, `~/.config/acme-agent/bin/as-me git commit --allow-empty -m 'as-me test'` → author is you, while `echo "${GH_TOKEN:0:4}"` still prints `ghs_`.
  `as-me` strips the four identity vars for that one command (falling back to global `user.*`) on top of the env the guard just set — authorship escapes, auth stays the bot.
- Zero-setup enrollment regression (the incident class Variant B exists for): enroll a fresh repo on the App, clone it, and run the bot-identity checks above (GH_TOKEN prefix, credential.helper scope, commit author) in a first-ever session there — they must pass with no per-repo file of any kind.
- Broken-guard regression: temporarily move or chmod away `~/.config/acme-agent/bin/bot-env`; the next Bash command in a Claude Code session must abort with the guard error instead of running with personal credentials.
- Ambiguity direction: in a scratch `git init` repo with no remotes, the next command warns on stderr and `git var GIT_AUTHOR_IDENT` shows the bot — ambiguity resolved toward the bot, never silently personal.
- Mid-session flip: move the session's working directory from a personal repo to an org repo — the very next command shows `ghs_` and the bot author; the reverse direction shows them gone.
- Mixed-case remote regression: in an org repo whose remote spells the host or org with different case (`git@github.com:Acme/x.git`), `GIT_SSH_COMMAND=/usr/bin/false git ls-remote origin` still succeeds — `bot-env` emitted a literal rewrite pair for that remote, covering git's case-sensitive `insteadOf` match.

## Common Mistakes — Claude Code mechanisms

These pitfalls name Claude Code mechanisms specifically; the harness-neutral mistakes stay in the SKILL.

| Mistake | Reality |
| --- | --- |
| Activating `gh` via a PATH shim in shell dotfiles | Claude Code freezes PATH into a snapshot built from a non-login `$HOME/.zshrc` (ignores `.zprofile` and `ZDOTDIR`); inject `GH_TOKEN` via a SessionStart hook writing `$CLAUDE_ENV_FILE` instead |
| Putting `GH_TOKEN` directly in `settings.local.json` `env` | It is a static field; the token expires hourly — mint it per command via the hook + `$CLAUDE_ENV_FILE` |
| Treating `settings.local.json` scoping as a security boundary | It is routing for the well-behaved path; the App installation list is the boundary |
| Centralizing by moving the static env block to user-level `settings.json` | Static env cannot be conditional — the bot activates in every project including other orgs and personal repos; centralize with the per-command guard (Variant B) |
| Treating a failing `SessionStart` preflight as a blocking control | Claude Code can continue after hook startup failure; install the guard first, and make the guard fail inside each Bash command when `bot-env` is unavailable |
| A bare `eval "$(bot-env)"` guard line | Fails open: a crashed or missing script evals the empty string and the session silently runs personal in an enrolled repo; capture the output and abort the command on script failure |
| Trusting the canonical `insteadOf` pair against mixed-case SSH remotes | git's `insteadOf` match is literal and case-sensitive while GitHub accepts `git@github.com:Acme/`, so the repo gets the bot verdict but pushes ride the personal SSH key; normalize remotes in Variant A, and in Variant B `bot-env` derives literal pairs from the matched remotes |
| Compound agent commands that cross repos (`cd <org-repo> && git commit`, `git -C <org-repo>`) | The Variant B verdict binds to the directory the command starts in, so the personal→org direction is silent human attribution; change directory in one command and commit in the next |
| Re-deciding identity with `CwdChanged`/stdin-cwd plumbing | Unneeded — `$CLAUDE_ENV_FILE` contents run before every Bash command in that command's shell and cwd, so a per-command guard tracks directory changes by construction |
| Running Variant A and Variant B together | The per-repo static env pins a stale identity regardless of what the guard decides; pick one and migrate by deleting the per-repo stanzas |
