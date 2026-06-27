# Design: enforce FINGERPRINT coverage via a canonical manifest snapshot

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

Add a CI-enforced **acknowledgment guard** that fails whenever the **full agent-visible surface**
changes, so the change cannot ship silently: the author must consciously regenerate the committed
snapshot, and the failure directs them to bump `FINGERPRINT` and add a CHANGELOG entry in the same
commit. The guard forces *acknowledgment and review* of every surface change; it does not
mechanically force the integer bump itself (see Non-goals / "Alternatives considered" — that is a
deliberate, proportionate choice for a priority-low / minor issue). Adopt remediation **(b)** from
the issue (a canonical, normalized manifest, snapshot-tested) **plus** the documentation broadening
from **(a)**.

> Naming note (per Codex review): because the published `FINGERPRINT` and the snapshot are
> independent editable artifacts, this is precisely a *surface-change acknowledgment guard*, not
> mechanical bump enforcement. The spec uses that framing consistently so the test's guarantee is
> not overstated.

## Non-goals

- Changing the published `FINGERPRINT` format. It stays the human-curated `schema-N` literal that
  clients cache by; the manifest snapshot is a *separate* guard artifact, never the published
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

1. **Tools** — the **complete** client wire object per tool (not a hand-picked subset). This includes
   `name`, `description`, `inputSchema`, `outputSchema`, `annotations` (e.g. `readOnlyHint`,
   `idempotentHint`), and any other client-visible MCP fields (`title`, `icons`, … if present). This
   subsumes value enums that appear in any input/output schema (`Severity`, `Verdict`, `Tier`,
   `Sandbox`, `Isolation`, `ReviewScope`, `Detail`, `Confidence`, `JobState`, `ToolStability`, …).
2. **Resources & resource templates** — the complete client wire objects (`uri`/`uriTemplate`,
   `name`, `description`, `mimeType`, `annotations`, `size`, `title`, …), metadata only (not content,
   except item 5).
3. **Prompts** — the complete client wire objects (`name`, `description`, `arguments`, …). None are
   registered today; included so adding one — or changing a prompt's argument schema — registers.

> **Allowlist vs. denylist (per Codex review #3):** the manifest **default-includes** every field of
> each client wire model (`model_dump(mode="json", by_alias=True, exclude_none=True)`) and removes
> only a small, documented set of proven-dynamic/noise paths (see Normalization). Projecting a hand-
> picked subset would silently miss future agent- or UI-visible fields (`title`, `icons`, prompt
> `arguments`); default-include + explicit, tested exclusions is the safer contract.
4. **Server `instructions`** — the initialize-result instructions string (= `CAPABILITY_SUMMARY`).
5. **Error envelope** — the *static* content of the `codex://error-envelope` resource. This is where
   the `ErrorCode` enum lives; it is **not** present in any success `outputSchema`, so it needs
   explicit coverage. The `codex://models` resource **content** is excluded — it is
   environment-dynamic (depends on the local `codex` CLI) and would make the snapshot
   machine-dependent; only its metadata (from item 2) is covered.
6. **The `codex_capabilities()` payload** — verified (Codex review #2) as a distinct agent-visible
   contract: `codex_capabilities` advertises "Clients can cache by the fingerprint," and its payload
   carries surface that the MCP tool wire shapes do **not** — the `active_tools`/`free_tools`
   classification, `tool_details` (`use_when`, `returns`, `required_params`, `key_optional_params`,
   per-tool `cost`/`stability`/`error_codes`), `negative_scope`, `prerequisites`, and `stability`.
   These can change without altering any tool's wire `inputSchema`/`description`, so they must be
   hashed too. **Exclude the release-variable `version` and the `fingerprint` field itself** (the
   payload echoes `FINGERPRINT`; including it would churn the snapshot on every bump and create a
   self-referential loop), plus any field proven environment-dependent. Everything else is included.
   Construct it by calling `codex_capabilities()` and canonicalizing the result.

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
2. **Sort top-level component lists**: tools by `name`, resources by `uri`, **resource templates by
   `uriTemplate`** (the MCP wire field for templates is `uriTemplate`, not `uri` — Codex review #5),
   prompts by `name`.
3. **Sort set-like JSON-Schema arrays only**: `enum`, `required`, and a multi-valued `type`. Sort by
   canonical JSON value so mixed types are deterministic. Leave all other arrays in source order —
   `anyOf`/`oneOf`/`allOf`/`prefixItems`/`examples` are order-sensitive in JSON Schema and must
   **not** be reordered (reordering would hide real changes and/or churn on pydantic upgrades).
4. **Drop only the exact documented framework noise path**: `_meta.fastmcp` (observed value
   `{"fastmcp": {"tags": []}}`). Per Codex review #4, do **not** drop the whole `_meta` key — MCP
   `_meta` is extensible and could later carry application-owned, agent-visible metadata; dropping
   all of it would be a permanent blind spot. Only the `fastmcp` sub-key is removed; any other
   `_meta` content is retained. (A regression test asserts an application-owned `_meta` entry would
   move the snapshot — see tests.) Nothing else is dropped — `title`, `$ref`, `$defs`, `icons`, etc.
   are retained so real changes still register.
5. **Stability across dependency upgrades** is provided by the committed `uv.lock` pinning
   `fastmcp` / `pydantic`; a dependency-induced wire change is review-worthy and will (correctly)
   move the snapshot, prompting a conscious snapshot + FINGERPRINT update. Determinism across the
   supported Python minors (3.11–3.14) is guaranteed by canonical key sorting + explicit set-like
   sorting (no reliance on dict/set iteration order or `PYTHONHASHSEED`); a determinism test pins
   this.

Serialize with compact, UTF-8, `sort_keys=True`, `ensure_ascii=False`, `allow_nan=False`. The
canonical JSON is committed as a **golden fixture** (the snapshot); a `sha256` hex digest of it is a
secondary, derived identity check.

### Snapshot artifact: golden JSON, not an opaque hash (Codex review #6)

The committed snapshot is the **canonical manifest JSON** (`tests/fixtures/manifest_snapshot.json`),
not just a 64-char digest. Rationale: when the surface changes, regenerating the snapshot produces a
**readable git diff** showing exactly which description / schema / annotation / capability field
moved, so the reviewer (and the PR's required review) can judge whether the change is intended and
whether it warrants the FINGERPRINT bump. An opaque hash would let a surface change be acknowledged
without any visibility into *what* changed. The `sha256` is retained only as a cheap determinism/
identity assertion layered on top of the JSON comparison.

## Components

### `src/codex_in_claude/manifest.py` (new)

- `async def build_manifest() -> dict` — assemble the normalized structure above using an in-process
  `Client(mcp)` (full client model dumps), plus the canonicalized `codex_capabilities()` payload
  (minus `version`) and the static `codex://error-envelope` content.
- `def _canonicalize(obj) -> obj` — apply the normalization rules recursively (sort keys, sort the
  set-like arrays, strip `_meta.fastmcp`).
- `def manifest_json(manifest: dict) -> str` — the canonical serialization used for both the golden
  file and the hash.
- `async def manifest_hash() -> str` — `sha256` hex of `manifest_json(build_manifest())`.
- The module imports `mcp`/`codex_capabilities` from `server`. Note: this is the package layer, not
  `_core` — it is allowed to import from the parent package (the `_core` one-way rule does not apply).

`manifest.py` is **not** placed under `_core/` (it is server-surface-specific, not generic bridge
machinery). To avoid a circular import (`server` → … and `manifest` → `server`), `manifest.py`
imports lazily inside `build_manifest()` if needed.

### Snapshot + tests (`tests/test_manifest.py`, `tests/fixtures/manifest_snapshot.json`)

- `test_manifest_matches_golden` — `assert build_manifest() == load(manifest_snapshot.json)`, with an
  assertion message:
  > "agent-visible surface changed — review the snapshot diff, then in the SAME commit bump
  > FINGERPRINT (schema-N) in schemas.py, regenerate tests/fixtures/manifest_snapshot.json, and add a
  > CHANGELOG entry under [Unreleased]."
  A helper / `make` target (or a `python -m codex_in_claude.manifest` entry) regenerates the fixture
  so updating it is a single deliberate command, not hand-editing.
- `test_manifest_hash_is_pinned` — `assert await manifest_hash() == EXPECTED_MANIFEST_HASH` (a pinned
  constant); secondary identity guard so the fixture and hash can't silently diverge.
- `test_manifest_covers_full_surface` — structural guard: the manifest's tool-name set equals the
  full registered tool set (`active_tools + free_tools` from capabilities), and the manifest contains
  the `capabilities`, `instructions`, `resources`, and `error_envelope` sections, so a regression
  that empties/short-circuits `build_manifest` can't pass with a stale snapshot.
- `test_manifest_excludes_dynamic_fields` — asserts `codex://models` *content* and the capabilities
  `version` field are absent (documents the determinism boundary), and that the static
  `error-envelope` content (with `ErrorCode`) **is** present.
- `test_manifest_drops_only_fastmcp_meta` — asserts `_meta.fastmcp` is stripped but an injected
  application-owned `_meta` key would change the manifest (guards the narrow exclusion from becoming
  a blind spot — Codex review #4).
- `test_manifest_is_deterministic` — `build_manifest()` called twice yields byte-identical canonical
  JSON (guards against set/dict-iteration nondeterminism across Python minors).

### Docs / process (remediation (a))

- Broaden the `FINGERPRINT` comment in `schemas.py:14-16` to explicitly name the now-covered
  surfaces (descriptions, annotations, server instructions, resource metadata, error envelope, and
  the `codex_capabilities()` payload) and point at `tests/test_manifest.py` as the guard.
- Update the AGENTS.md "The result contract" paragraph to mention that the committed manifest
  snapshot guards the surface and must be regenerated alongside a `FINGERPRINT` bump.

## Fingerprint / CHANGELOG impact

- **No `FINGERPRINT` bump from this change itself**: it adds a test + fixture + module + docs and
  does not alter the agent-visible surface. (Confirm during implementation; the *first* committed
  snapshot establishes the baseline.)
- `CHANGELOG.md`: add an entry under `## [Unreleased]` (Added: manifest-snapshot guard enforcing
  FINGERPRINT coverage over the full agent-visible surface). No version-literal bump (two-PR release
  rule).

## Testing strategy (TDD)

1. Write `tests/test_manifest.py` first (against the intended `manifest` API) — red.
2. Implement `manifest.py` minimally to satisfy structure / determinism / coverage / exclusion tests.
3. Generate the golden fixture once (`python -m codex_in_claude.manifest > tests/fixtures/manifest_snapshot.json`),
   pin `EXPECTED_MANIFEST_HASH`; the golden + hash tests go green.
4. Verify the gate works: temporarily tweak a tool docstring / annotation / a `use_when` string
   locally and confirm `test_manifest_matches_golden` fails with a readable diff; revert.
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

Design developed with OpenAI Codex across two rounds, each finding verified against the codebase
before incorporation:

- **`codex_consult` (initial design):** wire-shape source of truth (in-process `Client`, not
  server-side `list_tools()`), the impossibility of a pure-pytest *forced* bump, surgical (not
  blanket) normalization, and set-like array sorting.
- **`codex_review_changes` (critical review of this spec, verdict: fail):** (#1) reframed the goal as
  an *acknowledgment* guard, not mechanical enforcement; (#2) added the `codex_capabilities()`
  payload as covered surface — verified at `server.py:943` that it advertises caching-by-fingerprint
  and carries `tool_details`/classification/`negative_scope`/`prerequisites` absent from the tool
  wire shapes; (#3) switched from a field allowlist to full client model dumps with documented
  exclusions; (#4) narrowed the `_meta` strip to `_meta.fastmcp` only; (#5) corrected resource-
  template sorting to `uriTemplate`; (#6) committed a golden JSON snapshot for reviewable diffs
  instead of an opaque digest.
