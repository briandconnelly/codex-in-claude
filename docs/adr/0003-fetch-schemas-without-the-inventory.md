# ADR 0003: Fetch schemas without the tool inventory

**Status:** Accepted (2026-07-27)

## Context

`codex_capabilities(include_schemas=[...])` is the documented fallback for **resource-blind** clients
— those that cannot read the `codex://` schema resources.
It returned the requested schema *plus the entire tool inventory*, so a client that had already
cached the inventory re-paid for it on every schema fetch.

Measured at `schema-62` (indicative; these move with the tool inventory):

| | `detail="summary"` (default) | `detail="full"` |
|---|---|---|
| bare payload / per-fetch overhead | 11,109 B | 21,391 B |
| of which `tool_details` | 7,306 B (65.8%) | — |

Issue #339 proposed a `schemas_only` boolean projecting the response to six fields.
Three findings, each checked rather than assumed, reshaped that:

- **A six-field projection violates the server's own published contract.** Both
  `CAPABILITIES_SCHEMA` (the tool's `outputSchema`) and `CAPABILITIES_RESULT_SCHEMA` reject it —
  `'transport' is a required property`. Confirmed with a control showing the same validator accepts
  today's full payload.
- **`tool_details` alone is free to drop.** It carries `default_factory=list`, so Pydantic leaves it
  out of `required` in both schemas. Omitting just that field needs **no schema change** and
  captures 7,322 B — 69% of what the full projection would have saved.
- **A new success branch would blow a deliberate guard.**
  `test_loosened_schemas_stay_under_byte_budget` caps `CAPABILITIES_SCHEMA` at 2,000 B and it sits
  at 1,968 B. A slim branch (~650 B) breaks a budget #242/#345 installed to stop exactly that
  regrowth — and it would be paid in every client's `tools/list`, the same currency the change is
  trying to save.

## Decision

Add **`detail="contracts"`** to `codex_capabilities`, on a **tool-local** `CapabilitiesDetail`
Literal. It omits `tool_details` and returns every other field unchanged.

Four consequences of that shape:

1. **No schema change**, because `tool_details` is already non-required in both published schemas.
2. **The shared `Detail` Literal stays two-valued.** It feeds consult, review, delegate, and the two
   job result-readers, where `contracts` is meaningless; extending it would widen five unrelated
   tools' accepted input.
3. **No invalid state, so no error path.** `codex_capabilities` constructs no error envelope
   anywhere today. A boolean would have created a `schemas_only` × `include_schemas` cross-product
   whose invalid corner needed the tool's first `invalid_arguments` — and a synthesized `Meta` for a
   tool with no workspace and no run.
4. **`detail="contracts"` alone is a legitimate call**: a 3,787 B `fingerprint` recheck for cache
   revalidation, which costs 11,109 B today.

Versioning: `FINGERPRINT` `schema-61` → `schema-62`; **not breaking**; `RESULT_FORMAT` stays at `7`.

## Rejected alternatives

**A dedicated `codex_contracts` tool** (the initial Codex design recommendation). Contract-cleaner in
isolation — its own closed result model, no cross-parameter states — but a new tool is charged to
*every* client's `tools/list`, every session, including the majority that read the `codex://`
resources and would never call it. A minimal entry is ~2.2 KB against ~1.0 KB for this change, and it
also adds a `ToolCapability` record to the very payload #339 set out to shrink, plus a
`_TOOL_ERROR_CODES` entry and selection-space growth. #345 had just slimmed that catalog
deliberately.

**A `schemas_only` boolean.** Superseded by the three findings above; it also runs against
`agent-friendly-mcp` §8, which prescribes "an explicit detail toggle … not a free parameter".

**Projecting to a minimal field set.** Would need a third `anyOf` branch in both schemas,
`published_schema` rework (a union yields bare `$ref` branches, which silently no-op the #242
`opaque_fields` opaquing), a restructure of the published `codex://capabilities-result` document
from an object to a union, and the budget raise above — for the remaining 31%.

## Consequences

`contracts` is a **projection**, not a verbosity level, on a knob otherwise meaning verbosity. Two
things keep that honest: its fields are a strict subset of `summary`'s (`agent-friendly-mcp` §8
requires this, and a set-equality test pins it), and the param is no longer described as
"verbosity". The shared `_DESCRIBED_PARAMS` guard was retargeted from `"verbosity"` to the default's
name for the same reason, with a per-surface test covering what that weakened.

Because several other top-level fields are *also* non-required, schema validation would not catch a
second field going missing. The set-equality test, not the schema, is what guarantees `tool_details`
is the only one dropped.
