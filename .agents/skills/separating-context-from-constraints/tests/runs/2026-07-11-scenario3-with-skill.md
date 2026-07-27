# Scenario 3 — With-Skill Scoring

Date: 2026-07-11
Run: final treatment with the skill after one rewrite-option iteration.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Report an exact R2 finding | PASS | The finding quotes the hedged statement and explains the ambiguous strength. |
| 2 | Present promoted and demoted rewrites | PASS | Mandatory and nonbinding-context alternatives are both present. |
| 3 | Mark the choice as an author decision | PASS | The rewrite explicitly names an author decision. |
| 4 | Do not silently select an interpretation | PASS | Three labeled alternatives are presented without selection. |
| 5 | Leave the other two rules unflagged | PASS | The summary calls both other rules explicit, atomic, and verifiable. |

Total: 5/5.

## Iteration Note

The first treatment substituted a binding default for the required demoted nonbinding alternative.
The rewrite contract was tightened so a defeasible default cannot replace the promoted-versus-demoted pair.

## Scored Output

## Findings

**R2 Explicit strength**

Location: line 11.
Quoted text: “Generally prefer rebase over merge.”
Why it fails: “Generally” leaves unclear whether this is a binding default, an absolute requirement, or nonbinding guidance, and no override conditions are identified.
Severity: material.
Suggested rewrite: this is an author decision among the following meanings.

- Mandatory: “Use rebase instead of merge.”
- Binding default: “Prefer rebase over merge unless [specify the conditions that permit merging].”
- Nonbinding context: move the statement outside `## Rules` and write, “Rebase is commonly used instead of merge, but this document does not require it.”

## Summary

Counts per rule: R1 0, R2 1, R3 0, R4 0, R5 0.
Counts per severity: material 1, minor 0.

The document cleanly isolates its behavioral rules in a dedicated section, and the force-push and fixup-commit rules are explicit, atomic, and verifiable.
The sole defect is the ambiguous strength of the rebase preference, which requires the author to decide whether it is mandatory, a binding default with stated exceptions, or merely contextual guidance.
