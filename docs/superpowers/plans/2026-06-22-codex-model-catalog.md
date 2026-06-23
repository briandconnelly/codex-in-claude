# Codex Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agent discover valid Codex `model` slugs via a new free `codex_models` tool and a `codex://models` MCP resource, sourced from Codex's on-disk cache with a bundled static fallback.

**Architecture:** A generic bounded-JSON reader lives in `_core` (no Codex knowledge). Codex-specific path/shape constants live in `cli_contract.py`. A new parent module `codex_models.py` resolves `$CODEX_HOME`, reads/validates `models_cache.json`, and falls back to a bundled `KNOWN_MODEL_SLUGS` list. The server exposes the catalog through both a free tool and a resource (one shared payload builder). The list is **advisory** — `model` stays pass-through; nothing rejects an unlisted slug, because `codex exec` is the real validator. It is **not** embedded in `codex_capabilities`, whose payload is fingerprint-cacheable and must stay stable.

**Tech Stack:** Python 3.11+, FastMCP, Pydantic v2, pytest. Tooling: `uv`, `ruff`, `ty`.

## Global Constraints

- Use `uv` for everything: `uv run pytest`, `uv run ruff check .`, `uv run ruff format`, `uv run ty check`. Never pip/poetry.
- `_core` must NEVER import from its parent package (`codex_in_claude.*`) — one-way dependency.
- Every assumption about the `codex` CLI lives in `cli_contract.py`.
- All tools return the envelope contract in `schemas.py`. Bump `FINGERPRINT` when the agent-visible surface changes (this plan adds a tool + resource + schema → bump).
- 95% coverage floor enforced in CI. `integration`-marked tests are excluded by default.
- Conventional Commits; branch `feat/model-catalog` off `main` (do not commit to default branch).
- All three gates must pass before done: `uv run ruff check . && uv run ruff format --check . && uv run ty check`, plus `uv run pytest`.
- Bump together when the surface changes: `FINGERPRINT`, `CHANGELOG.md`. (No version literal in README; pyproject/plugin.json/.mcp.json bumps happen at release-cut time, not in this feature PR unless cutting a release.)

## File Structure

- **Create** `src/codex_in_claude/_core/jsoncache.py` — generic `read_bounded_json(path, max_bytes)`; stdlib only; no parent imports.
- **Modify** `src/codex_in_claude/cli_contract.py` — add model-catalog constants: filename, byte/entry caps, slug pattern, `KNOWN_MODEL_SLUGS`.
- **Modify** `src/codex_in_claude/schemas.py` — add `ModelCatalogSource`, `ModelInfo`, `ModelCatalogResult`, `MODEL_CATALOG_SCHEMA`; bump `FINGERPRINT`.
- **Create** `src/codex_in_claude/codex_models.py` — `read_model_catalog()` (resolve `$CODEX_HOME`, parse cache, fall back to static).
- **Modify** `src/codex_in_claude/server.py` — `codex_models` tool + `codex://models` resource (shared payload builder); add to `free_tools`, `tool_details`, `_TOOL_ERROR_CODES`.
- **Tests:** create `tests/test_jsoncache.py`, `tests/test_codex_models.py`; extend `tests/test_cli_contract.py`, `tests/test_server.py`, `tests/test_codex.py`.
- **Modify** `CHANGELOG.md` — `## [Unreleased]` → Added.

---

### Task 1: Generic bounded JSON reader in `_core`

**Files:**
- Create: `src/codex_in_claude/_core/jsoncache.py`
- Test: `tests/test_jsoncache.py`

**Interfaces:**
- Produces: `read_bounded_json(path: Path, max_bytes: int) -> Any | None` — returns parsed JSON, or `None` for missing / non-regular-file / oversize / unreadable / invalid-JSON. Never raises for those cases.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jsoncache.py
from pathlib import Path

from codex_in_claude._core.jsoncache import read_bounded_json


def test_reads_valid_json(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    assert read_bounded_json(p, 1000) == {"a": 1}


def test_missing_file_returns_none(tmp_path: Path):
    assert read_bounded_json(tmp_path / "nope.json", 1000) is None


def test_directory_returns_none(tmp_path: Path):
    assert read_bounded_json(tmp_path, 1000) is None


def test_oversize_returns_none(tmp_path: Path):
    p = tmp_path / "big.json"
    p.write_text('{"a": "' + "x" * 1000 + '"}', encoding="utf-8")
    assert read_bounded_json(p, 100) is None


def test_invalid_json_returns_none(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_bounded_json(p, 1000) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_jsoncache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codex_in_claude._core.jsoncache'`

- [ ] **Step 3: Implement the reader**

```python
# src/codex_in_claude/_core/jsoncache.py
"""Generic bounded JSON file reader.

Lives in _core (no parent imports): reads and parses a JSON file defensively,
returning None on any problem rather than raising. Knows nothing about Codex or any
specific cache shape — callers layer their own validation on top.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_bounded_json(path: Path, max_bytes: int) -> Any | None:
    """Parse the JSON at `path`, or return None.

    Returns None when the path is missing, not a regular file, larger than
    `max_bytes`, unreadable, or not valid UTF-8 JSON. Never raises for those cases —
    a caller treats None as "no usable data" and falls back. `is_file()` follows
    symlinks, so a symlink is read but still size-capped and shape-validated downstream.
    """
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > max_bytes:
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except ValueError:  # JSONDecodeError subclasses ValueError
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_jsoncache.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/codex_in_claude/_core/jsoncache.py tests/test_jsoncache.py
git commit -m "feat(core): add generic bounded JSON reader"
```

---

### Task 2: Model-catalog constants in `cli_contract.py`

**Files:**
- Modify: `src/codex_in_claude/cli_contract.py` (add a section after `HELP_GATED_FLAGS`, ~line 72)
- Test: `tests/test_cli_contract.py`

**Interfaces:**
- Produces: `MODELS_CACHE_FILENAME: str`, `MODELS_CACHE_MAX_BYTES: int`, `MODELS_CACHE_MAX_ENTRIES: int`, `MODEL_SLUG_PATTERN: re.Pattern`, `KNOWN_MODEL_SLUGS: tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli_contract.py
def test_known_model_slugs_match_slug_pattern():
    assert cli_contract.KNOWN_MODEL_SLUGS  # non-empty bundled fallback
    for slug in cli_contract.KNOWN_MODEL_SLUGS:
        assert cli_contract.MODEL_SLUG_PATTERN.match(slug), slug


def test_models_cache_filename_is_a_bare_name():
    # Joined under $CODEX_HOME — must never be absolute or contain a path separator.
    assert cli_contract.MODELS_CACHE_FILENAME == "models_cache.json"
    assert "/" not in cli_contract.MODELS_CACHE_FILENAME


def test_model_slug_pattern_rejects_junk():
    assert cli_contract.MODEL_SLUG_PATTERN.match("gpt-5.5")
    assert not cli_contract.MODEL_SLUG_PATTERN.match("bad slug!")
    assert not cli_contract.MODEL_SLUG_PATTERN.match("")
```

(If `tests/test_cli_contract.py` does not already `import` the module as `cli_contract`, mirror its existing import — e.g. `from codex_in_claude import cli_contract`.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli_contract.py -k model -v`
Expected: FAIL with `AttributeError: module 'codex_in_claude.cli_contract' has no attribute 'KNOWN_MODEL_SLUGS'`

- [ ] **Step 3: Add the constants**

Insert after the `HELP_GATED_FLAGS` block (after ~line 72), before `HELP_CACHE_TTL_SECONDS`:

```python
# --- Model catalog (advisory discovery) -----------------------------------------
# Codex caches its authoritative model list at $CODEX_HOME/models_cache.json (default
# ~/.codex). It is an UNDOCUMENTED internal file, written lazily by real Codex sessions
# (a fresh install has none) and NOT regenerated by `codex doctor`. We read it only to
# help an agent DISCOVER valid `--model` slugs; `codex exec` remains the real validator,
# so we never reject a slug merely because it is absent here.
MODELS_CACHE_FILENAME = "models_cache.json"
# Defensive bounds for that env-controlled file (consumed in codex_models via
# _core.jsoncache). The real file is ~150 KB; 1 MB is generous headroom.
MODELS_CACHE_MAX_BYTES = 1_000_000
MODELS_CACHE_MAX_ENTRIES = 256  # ignore anything past this many model entries
# A conservative slug shape; entries failing it are dropped (defends against a
# malformed/hostile cache surfacing junk to an agent).
MODEL_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Bundled advisory fallback used ONLY when the on-disk cache is absent/unreadable.
# Copied from codex-cli 0.141.0's models_cache.json on 2026-06-22. NOT authoritative
# and will age: it documents what shipped with the pinned CLI, not the live account's
# available models. Keep in lockstep with SUPPORTED_VERSIONS when bumping the CLI.
KNOWN_MODEL_SLUGS = ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "codex-auto-review")
```

(`re` is already imported at the top of `cli_contract.py`.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli_contract.py -k model -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/codex_in_claude/cli_contract.py tests/test_cli_contract.py
git commit -m "feat(cli-contract): add model-catalog cache constants and static fallback"
```

---

### Task 3: Catalog schema + reader (`schemas.py`, `codex_models.py`)

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (bump `FINGERPRINT` line 15; add models after `CapabilitiesResult` ~line 375; add `MODEL_CATALOG_SCHEMA` near `CAPABILITIES_SCHEMA` ~line 539)
- Create: `src/codex_in_claude/codex_models.py`
- Test: `tests/test_codex_models.py`

**Interfaces:**
- Consumes: `cli_contract.{MODELS_CACHE_FILENAME, MODELS_CACHE_MAX_BYTES, MODELS_CACHE_MAX_ENTRIES, MODEL_SLUG_PATTERN, KNOWN_MODEL_SLUGS}` (Task 2); `read_bounded_json` (Task 1).
- Produces: `schemas.ModelInfo(slug: str, display_name: str | None)`; `schemas.ModelCatalogResult(ok, source, models, fetched_at, cache_client_version, advisory, unavailable_reason, fingerprint)`; `schemas.MODEL_CATALOG_SCHEMA: dict`; `codex_models.read_model_catalog() -> ModelCatalogResult`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codex_models.py
import json
from pathlib import Path

from codex_in_claude import cli_contract, codex_models


def _write_cache(home: Path, payload: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / cli_contract.MODELS_CACHE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def test_reads_cache_when_present(tmp_path, monkeypatch):
    _write_cache(
        tmp_path,
        {
            "fetched_at": "2026-06-23T00:04:15Z",
            "client_version": "0.141.0",
            "models": [
                {"slug": "gpt-5.5", "display_name": "GPT-5.5"},
                {"slug": "gpt-5.4", "display_name": "GPT-5.4"},
            ],
        },
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cat = codex_models.read_model_catalog()
    assert cat.source == "cache"
    assert [m.slug for m in cat.models] == ["gpt-5.5", "gpt-5.4"]
    assert cat.models[0].display_name == "GPT-5.5"
    assert cat.fetched_at == "2026-06-23T00:04:15Z"
    assert cat.cache_client_version == "0.141.0"
    assert cat.advisory


def test_falls_back_to_static_when_cache_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))  # empty dir, no cache file
    cat = codex_models.read_model_catalog()
    assert cat.source == "static"
    assert {m.slug for m in cat.models} == set(cli_contract.KNOWN_MODEL_SLUGS)
    assert cat.fetched_at is None


def test_default_home_used_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _write_cache(tmp_path / ".codex", {"models": [{"slug": "gpt-5.5"}]})
    cat = codex_models.read_model_catalog()
    assert cat.source == "cache"
    assert [m.slug for m in cat.models] == ["gpt-5.5"]


def test_malformed_shape_falls_back_to_static(tmp_path, monkeypatch):
    _write_cache(tmp_path, {"models": "not-a-list"})
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codex_models.read_model_catalog().source == "static"


def test_junk_entries_are_filtered(tmp_path, monkeypatch):
    _write_cache(
        tmp_path,
        {"models": [{"slug": "gpt-5.5"}, {"slug": "bad slug!"}, {"no_slug": 1}, "nope"]},
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cat = codex_models.read_model_catalog()
    assert cat.source == "cache"
    assert [m.slug for m in cat.models] == ["gpt-5.5"]


def test_oversize_cache_falls_back(tmp_path, monkeypatch):
    tmp_path.mkdir(parents=True, exist_ok=True)
    big = {"models": [{"slug": f"m{i}", "display_name": "x" * 100} for i in range(100_000)]}
    (tmp_path / cli_contract.MODELS_CACHE_FILENAME).write_text(json.dumps(big), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codex_models.read_model_catalog().source == "static"


def test_source_none_when_no_cache_and_no_static(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(cli_contract, "KNOWN_MODEL_SLUGS", ())
    cat = codex_models.read_model_catalog()
    assert cat.source == "none"
    assert cat.unavailable_reason
    assert cat.models == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_codex_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codex_in_claude.codex_models'`

- [ ] **Step 3a: Bump FINGERPRINT in `schemas.py`**

Change line 15 from:
```python
FINGERPRINT = "codex-in-claude/0.1/schema-10"
```
to:
```python
FINGERPRINT = "codex-in-claude/0.1/schema-11"
```

- [ ] **Step 3b: Add the schema models**

Insert after `class CapabilitiesResult` (after ~line 375, before `class JobStarted`):

```python
ModelCatalogSource = Literal["cache", "static", "none"]


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    display_name: str | None = None


class ModelCatalogResult(BaseModel):
    """Advisory list of Codex model slugs for the optional `model` param.

    Discovery only: `source` says where it came from and `advisory` states it is not
    authoritative (Codex validates the real slug at exec time). Returned by the
    codex_models tool and the codex://models resource; deliberately NOT embedded in
    codex_capabilities, whose payload is fingerprint-cacheable and must stay stable.
    """

    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    source: ModelCatalogSource
    models: list[ModelInfo] = Field(default_factory=list)
    # From the on-disk cache; None for the static fallback / none.
    fetched_at: str | None = None
    cache_client_version: str | None = None
    advisory: str
    # Set only when source == "none" (no cache and no static fallback).
    unavailable_reason: str | None = None
    fingerprint: str = FINGERPRINT
```

Then add near `CAPABILITIES_SCHEMA` (~line 539):

```python
MODEL_CATALOG_SCHEMA = ModelCatalogResult.model_json_schema()
```

- [ ] **Step 3c: Create the reader module**

```python
# src/codex_in_claude/codex_models.py
"""Read Codex's on-disk model catalog for advisory `model`-slug discovery.

Codex-specific glue around the generic _core.jsoncache reader: resolves $CODEX_HOME,
reads models_cache.json, validates its shape defensively, and falls back to the bundled
KNOWN_MODEL_SLUGS when the cache is absent/unreadable. Discovery only — the result is
explicitly advisory; `codex exec` validates the real slug.
"""

from __future__ import annotations

import os
from pathlib import Path

from codex_in_claude import cli_contract
from codex_in_claude._core.jsoncache import read_bounded_json
from codex_in_claude.schemas import ModelCatalogResult, ModelInfo

_ADVISORY = (
    "Advisory model list for the `model` param — not authoritative. Codex validates "
    "the slug at run time; an unlisted slug may still work and a listed one may be "
    "unavailable to your account."
)
_UNAVAILABLE = (
    "No model catalog found: Codex has not written its on-disk cache yet (a fresh "
    "install populates it on first use) and no bundled fallback is configured. Pass a "
    "known Codex model slug directly; it is validated at run time."
)


def _codex_home() -> Path:
    """$CODEX_HOME if set, else ~/.codex (matching the codex CLI's own resolution)."""
    env = os.environ.get("CODEX_HOME")
    return Path(env).expanduser() if env else Path.home() / ".codex"


def _parse_models(raw: object) -> tuple[list[ModelInfo], str | None, str | None] | None:
    """Validate the cache's expected shape, or None if it has drifted.

    Returns (models, fetched_at, client_version). Drops entries whose slug fails
    MODEL_SLUG_PATTERN and caps the list at MODELS_CACHE_MAX_ENTRIES; returns None when
    the top-level shape is wrong or no valid entry survives (caller falls back to static).
    """
    if not isinstance(raw, dict):
        return None
    entries = raw.get("models")
    if not isinstance(entries, list):
        return None
    models: list[ModelInfo] = []
    for entry in entries[: cli_contract.MODELS_CACHE_MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not cli_contract.MODEL_SLUG_PATTERN.match(slug):
            continue
        display = entry.get("display_name")
        display = display if isinstance(display, str) and len(display) <= 128 else None
        models.append(ModelInfo(slug=slug, display_name=display))
    if not models:
        return None
    fetched_at = raw.get("fetched_at")
    fetched_at = fetched_at if isinstance(fetched_at, str) and len(fetched_at) <= 64 else None
    version = raw.get("client_version")
    version = version if isinstance(version, str) and len(version) <= 64 else None
    return models, fetched_at, version


def read_model_catalog() -> ModelCatalogResult:
    """The advisory model catalog: live cache if usable, else bundled static, else none."""
    raw = read_bounded_json(
        _codex_home() / cli_contract.MODELS_CACHE_FILENAME,
        cli_contract.MODELS_CACHE_MAX_BYTES,
    )
    parsed = _parse_models(raw) if raw is not None else None
    if parsed is not None:
        models, fetched_at, version = parsed
        return ModelCatalogResult(
            source="cache",
            models=models,
            fetched_at=fetched_at,
            cache_client_version=version,
            advisory=_ADVISORY,
        )
    if cli_contract.KNOWN_MODEL_SLUGS:
        return ModelCatalogResult(
            source="static",
            models=[ModelInfo(slug=s) for s in cli_contract.KNOWN_MODEL_SLUGS],
            advisory=_ADVISORY,
        )
    return ModelCatalogResult(source="none", advisory=_ADVISORY, unavailable_reason=_UNAVAILABLE)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_codex_models.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Run type-check (the new module + schema must type-clean)**

Run: `uv run ty check`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/codex_in_claude/schemas.py src/codex_in_claude/codex_models.py tests/test_codex_models.py
git commit -m "feat(schemas): add advisory model catalog reader and result schema"
```

---

### Task 4: Expose the catalog — `codex_models` tool + `codex://models` resource

**Files:**
- Modify: `src/codex_in_claude/server.py` (import; shared payload builder; tool; resource; `free_tools`; `tool_details`; `_TOOL_ERROR_CODES` ~line 634)
- Modify: `tests/test_server.py` (update fingerprint assertion line ~1419; add tool/resource tests)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `codex_models.read_model_catalog()` (Task 3); `schemas.MODEL_CATALOG_SCHEMA` (Task 3); existing `_FREE_READ`, `mcp`, `_TOOL_ERROR_CODES`, `ToolCapability`.
- Produces: MCP tool `codex_models() -> dict` and resource `codex://models`, both returning the `ModelCatalogResult` payload (`exclude_none=True`).

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_server.py
import json

import pytest


def test_codex_models_tool_returns_advisory_catalog():
    res = server.codex_models()
    assert res["ok"] is True
    assert res["source"] in {"cache", "static", "none"}
    assert res["advisory"]
    assert res["fingerprint"] == server.FINGERPRINT


def test_codex_models_listed_as_free_tool_and_detailed():
    caps = server.codex_capabilities()
    assert "codex_models" in caps["free_tools"]
    by_name = {t["name"]: t for t in caps["tool_details"]}
    assert "codex_models" in by_name
    assert by_name["codex_models"]["cost"] == "free"


async def test_codex_models_resource_matches_tool_payload():
    contents = await server.mcp.read_resource("codex://models")
    # FastMCP returns a list of resource contents; take the first's text/body.
    body = contents[0]
    text = getattr(body, "text", None) or getattr(body, "blob", None) or body
    payload = json.loads(text) if isinstance(text, str | bytes) else text
    assert payload == server.codex_models()
```

> NOTE on the resource test: the exact return type of `mcp.read_resource(...)` depends on the installed FastMCP version (it may yield objects with `.text`, or `.content`, or a JSON string). Before implementing, invoke the **fastmcp** skill to confirm the resource-registration decorator and the `read_resource` return shape, then adjust this test's unwrapping to match. Keep the assertion "resource payload == tool payload" intact.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_server.py -k codex_models -v`
Expected: FAIL with `AttributeError: module 'codex_in_claude.server' has no attribute 'codex_models'`

- [ ] **Step 3a: Import the reader and schema**

In `server.py`, add to the `from codex_in_claude import (...)` block (line ~29) the module `codex_models`, and add `MODEL_CATALOG_SCHEMA` to the `from codex_in_claude.schemas import (...)` block (line ~40). Match the existing alphabetical/grouping style already used there.

- [ ] **Step 3b: Add a shared payload builder + tool + resource**

Place immediately after the `codex_capabilities` function (after ~line 996):

```python
def _model_catalog_payload() -> dict:
    """Single source for the tool and resource so their payloads cannot drift."""
    return codex_models.read_model_catalog().model_dump(mode="json", exclude_none=True)


@mcp.tool(annotations=_FREE_READ, output_schema=MODEL_CATALOG_SCHEMA)
def codex_models() -> dict:
    """List Codex model slugs you can pass as `model`. Free — no model call.

    Advisory discovery only: read from Codex's on-disk cache when present, else a
    bundled fallback (`source` says which). `codex exec` validates the real slug, so an
    unlisted slug may still work and a listed one may be unavailable to your account.
    Same payload as the codex://models resource. Not fingerprint-stable — do not cache
    it by the capabilities fingerprint."""
    return _model_catalog_payload()


@mcp.resource("codex://models", mime_type="application/json")
def codex_models_resource() -> dict:
    """Advisory Codex model catalog (same payload as the codex_models tool)."""
    return _model_catalog_payload()
```

> NOTE: if the installed FastMCP requires a resource to return `str`/`bytes` rather than a `dict`, wrap with `json.dumps(...)` (add `import json` at the top of `server.py` if absent) and update the test unwrapping accordingly. Confirm via the fastmcp skill in Step 1.

- [ ] **Step 3c: Advertise the tool**

In `codex_capabilities`, add `"codex_models"` to the `free_tools=[...]` list, and add this `ToolCapability` to `tool_details=[...]` (e.g. right after the `codex_capabilities` entry):

```python
            ToolCapability(
                name="codex_models",
                cost="free",
                use_when="To discover valid `model` slugs before passing `model` to a "
                "Codex call; also available at the codex://models resource. Advisory — "
                "codex validates the real slug at run time.",
                returns="An advisory model catalog: source (cache|static|none), models "
                "(slug + display_name), and the cache's fetched_at/client_version when "
                "read from Codex's on-disk cache. Not fingerprint-stable — do not cache "
                "it by the capabilities fingerprint.",
            ),
```

- [ ] **Step 3d: Register its (empty) error-code list**

In the `_TOOL_ERROR_CODES` dict (~line 634) add:

```python
    "codex_models": [],
```

(The catalog reader tolerates all failures and always returns `ok: True`, so it has no error codes. This entry is required: `test_tool_error_codes_cover_every_tool_and_are_valid` asserts the dict keys equal the advertised tool set, and `codex_capabilities` raises `KeyError` otherwise.)

- [ ] **Step 3e: Update the existing fingerprint assertion**

In `tests/test_server.py` (~line 1419) change:
```python
    assert FINGERPRINT == "codex-in-claude/0.1/schema-10"
```
to:
```python
    assert FINGERPRINT == "codex-in-claude/0.1/schema-11"
```

- [ ] **Step 4: Run the targeted + invariant tests**

Run: `uv run pytest tests/test_server.py tests/test_packaging.py -v`
Expected: PASS — including `test_capabilities_match_registered_tools` (the tool is now registered AND advertised; the `codex://models` resource is not a tool so it does not affect that set) and `test_tool_error_codes_cover_every_tool_and_are_valid`.

- [ ] **Step 5: Update CHANGELOG**

Under `## [Unreleased]`, add an `### Added` entry (create the subheading if absent):

```markdown
### Added
- `codex_models` tool and `codex://models` resource expose an advisory catalog of
  Codex `model` slugs, read from Codex's on-disk cache (`$CODEX_HOME/models_cache.json`)
  with a bundled static fallback. Discovery only — `model` stays pass-through and
  `codex exec` validates the real slug. (`FINGERPRINT` → `schema-11`.)
```

- [ ] **Step 6: Commit**

```bash
git add src/codex_in_claude/server.py tests/test_server.py CHANGELOG.md
git commit -m "feat(tools): add codex_models tool and codex://models resource"
```

---

### Task 5: Pass-through guarantee + optional live behavior

**Files:**
- Modify: `tests/test_codex.py` (unit: arbitrary slug passes through command construction)
- Modify: `tests/test_integration.py` (optional, `integration`-marked: unlisted slug does not raise at the plugin layer)

**Interfaces:**
- Consumes: `codex.build_exec_command(...)` (existing). Mirrors the existing `_ALL_FLAGS` fixture used by neighboring build-command tests.

- [ ] **Step 1: Write the failing unit test**

```python
# add to tests/test_codex.py (alongside the other build_exec_command tests)
def test_build_exec_command_passes_arbitrary_model_through(tmp_path):
    # An unlisted/unknown slug is NOT validated here — codex exec is the validator.
    cmd, dropped = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        model="totally-made-up-model-9000",
        flag_support=_ALL_FLAGS,
    )
    assert cmd[cmd.index("--model") + 1] == "totally-made-up-model-9000"
    assert dropped == []
```

(`_ALL_FLAGS` is the existing module-level fixture used by `test_build_exec_command_*`. If its name differs, reuse whatever the neighboring passing tests pass as `flag_support`.)

- [ ] **Step 2: Run to verify it passes immediately**

Run: `uv run pytest tests/test_codex.py -k arbitrary_model -v`
Expected: PASS (this is a characterization test — pass-through already works; it locks the guarantee so a future validation gate can't sneak in).

- [ ] **Step 3 (optional): Add an opt-in live behavior test**

Only if a live check is wanted. Mark it `integration` so it is excluded by default and never runs in the coverage gate:

```python
# add to tests/test_integration.py
@pytest.mark.integration
async def test_unknown_model_returns_envelope_not_exception():
    """An unknown slug surfaces a structured envelope (likely ok:false), never a crash.

    Opt-in — calls the real codex CLI and may spend. Run with:
        uv run pytest -m integration --no-cov -k unknown_model
    """
    res = await server.codex_consult.fn(
        question="ping",
        model="definitely-not-a-real-model-zzz",
        workspace_root=str(ROOT),
    )
    assert "ok" in res  # structured envelope, not an exception
```

(Match the integration suite's existing conventions for invoking a tool and for `ROOT`/fixtures; adjust the call form — `.fn(...)` vs direct — to whatever the other integration tests use.)

- [ ] **Step 4: Run the unit test (skip integration)**

Run: `uv run pytest tests/test_codex.py -k model -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_codex.py tests/test_integration.py
git commit -m "test(tools): lock model pass-through and document unknown-slug behavior"
```

---

### Task 6: Full gate + branch wrap-up

**Files:** none (verification only).

- [ ] **Step 1: Run the complete gate**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```
Expected: all green, coverage ≥ 95%.

- [ ] **Step 2: Sanity-check the live cache path (manual, no spend)**

Run: `uv run python -c "from codex_in_claude.codex_models import read_model_catalog; print(read_model_catalog().model_dump(exclude_none=True))"`
Expected: on this machine (cache present) → `source='cache'` with the real slugs and a `fetched_at`.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/model-catalog
gh pr create --title "feat(tools): add codex_models tool and codex://models resource" \
  --body "Adds an advisory Codex model catalog (cache-backed with static fallback) via a free codex_models tool and a codex://models resource. model stays pass-through; FINGERPRINT bumped to schema-11. Label: breaking-change (FINGERPRINT/surface change per AGENTS.md)."
```

Then: get checks green, address Copilot review-on-push comments, resolve threads. **Do not merge** — the maintainer merges.

---

## Self-Review

**Spec coverage:**
- 1+3 combo (cache reader + static fallback, merge-prefer-cache) → Tasks 1–3. ✓
- `_core` boundary (generic reader only) → Task 1; Codex-specifics in Task 2/3. ✓
- Hardened parsing (byte cap, shape validation, slug pattern, entry cap, no path leak, absent-vs-malformed distinction) → Tasks 1–3. ✓
- Static fallback labeled advisory, version-stamped, non-authoritative; `source: none` when empty → Tasks 2–3. ✓
- No `stale` flag computed in capabilities; raw `fetched_at`/`cache_client_version` surfaced in the catalog itself → Task 3/4. ✓
- Resource + mirror tool (user's choice); capabilities carries only a static pointer (the tool entry), not the dynamic list → Task 4. ✓
- `model` stays pass-through, no validation gate → Task 5. ✓
- FINGERPRINT bump + CHANGELOG → Task 3/4. ✓
- Unit pass-through test + opt-in integration → Task 5. ✓

**Placeholder scan:** Two deliberate, flagged unknowns — the FastMCP `read_resource` return shape and dict-vs-str resource return — both routed through the fastmcp skill in Task 4 Step 1, with the invariant ("resource payload == tool payload") fixed. Test-fixture names (`_ALL_FLAGS`, `ROOT`, `.fn`) reference existing patterns the implementer mirrors. No "TODO/handle errors" placeholders.

**Type consistency:** `read_model_catalog()` returns `ModelCatalogResult` (Task 3) consumed by `_model_catalog_payload()` (Task 4). `ModelInfo.slug/display_name`, `ModelCatalogResult.source/models/fetched_at/cache_client_version/advisory/unavailable_reason` names are identical across schema definition, reader, and tests. `MODEL_CATALOG_SCHEMA` defined in Task 3, imported in Task 4. `KNOWN_MODEL_SLUGS`/`MODELS_CACHE_*`/`MODEL_SLUG_PATTERN` defined in Task 2, consumed in Task 3.
