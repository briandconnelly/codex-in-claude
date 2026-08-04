# Harness Deny Rules

Implements: §2, §4 (T3, T10, T11)

This is the only control in this skill that lives outside GitHub, and it exists because three residuals cannot be closed by repository configuration:

- **Merge authority (T3)** in the solo interim, where GitHub cannot distinguish the human merging from the agent merging as the human.
- **Control-plane tampering (T10)** wherever the agent's identity holds repository administration — always true in the solo interim.
- **Release publishing (T11)** in every profile, because releases are governed by the `contents: write` the agent needs to push branches, so there is no permission to withhold.

## Read this before relying on it

A harness deny rule is weaker than a ruleset, in three specific ways an auditor must weigh:

1. **It is only as good as its coverage of paths to the same API.** Denying `gh api` does not deny `curl`, a Python script, an MCP server with its own GitHub client, or a workflow the agent authored. Deny at the narrowest layer your harness offers — a network or credential boundary beats a command-pattern match; a command-pattern match beats nothing.
2. **Its own configuration is a target.** If the deny list lives in a file inside the repository the agent has `contents: write` on, the agent can propose editing it — T10 recursing one level up. Keep harness permission configuration outside the repository, or CODEOWNERS-own it and treat any change to it as a protected-path change.
3. **It is not audit-evident.** GitHub logs a ruleset bypass; nothing logs "the harness declined to run a command." Absence of evidence here is not evidence of enforcement, which is why a correctly-configured solo interim is still recorded as a finding (audit-workflow.md).

State these limits when you tell a maintainer the interim is "covered." It is compensating control, not equivalent control.

## What must be denied

Cover both the CLI surface and the REST paths behind it; the second column is what an auditor checks the deny list against.

| Surface | REST paths (method) | Why |
|---|---|---|
| Rulesets | `repos/{o}/{r}/rulesets*` (POST, PUT, PATCH, DELETE); `orgs/{o}/rulesets*` | The gates themselves (T10) |
| Branch protection | `repos/{o}/{r}/branches/*/protection*` (PUT, PATCH, DELETE) | The legacy form of the same gates (T10) |
| Repository settings | `repos/{o}/{r}` (PATCH) | Flips `allow_auto_merge`, default branch, visibility (T3, T10) |
| Actions settings | `repos/{o}/{r}/actions/permissions*` (PUT) | Re-enables all actions, widens the default token (T10) |
| Environments and secrets | `repos/{o}/{r}/environments/*` (PUT, DELETE); `.../actions/secrets/*`; `.../actions/variables/*` (POST, PUT, PATCH, DELETE) | Removes deployment gates; plants `ACTIONS_STEP_DEBUG` (T5, T10) |
| App installations | `orgs/{o}/installations*`; `user/installations/*/repositories/*` (PUT, DELETE) | Widens the agent's own reach (T9, T10) |
| Merge | `repos/{o}/{r}/pulls/*/merge` (PUT) | Merge authority in the interim (T3) |
| Releases and tags | `repos/{o}/{r}/releases*` (POST, PATCH, DELETE); `.../git/refs/tags/*` (POST, PATCH, DELETE) | Publishing to consumers (T11) |

## Claude Code example

`.claude/settings.json` — deny rules are evaluated before the permission prompt, so a denied call fails rather than asking.
Keep this file outside the agent's writable tree, or CODEOWNERS-own it.

```json
{
  "permissions": {
    "deny": [
      "Bash(gh api:*--method DELETE*rulesets*)",
      "Bash(gh api:*rulesets*)",
      "Bash(gh api:*branches/*/protection*)",
      "Bash(gh api:*actions/permissions*)",
      "Bash(gh api:*actions/secrets*)",
      "Bash(gh api:*environments*)",
      "Bash(gh api:*installations*)",
      "Bash(gh api:*/merge*)",
      "Bash(gh api:*releases*)",
      "Bash(gh pr merge:*)",
      "Bash(gh release:*)",
      "Bash(gh ruleset:*)",
      "Bash(gh secret:*)",
      "Bash(gh variable:*)",
      "Bash(git push:*--tags*)",
      "Bash(git tag:*)"
    ]
  }
}
```

Pattern matching is per-harness and literal — it will not catch `curl`, an alias, a here-doc, or a script that shells out.
Deny the outbound credential where your harness can (no `GH_TOKEN` in the agent's environment, with a wrapper that mints a scoped token per allowed operation) and treat the list above as defense in depth rather than the boundary.

## Verifying it

A deny list you have not exercised is a claim, not a control.
For each row in the table, run the denied command and confirm it is refused — then run one command you expect to be ALLOWED (`gh pr create`, `gh issue list`) and confirm it succeeds.
A deny list that blocks everything is indistinguishable from a broken harness, and one that blocks nothing looks identical to a clean run; only both halves together are evidence.

```sh
# Expect: refused by the harness, no API call made.
gh api repos/{owner}/{repo}/rulesets --method POST --input /dev/null
gh pr merge 1 --squash
gh release create v0.0.0-probe

# Positive control — expect: succeeds.
gh issue list --limit 1
```

Record the transcript of both halves as the evidence for this control.
