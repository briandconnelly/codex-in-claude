---
description: Use when giving a local coding agent a distinct GitHub App bot identity — its commits, pushes, and PRs attribute to the bot while manual git operations on the same machine keep the personal account untouched — when splitting attribution in a repo where the human and agent both contribute, or when auditing such a dual-identity setup for over-trust. The App/token/credential-helper core is harness-neutral; adapters — Claude Code (tested), Codex CLI (Variant A partially verified, GitHub write path not exercised; Variant B pending).
metadata:
    github-path: agent-bot-identity
    github-ref: refs/heads/main
    github-repo: https://github.com/briandconnelly/skills
    github-tree-sha: 08552e9bb83c7d29b0854009c995030005a059cf
name: agent-bot-identity
---
# Agent Bot Identity

Core is harness-neutral; adapters ship for Claude Code (tested) and Codex CLI — Variant A partially verified with its GitHub write path not exercised and a documented `as-me` limitation, Variant B pending — see Phase 4.

## Overview

Give a local coding agent its own GitHub App identity so the commits, pushes, and PRs it makes attribute to a bot by default, while the human's git setup (SSH key, GPG signing, keychain credentials) stays untouched on the same machine.
Attribution is per unit of work: autonomous agent work carries the bot identity, and in repos where the human also contributes through the agent, collaborated commits can carry the human's authorship via the `as-me` wrapper (Phase 3) while auth still rides the bot token.
The isolation mechanism is local routing that injects the bot's credentials only into the agent's sessions where the bot belongs — either per-project opt-in or automatic org-gated routing, depending on the harness adapter (Phase 4) — without editing any shell dotfiles.

Core principle: **this buys attribution, not containment.**
The per-project scoping makes the well-behaved default path use the bot identity; the only hard boundaries are the App's installation list — which bounds git/content access and all access to private repos, not everything (see What This Enforces) — and the server-side rulesets of the repos it touches.
Never present this setup as a sandbox.

## When to Use

- Setting up Claude Code (or a similar local agent) to commit, push, and open PRs as a bot on a developer machine, with personal git operations unchanged.
- Splitting attribution in a repo where you contribute both directly and via the agent — collaborated work authored as you, autonomous work as the bot, without separate checkouts or settings toggles.
- Auditing an existing dual-identity setup for over-trust — e.g. a review gate assumed to bind the agent, or local scoping treated as a security boundary.
- Symptoms: agent commits show the human as author; "have the agent open PRs as a bot"; bot PRs need human approval but the agent runs on the human's laptop.

## When Not to Use

- Repo-side configuration — rulesets, CODEOWNERS, required checks, Actions hardening — is the agent-friendly-github skill; this skill is the local-machine implementation of its §4 identity step, and Phase 6 here hands back to that skill's checklist.
- CI or hosted runners: mint the App token directly in the workflow (e.g. `actions/create-github-app-token`); there is no personal identity to isolate from.
- Hard security isolation: run the agent as a separate OS user, container, or VM; this skill does not provide that.

## Design at a Glance

| Layer | Mechanism | What it buys |
| --- | --- | --- |
| Identity | Org-owned GitHub App, webhook disabled | True `[bot]` attribution, fine-grained scopes, short-lived tokens, audit trail |
| Blast radius | App installed on "Only select repositories" | Token cannot touch non-enrolled **private** repos or push to any non-enrolled repo, even if config leaks; public-repo surfaces open to any actor (e.g. filing issues) stay open |
| Local routing | harness adapter — see Phase 4 | Bot identity activates only where the adapter routes it — per-project opt-in or org-gated automatic; no shell dotfiles change |
| git auth | `insteadOf` SSH→HTTPS rewrite + git credential helper (`GIT_CONFIG_*`) | Pushes use the installation token, not the personal SSH key or keychain |
| gh auth | harness adapter — see Phase 4 | `gh` calls use the installation token, minted fresh per session/command rather than the personal login |
| Collaborated work | `as-me` wrapper unsets the author/committer env per command | Human authorship on collaborated commits; pushes and PRs still ride the bot token |
| Enforcement | Repo rulesets (Phase 6) | The only controls that bind a misbehaving agent |

## Phase 1 — Register the GitHub App

1. Register at the org level: Settings → Developer settings → GitHub Apps → New GitHub App.
   This needs org owner or "GitHub App manager" role; otherwise register under the personal account and have an org admin approve installation.
2. Name it for the agent (e.g. `acme-agent`); the actor becomes `acme-agent[bot]`.
   Homepage URL can be anything.
3. **Uncheck "Active" under Webhook** — this is a pure identity App; it receives no events.
4. Repository permissions (exact grants, nothing else):
   - Contents: **Read and write** (fetch, push branches)
   - Issues: **Read and write** (file and update issues, cross-link PRs)
   - Pull requests: **Read and write** (open, update, comment)
   - Checks: **Read-only** — required for the agent to read check-run status on its own PRs
   - Actions: **Read-only** — also required when CI runs on GitHub Actions: `gh pr checks` resolves the status rollup through each check suite's workflow run, and an App token lacking `actions: read` fails with "Resource not accessible by integration"
   - Metadata: Read-only (automatic)
   - Optional: Commit statuses Read-only (legacy status contexts)
   - **Never Workflows: Read and write** — a prompt-injected agent could rewrite CI; the absence is also a server-side control, because pushes touching `.github/workflows/` get rejected
5. Note the **App ID** from the settings page (GitHub also issues a client ID and now recommends it as the JWT `iss`; the App ID continues to work).
6. Generate a private key; store at `~/.config/acme-agent/key.pem`, `chmod 600`.
   Hardening option: store the PEM in the login keychain (`security add-generic-password`, base64-wrapped).
   This only works if you also change `bot-token` to read the key from the keychain instead of `KEY.read_text()` — deleting `key.pem` while the script still reads from disk breaks token minting.
   Either rewrite the read or keep the file; don't delete it on the strength of the keychain copy alone.

## Phase 2 — Install the App

1. App settings → Install App → choose the org.
   Installation needs an org owner — the App manager role can register Apps but not install them.
   An org member who is admin of the target repos can install an App that requests no organization permissions (this App requests none); otherwise it files an installation request for an owner to approve.
2. Choose **"Only select repositories"** and pick the target repos.
   This list is the real blast-radius limit; enrolling a repo in the program means adding it here.
3. Note the **Installation ID** from the post-install URL (`.../settings/installations/<id>`), via `gh api orgs/{org}/installations` with an org-admin user token, or via app JWT (`gh api /app/installations` — a normal user token will not work on `/app/*` endpoints).
4. Get the bot's user ID for commit attribution: `gh api 'users/acme-agent%5Bbot%5D' --jq .id`.
   The bot's commit email is `<BOT_UID>+acme-agent[bot]@users.noreply.github.com`; using it makes commits render with the bot's avatar.

## Phase 3 — Helper scripts

The helper scripts are bundled under `scripts/`; copy the needed files into a single flat directory, customize their placeholders, and `chmod +x` each copied file.
`~/.config/acme-agent/bin/` is the recommended neutral location.
Install everything flat in that one directory — including each harness adapter's glue scripts, which live under `scripts/claude/` (and `scripts/codex/`) in this repo but sit next to the shared scripts once installed.
Install only the glue for the adapter(s) you actually use; an unused adapter's scripts are extra attack surface with no benefit.
**Never put the install directory on your personal `PATH`.**
It contains a `gh` shim (the Codex adapter's) that would route your own terminal through the bot token, and the whole design rests on your personal shells never resolving it.
The scripts self-locate, so existing `~/.claude/bot-shims/` installs keep working unchanged; for a new install prefer the neutral directory, and adjust every path in the settings examples consistently.
Keep the install path free of spaces and shell metacharacters: `bot-env` emits a `!`-prefixed credential helper that git re-parses through `sh -c`, and both it and the Claude glue refuse to install from an unsafe path rather than emit a line that breaks or executes.

Use these resources:

- `scripts/bot-token` mints and caches installation tokens.
- `scripts/git-credential-bot` feeds installation tokens to git and answers only `https://github.com`, accepting case-insensitive hostnames and an optional port suffix from git's credential protocol.
- `scripts/as-me` provides personal authorship for collaborated commits.

Each harness adapter (Phase 4) adds its own glue scripts on top of these; see the adapter doc.

Customize `bot-token` with the App ID, installation ID, key path, and cache path.
Its cache defaults to `~/.cache/acme-agent/token.json`, deliberately outside the key-and-scripts directory: a sandboxed harness can then grant write access to the cache without also granting it to `key.pem` and the fail-closed scripts (see the Codex adapter's sandbox profile).
It parses `expires_at`, writes the token cache through a `0600` temp file swapped in with `os.replace()`, and sets a request timeout so a hung mint does not hang git or `gh` indefinitely.
It refuses to print an empty token — from the cache or from the API — because an empty `GH_TOKEN` is the fail-open case every caller here guards against.
The `uv run` shebang requires `uv` on PATH where the script is invoked; use an absolute path to `uv` if that is not guaranteed.
On a cache hit this runs in well under 100 ms, cheap enough to call before every Bash command.
There is no lock around the mint: concurrent cold-cache invocations may each mint a token — duplicate API work, not a correctness problem, since both tokens are valid and the last cache write wins.
Optional hardening: pass a `"repositories"` field in the token request to scope each token to the repo being worked, at the cost of the shared cache.

Keep `git-credential-bot` host-gated.
For an eligible `https://github.com` request, a crashed or empty mint must return the complete invalid credential `username=x-access-token` and `password=BOT-TOKEN-MINT-FAILED` so Git cannot fall through to askpass, terminal prompting, or a personal credential source.
For a wrong-host request it must stay silent so a typosquatted or mis-rewritten remote cannot coax out the installation token.
Normalize hostnames case-insensitively and strip any credential-protocol port suffix before comparing to `github.com`.
This matters most under an automatic-activation adapter that installs the helper in any org-matching repo (e.g. Claude Code's Variant B — see the adapter doc).
Do not globally override `GIT_ASKPASS`, `SSH_ASKPASS`, or terminal-prompt variables in Variant B because a personal verdict cannot safely reconstruct IDE-provided values that the guard inherited.

Use `as-me` only for commit authorship, always with a command — the script refuses zero arguments, because bare `env` would print the entire environment, `GH_TOKEN` included.
Unsetting the four identity variables lets git fall back to the global `user.*` config while pushes, `gh` calls, and PRs still ride the bot token.
These commits are unsigned because `gpgsign false` stays in effect, and they show no Verified badge once pushed.
Forgot the wrapper and committed as the bot? `as-me git commit --amend --reset-author` fixes the last commit.
When to use it is a policy question, not a mechanism question; see Mixed Contribution below.

## Phase 4 — Local routing: the adapter contract

Phases 1–3 are harness-neutral; local routing is the only harness-specific layer.
An adapter for a local agent harness must supply all of the following, without editing shell dotfiles:

- Per-repo activation: explicit opt-in, or a gated automatic equivalent keyed on a repo-intrinsic signal such as the org remote.
- The static git identity env (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`).
- Command-scope `GIT_CONFIG_*`: credential-helper reset plus the bot helper, org-scoped `insteadOf`, `commit.gpgsign false`.
- A dynamic `GH_TOKEN` re-minted across hour-plus sessions.
- A fail-closed substitute when minting fails: a non-empty invalid token, never an empty value.

A harness missing one of these capabilities is pending, not approximated — a half-wired adapter fails open to the personal identity.
Do not port an adapter mechanically: Claude Code's per-command env-file evaluation has no assumed equivalent elsewhere.

Implemented adapters:

- [Claude Code](references/adapters/claude-code.md) — Variant A (per-project opt-in) and Variant B (user-level automatic, per-command re-decision). Tested.
- [Codex CLI](references/adapters/codex.md) — Variant A partially verified, with the GitHub write path not exercised and a documented `as-me` limitation; Variant B pending. Core routing probes were run against Codex 0.143.0, with activation behavior re-probed on 0.144.1.

| Capability | Claude Code | Codex CLI |
| --- | --- | --- |
| Static identity env | ✅ settings/env or guard | ✅ `shell_environment_policy.set` via named profile |
| Dynamic `GH_TOKEN` | ✅ SessionStart hook, per command | ✅ PATH-shimmed `gh`, minted per invocation |
| Per-command redecision | ✅ Variant B guard | ❌ pending |
| Fail-closed routing | ✅ guard aborts / sentinel token | ✅ sentinel token (routing only) |
| Automatic user-level routing | ✅ Variant B | ❌ pending |
| `as-me` authorship escape | ✅ | ❌ (sandbox denies non-literal-git `.git` writes) |
| Verification status | Scenarios 1–5 tested | Partially verified; GitHub write path not exercised; scenarios C1–C2 tested |

("Fail-closed" is scoped to routing, never containment.)

## Phase 5 — Verify both directions

In a fresh agent session in an opted-in repo:

- Run your adapter's activation checks first — see the adapter doc.
- Do not begin git or `gh` work until your adapter's token check and the command-scope credential-helper check pass — see the adapter doc for which token check applies.
- If the adapter injects `GH_TOKEN` into the session env (Claude Code): `echo "${GH_TOKEN:0:4}"` → `ghs_`, proving the adapter injected the installation token.
  This check does not port: under a per-invocation shim adapter (Codex CLI) a session-level `GH_TOKEN` is an audit *smell*, not a pass — the shim exports it per invocation.
- `gh api installation/repositories --jq '.total_count'` → the count of enrolled repos, proving `gh` acts as the bot. Use this, not `gh api user` — an installation token has no user and 403s on `/user`.
  This is the harness-neutral token check both adapters share; prefer it when writing adapter-agnostic runbooks.
- `git config --show-scope credential.helper` → bot helper at `command` scope (proves env-scoped, no file changed).
- `GIT_SSH_COMMAND=/usr/bin/false git ls-remote origin` → succeeds, proving the HTTPS-rewrite-plus-token path is in use (SSH is disabled for that invocation).
- Test commit → author `acme-agent[bot]`, unsigned (`git log -1 --format='%an <%ae> %G?'`).
  Once pushed, the commit shows no Verified badge — expected, because local commits pushed with an App token are never auto-verified; only commits created through the App's API path (e.g. GraphQL `createCommitOnBranch`) get the badge.
- If the adapter supports `as-me` (see the Phase 4 support matrix) — collaborated path: `~/.config/acme-agent/bin/as-me git commit --allow-empty -m "as-me test"` → author is you, unsigned (`git log -1 --format='%an <%ae> %G?'`), while `echo "${GH_TOKEN:0:4}"` still prints `ghs_`.
  Once pushed, this commit also shows no Verified badge — App-token pushes are never auto-verified, same as bot commits.
- Branch push + `gh pr create` → PR and commits authored by the bot on GitHub.
- `gh pr checks` → returns status (proves Checks and Actions read).
- Negative: `git ls-remote https://github.com/acme/<private-non-enrolled-repo>.git` → fails, proving the installation boundary.
  The probe repo must be private — public repos are readable over unauthenticated HTTPS, so a success there proves nothing.

Run your adapter's own routing checks in addition to these — for an automatic adapter, that includes the gate's fail direction (ambiguity resolves to bot, broken guard aborts, mid-session flips). See the adapter doc.

In a personal terminal (and any agent session the routing leaves personal — see the adapter doc for which sessions those are):

- `git config credential.helper` → still osxkeychain; `git config commit.gpgsign` → still true.
- `gh auth status` → personal account; commits signed and authored as the human; SSH push works.
- Because no shell dotfile was modified, personal shells are unaffected by construction — there is nothing project-specific on `PATH` or in the rc files to leak.
  This holds only while the install directory stays off your personal `PATH` (Phase 3); adding it there puts the Codex adapter's `gh` shim in front of the real `gh` in your own terminal.

## Mixed Contribution — Collaborated Work as You

In repos where the human contributes both directly and through the agent, attribution is per unit of work, not per repo or per process: work the human collaborated on is authored as the human via `as-me` (Phase 3); work the agent did alone stays the bot's.

- **The bot stays the session default.**
  Do not invert to personal-credentials-by-default with a bot-credentialed subagent for autonomous work: forgetting that switch attributes autonomous agent work to the human — this skill's headline failure mode — whereas forgetting `as-me` merely attributes your work to the bot, recoverable with `as-me git commit --amend --reset-author`.
  Subagents also inherit the session environment, so a subagent cannot cleanly carry different credentials; per-command wrapping is the granularity that actually exists, and it is also the only granularity that covers mixing within a single interactive session.
- **Explicit direction only.**
  The agent uses `as-me` only when the user explicitly marks a piece of work as collaborative; it never self-decides.
  Subagents, headless runs, and scheduled agents never receive that direction, so their work is bot-attributed by construction.
- **Authorship only.**
  Pushes, `gh` calls, and PRs always ride the bot token; never bring personal credentials (SSH key, personal `gh` token) into agent sessions to make PRs "fully yours" — that reintroduces the approval-laundering surface the mitigations below remove.
- Optional convention: when the agent did substantial work on a collaborated commit, add a `Co-authored-by: acme-agent[bot] <<BOT_UID>+acme-agent[bot]@users.noreply.github.com>` trailer.

## Phase 6 — Audit repo-side guardrails

Identity is Step 1 of the agent-friendly-github setup workflow; enforcement lives in each repo's ruleset, not in the App.
For every repo the App is installed on, walk that skill's checklist §2; the items this setup specifically depends on:

- Required approving reviews ≥ 1 with self-approval not counted.
- `dismiss_stale_reviews_on_push` enabled.
- Force-push and branch deletion blocked; required status checks strict.
- Bypass-actors list contains no automation identity — including this App and any other bot App already installed (verify; never assume an existing bot's posture is clean).
- No `required_signatures` rule, or a dedicated bot signing key is provisioned first — `gpgsign false` plus an App-token push means every bot commit is unsigned, and a `required_signatures` ruleset rejects the push outright.

Run the audit from a personal terminal, never through the bot token — not because the bot token fails loudly, but because it does not fail at all.
An installation-token ruleset read succeeds with `bypass_actors` silently withheld — `--jq '.bypass_actors'` prints `null` instead of the request failing with 403 — so a bot-token audit reports "no bypass actors" while blind to exactly the item most likely to be non-clean.
A bot-token audit is therefore not merely incomplete; it is affirmatively misleading.
Positive control: before recording "no bypass actors on any ruleset", prove the reading identity could have seen one — a redacted view and a clean result are otherwise identical.
`gh api user` returning your personal login rules out the bot token (installation tokens 403 there) but is not sufficient: a personal account without admin access to the repo also gets `bypass_actors` omitted from the read, the same false-clean shape (verified 2026-07-12).
Confirm `gh api repos/OWNER/REPO --jq .permissions.admin` returns `true` for each repo audited.
Some checks (Actions settings, secret scanning) additionally need admin access.
File gaps with the repo's admins rather than working around them.

## What This Enforces — and What It Does Not

Enforced, server-side:

- The installation token carries only the granted scopes and expires in one hour; the installation list bounds git/content access and **all** access to private repos.
  It does not bound issue creation on public repos: any authenticated actor — an App installation token included — can open an issue on any public repo with Issues enabled, even one the App is not installed on, so a misdirected agent can post far outside the enrolled set, attributed to the operator's App.
  That is a reputational and spam surface rather than a code-integrity one, but treat the enrolled set as the bot's *git* reach, not its *write* reach.
- No Workflows permission means GitHub rejects bot pushes that add or modify files under `.github/workflows/`; CI logic living elsewhere — scripts the workflows invoke, composite actions, Makefiles — is still reachable with Contents write, which is part of why the human review gate matters.
- Supported GitHub-side events such as `pull_request.create` and `pull_request_review.submit` record actors and can support the approval-laundering detection pattern; local commits, most reads, and every local action are not automatically organization audit-log events.
- On GitHub Enterprise Cloud, [Git transport events](https://docs.github.com/en/enterprise-cloud@latest/admin/monitoring-activity-in-your-enterprise/reviewing-audit-logs-for-your-enterprise/audit-log-events-for-your-enterprise#git) (`git.clone`, `git.fetch`, and `git.push`) are available through the REST API, export, or audit-log streaming rather than the web interface, and GitHub retains them for [seven days](https://docs.github.com/en/enterprise-cloud@latest/organizations/keeping-your-organization-secure/managing-security-settings-for-your-organization/reviewing-the-audit-log-for-your-organization#using-the-audit-log-api).

Not enforced — the part everyone overstates:

- The agent runs as the human's OS user: it can read `key.pem`, run `bot-token` directly, call the real `gh` by full path, read `~/.ssh`, or edit the shims and `settings.local.json` themselves.
- It therefore holds **both identities**, which enables approval laundering: author a PR as the bot, approve it as the human from a non-opted-in session.
  GitHub's block on author self-approval does not close this.
- A required-review rule binds the **bot token**, not the agent; treat it as enforcement against the bot identity and a convention for the agent.
- Mitigations for approval laundering, strongest first: structural — strip Pull requests write from the personal `gh` credentials resident on the machine (fine-grained PAT, approve in the browser); server-side — agent-friendly-github §2's human-only-approvals check and `require_last_push_approval`; detection — audit-log alerts on the App's operator approving the App's PRs; procedural — never approve from inside an agent session.
  The worked list with rationale: [references/approval-laundering.md](references/approval-laundering.md).
- If containment is actually required, change the architecture (separate OS user, container, or VM), not the config.

## Common Mistakes

| Mistake | Reality |
| --- | --- |
| Letting a failed mint leave `GH_TOKEN` empty | `gh` treats empty as unset and silently falls back to the personal stored credentials; substitute a non-empty invalid token so the failure surfaces as an auth error |
| Probing identity with `gh api user` | Installation tokens have no user and 403 there; use `gh api installation/repositories` |
| Self-assigning issues to signal the bot is working them | A GitHub App bot actor is not a valid assignee, so `gh issue edit --add-assignee` with the bot as target fails — and `Issues: write` is already the max grant, so no wider permission exists; signal work-in-progress with a claim label (e.g. `agent:in-progress`) via `--add-label` instead |
| Granting Checks read without Actions read | Under an App token `gh pr checks` needs both — the status rollup traverses each check suite's workflow run |
| Granting Workflows: write "to be safe" | Hands a prompt-injected agent the ability to rewrite CI |
| Write token cache, then chmod | umask window exposes the token; create `0600` atomically |
| Assuming one-hour token life | Parse `expires_at` from the response |
| Leaving the personal GPG key signing bot commits | Attribution mismatch — human signature on bot-authored work; set `commit.gpgsign false` |
| Expecting the Verified badge on bot commits | Local commits pushed with an App token are not auto-verified; only API-path commits (e.g. GraphQL `createCommitOnBranch`) get the badge |
| Calling the review gate "enforced against the agent" | The agent holds both identities; the gate binds the bot token (see approval laundering above) |
| Leaving the personal `gh` OAuth login on the agent's machine | Its token carries PR write, so the agent can approve bot PRs as the human in one command; auth personal `gh` with a fine-grained PAT lacking Pull requests write and approve in the browser |
| A credential helper that answers for any host | git invokes it for every host it authenticates to, so a host-blind helper hands the installation token to a typosquatted, mis-rewritten, or attacker-controlled remote; read git's stdin request and answer only `https://github.com` |
| Deciding the org match by pattern-matching the raw remote line | Any boundary char you pick (`/`, `@`) also appears in URL *paths*, so `notgithub.com/acme/`, `example.com/@github.com/acme/`, or `github.com.evil.tld/acme/` can all spoof a bot verdict; parse each remote down to its authority (`[userinfo@]host[:port]`) and compare the host case-insensitively to `github.com`, then check the org path segment case-insensitively — don't regex the whole line |
| Gating user-level activation on a local repo allowlist | An enrolled repo missing from the list silently works as the human — the headline failure; gate on the org remote and let the installation boundary fail loudly for stragglers |
| Defaulting agent sessions to personal credentials, with a bot subagent for autonomous work | Fails open — a forgotten switch attributes agent work to the human, the headline failure mode; keep the bot as the default and escape per task with `as-me` |
| Letting the agent decide when to use `as-me` | Explicit user direction only; subagents and scheduled runs then stay bot-attributed by construction |
| Extending `as-me` to pushes and PRs with personal credentials | Reintroduces the approval-laundering surface; the escape is commit authorship only — auth stays the bot token |
| Expecting `as-me` commits to be signed or Verified | `gpgsign false` stays in effect and App-token pushes are never auto-verified; amend from a personal terminal if a signature is required |
| Auditing Phase 6 rulesets with the bot token | A bot-token ruleset read succeeds with `bypass_actors` withheld (`--jq` prints `null`) rather than failing with 403 — the audit reports "no bypass actors" and looks clean while blind; read rulesets with a personal identity holding repo admin and run the Phase 6 positive control |
| Assuming the installation list bounds everything the bot can write | It bounds git/content access and all private-repo access; issue creation on any public repo with Issues enabled is open to any authenticated actor, App tokens included |

Harness-mechanism-specific pitfalls (PATH-shim snapshots, `settings.local.json` static env, the per-command guard, `CwdChanged` plumbing) live with each adapter — see the adapter doc.

## Done Criteria

- Phase 5 verification passes in both directions, including the negative tests, minus any check the adapter's support matrix marks unavailable.
- Phase 6 audit recorded for every installed repo, with gaps filed rather than bypassed.
- Any document describing the setup states the attribution-not-containment boundary explicitly.
