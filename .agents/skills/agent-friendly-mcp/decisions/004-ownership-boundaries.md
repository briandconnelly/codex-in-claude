# 004 — Ownership boundaries after the 2026-08-15 three-model review

Decided 2026-08-15.
Triggered by finding F4 of the 2026-08-15 three-model skill review (Claude, Codex, and Kimi; conducted in-session and not archived in-repo): three absolute non-restatement claims did not match the corpus, and two live drift instances (F1/F2 in SKILL.md's Spec Baseline, F3 in examples.md) showed the risk is real.

## Question

Where do spec-revision facts, the native-vs-convention rule, and rule summaries live, and what may other files restate?

## Decision

Keep current homes; narrow the claims to match them.

- `contract-checklist.md` owns behavioral rules by stable id.
- `SKILL.md` owns two named exceptions: the Spec Baseline revision-level facts (revision identity, status, tasks-extension caveat, deprecations) and the native-vs-convention rule with its native-field inventory (per decisions/001 impact matrix row 1).
- `native-wire-shapes.md` owns exact wire shapes and may restate protocol obligations, per its own line-5 carve-out.
- `decisions/` records are dated historical evidence, never current authority.
- Rule text and wire reference may restate the mechanics they govern; summaries (Checklist Map, workflow steps) cite rule ids wherever they compress a rule.
- The two native-field enumerations in `references/examples.md` (ex§5 Prompt, ex§8 Tool) are deliberate synchronized copies of the SKILL.md inventory, pinned by `check_fact_sync` probes rather than replaced with pointers.
- `tests/check_fact_sync.py` trips on the two demonstrated drift modes (cache-hint carriers, notification routing, native-field inventories).

## Rejected

Moving the native-vs-convention rules into checklist ids: three files cite "the native-vs-convention rule in `SKILL.md`" as a stable reference, the id grammar is bound to checklist sections, and the move would re-home a surface decisions/001 assigned to SKILL.md — churn with no drift-protection gain over the sync check.
