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
#338 exists precisely because agents were choosing the synchronous tool for work that exceeded its deadline and losing the partial run; the
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
- `tests/test_wire_size.py` pins the resulting size so drift is a reviewed decision.
- Revisit only if an eval shows agents selecting correctly from a `mode` parameter at the rate
  they currently select `_async` by name.
