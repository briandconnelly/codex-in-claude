# Scenario 1 — Baseline Scoring

Date: 2026-07-11
Run: baseline without the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | R1 finding for the conventional-commit rule | FAIL | The output notices burial but supplies no R1 finding or six-field format. |
| 2 | R1 finding for the 72-character rule | FAIL | The output notices the rule but supplies no R1 finding or material severity. |
| 3 | R1 finding for the Test plan rule | FAIL | The output notices the rule but supplies no R1 finding or material severity. |
| 4 | Mark all three findings material | FAIL | No severities are assigned. |
| 5 | Leave pure background unflagged | FAIL | The findings criticize anecdotes and raise unrelated accepted-type and scope issues. |

Total: 0/5.

## Scored Output

## Audit

Separation is weak because three enforceable constraints are embedded in historical and explanatory prose rather than collected in a clearly labeled rules section.

### Constraints

- Every commit message must start with a lowercase Conventional Commit type, such as `feat` or `fix`.
- The subject line must not exceed 72 characters.
- Before opening a pull request, the commit body must include a `Test plan` section describing verification.

### Context

- Commit-message style drifted after years of using different tools.
- The convention was adopted during a 2023 tooling cleanup and has worked well for the team.
- Consistent type prefixes support automated changelog generation.
- The 72-character limit improves display in terminal-based Git tools.
- Reviewers have previously flagged missing test plans.
- The release-notes generator reads the root `CHANGELOG.md`.

### Findings

- The lowercase type rule is buried in repository history.
- The 72-character rule is introduced as “worth noting,” which understates a mandatory requirement.
- The `Test plan` requirement appears under “Notes” and begins with “By the way,” making a required action sound incidental.
- Anecdotes about team satisfaction, contributor questions, and people breaking the rule obscure the operational requirements.
- “Such as `feat` or `fix`” does not define the complete set of accepted types.
- “Before you open a pull request” leaves unclear whether every commit needs a `Test plan` section or only a particular commit.
- The `CHANGELOG.md` detail explains release tooling but does not affect how a commit message should be drafted.

### Recommended structure

Create a `## Constraints` section near the top containing the three mandatory rules as direct statements.
Keep history, rationale, tooling behavior, and anecdotes under `## Context` or `## Background`.
Clarify the accepted commit types and the exact scope of the `Test plan` requirement.
