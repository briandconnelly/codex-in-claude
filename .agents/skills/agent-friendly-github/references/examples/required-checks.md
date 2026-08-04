# Required Checks

Artifacts from the examples set — see [README.md](README.md) for the full index.

## Required Check: PR Links a Real Open Issue (scoped)

Implements: §1, §2 (T3)

This is the worked example of the principle in §1: template-prompted metadata that matters (the linked issue) is CI-verified, not trusted.
Mark this check REQUIRED in the §2 ruleset ONLY in repos whose workflow mandates issue-backed PRs.

Escape hatch: a `no-issue-required` label exempts hotfixes, reverts, release PRs, and dependency-bump PRs — and because the agent's own `issues: write` scope can apply labels to PRs (PRs are issues to the labels API), the check verifies WHO applied the label and fails if it was a bot or a listed machine user, so the agent cannot clear its own required check by self-applying the exemption.

Scope limitations and failure modes: only same-repo numeric `#N` references are matched at all.
A cross-repo `owner/repo#N` reference does not match the regex (which requires whitespace directly before `#`), so a PR whose only link is cross-repo FAILS the check with "PR body must close an issue" rather than passing unverified — deliberate, but tell contributors, or they will read the failure as a bug.
An issue can also be closed between PR open and merge (re-running on `synchronize`/`reopened` mitigates this); if unambiguous parsing matters, have the template emit a structured trailer rather than free prose.
This check verifies the issue state at the last workflow run, not at merge time, so an issue closed after the last run will not be caught; the `merge_group` leg below deliberately reports success without re-verifying (a merge-group payload carries no PR context), so a merge queue does not close this gap either — if it matters, use an external check app that re-validates near merge.
The `merge_group` trigger itself is still required whenever a merge queue is enabled: a required check that never reports on the merge-group ref stalls every queued merge (config-checklist.md §2).
The workflow also triggers on `labeled` and `unlabeled` events so that applying or removing the `no-issue-required` escape-hatch label immediately re-runs the check and clears or sets the status — without those triggers, adding the label after a failed run leaves the check permanently red.
Keep the job id stable, since `require-issue` is the check-run name the ruleset matches.
Residual: like the human-only-approvals check, this is a `pull_request`-triggered workflow the PR under review can edit — see the §2 mitigation (CODEOWNERS-owned `.github/workflows/`, or an org-ruleset required workflow pinned to a protected ref).

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
  # Required for merge queues (config-checklist.md §2). The script reports success
  # on merge_group without re-verifying — a merge-group payload has no PR context.
  merge_group:

permissions:
  contents: read
  issues: read
  pull-requests: read

jobs:
  require-issue:
    runs-on: ubuntu-24.04
    steps:
      - name: Verify the PR closes a real open issue
        uses: actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea  # v7.0.1
        with:
          script: |
            const LABEL = "no-issue-required";
            // PAT-backed machine users are API type "User"; list their logins here
            // so a label they apply is rejected like a bot's (config-checklist.md §4).
            const MACHINE_USERS = [];

            if (context.eventName === "merge_group") {
              core.info("merge_group run: the PR-level run gated queue entry; reporting success.");
              return;
            }

            const pr = context.payload.pull_request;

            // Escape hatch: `no-issue-required` exempts hotfixes, reverts, release,
            // and dependency-bump PRs — but only when a HUMAN applied it. The agent's
            // own issues:write token can label its own PR, so verify the labeler.
            if (pr.labels.some((l) => l.name === LABEL)) {
              const events = await github.paginate(github.rest.issues.listEvents, {
                owner: context.repo.owner,
                repo: context.repo.repo,
                issue_number: pr.number,
              });
              const applied = events
                .filter((e) => e.event === "labeled" && e.label && e.label.name === LABEL)
                .pop();
              const actor = applied ? applied.actor : null;
              if (!actor || actor.type === "Bot" || MACHINE_USERS.includes(actor.login)) {
                core.setFailed(
                  `'${LABEL}' must be applied by a human; it was applied by ` +
                  `${actor ? actor.login : "an unknown actor"}. Have a maintainer re-apply it.`
                );
                return;
              }
              core.info(`'${LABEL}' applied by human ${actor.login}; issue link not required.`);
              return;
            }

            const body = pr.body || "";
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

GitHub is understood to count an approving review from any write-access actor — including a `[bot]` — toward `required_approving_review_count`, and the agent's App needs Pull requests write to open PRs, so the permission cannot be dropped.
(That counting behavior is assumption 2 below, and is not confirmed.)
This check detects that condition: it stays green while no live bot approval exists, and fails while one does, so combined with reviews >= 1 it is intended to force the counting approval to come from a human.
Mark it REQUIRED in the §2 ruleset.

**Status: unverified against a live repository.**
Treat this as detection, not as an enforced guarantee, until someone runs the adversarial test below.
Two unproven assumptions carry the whole control:

1. That a `pull_request_review`-triggered run updates the required check for the commit the ruleset evaluates.
   GitHub's documented `GITHUB_SHA` for `pull_request_review` is the last merge commit on `refs/pull/<n>/merge` — NOT the PR head SHA (the earlier claim to the contrary in this file was wrong).
   Running the same job name on both `pull_request` and `pull_request_review` is the standard community workaround for this, and it is why both triggers are present, but the skill has not confirmed the resulting check-run actually re-reports.
2. That a bot's approving review counts toward `required_approving_review_count` at all — asserted throughout §2 and not confirmed in GitHub's primary documentation.
   If it does not, this check is unnecessary rather than broken.

The failure direction matters: assumption 1 fails **open**.
The check goes green on `synchronize` while no approval exists; a bot approval arriving later must turn it red. If that later run does not re-report, the stale green stands and the bot approval counts — exactly the outcome the check exists to prevent.

The adversarial test, on a scratch repo: reviews >= 1, this check required, open a PR from the App identity, have a second bot identity approve it, then confirm the check goes red and the merge button is blocked BEFORE any further push.
Record the result in this file. Until then, the durable form of this control is an external GitHub App check or an org-level required workflow pinned to a protected ref — code the PR cannot edit, posting to the SHA GitHub evaluates, revalidating at queue admission.

It fails on a bot approval even when a human approval is also present — GitHub counts approvals indistinguishably, so the remedy is dismissing the bot review, not outvoting it.
The `pull_request` triggers keep a required check from sitting PENDING forever on PRs that never receive a review event, and `pull_request_review` (`submitted`, `dismissed`) re-runs it the moment an approval appears or is dismissed.
Like any review-state check, it reflects the last run, not merge time; the `merge_group` leg reports success without re-checking (a merge-group payload has no PR context), so treat queue-time re-validation as out of this check's scope — the trigger is still required whenever a merge queue is enabled, or queued merges stall (config-checklist.md §2).
The `OPERATORS` list is the org-profile leg: a bot-authored PR whose only human approvals come from the App's registered operators fails, forcing a second human.
Leave it empty in the solo profile, where the operator is the only human reviewer.
The check natively catches API type `Bot` only; a PAT-backed machine user is type `User`, so list any such login in `MACHINE_USERS` or its approvals pass as human (config-checklist.md §2, §4).
Residual: this is a `pull_request`-triggered workflow, so the PR under review can edit the check itself — bind it per §2 (CODEOWNERS-owned `.github/workflows/`, or an org-ruleset required workflow pinned to a protected ref).

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
  # Required for merge queues (config-checklist.md §2). The script reports success
  # on merge_group without re-checking — a merge-group payload has no PR context.
  merge_group:

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
            // PAT-backed machine users are API type "User", not "Bot"; list their
            // logins here so their approvals are treated like bot approvals
            // (config-checklist.md §2, §4).
            const MACHINE_USERS = [];

            if (context.eventName === "merge_group") {
              core.info("merge_group run: approval state was validated on the PR before queue entry; reporting success.");
              return;
            }

            const pr = context.payload.pull_request;
            const isMachine = (u) => u.type === "Bot" || MACHINE_USERS.includes(u.login);
            const reviews = await github.paginate(github.rest.pulls.listReviews, {
              owner: context.repo.owner,
              repo: context.repo.repo,
              pull_number: pr.number,
            });
            // GitHub counts each reviewer's latest state-changing review; mirror that.
            const latest = new Map();
            for (const r of reviews) {
              if (!r.user) continue; // reviewer account deleted — cannot be counted either way
              if (!["APPROVED", "CHANGES_REQUESTED", "DISMISSED"].includes(r.state)) continue;
              latest.set(r.user.login, r);
            }
            const approvals = [...latest.values()].filter((r) => r.state === "APPROVED");

            const botApprovals = approvals.filter((r) => isMachine(r.user));
            if (botApprovals.length > 0) {
              core.setFailed(
                `Approval from bot or machine-user actor(s) ${botApprovals.map((r) => r.user.login).join(", ")} ` +
                "must not count toward required review; dismiss that review and obtain a human approval."
              );
              return;
            }

            if (OPERATORS.length > 0 && isMachine(pr.user)) {
              const humanApprovals = approvals.filter((r) => !isMachine(r.user));
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

            core.info("No bot or machine-user approvals; review policy satisfied.");
```
