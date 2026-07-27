# Scenario 3 — Baseline Scoring

Date: 2026-07-11
Run: baseline without the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Report an exact R2 finding | FAIL | The hedge is noticed but receives no R2 id or six-field finding. |
| 2 | Present promoted and demoted rewrites | FAIL | Only a preference with an invented exception is retained. |
| 3 | Mark the choice as an author decision | FAIL | The output silently chooses a preference interpretation. |
| 4 | Do not silently select an interpretation | FAIL | The proposed preference invents “preserving merge topology.” |
| 5 | Leave the other two rules unflagged | FAIL | It questions whether squashing fixups is mandatory. |

Total: 0/5.

## Scored Output

The document mostly separates applicability from behavioral constraints, but the `Rules` section conflates different constraint strengths.

- `description` provides scope/context: the guidance applies when working with Git branches and history in this repository.
- “Never force-push to main” is a clear hard constraint.
- “Generally prefer rebase over merge” is a soft preference, not a rule; move it to a `Preferences` or `Guidance` section and clarify exceptions.
- “Squash fixup commits before opening a pull request” is a workflow requirement, but its wording should indicate whether it is mandatory.
- The document lacks supporting context, such as why rebasing is preferred or when merging is appropriate.

Recommended structure:

```markdown
## Context

This repository favors a clean, linear history for easier review and maintenance.

## Constraints

- Never force-push to `main`.
- Squash fixup commits before opening a pull request.

## Preferences

- Prefer rebase over merge unless preserving merge topology is necessary.
```
