# Test Scenarios for agent-bot-identity

Behavioral test scenarios for this skill, following the baseline/with-skill methodology: run each scenario with a fresh subagent that does NOT have the skill loaded (baseline), then with the skill loaded (treatment), and compare against the assertions.
A baseline run that already satisfies every assertion means the scenario is too easy; tighten it.
An assertion the with-skill run misses is a finding against the skill, not against the agent.

## How to run

1. **Baseline:** dispatch a subagent with only the scenario prompt below.
   Record which assertions its output satisfies.
2. **Treatment:** dispatch a fresh subagent with the skill content available (or triggered via its description) and the same prompt.
3. **Score:** every assertion is pass/fail with a one-line evidence pointer into the transcript.
   Record results in the table at the bottom.

## Scenario 1: Set up a dual-identity bot (application test)

**Prompt:**

> You are setting up a distinct bot identity for a local AI coding agent (Claude Code) on a macOS laptop.
> Produce a complete written plan as your final answer.
> Do not run any commands or create any files — this is a design/planning exercise; everything you need is stated below.
>
> Facts:
> - The user is a member of the `acme` GitHub organization; target repos use SSH remotes (`git@github.com:acme/*.git`).
> - Global git config: `commit.gpgsign true` with the user's personal GPG key, `credential.helper osxkeychain`, and gh CLI credential helpers for HTTPS.
> - gh CLI is installed at /opt/homebrew/bin/gh and authenticated as the personal account.
> - Login shell is zsh. Claude Code is the agent; it runs shell commands locally as the user, and per-project settings (environment variables and hooks) can be configured via `.claude/settings.local.json`.
> - CI in these repos runs on GitHub Actions.
>
> Goal:
> - Claude Code commits, pushes, and opens PRs as a bot identity (e.g. `acme-agent[bot]`), scoped per-project (only in opted-in repos).
> - Manual git operations in the same repos continue to use the personal account (SSH key, GPG signing, keychain) with zero changes.
> - The agent must be able to read CI/check status on its own PRs.
>
> Deliverables (all four, in order):
> 1. The GitHub-side identity provisioning plan, with the exact identity mechanism and exact permission grants.
> 2. The local isolation mechanism: exact file contents and locations for any scripts, config, or environment setup.
> 3. Verification steps proving both directions (agent sessions use the bot; personal terminal is unchanged).
> 4. An honest statement of what this setup does and does not enforce from a security standpoint.

**Assertions (with-skill run must satisfy):**

- [ ] Chooses a GitHub App over a PAT or second user account; webhook deactivated; installed with "Only select repositories".
- [ ] Permissions include Contents `write`, Issues `write`, Pull requests `write`, AND Checks `read` AND Actions `read` (under an App token `gh pr checks` needs both — the status rollup traverses each check suite's workflow run); `workflows: write` is explicitly excluded with a blast-radius rationale.
- [ ] Commit attribution uses the bot's noreply email (`<UID>+<app-slug>[bot]@users.noreply.github.com`) with the user ID fetched from the API.
- [ ] Token minting: short-lived installation tokens via app JWT; cache file created `0600` atomically (no write-then-chmod window); expiry taken from the response's `expires_at`, not assumed.
- [ ] git auth via per-project env (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`, `GIT_CONFIG_*` with credential-helper reset + bot helper, org-scoped `insteadOf` SSH→HTTPS rewrite, `commit.gpgsign false`) — no shell dotfile edits.
- [ ] gh auth via a SessionStart hook that writes `export GH_TOKEN="$(...)"` to `$CLAUDE_ENV_FILE` (sourced before every Bash command), NOT a `gh` PATH shim in shell dotfiles; rationale notes Claude Code freezes PATH into a snapshot built from a non-login `$HOME/.zshrc` (ignoring `ZDOTDIR`), and that `GH_TOKEN` is dynamic so it cannot be a static `settings.local.json` env value.
- [ ] Verification covers both directions: bot active inside an opted-in agent session (`GH_TOKEN` starts `ghs_`; `gh api installation/repositories`, not `gh api user`) AND personal terminal unchanged (helper, signing, SSH push) — the latter is true by construction since no dotfile changes.
- [ ] Security statement says local scoping is attribution, not containment: the agent can still read the App key, call binaries by full path, and act as the human with personal credentials from any session — so the human-review gate binds the bot token, not the agent (approval-laundering risk named or clearly described).
- [ ] Calls for repo-side enforcement: ruleset audit on installed repos — bot never a bypass actor, required reviews >= 1 with self-approval not counted, and the `required_signatures` interaction (unsigned bot pushes rejected) checked per repo.
- [ ] Caveat present: local commits pushed with an App token are NOT auto-verified; only commits created through the App's API path get the verified badge.

**Expected baseline failures:** reaches for a `gh` PATH shim in a shell dotfile (`~/.zshrc`/`~/.zshenv`) — defeated by Claude Code's frozen, non-login, ZDOTDIR-ignoring snapshot — instead of the `CLAUDE_ENV_FILE` SessionStart hook; tries to put `GH_TOKEN` as a static `settings.local.json` env value (expires hourly); `checks: read`/`actions: read` omitted or misunderstood; isolation framed as a security boundary rather than attribution; no repo-side ruleset audit; `required_signatures` interaction unmentioned; token cache chmod race and hardcoded expiry.

## Scenario 2: Audit a flawed dual-identity setup (retrieval test)

**Prompt:**

> Review this dual-identity bot setup for a local AI coding agent (Claude Code) on a macOS laptop, and report problems with severity and concrete fixes.
> You cannot run commands; you have only this captured material.
> The audience is the engineer who built it, who can change anything.
>
> Captured notes from the engineer:
>
> - GitHub App `acme-agent` registered at the org level; webhook deactivated.
> - Repository permissions: Contents Read and write, Issues Read and write, Pull requests Read and write, Checks Read-only, Workflows Read and write ("so the agent can fix CI when it breaks").
> - Installed on **All repositories** ("simpler than maintaining a list").
> - The App was added to the `main` ruleset's bypass actors with `bypass_mode: always` ("CI was flaky and kept blocking the bot's merges").
> - Shell activation in `~/.zshrc`: `[[ -n $CLAUDE_BOT_IDENTITY ]] && path=($HOME/.claude/bot-shims $path)`.
> - Token script excerpt:
>
> ```python
> r.raise_for_status()
> CACHE.write_text(json.dumps({"token": r.json()["token"], "exp": now + 3540}))
> CACHE.chmod(0o600)
> print(r.json()["token"])
> ```
>
> - The bot env block (`GIT_AUTHOR_*`, `GIT_COMMITTER_*`, credential helper reset + bot helper, org-scoped `insteadOf`) lives in `~/.claude/settings.json` ("one place instead of per-repo files").
>   It contains no `commit.gpgsign` entry; global git config has `commit.gpgsign true` with the engineer's personal GPG key.
> - Verification notes: "Negative test passed-ish: `git ls-remote https://github.com/acme/website.git` (public repo, not enrolled) succeeded — probably GitHub propagation delay, will recheck later."
> - Security summary in their runbook: "Required reviews are >= 1 and GitHub blocks self-approval, so the agent cannot merge unreviewed code. The per-project env scoping ensures the agent can only ever act as the bot."

**Assertions (with-skill run must satisfy):**

- [ ] Bypass-actors entry flagged at the top severity: the App can push straight past the ruleset and its required checks; remediation removes the App from bypass (and fixes flaky CI instead), never softens the mode.
- [ ] Workflows `write` flagged: hands a prompt-injected agent the ability to rewrite CI, and forfeits the server-side rejection of workflow-file pushes; "fix CI when it breaks" is not a justification.
- [ ] All-repositories install flagged: the installation list is the real blast-radius boundary; switch to "Only select repositories".
- [ ] `~/.zshrc` PATH-shim approach flagged as fundamentally fragile under Claude Code: the snapshot is built from a non-login `$HOME/.zshrc` (so `.zprofile` is skipped and `ZDOTDIR` is ignored) and freezes PATH before per-project env applies; remediation replaces it with a SessionStart hook writing `GH_TOKEN` to `$CLAUDE_ENV_FILE`, not a different dotfile.
- [ ] Token script flagged on both counts: write-then-chmod leaves a umask window (create `0600` atomically) and `now + 3540` assumes the expiry (parse `expires_at`).
- [ ] Global `~/.claude/settings.json` placement flagged: a static env block cannot be conditional, so the bot identity activates in every project including non-enrolled ones; remediation is per-repo `.claude/settings.local.json` or a user-level per-command guard gated on the org remote — the flaw is the missing gate, not centralization itself.
- [ ] Missing `commit.gpgsign false` flagged: bot-authored commits get signed with the personal GPG key — an attribution mismatch.
- [ ] Public-repo negative test debunked: public repos are readable over unauthenticated HTTPS, so the success proves nothing about the installation boundary (it is not "propagation delay"); re-run against a private non-enrolled repo.
- [ ] Runbook security claim corrected: the agent holds both identities, so it can author as the bot and approve as the human (approval laundering) — GitHub's self-approval block does not close this; the review gate binds the bot token, and env scoping is routing for the well-behaved path, not a guarantee.
- [ ] Overall framing lands on attribution-not-containment, with real containment requiring an architectural change (separate OS user, container, or VM).

**Expected baseline failures:** the PATH-shim approach accepted as fine (or only the `~/.zshrc`-vs-`~/.zshenv` placement quibbled, missing that any dotfile PATH shim is defeated by the frozen non-login snapshot, and not reaching for `CLAUDE_ENV_FILE`), the public-repo negative test accepted or misdiagnosed, the runbook's review-gate claim accepted or only partially corrected (self-approval block treated as sufficient), the gpgsign attribution mismatch missed, token-script nits missed; the bypass actor and Workflows grants are likely caught even at baseline.

## Scenario 3: Mixed-contribution design question (judgment test)

**Prompt:**

> You maintain a dual-identity setup for Claude Code on your laptop: in opted-in repos, per-project configuration (`.claude/settings.local.json` env plus a SessionStart hook) routes all git and gh activity through a GitHub App bot identity (`acme-agent[bot]`), while your personal terminal keeps your own SSH key, GPG signing, and gh login.
> In some of these repos you contribute both yourself (pair-programming with the agent interactively) and via autonomous agent runs (subagents dispatched from interactive sessions, headless sessions, scheduled jobs).
> The current all-or-nothing arrangement is cumbersome: collaborated work is misattributed to the bot, and you sometimes keep two checkouts to switch identities.
> Proposal under consideration: invert the setup — agent sessions use your personal credentials by default, and a dedicated subagent uses the bot credentials for autonomous work.
> Evaluate the proposal and recommend a design.
> You cannot run commands; produce your analysis and recommendation as text.

**Assertions (with-skill run must satisfy):**

- [ ] Rejects the personal-default proposal on fail-direction grounds: a forgotten switch attributes autonomous agent work to the human (provenance loss in their name), which is worse than the inverse misattribution — collaborated work showing as the bot — which is amendable (`--amend --reset-author`).
- [ ] Keeps the bot as the session default; personal identity is a per-command/per-task escape, not a session or repo mode.
- [ ] The escape is authorship-only: flip or unset `GIT_AUTHOR_*`/`GIT_COMMITTER_*` (falling back to global `user.*`) while pushes, gh calls, and PRs stay on the bot token; no personal credentials enter agent sessions (approval-laundering surface preserved as-is).
- [ ] Notes that subagents inherit the session environment, so a bot-credentialed subagent cannot cleanly carry different credentials; the attribution boundary is per unit of work, and only a per-command mechanism covers mixing within one interactive session.
- [ ] States a use rule: the human explicitly marks collaborated work; the agent never self-decides; subagents, headless runs, and scheduled agents are bot-attributed by construction.

**Expected baseline failures:** accepts the personal-default-plus-bot-subagent proposal (or rejects it only on convenience grounds, missing the fail-direction argument); proposes per-session or per-repo toggles that cannot handle within-session mixing; brings personal gh or SSH credentials into agent sessions for "fully personal" PRs; misses subagent env inheritance.

## Scenario 4: Centralize activation at the user level (design test)

**Prompt:**

> You maintain a dual-identity setup for Claude Code on a macOS laptop, currently wired per-project.
> A GitHub App `acme-agent` (installed on selected `acme` org repos, "Only select repositories") provides hour-long installation tokens via a cached mint script at `~/.claude/bot-shims/bot-token`, and a git credential helper at `~/.claude/bot-shims/git-credential-bot` feeds those tokens to git.
> Each enrolled repo has a hand-created `.claude/settings.local.json` carrying the static bot env (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`, `GIT_CONFIG_*` credential-helper reset plus bot helper, org-scoped SSH→HTTPS `insteadOf`, `commit.gpgsign false`) plus a SessionStart hook that appends a per-command `GH_TOKEN` mint line to `$CLAUDE_ENV_FILE`, which Claude Code sources before every Bash command.
> The personal terminal keeps the user's SSH key, GPG signing, and osxkeychain credentials.
>
> Pain: every newly enrolled repo needs the per-repo file created and gitignored by hand; last month one enrolled repo's file was forgotten, and the agent quietly worked there as the human for a week before anyone noticed.
>
> Goal: centralize activation so any Claude Code session automatically gets the bot identity in `acme` org repos and stays personal everywhere else — no per-repo files, no shell-dotfile edits, personal terminal untouched.
>
> Design the replacement wiring as text (you cannot run commands):
> 1. Exact configuration locations and contents.
> 2. The activation decision: what is checked, when it is checked and re-checked during a session, and which way the decision should fail when the answer is ambiguous.
> 3. Verification steps proving the agent is the bot in enrolled repos, personal elsewhere, and the personal terminal is unchanged.
> 4. What the new wiring enforces and what it does not.
>
> Facts you may rely on: Claude Code supports hooks configured in user-level `~/.claude/settings.json` that apply to all projects; hook processes receive event JSON on stdin; settings-file `env` values are static strings; the `$CLAUDE_ENV_FILE` mechanism works as described above.

**Assertions (with-skill run must satisfy):**

- [ ] Activation lives in a user-level `~/.claude/settings.json` `SessionStart` hook — not per-repo files, not shell dotfiles, and explicitly NOT a static `env` block in user settings (static env cannot be conditional, so it would activate the bot in every project including other orgs and personal repos).
- [ ] The hook installs a single unevaluated decision line into `$CLAUDE_ENV_FILE` (idempotently — `SessionStart` re-fires on resume and clear), so the bot-or-personal decision re-runs before every Bash command in that command's shell and working directory; mid-session directory changes flip identity on the next command with no session-level cached verdict.
- [ ] The decision line fails closed against script failure: the script's output is captured and the preamble aborts the command (non-zero exit) when the script errors, because `eval "$(script)"` directly turns a crash into an empty eval — a silently personal session in an enrolled repo, the headline failure mode.
- [ ] On a bot verdict the script emits the complete bot env — identity vars, `GIT_CONFIG_*` (helper reset, bot helper, org-scoped `insteadOf`, `commit.gpgsign false`), and `GH_TOKEN` — minting per command through the existing cache and substituting a non-empty invalid token when the mint fails; on a personal verdict it emits no bot env (emitting nothing is acceptable; emitting explicit `unset`s of the bot vars is preferred, to defend against a reused per-command shell).
- [ ] The gate is an org match on the repo's remotes, justified by fail direction: ambiguity (remote query fails, repo has no remotes) resolves toward the bot, because a non-enrolled repo wrongly getting bot env fails loudly at push against the installation boundary, while the inverse — an enrolled repo silently staying personal — is the headline failure mode; a local allowlist file is rejected or explicitly warned against on exactly these grounds.
- [ ] The trade-off against the per-project variant is stated: explicit per-repo opt-in disappears, the App installation list remains the only enforcement, and personal-authorship work inside org repos goes through a per-command authorship escape, not a personal-credentials session mode.
- [ ] Migration is explicit: the per-repo `.claude/settings.local.json` stanzas and the per-repo SessionStart hook are removed so they cannot drift as a second source of truth.
- [ ] Verification covers three states — an enrolled org repo session (bot active: `ghs_` token, command-scope helper), a non-org repo session (no bot env, personal helper untouched), and the personal terminal (unchanged by construction) — plus the incident regression: a freshly enrolled repo passes with zero per-repo setup.
- [ ] Attribution-not-containment is restated for the new wiring: the per-command gate is routing for the well-behaved path; the agent can still read the key, call the mint script, or act as the human.

**Expected baseline failures:** centralizes with a static env block in `~/.claude/settings.json` (ungated — activates everywhere) or a shell-dotfile/PATH mechanism; freezes the verdict at session start (no per-command re-decision, stale across mid-session directory changes); `eval`s the decision script's output without capturing failure (crash = empty eval = silently personal); `GH_TOKEN` evaluated once at hook time (frozen, expires mid-session); appends to `$CLAUDE_ENV_FILE` without an idempotence guard (line accumulation across resume/clear); picks a local allowlist or per-command API lookup without fail-direction analysis; verification misses the non-org-repo session state.

## Scenario 5: Broken Variant B guard setup (regression test)

**Prompt:**

> Review this centralized Variant B bot-identity setup for a local AI coding agent (Claude Code) on a macOS laptop, and report problems with severity and concrete fixes.
> You cannot run commands; you have only this captured material.
>
> Intended design:
> - A user-level `~/.claude/settings.json` `SessionStart` hook should make Claude Code use the GitHub App bot identity in `acme` org repos and stay personal elsewhere.
> - `~/.claude/bot-shims/bot-token` and `~/.claude/bot-shims/git-credential-bot` already exist and work.
> - `~/.claude/bot-shims/bot-env` should decide bot vs personal per Bash command and emit the needed env.
>
> Captured hook:
>
> ```bash
> #!/usr/bin/env bash
> set -euo pipefail
> if [ -z "${CLAUDE_ENV_FILE:-}" ]; then
>   echo "missing env file" >&2
>   exit 1
> fi
> if [ ! -x "$HOME/.claude/bot-shims/bot-env" ]; then
>   echo "bot-env missing or not executable" >&2
>   exit 1
> fi
> line='__bot_env="$("$HOME/.claude/bot-shims/bot-env")" || { echo "bot-env failed" >&2; exit 1; }; eval "$__bot_env"'
> grep -qxF -- "$line" "$CLAUDE_ENV_FILE" 2>/dev/null || printf "%s\n" "$line" >> "$CLAUDE_ENV_FILE"
> ```
>
> Captured incident:
> - `bot-env` was present but not executable after a permissions mistake.
> - Claude Code displayed the hook failure during session startup but still allowed Bash commands.
> - The engineer then ran `git commit` in an enrolled `acme` repo, and the commit used the personal author from global git config.
>
> Explain what is wrong, why it is severe, and exactly how to change the hook/guard and verification procedure.

**Assertions (with-skill run must satisfy):**

- [ ] Identifies the top issue as a fail-open Variant B guard installation path, because `SessionStart` failure is non-blocking and can leave later Bash commands without any identity guard.
- [ ] Rejects hook-time `bot-env` executable preflight as the only protection; the installed `$CLAUDE_ENV_FILE` guard must check missing or non-executable `bot-env` inside every Bash command.
- [ ] Keeps capture-then-eval for `bot-env` output and says malformed emitted shell or a crashing `bot-env` must abort the Bash command.
- [ ] States that the failure mode is severe because it silently attributes enrolled org work to the human, which is the headline problem this skill prevents.
- [ ] Requires verification before git or `gh` work: `GH_TOKEN` starts `ghs_`, `credential.helper` is command-scope bot helper in an org repo, and the broken-guard case aborts instead of running personal.
- [ ] Preserves the existing routing model: org-remote verdicts still decide bot vs personal, ambiguous repo states still resolve toward bot, and personal repos still emit no bot env.

**Expected baseline failures:** treats `SessionStart` failure as sufficient because the hook printed an error; keeps the executable preflight in the hook instead of moving it into the per-command guard; misses that the incident is silent human attribution, not just hook reliability; verifies only happy-path bot activation and not the broken-guard abort path.

## Results

> [!IMPORTANT]
> **Mechanism change (2026-06-10, after real-world deployment).** The original skill activated `gh` with a PATH shim loaded from a shell dotfile (`~/.zshenv`).
> Deploying it on a real machine proved this fails under Claude Code: the Bash tool replays a *shell snapshot* built from a **non-login** shell sourcing **`$HOME/.zshrc`** (ignoring `ZDOTDIR`), with **PATH frozen** before the per-project env applies — so no dotfile PATH shim activates.
> Debug logs confirmed the snapshot path; the fix that worked (verified live: `GH_TOKEN` = `ghs_…`, `gh api installation/repositories` = enrolled count) is a **`SessionStart` hook writing `GH_TOKEN` to `$CLAUDE_ENV_FILE`**, the documented per-command env mechanism.
> The skill and the assertions above were rewritten to match.
> The first block of rows below scored the **prior PATH-shim skill**; assertion 6 (Scenario 1) and assertion 4 (Scenario 2) have since changed, so those rows are retained as history but **do not reflect the current assertions**.
> The hook-based skill was then re-validated against the new assertions — see the "Hook-based mechanism (current skill)" block (Scenario 1 baseline 5/10, with-skill 10/10).

Note: Scenario 1's assertion 2 originally treated Actions `read` as optional ("workflow-log access").
An independent review (2026-06-10, finding H1) established that `gh pr checks` under an App token also requires `actions: read`, and the assertion and skill were corrected together; the first two Scenario 1 rows below were scored against the original wording, and the re-run row against the corrected one.

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-06-10 | 1 (set up) | baseline | 5/10 | Strong: GitHub App with webhook off and select-repos install; bot noreply email with API-fetched UID; command-scope env isolation (helper reset, org-scoped `insteadOf`, `gpgsign false`); thorough two-direction verification with negative tests; unverified-badge caveat with a `createCommitOnBranch` pointer. Missed: shell guard placed in `~/.zprofile`/`~/.zshrc` (never sourced by non-interactive zsh); `Issues: write` omitted; token expiry hardcoded `now+3500` instead of `expires_at` (`umask 077` did avoid the chmod race); "rulesets require human approval" listed as enforced with no agent-approves-as-human laundering gap; no per-repo ruleset audit (prospective hardening only, no self-approval-not-counted). |
| 2026-06-10 | 1 (set up) | with-skill | 10/10 | All assertions satisfied. App with webhook off and select-repos install; exact permission set with Checks read required, Actions read distinguished as logs-only, Workflows write refused with the server-side-rejection rationale; bot noreply email with UID; `expires_at` parsed and cache created 0600 atomically, both called out as deliberate; full env isolation block; `~/.zshenv` with the non-interactive/non-login explanation and the `which gh` canary; two-direction verification incl. `GIT_SSH_COMMAND=/usr/bin/false` and non-enrolled-repo negative tests; ruleset audit walked per repo incl. self-approval-not-counted and `required_signatures` rejection; attribution-not-containment stated with approval laundering named and the gate-binds-token-not-agent framing; unverified-badge caveat present. |
| 2026-06-10 | 1 (set up) | with-skill (re-run, post-review fixes, corrected assertion 2) | 9/10 | Carries every review fix: Actions read granted with the rollup/workflowRun rationale and exact error string; `timeout=10` with the hung-push rationale; private-non-enrolled negative probe with the public-repo caveat; org-owner install note; canary treated as authoritative over file placement; edit-the-shims example; self-approval covered in the security section (audit list itself says reviews ≥ 1 without restating it). **Miss (finding against the skill):** assertion 10 — the unverified-badge caveat never appears; the fact lived only in the Common Mistakes table, so it was compressed out. Fix: caveat added to the Phase 6 test-commit step, on the verification path. |
| 2026-06-10 | 1 (set up) | with-skill (v3, after badge-caveat relocation and fail-closed gh shim) | 10/10 | All assertions satisfied, including assertion 10: the unverified-badge caveat now surfaces in the verification section ("Expected quirk: bot commits show no Verified badge…"), confirming the relocation fix. The run also reproduces the fail-closed gh shim with the falls-back-to-personal-auth rationale, and folds the repo-side ruleset audit into deliverable 1 as a "required companion step". |
| 2026-06-10 | 2 (audit) | baseline | 8/10 | Strong: bypass actor at top severity with the org-wide-push reasoning; Workflows write with secret-exfiltration rationale and "fix CI" rejected; all-repos install; both token-script nits with correct fixes; gpgsign attribution mismatch (plus pinentry-stall bonus); public-repo negative test debunked as untestable-by-design; approval laundering described both directions with self-approval block called insufficient; attribution-not-containment with separate-OS-user remediation. Missed: the `~/.zshrc` non-interactive trap entirely (reframed as a fail-open-default critique; no `.zshenv`, no canary); bypass remediation hedged with "at absolute most `bypass_mode: pull_request`" for the App — softening the mode for an automation identity, which the assertion forbids. Bonus insight adopted into the skill: the gh shim was fail-open on mint failure (empty `GH_TOKEN` falls back to personal auth); shim now uses `set -euo pipefail`. |
| 2026-06-10 | 2 (audit) | with-skill | 10/10 | All assertions satisfied. Bypass actor is the top Critical with removal-only remediation ("never solve flakiness with bypass" — no mode softening); Workflows write flagged with the prompt-injection rationale, the forfeited server-side rejection, and "fix CI" rejected; all-repos install → only-select with the blast-radius framing; `~/.zshrc` trap caught precisely (non-interactive shells, shim silently never fires, `.zshenv` fix, `which gh` canary); both token-script flaws with exact fixes plus an unprompted timeout check; global → per-repo `settings.local.json` with the gitignore nuance; gpgsign mismatch with the `required_signatures` interaction; public-repo negative test debunked (and the all-repos-makes-it-impossible insight added); runbook corrected with both-identities approval laundering and gate-binds-token; attribution-not-containment with architectural containment. Unprompted bonus: flagged the missing Actions read with the rollup rationale. |

### Scenario 4 — user-level activation (added 2026-06-11)

> [!NOTE]
> **Mechanism verification (2026-06-11).** Scenario 4's assertions encode a per-command eval-guard design, which rests on one load-bearing fact about `$CLAUDE_ENV_FILE`: its contents are evaluated before **every** Bash command, in that command's shell and working directory — not once at session start.
> Verified empirically on Claude Code 2.1.172: a headless session launched with `CLAUDE_ENV_FILE` pointing at a file containing `export PROBE_PREAMBLE_PWD="$PWD"` showed `PROBE_PREAMBLE_PWD == $PWD` for each command, and inspection of the installed build confirmed the file's contents are cached as a string per session and prepended to each Bash command (re-evaluated per command; the cache invalidates when hooks rewrite the file).
> A consequence verified the fail-closed assertion: `eval "$(script)"` on a crashed script evals the empty string and the command proceeds silently personal, so the guard line must capture output and abort on script failure.

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-06-11 | 4 (user-level) | baseline (no skill) | 5/9 | Strong architecture instincts: independently invented the per-command eval-guard line with `grep -qxF` idempotence (1, 2), full env emission with fail-closed block scripts on mint/git failure (4), explicit decommissioning of per-repo files (7), three-state verification with fail-closed proofs (8). Standout principle stated unprompted: "ambiguity may cost availability, never attribution". Missed: direct `eval "$(...)"` fails open on script crash (3); zero-remote repos resolve **personal** (silent toward-human) and exceptions go through a repo-mode overrides file rather than a per-command escape (5); no `as-me`/per-task authorship story, personal-mode overrides reintroduce a session-mode switch (6); attribution-not-containment never states the agent can read the key or edit the wiring (9). |
| 2026-06-11 | 4 (user-level) | baseline (current skill) | 8/9 | The existing skill's principles carried almost everything: user-level hook with static-env rejection (1); unevaluated per-command eval line, idempotent append, mid-session flip both directions (2); full env with per-command mint and `BOT-TOKEN-MINT-FAILED` sentinel (4); org match on both SSH forms with ambiguity→bot and a fail-direction asymmetry table, allowlist avoided (5); trade-offs incl. installation-list-as-boundary and `as-me` policy unchanged (6); explicit removals/migration (7); A–D verification incl. the zero-setup enrollment regression (8); attribution-not-containment incl. agent-can-edit-the-wiring (9). **Miss (the finding motivating this edit): assertion 3 — `eval "$(bot-env)"` is used directly, so a crashed or missing decision script evals empty and the session proceeds silently personal in an enrolled repo.** The skill also contained nothing on user-level activation; the run derived it from principles, which this edit codifies. |
| 2026-06-11 | 4 (user-level) | with-skill (Variant B added) | 9/9 | All assertions satisfied. User-level SessionStart hook with the static-env and dotfile/PATH antipatterns explicitly rejected (1); single idempotent unevaluated guard line, per-command re-decision in the command's shell and cwd, mid-session flip both directions, no `CwdChanged` plumbing (2); **capture-then-eval named as one of "three deliberate shapes" with the bare-eval fails-open rationale, plus a decision-table row for script-crash → command aborts** (3); full env emission with per-command mint, `BOT-TOKEN-MINT-FAILED` sentinel, and the empty-`GH_TOKEN`-falls-back warning (4); org-remote gate with the asymmetric-failure argument, allowlist and per-command API lookup both rejected with reasons (5); enrollment-is-one-act trade-off, installation list as the boundary, `as-me` escape verified in the migration checks (6); mandatory migration with repos enumerated from the installation list, not memory (7); three-state verification plus the zero-setup enrollment regression labeled "the entire point of the change" (8); attribution-not-containment with agent-can-edit-the-wiring and approval-laundering mitigations (9). |

> [!NOTE]
> **Hardening after independent review (2026-06-16).** A critical review of the Variant B PR surfaced five fixes, applied to the skill without changing the design:
> (1) the org match no longer pattern-matches the raw remote line at all — it parses each remote down to its authority (`[userinfo@]host[:port]`) and compares the host *exactly* to `github.com`, then checks the org path segment, so a spoofed host (`notgithub.com`), a lookalike (`github.com.evil.tld`), or an org-shaped path on another host (`example.com/@github.com/acme/`) can no longer yield a bot verdict;
> (2) `git-credential-bot` now reads git's stdin request and answers only `https://github.com`, so the installation token can't be coaxed out by a mis-rewritten or hostile remote — material under Variant B, which installs that helper as the only credential helper automatically;
> (3) the personal verdict now emits explicit `unset`s instead of nothing, defending against a hypothetical reused per-command shell;
> (4) the machine-wide blast radius of a broken `bot-env` (it aborts every Bash command in every session) is now stated as the cost of never failing open;
> (5) the Variant B verification adds an `as-me` check, since the guard re-sets the bot identity every command.
> Assertion 4's "emits nothing" was reworded to "emits no bot env" to match (3); the 9/9 row above predates these edits and was not re-scored against the new wording.

### Scenario 5 — broken Variant B guard setup (added 2026-06-16, first run 2026-07-07)

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-07-07 | 5 (broken guard) | baseline | 4/6 | Unusually strong on the core bug: fail-open install path identified as the top Critical with the SessionStart-is-advisory reasoning (1); validation moved into the guard line itself (2); capture kept and garbage output rejected fail-closed, via regex validation rather than eval-abort (3, met in substance); silent personal attribution named as the incident (4). Missed: pre-git verification specifics — no `ghs_` prefix or command-scope helper checks, used `git var GIT_AUTHOR_IDENT` only (5); replaced the routing model wholesale with `includeIf`-based gitconfig as source of truth plus a PreToolUse deny backstop, abandoning per-command env routing and calling the `CLAUDE_ENV_FILE` semantics unverified (6). Baseline bonus adopted into the skill: a compound `cd <org-repo> && git commit` is classified by the starting directory — a real silent-personal gap, now documented with the split-the-commands guidance. |
| 2026-07-07 | 5 (broken guard) | with-skill | 6/6 | All assertions satisfied. Fail-open install path as the top Critical with the exact incident mechanics and the mid-session vs session-start asymmetry (1); all availability checking moved into the per-command guard line, hook installs unconditionally (2); capture-then-eval kept with malformed-shell abort (3); silent human attribution named as the headline failure mode (4); pre-git verification with `ghs_` prefix, command-scope helper, and the broken-guard abort regression (5); routing model preserved — org-remote verdicts, ambiguity→bot, personal repos emit no bot env (6). Unprompted bonuses: `__bot_env` unset hygiene, and the guard-line idempotence migration hazard (old line survives a text change in a resumed session) — both adopted into the skill/scripts. |

> [!NOTE]
> **Hardening after dual-model review (2026-07-07, Claude Fable review cross-checked with Codex).** Applied without changing the design:
> (1) `bot-env` now derives extra `insteadOf` pairs from the matched remotes themselves — git's `insteadOf` match is literal and case-sensitive while GitHub accepts mixed-case hosts/orgs, so the canonical pair alone could take a bot verdict yet let pushes silently ride the personal SSH key (`git@github.com:Acme/`); verified against 12 remote-URL cases including live rewrite checks, with all spoof cases still resolving personal;
> (2) `as-me` refuses zero arguments (bare `env` would print the whole environment, `GH_TOKEN` included);
> (3) documented: the Variant B verdict binds to a command's starting directory (compound `cd <org-repo> && git commit` is a silent-personal gap — split the commands), Variant A's session-static env follows the session into other directories, the personal verdict clobbers user-set `GH_TOKEN`/`GIT_AUTHOR_*`/`GIT_CONFIG_*` in agent commands, the guard-line idempotence migration hazard, and `bot-token`'s benign double-mint race;
> (4) the `CLAUDE_ENV_FILE` per-command mechanism now cites the official hooks reference ("Persist environment variables") instead of resting on the 2.1.172 probe alone;
> (5) frontmatter description trimmed to triggering conditions (~450 chars); the PATH-shim mechanism detail/adapter contract and the approval-laundering mitigation ladder moved to `references/` with load-bearing facts kept inline.
> All five scenarios were re-run with-skill against the edited skill the same day — see the rows below.

### Post-hardening re-validation (2026-07-07, edited skill with `references/`)

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-07-07 | 1 (set up) | with-skill | 10/10 | All assertions satisfied against the restructured skill, including assertion 10 (Verified-badge caveat — the historical compression casualty — surfaced in both the env-block notes and the gpgsign rationale). The plan also reproduced the new content: literal/case-sensitive `insteadOf` caveat with the normalize-remotes fix, `as-me` zero-arg refusal, Variant A session-follows-not-directory behavior, and the no-lock mint note. Depth note: the PATH-shim rationale said "PATH-frozen, non-login shell snapshot" without repeating the ZDOTDIR detail (present inline in the skill; compressed in the output). |
| 2026-07-07 | 2 (audit) | with-skill | 9/10 | Bypass actor top Critical with removal-only remediation; Workflows write with the forfeited-server-side-control framing; all-repos install flagged incl. the negative-test-impossible insight; zshrc shim called dead code under the frozen snapshot plus the personal-PATH leak risk; both token-script flaws plus timeout and double-parse bonuses; ungated-global env antipattern with A-or-B remediation; gpgsign mismatch; public-repo negative test debunked on both grounds; runbook corrected with both-identities laundering and scoping-as-routing. **Miss: assertion 10's architectural-containment clause (separate OS user/container/VM) was not repeated** — the framing landed attribution-not-containment, and the fact remains inline in When Not to Use and What This Enforces, so this is recorded as run-level output compression rather than a skill gap; watch on future runs. |
| 2026-07-07 | 3 (mixed contribution) | with-skill | 5/5 | All assertions satisfied; cited the new Common Mistakes inversion row by name, added the Verified-signature false-attestation angle, the three-autonomous-paths coverage argument, and the session-mode "pair flag" leak-into-subagents trap. |
| 2026-07-07 | 4 (user-level) | with-skill | 9/9 | All assertions satisfied; the design additionally carried the new material — remote-derived `insteadOf` pairs for mixed-case/`ssh://` forms, the compound-command starting-directory quirk with split-the-commands guidance, the personal-verdict env-clobbering cost, the fresh-sessions-after-guard-change migration note, and the selective in-org opt-out → stay-on-Variant-A trade-off. |
| 2026-07-07 | 5 (broken guard) | with-skill | 6/6 | All assertions satisfied; also derived the fixed-chmod-recovers-next-command contrast, the mixed-old-and-new guard-line convergence behavior, and the incident-remediation path (`--amend --reset-author` mirror image, force-push ruleset coordination). |

> [!NOTE]
> **Context/constraints separation pass (2026-07-07, separating-context-from-constraints audit).** Four semantic-preserving rewrites: the Variant A normalize-remotes directive now leads its bullet as an imperative instead of trailing an explainer sentence (R1); "keep `bot-env` small" became a testable dependency constraint, with the Phase 5 re-run obligation split into its own sentence (R3) — the constraint's first wording ("shells out only to `git` and `bot-token`") was inaccurate (the script also uses `tr`/`cat`) and was refined in Copilot review round 3 to the current no-network-calls property; the allowlist and per-command-network prohibitions split into separate sentences (R4); the Phase 6 closing sentence unbundled into three (R4).
> Spot-check re-runs of scenarios 1 and 4 (the assertions exercising the rewritten passages): 10/10 and 9/9; the normalize-remotes rule and the no-network-calls constraint both surfaced in the outputs.

### Scenario 3 — mixed contribution (first run 2026-07-07)

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-07-07 | 3 (mixed contribution) | baseline | 2/5 | Strong instincts on the two assertions it passed: rejected the inversion on asymmetric-misattribution grounds incl. the false-attestation framing of GPG-signed bot work (1), and called the bot subagent a prompt-level control that shares env/HOME/ssh-agent with the parent (4). Failed exactly as predicted: designed a three-mode scheme where *interactive agent sessions default to personal credentials* (a session-mode toggle, not a per-command escape) (2); brought a personal fine-grained PAT into agent sessions for interactive work (3); keyed the use rule on session supervision mode with launcher-set env, not explicit per-unit-of-work human marking (5). |
| 2026-07-07 | 3 (mixed contribution) | with-skill | 5/5 | All assertions satisfied. Inversion rejected on fail-direction grounds with the amend-recoverability asymmetry and the unattended-path framing (1); bot stays the session default with `as-me` as the per-command escape (2); escape authorship-only, pushes/`gh`/PRs on the bot token, laundering surface preserved (3); subagent env inheritance stated, per-command wrapping named as the only real granularity, headless/scheduled noted as top-level sessions a subagent scheme cannot cover (4); explicit-direction-only use rule with subagents/headless/scheduled bot-attributed by construction (5). Bonus: named the honest residual friction (unsigned `as-me` commits where a repo requires signatures). |

### Post-restructure spot-check (2026-07-07, core + adapter split)

> [!NOTE]
> **Restructure regression check.** The skill was split into a harness-neutral core (`SKILL.md`, whose Phase 4 states the adapter routing contract) plus per-harness adapter references (`references/adapters/claude-code.md`, `references/adapters/codex.md`).
> Scenarios 1 and 4 exercise the passages that moved, so both were re-run with-skill against the restructured skill.
> Adapter discoverability was part of the test: each run was pointed only at `SKILL.md` and told it might need other files under `references/`, never at the adapter directly.
> Both runs found `claude-code.md` through the Phase 4 links unaided, and both matched their post-hardening scores.

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-07-07 | 1 (set up) | with-skill (restructured skill) | 10/10 | All assertions satisfied through the core-plus-adapter path; the run read `SKILL.md`, followed Phase 4 to `references/adapters/claude-code.md`, and also pulled `references/approval-laundering.md` and the bundled scripts. The moved Claude mechanism detail all surfaced: the `SessionStart` → `$CLAUDE_ENV_FILE` hook with the full snapshot rationale (non-login `$HOME/.zshrc`, `.zprofile` skipped, ZDOTDIR ignored, frozen PATH) and the GH_TOKEN-is-dynamic argument (6); Checks **and** Actions read with the rollup rationale and the exact "Resource not accessible by integration" string (2); atomic `0600` cache via `mkstemp`+`os.replace` with `expires_at` parsed (4); Verified-badge caveat on the verification path (10). Assertion 9 met in substance: the run mandates walking agent-friendly-github §2 per enrolled repo and names `required_signatures` and human-only approvals, but rides the checklist reference for bypass-actors and reviews-≥1-self-approval-not-counted rather than restating them — that content is inline in core Phase 6 and unmoved, so this is run-level compression, not a restructure loss. |
| 2026-07-07 | 4 (user-level) | with-skill (restructured skill) | 9/9 | All assertions satisfied; the run reached Variant B through the Phase 4 adapter link and read the bundled guard scripts. Per-command re-decision, idempotent single guard line, capture-then-eval with all four abort branches, the ambiguity→bot decision table with the loud-vs-silent asymmetry, remote-derived `insteadOf` pairs, migration removals, the three-state verification plus the zero-setup enrollment regression, and attribution-not-containment all surfaced. Three assertions met in substance rather than verbatim: the static-env rejection appears as "static env never centralized" with the cannot-be-conditional rationale compressed out (1); the allowlist rejection surfaces as "eliminating per-repo state" rather than a named local-allowlist prohibition (5); the per-command authorship escape appears via the `as-me --amend --reset-author` recovery rather than as the designed personal-work path (6). |

### Hook-based mechanism (current skill) — re-validation 2026-06-10

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-06-10 | 1 (set up) | baseline | 5/10 | Unusually strong (did web research): GitHub App + webhook-off + select-repos (1); bot noreply email with API-fetched UID (3); git isolation with no global/dotfile changes, via repo-local `.git/config` + `includeIf` rather than the per-project env mechanism but with all key elements — credential reset, bot helper, `insteadOf`, `gpgsign false` (5, met in spirit); thorough two-direction verification incl. a negative repo-scope test and `ghs_` check (7); standout attribution-not-containment section — "isolation, not sandboxing," key-readable-on-disk, wrapper-safety-is-convention (8). **Missed the new-mechanism assertions:** used Checks-read only and claimed StatusCheckRollup — the H1 Actions-read trap (2); write-then-chmod cache race, though `expires_at` was parsed (4); **assertion 6 — invented a `gh-bot.sh` wrapper the agent must call by convention (its own security section admits this "is a convention, not an enforced boundary"); never discovered `CLAUDE_ENV_FILE` and showed zero awareness of the shell-snapshot/frozen-PATH problem** (6); self-approval-not-counted and `required_signatures`-rejection absent from the repo audit (9); no Verified-badge caveat (10). Confirms the scenario discriminates on the hook mechanism. |
| 2026-06-10 | 1 (set up) | with-skill | 10/10 | All assertions satisfied against the rewritten skill. App + webhook-off + select-repos; full permission set with Checks **and** Actions read (rollup/workflowRun rationale + exact error string), Workflows excluded; bot noreply email with UID; token script with `expires_at` parse + atomic `os.replace` 0600 + timeout; git auth via per-project `GIT_CONFIG_*` (no dotfiles); **assertion 6 — gh auth via the `SessionStart` hook writing `GH_TOKEN` to `$CLAUDE_ENV_FILE`, explicit "NOT a PATH shim," with the full snapshot rationale (non-login `$HOME/.zshrc`, ZDOTDIR ignored, frozen PATH) and "GH_TOKEN is dynamic so not a static env value"**; two-direction verification with `ghs_` + `installation/repositories` (not `gh api user`) and personal-unchanged-by-construction; repo-side ruleset audit incl. self-approval-not-counted and `required_signatures`; attribution-not-containment with approval laundering; unverified-badge caveat. |

## Codex adapter scenarios

These two scenarios exercise the **Codex CLI adapter** (`references/adapters/codex.md`), which implements the SKILL's Phase 4 routing contract for Codex CLI instead of Claude Code.
They follow the same baseline/with-skill methodology as Scenarios 1–5 (a baseline that satisfies every assertion means the scenario is too easy; tighten it).
The Codex mechanism differs from Claude Code's: activation is a **named `bot` profile invoked per run** (`--profile bot`), static identity and `GIT_CONFIG_*` ride the profile's `[shell_environment_policy].set` block, `gh` routes through a Codex-controlled PATH shim, and the sandbox is configured via `sandbox_mode` / `[sandbox_workspace_write]`.
For the with-skill run, the treatment subagent reads both `SKILL.md` and `references/adapters/codex.md`.

### Scenario C1: Set up a Codex bot identity (application test)

**Prompt:**

> You are setting up a distinct bot identity for a local AI coding agent (Codex CLI, `codex-cli 0.143.0`) on a macOS laptop.
> Produce a complete written plan as your final answer.
> Do not run any commands or create any files — everything you need is stated below.
>
> Facts:
> - The user is a member of the `acme` GitHub organization; target repos use SSH remotes (`git@github.com:acme/*.git`).
> - Global git config: `commit.gpgsign true` with the user's personal GPG key, `credential.helper osxkeychain`, and gh CLI credential helpers for HTTPS.
> - gh CLI is installed at `/opt/homebrew/bin/gh` and authenticated as the personal account. Login shell is zsh.
> - A GitHub App bot identity is ALREADY provisioned; a per-invocation token-mint script `bot-token` and a host-gated `git-credential-bot` credential helper ALREADY exist and work (the harness-neutral core). Your task is the Codex-side routing only.
> - Codex CLI configuration surfaces, all empirically confirmed on this version:
>   - Base config is `~/.codex/config.toml`. A NAMED PROFILE is a separate file `$CODEX_HOME/<name>.config.toml` layered on top of the base config only when invoked with `--profile <name>` (`codex sandbox --profile <name> …` / `codex exec --profile <name> …`). There is no repo-local auto-load — a project `.codex/config.toml` at a repo root is NEVER loaded by this version (confirmed even with the directory marked trusted; directory trust only gates interactive approval prompts).
>   - A profile's `[shell_environment_policy]` `set = { … }` inline table injects env vars into every sandboxed command.
>   - `shell_environment_policy.set.PATH` REPLACES PATH and performs NO `$PATH` expansion — a value like `"$SHIMS:$PATH"` is taken literally and breaks command resolution; the value must be a complete literal absolute PATH.
>   - Sandbox keys: `sandbox_mode = "workspace-write"`, and a `[sandbox_workspace_write]` table with `writable_roots = [...]` (absolute paths) and `network_access = true|false`. Without `network_access`, the sandbox blocks network at the DNS layer.
>   - Under the sandbox, `.git`-mutating commands succeed only as a bare literal `git …` invocation; any wrapper, env-prefix, alias, or command-substituted form is denied (`Operation not permitted` on `.git/index.lock`). Ordinary non-`.git` writes under the same session are unaffected.
> - CI runs on GitHub Actions.
>
> Goal:
> - Codex CLI commits, pushes, and opens PRs as a bot identity (e.g. `acme-agent[bot]`), scoped to opted-in repos, invoked with the bot profile.
> - Manual git/terminal operations continue to use the personal account (SSH key, GPG signing, keychain) with zero changes.
> - The agent must be able to read CI/check status on its own PRs.
>
> Deliverables (all four, in order):
> 1. The Codex activation surface and static-identity wiring: exact profile file location and its `[shell_environment_policy].set` contents (identity vars + `GIT_CONFIG_*`), and how it is invoked.
> 2. `gh` auth and PATH: exact mechanism, the PATH value form, and the mint / failure behavior.
> 3. The sandbox permission profile: exact keys and why each is needed.
> 4. Verification steps proving both directions, plus an honest statement of what this setup does and does not enforce — including the status of any human-authorship (`as-me`) escape under Codex.

**Assertions (with-skill run must satisfy):**

- [ ] Static identity + `GIT_CONFIG_*` (credential-helper reset + bot helper, org-scoped `insteadOf`, `commit.gpgsign false`) delivered through the `bot` profile's `[shell_environment_policy].set` on the recorded per-invocation activation surface — NOT `$CLAUDE_ENV_FILE`, NOT `.claude/settings.local.json`, NOT a `SessionStart` hook (Claude-harness mechanisms not proposed for Codex; if named, named only to reject as inapplicable).
- [ ] `gh` via a Codex-controlled PATH shim that mints a fresh installation token per invocation and fails closed with a NON-EMPTY invalid sentinel (so `gh` 401s loudly instead of silently using personal stored credentials on an empty `GH_TOKEN`); the PATH is a complete literal absolute REPLACEMENT value with the shim directory first — never a `$PATH`-referencing prepend; no static `GH_TOKEN` anywhere in Codex config.
- [ ] Dotfile PATH shims / shell aliases (`~/.zshrc`, `~/.zprofile`) rejected as not the Codex-controlled routing surface.
- [ ] Sandbox permission profile stated: `sandbox_mode = workspace-write`, `network_access = true` for the cold mint (GitHub API), and `writable_roots` covering the token cache and the `uv` cache (i.e. installation-key/token read, cache write, network to api.github.com).
- [ ] Activation/trust caveat: `--profile bot` is a per-invocation flag (project `.codex/config.toml` never loaded; directory trust only gates approval prompts), so FORGETTING it silently runs the personal identity — a weaker fail direction than a present-or-absent per-repo file; treat `--profile bot` as mandatory on every invocation.
- [ ] Attribution-not-containment restated for the shim: it is PATH routing, not a sandbox boundary; an absolute-path `gh` (`/opt/homebrew/bin/gh`) or any wrapper around the shim reaches the personal credentials; the hard boundaries are the App installation list and the repos' server-side rulesets (naming the installation list alone satisfies this only if rulesets are named as enforcement elsewhere in the answer).
- [ ] `as-me` limitation acknowledged: under this Codex sandbox a wrapped `git` (the `as-me` escape) is denied — `.git` writes are granted only to a bare literal `git` — so collaborated authorship happens OUTSIDE Codex (personal terminal / Claude Code adapter, or amend later); the plan does NOT promise a working in-Codex `as-me`.

**Expected baseline failures:** reaches for a Claude mechanism (`CLAUDE_ENV_FILE`/`SessionStart`/`settings.local.json`) or a shell-dotfile PATH shim; puts a static `GH_TOKEN` in the profile (expires hourly) instead of a per-invocation mint; leaves `gh` fail-OPEN on mint failure (empty `GH_TOKEN` falls back to personal); misses the per-invocation activation fail-direction; frames the shim as containment rather than attribution; promises a working in-Codex `as-me` despite the sandbox denial.

### Scenario C2: Audit a flawed Codex bot setup (retrieval test)

**Prompt:**

> Review this Codex CLI (`codex-cli 0.143.0`) bot-identity setup for a local AI coding agent on a macOS laptop, and report problems with severity and concrete fixes.
> You cannot run commands; you have only this captured material. The audience is the engineer who built it, who can change anything.
>
> Facts about this Codex version (all empirically confirmed):
> - A bot identity is activated by a named profile file `~/.codex/bot.config.toml` invoked with `--profile bot`.
> - `shell_environment_policy.set.PATH` REPLACES PATH and does NO `$PATH` expansion — a value containing `$PATH` is taken literally.
> - `[sandbox_workspace_write].network_access` gates whether sandboxed commands can reach the network; without it, DNS is blocked at the sandbox layer.
> - The intended token path is a GitHub App plus a per-invocation token-mint script (`bot-token`) and a `gh` shim that mints a fresh short-lived installation token on every call. Installation tokens are short-lived (expire ~1 hour).
>
> Captured notes from the engineer:
> - `~/.codex/bot.config.toml` → `[shell_environment_policy]`:
>
>   ```toml
>   set = { GH_TOKEN = "ghs_AAAABBBBCCCC...redacted", PATH = "$HOME/.codex/shims:$PATH", GIT_AUTHOR_NAME = "acme-agent[bot]", GIT_AUTHOR_EMAIL = "1234567+acme-agent[bot]@users.noreply.github.com", GIT_COMMITTER_NAME = "acme-agent[bot]", GIT_COMMITTER_EMAIL = "1234567+acme-agent[bot]@users.noreply.github.com" }
>   ```
>
>   "I minted the `GH_TOKEN` by hand last week so `gh` just works without any shim overhead. The shims dir is first on PATH so my `gh` wins."
> - `~/.zshrc`: `alias gh=/opt/homebrew/bin/gh` ("so I can still use gh normally in my own terminal").
> - `[sandbox_workspace_write]` in the same profile: `writable_roots = ["~/.codex/cache"]`, with NO `network_access` line.
> - Runbook: "The `bot-token` mint script exists as a fallback but I haven't needed it — with the static token, minting just works. Sandbox is locked down tight: no network access, which is good for security."

**Assertions (with-skill run must satisfy):**

- [ ] Stale static `GH_TOKEN` flagged (top or high severity): installation tokens expire hourly, so a week-old literal is long dead — `gh` fails or, worse, a static token masks the per-invocation mint; `GH_TOKEN` must come ONLY from the shim's per-call mint, never from Codex config.
- [ ] Literal `$PATH` in `set.PATH` flagged: `set.PATH` REPLACES and does not expand `$PATH`, so the value is taken literally — command resolution breaks or the shim never wins; fix is a complete literal absolute PATH with the shim dir first.
- [ ] The `~/.zshrc` `gh` alias flagged as a bypass of the Codex-controlled PATH (a dotfile alias is not the sandbox routing surface; resolving to the real `gh` by absolute path reaches the personal credentials).
- [ ] Sandbox/network gap flagged: with no `network_access`, the cold mint's GitHub API call fails at DNS, so the (correct) per-invocation mint cannot run; a fail-closed non-empty sentinel is expected on mint failure, and the required profile must set `network_access = true` with `writable_roots` covering the token + `uv` caches.

**Expected baseline failures:** accepts the static `GH_TOKEN` as a reasonable optimization or misses that it expires hourly; misreads the literal `$PATH` as a working prepend; treats the dotfile alias as harmless (personal-terminal only) rather than a routing bypass; endorses "no network = more secure" without connecting it to the mint failing at DNS, or omits the fail-closed sentinel expectation.

### Live verification record

> [!NOTE]
> **Codex adapter partial live verification (2026-07-07).** The Codex Variant A core routing checks were exercised against `codex-cli 0.143.0`, but the GitHub write path was not exercised; the durable evidence summary lives in `references/adapters/codex.md` (Status + Verification sections), backed by a session-local verification transcript (session records).
> - **Codex version:** `codex-cli 0.143.0`.
> - **Historical activation probe:** the named `bot` profile (`~/.codex/bot.config.toml`) was invoked per run with `--profile bot`, and the base `~/.codex/config.toml` was not edited.
> - **Current supported activation:** invoke the guarded `codex-bot` launcher by absolute path; it checks the profile file, forces the bot profile, and rejects profile overrides.
> - **Project-config probe:** a project-scoped `.codex/config.toml` did not load on the tested 0.143.0 surfaces, including with the directory explicitly trusted, but this version-specific observation conflicts with broader current configuration documentation and is not a general claim that project config never loads.
> - **Recorded sandbox quirk:** `--profile bot` applied `[shell_environment_policy].set` (PATH + all `GIT_*`) but not the profile's top-level `sandbox_mode` key by itself, so sandboxed checks forced it with `-c 'sandbox_mode="workspace-write"'` and `codex exec` used `-s workspace-write`.
> - **Sandbox profile:** `sandbox_mode = workspace-write`, `network_access = true` (cold mint only; a negative control failed at DNS), `writable_roots` = the `uv` cache (required every run — `bot-token` is a `uv run --script`) and the bot-config token-cache dir.
> - **`as-me` status:** documented limitation — under this sandbox `.git` writes succeed only for a bare literal `git`; every wrapper/env-prefix/alias/command-substituted form is denied, so `as-me` cannot run in Codex; a literal `--author=` flag yields author=human but committer=bot. Collaborated authorship therefore happens outside Codex.
>
> Task 7's ten checks (pointer: `references/adapters/codex.md` Verification section; verification transcript is session-local):
>
> | # | Check | Result |
> | --- | --- | --- |
> | 1 | `command -v gh` → shim wins PATH | PASS |
> | 2 | `gh api installation/repositories --jq .total_count` → enrolled count | PASS |
> | 3 | `git config --show-scope credential.helper` → bot helper, `command` scope | PASS |
> | 4 | `GIT_SSH_COMMAND=/usr/bin/false git ls-remote origin` → succeeds via HTTPS rewrite | PASS |
> | 5 | Real `codex exec` empty commit → bot author/committer, unsigned (`%G?`=`N`) | PASS |
> | 6 | `as-me git commit` | **FAIL — documented `as-me` limitation** (sandbox denies `.git` writes for any wrapped/non-bare `git`), not a setup error |
> | 7 | Negative boundary: private non-enrolled repo `git ls-remote` → fails | PASS |
> | 8 | Fail-closed: non-executable `bot-token` → loud `401`, never personal fallback | PASS |
> | 9 | Untrusted fresh clone → identical behavior (no trust gate) | recorded, not a pass/fail gate |
> | 10 | Bypass smell: real `gh` by absolute path → personal account | recorded, not a pass/fail gate |
>
> Core-routing gate = checks 1–5, 7, 8 (all PASS); check 6 is the documented limitation; checks 9–10 are recorded behaviors.
> Full verification still requires a branch push, PR creation, check reading, and GitHub-side commit and PR actor verification.
>
> Audit-trail footnote: the gate was originally specified as checks 1–8.
> Check 6 (`as-me`) was subsequently scoped out of the gate — by user decision — on the grounds that it tests a feature outside the SKILL's Phase 4 routing contract, which requires per-repo activation, static identity env, command-scope `GIT_CONFIG_*`, a dynamic `GH_TOKEN`, and a fail-closed substitute, but not an authorship escape.
> Its result is recorded above unchanged as a documented limitation rather than deleted.
>
> Scope footnote: no branch was pushed and no `gh pr create` was run under Codex.
> Check 4 exercised the authenticated HTTPS-rewrite path via `git ls-remote` and check 5 made a local commit; the write path to GitHub rides the same token and helper but was not exercised end-to-end.

### Codex scenario results

> [!NOTE]
> **Audit-smell promotion after the first C2 with-skill run (2026-07-07).** The first C2 with-skill run missed assertions 3 and 4 on facts that existed in `references/adapters/codex.md` but only outside the audit-smells list (the gh-auth section's "a shell alias … goes around the shim" sentence; Check 8's fail-closed sentinel).
> Following the badge-caveat-relocation precedent (Scenario 1 history), the two facts were promoted into the adapter doc's audit-smells list as explicit smells — a `gh` alias or absolute-path invocation anywhere as a shim bypass, and silent mint failure / a "minting just works" runbook without the sandbox prerequisites — relocation of already-verified claims, no new claims.
> C2's treatment was then re-run once against the edited doc — see the re-run row.

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-07-07 | C1 (Codex set up) | baseline | 3/7 | Strong on the given facts: profile-based `[shell_environment_policy].set` wiring with identity vars, helper reset, gpgsign false, no Claude mechanisms; per-invocation mint shim, full literal replacement PATH, no static `GH_TOKEN`, fail-closed met in substance — refuses to exec the real `gh` on mint failure with the personal-fallback danger named, via exit-nonzero rather than the sentinel (2); no dotfile mechanism proposed (3, by construction). Missed: `insteadOf` host-wide (`url.https://github.com/.insteadOf`) instead of org-scoped, and the inline TOML `set` table written multi-line with comments — invalid TOML (1); `writable_roots` = repo dirs only, token cache and `uv` cache absent so the mint's own writes would be denied (4); invocation called "always explicit" but the forgotten-`--profile` → silently-personal fail direction never named (5); routing-by-convention and installation-list boundary stated but the absolute-path `gh` bypass never named (6); as-me inverted — claims a `git -c user.name=…` in-Codex identity override still works (unverified, contradicting the recorded wrapper denials) instead of sending collaborated authorship outside Codex (7). Confirms the scenario discriminates on the verification-derived facts. |
| 2026-07-07 | C1 (Codex set up) | with-skill | 7/7 | All assertions satisfied. The verified profile block reproduced verbatim (org-scoped `insteadOf` with the case-sensitivity and `ssh://` second-pair notes, helper reset semantics, git-expands-`$HOME`-not-Codex) (1); shim with per-invocation mint, the `BOT-TOKEN-MINT-FAILED` non-empty sentinel → loud `401`, the empty-token-falls-back-to-personal rationale, and the complete literal replacement PATH shim-first (2); no dotfiles touched, shell alias named as a shim bypass (3); full sandbox profile with the cold-mint DNS rationale, `uv` + token cache writable roots, and the `sandbox_mode` force-apply quirk with both invocation forms (4); dedicated fail-direction paragraph — forgetting `--profile bot` silently runs personal, weaker than a per-repo file, mandatory every invocation (5); routing-not-containment with the absolute-path `gh` bypass (Check 10), approval laundering, and the installation list named as a hard boundary (6 — this row predates the assertion's widening to also name server-side rulesets, and was not re-scored against the new wording); `as-me` denial with the shape-based root cause, the literal `--author` partial measure (committer stays bot), collaborated authorship outside Codex, and the amend-from-personal-terminal recovery (7). Bonus: `codex exec`-for-git-mutations vs `codex sandbox`-for-everything-else split surfaced correctly. |
| 2026-07-07 | C2 (Codex audit) | baseline | 2/4 | Caught the two config-literal flaws: static `GH_TOKEN` as Critical with hourly expiry and per-invocation-mint remediation (1); literal `$PATH` as Critical with the exact replace-no-expansion diagnosis and the full-literal-absolute fix (2). Missed: the `~/.zshrc` alias called "harmless… no action required" — the absolute-path bypass of the Codex-controlled routing surface never flagged (3); the network gap caught with the DNS/mint connection and the `network_access = true` fix, but no fail-closed sentinel expectation and no token/`uv` cache writable-roots coverage (4). Bonus insights: the two Critical bugs mask each other operationally; env-inheritance mode flagged as unverified. |
| 2026-07-07 | C2 (Codex audit) | with-skill | 2/4 | Passed: stale `GH_TOKEN` as High citing the adapter's audit smell by name, `GH_TOKEN` only from the shim (1); literal `$PATH` as Critical with the execvp-breakage probe evidence and the full-literal fix (2). **Misses (findings against the skill, not the agent):** the alias classified Low/"no action needed" — mechanically defensible (aliases don't fire under `execvp`), but the adapter's "a shell alias … goes around the shim and reaches the personal credentials" sentence never surfaced (3); DNS/cold-mint diagnosis, `network_access = true`, token + `uv` cache writable roots, and the `sandbox_mode` force-apply quirk all caught, yet the fail-closed non-empty sentinel expectation on mint failure never appeared (4). Both missed facts live in `references/adapters/codex.md` (gh-auth section; Check 8) — recorded as retrieval misses to watch; if they recur, promote the sentinel and alias-bypass lines within the adapter doc. Unprompted bonus: made the captured notes' missing `GIT_CONFIG_*` block (helper reset, bot helper, org `insteadOf`, gpgsign false) its top Critical — pushes would ride personal credentials while commits display bot metadata. |
| 2026-07-07 | C2 (Codex audit) | with-skill (re-run, after audit-smell promotion) | 4/4 | All assertions satisfied against the edited adapter doc. Stale `GH_TOKEN` as Critical citing the audit smell verbatim, dead week-old token, shim-only sourcing (1); literal `$PATH` as Critical with the exact "cannot write `$SHIMS:$PATH`" pattern, the execvp probe, and the full-literal fix (2); the alias now flagged (Medium) citing the promoted smell verbatim — "flag the alias even in an interactive-only file… defeats the shim wherever it fires" — with the Check-10 personal-credentials consequence, while keeping the correct mechanical nuance that zsh aliases don't fire in Codex sessions (3); network gap as High with the DNS/cold-mint diagnosis and "no working path to a valid token today", `network_access = true`, `uv` + token cache writable roots as absolute paths, and the fail-closed mint-failure expectation surfaced — non-executable `bot-token` → clean 401, never silent personal fallback (met in substance for the sentinel clause) (4). Carried over the bonus missing-`GIT_CONFIG_*` Critical, the runbook's backwards security framing, and the `sandbox_mode` force-apply confirmation ask. Confirms the promotion fixed both retrieval misses. |
