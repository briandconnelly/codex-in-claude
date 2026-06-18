# Configuration Checklist

This is the mechanical standard walked by both the Set up and Audit workflows.
Each item names the concrete GitHub mechanism where one exists, and otherwise states the policy to enforce; see `threat-model.md` for the rationale behind each control.
Public/private and monorepo/traditional variations are noted inline where the requirement differs.
Walk the sections in order; they are numbered §1–§4 and cited by that number elsewhere in the skill.

## Plan & Visibility Caveats

Several controls are gated by repository visibility and GitHub plan.
Any control the repo's plan does not provide is marked N/A with the plan reason in an audit, and an external alternative is used where one exists.

| Control | Public repos | Private / internal repos |
| --- | --- | --- |
| Dependabot | All plans | All plans |
| Rulesets / branch protection, CODEOWNERS enforcement | All plans | GitHub Pro, Team, or Enterprise — not available on Free |
| Environment required reviewers & wait timers | All plans | GitHub Enterprise only (Pro/Team private repos get environments, secrets, and deployment-branch policies, not these protection rules) |
| Code scanning (CodeQL), dependency review | Free | GitHub Code Security (Team or Enterprise; one half of the April 2025 GitHub Advanced Security unbundling) |
| Secret scanning + push protection | Free | GitHub Secret Protection (Team or Enterprise; the other half of that unbundling) |

## Repository Profiles

Pick the profile that matches the repository, then walk §1–§4 applying the controls it calls for.
Every profile shares a non-negotiable baseline; higher-risk profiles add controls on top.
The baseline guarantees the property that must hold everywhere: an agent can never bypass the guardrails to reach a protected branch — it cannot push or force-push directly, cannot merge a PR that fails its required checks, and its own approval never counts toward a required review — and, equally, a control is never configured so the legitimate human maintainer is locked out of merging their own work.
Wherever a second human can review (the small-team and org profiles), reviews are >= 1 and the agent cannot self-merge: it needs an approval from a human who is not its author, which it cannot supply, so no agent change merges unreviewed.
This holds even if the agent temporarily runs on one maintainer's account, because a *different* human approves.
The sole exception is the solo profile before a distinct agent identity exists — the *interim posture* below is the canonical statement of that exception and its rationale; other sections and files cite it rather than restate it.
The security boundary is human-vs-agent, not author-vs-reviewer.

**Baseline — all profiles:**
- Agent identity is least-privilege and is NOT a bypass actor. (§2, §4; T4, T9)
- Protected-branch ruleset: a PR is required, self-approval not counted, and force-push and deletion blocked — so the agent can never push past the ruleset and its own approval never counts toward a review.
  Set `required_approving_review_count` to >= 1 in any profile that has a second human who can review (small-team, org) — and in the solo profile once a distinct agent identity exists — which bars self-merge because the agent then needs an approval from a human who is not its author.
  The sole exception is the solo profile before that identity exists, where a required review is **illusory**: set it to `0` and lean on the actor-independent gates, per the solo interim posture below. (§2; T3, T4)
- Least-privilege `GITHUB_TOKEN`; no privileged `pull_request_target`/`workflow_run` on untrusted code; third-party actions pinned to a full commit SHA. (§3; T2, T5, T6)
- `.gitignore` covers secret-bearing paths; secret scanning + push protection enabled where the plan provides it. (§1, §3; T5)
- No classic broad PATs. (§4; T9)

**solo** — one active human maintainer.
Once a distinct agent identity exists and reviews are set to >= 1, add a human bypass actor so the lone maintainer can merge their own work past the review requirement (see §2); in the pre-identity interim below keep the bypass list EMPTY.
CODEOWNERS is optional (single-owner) and "require review from code owners" is usually left off for the maintainer's own PRs.
`require_last_push_approval`, merge queue, required deployments, team reviewers, and `required_signatures` are N/A unless the maintainer opts in.

*Interim posture before a distinct agent identity exists* — the agent still runs on the maintainer's own credentials, so author and approver collapse into one actor.
A required review here is illusory: with an empty bypass list it locks out the lone human, and any human bypass that lets the maintainer merge is one the shared-credential agent inherits and can merge through — the review gate enforces nothing either way.
So set `required_approving_review_count: 0` and rely instead on the gates that genuinely bind every actor: strict required status checks (§2), required linear history, blocked force-push and deletion (§2), and optionally `required_review_thread_resolution` and signing.
Keep the bypass-actors list EMPTY in this interim: a `pull_request`-mode bypass would let the shared-credential agent merge a PR past those required checks, defeating the very gates this posture leans on, and with reviews at 0 the maintainer needs no bypass to merge a green PR.
Provision the distinct identity (§4) FIRST and treat it as the event that flips reviews to 1 and adds the human bypass actor — at that point the agent (a separate non-bypass identity) needs the human's approval, and the human approving the agent's PR is not a self-approval.
Setting reviews to 0 in this interim is the correct posture, not a baseline violation.
Name the residual gap this posture cannot close: with shared credentials, no GitHub control distinguishes the human merging from the agent merging *as* the human, so in the interim merge authority is enforceable only outside GitHub — as the operating rule that the agent never merges a PR it authored without explicit delegation (operating-playbook.md), stated affirmatively in `AGENTS.md`, optionally backed by an agent-runtime command restriction (e.g. a deny rule on `gh pr merge` in the agent harness's permission configuration).
Phrase the `AGENTS.md` rule affirmatively ("agents never merge; the maintainer merges") — a prohibition list that only bans red-check merges or self-approval reads as implicit permission to merge green, which is exactly how a shared-credential agent talks itself into merging.
This exception does not reach the small-team/org profiles, where a different human supplies the review. (T3, T4)

**small-team** — 2+ active humans, low-to-moderate risk.
Routine changes get real human review with no bypass; CODEOWNERS by path; `dismiss_stale_reviews` on; `require_last_push_approval` recommended; signing opt-in.

**org / high-risk** — production, regulated, or many contributors.
All of the above plus merge queue, environment gates with required deployments, GitHub Code Security and Secret Protection (code scanning, dependency review, secret scanning with push protection), `require_last_push_approval`, CODEOWNERS teams, and `required_signatures` if the org mandates it.

## §1 Issues & PRs

- Issue and PR templates are present and in use: `.github/ISSUE_TEMPLATE/` directory for issues and `.github/PULL_REQUEST_TEMPLATE.md` for pull requests. (productivity; closes T1 via reduced ambiguity)
- Label taxonomy covers type and priority labels; monorepos add `scope/<area>` labels so PRs and issues are routable per subtree. (productivity)
- `CODEOWNERS` uses explicit path prefixes, never a catch-all-only rule; owners on protected paths must be human users or teams, kept bot-free by membership hygiene — GitHub has no native "owners must be human" enforcement, so this is a repository policy you maintain.
  The goal is that a required CODEOWNERS review can never be satisfied by an agent.
  Monorepo: one prefix per owned subtree.
  A human-owned last-resort catch-all is acceptable only after explicit path rules.
  Solo: a single-user owner (`* @maintainer`) is fine, but a solo owner cannot satisfy their own required CODEOWNERS review, so either rely on the §2 human bypass to merge their own owned-path PRs or do not enable "require review from code owners" in a single-maintainer repo.
  Rulesets also offer a path-scoped `required_reviewers` rule (GA February 2026) that requires review from a named team by file pattern, independent of CODEOWNERS; the same human-only membership hygiene applies to those teams. (closes T3)
- Required reviews are enabled on protected branches and enforced through CODEOWNERS — except in the solo interim posture, where reviews are intentionally 0 (see Repository Profiles).
  In any profile with a second human reviewer, required reviews + CODEOWNERS stay enforced even if the agent is temporarily on a human account. (closes T3)
- Draft-first on owned paths is an operate convention, not a CI gate: open a protected-path change as a draft and wait for a human to promote it to ready; there is no robust required status check for "opened as draft" — a check that fails while the PR is ready-for-review blocks merge permanently, and one that only inspects the `opened` action is cleared by the next push; the enforcement for protected-path changes is the required CODEOWNERS review (§2), which requires a CODEOWNERS-listed human to approve. (closes T3)
- Canonical instruction file (`AGENTS.md`) is present; per-tool files are thin references to it; reusable procedures live as committed artifacts, not pasted into instruction files; monorepos add a nested `AGENTS.md` per subtree. (closes T1 via clear guidance)
- `CONTRIBUTING` is present and discoverable (`CONTRIBUTING.md` or `.github/CONTRIBUTING.md`) describing branch, PR, and review expectations that a contributor or agent must follow. (productivity; closes T1)
- `SECURITY.md` is present (this skill requires it for public repos — GitHub itself treats it as a recommended community file, not a platform requirement; cross-referenced in §4 with private vulnerability reporting). (closes T1)
- `.gitignore` is present and covers the language/framework's secret-bearing and noise paths (`.env*`, `*.pem`, `*.key`, `*.p12`, credential files, build output, caches, dependency dirs); it is the preventive layer in front of §3's secret scanning and push protection, and it keeps PR diffs reviewable; note it does NOT untrack already-committed files — a `git rm --cached` plus history rewrite is needed for those, and that rewrite is the force-push the branch guardrails otherwise warn against, which is why prevention via `.gitignore` matters. (closes T5)
- `.gitattributes` sets `* text=auto` (with `eol=lf`, or `eol=crlf` on Windows-primary repos) to prevent line-ending churn across agent platforms, and marks true generated build output with `linguist-generated=true` to keep PR diffs reviewable.
  Do not mark lockfiles as generated, because lockfile diffs are part of dependency review.
  Note `linguist-generated` affects GitHub's diff display and language stats only — it does not enforce anything.
  Adding `* text=auto` to a repo that already has mixed line endings will produce a one-time normalization commit on first add/commit. (productivity)
- Metadata a PR template prompts for that matters for safety or correctness (a linked issue, a changelog entry, a migration note) is verified by a CI check that is marked REQUIRED in the §2 ruleset — a check enforces nothing until it is required, and a template's prose or checkboxes are never treated as evidence the property holds. (closes T3)

## §2 Branch / Repo Guardrails

- Rulesets (or branch protection) are applied to the default and release branches for ALL actors. No automation identity — the agent identity, a bot PAT, a deploy key, or any CI app the agent can act as — may appear in the bypass-actors list, so the agent can never push past the ruleset.
  In the solo profile, once a distinct agent identity exists and reviews are >= 1, a human maintainer MAY be a bypass actor so the lone human can merge their own work past the review requirement; in the pre-identity interim, and in the small-team and org profiles, the list stays empty (see Repository Profiles).
  Give that human entry `bypass_mode: pull_request`, never `always` — `always` additionally lets the actor push directly to the protected ref, fully bypassing the PR and its required checks (not merely the review) — and never `exempt` (added September 2025), which skips the rules silently without even writing a bypass audit entry.
  Prefer an individual `User` entry (GitHub added user-level bypass for repository-level rulesets in May 2026); where user-level bypass is unavailable (older GitHub Enterprise Server, etc.), use the `Maintain` or `Repository admin` role, but only if the agent provably cannot hold that role — never the `Write` role, which the agent does hold.
  This human entry is the documented escape hatch; the security boundary is human-vs-agent, not author-vs-reviewer.
  Merge queue operates through the ruleset's normal flow and needs no bypass-actors entry in the common case; rules that also target the temporary `gh-readonly-queue/**` branches can require treating the merge-queue app explicitly, so scope rules to the protected branch itself. (closes T4)
- Required status checks are configured and set strict (`strict_required_status_checks_policy: true`, the "Require branches to be up to date before merging" toggle) so a branch that is green against a stale base cannot merge and silently break the protected branch after an interleaving merge; strictness matters most in the solo/interim profile, where these checks are the load-bearing gate doing the work a review otherwise would; in monorepos, use a single always-running gate check per relevant scope that detects changed paths internally and short-circuits (exits 0) when nothing relevant changed — do NOT use `paths:` filters on a required check, because a skipped workflow leaves the required check PENDING and blocks merge forever; there is no ruleset condition that scopes a required status check to changed file paths (branch rulesets target refs, not changed files), so the always-running aggregate gate or an external check app are the correct alternatives. (closes T4)
- `dismiss_stale_reviews` is enabled so a post-approval push invalidates the prior approval; this is the branch-protection field (`dismiss_stale_reviews_on_push` in the rulesets API), not merely an agent convention. (closes T3)
- `require_last_push_approval` is enabled (small-team and org profiles) so the most recent push must be approved by someone other than its author, preventing an author from pushing after approval and merging unreviewed code; this is the strongest anti-approval-laundering control because it directly closes the "approve then sneak a commit" gap.
  It requires a second human, so it deadlocks a single-maintainer repo — in the solo profile leave it off and rely on the agent being unable to self-approve or bypass. (closes T3)
- Linear history is required; restricting `allowed_merge_methods` to `squash` and `rebase` is an optional clarity tightening that matches it — required linear history already blocks merge-commit merges on the protected branch, so leaving the `merge` method enabled is not a functional contradiction, just a misleading dead-end option (the button is offered but the merge is refused) worth removing to reduce confusion. (closes T8)
- Signed commits are strongly recommended and enforced (`required_signatures`) only when the maintainer opts in AND every committer — humans and the agent — has a working signing path; define the accepted mechanism (GitHub App commit signing, GPG, or SSH) so audits know what evidence to check.
  Do not enable `required_signatures` by default: it rejects every unsigned push, an agent that commits locally and pushes with a GitHub App token is NOT auto-signed, and required signing can block a non-author from squash-merging a PR through the web UI. Attribution itself rests on a distinct identity, preserved trailers, and linear history (§4), not on signing. (closes T8)
- Merge queue is configured where serialized merges matter. (productivity; closes T4)
- Force-push and branch deletion are blocked on protected refs. (closes T4)
- Auto-merge safety is a combination, not a single setting: required approving review count >= 1, self-approval not counted, and a CODEOWNERS-required human review — GitHub has no native "human-only approver" flag, so you assemble it from these three settings plus the human-only-approvals required check below.
  This presumes a distinct agent identity excluded from bypass; in the solo interim before that identity (the agent runs on the maintainer's credentials) reviews are intentionally 0 and this combination does not apply — rely on the actor-independent gates per the solo interim posture above.
  Once the distinct identity exists, the solo-profile human bypass actor does not weaken this: it lets the human merge their own work, while the agent (excluded from bypass) still cannot approve or merge its own PR.
  In the post-identity solo profile the CODEOWNERS leg is usually absent ("require review from code owners" is off per the solo profile), so the combination runs on two legs — reviews >= 1 plus self-approval-not-counted still block agent self-merge; the human approval is simply not CODEOWNERS-scoped. (closes T3)
- No `[bot]` approval counts toward the required review threshold: an App with Pull requests write can submit approving reviews, and GitHub counts them like any write-access reviewer's — and the agent's App needs that permission to open PRs, so the grant cannot be dropped.
  There is no setting that excludes bots from counting, so enforce it as the human-only-approvals required status check (worked example in examples.md): triggered on `pull_request` and `pull_request_review`, failing while any live approving review on the PR comes from a `[bot]` actor.
  Org profile: configure the check's operator list so a bot-authored PR approved only by the App's registered operator also fails; leave the list empty in the solo profile, where the operator is the only human reviewer. (closes T3)
- Environment or deployment protection rules gate production via required reviewers or a wait timer on the environment.
  Caveat: environment required reviewers and wait timers are available on public repos on all plans, but on private/internal repos they are Enterprise-only — Pro/Team private repos get environments, secrets, and deployment-branch policies, not these protection rules; if the plan does not provide them, use an external deployment-approval mechanism and mark this item N/A with the plan reason. (closes T5)

## §3 Actions & Supply Chain

- `GITHUB_TOKEN` permissions are least-privilege at both layers: the repository/org Actions "Workflow permissions" setting sets the DEFAULT `GITHUB_TOKEN` permission — set it to read-only so workflows that declare nothing start least-privilege; it is a default, not a hard ceiling, because a workflow can still elevate permissions via its own top-level or job `permissions:` block, so the per-workflow least-privilege `permissions:` declaration is the actual control — declare it in every workflow.
  Additionally, for workflows triggered by fork PRs the `GITHUB_TOKEN` is read-only with no repository secrets by default unless explicitly enabled. (closes T5)
- Untrusted `github.event.*` strings are never interpolated directly into `run:` steps; bind them through `env:` and reference it as a quoted shell variable (e.g. `"$PR_TITLE"`), never via `eval`. (closes T2)
- `pull_request_target` and `workflow_run` discipline: both run privileged with repository secrets and a write-capable token even when the triggering workflow could not, so prefer plain `pull_request` for untrusted fork PRs because it is read-only with no repository secrets by default.
  If `pull_request_target` is unavoidable, no job checks out or executes untrusted head code, and any job with secrets or write scopes is gated behind a protected environment with human approval.
  For `workflow_run`, do not trust artifacts, caches, or other inputs downloaded from the triggering run (they are attacker-controlled when the triggering run came from a fork), and do not check out untrusted head code in any `workflow_run` job that holds secrets or write scope. (closes T2)
- Third-party actions are pinned to a full commit SHA, not a mutable tag. (closes T6)
- OIDC is used for cloud authentication instead of stored long-lived secrets. (closes T5, T9)
- Secret handling: `ACTIONS_STEP_DEBUG` and `ACTIONS_RUNNER_DEBUG` are not enabled in production (registered secrets stay masked even in debug logs, but verbose debug output widens the leak surface for unregistered or derived sensitive values). (closes T5)
- Dependabot is enabled for both version updates and security updates. (closes T6)
- Dependency-update PRs (Dependabot or version bumps) pass through the same required reviews and status checks as any other change; where auto-merge is used it is limited to non-major updates with green checks and never bypasses the required review assembled in §2. (closes T6)
- Code scanning is enabled (CodeQL or an equivalent analyzer); on private/internal repos this requires GitHub Code Security — sold standalone since the April 2025 GitHub Advanced Security unbundling, on Team and Enterprise plans (free on public repos) — configure where available, otherwise mark N/A with the plan reason. (closes T6)
- Secret scanning is enabled with push protection; on private/internal repos this requires GitHub Secret Protection — the other half of that unbundling, on Team and Enterprise plans (free on public repos) — configure where available, otherwise mark N/A with the plan reason. (closes T5)
- Dependency review is enabled on PRs; for agent-added packages, a scoped or private registry and a committed lockfile blunt dependency confusion; on private/internal repos dependency review requires GitHub Code Security (free on public repos) — configure where available, otherwise mark N/A with the plan reason. (closes T6, T7)

## §4 Auditability & Identity

- Distinct agent identity is provisioned, in preference order: GitHub App > fine-grained bot PAT > shared user account. (closes T8, T9)
- PATs, where used at all, are fine-grained and short-lived; classic broad PATs are never used; the token inventory is reviewed periodically and stale tokens are revoked. (closes T9)
- Commits are authored with attribution preserved through squash and rebase — when squash-merging, verify the `Co-authored-by:` trailers survive into the final squash commit message, since squash builds a new message and drops trailers not carried into it; humans are co-authored when pairing; commits are signed when the repo opts into required signing (recommended, not required by default — see §2). (closes T8)
- Audit-log coverage is explicitly considered: the organization audit log is a fixed 180-day window on GitHub.com (not configurable), longer retention requires Enterprise audit-log streaming, and there is no repository-level audit log. (closes T8)
- No mid-session privilege escalation: token scopes are provisioned up front; widening scope requires a human action. (closes T9)
- Private vulnerability reporting is enabled and `SECURITY.md` is present for public repos (see §1). (closes T1)
