# Agent Identity

Artifacts from the examples set — see [README.md](README.md) for the full index.

## Agent Identity Setup

Implements: §4 (T8, T9, T10)

Provision a distinct GitHub App identity for agent work.
The App gets fine-grained permissions scoped to the target repository, produces short-lived installation tokens, and creates a clear audit trail.
GitHub can mark commits as verified when they are created through a verified GitHub App API path that meets GitHub's bot-signature rules.
Local `git commit` followed by `git push` with an App installation token is not automatically signed.
Local commits still need GPG, SSH, or S/MIME signing when the ruleset requires signed commits.

```sh
# 1. Create the App at the org level (GitHub UI or gh api).
#    Set permissions: contents=write, pull_requests=write, issues=write,
#    checks=read, actions=read. Both reads are needed for `gh pr checks`:
#    the status rollup traverses each check suite's workflow run, and an App
#    token lacking actions:read fails with "Resource not accessible by integration".
#    Never grant workflows=write — its absence is itself a server-side control,
#    because GitHub rejects App-token pushes that add or modify files in the
#    .github/workflows/ directory.
#    Record the App ID and generate a private key.

# 2. Install the App on the target repository and note the installation ID.
gh api orgs/{org}/installations \
  --jq '.installations[] | {app_slug, app_id, installation_id: .id, repository_selection}'

# 3. Generate a short-lived installation token (expires in 1 hour).
#    In CI, use actions/create-github-app-token to do this automatically.
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
| Read PR check status (`gh pr checks`) | `checks: read` + `actions: read` |
| Trigger workflow dispatch | `actions: write` (add only if needed) |

Permissions that must NOT be granted, each because its absence is itself a control:

| Permission | Why it stays off |
|---|---|
| `administration: write` | Lets the identity edit or delete the ruleset that gates it — the precondition all of §2 rests on (T10). |
| `workflows: write` | GitHub rejects App-token pushes touching `.github/workflows/` without it, which is a server-side guard on the checks themselves (T3). |
| `actions: write` | Workflow dispatch; grant only in the specific job that needs it, never on the default token. |

There is deliberately NO "release" row here.
Releases are governed by the Contents permission the agent already needs to push branches, so there is nothing to withhold — the T11 boundary is a protected tag ruleset plus a harness deny rule on the release endpoints (config-checklist.md §2), not a permission you can leave off this list.
Package publishing follows the registry's own access model, not a GitHub App repository permission.
These are GitHub App repository permissions — a separate namespace from the workflow `GITHUB_TOKEN` `permissions:` blocks elsewhere in this file; granting one never grants the other.
