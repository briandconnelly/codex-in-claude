# Scenario 8 — Baseline Scoring

Date: 2026-07-11
Run: baseline without the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | One material R5 finding | FAIL | The conflict is noticed but receives no R5 id or severity. |
| 2 | Attach the finding to both statements | PASS | The opening explanation quotes both rules as one conflict. |
| 3 | Present both precedence policies | FAIL | Only the EU-specific policy is presented. |
| 4 | Do not silently select specific-over-general | FAIL | The suggested revision silently makes the EU rule win. |

Total: 1/4.

## Scored Output

The rules conflict: “Always use the global endpoint” forbids the EU endpoint required by the next rule.
The document also mixes a default behavior with an exception without defining precedence.

Suggested revision:

```markdown
# upload-router

## Context

EU customer uploads require regional routing.

## Constraints

- Use the EU endpoint for EU customer uploads.
- Use the global endpoint for all other uploads.
```
