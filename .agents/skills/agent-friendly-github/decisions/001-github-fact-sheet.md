# 001 — GitHub fact sheet

Every dated or version-dependent claim this skill makes about GitHub, with its source and the date it was checked.
A claim not in this table has not been verified; do not treat its presence in the skill as evidence.

Re-verify before relying on any row: GitHub ships changes to rulesets and Actions continuously, and a row's "verified" date is the last time someone looked, not a guarantee of current behavior.
When a row is found wrong, fix the skill AND the row in the same change.

## Verified

Checked 2026-08-03 against the linked primary sources.

| Claim | Source | Note |
|---|---|---|
| `bypass_mode` enum is `always`, `pull_request`, `exempt`; `actor_type` is `Integration`, `OrganizationAdmin`, `RepositoryRole`, `Team`, `DeployKey`, `User` | [REST rules API](https://docs.github.com/en/rest/repos/rules) | |
| `exempt` silently skips enforcement with no bypass audit signal; added September 2025 | [changelog 2025-09-10](https://github.blog/changelog/2025-09-10-github-ruleset-exemptions-and-repository-insights-updates/) | Explicitly contrasted with break-glass bypass, which does emit audit signals |
| Individual `User` bypass actors on repository-level rulesets; May 2026 | [changelog 2026-05-07](https://github.blog/changelog/2026-05-07-repository-rulesets-user-bypass-and-branch-renaming/) | UI, REST, and GraphQL |
| Path-scoped required-reviewer rule GA; February 2026 | [changelog 2026-02-17](https://github.blog/changelog/2026-02-17-required-reviewer-rule-is-now-generally-available/) | **Inconsistency:** the REST reference still says "required_reviewers is in beta and subject to change." Validate payloads against the live schema. |
| Review-dismissal restriction (name who may dismiss reviews) GA; July 2026 | [changelog 2026-07-07](https://github.blog/changelog/2026-07-07-restrict-who-can-dismiss-reviews-in-rulesets/) | Inside the "Require a pull request before merging" rule |
| `bypass_actors` is returned only to a requester with **write access to the ruleset** | [REST rules API](https://docs.github.com/en/rest/repos/rules) | Not "repository admin" — the distinction matters for inherited org rulesets (audit-workflow.md probe) |
| `GITHUB_SHA` for `pull_request_review` is the last merge commit on `refs/pull/<n>/merge`, NOT the PR head SHA | [Events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) | Same as `pull_request`. Corrects an earlier claim in examples/required-checks.md |
| A `paths:`-filtered workflow that is skipped never reports, so a required check on it stays pending | [Troubleshooting required status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks) | A configured bypass actor can still merge past it |
| Push protection can be bypassed by any actor with push access unless delegated bypass is configured with an explicit bypass list | [Enabling delegated bypass](https://docs.github.com/en/enterprise-cloud@latest/code-security/secret-scanning/using-advanced-secret-scanning-and-push-protection-features/delegated-bypass-for-push-protection/enabling-delegated-bypass-for-push-protection) | Org-level configuration disables the repo-level setting |
| `dismissal_restriction` is the `pull_request` rule parameter restricting who may dismiss reviews; shape is `{enabled, allowed_actors[{type, id}]}` | [REST rules API](https://docs.github.com/en/rest/repos/rules) | Full parameter list: `allowed_merge_methods`, `dismiss_stale_reviews_on_push`, `dismissal_restriction`, `require_code_owner_review`, `require_last_push_approval`, `required_approving_review_count`, `required_review_thread_resolution`, `required_reviewers` |
| Events raised by `GITHUB_TOKEN` generally create no further workflow runs, BUT `pull_request` `opened`/`synchronize`/`reopened` do — in an approval-required state a write-access human must release | [GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token) | Corrects an earlier draft of this skill that said such PRs never trigger workflows and can never merge |
| OIDC immutable subject format `repo:<owner>@<owner-id>/<repo>@<repo-id>:...` applies to repos created after 15 July 2026, to repos renamed or transferred after that date, and to opt-ins; older repos keep `repo:<owner>/<repo>:...`. Not available on GHES | [OIDC reference](https://docs.github.com/en/actions/reference/security/oidc) | A trust policy in the wrong form fails to authenticate |
| Organization-level rulesets are a GitHub Enterprise feature; repository-level rulesets are Team and Enterprise | [About rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets) | Gates the T10-resistant ruleset placement |
| Releases are governed by the **Contents** permission — there is no separable release permission to withhold from an identity that needs `contents: write` | [REST releases](https://docs.github.com/en/rest/releases/releases) | Why T11's boundary is a tag ruleset plus a harness deny rule, not a permission |
| Repository admins can create, edit, and delete repository rulesets | [Managing rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/managing-rulesets-for-a-repository) | The basis for T10 and the solo-interim correction |
| `actions/checkout` `persist-credentials` defaults to `true`; v6 stores the credential under `$RUNNER_TEMP` rather than `.git/config` | [actions/checkout README](https://github.com/actions/checkout) | The default did not change; only the location did |
| OIDC trust policies need at least one `sub`-style condition or untrusted repositories can assume the role | [OIDC reference](https://docs.github.com/en/actions/reference/security/oidc) | |
| Environment required reviewers and wait timers: public repos all plans; private/internal Enterprise-only (Pro/Team get environments, secrets, deployment branch policies) | GitHub docs, deployment environments | Matches the wording in the plan-caveats table |
| Org audit log is a fixed 180-day window on GitHub.com; Git events within it retained 7 days | GitHub docs, audit log | |
| CODEOWNERS is last-match-wins; GitHub uses the first file found in `.github/`, root, then `docs/` | GitHub docs, CODEOWNERS | |
| A PR author cannot approve their own pull request | GitHub docs, PR reviews | Platform invariant the review controls rely on |
| App-token pushes touching `.github/workflows/` are rejected without `workflows: write` | GitHub docs, App permissions | Why withholding that permission is itself a control |

## Asserted but NOT verified

These are load-bearing in the skill and were not confirmed in primary documentation.
Each is flagged inline where it appears.
Resolve them with a live test on a scratch repository, then move the row up.

| Claim | Where it matters | How to settle it |
|---|---|---|
| A GitHub App's approving review counts toward `required_approving_review_count` | The entire premise of the human-only-approvals check (§2, references/examples/required-checks.md) | Have an App with Pull requests write approve a PR on a repo with reviews >= 1; observe whether the merge button unblocks |
| A `pull_request_review`-triggered run updates the required check for the commit the ruleset evaluates | Whether the human-only-approvals check binds at all; it fails **open** if not | Required check + bot approval after a green run; observe whether the check goes red before merge |
| An installation token scoped to selected repositories can open issues on arbitrary public repos | §4's "treat the enrolled set as the bot's git reach, not its write reach" | Attempt `POST /repos/{other}/{public-repo}/issues` with a scoped installation token |

## Untested surface

- GitHub Enterprise Server: flagged untested throughout the skill. Field names and API paths have not been checked against any GHES release.
- None of the executable artifacts in `references/examples/` (ruleset JSON, four workflows, environment payload) has been applied to a live repository. They have been parsed (`jq`) and linted (`actionlint`) only.
