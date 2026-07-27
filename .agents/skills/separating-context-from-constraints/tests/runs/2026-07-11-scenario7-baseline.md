# Scenario 7 — Baseline Scoring

Date: 2026-07-11
Run: baseline without the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | One material R4 finding | FAIL | The output supplies no R4 id or material severity. |
| 2 | Retain the shared trigger | FAIL | The listed constraints drop “Before publishing a package.” |
| 3 | Present the grouping meaning as an author decision | FAIL | It asks for clarification but does not present the required alternatives as an author decision. |
| 4 | Do not discard or invent grouping semantics | PASS | It identifies possible meanings without selecting a mechanism. |

Total: 1/4.

## Scored Output

## Audit

The document mostly separates context from constraints: `package-publisher` identifies the subject, while the bullet states the required behavior.
The phrase “Before publishing a package” is contextual scope embedded in the rule and would be clearer as a separate workflow or applicability statement.
The constraints are:

- Validate the artifact checksum.
- Sign the artifact.
- Upload provenance.
- Perform these actions “in one operation.”

The final constraint is ambiguous because validation, signing, and uploading are inherently distinct steps, and “one operation” does not define whether it means one command, one workflow, or transactional atomicity.
Clarify the context separately and define the required execution semantics, especially failure and rollback behavior.
