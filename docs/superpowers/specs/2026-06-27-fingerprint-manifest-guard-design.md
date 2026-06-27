# Design: enforce FINGERPRINT coverage via a canonical manifest hash

**Issue:** #140 — `test(schemas): enforce FINGERPRINT coverage over the full agent-visible surface`
**Date:** 2026-06-27
**Status:** Approved (brainstorming)

## Problem

`FINGERPRINT` (`src/codex_in_claude/schemas.py`) is a hand-maintained literal
(`codex-in-claude/0.1/schema-16`) that clients cache by. Its bump policy is enforced only by
author discipline: `tests/test_server.py` pins the *string* (`test_fingerprint_is_schema_16`) but
nothing recomputes the surface it is supposed to summarize. The comment at `schemas.py:14-16`
lists tool names / schemas / error codes / enums / guarantees, but **not** tool descriptions,
annotations, `CAPABILITY_SUMMARY` (server `instructions`), or resource metadata. So a change to any
of those uncovered surfaces ships without forcing a bump.

This is the audit finding F6 (`AGENT_FRIENDLINESS_AUDIT.md`), severity Minor, priority low.

## Goal

Add a CI-enforced guard that fails whenever the **full agent-visible surface** changes, so the
author is forced to consciously bump `FINGERPRINT`, update the snapshot, and add a CHANGELOG entry —
rather than silently shipping a surface change. Adopt remediation **(b)** from the issue (a
canonical, normalized manifest hash, snapshot-tested) **plus** the documentation broadening from
**(a)**.

## Non-goals

- Changing the published `FINGERPRINT` format. It stays the human-curated `schema-N` literal that
  clients cache by; the manifest hash is a *separate* enforcement snapshot, never the published
  value. (Changing the published format/computation would itself require a bump + a
  `COMPATIBILITY.md` note — explicitly avoided here.)
- Airtight CI coupling that *mechanically forces the integer bump* (e.g. a merge-base–aware CI job
  that requires `schema-N` to increase). Considered and declined as disproportionate to a
  priority-low / minor issue. The pytest snapshot forces **acknowledgment** of any surface change;
  the failure message directs the human to bump. (Recorded here so a future reader knows it was a
  deliberate choice, not an oversight — see "Alternatives considered".)

## What counts as the "agent-visible surface"

The **client-visible MCP wire representation** plus the one enum contract that the wire schemas do
not already expose:

1. **Tools** — for each: `name`, `description`, `inputSchema`, `outputSchema`, `annotations`
   (e.g. `readOnlyHint`, `idempotentHint`). This subsumes value enums that appear in any
   input/output schema (`Severity`, `Verdict`, `Tier`, `Sandbox`, `Isolation`, `ReviewScope`,
   `Detail`, `Confidence`, `JobState`, `ToolStability`, …).
2. **Resources & resource templates** — `uri`, `name`, `description`, `mimeType` (metadata only).
3. **Prompts** — `name`, `description` (currently none registered; included so adding one registers).
4. **Server `instructions`** — the initialize-result instructions string (= `CAPABILITY_SUMMARY`).
5. **Error envelope** — the *static* content of the `codex://error-envelope` resource. This is where
   the `ErrorCode` enum lives; it is **not** present in any success `outputSchema`, so it needs
   explicit coverage. The `codex://models` resource **content** is excluded — it is
   environment-dynamic (depends on the local `codex` CLI) and would make the hash machine-dependent;
   only its metadata (from item 2) is covered.

### Source of truth: in-process `Client`, not server-side `list_tools()`

Per Codex review (verified): `mcp.list_tools()` returns internal `Tool` objects and is **not** the
wire representation — the wire shape uses `inputSchema`/`outputSchema` (camelCase), carries
`annotations` and `_meta`, and is produced by `Tool.to_mcp_tool()`. The manifest therefore opens an
in-process `fastmcp.Client(mcp)` and uses `client.list_tools()` / `list_resources()` /
`list_resource_templates()` / `list_prompts()` and the initialize result, serialized with
`model_dump(mode="json", by_alias=True, exclude_none=True)`. (Verified: the wire tool dump exposes
keys `name, description, inputSchema, outputSchema, annotations, _meta`.)

## Normalization contract

Explicit and surgical — **not** a blanket recursive strip (which would create false negatives by
hiding real wire changes). Verified against the codebase: existing tests already treat enum order as
non-contractual (`test_fixed_value_params_advertise_enum`: "assert membership, not order … may vary
across Pydantic/FastMCP versions"), so set-like arrays must be sorted before hashing or the hash is
order-flaky.

Rules:

1. **Sort all object keys** (canonical JSON; `json.dumps(..., sort_keys=True)`).
2. **Sort top-level component lists**: tools by `name`, resources & templates by `uri`, prompts by
   `name`.
3. **Sort set-like JSON-Schema arrays only**: `enum`, `required`, and a multi-valued `type`. Sort by
   canonical JSON value so mixed types are deterministic. Leave all other arrays in source order
   (some are order-sensitive).
4. **Drop only the documented framework-owned noise**: the `_meta` key on wire objects (observed
   value `{"fastmcp": {"tags": []}}`). Nothing else is dropped — `title`, `$ref`, `$defs`, etc. are
   retained so real changes still register.
5. **Stability across dependency upgrades** is provided by the committed `uv.lock` pinning
   `fastmcp` / `pydantic`; a dependency-induced wire change is review-worthy and will (correctly)
   move the hash, prompting a conscious snapshot + FINGERPRINT update.

Serialize with compact, UTF-8, `sort_keys=True`, `ensure_ascii=False`, `allow_nan=False`; hash with
`sha256`, hex digest.

## Components

### `src/codex_in_claude/manifest.py` (new)

- `async def build_manifest() -> dict` — assemble the normalized structure described above using an
  in-process `Client(mcp)`.
- `def _canonicalize(obj) -> obj` — apply the normalization rules recursively.
- `async def manifest_hash() -> str` — `sha256` hex of the canonical JSON of `build_manifest()`.
- The module imports `mcp` from `server`. Note: this is the package layer, not `_core` — it is
  allowed to import from the parent package (the `_core` one-way rule does not apply here).

`manifest.py` is **not** placed under `_core/` (it is server-surface-specific, not generic bridge
machinery).

### `tests/test_manifest.py` (new)

- `EXPECTED_MANIFEST_HASH = "<64-hex>"` pinned constant.
- `test_manifest_hash_is_pinned` — `assert await manifest_hash() == EXPECTED_MANIFEST_HASH`, with an
  assertion message:
  > "agent-visible surface changed — bump FINGERPRINT (schema-N) in schemas.py, update
  > EXPECTED_MANIFEST_HASH here, and add a CHANGELOG entry under [Unreleased]."
- `test_manifest_covers_all_tools` — structural guard: the manifest's tool-name set equals
  `set(codex_capabilities()["active_tools"] + ["..."])` (or the full registered tool set), so a
  regression that empties/short-circuits `build_manifest` can't pass with a stale hash.
- `test_manifest_excludes_dynamic_resource_content` — asserts `codex://models` *content* is not in
  the manifest (only its metadata), documenting the determinism boundary.
- `test_manifest_is_deterministic` — `build_manifest()` called twice yields equal hashes (guards
  against accidental nondeterminism, e.g. set iteration).

### Docs / process (remediation (a))

- Broaden the `FINGERPRINT` comment in `schemas.py:14-16` to explicitly name the now-covered
  surfaces (descriptions, annotations, server instructions, resource metadata, error envelope) and
  point at `tests/test_manifest.py` as the enforcement.
- Update the AGENTS.md "The result contract" paragraph to mention that the manifest hash guards the
  surface and must be updated alongside a `FINGERPRINT` bump.

## Fingerprint / CHANGELOG impact

- **No `FINGERPRINT` bump from this change itself**: it adds a test + module + docs and does not
  alter the agent-visible surface. (Confirm during implementation by computing the hash; the
  *first* pin establishes the baseline.)
- `CHANGELOG.md`: add an entry under `## [Unreleased]` (Added: manifest-hash guard enforcing
  FINGERPRINT coverage). No version-literal bump (per the two-PR release rule).

## Testing strategy (TDD)

1. Write `tests/test_manifest.py` first (against the intended `manifest` API) — red.
2. Implement `manifest.py` minimally to satisfy structure/determinism/coverage tests.
3. Run once to compute the real hash; pin it into `EXPECTED_MANIFEST_HASH`; the pin test goes green.
4. Verify the gate works: temporarily tweak a tool docstring / annotation locally and confirm
   `test_manifest_hash_is_pinned` fails; revert.
5. Full gate: `uv run ruff check . && uv run ruff format --check . && uv run ty check &&
   uv run pytest` (95% coverage floor).

## Alternatives considered

- **(a) Docs/process only** — rejected as primary fix: still discipline-only, no CI enforcement.
  Folded in as a supplement.
- **Replace/derive `FINGERPRINT` from the hash** — rejected: changes the published fingerprint
  format (clients lose readable `schema-N`), and itself needs a bump + compatibility note.
- **Merge-base–aware CI job that mechanically forces the integer bump** — rejected as
  disproportionate to a priority-low/minor issue; adds git-base logic and a CI job. The pytest
  snapshot forcing acknowledgment is the chosen proportionate enforcement.
- **Hand-maintained `SURFACE_ENUMS` registry** — avoided: enums are already covered by the wire
  schemas, and the one exception (`ErrorCode`) is covered via the static `codex://error-envelope`
  resource content. No parallel registry to drift.

## Collaboration note

Design reviewed with OpenAI Codex (`codex_consult`); its findings on the wire-shape source of truth,
the impossibility of a pure-pytest forced bump, surgical (not blanket) normalization, and set-like
array sorting were verified against the codebase and incorporated above.
