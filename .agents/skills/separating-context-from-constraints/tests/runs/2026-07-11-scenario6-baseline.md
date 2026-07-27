# Scenario 6 — Baseline Scoring

Date: 2026-07-11
Run: baseline without the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | One consolidated R2/R3 finding | FAIL | The output uses no rule ids or consolidation. |
| 2 | Explain ambiguous strength and unverifiability | PASS | It calls the rule "optional-sounding and not testable." |
| 3 | Preserve policy as an author decision | FAIL | It proposes authorization, logging, recovery, and other safeguard categories. |
| 4 | Separate minor R1 finding for history | FAIL | It moves the history but supplies no R1 id or severity. |
| 5 | Report one material and one minor finding | FAIL | It supplies no counts or severities. |

Total: 1/5.

## Scored Output

- **Context misplaced as a rule:** “The retention service was introduced during the 2024 storage migration” is historical background.
  Move it to a `## Context` section.
- **Constraint is vague and weak:** “Generally try to be careful” is optional-sounding and not testable.
  Define mandatory safeguards and precise conditions for deleting customer records.
- **Important details are missing:** The document does not specify authorization, retention periods, confirmation requirements, audit logging, recovery procedures, or exceptions.

Suggested separation:

```markdown
## Context

- The retention service was introduced during the 2024 storage migration.

## Constraints

- Delete customer records only when [defined authorization and retention conditions] are satisfied.
- [Define required safeguards, logging, and recovery behavior.]
```
