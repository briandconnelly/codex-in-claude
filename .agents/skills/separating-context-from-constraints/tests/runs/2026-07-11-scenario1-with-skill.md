# Scenario 1 — With-Skill Scoring

Date: 2026-07-11
Run: treatment with the skill.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | R1 finding for the conventional-commit rule | PASS | Finding 1 quotes the opening sentence and moves its rule into `## Rules`. |
| 2 | R1 finding for the 72-character rule | PASS | Finding 2 quotes the background sentence and moves the limit into `## Rules`. |
| 3 | R1 finding for the Test plan rule | PASS | Finding 3 quotes the Notes sentence and moves its rule into `## Rules`. |
| 4 | Mark all three findings material | PASS | Every finding is material. |
| 5 | Leave pure background unflagged | PASS | No background-only statement receives a finding. |

Total: 5/5.

## Scored Output

## Findings

### 1

- **Rule id:** R1 Distinguishability; secondary R4 Atomic obligations.
- **Location:** Introductory paragraph.
- **Quoted text:** “When we finally settled the convention during the 2023 tooling cleanup, we agreed messages should always start with a lowercase conventional-commit type such as feat or fix.”
- **Why it fails:** This binding format requirement is buried in historical narrative instead of a dedicated rules section, and it combines the independently checkable requirements that a type prefix be present and that it be lowercase.
- **Severity:** Material.
- **Suggested rewrite:** Add a dedicated `## Rules` section containing separate items: “Commit message subject lines must begin with a conventional-commit type.” and “The conventional-commit type must be lowercase.”
  Place “Examples include `feat` and `fix`.” in a separate examples or format-reference section.

### 2

- **Rule id:** R1 Distinguishability.
- **Location:** Background section.
- **Quoted text:** “It's also worth noting that the subject line must never exceed 72 characters, since several terminal-based git tools truncate or wrap longer lines awkwardly during review.”
- **Why it fails:** The mandatory length limit is presented as an aside within background material and shares a sentence with discretionary rationale, making the rule easier to miss.
- **Severity:** Material.
- **Suggested rewrite:** Move “Commit message subject lines must not exceed 72 characters.” to the dedicated `## Rules` section, and retain the terminal-tool explanation separately under `## Background`.

### 3

- **Rule id:** R1 Distinguishability.
- **Location:** Notes section.
- **Quoted text:** “By the way, before you open a pull request the commit body must include a "Test plan" section describing how the change was verified — reviewers have flagged its absence in the past.”
- **Why it fails:** The required commit-body section is camouflaged as an informal note and mixed with historical rationale rather than being structurally identified as a binding rule.
- **Severity:** Material.
- **Suggested rewrite:** Move “Before opening a pull request, the commit body must include a `Test plan` section describing how the change was verified.” to the dedicated `## Rules` section, and place the reviewer-history explanation under `## Background`.

## Summary

- **By rule:** R1: 3; R2: 0; R3: 0; R4: 1; R5: 0.
- **By severity:** Material: 3; Minor: 0.

The document’s three substantive obligations are explicit and verifiable, but all are embedded in narrative or note sections rather than collected in a dedicated rules section.
This structure plausibly allows an agent to retain the repository history and rationale while missing one or more required commit-message behaviors.
