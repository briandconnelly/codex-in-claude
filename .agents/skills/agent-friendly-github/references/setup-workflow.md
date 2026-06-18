# Setup Workflow

Use this workflow to configure a new repository for agent work, or to harden an existing one.
The workflow walks every item in [config-checklist.md](config-checklist.md) and emits concrete artifacts — ruleset JSON, CODEOWNERS files, workflow permission blocks, and label definitions — that live in [examples.md](examples.md).
Steps are ordered by blast-radius: you close the dangerous gaps first and add the productivity surface only after the guardrails are in place.

Note: if you use the GitHub MCP server to carry out these steps, it is subject to the same identity and permission constraints as any other agent surface — the ruleset, CODEOWNERS ownership, and required checks apply regardless of which API surface the agent uses.

## Ordering Principle

Do §4 (Auditability & Identity) first, then §2 (Branch / Repo Guardrails), then §3 (Actions & Supply Chain), then §1 (Issues & PRs).

The reason follows blast radius: close the highest-risk surfaces first.
§4 (identity) is the foundation — every later action must be attributable, so identity comes before anything else.
§2 (branch/merge guardrails) comes next, because an agent with an identity but no guardrails can still push directly to a protected branch or approve its own PR.
§3 (CI/supply-chain hardening) comes before the productivity surface because the CI surface is dangerous the moment any workflow runs on repo content — injection, privilege escalation, and supply-chain risks all activate at first workflow run.
§1 (templates, labels, CODEOWNERS, instruction files) is last because it is the least dangerous and depends on everything else being in place.

One dependency to observe: CODEOWNERS lives in §1, but the §2 required-review rule depends on it.
Create CODEOWNERS as part of the §2 step (or immediately before it), even though the rest of §1 comes last.

If the repository already runs Actions on untrusted input — a `pull_request_target` workflow, a workflow that accepts arbitrary `github.event.*` data in a `run:` step, or any workflow triggered by fork PRs with secrets — treat §3 as urgent; do not defer any of it.

## Procedure

Before Step 1, pick the repository profile (solo, small-team, or org/high-risk) defined at the top of [config-checklist.md](config-checklist.md).
The profile determines which controls below are required versus N/A — most importantly, a solo repo keeps the bypass list empty and reviews at 0 until a distinct agent identity exists, then adds a human bypass actor in Step 2 so the lone maintainer can still merge their own work, and treats `require_last_push_approval` and `required_signatures` as opt-in.

### Step 1 — Establish agent identity (§4)

Provision a distinct, attributable identity for every agent that will work this repository.
Preference order: GitHub App (fine-grained permissions, short-lived tokens, clear audit trail) > fine-grained PAT (narrowly scoped, short expiry) > shared user account (avoid for automated work).
Never create or rely on classic broad PATs (closes T9).

For a GitHub App, create it at the org level (or under the personal account that owns a user-owned repository) and generate an installation token scoped to the target repository:

```sh
# Inspect an existing app installation (illustrative — substitute real app and org IDs)
gh api orgs/{org}/installations \
  --jq '.installations[] | {installation_id: .id, app_slug, app_id, repository_selection}'

# Confirm token scopes for a specific installation.
# NOTE: the /app/* endpoints require authenticating AS THE APP with a JWT signed by the
# app's private key — a normal `gh auth login` user token will not work here.
# See the Agent Identity Setup artifact in examples.md.
gh api app/installations/{installation_id}/access_tokens \
  --method POST \
  --field "repositories[]={repo_name}" \
  --jq '.permissions'
```

Decide your commit-signing posture before any agent commits land.
Signing is strongly recommended but opt-in — do not enforce `required_signatures` (Step 2) unless every committer (humans and the agent) has a working signing path, because a local commit pushed with a GitHub App token is not auto-signed and would be rejected.
If you adopt signing, decide the accepted mechanism — GitHub App commit signing (automatic when the App pushes via the API), GPG, or SSH — and record it in the repository so audits know what evidence to expect (closes T8).

See the **agent identity setup** artifact in [examples.md](examples.md) for App registration steps, a token-scope inventory table, and the co-authorship trailer format.

### Step 2 — Apply branch / repo guardrails (§2)

Create a repository ruleset targeting the default branch and any release branches.
No automation identity — the agent identity, a bot PAT, a deploy key, or any CI app the agent can act as — may appear in the bypass-actors list, so the agent can never push past the ruleset.
In the solo profile, once a distinct agent identity exists and reviews are >= 1, add the human maintainer as a bypass actor so the lone human can merge their own work past the review requirement: use an individual `User` entry with `bypass_mode: pull_request` — never `always` (which additionally permits direct pushes) or `exempt` (which skips the rules silently with no bypass audit entry).
Where user-level bypass is unavailable, use the `Maintain` or `Repository admin` role, but only if the agent provably cannot hold that role — never the `Write` role, which the agent holds.
In the pre-identity solo interim (reviews 0) keep the bypass list empty, per the solo interim posture in [config-checklist.md](config-checklist.md).
In the small-team and org/high-risk profiles the list stays empty too — a second human provides the review, so there is no lone maintainer to unblock.
Merge queues operate through the normal ruleset flow and need no bypass entry in the common case (see §2 for the `gh-readonly-queue/**` caveat) (closes T4).

Required ruleset conditions:

- Required status checks, set strict (`strict_required_status_checks_policy: true`, the "Require branches to be up to date before merging" toggle) so a branch green against a stale base cannot merge and silently break the protected branch — load-bearing especially in the solo interim posture, where these checks do the work review otherwise would; in monorepos, use a single always-running gate check that detects changed paths internally rather than `paths:`-filtering the workflow — a skipped required check stays PENDING and blocks merge forever.
- `dismiss_stale_reviews` enabled — a post-approval push invalidates the prior approval; this is a ruleset setting, not agent convention (closes T3).
  Note: this is the branch-protection field name; the equivalent ruleset API field is `dismiss_stale_reviews_on_push`.
- Signed commits (`required_signatures`) only if you opted into signing in Step 1 and every committer has a working signing path — recommended, not a default; otherwise omit it (closes T8).
- `require_last_push_approval` in small-team/org profiles; omit it in the solo profile, where it would deadlock the lone maintainer (closes T3).
- Linear history required (closes T8).
- Force-push and branch deletion blocked on protected refs (closes T4).

Assemble auto-merge safety as a combination of three settings (GitHub has no single "human-only approver" flag):
required approving review count ≥ 1 + self-approval not counted + CODEOWNERS-required human review on protected paths (closes T3).
This presumes a distinct agent identity exists; in the solo interim before that identity (the agent runs on the maintainer's own credentials) a required review is illusory — set the count to 0 and rely on the actor-independent gates per the solo interim posture in [config-checklist.md](config-checklist.md), then flip it to ≥ 1 once the distinct identity is provisioned.

Add an environment protection rule for any production deployment target: require a named human reviewer or a timed wait before the environment job proceeds (closes T5).
Note: environment required reviewers and wait timers are available on public repos on all plans, but on private/internal repos they are Enterprise-only — Pro/Team private repos get environments, secrets, and deployment-branch policies, not these protection rules; if the plan does not provide them, use an external deployment-approval mechanism and mark this step N/A with the plan reason.
If the default branch ruleset must require successful production deployment before merge, add a `required_deployments` rule for that environment.
See the **production environment gate** artifact in [examples.md](examples.md).

Apply the ruleset via `gh api` (illustrative — substitute your repo and the hardened ruleset JSON from [examples.md](examples.md)):

```sh
# Create a repository ruleset from a local JSON file
gh api repos/{owner}/{repo}/rulesets \
  --method POST \
  --input hardened-ruleset.json

# List existing rulesets to confirm
gh api repos/{owner}/{repo}/rulesets \
  --jq '.[].name'
```

See the **hardened ruleset JSON** artifact in [examples.md](examples.md) for the complete field set including `required_status_checks`, `dismiss_stale_reviews_on_push` (the ruleset field; the branch-protection equivalent is `dismiss_stale_reviews`), and `non_fast_forward`; `required_signatures` is not in the baseline and appears only in the optional signing variant there.

### Step 3 — Wire Actions / supply-chain hardening (§3)

Set least-privilege `GITHUB_TOKEN` permissions at the repository level (Settings → Actions → Workflow permissions → Read repository contents and packages) and enforce them in every workflow file with a top-level `permissions:` block that defaults to read-only; grant write scopes narrowly per job only (closes T5).
These are two distinct layers: the repository or organization "Workflow permissions" setting sets the DEFAULT `GITHUB_TOKEN` permission — it is a default, not a hard ceiling, because a workflow can still elevate via its own `permissions:` block; the per-workflow top-level `permissions:` declaration is the actual control, so configure both — the repository setting as the least-privilege default and the workflow block as the operative grant.

Pin every third-party action to a full commit SHA, not a mutable tag or `@main` (closes T6).
Enable OIDC for cloud authentication in place of stored long-lived secrets (closes T5, T9).

Ensure no untrusted `github.event.*` strings flow directly into `run:` steps — bind them through `env:` and reference the environment variable in shell (closes T2).
Audit `pull_request_target` usage: prefer replacing it with plain `pull_request`, which gets a read-only token and no repository secrets for fork workflows (closes T2).
If `pull_request_target` is unavoidable, no job checks out or executes untrusted head code, and any job with secrets or write scopes is gated behind a protected environment with a human reviewer.

Enable GitHub-native security features:

- Dependabot version and security updates (closes T6).
- Code scanning (CodeQL or equivalent) (closes T6).
- Secret scanning with push protection (closes T5).
- Dependency review action on pull requests, with a committed lockfile and (for private packages) a scoped registry so dependency confusion is blunted (closes T6, T7).

Do not enable `ACTIONS_STEP_DEBUG` or `ACTIONS_RUNNER_DEBUG` in production environments — registered secrets stay masked, but verbose debug logs widen the leak surface for unregistered or derived sensitive values (closes T5).

See the **least-privilege, injection-safe Actions workflow** artifact in [examples.md](examples.md) for a complete annotated example showing the top-level `permissions:` block, SHA-pinned actions, `env:`-bound event values, and OIDC credential exchange.

### Step 4 — Lay down the productivity surface (§1)

With guardrails in place, add the surfaces that make agents productive and contributions unambiguous (closes T1, T3).

**Issue and PR templates.**
Create `.github/ISSUE_TEMPLATE/` with templates for the issue types your project uses, and `.github/PULL_REQUEST_TEMPLATE.md` covering the PR checklist (linked issue, test plan, security consideration).
See the **issue and PR templates** artifacts in [examples.md](examples.md).
If your workflow mandates issue-backed PRs, wire up the **Required Check: PR Links a Real Open Issue** artifact in [examples.md](examples.md) and mark it required in the Step 2 ruleset, so the template's linked-issue field is CI-verified rather than trusted (§1).

**Label taxonomy.**
Define type labels (`type/bug`, `type/feature`, `type/chore`, `type/docs`) and priority labels (`priority/p0`–`priority/p2`).
In a monorepo, add `scope/<area>` labels for each major subtree so PRs and issues are routable without reading the diff.
See the **label taxonomy** artifact in [examples.md](examples.md).

**CODEOWNERS.**
Use explicit path prefixes — never a catch-all-only rule.
Owners on protected paths must be human users or teams, never bot or agent accounts, so a required review can never be satisfied by an agent (closes T3).
In a monorepo, one prefix per owned subtree.
See the **CODEOWNERS Patterns (Monorepo and Solo)** artifact in [examples.md](examples.md).

**Draft-first convention.**
GitHub has no native "require draft by path" feature.
Document the convention in `AGENTS.md` and the operating playbook: protected-path changes open as drafts and wait for a human to promote them.
Do not make draft-state a required merge gate.
The enforceable control is the CODEOWNERS-required human review.
See the **draft-first convention** note in [examples.md](examples.md).

**`AGENTS.md` and per-tool files.**
Create a canonical `AGENTS.md` at the repo root covering branching strategy, commit format, review expectations, label use, test commands, and off-limits paths.
Per-tool files (`CLAUDE.md`, `GEMINI.md`, etc.) are thin pointers — for Claude Code, the full content of `CLAUDE.md` is a single line: `@AGENTS.md`.
Reusable procedures belong as committed artifacts, not pasted into instruction files.
In a monorepo, add a nested `AGENTS.md` per subtree with meaningfully different rules.
See the **Agent Instruction File Pattern** artifact in [examples.md](examples.md).

**`.gitignore` and `.gitattributes`.**
Add a `.gitignore` covering secret-bearing and noise paths before running the first `git add`, so secrets are never staged; see the **Starter .gitignore** artifact in [examples.md](examples.md).
Add a `.gitattributes` (with `* text=auto eol=lf`) before cross-platform commits land, so line endings normalize from the start and generated files are collapsed in PR diffs; see the **Minimal .gitattributes** artifact in [examples.md](examples.md).

**`CONTRIBUTING` and `SECURITY.md`.**
Add `CONTRIBUTING.md` or `.github/CONTRIBUTING.md` describing branching, PR, and review expectations.
Add `SECURITY.md` — this skill requires it for public repos and recommends it for private repos — and cross-reference it with private vulnerability reporting (Step 5).
See the **Supporting Files (CONTRIBUTING.md, SECURITY.md)** artifact in [examples.md](examples.md).

### Step 5 — Confirm public/private and monorepo specifics

Run through the variations that apply to your repository type:

**Monorepo.**
Confirm an always-running monorepo gate check is configured (not `paths:`-filtered) so a change in one subtree does not block unrelated subtrees (§2).
Confirm nested `AGENTS.md` files exist for subtrees with different rules (§1).
Confirm `scope/<area>` labels cover all major subtrees (§1).
Confirm CODEOWNERS has one prefix per owned subtree and is never catch-all-only (§1).
If a last-resort catch-all exists, confirm it is human-owned and appears after explicit path rules.

**Public repos.**
Enable private vulnerability reporting (GitHub Security → Private vulnerability reporting) and verify `SECURITY.md` references it (§4, §1).
Confirm secret scanning with push protection is active — GitHub auto-enables it only on new public repos owned by personal accounts (since March 2024), so org-owned and pre-existing public repos must enable it explicitly.

**Private repos.**
Private repos get no guardrail exemption.
Apply identical rulesets, the same least-privilege `GITHUB_TOKEN` defaults, and the same CODEOWNERS rules.
Enable Dependabot explicitly, and code scanning where the plan provides it — code scanning on private repos requires GitHub Code Security (sold standalone since the April 2025 GitHub Advanced Security unbundling, on Team and Enterprise plans).
Enable secret scanning with push protection where the plan provides it — free on public repos, but private repos require GitHub Secret Protection (the other half of that unbundling); if unavailable, record secret scanning as N/A in the audit rather than implying it can always be turned on.

## Verification

Before declaring setup done, re-walk [config-checklist.md](config-checklist.md) in full.
Every item must be recorded as one of:

- **Configured** — the specific GitHub field, setting, or file is named.
- **N/A** — one line stating why it does not apply to this repository.

This mirrors the Set up Done Criteria in SKILL.md: every checklist item configured or explicitly marked N/A, all emitted artifacts listed.

Checklist walkthrough by section:

**§1 Issues & PRs** — verify issue templates present, PR template present, label taxonomy defined (including `scope/<area>` if monorepo), CODEOWNERS with explicit prefixes and human-only owners on protected paths, required reviews enabled (or intentionally 0 in the solo pre-identity interim — see the profiles in config-checklist.md), draft-first convention documented, safety-relevant template metadata verified by a required CI check, `.gitignore` covering secret-bearing paths, `.gitattributes` normalization in place, `AGENTS.md` present with thin per-tool adapter files, `CONTRIBUTING` present, `SECURITY.md` present.

**§2 Branch / Repo Guardrails** — verify ruleset targets default and release branches, bypass-actors list contains no automation identity (and only the human maintainer in the solo profile once reviews are >= 1; empty in the solo pre-identity interim and in small-team and org), required status checks configured (always-running gate check if monorepo, not `paths:`-filtered), `dismiss_stale_reviews_on_push` set in ruleset, `require_last_push_approval` set per profile (omitted for solo), linear history required, signed commits required only if signing was opted into, merge queue configured if needed, force-push and deletion blocked, auto-merge safety assembled from the three-part combination, environment protection rules in place for production targets.

**§3 Actions & Supply Chain** — verify top-level `permissions:` block in every workflow defaults to read-only with write scopes granted narrowly per job only, no untrusted `github.event.*` values interpolated directly into `run:` steps, no `pull_request_target` workflow checks out or executes untrusted head code, any privileged `pull_request_target` job is environment-gated, no `workflow_run` job with secrets or write scope downloads triggering-run artifacts or caches or checks out untrusted head code, all third-party actions pinned to full commit SHAs, OIDC used for cloud auth, `ACTIONS_STEP_DEBUG` / `ACTIONS_RUNNER_DEBUG` not set in production, Dependabot enabled for actions and ecosystem dependencies, dependency-update PRs subject to the same required reviews and checks (auto-merge limited to non-major updates with green checks), CodeQL or equivalent code scanning enabled, secret scanning with push protection enabled, dependency review action active on PRs.

**§4 Auditability & Identity** — verify distinct agent identity provisioned (GitHub App preferred), no classic broad PATs in use, fine-grained PATs short-lived if any, commits authored with attribution preserved (and signed if signing was opted into), audit-log coverage explicitly considered (the org log's fixed 180-day window on GitHub.com; Enterprise streaming for longer retention), no mid-session privilege escalation path exists (token scopes provisioned up front), private vulnerability reporting enabled on public repos.

Emitted artifacts (confirm each is in place or pointed to in [examples.md](examples.md)):

- Hardened ruleset JSON
- Least-privilege, injection-safe Actions workflow (including OIDC job)
- CODEOWNERS file (monorepo or solo pattern)
- Issue and PR templates
- Issue-link required check (if the workflow mandates issue-backed PRs)
- Label taxonomy
- Agent Instruction File Pattern (canonical file + thin adapter)
- Supporting files (`CONTRIBUTING.md`, `SECURITY.md`)
- Draft-first convention note
- Agent identity setup (App registration, token scope table, co-authorship trailer)
- Always-running monorepo gate check (if monorepo)
- Production environment gate (if production deployments exist)
