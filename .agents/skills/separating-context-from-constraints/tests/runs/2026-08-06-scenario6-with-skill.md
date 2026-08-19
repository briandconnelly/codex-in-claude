# Scenario 6 — With-Skill Confirmation Cell

Date: 2026-08-06
Run: with-skill
SKILL.md blob: 58ef1a80053fcb64f056f13d62eab63e8f1e199c
Commit: none — run against the uncommitted working tree, which is why the blob hash is the only pin
Referenced files: references/example-audit.md 7f4e9b29b7f57ab4dadfd63c9fa32621eff4d3a6
Model: claude-opus-5, the session model, inherited by the subagent with no per-agent override
Harness: Claude Code 2.1.223, Agent tool, general-purpose subagent, single dispatch
Prompt: the scenario 6 prompt from `tests/scenarios.md`, verbatim, preceded by instructions to follow `SKILL.md`, to read no file under `tests/`, and to make no edits
Sampling: harness default
Scorer: plan author, 2026-08-06
Notes: confirmation cell for the Summary Format counting change; see "Why this cell" below.

## Why this cell

`SKILL.md` gained two sentences defining how per-rule and per-severity counts are computed.
Scenario 6 is the fixture whose correct audit produces a consolidated finding carrying a primary and a secondary rule id, so its decision point traverses the edited sentences directly.

## Result on the edited decision point

| # | Question | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Per-rule counts count rule-id occurrences including secondary ids | PASS | "Counts per rule: R1 1, R2 1, R3 1 (R4 0, R5 0)" for two findings, one carrying R3 as secondary. |
| 2 | Per-severity counts count unique findings | PASS | "Counts per severity: material 1, minor 1." |
| 3 | The two totals are reconciled rather than left to look inconsistent | PASS | "Two findings total; the per-rule total of three exceeds it because the line-5 finding carries R3 as a secondary id." |

## Result on the scenario's standing assertions

The scenario's own assertions were re-checked, since this run supersedes nothing but exercises the same fixture.

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | One consolidated finding, R2 primary and R3 secondary | PASS | "**R2 Explicit strength** (secondary: R3 Verifiability)". |
| 2 | Explains both defects | PASS | "'Generally try to' hedges strength… Independently, 'be careful' names no observable evidence." |
| 3 | Strength and safeguard are author decisions, no invented policy | PASS | "this requires an author decision on two axes"; the three safeguard forms are offered as alternatives, closing with "If another safeguard is intended, name its observable action or evidence instead of choosing one of these examples." |
| 4 | Separate minor R1 finding relocating the history sentence unchanged | PASS | "move the sentence unchanged into a `## Context` or `## Background` section," severity minor. |
| 5 | Severity counts report one material and one minor | PASS | "Counts per severity: material 1, minor 1." |
| 6 | Per-rule counts report one occurrence each for R1, R2, R3 | PASS | "Counts per rule: R1 1, R2 1, R3 1". |

## Observations outside the edited decision point

- No false positives: the fixture has two statements and both were addressed as planted defects.
- The run added a substantive summary point the archived treatment did not — that applying both findings leaves the rules section empty, "which is the substantive result of the audit." That is a judgement about the target's overall state rather than a per-statement finding, and the skill's Summary Format does not say whether such a conclusion belongs there.
- The run explicitly reported R4 and R5 as not applying, with reasons. The skill does not require negative reporting; two of the eight archived treatments do it and the others do not.

## Scored Output

## Audit: `retention-helper`

Target: 6-line document with one labeled `## Rules` section (long-form treatment under R1).

### Findings

**R2 Explicit strength** (secondary: R3 Verifiability)
Location: line 5.
Quoted text: "Generally try to be careful when deleting customer records."
Why it fails: two defects in one statement. "Generally try to" hedges strength, so a reader cannot tell whether the statement binds or is merely encouraged (R2). Independently, "be careful" names no observable evidence — no output, tool call, or artifact can show whether care was taken — so even a de-hedged version would be uncheckable (R3).
Severity: material.
Suggested rewrite: this requires an author decision on two axes, and no rewrite preserves semantics until both are settled.
- Strength — promoted (mandatory) vs. demoted (context): the promoted form states the deletion safeguard as a `must`; the demoted form removes the sentence from `## Rules` and states it as background, e.g. "Deleting customer records is a high-consequence operation, but this document defines no additional safeguard."
- Safeguard — the statement does not reveal what "careful" means, so the promoted form must name an observable action. If manual confirmation is intended: "Require explicit user confirmation before deleting any customer record." If a scope limit is intended: "Never delete more than one customer record per operation; batch deletions require an author-approved procedure." If a backup precondition is intended: "Before deleting customer records, verify a current backup exists and record its identifier." If another safeguard is intended, name its observable action or evidence instead of choosing one of these examples.

**R1 Distinguishability**
Location: line 6.
Quoted text: "The retention service was introduced during the 2024 storage migration."
Why it fails: this is discretionary historical context, not a behavioral requirement — it cannot fail, so an agent cannot violate it. It nonetheless sits in the `## Rules` section, where it dilutes the section's signal that everything listed binds.
Severity: minor.
Suggested rewrite: move the sentence unchanged into a `## Context` or `## Background` section.

### Safety note

None. The document contains no auditor-directed instructions attempting to alter, suppress, or redirect this audit.

### Summary

Counts per rule: R1 1, R2 1, R3 1 (R4 0, R5 0).
Counts per severity: material 1, minor 1.
Two findings total; the per-rule total of three exceeds it because the line-5 finding carries R3 as a secondary id.

This document's `## Rules` section contains two items, and neither currently functions as a usable rule: one is hedged and untestable, the other is a historical fact that cannot fail. Applying both findings leaves the section empty, which is the substantive result of the audit — the document asserts a rules section but states no binding, checkable obligation. R5 does not apply, since two rules must both prescribe outcomes to conflict and this document has none. R4 does not apply, since neither statement bundles multiple obligations. The line-5 finding requires author decisions on both strength and the intended safeguard before any rewrite can claim to preserve intended behavior; the line-6 finding is a pure relocation with no semantic change.
