# Audit Workflow

Use this workflow to assess an existing repository against [config-checklist.md](config-checklist.md).
Findings must be grounded in evidence — a settings value, a file path and line, an API field, or a log excerpt — not in impressions.
Where evidence is unavailable (e.g., you lack admin access to view ruleset bypass lists, or a feature is not applicable to the plan tier), mark the item `not-checked` and record the reason rather than guessing.

**Safety:** Do not change live repository settings, rotate credentials, or trigger paid or destructive operations while auditing.
All inspection is read-only: `gh api`, `gh ruleset view`, reading files in the repository, and reviewing Actions run logs.
Do not run workflows, approve PRs, or write to the repository in any form during the audit.

## Severity Scale

- **Critical** — an active path to compromise or to an unreviewed merge reaching a protected branch.
  Examples: an automation identity (the agent, a bot PAT, a deploy key, or a CI app the agent uses) present in the bypass-actors list of a protected-branch ruleset (§2, T4); a `pull_request_target` workflow that checks out and executes untrusted PR code with repository secrets available (§3, T2); an agent identity listed in `CODEOWNERS` for protected paths or whose approvals count toward the required-review threshold, allowing it to approve or auto-merge its own PRs (§1, §2, T3); a classic broad PAT with `repo`-wide write scope used by the agent (§4, T9).
  Blocks confidence; fix before agents operate.

- **High** — a guardrail is missing or easily defeated.
  Examples: `dismiss_stale_reviews` not enabled, so a post-approval commit avoids re-review (§2, T3); third-party actions pinned to mutable tags or left unpinned (§3, T6); no dependency review or committed lockfile on a repository where agents add dependencies (§3, T7); no required reviews configured on protected branches in any profile that expects them — every multi-human profile, and the solo profile once a distinct agent identity exists (§2, T3) — but `required_approving_review_count: 0` is the EXPECTED posture ONLY in the solo interim (a single maintainer with no distinct agent identity yet) and is not by itself a High "missing required reviews" finding there (see the illusory review gate exception below); force-push or branch deletion not blocked on protected refs (§2, T4); `ACTIONS_STEP_DEBUG` or `ACTIONS_RUNNER_DEBUG` enabled where sensitive values flow (§3, T5) — registered secrets stay masked even in debug logs, but verbose output widens the leak surface for unregistered or derived values.

- **Medium** — weakens auditability or team productivity without an immediate compromise path.
  Examples: linear history is not required (§2, T8); the repo opted into signing but commits are unsigned (§2, T8); `CODEOWNERS` uses only a catch-all rule rather than explicit path prefixes (§1, T3); issue and PR templates are absent (§1, productivity); debug logging is enabled but no secret is currently exposed (§3, T5); the agent identity is a shared user account rather than a GitHub App or fine-grained PAT (§4, T8, T9).

- **Low** — hygiene items.
  Examples: `scope/<area>` labels missing in a monorepo (§1, productivity); `SECURITY.md` absent on a private repository (§1); `CONTRIBUTING.md` absent or not linked from `AGENTS.md` (§1, productivity); audit-log coverage (the fixed 180-day org window, or Enterprise streaming for longer retention) not explicitly considered (§4, T8).

**Availability / operability** is a finding class orthogonal to the compromise-focused scale above: a control configured so strictly that the legitimate human maintainer cannot merge a compliant PR, ship a release, or apply an emergency fix.
Rate it **High** normally, or **Critical** when it blocks a security fix or production recovery.
Examples: a single-maintainer repo with required reviews and a bypass-actors list empty of any human, so the lone maintainer can never merge their own work (§2); `require_last_push_approval` or environment `prevent_self_review` enabled in a solo repo; `required_signatures` enforced where a committer has no signing path, rejecting all their pushes (§2, T8).
Remediation is profile-aware — add the human bypass actor or relax the opt-in control to match the repository profile, not "remove all bypass actors."
A non-empty bypass list is a finding only when it contains an automation identity the agent can act as; a human-maintainer bypass in the solo profile with reviews >= 1 is expected, not a defect (in the solo interim with reviews 0, and in small-team and org repos, the list is empty — a green PR merges without a bypass, or a second human reviewer unblocks merges).
Exception — the illusory review gate: if a solo repo has required reviews >= 1, the agent runs on the maintainer's own credentials (no distinct agent identity, §4), AND a human bypass actor is configured so merges are actually possible, then that bypass is agent-reachable and the review requirement is theater — the agent author and the bypassing/approving maintainer are one actor.
Flag this (Medium; T3): the review setting implies a protection that does not hold.
(If instead the bypass list is empty in that scenario, no one can merge — the lone human cannot self-approve and there is no second reviewer — so it is the availability/lockout finding above, not an illusory gate.)
Medium, not High, even though "no required reviews configured" is a High example above: in a solo repo human review is structurally unenforceable (there is no second human), so the illusory gate does not defeat a guardrail that could otherwise have bound — it advertises one that never could, a misleading-auditability harm, while the actor-independent gates (strict checks, linear history, blocked force-push/deletion) still bind.
Remediation is not to tighten the review rule but to provision a distinct agent identity, or, in the interim, drop required reviews to 0 and lean on the actor-independent gates per the solo interim posture in config-checklist.md.

## Audit Procedure

1. **Establish context.**
   Record whether the repository is public or private, monorepo or traditional, which branches are protected, and which agent identities operate here (GitHub Apps, fine-grained PATs, or shared user accounts).
   Note stated scope (what the agent is permitted to do) and negative scope (what it must not do).
   Record any checklist items that are not applicable to this repository type and mark them `not-checked` now, before walking the checklist.

2. **Walk config-checklist.md §1 → §4, top to bottom.**
   For each item, record exactly one of:
   - a **finding** (severity + the Tn it implicates + evidence + remediation),
   - **`OK`** with a one-line evidence pointer (setting value, `file:line`, or API field),
   - **`not-checked`** with a reason (e.g., "admin access not available," "no GitHub Actions configured," "private repo — requirement does not apply").
   A section may accumulate multiple findings; an item with both a finding and `OK` evidence on adjacent sub-items should note both.

3. **Run the focused probes below** to confirm the highest-risk items rather than trusting settings labels.
   The probes are read-only; run at least the Critical-risk ones and record the output or the reason they could not be run.

4. **Synthesize into the report.**
   Order findings Critical → Low, then by checklist section within each severity band.
   After findings, assemble the coverage table (one row per §1–§4).

## Probes

Read-only checks to confirm the highest-risk controls.
Run each with `gh api` or `gh ruleset view` unless otherwise stated.
Mark each probe with the checklist section and threat class it targets.

- **Bypass-actors list** — retrieve the default-branch ruleset and confirm `bypass_actors` contains no automation identity the agent can act as (its GitHub App / `Integration`, a bot PAT, a deploy key, or a role such as `Write` that the agent holds).
  A human-maintainer entry (an individual `User`, or `Maintain`/`Repository admin` the agent cannot hold) is expected in the solo profile once reviews are >= 1 and is not a finding; an empty list is expected in the solo interim (reviews 0) and in small-team and org/high-risk repos (a second human reviewer unblocks merges without a bypass).
  Flag any `bypass_mode: exempt` entry regardless of actor — exempt (added September 2025) skips the rules silently and writes no bypass audit entry.
  `gh api repos/{owner}/{repo}/rulesets` then `gh ruleset view <id>`.
  *(§2, T4)*

- **`pull_request_target`, `workflow_run`, and injection surface** — list all workflow files in `.github/workflows/` and grep for `pull_request_target` or `workflow_run` as a trigger; for any `pull_request_target` match, check whether the workflow checks out or executes PR head code (`actions/checkout` with `ref: ${{ github.event.pull_request.head.sha }}` or equivalent), whether any privileged job is protected by an environment with human approval, and whether any `run:` step interpolates `${{ github.event.* }}` directly rather than binding through `env:`; for any `workflow_run` match, check whether the workflow downloads artifacts or caches from the triggering run (attacker-controlled when triggered by a fork PR) or checks out untrusted head code in a job that holds secrets or write scope.
  *(§3, T2)*

- **Agent identity listed in CODEOWNERS or counted as approver** — read the active `CODEOWNERS` file (GitHub looks for it at the repo root, in `.github/`, or in `docs/` — check all three) and check whether the agent account or app appears as an owner on any protected path; then check branch-protection or ruleset settings to confirm self-approval is not counted toward the required-review threshold.
  *(§1, §2, T3)*

- **Action pin format** — for each `uses:` line in all workflow files, confirm the pin is a full 40-character commit SHA, not a mutable tag (`@v3`, `@main`, `@latest`) and not absent.
  *(§3, T6)*

- **`dismiss_stale_reviews`** — in the branch-protection or ruleset configuration for the default branch, confirm `dismiss_stale_reviews` is `true`.
  Legacy branch protection: `gh api repos/{owner}/{repo}/branches/{branch}/protection`.
  Rulesets express the same control as `dismiss_stale_reviews_on_push`; if the repo uses rulesets, check via `gh ruleset view <id>` or the rulesets API instead — audit whichever path the repo actually uses.
  *(§2, T3)*

- **Debug logging flags** — check repository and environment variables for `ACTIONS_STEP_DEBUG` and `ACTIONS_RUNNER_DEBUG`; confirm neither is set to `true` in any environment used by the agent.
  `gh api repos/{owner}/{repo}/actions/variables` and per-environment equivalents.
  *(§3, T5)*

- **`.gitignore` coverage and committed secrets** — confirm a `.gitignore` exists and covers secret-bearing paths (`.env*`, `*.pem`, `*.key`, credentials); then grep the tracked tree for already-committed secret files: `git ls-files | grep -iE '(^|/)\.env($|\.)|\.pem$|\.key$|credentials'` — any match is a finding regardless of what `.gitignore` contains, because `.gitignore` does not protect already-tracked files.
  *(§1, T5)*

- **Classic PAT scope** — if the agent uses a PAT, verify it is fine-grained, not classic.
  A classic broad PAT used by the agent is a Critical finding.
  You generally cannot enumerate classic PATs via the GitHub API — the org fine-grained-PAT endpoints (`/orgs/{org}/personal-access-tokens`, `/orgs/{org}/personal-access-token-requests`) list fine-grained PATs only and have no `token_type`/`classic` field.
  Detect the risk via org policy instead: check whether the org restricts or forbids classic PAT access (Settings → Personal access tokens); review the fine-grained PAT inventory and approval policy via `gh api orgs/{org}/personal-access-tokens` (illustrative; lists fine-grained tokens only); and review the audit log for PAT-authenticated writes where available.
  If none of these are accessible, mark the probe `not-checked` with that reason rather than asserting a clean result.
  *(§4, T9)*

## Finding Format

Each finding has five labeled lines:

- **Severity:** Critical | High | Medium | Low
- **Section:** `§N` (list multiple if a finding spans sections)
- **Threat:** `Tn`
- **Evidence:** a concrete setting value, file path and line number, or log excerpt — never "it feels wrong."
- **Remediation:** the exact mechanism to change (ruleset rule name, API field, file path), referencing config-checklist.md.

**Worked example:**

- **Severity:** Critical
- **Section:** §2
- **Threat:** T4
- **Evidence:** `gh ruleset view 42` returns `"bypass_actors": [{"actor_id": 99, "actor_type": "Integration", "bypass_mode": "always"}, {"actor_id": 4, "actor_type": "RepositoryRole", "bypass_mode": "always"}]` — the agent's GitHub App and the `Maintain` role (which the agent account also holds) can both push directly to `main` without review or status checks.
- **Remediation:** Remove every automation identity from `bypass_actors` — the agent App and any role the agent can hold (config-checklist.md §2: "no automation identity ... may appear in the bypass-actors list"). In the solo profile with reviews >= 1 a human-maintainer `User` entry with `bypass_mode: pull_request` may remain as the documented escape hatch; in the solo interim (reviews 0) and in small-team and org/high-risk repos the list should be empty.
  Navigate to Settings → Rules → Rulesets → select the ruleset → Bypass list, remove the offending entries, then save.

## Coverage Table

One row per checklist section §1–§4.
Each row records `finding(s)` with finding references, `OK` with a brief evidence pointer, or `not-checked` with a reason.
The table below shows an illustrative example of a completed audit; replace values with actual findings.

| Section | Status | Notes |
| --- | --- | --- |
| §1 Issues & PRs | finding F1; OK on remaining items | F1 (Medium): CODEOWNERS catch-all only — no explicit path prefixes; templates present at `.github/ISSUE_TEMPLATE/`; `CONTRIBUTING.md` present; `SECURITY.md` absent (private repo — Low) |
| §2 Branch / Repo Guardrails | finding F2, F3 | F2 (Critical): agent GitHub App present in bypass-actors list on `main` ruleset — `gh ruleset view 42`; F3 (High): `dismiss_stale_reviews` is `false` — `gh api .../branches/main/protection` returns `"dismiss_stale_reviews": false` |
| §3 Actions & Supply Chain | finding F4; OK on remaining items | F4 (High): three actions pinned to mutable tags (`actions/checkout@v4`, `actions/setup-node@v4`, `github/codeql-action@v3`) — `.github/workflows/ci.yml:12,18,34`; OIDC configured; secret scanning enabled |
| §4 Auditability & Identity | OK | Agent uses GitHub App (App ID 1234); fine-grained tokens only; linear history required; audit-log coverage considered (org log's fixed 180-day window documented; Enterprise streaming not in use) |

## Done Criteria

- Every §1–§4 item in config-checklist.md is accounted for in the coverage table: covered by at least one finding (with severity and a `Tn` threat reference), marked `OK` with brief evidence, or marked `not-checked` with an explicit reason.
- Each finding carries all five labeled lines (Severity, Section, Threat, Evidence, Remediation).
- At least the Critical-risk probes (bypass-actors list, `pull_request_target` injection surface, agent listed in `CODEOWNERS`, classic PAT scope) were run and their output or a reason they could not be run is recorded.
- When no Critical or High findings are present, the report says so explicitly and names residual risks (e.g., "bypass-actors probe could not be run — admin access unavailable" or "no live Actions run logs were inspected for secret exposure").

This mirrors the Audit Done Criteria stated in SKILL.md.
