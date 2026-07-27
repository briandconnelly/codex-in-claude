# Scenario 6 — With-Skill Scoring

Date: 2026-07-11
Run: treatment with the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | One consolidated R2/R3 finding | PASS | Finding 1 uses R2 as primary and R3 as secondary. |
| 2 | Explain ambiguous strength and unverifiability | PASS | Finding 1 identifies both “Generally try” and the absence of observable safeguards. |
| 3 | Preserve policy as an author decision | PASS | Both alternatives retain an author-defined placeholder instead of inventing policy. |
| 4 | Separate minor R1 finding for history | PASS | Finding 2 moves the unchanged statement to Context and marks it minor. |
| 5 | Report one material and one minor finding | PASS | The summary reports material 1 and minor 1 without double-counting. |

Total: 5/5.

## Scored Output

## Findings

### 1. Ambiguous and unverifiable deletion guidance

- **Rule ID:** R2; also R3
- **Location:** `Rules`, first list item
- **Quoted text:** “Generally try to be careful when deleting customer records.”
- **Why it fails:** “Generally try to” does not establish whether the guidance is mandatory, a defeasible default, or nonbinding context.
  “Be careful” also identifies no observable safeguard, so compliance cannot be verified.
- **Severity:** Material
- **Suggested rewrite:** Author decision required.
  First define the observable safeguard intended by “careful,” then choose one of these semantic alternatives:
  - **Mandatory:** “Before deleting customer records, you must `<author-specified observable safeguard>`.”
  - **Default:** “Prefer `<author-specified observable safeguard>` before deleting customer records unless `<author-specified override condition>`.”
  - **Nonbinding context:** Move outside `Rules`: “Deleting customer records warrants particular care.”

### 2. Historical context placed in the rules section

- **Rule ID:** R1
- **Location:** `Rules`, second list item
- **Quoted text:** “The retention service was introduced during the 2024 storage migration.”
- **Why it fails:** This is discretionary historical context rather than a behavioral constraint, so it does not belong in the rules section.
- **Severity:** Minor
- **Suggested rewrite:** Move it unchanged to a context section:

```markdown
## Context

The retention service was introduced during the 2024 storage migration.
```

## Summary

- **By rule:** R1: 1; R2: 1; R3: 1; R4: 0; R5: 0.
- **By severity:** Material: 1; Minor: 1.

The document mixes one ambiguously binding, unverifiable instruction with one historical fact under `Rules`.
The deletion policy needs an author-defined, observable safeguard and explicit strength, while the migration history should be separated into context.
