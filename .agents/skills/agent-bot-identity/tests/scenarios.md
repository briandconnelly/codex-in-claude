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

### Scenario 5 — broken Variant B guard setup (added 2026-06-16)

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-06-16 | 5 (broken guard) | not yet run | n/a | Added to cover issue #22: a hook-time executable preflight can fail during non-blocking `SessionStart` before installing the per-command guard, leaving later Bash commands able to run personal. |

### Hook-based mechanism (current skill) — re-validation 2026-06-10

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-06-10 | 1 (set up) | baseline | 5/10 | Unusually strong (did web research): GitHub App + webhook-off + select-repos (1); bot noreply email with API-fetched UID (3); git isolation with no global/dotfile changes, via repo-local `.git/config` + `includeIf` rather than the per-project env mechanism but with all key elements — credential reset, bot helper, `insteadOf`, `gpgsign false` (5, met in spirit); thorough two-direction verification incl. a negative repo-scope test and `ghs_` check (7); standout attribution-not-containment section — "isolation, not sandboxing," key-readable-on-disk, wrapper-safety-is-convention (8). **Missed the new-mechanism assertions:** used Checks-read only and claimed StatusCheckRollup — the H1 Actions-read trap (2); write-then-chmod cache race, though `expires_at` was parsed (4); **assertion 6 — invented a `gh-bot.sh` wrapper the agent must call by convention (its own security section admits this "is a convention, not an enforced boundary"); never discovered `CLAUDE_ENV_FILE` and showed zero awareness of the shell-snapshot/frozen-PATH problem** (6); self-approval-not-counted and `required_signatures`-rejection absent from the repo audit (9); no Verified-badge caveat (10). Confirms the scenario discriminates on the hook mechanism. |
| 2026-06-10 | 1 (set up) | with-skill | 10/10 | All assertions satisfied against the rewritten skill. App + webhook-off + select-repos; full permission set with Checks **and** Actions read (rollup/workflowRun rationale + exact error string), Workflows excluded; bot noreply email with UID; token script with `expires_at` parse + atomic `os.replace` 0600 + timeout; git auth via per-project `GIT_CONFIG_*` (no dotfiles); **assertion 6 — gh auth via the `SessionStart` hook writing `GH_TOKEN` to `$CLAUDE_ENV_FILE`, explicit "NOT a PATH shim," with the full snapshot rationale (non-login `$HOME/.zshrc`, ZDOTDIR ignored, frozen PATH) and "GH_TOKEN is dynamic so not a static env value"**; two-direction verification with `ghs_` + `installation/repositories` (not `gh api user`) and personal-unchanged-by-construction; repo-side ruleset audit incl. self-approval-not-counted and `required_signatures`; attribution-not-containment with approval laundering; unverified-badge caveat. |
