# 003 — Omitting the offending value: one policy, and what disclosure buys

Decided 2026-07-29.
Prompted by issue #132, which was surfaced by an independent evaluation of an agent-friendly-mcp audit of a real server (codex-in-claude).

## Question

`[6.offending-value]` ("Include the offending value when safe. Redact when sensitive; never omit silently.") and `[6.details-field]` ("Include `value` only when safe and meaningful") stated different strengths for one obligation, and neither said whether a *documented* blanket omission of `value` is conformant.
codex-in-claude ships exactly that: its published error-envelope resource discloses that `value` is never emitted, because a received value could be a secret and best-effort redaction cannot reliably catch a plain one.
The audit that hit this charged it as a plain defect and remediated with "add `value`, redacted when sensitive" — pushing toward the risk review-workflow.md grades Critical ("secrets leaked in error payloads") on parameters whose sensitivity is undeterminable.

## Position adopted

`[6.offending-value]` is the single home for the emission policy (the envelope table's `details` row, `[6.details-field]`, and design-workflow.md now cite it):

1. Emit `value` when the received value itself is known-safe — server-minted (a handle or URI the server issued), or one of the schema's published enum members arriving where it is invalid.
   This deliberately narrows the issue's suggested example ("enum/format violations of non-secret parameters"): a value that *failed* a format check is arbitrary text — exactly where a mispasted secret lives — so schema-level innocence of the parameter never makes the received value safe.
2. Reliable redaction is a precondition for echoing a potentially sensitive value; best-effort pattern matching does not qualify, because a plain secret matches no pattern.
3. A blanket omission for parameters whose received values' sensitivity the server cannot reliably determine is conformant when disclosed on an agent-visible surface (error-envelope schema/resource or capability summary) — that disclosure is what "never omit silently" requires; an undisclosed omission is the defect.

review-workflow.md's first-repair probe now tells reviewers to check for a disclosed omission policy before charging `[6.offending-value]`, and that "echo the value, redacted" is not a valid remediation where sensitivity is undeterminable.

The safety asymmetry decided it: the cost of omitting a safe value is one extra round trip (the agent re-derives what it sent); the cost of echoing a mispasted secret is a leak graded Critical.
The rule therefore keys emission to the *received value's* safety, not the parameter's schema-level innocence.

## Verification

Baseline probe (3 reps, neutral conformance-verdict prompt on the old text, disclosed-blanket-omission scenario): 3/3 charged nonconformant under `[6.offending-value]`, and 3/3 remediated with "emit `value`, redact when sensitive" — the exact Critical-graded push; one rep explicitly ruled that disclosure cannot satisfy the rule.
Fix probe, first wording (3 reps): 2/3 conformant; 1 rep read the "enum/format violations of a parameter that cannot carry a secret" example as making a failed format check known-safe, and charged the disclosed policy with covering the wrong category — divergent interpretations meant the wording was not binding.
Refactor: replaced that example with the value-level rule above ("safety is a property of the value, not of the schema field").
Refactor probe (3 reps): 3/3 conformant with converged reasoning (failed format check → not known-safe → disclosed blanket omission applies), remediation `none`.
Over-correction control (2 reps on the refactored text): the same omission with no disclosed policy anywhere — 2/2 nonconformant, remediating "disclose the policy or emit known-safe values," with no rep pushing "echo the value, redacted."
