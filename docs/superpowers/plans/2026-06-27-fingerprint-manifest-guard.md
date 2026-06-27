# FINGERPRINT Manifest-Snapshot Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CI-enforced acknowledgment guard that fails whenever the full agent-visible MCP surface changes, forcing a conscious `FINGERPRINT` bump.

**Architecture:** A new `manifest.py` builds a canonical, normalized snapshot of the client-visible surface (full tool/resource/template/prompt wire dumps via an in-process `fastmcp.Client`, the server `instructions`, the static `codex://error-envelope` content, and the `codex_capabilities()` payload minus release-variable fields). The snapshot is serialized to a committed golden JSON fixture for reviewable diffs; tests assert the live manifest matches the fixture and a pinned sha256.

**Tech Stack:** Python ≥3.11, FastMCP 3.4.2, pydantic v2, pytest (+ asyncio auto mode), `uv`, `ruff`, `ty`.

## Global Constraints

- Use `uv` for everything (`uv run pytest`, `uv run ruff`, `uv run ty`). Never pip/poetry.
- All three must pass before done: `uv run ruff check . && uv run ruff format --check . && uv run ty check`.
- pytest with a **95% coverage floor**. `manifest.py` must be ≥95% covered.
- Conventional Commits; branch is `test/fingerprint-manifest-guard` (already created). Squash-merge; PR title must be a valid Conventional Commit.
- `_core/` one-way rule does NOT apply here — `manifest.py` is package-layer and may import from `server`.
- This change does **not** alter the agent-visible surface, so **do not bump `FINGERPRINT`** and **do not** touch the three version literals (`pyproject.toml`, `.claude-plugin/plugin.json`, `.mcp.json`).
- Add a `CHANGELOG.md` entry under `## [Unreleased]`.
- Existing async tests are bare `async def test_...` (pytest-asyncio auto mode) — follow that; no `@pytest.mark.asyncio` needed.

---

### Task 1: `manifest.py` — build + normalize the canonical manifest

**Files:**
- Create: `src/codex_in_claude/manifest.py`
- Test: `tests/test_manifest.py`

**Interfaces:**
- Consumes: `codex_in_claude.server.mcp` (FastMCP instance), `codex_in_claude.server.codex_capabilities` (`() -> dict`).
- Produces:
  - `_canonicalize(obj: Any) -> Any` — recursively strips `_meta.fastmcp`; sorts the set-like arrays `enum`/`required` and a multi-valued `type`.
  - `async build_manifest() -> dict[str, Any]` — keys: `tools`, `resources`, `resource_templates`, `prompts`, `instructions`, `error_envelope`, `capabilities`.
  - `manifest_json(manifest: dict[str, Any]) -> str` — canonical serialization (indent=2, sort_keys, ensure_ascii=False, trailing newline).
  - `async manifest_hash() -> str` — sha256 hex of `manifest_json(build_manifest())`.

- [ ] **Step 1: Write the failing tests for normalization + structure**

Create `tests/test_manifest.py`:

```python
"""Guard: the manifest snapshot covers the full agent-visible surface (issue #140)."""

from codex_in_claude import manifest, server


def test_canonicalize_strips_only_fastmcp_meta():
    # An app-owned _meta key survives; the fastmcp sub-key is removed.
    assert manifest._canonicalize(
        {"_meta": {"fastmcp": {"tags": []}, "app": {"k": 1}}}
    ) == {"_meta": {"app": {"k": 1}}}
    # A _meta that is only fastmcp noise is dropped entirely.
    assert manifest._canonicalize({"_meta": {"fastmcp": {"tags": []}}}) == {}


def test_canonicalize_sorts_setlike_arrays():
    canon = manifest._canonicalize(
        {"enum": ["c", "a", "b"], "required": ["z", "a"], "type": ["string", "null"]}
    )
    assert canon["enum"] == ["a", "b", "c"]
    assert canon["required"] == ["a", "z"]
    assert canon["type"] == ["null", "string"]


def test_canonicalize_preserves_order_sensitive_arrays():
    # anyOf is order-sensitive in JSON Schema and must NOT be reordered.
    src = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert manifest._canonicalize(src)["anyOf"] == [{"type": "string"}, {"type": "null"}]


async def test_build_manifest_covers_full_surface():
    m = await manifest.build_manifest()
    caps = server.codex_capabilities()
    expected_tools = set(caps["active_tools"]) | set(caps["free_tools"])
    assert {t["name"] for t in m["tools"]} == expected_tools
    for section in ("resources", "instructions", "error_envelope", "capabilities"):
        assert m[section], f"manifest section {section} is empty"


async def test_build_manifest_excludes_dynamic_fields():
    m = await manifest.build_manifest()
    # Release-variable / self-referential capability fields are excluded.
    assert "version" not in m["capabilities"]
    assert "fingerprint" not in m["capabilities"]
    # Resource METADATA for codex://models is present; its dynamic CONTENT is not read.
    uris = {r["uri"] for r in m["resources"]}
    assert "codex://models" in uris
    # The static error-envelope content (where ErrorCode lives) IS captured.
    blob = manifest.manifest_json(m)
    assert "invalid_workspace_root" in blob  # an ErrorCode literal


async def test_build_manifest_strips_fastmcp_meta_from_tools():
    m = await manifest.build_manifest()
    for tool in m["tools"]:
        assert "fastmcp" not in tool.get("_meta", {})


async def test_manifest_json_is_deterministic():
    a = manifest.manifest_json(await manifest.build_manifest())
    b = manifest.manifest_json(await manifest.build_manifest())
    assert a == b
    assert a.endswith("\n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_manifest.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'codex_in_claude.manifest'`.

- [ ] **Step 3: Implement `manifest.py`**

Create `src/codex_in_claude/manifest.py`:

```python
"""Canonical manifest of the full agent-visible MCP surface (issue #140).

Snapshotting this manifest guards ``FINGERPRINT``: any change to the
client-visible surface — tool/resource/template/prompt wire shapes, the server
``instructions``, the ``codex://error-envelope`` content, or the
``codex_capabilities`` payload — moves the snapshot, forcing a conscious
``FINGERPRINT`` bump. See
``docs/superpowers/specs/2026-06-27-fingerprint-manifest-guard-design.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from fastmcp import Client

from codex_in_claude.server import codex_capabilities, mcp

# Framework-owned noise dropped from every wire object's ``_meta`` (observed
# value ``{"fastmcp": {"tags": []}}``). Only this sub-key is removed; any
# application-owned ``_meta`` content is retained so it stays guarded.
_FASTMCP_META_KEY = "fastmcp"
# JSON-Schema arrays that are semantically sets — sorted for order-independence
# (enum order is explicitly non-contractual; see tests/test_server.py).
_SETLIKE_ARRAY_KEYS = frozenset({"enum", "required"})
# capabilities fields excluded: ``version`` is release-variable and
# ``fingerprint`` echoes FINGERPRINT itself (would self-reference the guard).
_CAPABILITIES_EXCLUDE = frozenset({"version", "fingerprint"})


def _sorted_by_json(items: list[Any]) -> list[Any]:
    return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))


def _canonicalize(obj: Any) -> Any:
    """Recursively strip ``_meta.fastmcp`` and sort set-like JSON-Schema arrays.

    Object-key ordering is handled at serialization time (``sort_keys=True``);
    order-sensitive arrays (``anyOf``/``oneOf``/``allOf``/``prefixItems``/...)
    are left untouched."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if key == "_meta" and isinstance(value, dict):
                value = {k: v for k, v in value.items() if k != _FASTMCP_META_KEY}
                if not value:
                    continue
            cval = _canonicalize(value)
            if isinstance(cval, list) and (
                key in _SETLIKE_ARRAY_KEYS or key == "type"
            ):
                cval = _sorted_by_json(cval)
            out[key] = cval
        return out
    if isinstance(obj, list):
        return [_canonicalize(v) for v in obj]
    return obj


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


async def build_manifest() -> dict[str, Any]:
    """Assemble the normalized, canonical agent-visible surface manifest."""
    async with Client(mcp) as client:
        tools = [_canonicalize(_dump(t)) for t in await client.list_tools()]
        resources = [_canonicalize(_dump(r)) for r in await client.list_resources()]
        templates = [
            _canonicalize(_dump(t)) for t in await client.list_resource_templates()
        ]
        prompts = [_canonicalize(_dump(p)) for p in await client.list_prompts()]
        instructions = client.initialize_result.instructions
        envelope = [
            _canonicalize(_dump(c))
            for c in await client.read_resource("codex://error-envelope")
        ]

    caps = {
        k: v for k, v in codex_capabilities().items() if k not in _CAPABILITIES_EXCLUDE
    }

    return {
        "tools": sorted(tools, key=lambda t: t["name"]),
        "resources": sorted(resources, key=lambda r: r["uri"]),
        "resource_templates": sorted(templates, key=lambda t: t["uriTemplate"]),
        "prompts": sorted(prompts, key=lambda p: p["name"]),
        "instructions": instructions,
        "error_envelope": envelope,
        "capabilities": _canonicalize(caps),
    }


def manifest_json(manifest: dict[str, Any]) -> str:
    """Canonical serialization used for both the golden fixture and the hash."""
    return (
        json.dumps(
            manifest, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
        )
        + "\n"
    )


async def manifest_hash() -> str:
    """sha256 hex of the canonical manifest JSON."""
    payload = manifest_json(await build_manifest())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render() -> str:
    """Synchronous helper: the canonical manifest JSON (for regeneration)."""
    return manifest_json(asyncio.run(build_manifest()))


def main() -> None:  # pragma: no cover - thin CLI wrapper
    import sys

    sys.stdout.write(render())


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_manifest.py -q`
Expected: PASS (7 tests). If `test_build_manifest_excludes_dynamic_fields` fails on the `invalid_workspace_root` assertion, open `codex://error-envelope` content and substitute a literal that is actually present (the test's intent: an `ErrorCode` value appears in the captured envelope).

- [ ] **Step 5: Lint, format, type-check**

Run: `uv run ruff check . && uv run ruff format . && uv run ty check`
Expected: all pass (format may reformat `manifest.py`/test — re-run `ruff format --check .` to confirm clean).

- [ ] **Step 6: Commit**

```bash
git add src/codex_in_claude/manifest.py tests/test_manifest.py
git commit -m "test(schemas): add agent-visible surface manifest builder (#140)"
```

---

### Task 2: Golden fixture + pinned-hash guard

**Files:**
- Create: `tests/fixtures/manifest_snapshot.json`
- Modify: `tests/test_manifest.py` (add golden + hash tests and the `EXPECTED_MANIFEST_HASH` constant)

**Interfaces:**
- Consumes: `manifest.build_manifest`, `manifest.manifest_json`, `manifest.manifest_hash` from Task 1.
- Produces: the committed golden snapshot and pinned hash that future surface changes must move.

- [ ] **Step 1: Write the failing golden + hash tests**

Append to `tests/test_manifest.py` (add `from pathlib import Path` at the top):

```python
_FIXTURE = Path(__file__).parent / "fixtures" / "manifest_snapshot.json"

# Pinned in Step 3 below, after generating the fixture.
EXPECTED_MANIFEST_HASH = "PENDING"


async def test_manifest_matches_golden():
    current = manifest.manifest_json(await manifest.build_manifest())
    assert current == _FIXTURE.read_text(encoding="utf-8"), (
        "agent-visible surface changed — review the snapshot diff, then in the SAME "
        "commit: bump FINGERPRINT (schema-N) in schemas.py, regenerate the fixture "
        "(`uv run python -m codex_in_claude.manifest > tests/fixtures/manifest_snapshot.json`), "
        "and add a CHANGELOG entry under [Unreleased]."
    )


async def test_manifest_hash_is_pinned():
    assert await manifest.manifest_hash() == EXPECTED_MANIFEST_HASH
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_manifest.py::test_manifest_matches_golden tests/test_manifest.py::test_manifest_hash_is_pinned -q`
Expected: FAIL — `FileNotFoundError` (no fixture) and hash mismatch (`PENDING`).

- [ ] **Step 3: Generate the fixture and pin the hash**

```bash
mkdir -p tests/fixtures
uv run python -m codex_in_claude.manifest > tests/fixtures/manifest_snapshot.json
uv run python -c "import asyncio; from codex_in_claude import manifest; print(asyncio.run(manifest.manifest_hash()))"
```

Copy the printed 64-char digest and replace `EXPECTED_MANIFEST_HASH = "PENDING"` with it.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_manifest.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Verify the guard actually trips (sanity check, then revert)**

```bash
# Temporarily edit any tool docstring, e.g. add a word to codex_status.__doc__ in server.py
uv run pytest tests/test_manifest.py::test_manifest_matches_golden -q   # expect FAIL with the readable diff
git checkout -- src/codex_in_claude/server.py                            # revert the probe
uv run pytest tests/test_manifest.py -q                                  # expect PASS again
```

Expected: the guard FAILS on the probe and PASSES after revert. (Do not commit the probe.)

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/manifest_snapshot.json tests/test_manifest.py
git commit -m "test(schemas): pin manifest snapshot + hash guard (#140)"
```

---

### Task 3: Broaden the FINGERPRINT comment + AGENTS.md + CHANGELOG

**Files:**
- Modify: `src/codex_in_claude/schemas.py:14-16` (the FINGERPRINT comment)
- Modify: `AGENTS.md` (the "The result contract" section)
- Modify: `CHANGELOG.md` (under `## [Unreleased]`)

**Interfaces:** docs only — no code interface.

- [ ] **Step 1: Broaden the FINGERPRINT comment**

In `src/codex_in_claude/schemas.py`, replace the comment above `FINGERPRINT`:

```python
# Bump this whenever the agent-visible surface changes: tool names, input or
# output schemas, descriptions, annotations, the ErrorCode set, the
# tier/sandbox/isolation/scope value sets, the capability guarantees, the server
# instructions (CAPABILITY_SUMMARY), resource metadata, or the
# codex_capabilities payload. Clients cache by it. The committed manifest
# snapshot (tests/fixtures/manifest_snapshot.json, guarded by
# tests/test_manifest.py) fails CI on any covered change so the bump is never
# silently skipped — regenerate that fixture in the same commit as the bump.
FINGERPRINT = "codex-in-claude/0.1/schema-16"
```

- [ ] **Step 2: Update AGENTS.md "The result contract"**

Replace that section's body with:

```markdown
All tools return the envelope in `src/codex_in_claude/schemas.py`. Bump `FINGERPRINT` whenever the
agent-visible surface changes (tool names, params/schemas, descriptions, annotations, error codes,
value enums, server instructions, resource metadata, or the `codex_capabilities` payload). A
committed manifest snapshot (`tests/fixtures/manifest_snapshot.json`, guarded by
`tests/test_manifest.py`) fails CI on any covered change, so a surface change cannot ship without a
deliberate bump: regenerate the fixture
(`uv run python -m codex_in_claude.manifest > tests/fixtures/manifest_snapshot.json`) and bump
`FINGERPRINT` in the same commit. Keep the change in `CHANGELOG.md`.
```

- [ ] **Step 3: Add a CHANGELOG entry**

Under `## [Unreleased]` (create an `### Added` subsection if absent), add:

```markdown
### Added
- Manifest-snapshot guard (`tests/test_manifest.py` + `tests/fixtures/manifest_snapshot.json`)
  that fails CI whenever the full agent-visible surface — tool/resource wire shapes, descriptions,
  annotations, server instructions, the error envelope, and the `codex_capabilities` payload —
  changes, forcing a conscious `FINGERPRINT` bump (#140).
```

- [ ] **Step 4: Verify docs didn't change the surface**

Run: `uv run pytest tests/test_manifest.py -q`
Expected: PASS — none of these edits touch the agent-visible surface, so the snapshot/hash are unchanged. (If `test_manifest_matches_golden` fails, a docstring/annotation was changed inadvertently — investigate before regenerating.)

- [ ] **Step 5: Full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`
Expected: all pass; coverage ≥95%.

- [ ] **Step 6: Commit**

```bash
git add src/codex_in_claude/schemas.py AGENTS.md CHANGELOG.md
git commit -m "docs(schemas): document the manifest-snapshot FINGERPRINT guard (#140)"
```

---

## Self-Review

**Spec coverage:**
- Surface items 1–4 (tools/resources/templates/prompts/instructions) → Task 1 `build_manifest`. ✓
- Item 5 (error-envelope content / ErrorCode) → Task 1 (`read_resource`) + `test_build_manifest_excludes_dynamic_fields`. ✓
- Item 6 (`codex_capabilities` payload, minus `version`/`fingerprint`) → Task 1 + exclusion test. ✓
- Normalization contract (sort keys, set-like arrays, `uriTemplate` sort, `_meta.fastmcp` strip, order-sensitive arrays preserved) → Task 1 code + `test_canonicalize_*`. ✓
- Golden JSON snapshot + hash + readable-diff guard → Task 2. ✓
- Determinism across runs → `test_manifest_json_is_deterministic`. ✓
- Docs broadening (schemas comment + AGENTS.md) → Task 3. ✓
- CHANGELOG under [Unreleased], no version/FINGERPRINT bump → Task 3 + Global Constraints. ✓

**Placeholder scan:** `EXPECTED_MANIFEST_HASH = "PENDING"` is an intentional, resolved-in-Step-3 placeholder (the hash is machine-generated; it cannot be known when writing the plan). No other placeholders.

**Type consistency:** `build_manifest`/`manifest_json`/`manifest_hash`/`_canonicalize`/`render` names and signatures match between Task 1's Produces block, the implementation, and Task 2's tests. Capabilities exclusion set matches between code and tests (`version`, `fingerprint`).
