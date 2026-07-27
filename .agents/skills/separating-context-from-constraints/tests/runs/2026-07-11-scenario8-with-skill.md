# Scenario 8 — With-Skill Scoring

Date: 2026-07-11
Run: treatment with the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | One material R5 finding | PASS | The output reports one material R5 finding for EU uploads. |
| 2 | Attach the finding to both statements | PASS | The location and quotation cover lines 5–6 together. |
| 3 | Present both precedence policies | PASS | EU-wins and global-wins rewrites are both present. |
| 4 | Do not silently select specific-over-general | PASS | The summary requires an author decision before a definitive rewrite. |

Total: 4/4.

## Scored Output

## Findings

### 1. Missing precedence for EU uploads

- **Rule id:** R5 Reachable precedence
- **Location:** `## Rules`, both list items
- **Quoted text:** “Always use the global endpoint for uploads.” / “For EU customer uploads, use the EU endpoint.”
- **Why it fails:** An EU customer upload satisfies both rules, but the rules prescribe different endpoints and do not state which one takes precedence.
- **Severity:** Material
- **Suggested rewrite:** Author decision required.
  - **EU rule takes precedence:** “Use the global endpoint for all uploads except EU customer uploads.
    For EU customer uploads, use the EU endpoint.”
  - **Global rule takes precedence:** “Use the global endpoint for every upload, including EU customer uploads.”
  - **If the endpoints are not actually alternatives:** Define their distinct roles or selection conditions so that an EU upload cannot trigger incompatible endpoint choices.

## Summary

- **R1:** 0
- **R2:** 0
- **R3:** 0
- **R4:** 0
- **R5:** 1
- **Material:** 1
- **Minor:** 0

The binding rules are clearly separated, mandatory, atomic, and verifiable, but their overlap leaves endpoint selection undefined for EU customer uploads.
