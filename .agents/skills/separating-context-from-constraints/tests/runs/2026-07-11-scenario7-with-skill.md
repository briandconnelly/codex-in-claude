# Scenario 7 — With-Skill Scoring

Date: 2026-07-11
Run: treatment with the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | One material R4 finding | PASS | The output reports one material R4 finding. |
| 2 | Retain the shared trigger | PASS | The phase rewrite begins “Before publishing a package.” |
| 3 | Present the grouping meaning as an author decision | PASS | It presents workflow-phase and transaction-or-command alternatives. |
| 4 | Do not discard or invent grouping semantics | PASS | It requires the author to name any literal mechanism. |

Total: 4/4.

## Scored Output

## Findings

**R4 Atomic obligations**
Location: line 5.
Quoted text: “Before publishing a package, validate its checksum, sign the artifact, and upload its provenance in one operation.”
Why it fails: this bundles three independently checkable obligations into one statement, while “in one operation” does not establish whether they must occur in one transaction, one command, or merely one workflow phase.
Severity: material.
Suggested rewrite: this requires an author decision about what “one operation” means.
If it means one workflow phase: “Before publishing a package, complete this preparation phase: (1) Validate the package checksum. (2) Sign the artifact. (3) Upload its provenance.”
If it means one transaction or command: name that mechanism and require it to produce all three observable results before publication.

## Summary

Counts per rule: R1 0, R2 0, R3 0, R4 1, R5 0.
Counts per severity: material 1, minor 0.

The document clearly distinguishes its mandatory rule from context, but the rule combines three independently verifiable obligations and leaves the required meaning of “one operation” undefined.
