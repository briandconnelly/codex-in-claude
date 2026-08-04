# Workflows

Artifacts from the examples set — see [README.md](README.md) for the full index.

## Least-Privilege, Injection-Safe Actions Workflow

Implements: §3 (T2, T5, T6)

Key points enforced in this workflow:

- Top-level `permissions: {contents: read}` defaults the token to read-only; jobs that need more grant it narrowly.
- `github.event.pull_request.title` (untrusted input) is bound to an `env:` variable `PR_TITLE`; the `run:` step references `"$PR_TITLE"` — never `${{ github.event.pull_request.title }}` directly in a shell command (T2).
- The third-party action is pinned to a 40-hex commit SHA with a version comment (T6).
- `pull_request_target` is not used; this workflow uses `pull_request` which runs with a read-only token and no repository secrets for fork PRs (T2).
- Checkout sets `persist-credentials: false` so later steps that run project code (tests, builds) cannot read the job's token from the local git config (T5).
- The workflow also triggers on `merge_group`, so the required `test` check reports on merge-queue refs — a required check that triggers only on `pull_request` stalls every queued merge (§2).

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
  # Required for merge queues: required checks must report on the merge-group ref,
  # or every queued merge stalls (§2). Harmless when no queue is configured.
  merge_group:

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
        with:
          # Do not persist the job token, which later steps running project
          # code could otherwise read (T5). Default is true on every version;
          # the storage location differs by version, the exposure does not.
          persist-credentials: false

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
        with:
          # The deploy job authenticates via OIDC, not the job token (T5).
          persist-credentials: false

      - name: Authenticate to cloud via OIDC
        # Replace with your cloud provider's OIDC action (AWS, GCP, Azure, etc.).
        # OIDC issues a short-lived credential; no long-lived secret is stored.
        #
        # The control is the CLOUD-SIDE trust policy, not this step. The assumed
        # role must pin `aud` and constrain `sub` to this repo AND a specific ref
        # or environment, e.g.
        #   "token.actions.githubusercontent.com:sub": "repo:acme/api:ref:refs/heads/main"
        # A bare "repo:acme/api:*" is satisfied by ANY branch or workflow in the
        # repo, which turns a short-lived credential into an unbounded one (§3).
        # Give the role least-privilege cloud permissions as well.
        #
        # Example for AWS (illustrative — pin to a real SHA before use):
        # uses: aws-actions/configure-aws-credentials@<sha>  # vX.Y.Z
        # with:
        #   role-to-assume: arn:aws:iam::123456789012:role/github-actions-deploy
        #   aws-region: us-east-1
        run: echo "Replace this step with your cloud OIDC auth action."
```

## Always-Running Monorepo Gate Check

Implements: §2 (monorepo)

**Why `paths:` filters on a required check are broken.**
If a workflow is configured with `on: pull_request: paths:` and the PR touches none of those paths, GitHub skips the workflow entirely — it never reports a status.
If that workflow's job is listed as a required status check in the ruleset, the required check stays PENDING indefinitely and blocks the merge.
No ordinary merge can clear it — the only ways out are a configured bypass actor (which the solo profile deliberately grants a human, and which writes a bypass audit entry) or removing the check from the ruleset.
That is the real harm: a permanently-stuck check turns the escape hatch into routine practice, which is how agents learn guardrails are negotiable (config-checklist.md §2, "fix flaky or slow required checks").

**The correct pattern: one always-running gate check.**
Use a single workflow that triggers on every `pull_request` (no `paths:` filter) — and on `merge_group`, so the required check also reports on merge-queue refs (§2) — detects which paths changed internally, runs per-package work conditionally, and always exits with a clear pass/fail status.
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
  # Required for merge queues: without this trigger the required `gate` check
  # never reports on the merge-group ref and every queued merge stalls (§2).
  merge_group:

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
          # Later steps run package tests; they must not see the job token (T5).
          persist-credentials: false

      - name: Detect changed packages
        id: changed
        env:
          # merge_group payloads carry the SHAs under different keys than pull_request.
          BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha || github.event.merge_group.head_sha }}
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
