# ADR 0001: Keep sync and `_async` tools as separate MCP tools

**Status:** Accepted (2026-07-26)

## Context

The 2026-07-26 agent-friendliness audit measured `tools/list` at 79,242 bytes / ~19.5k tokens
across 17 tools.
The largest single reduction available is collapsing each sync/`_async` pair
(`codex_consult`/`codex_consult_async`, and the review and delegate pairs) into one tool with a
`mode: "sync" | "background"` discriminator.
Measured saving: ~17,079 bytes, about 21% of the discovery surface.

## Decision

Keep them separate.

## Rationale

The separate tool *name* is the selection signal.
Issue #338 exists precisely because agents were choosing the synchronous tool for work that exceeded its deadline and losing the partial run; the
fix was to make `_async` visible at selection time, in the tool list, before any argument is
chosen.
A `mode` parameter moves that decision from tool selection into argument selection, where
it is one defaulted field among a dozen — the failure #338 fixed would recur.

The collapse is also the only breaking change in the audit's remediation set: it removes three
registered tool names, requiring a deprecation alias window, and it rewrites `AsyncLifecycle`,
the `commands/` slash prompts, the bundled skills, and roughly thirty tests.

## Consequences

- The ~17 KB stays on the wire.
  Discovery cost is instead reduced by compaction that does not touch tool granularity:
  - `tools/list` went 79,242 → 77,561 bytes: a net −1,681 B (−2.1%).
    Compression saved 2,331 B; adding the `detail` parameter and its description cost 650 B back.
  - `codex_capabilities`' own default response went 21,167 → 10,885 bytes, a −49% cut — but that is paid only by clients that call the tool, not by every client the way `tools/list` is.
  - Parameter rationalization (audit-2 F2): only `extra_context` moved to the `codex://params` resource, and `idempotency_key` was compressed in place.
    Three other parameters (`workspace_root`, `model`, `isolation`) were measured and deliberately **not** registered, because their current descriptions are already terse enough that adding the required `codex://params` pointer made them longer.
- `tests/test_wire_size.py` budgets the resulting size — it asserts a ceiling, not an exact
  value — so growth stays within the headroom and exceeding it is a reviewed decision.
- Revisit only if an eval shows agents selecting correctly from a `mode` parameter at the rate
  they currently select `_async` by name.

## Postscript (2026-07-28)

Every byte figure above was measured on 2026-07-26, when this decision was made, and is left
unedited as the historical record.
Later work in the same pre-0.16.0 window has already outgrown them, so do not read them as current.

This postscript deliberately restates no replacement numbers: a copied figure is exactly what went
stale here, and a second copy would go stale the same way.
Where to look instead, and what each source actually promises:

- `tests/test_wire_size.py` is the only mechanically enforced figure — it asserts a `tools/list`
  byte **ceiling**, and carries a dated log of each measurement that moved it.
  Because it enforces a ceiling, the payload can drift below the budget without the log changing;
  re-measure with the test's own method for an exact current size.
- ADR 0003 and `docs/REFERENCE.md` carry `codex_capabilities` response figures, but these are
  decision-time and explicitly indicative measurements, not current ones — `REFERENCE.md` says so
  in place.

The later growth does not change the decision.
The exact post-compaction totals were never its deciding criterion; it turns on selection
reliability (#338), and the figures record what that reliability cost at the time.
