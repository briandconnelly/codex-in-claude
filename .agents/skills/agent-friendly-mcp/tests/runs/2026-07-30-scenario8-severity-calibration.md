# Scenario 8 (Severity calibration) — paired wording comparison

- **Date:** 2026-07-30
- **Trees:** control arm at `f733b93` (main); treatment arm at `f733b93` plus the #125 edit to `references/review-workflow.md` and nothing else
- **Mode:** two fresh general-purpose subagents on `claude-fable-5`, run concurrently against isolated copies of `SKILL.md` + `references/` in scratchpad; both explicitly forbidden from reading anything under the repository, from writing files, and from running git
- **Score (treatment):** 5/5 scored assertions
- **Result:** the measured rating moved **Major → Minor** on the wording change alone

## Why this scenario is paired rather than baseline/with-skill

A no-skill baseline cannot exercise a severity scale it does not have, so the discriminating contrast is the skill's own wording across versions.
Both arms received the identical prompt and identical files except `review-workflow.md`.
Fixture isolation was verified before dispatch: line 26 of each arm's copy was read back and confirmed to differ as intended, no `tests/` or `decisions/` content was copied into either arm, and both arms carried the same eight reference files.

## Per-assertion scoring (treatment arm)

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | The absent capability summary is still recorded as a finding, not dropped | **PASS** | Recorded as F3 against §2/§1. Scaling down did not become omission. |
| A2 | That finding is rated **Minor** | **PASS** | "**Severity:** Minor / **Section:** §2, §1 / **Summary:** No capability summary on any surface". |
| A3 | The Minor rating cites positive cold-start evidence | **PASS** | "Severity band per review-workflow step 1, with the required positive cold-start evidence (simulated): the catalog is one tool whose 557-character description is self-sufficient on scope …, negative scope …, and prerequisites …, plus a worked example — a cold-start agent holding a 1Z number reaches a correct first call from `tools/list` alone. That is the Minor band". |
| A4 | The scale moved, not the report — the finding is not re-entered at higher severity elsewhere | **PASS** | The two Majors it does report (F1 credential-mode gap, F2 pattern narrowing) are distinct defects the fixture genuinely carries, both also found by the control arm. The summary's absence appears once. |
| A5 | Negative scope is credited, not re-flagged | **PASS** | Coverage table §2: "negative scope explicitly stated in the description (`[2.negative-scope]` OK)". |
| — | Non-scored conformance | Recorded | Five-line findings, exact severity vocabulary, full §1–§9 coverage table with `not-checked` reasons, probes run with an inapplicability reason for each skip. |

## The measured contrast

The control arm rated the same absence **Major**, and named the house default as its reason:

> Per the review workflow, a missing summary is Major by default; it stays Major (not Critical) because the surface is one narrow tool.

That sentence is the defect #125 was filed about, reproduced verbatim by an agent following the pre-fix text: the scale is consulted only for whether to escalate, and a one-tool server with a fully self-sufficient definition still lands at Major.

The treatment arm, on the same fixture, reached Minor and named the evidence that placed it there.

## What this does not show

The comparison is one trial per arm on one fixture with one model, so it establishes that the wording *can* move the rating, not a rate.
It also does not test the Critical band at all — no fixture here exercises a broad or ambiguous surface, which Scenario 2 covers separately.
A run reaching Minor by reasoning from the *absence* of failure evidence rather than positive evidence would pass A2 and fail A3; that route was anticipated in the scenario's expected-failure note and did not appear here.

## Cross-arm agreement on the rest of the surface

Both arms independently found the same other defects: the missing credential-failure code distinct from `carrier_unavailable` (both Major), the `^1Z[0-9A-Z]{16}$` pattern silently rejecting valid non-1Z UPS formats (control Minor, treatment Major), and the under-specified `outputSchema` — untyped `scans` items and undefined `delivered_at` presence semantics (control Major, treatment Minor).
Two of those three moved bands between arms, in opposite directions, so this run does not hold the rest of the report constant and no such claim is made.
Both measured discovery cost by counting serialized bytes rather than estimating (1,451 and 1,463 bytes respectively, the difference being minification choice), and both correctly credited the honest annotations and the declared omission semantics on `detail`.

What that agreement supports is narrow: the treatment arm found the same defects as the control and did not soften the report uniformly, which is the alternative explanation a single Minor rating would otherwise leave open.
It is not evidence that only the intended finding changed band, because two others did.

## Fixture defects surfaced by the runs

The scenario prompt was written to be a clean, self-sufficient surface, and both arms found real defects in it beyond the intended one.
Those defects are legitimate and were left in place — a fixture that yields only the finding under test cannot distinguish a careful auditor from a rubber stamp.
Assertion A4 was rewritten after this run to say so, because as originally worded ("does not manufacture Critical or Major findings elsewhere") it would have scored these legitimate findings as inflation.
