# Examples

Copy-adaptable artifacts implementing the controls in `config-checklist.md`; each is labeled with the §N section and threat class (Tn) it implements.
Adapt names, paths, org slugs, and SHAs to your repository before use.
Action SHAs and version comments throughout this file are illustrative examples only — verify and re-pin each SHA against the release you actually intend to use before deploying.

## Hardened Ruleset JSON

Implements: §2 (T3, T4, T8)

Apply via the GitHub Rulesets REST API (illustrative — substitute your owner and repo):

```sh
gh api --method POST repos/{owner}/{repo}/rulesets --input hardened-ruleset.json
```

JSON has no comment syntax; all caveats are in this prose.
This baseline implements the **org / high-risk** profile's ruleset-expressible review and merge controls; full org posture adds merge queue and `required_deployments` (see the production environment gate below) where they apply.
See the solo-profile adaptation note after the JSON.
The `bypass_actors` array is empty in this baseline — no automation identity is exempted.
In the solo profile, once a distinct agent identity exists and reviews are >= 1, add the human maintainer here (an individual `User` entry with `bypass_mode: pull_request`, or the `Maintain`/`Repository admin` role only if the agent cannot hold it) so the lone human can merge their own work past the review requirement.
In the pre-identity solo interim (reviews 0) leave this empty, per the solo interim posture in [config-checklist.md](config-checklist.md).
Never add the agent identity, a bot PAT, a deploy key, or the `Write` role, and never use `bypass_mode: exempt`, which skips the rules silently with no bypass audit entry.
Small-team and org repos keep this list empty too — a second human reviewer unblocks merges without a bypass.
`non_fast_forward` blocks force-push to the protected ref.
`dismiss_stale_reviews_on_push` invalidates approvals after any new push, closing the post-approval-push gap (T3).
`require_code_owner_review: true` requires a human CODEOWNERS review (T3).
`require_last_push_approval: true` requires the most recent push to be approved by someone other than its author — this defeats the "approve then sneak a commit" pattern where an author pushes after approval and merges unreviewed code (T3).
It needs a second human, so omit this parameter in a solo repo, where it would deadlock the lone maintainer.
`required_review_thread_resolution: true` requires code-review conversations to be resolved before merge.
Signed commits (`required_signatures`) are intentionally NOT in this baseline: enforce them only when you have opted into signing and every committer (humans and the agent) has a working signing path — see the optional signing variant below (T8).
`required_linear_history` prevents merge commits (T8).
`allowed_merge_methods` is restricted to `squash` and `rebase` because `required_linear_history` is enabled — a plain merge commit would not preserve linear history, so it is excluded; squash and rebase both do.
Note: a squash merge builds a new commit message, so confirm `Co-authored-by:` trailers carry into it (attribution, T8); and if you enable required signing, GitHub blocks a non-author from squash-merging via the web UI — a reason to prefer rebase for attribution- or signing-sensitive agent PRs.
Replace `"context"` values under `required_status_checks` with the exact check-run names your CI reports — for a GitHub Actions job this is the job name (the job's `name:`, or its id when no `name:` is set), NOT the `workflow / job` string the PR UI displays; a reusable workflow reports `caller-job / called-job`.
Verify the exact string via the ruleset UI's check picker or `gh api repos/{owner}/{repo}/commits/{sha}/check-runs`.
`integration_id` is optional and omitted here — leave it out to match any app reporting that context, or set it to the reporting app's id (for example, the GitHub Actions app) to require that the status come from that specific app.
Scoped checks (such as the issue-link verifier in the §1 example below) are added to `required_status_checks` only in repos whose workflow mandates them.

```json
{
  "name": "default-branch-hardened",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "bypass_actors": [],
  "rules": [
    {
      "type": "deletion"
    },
    {
      "type": "non_fast_forward"
    },
    {
      "type": "required_linear_history"
    },
    {
      "type": "pull_request",
      "parameters": {
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "required_approving_review_count": 1,
        "require_last_push_approval": true,
        "required_review_thread_resolution": true,
        "allowed_merge_methods": ["squash", "rebase"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          {
            "context": "test"
          }
        ]
      }
    }
  ]
}
```

### Optional: required signing, and the solo-profile adaptation

**Enable signing (opt-in).** When the maintainer adopts signing and every committer has a working signing path, add this rule object to the `rules` array above:

```json
{
  "type": "required_signatures"
}
```

A local commit pushed with a GitHub App token is not auto-signed, so the agent must either sign locally (GPG/SSH) or commit through the App's verified API path; otherwise its pushes are rejected.

**Solo-profile adaptation.** Starting from the baseline above, for the post-identity solo posture (distinct agent identity, reviews >= 1): add the human maintainer to `bypass_actors`, and remove `require_last_push_approval` from the `pull_request` parameters (the small-team profile keeps both an empty bypass list and `require_last_push_approval` — it has a second human to review).
Keep `required_approving_review_count: 1` ONLY once the agent has a distinct identity excluded from the bypass list — then lowering it to 0 would let that agent self-merge after checks, so keep it at 1.
If the agent still runs on the maintainer's own credentials (no distinct identity yet), `required_approving_review_count: 1` is illusory: set it to `0` in that interim, keep `bypass_actors` empty, and rely on the actor-independent gates (strict checks, linear history, blocked force-push/deletion), per the solo interim posture in [config-checklist.md](config-checklist.md).
Flip reviews to 1 and add the human bypass actor when you provision the distinct identity.

```json
"bypass_actors": [
  { "actor_type": "User", "actor_id": 1234567, "bypass_mode": "pull_request" }
]
```

Confirm the exact `actor_type`/`actor_id` fields against the current rulesets API, or add the user through the UI (Settings → Rules → Rulesets → Bypass list → Add bypass).
Where user-level bypass is unavailable, use the `Maintain` or `Repository admin` role instead, but only if the agent provably cannot hold that role — never the `Write` role, which the agent holds.
The maintainer merges their own PRs via this bypass; the agent, once it has a distinct identity excluded from bypass and unable to self-approve, still cannot merge anything without the human (until that identity exists, see the interim caveat above).

## Production Environment Gate

Implements: §2, §3 (T5)

Use this when a repository has production deployments.
The protected environment controls access to production secrets and OIDC credentials.
The optional `required_deployments` ruleset rule makes the named deployment environment pass before matching branches can be merged.
Only use `required_deployments` when every PR matching the branch ruleset reliably triggers a deployment to that environment.
If no deployment reports for a matching PR, the requirement remains unsatisfied and blocks merge just like a skipped required check.
If production deployment is human-managed or conditional, keep the environment protection rule and do not add `required_deployments` to the branch ruleset.

Create `production-environment.json` with the required reviewer and apply it:

```sh
gh api --method PUT repos/{owner}/{repo}/environments/production \
  --input production-environment.json
```

```json
{
  "wait_timer": 0,
  "prevent_self_review": true,
  "reviewers": [
    {
      "type": "Team",
      "id": 123456
    }
  ],
  "deployment_branch_policy": {
    "protected_branches": true,
    "custom_branch_policies": false
  }
}
```

The reviewer team must be human-owned and bot-free, just like CODEOWNERS teams.
`prevent_self_review: true` stops whoever triggered the deployment from approving it — correct for multi-human teams, but in a solo repo it locks the lone maintainer out of their own deployments; set it to `false` (or rely on the wait timer / an external approval) in the solo profile.

Add this rule to the default-branch ruleset when production deployment must pass before merge:

```json
{
  "type": "required_deployments",
  "parameters": {
    "required_deployment_environments": ["production"]
  }
}
```

## CODEOWNERS Patterns (Monorepo and Solo)

Implements: §1 (T3)

The explicit-prefix pattern applies to any repo; the monorepo example below shows per-subtree ownership, and the solo variant follows it.

```text
# Owners on protected paths must be human users or teams, kept bot-free by membership hygiene.
# GitHub has no native "owners must be human" enforcement — this is a repository policy.
# A required CODEOWNERS review can only be satisfied by a listed owner;
# if a bot or agent account were listed, it could satisfy its own review (T3).

# Per-package ownership — explicit path prefixes, never catch-all-only
/packages/auth/          @org/auth-team
/packages/billing/       @org/billing-team
/packages/core/          @org/platform-team

# Shared infrastructure owned by platform
/.github/                @org/platform-team
/infra/                  @org/infra-team

# Documentation owned by docs team
/docs/                   @org/docs-team

# Last-resort catch-all: any path not matched above goes to platform-team.
# Must be a human team, not a bot.
*                        @org/platform-team
```

**Solo variant.** A single-maintainer repo uses one user owner and usually does NOT enable "require review from code owners," because the maintainer cannot approve their own PR:

```text
# Solo: one human owner. If you DO enable required code-owner review,
# the maintainer must merge their own owned-path PRs via the §2 human bypass.
*        @maintainer
/infra/  @maintainer
```

## Issue and PR Templates

Implements: §1

Place the issue form at `.github/ISSUE_TEMPLATE/bug.yml` and the PR template at `.github/PULL_REQUEST_TEMPLATE.md`.

**`.github/ISSUE_TEMPLATE/bug.yml`** — a structured bug-report form that reduces ambiguity and prompt-injection surface (T1):

```yaml
name: Bug report
description: Report a reproducible defect
title: "[bug] "
labels:
  - type/bug
body:
  - type: markdown
    attributes:
      value: |
        Thank you for reporting a bug.
        Fill in every section; incomplete reports will be closed.
  - type: textarea
    id: description
    attributes:
      label: Description
      description: What happened, and what did you expect?
    validations:
      required: true
  - type: textarea
    id: reproduction
    attributes:
      label: Steps to reproduce
      description: Minimal steps that reliably trigger the bug.
    validations:
      required: true
  - type: textarea
    id: environment
    attributes:
      label: Environment
      description: OS, runtime version, package version.
    validations:
      required: true
  - type: checkboxes
    id: existing
    attributes:
      label: Existing issues
      options:
        - label: I searched open issues and this is not a duplicate.
          required: true
```

**`.github/PULL_REQUEST_TEMPLATE.md`** — requires a linked issue, a test plan, and a security note:

```markdown
## Summary

<!-- One paragraph describing what this PR does and why. -->

Closes #

## Test plan

- [ ] Unit tests added or updated
- [ ] Integration tests pass locally (`uv run pytest`)
- [ ] Manual verification steps (list them):

## Security considerations

<!-- Does this change touch auth, secrets, permissions, or CI? Note it here. -->
None / see below:

## Checklist

- [ ] Branch is up to date with `main`
- [ ] Commit messages follow conventional commits
- [ ] CODEOWNERS paths updated if new owned paths were added
- [ ] PR is in draft until all checks pass and human review is requested
```

## Least-Privilege, Injection-Safe Actions Workflow

Implements: §3 (T2, T5, T6)

Key points enforced in this workflow:

- Top-level `permissions: {contents: read}` defaults the token to read-only; jobs that need more grant it narrowly.
- `github.event.pull_request.title` (untrusted input) is bound to an `env:` variable `PR_TITLE`; the `run:` step references `"$PR_TITLE"` — never `${{ github.event.pull_request.title }}` directly in a shell command (T2).
- The third-party action is pinned to a 40-hex commit SHA with a version comment (T6).
- `pull_request_target` is not used; this workflow uses `pull_request` which runs with a read-only token and no repository secrets for fork PRs (T2).

```yaml
# WARNING: never use pull_request_target for workflows that check out untrusted code
# or interpolate github.event.* into run: steps — it runs in the base-repo context
# with repository secrets, and its token can hold write scope unless the repo/org
# workflow-permissions setting or a permissions: block restricts it; the danger is
# that privileged context and secret access, not the checkout.
# Use pull_request instead.
name: CI

on:
  pull_request:
    branches:
      - main
  push:
    branches:
      - main

# Default token is read-only for all jobs in this workflow.
permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-24.04

    steps:
      - name: Checkout
        # Third-party action pinned to a full 40-hex commit SHA, not a mutable tag (T6).
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2

      - name: Log PR title safely
        # Bind the untrusted PR title via env: — NEVER interpolate ${{ github.event.* }}
        # directly into a run: script (T2 / injection-safe).
        # This step only runs on pull_request events — push events have no PR title.
        if: github.event_name == 'pull_request'
        env:
          PR_TITLE: ${{ github.event.pull_request.title }}
        run: |
          echo "PR title: $PR_TITLE"

      - name: Set up Python
        uses: actions/setup-python@0b93645e9fea7318ecaed2b359559ac225c90a2b  # v5.3.0
        with:
          python-version: "3.12"

      - name: Install uv
        uses: astral-sh/setup-uv@f94ec6bedd8674c4426838e6b50417d36b6ab231  # v5.3.1

      - name: Run tests
        run: uv run pytest

  # OIDC cloud-auth job — only this job gets id-token: write.
  # The test job above keeps the default contents: read from the top-level block.
  # OIDC replaces stored long-lived cloud credentials (T5, T9).
  # The deploy job runs only on push to the protected branch — never in a PR context —
  # so OIDC cloud credentials are never available to PR-triggered runs.
  deploy:
    runs-on: ubuntu-24.04
    needs: test
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    # Grant id-token: write only to this job — narrowest possible scope (T5).
    permissions:
      contents: read
      id-token: write

    steps:
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2

      - name: Authenticate to cloud via OIDC
        # Replace with your cloud provider's OIDC action (AWS, GCP, Azure, etc.).
        # OIDC issues a short-lived credential; no long-lived secret is stored.
        # Example for AWS (illustrative — pin to a real SHA before use):
        # uses: aws-actions/configure-aws-credentials@<sha>  # vX.Y.Z
        # with:
        #   role-to-assume: arn:aws:iam::123456789012:role/github-actions-deploy
        #   aws-region: us-east-1
        run: echo "Replace this step with your cloud OIDC auth action."
```

## Agent Instruction File Pattern

Implements: §1

A canonical `AGENTS.md` at the repo root holds all agent guidance.
Per-tool files are thin pointers to it — they do not duplicate content.
For Claude Code, `CLAUDE.md` contains exactly one line (`@AGENTS.md`) and nothing else; Claude Code resolves the reference automatically.
Other tools use their own include or reference mechanism (for example, Gemini's `GEMINI.md` may use a different syntax — check the tool's documentation).
In a monorepo, add a nested `AGENTS.md` per subtree that has meaningfully different branching, testing, or off-limits rules; the root `AGENTS.md` sets defaults and the nested file overrides only the differences.

**`AGENTS.md`** — canonical agent instruction file at the repo root:

```markdown
# Agent Instructions

## Identity and attribution

- Use a distinct GitHub App identity or fine-grained PAT for automated commits.
- Every commit must carry a `Co-authored-by:` trailer when pairing with a human.
- Do not use classic broad PATs.

## Branching strategy

- Branch off `main` for all work: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`.
- Never commit directly to `main` or a release branch.
- Never force-push a remote ref without explicit human instruction.

## Commit format

- Follow Conventional Commits: `type(scope): short description`.
- Keep the subject line under 72 characters.
- Reference the issue number in the commit body: `Closes #<n>`.

## Pull requests

- Open PRs touching CODEOWNERS-owned paths as drafts; only mark ready for review after all checks pass.
- Agents never merge PRs: open the PR, report check status, and stop — the maintainer merges.
  (If merge automation is ever wanted, the delegation must be written here explicitly.)
- Never approve your own PR, and never treat a red required check as ignorable.
- Re-request human review after any post-approval push.
- CODEOWNERS paths require a human team review — do not attempt to satisfy it yourself.

## Testing

- Run `uv run pytest` before marking a PR ready.
- Every required check must be green before a PR is handed to the maintainer to merge.

## Off-limits paths

- `.github/workflows/` — do not edit CI workflows without explicit human instruction.
- `CODEOWNERS` — do not edit without explicit human instruction.
- `infra/` — do not edit production infrastructure files.

## Security

- Never interpolate `github.event.*` values directly into shell commands.
- Never enable `ACTIONS_STEP_DEBUG` or `ACTIONS_RUNNER_DEBUG` in production.
- Pin any third-party action you add to a full 40-hex SHA with a version comment.
```

**`CLAUDE.md`** — the complete file for Claude Code; a single line that references the canonical instruction file:

```text
@AGENTS.md
```

## Label Taxonomy

Implements: §1

A minimal label set covering type, priority, and (in monorepos) scope.
Create labels via `gh label create` or import them from a YAML file with a tool like `github-label-sync`.

```yaml
# labels.yml — adapt names and colors to your project conventions
labels:
  # Type labels
  - name: "type/bug"
    color: "d73a4a"
    description: "Something is not working"
  - name: "type/feature"
    color: "0075ca"
    description: "New feature or request"
  - name: "type/chore"
    color: "e4e669"
    description: "Maintenance, refactoring, or tooling"
  - name: "type/docs"
    color: "0052cc"
    description: "Documentation only change"
  # Priority labels
  - name: "priority/p0"
    color: "b60205"
    description: "Critical — blocking or data-loss risk"
  - name: "priority/p1"
    color: "e99695"
    description: "High — should land this sprint"
  - name: "priority/p2"
    color: "f9d0c4"
    description: "Normal — backlog"
  # Scope labels (monorepo — add one per major subtree)
  - name: "scope/auth"
    color: "c5def5"
    description: "packages/auth subtree"
  - name: "scope/billing"
    color: "bfd4f2"
    description: "packages/billing subtree"
  - name: "scope/core"
    color: "d4c5f9"
    description: "packages/core subtree"
```

## Agent Identity Setup

Implements: §4 (T8, T9)

Provision a distinct GitHub App identity for agent work.
The App gets fine-grained permissions scoped to the target repository, produces short-lived installation tokens, and creates a clear audit trail.
GitHub can mark commits as verified when they are created through a verified GitHub App API path that meets GitHub's bot-signature rules.
Local `git commit` followed by `git push` with an App installation token is not automatically signed.
Local commits still need GPG, SSH, or S/MIME signing when the ruleset requires signed commits.

```sh
# 1. Create the App at the org level (GitHub UI or gh api).
#    Set permissions: contents=write, pull_requests=write, issues=write.
#    Record the App ID and generate a private key.

# 2. Install the App on the target repository and note the installation ID.
gh api orgs/{org}/installations \
  --jq '.installations[] | {app_slug, app_id, installation_id: .id, repository_selection}'

# 3. Generate a short-lived installation token (expires in 1 hour).
#    In CI, use an action like tibdex/github-app-token to do this automatically.
#    Illustrative manual call (requires a signed JWT — use gh-app-token or similar):
# gh api app/installations/{installation_id}/access_tokens \
#   --method POST \
#   --field "repositories[]={repo_name}" \
#   --jq '{token: .token, expires_at: .expires_at, permissions: .permissions}'

# 4. Use the installation token as GH_TOKEN in subsequent gh or git operations.
#    Commits created through a verified GitHub App API path can be auto-verified by GitHub.
#    Local git commits pushed with an App token still need GPG, SSH, or S/MIME signing.

# 5. For human+agent pairing, add a co-authorship trailer to commit messages:
#    Co-authored-by: Human Name <human@example.com>

# 6. If the repo squash-merges, confirm Co-authored-by: trailers carry into the final
#    squash commit message — squash builds a new message and drops trailers not included
#    in it. Prefer rebase merge for attribution-sensitive agent work.
```

**Token scope inventory** (minimum required scopes for agent PR work):

| Operation | Scope |
|---|---|
| Read repo contents | `contents: read` |
| Push branch / commits | `contents: write` |
| Open / update PRs | `pull_requests: write` |
| Create / update issues | `issues: write` |
| Read check runs | `checks: read` |
| Trigger workflow dispatch | `actions: write` (add only if needed) |

Keep `actions: write` out of the default token; grant it only in the specific job that needs it.

## Always-Running Monorepo Gate Check

Implements: §2 (monorepo)

**Why `paths:` filters on a required check are broken.**
If a workflow is configured with `on: pull_request: paths:` and the PR touches none of those paths, GitHub skips the workflow entirely — it never reports a status.
If that workflow's job is listed as a required status check in the ruleset, the required check stays PENDING indefinitely and blocks the merge forever.
There is no way to merge a PR that has a required check stuck in PENDING.

**The correct pattern: one always-running gate check.**
Use a single workflow that triggers on every `pull_request` (no `paths:` filter), detects which paths changed internally, runs per-package work conditionally, and always exits with a clear pass/fail status.
When nothing relevant changed, the gate exits 0 (pass) immediately.
The required-check context is the job name `gate` — that is what you add to the ruleset's `required_status_checks`.

**Alternative:** there is no ruleset condition that scopes a required status check to changed file paths — branch rulesets target refs, not changed files.
CODEOWNERS path patterns give path-specific human review, but path-specific required CI must come from the always-running aggregate gate above, or from an external check app (a GitHub App that computes which paths changed and posts a single check) — not a ruleset path condition.

**Non-required cost-saving workflows** (not required checks) may still use `paths:` filters — skipping them saves CI minutes and causes no merge problems.
Label them clearly and do not add them to `required_status_checks`.

```yaml
# monorepo-gate.yml
# IMPORTANT: No paths: filter here — this workflow MUST run on every PR.
# A paths:-filtered required check stays PENDING when skipped and blocks merge forever.
name: monorepo-gate

on:
  pull_request:
    branches:
      - main

permissions:
  contents: read

jobs:
  gate:
    runs-on: ubuntu-24.04

    steps:
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
        with:
          fetch-depth: 0

      - name: Detect changed packages
        id: changed
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        run: |
          changed_files=$(git diff --name-only "$BASE_SHA"..."$HEAD_SHA")
          echo "Changed files:"
          echo "$changed_files"

          auth_changed=false
          billing_changed=false
          if echo "$changed_files" | grep -q "^packages/auth/"; then
            auth_changed=true
          fi
          if echo "$changed_files" | grep -q "^packages/billing/"; then
            billing_changed=true
          fi

          echo "auth_changed=$auth_changed" >> "$GITHUB_OUTPUT"
          echo "billing_changed=$billing_changed" >> "$GITHUB_OUTPUT"

      - name: Set up Python
        uses: actions/setup-python@0b93645e9fea7318ecaed2b359559ac225c90a2b  # v5.3.0
        with:
          python-version: "3.12"

      - name: Install uv
        uses: astral-sh/setup-uv@f94ec6bedd8674c4426838e6b50417d36b6ab231  # v5.3.1

      - name: Run auth tests
        if: steps.changed.outputs.auth_changed == 'true'
        working-directory: packages/auth
        run: uv run pytest

      - name: Run billing tests
        if: steps.changed.outputs.billing_changed == 'true'
        working-directory: packages/billing
        run: uv run pytest

      - name: Gate passed
        run: echo "All relevant package checks passed (or no relevant paths changed)."
```

Add `gate` as the single required status check in the ruleset.
The GitHub UI under **Settings → Rules → Rulesets** lets you pick required checks by name; for a GitHub Actions job the check-run name is the job name (`gate`, or the job's `name:` if one is set), not the `monorepo-gate / gate` string the PR UI displays.
Because this workflow always runs, the required check always reports a result and never stalls a merge.

## Draft-First Convention, Not a CI Gate

Implements: §1 (T3)

Draft-first (open a protected-path change as a draft, let a human promote it to ready) is an OPERATE convention, not something a required status check can robustly enforce.

No robust required check exists for this property.
A required check that fails while the PR is ready-for-review would block merge permanently — a PR must be non-draft to merge, so a check that demands draft state would never clear.
A variant that only inspects the `opened` action is cleared by the next push (`synchronize`), so it is trivially bypassed.
"Opened as draft" is not durably re-verifiable: once the PR is promoted, there is no webhook event that reliably re-runs the check to confirm the original state.

The actual enforcement for protected-path changes is the required CODEOWNERS review (§2): a CODEOWNERS-listed human must approve, and agents are never listed as owners.
Pair that enforcement with the operate-playbook draft-first rule.

If you want a nudge, a NON-required workflow may post a comment when a protected-path PR is opened ready — but never make draft-state a required merge gate.

## Required Check: PR Links a Real Open Issue (scoped)

Implements: §1, §2 (T3)

This is the worked example of the principle in §1: template-prompted metadata that matters (the linked issue) is CI-verified, not trusted.
Mark this check REQUIRED in the §2 ruleset ONLY in repos whose workflow mandates issue-backed PRs.

Escape hatch: a `no-issue-required` label, applied through a maintainer/CODEOWNERS-gated process, exempts hotfixes, reverts, release PRs, and dependency-bump PRs.

Scope limitations and failure modes: only same-repo numeric `#N` references are verified — cross-repo `owner/repo#N` and private-repo references are not (they would 404); an issue can be closed between PR open and merge (re-running on `synchronize`/`reopened` mitigates this); if unambiguous parsing matters, have the template emit a structured trailer rather than free prose.
This check verifies the issue state at the last workflow run, not at merge time, so an issue closed after the last run will not be caught; if that matters, gate merges through a merge queue (which re-runs checks just before merge) or a check that runs close to merge.
The workflow also triggers on `labeled` and `unlabeled` events so that applying or removing the `no-issue-required` escape-hatch label immediately re-runs the check and clears or sets the status — without those triggers, adding the label after a failed run leaves the check permanently red.
When the label makes the job-level `if:` skip the job, GitHub reports the skipped job as success, so the required check clears; keep the job id stable, since `require-issue` is the check-run name the ruleset matches.

```yaml
name: require-issue-link

on:
  pull_request:
    types:
      - opened
      - edited
      - reopened
      - synchronize
      - labeled
      - unlabeled
    branches:
      - main

permissions:
  contents: read
  issues: read
  pull-requests: read

jobs:
  require-issue:
    runs-on: ubuntu-24.04
    # Escape hatch: a maintainer or CODEOWNERS-listed reviewer applies `no-issue-required` for hotfixes,
    # reverts, release, or dependency-bump PRs. Gate who can add that label through
    # your process — the label is the documented exception, not a free bypass.
    if: ${{ !contains(github.event.pull_request.labels.*.name, 'no-issue-required') }}
    steps:
      - name: Verify the PR closes a real open issue
        uses: actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea  # v7.0.1
        with:
          script: |
            const body = context.payload.pull_request.body || "";
            // Match explicit closing keywords only — not incidental "#123" mentions.
            const re = /\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#(\d+)\b/gi;
            const nums = [...body.matchAll(re)].map((m) => Number(m[3]));
            if (nums.length === 0) {
              core.setFailed(
                "PR body must close an issue, e.g. 'Closes #123'. " +
                "Apply the maintainer-gated 'no-issue-required' label for hotfixes/reverts/release PRs."
              );
              return;
            }
            for (const n of nums) {
              try {
                const { data: issue } = await github.rest.issues.get({
                  owner: context.repo.owner,
                  repo: context.repo.repo,
                  issue_number: n,
                });
                if (issue.pull_request) {
                  core.setFailed(`#${n} is a pull request, not an issue.`);
                  return;
                }
                if (issue.state !== "open") {
                  core.setFailed(`#${n} is not open (state: ${issue.state}).`);
                  return;
                }
              } catch (err) {
                core.setFailed(`Could not verify #${n}: ${err.message}`);
                return;
              }
            }
            core.info("Verified: PR references a real open issue.");
```

## Required Check: Human-Only Approvals

Implements: §2 (T3)

GitHub counts an approving review from any write-access actor — including a `[bot]` — toward `required_approving_review_count`, and the agent's App needs Pull requests write to open PRs, so the permission cannot be dropped.
This check makes the policy enforceable: it stays green while no live bot approval exists, and fails while one does, so combined with reviews >= 1 the counting approval must come from a human.
Mark it REQUIRED in the §2 ruleset.

It fails on a bot approval even when a human approval is also present — GitHub counts approvals indistinguishably, so the remedy is dismissing the bot review, not outvoting it.
The `pull_request` triggers keep a required check from sitting PENDING forever on PRs that never receive a review event, and `pull_request_review` (`submitted`, `dismissed`) re-runs it the moment an approval appears or is dismissed; `pull_request_review` runs attach to the PR's head SHA, which is what the ruleset checks.
Like any review-state check, it reflects the last run, not merge time; a merge queue re-runs checks just before merge if that gap matters.
The `OPERATORS` list is the org-profile leg: a bot-authored PR whose only human approvals come from the App's registered operators fails, forcing a second human.
Leave it empty in the solo profile, where the operator is the only human reviewer.

```yaml
name: human-only-approvals

on:
  pull_request:
    types:
      - opened
      - reopened
      - synchronize
    branches:
      - main
  pull_request_review:
    types:
      - submitted
      - dismissed

permissions:
  contents: read
  pull-requests: read

jobs:
  human-only-approvals:
    runs-on: ubuntu-24.04
    steps:
      - name: Fail while any counting approval comes from a bot
        uses: actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea  # v7.0.1
        with:
          script: |
            // Org profile: App operator logins whose solo approval of a
            // bot-authored PR must not satisfy review. Solo profile: leave empty.
            const OPERATORS = [];

            const pr = context.payload.pull_request;
            const reviews = await github.paginate(github.rest.pulls.listReviews, {
              owner: context.repo.owner,
              repo: context.repo.repo,
              pull_number: pr.number,
            });
            // GitHub counts each reviewer's latest state-changing review; mirror that.
            const latest = new Map();
            for (const r of reviews) {
              if (!["APPROVED", "CHANGES_REQUESTED", "DISMISSED"].includes(r.state)) continue;
              latest.set(r.user.login, r);
            }
            const approvals = [...latest.values()].filter((r) => r.state === "APPROVED");

            const botApprovals = approvals.filter((r) => r.user.type === "Bot");
            if (botApprovals.length > 0) {
              core.setFailed(
                `Approval from bot actor(s) ${botApprovals.map((r) => r.user.login).join(", ")} ` +
                "must not count toward required review; dismiss the bot review and obtain a human approval."
              );
              return;
            }

            if (OPERATORS.length > 0 && pr.user.type === "Bot") {
              const humanApprovals = approvals.filter((r) => r.user.type !== "Bot");
              if (
                humanApprovals.length > 0 &&
                humanApprovals.every((r) => OPERATORS.includes(r.user.login))
              ) {
                core.setFailed(
                  "Bot-authored PR is approved only by the bot's operator(s); a second human must review."
                );
                return;
              }
            }

            core.info("No bot-submitted approvals; review policy satisfied.");
```

## Starter .gitignore

Implements: §1 (T5)

This is a starting point, not exhaustive; it does not untrack files already committed (`git rm --cached <file>` plus a history rewrite is needed for those — and the rewrite is the dangerous force-push the playbook warns against).

```text
# Secrets and credentials
.env
.env.*
*.pem
*.key
*.p12
*.pfx
*credentials*
*.secret

# Build output and caches
node_modules/
__pycache__/
.venv/
dist/
build/
*.log
.coverage
coverage/
```

## Minimal .gitattributes

Implements: §1 (productivity)

`text=auto` normalizes line endings on the next add/commit and will produce a one-time churn commit if the repo already has mixed line endings.
`linguist-generated` only affects GitHub's diff display and stats; it does not block or enforce anything.
Do not mark lockfiles as generated, because lockfile diffs are part of dependency review.

```text
# Normalize line endings for all text files
* text=auto eol=lf

# Mark true generated output so GitHub collapses its diffs and excludes it from language stats
dist/** linguist-generated=true
build/** linguist-generated=true
```

## Supporting Files (CONTRIBUTING.md, SECURITY.md)

Implements: §1 (T1)

**`CONTRIBUTING.md`** — points contributors and agents at the canonical instruction file and sets process expectations:

```markdown
# Contributing

Agent guidance lives in [`AGENTS.md`](/AGENTS.md); read it before opening any PR.

- Branch off `main`: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`.
- Open PRs touching CODEOWNERS-owned paths as drafts; mark ready for review only after all required checks pass.
- At least one human reviewer must approve before merge — agents may not self-approve.
- Run `uv run pytest` locally before marking a PR ready.
- Follow [Conventional Commits](https://www.conventionalcommits.org/) for all commit messages.
```

**`SECURITY.md`** — directs security reporters away from public issues:

```markdown
# Security Policy

**Do not file security vulnerabilities as public GitHub issues.**

Report security issues privately using [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
or email the security contact listed in the repository's package metadata.

We will acknowledge reports within 5 business days and aim to resolve critical issues within 30 days.
```
