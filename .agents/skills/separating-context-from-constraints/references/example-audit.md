# Worked Example Audit

A before/after audit of a small fictional skill, `deploy-helper`, using the finding format defined in `SKILL.md`.

## Target Document (before)

```markdown
# deploy-helper

This skill deploys services to production using our internal release tooling.

## Background

We've used this workflow for every production push since the Q3 migration off Jenkins, deploying over four hundred releases without a major incident.
And of course, you should never deploy on Fridays.

## Rules

- Always deploy from main.
- For hotfixes, deploy from the hotfix branch.
- Generally try to run the smoke tests after deploying.
- Be careful with production.
- Tag the release, update the changelog, and notify the team in one step.

## Notes

The deploy script reads the target environment from the `DEPLOY_ENV` variable set by the CI runner.
```

## Findings

**R1 Distinguishability**
Location: line 8.
Quoted text: "And of course, you should never deploy on Fridays."
Why it fails: this is a binding rule, but it is buried as the tail of a narrative paragraph about deploy history rather than placed in the `## Rules` section or otherwise marked as a directive; under long-context pressure it reads as color commentary and is easy to miss.
Severity: material.
Suggested rewrite: move it into the `## Rules` section as its own item: "Never deploy on Fridays."

**R2 Explicit strength**
Location: line 14.
Quoted text: "Generally try to run the smoke tests after deploying."
Why it fails: "Generally try to" hedges strength; a reader cannot tell whether smoke tests are mandatory or optional, so the rule cannot be reliably followed or checked.
Severity: material.
Suggested rewrite: this is an author decision between two non-hedged readings.
Promoted (mandatory): "Run the smoke tests after every deploy."
Demoted (context): remove it from `## Rules` and state it as background instead, e.g. "Smoke tests are commonly run after deploying, but this skill does not enforce it."

**R3 Verifiability**
Location: line 15.
Quoted text: "Be careful with production."
Why it fails: there is no observable evidence — output, tool call, or artifact — against which "careful" can be checked; the rule cannot be verified as followed or violated.
Severity: material.
Suggested rewrite: "Require manual confirmation before any deploy to the `production` environment."

**R4 Atomic obligations**
Location: line 16.
Quoted text: "Tag the release, update the changelog, and notify the team in one step."
Why it fails: this bundles three independently checkable obligations — tagging, changelog update, team notification — into one statement; a partial failure (e.g. changelog updated but team not notified) cannot be pinned to a single checkable unit.
Severity: minor.
Suggested rewrite: state the three obligations separately: "Tag the release.", "Update the changelog.", "Notify the team."

**R5 Reachable precedence**
Location: lines 12-13.
Quoted text: "Always deploy from main." / "For hotfixes, deploy from the hotfix branch."
Why it fails: these two rules can actually conflict on a realistic input — a hotfix release — and no precedence is stated for which one governs.
Severity: material.
Suggested rewrite: "Deploy from main; for hotfixes, deploy from the hotfix branch, which takes precedence over the main-branch default for hotfix releases."

Not flagged (false-positive guard): line 7, the migration-history sentence, is discretionary context — rationale and background that degrades gracefully if lost — and is correctly left out of `## Rules`.
Not flagged (false-positive guard): line 20, the `DEPLOY_ENV` sentence, is a load-bearing fact about tool semantics — losing it would make output wrong, but it is not a directive, so it correctly belongs outside `## Rules`.

## Summary

Counts per rule: R1 1, R2 1, R3 1, R4 1, R5 1.
Counts per severity: material 4 (R1, R2, R3, R5), minor 1 (R4).

This document buries one rule in narrative prose, states one rule with ambiguous strength, states one untestable rule, bundles three obligations into one compound rule, and leaves a realistic branch-selection conflict without stated precedence.
The migration-history background and the `DEPLOY_ENV` tool-semantics note are both correctly placed outside `## Rules` and require no changes.
Restructuring the `## Rules` section to contain only atomic, unambiguous, testable, precedence-complete rules — as shown in the rewritten document below — resolves all five findings without changing the skill's intended behavior.

## Rewritten Document (after)

```markdown
# deploy-helper

This skill deploys services to production using our internal release tooling.

## Context

We've used this workflow for every production push since the Q3 migration off Jenkins, deploying over four hundred releases without a major incident.

## Rules

1. Deploy from main; for hotfixes, deploy from the hotfix branch, which takes precedence over the main-branch default for hotfix releases.
2. Never deploy on Fridays.
3. Run the smoke tests after every deploy.
4. Require manual confirmation before any deploy to the `production` environment.
5. After every release:
   - Tag the release.
   - Update the changelog.
   - Notify the team.

## Notes

The deploy script reads the target environment from the `DEPLOY_ENV` variable set by the CI runner.
```
