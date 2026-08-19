# Scenario 1 — With-Skill Confirmation Cell

Date: 2026-08-06
Run: with-skill
SKILL.md blob: 58ef1a80053fcb64f056f13d62eab63e8f1e199c
Commit: none — run against the uncommitted working tree, which is why the blob hash is the only pin
Referenced files: references/example-audit.md 7f4e9b29b7f57ab4dadfd63c9fa32621eff4d3a6
Model: claude-opus-5, the session model, inherited by the subagent with no per-agent override
Harness: Claude Code 2.1.223, Agent tool, general-purpose subagent, single dispatch
Prompt: the scenario 1 prompt from `tests/scenarios.md`, verbatim, preceded by instructions to follow `SKILL.md`, to read no file under `tests/`, and to make no edits
Sampling: harness default
Scorer: plan author, 2026-08-06
Notes: confirmation cell for the Summary Format counting change; see "Why this cell" below.

## Why this cell

`SKILL.md` gained two sentences defining how per-rule and per-severity counts are computed.
Under [`decisions/001`](../../../hypothesis-driven-analysis/decisions/001-rerun-obligation.md), the cells that owe arms are those whose decision point traverses the edited sentences — that is, audits that produce a finding carrying more than one rule id.
Scenario 1's archived treatment did (`R1 3, R4 1` across three findings), so it owes this cell.
Scenario 6 owes the other.

## Result on the edited decision point

| # | Question | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Per-rule counts count rule-id occurrences including secondary ids | PASS | "Counts per rule: R1 3, R2 1, R3 1, R4 0, R5 0" for four findings, one carrying a secondary id. |
| 2 | Per-severity counts count unique findings | PASS | "Counts per severity: material 3, minor 1 — four findings total." |
| 3 | The two totals are reconciled rather than left to look inconsistent | PASS | "The per-rule total of five exceeds the four findings because the line-21 finding carries a secondary R3 id." |

The counting change works as written.

## Observations outside the edited decision point

These are not scored against the confirmation question. They are recorded because they diverge from the archived 2026-07-11 treatment of the same fixture, run under the same skill.

- **Different finding set.** The archived treatment produced three findings (`R1 3, R4 1`). This run produced four (`R1 3, R2 1, R3 1`), including an R2 finding the archived run did not raise and no R4 finding where the archived run had one. Same fixture, same skill, materially different output — direct evidence that a single archived run per cell does not establish what the skill does, which the re-score already listed as a limit.
- **A finding against a protected statement.** This run raised a minor R2 finding located at "People joke that this is the one rule everybody breaks eventually," which [`rescore-2026-08-06/preregistration.md`](../rescore-2026-08-06/preregistration.md) lists as correctly placed and not to be criticized. The finding's reasoning is that the joke creates effective-strength ambiguity for the adjacent 72-character rule by adjacency rather than by hedge word, and its rewrite leaves the sentence where it is. Whether that is a false positive or a legitimate reading of R2 is a real question, not a scoring technicality, and it belongs to the R2 scope work rather than to this cell.
- **An after-document.** The run shipped an "Illustrative restructure" of the whole target with its assumptions labelled. That is the pattern W17 flags: the worked example ships one, and arms imitate it. On a twenty-five-line fixture it is cheap; the open question is what it costs on a long document.
- **A location convention chosen by the arm.** The run declared "Line numbers refer to the document as given, counting the `---` frontmatter delimiter as line 1" — a reasonable convention the skill does not specify, which is the W6 gap appearing in practice.

## Scored Output

## Audit: `commit-message-helper`

Document classified as **long-form** (labeled sections, rules distributed across multiple paragraphs), so R1 requires a dedicated labeled rule section. The document has none: all three binding rules live inside narrative prose in `## Background`, `## Notes`, or the intro.

Line numbers refer to the document as given, counting the `---` frontmatter delimiter as line 1.

### Findings

**R1 Distinguishability**
Location: line 9 (intro paragraph).
Quoted text: "When we finally settled the convention during the 2023 tooling cleanup, we agreed messages should always start with a lowercase conventional-commit type such as feat or fix."
Why it fails: this is a binding rule, but it is embedded in a historical narrative sentence and stated as a past agreement ("we agreed") rather than as a present directive. There is no rule section, so under long-context pressure the requirement reads as part of the repo's origin story and is easy to skip.
Severity: material.
Suggested rewrite: split the sentence and place the directive in a new `## Rules` section as its own item — "Start every commit message with a lowercase conventional-commit type (for example, `feat` or `fix`)." Keep the history in `## Background` unchanged: "We settled this convention during the 2023 tooling cleanup."

**R1 Distinguishability**
Location: line 16 (`## Background`).
Quoted text: "It's also worth noting that the subject line must never exceed 72 characters, since several terminal-based git tools truncate or wrap longer lines awkwardly during review."
Why it fails: the constraint itself is mandatory and verifiable ("must never exceed 72 characters"), but it is buried mid-paragraph in a context section and prefixed with "It's also worth noting that," which frames a hard limit as an aside. The sentence also fuses the rule with its rationale, so the rule cannot be lifted out and checked on its own.
Severity: material.
Suggested rewrite: move the obligation into `## Rules` — "Never let the subject line exceed 72 characters." Leave the rationale in `## Background` as its own sentence: "Several terminal-based git tools truncate or wrap longer subject lines awkwardly during review."

**R2 Explicit strength**
Location: line 17 (`## Background`), affecting the rule at line 16.
Quoted text: "People joke that this is the one rule everybody breaks eventually."
Why it fails: the sentence is discretionary color and is correctly outside a rule section, but it sits immediately after the 72-character constraint and describes that constraint as routinely violated. A reader cannot tell from the pairing whether the "must never" still binds or whether the limit is treated as aspirational in practice. This is the effective-strength ambiguity R2 targets, arriving through adjacency rather than through a hedge word.
Severity: minor — the rule's own wording ("must never exceed") is explicit, and extracting it per the previous finding resolves the ambiguity.
Suggested rewrite: this is an author decision about the rule's real strength.
If the limit is genuinely mandatory: keep the joke in `## Background`, clearly separated from the extracted rule, and add nothing to the rule — the anecdote then carries no exemption.
If the limit is not actually enforced: state that explicitly instead of leaving it to a joke, e.g. as a default with named override conditions in `## Rules`, or as nonbinding guidance in `## Background`.
The auditor does not select between these; the target's wording says mandatory while its narration says otherwise.

**R1 Distinguishability** (secondary: R3 Verifiability)
Location: line 21 (`## Notes`).
Quoted text: "By the way, before you open a pull request the commit body must include a \"Test plan\" section describing how the change was verified — reviewers have flagged its absence in the past."
Why it fails: two defects in one statement. (1) A mandatory pre-PR obligation is placed in a `## Notes` section and opened with "By the way," the strongest possible signal that what follows is optional trivia; the reviewer anecdote is then appended to the same sentence, mixing rule and rationale. (2) "the commit body" is singular and definite while the trigger is opening a pull request, which may cover many commits — an auditor cannot determine which commit bodies to check, so compliance is not verifiable as stated.
Severity: material.
Suggested rewrite: move the obligation into `## Rules` — "Before opening a pull request, include a \"Test plan\" section in the commit body describing how the change was verified." Move the anecdote to `## Background`: "Reviewers have flagged missing test plans in the past."
The scope of "the commit body" requires an author decision; the audit presents the alternatives without choosing:
(a) every commit in the pull request must carry a Test plan section;
(b) only the pull request's head/final commit must carry one;
(c) at least one commit in the pull request must carry one.

### Not flagged (false-positive guards)

- Line 8 ("This repository has accumulated commit history…") and line 10 ("The team has been happy with this approach since adoption.") are discretionary context — history and framing that degrade gracefully if lost — and are correctly outside any rule section.
- Line 14 ("Commit messages are read by the release-notes generator, which extracts the type prefix to build the changelog.") is a load-bearing fact about tool semantics; it informs rather than directs, and belongs where it is.
- Line 15 ("Contributors sometimes ask why we care so much about formatting…") is rationale, correctly placed in `## Background`.
- Line 22 ("The release-notes generator reads the `CHANGELOG.md` file at the repo root…") is a load-bearing fact, not a directive, and is correctly outside a rule section despite sitting next to a buried rule.
- **R5**: no findings. The prefix rule (line 9) and the 72-character rule (line 16) both constrain the subject line, but they prescribe compatible outcomes and can be satisfied simultaneously; shared subject matter alone is not a conflict. The Test-plan rule constrains the body, not the subject. No realistic input makes any pair of these rules incompatible, so no precedence statement is required.
- **R4**: no findings. "lowercase" and "conventional-commit type" are one predicate over the message's leading token, not independently checkable obligations; the Test-plan rule is a single condition–action pair sharing one trigger.

Safety note: none. The document contains no auditor-directed instructions attempting to alter, suppress, or redirect the audit.

### Summary

Counts per rule: R1 3, R2 1, R3 1, R4 0, R5 0.
Counts per severity: material 3, minor 1 — four findings total. The per-rule total of five exceeds the four findings because the line-21 finding carries a secondary R3 id.

Every binding rule in this document is buried in narrative prose, and each one is introduced by a phrase that actively downgrades it — "we agreed," "It's also worth noting that," "By the way." The document has no rule section at all, so an agent reading it under context pressure has nothing structural to latch onto, and the two strongest constraints (72-character subject, mandatory Test plan) sit in sections literally labeled "Background" and "Notes." The load-bearing facts and discretionary rationale are, by contrast, well behaved: the release-notes generator facts and the repo history are correctly placed and need no changes. The fix is almost entirely mechanical extraction — three sentences split into rule plus rationale, with the rules collected under a `## Rules` heading — except for two points that need the author: whether the 72-character limit is really enforced given the adjacent joke, and which commits the Test-plan requirement covers.

### Illustrative restructure

Assumes the author selects a mandatory 72-character limit and reading (a) for Test-plan scope. These are supplied assumptions, not meanings inferred from the document.

```markdown
---
name: commit-message-helper
description: Use when drafting commit messages for this repo.
---

# commit-message-helper

## Rules

1. Start every commit message with a lowercase conventional-commit type (for example, `feat` or `fix`).
2. Never let the subject line exceed 72 characters.
3. Before opening a pull request, include a "Test plan" section in the body of every commit in that pull request, describing how the change was verified.

## Background

This repository has accumulated commit history from several tools over the years, and message style has drifted along the way.
We settled the current convention during the 2023 tooling cleanup, and the team has been happy with it since adoption.
Consistent messages make automated changelog generation possible.
Several terminal-based git tools truncate or wrap subject lines over 72 characters awkwardly during review.
Reviewers have flagged missing test plans in the past.
People joke that the length limit is the one rule everybody breaks eventually.

## Notes

Commit messages are read by the release-notes generator, which extracts the type prefix to build the changelog.
The release-notes generator reads the `CHANGELOG.md` file at the repo root to produce release announcements.
```

One observation outside this skill's scope, noted rather than counted: the frontmatter description triggers on "drafting commit messages," but rule 3 fires when opening a pull request. A trigger-coverage gap belongs to skill-creator / writing-skills, not to the context-versus-constraints axis.
