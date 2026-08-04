# Rulesets and Environments

Artifacts from the examples set — see [README.md](README.md) for the full index.

## Hardened Ruleset JSON

Implements: §2 (T3, T4, T8)

Apply via the GitHub Rulesets REST API (illustrative — substitute your owner and repo):

```sh
gh api --method POST repos/{owner}/{repo}/rulesets --input hardened-ruleset.json
```

JSON has no comment syntax; all caveats are in this prose.
This baseline implements the **org / high-risk** profile's ruleset-expressible review and merge controls; full org posture adds merge queue and `required_deployments` (see the production environment gate below) where they apply.
See the solo-profile adaptation note after the JSON.
The `bypass_actors` array is empty in this baseline.
Who may appear there, in which `bypass_mode`, and under which profile conditions is governed by the bypass-actors bullet in [config-checklist.md](../config-checklist.md) §2 — read it before changing this array; the JSON below is a rendering of that rule, not a second statement of it.
`non_fast_forward` blocks force-push to the protected ref.
`dismiss_stale_reviews_on_push` invalidates approvals after any new push, closing the post-approval-push gap (T3).
`require_code_owner_review: true` requires a human CODEOWNERS review (T3).
`require_last_push_approval: true` requires the most recent push to be approved by someone other than its author — this defeats the "approve then sneak a commit" pattern where an author pushes after approval and merges unreviewed code (T3).
It needs a second human, so omit this parameter in a solo repo, where it would deadlock the lone maintainer.
`required_review_thread_resolution: true` requires code-review conversations to be resolved before merge.
`dismissal_restriction` names who may dismiss reviews — substitute a human team's real id, and never list the agent identity: any actor with `pull_requests: write` can otherwise clear a human's review, which launders approval by subtraction (§2, T3).
Confirm the object's shape (`enabled`, `allowed_actors[]` of `{type, id}`) against the live rulesets schema before applying; it is newer than the rest of this rule.
Signed commits (`required_signatures`) are intentionally NOT in this baseline: enforce them only when you have opted into signing and every committer (humans and the agent) has a working signing path — see the optional signing variant below (T8).
`required_linear_history` prevents merge commits (T8).
`allowed_merge_methods` is restricted to `squash` and `rebase` because `required_linear_history` is enabled — a plain merge commit would not preserve linear history, so it is excluded; squash and rebase both do.
Note: a squash merge builds a new commit message, so confirm `Co-authored-by:` trailers carry into it (attribution, T8); and if you enable required signing, GitHub blocks a non-author from squash-merging via the web UI — a reason to prefer rebase for attribution- or signing-sensitive agent PRs.
The `required_status_checks` array carries every check the profile makes mandatory, not just the project's test job: `human-only-approvals` is listed here because config-checklist.md §2 requires it, and a "hardened" ruleset that omits it does not implement the profile it claims to.
Add the issue-link check the same way in repos whose workflow mandates issue-backed PRs, and drop `human-only-approvals` only if you have deliberately marked that control N/A.
Before applying, confirm each listed context is a check that actually reports on every PR — a required context no workflow produces stays PENDING and blocks merge forever (see the monorepo gate below).
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
        "allowed_merge_methods": ["squash", "rebase"],
        "dismissal_restriction": {
          "enabled": true,
          "allowed_actors": [
            { "type": "Team", "id": 123456 }
          ]
        }
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          {
            "context": "test"
          },
          {
            "context": "human-only-approvals"
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

**Solo-profile adaptation.** The solo profile and its pre-identity interim are defined in [config-checklist.md](../config-checklist.md) (Repository Profiles) — that section is the authority on which values apply when, and why.
Mechanically, adapting this baseline for solo means: set `required_approving_review_count` and `bypass_actors` to the values the profile calls for at your current stage, and remove `require_last_push_approval` (it needs a second human).
The JSON fragment below is the shape of a human bypass entry, for when the profile calls for one.

```json
"bypass_actors": [
  { "actor_type": "User", "actor_id": 1234567, "bypass_mode": "pull_request" }
]
```

Confirm the exact `actor_type`/`actor_id` fields against the current rulesets API, or add the user through the UI (Settings → Rules → Rulesets → Bypass list → Add bypass).
Fall back from a `User` entry only as §2 permits.

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
