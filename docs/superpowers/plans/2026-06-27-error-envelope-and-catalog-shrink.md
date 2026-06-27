# Error-envelope alignment + tool-catalog shrink — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape the error envelope to the agent-friendly-mcp §6 contract (symbolic repair, no placeholder nulls) and roughly halve the preloaded `tools/list` catalog by publishing success-only output schemas with one shared opaque error branch.

**Architecture:** A new `errors.py` owns error construction (`make_error`) and serialization (`serialize_error`) so the new shape and null-stripping live in one place that ~38 call sites route through. `schemas.py` gets the reshaped `ErrorInfo`/`Repair`/`ErrorDetail` models plus a rewritten schema builder that emits each tool's success branch(es) + one fully-opaque error branch with generated noise stripped. The full error schema is published once via a `codex://error-envelope` resource.

**Tech Stack:** Python 3.11+, Pydantic v2, FastMCP, pytest, `uv`, `ruff`, `ty`.

**Spec:** `docs/superpowers/specs/2026-06-27-error-envelope-and-catalog-shrink-design.md` (read it first).

## Global Constraints

- Tooling: `uv` only (`uv run pytest`, `uv run ruff …`, `uv run ty check`). Never pip/poetry.
- All three must pass before any task is done: `uv run ruff check . && uv run ruff format --check . && uv run ty check`.
- Tests: pytest, **95% coverage floor** (CI-enforced). Integration tests (`-m integration`) excluded by default.
- TDD: failing test first, then minimal code. Conventional Commits per commit.
- `_core` must never import from its parent package (`errors.py` lives in the parent package and may import from `schemas.py`; nothing in `_core` may import `errors.py`).
- This is a **breaking** change: bump `FINGERPRINT` `codex-in-claude/0.1/schema-15` → `codex-in-claude/0.1/schema-16` (Task 8 only — do not bump it piecemeal).
- **Do NOT** change version literals in `pyproject.toml`, `.claude-plugin/plugin.json`, or the `.mcp.json` pin — those move only in the dedicated release PR.
- Branch already exists: `feat/schema-publishing-redesign`. Do not commit to `main`.

---

## File Structure

- `src/codex_in_claude/schemas.py` — **modify**: add `RepairStep`, `Repair`, `ErrorDetail`; reshape `ErrorInfo`; remove `StatusResult.default_errors`; add `error_envelope_resource` to `CapabilitiesResult`; rewrite the schema builder; redefine all `*_SCHEMA` constants; add `ERROR_ENVELOPE_SCHEMA`.
- `src/codex_in_claude/errors.py` — **create**: `_REPAIR_BY_CODE` table, `make_error()`, `serialize_error()`.
- `src/codex_in_claude/codex.py` — **modify**: `_auth_error`, `_rate_limit_error`, `contract_changed_error`, `classify_failure` nonzero-exit builder → `make_error`.
- `src/codex_in_claude/orchestration.py` — **modify**: 3 `ErrorResult` sites + `_GITDIFF_REPAIR` → `make_error`/`serialize_error`.
- `src/codex_in_claude/delegate.py` — **modify**: 3 `ErrorInfo` sites → `make_error`.
- `src/codex_in_claude/_worker.py` — **modify**: crash-error site → `make_error`/`serialize_error`.
- `src/codex_in_claude/server.py` — **modify**: helper error builders, `_invalid_arguments_envelope`, `_STATE_TO_ERROR`, `_job_result_impl`, all `ErrorResult(...).model_dump` → `serialize_error`; add `codex://error-envelope` resource + capabilities pointer.
- `tests/test_errors.py` — **create**: factory + serializer + mapping coverage.
- `tests/test_schemas.py` — **create**: model invariants, builder, all-12-schema validation, catalog size cap.
- `tests/test_server.py`, `tests/test_worker.py`, `tests/test_orchestration.py`, `tests/test_delegate.py`, `tests/test_codex.py`, `tests/test_jobs.py` — **modify**: migrate to new field names; mechanical guard test.
- `CHANGELOG.md`, `COMPATIBILITY.md`, `README.md`, `docs/REFERENCE.md` — **modify**.

---

## Task 1: Error model shapes + invariants

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (the `InvalidArgument`/`ErrorInfo` block, ~352-392)
- Test: `tests/test_schemas.py` (create)

**Interfaces:**
- Produces: `RepairStep` (Literal), `Repair`, `ErrorDetail`, reshaped `ErrorInfo` with fields
  `code: ErrorCode`, `message: str`, `temporary: bool`, `retry_after_ms: int | None`,
  `repair: Repair | None`, `details: ErrorDetail | None`,
  `invalid_arguments: list[InvalidArgument] | None`, and the kept extensions
  `limit_bytes`/`actual_bytes`/`candidate_roots`. `InvalidArgument` is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_schemas.py
import pytest
from pydantic import ValidationError
from codex_in_claude.schemas import Repair, ErrorDetail, ErrorInfo


def test_repair_next_step_is_symbolic_and_optional_fields_default_none():
    r = Repair(next_step="poll_job_status")
    assert r.next_step == "poll_job_status"
    assert r.tool is None and r.arguments is None and r.alternative is None


def test_errorinfo_requires_temporary_and_retry_after_ms_in_schema():
    schema = ErrorInfo.model_json_schema()
    assert "temporary" in schema["required"]
    assert "retry_after_ms" in schema["required"]


def test_errorinfo_invariant_non_temporary_forbids_retry_after_ms():
    with pytest.raises(ValidationError):
        ErrorInfo(code="internal_error", message="x", temporary=False, retry_after_ms=5)


def test_errorinfo_retry_after_ms_must_be_non_negative():
    with pytest.raises(ValidationError):
        ErrorInfo(code="codex_rate_limited", message="x", temporary=True, retry_after_ms=-1)


def test_errorinfo_temporary_with_backoff_ok():
    e = ErrorInfo(code="codex_rate_limited", message="x", temporary=True, retry_after_ms=60000)
    assert e.temporary is True and e.retry_after_ms == 60000


def test_errordetail_has_no_value_field():
    assert "value" not in ErrorDetail.model_fields
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: FAIL (ImportError for `Repair`/`ErrorDetail`; `ErrorInfo` has no `temporary`).

- [ ] **Step 3: Implement the models**

In `schemas.py`, replace the `ErrorInfo` class (keep `InvalidArgument` as-is above it) with:

```python
RepairStep = Literal[
    "retry_after_delay",
    "correct_arguments",
    "use_allowed_value",
    "reduce_input",
    "use_workspace_in_roots",
    "poll_job_status",
    "list_jobs",
    "start_new_job",
    "authenticate",
    "install_codex",
    "install_git",
    "init_git_repo",
    "update_plugin",
    "inspect_and_retry",
    "retry_then_report",
]


class Repair(BaseModel):
    """Machine-actionable recovery guidance (agent-friendly-mcp §6). `next_step` is a
    STABLE SYMBOLIC label an agent branches on (not prose); `alternative` carries the
    human-readable fallback. `tool`/`arguments` name a tool to call to recover."""

    model_config = ConfigDict(extra="forbid")
    next_step: RepairStep
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    alternative: str | None = None


class ErrorDetail(BaseModel):
    """§6 details{field, value, reason}. `value` is deliberately omitted: a Literal/string
    param accepts arbitrary input that could be a secret, and best-effort redaction cannot
    reliably catch a plain one; the caller already holds what it sent. Documented divergence."""

    model_config = ConfigDict(extra="forbid")
    field: str | None = None
    reason: str | None = None
    allowed_values: list[str] | None = None


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ErrorCode
    message: str
    # §6: both always present in the canonical schema. `temporary` was `retryable`.
    temporary: bool = Field(...)
    retry_after_ms: int | None = Field(..., ge=0)
    repair: Repair | None = None  # omitted only when no corrective path exists
    details: ErrorDetail | None = None
    # Multi-field validation carrier (#136); details mirrors the first entry.
    invalid_arguments: list[InvalidArgument] | None = None
    # Documented top-level extensions (unchanged):
    limit_bytes: int | None = None
    actual_bytes: int | None = None
    candidate_roots: list[str] | None = None

    @model_validator(mode="after")
    def _retry_after_only_when_temporary(self) -> "ErrorInfo":
        if not self.temporary and self.retry_after_ms is not None:
            raise ValueError("retry_after_ms must be None when temporary is False")
        return self
```

Add `model_validator` to the pydantic import at the top of `schemas.py`:
`from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator`.
(`Any` is already imported.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint/type/format**

Run: `uv run ruff check . && uv run ruff format src/ tests/ && uv run ty check`
Expected: clean (note: other modules still reference old fields — `ty` may flag them; if so, proceed; Tasks 5–6 fix them, and they're committed together. If `ty` errors block, do Task 2–6 before this commit. Otherwise commit now.)

- [ ] **Step 6: Commit**

```bash
git add src/codex_in_claude/schemas.py tests/test_schemas.py
git commit -m "feat(schemas): reshape ErrorInfo to symbolic repair contract"
```

---

## Task 2: Central error factory + serializer (`errors.py`)

**Files:**
- Create: `src/codex_in_claude/errors.py`
- Test: `tests/test_errors.py` (create)

**Interfaces:**
- Consumes: `Repair`, `ErrorDetail`, `ErrorInfo`, `ErrorResult`, `ErrorCode` from `schemas`.
- Produces:
  - `make_error(code: ErrorCode, message: str, *, retry_after_ms: int | None = None, temporary: bool | None = None, repair_arguments: dict | None = None, repair_alternative: str | None = None, details: ErrorDetail | None = None, invalid_arguments: list | None = None, limit_bytes: int | None = None, actual_bytes: int | None = None, candidate_roots: list[str] | None = None) -> ErrorInfo`
  - `serialize_error(result: ErrorResult) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_errors.py
from codex_in_claude.errors import make_error, serialize_error, _REPAIR_BY_CODE
from codex_in_claude.schemas import ErrorResult, ErrorInfo, Meta, ErrorCode
import typing


def _meta():
    return Meta(cwd="/x", tier="consult", sandbox="read-only", isolation="inherit",
                timeout_seconds=180, elapsed_ms=1)


def test_repair_map_covers_every_error_code():
    codes = set(typing.get_args(ErrorCode))
    assert codes <= set(_REPAIR_BY_CODE), codes - set(_REPAIR_BY_CODE)


def test_make_error_derives_symbolic_repair():
    e = make_error("job_running", "still running", retry_after_ms=2000,
                   repair_arguments={"job_id": "j1"})
    assert e.temporary is True
    assert e.retry_after_ms == 2000
    assert e.repair.next_step == "poll_job_status"
    assert e.repair.tool == "codex_job_status"
    assert e.repair.arguments == {"job_id": "j1"}


def test_make_error_non_temporary_has_no_backoff():
    e = make_error("invalid_arguments", "bad arg")
    assert e.temporary is False and e.retry_after_ms is None
    assert e.repair.next_step == "correct_arguments"


def test_serialize_error_strips_nulls_but_keeps_retry_after_ms():
    env = ErrorResult(error=make_error("invalid_arguments", "bad"), meta=_meta())
    d = serialize_error(env)
    assert d["error"]["retry_after_ms"] is None          # kept (§6)
    assert "details" not in d["error"]                    # null stripped
    assert "limit_bytes" not in d["error"]                # null stripped
    assert d["ok"] is False


def test_serialize_error_keeps_populated_fields():
    env = ErrorResult(error=make_error("codex_rate_limited", "limited", retry_after_ms=60000),
                      meta=_meta())
    d = serialize_error(env)
    assert d["error"]["retry_after_ms"] == 60000
    assert d["error"]["temporary"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_errors.py -v`
Expected: FAIL (no module `errors`).

- [ ] **Step 3: Implement `errors.py`**

```python
"""Central construction and serialization of the error envelope.

One place owns the §6 symbolic-repair mapping and the null-stripping policy so every
error path emits the identical, machine-actionable shape. Nothing in `_core` imports
this module (it depends on the parent package's `schemas`)."""

from __future__ import annotations

from typing import Any

from codex_in_claude.schemas import (
    ErrorCode,
    ErrorDetail,
    ErrorInfo,
    ErrorResult,
    InvalidArgument,
    Repair,
    RepairStep,
)

# code -> (next_step, repair_tool, temporary, default alternative prose)
_REPAIR_BY_CODE: dict[str, tuple[RepairStep, str | None, bool, str]] = {
    "codex_not_found": ("install_codex", None, False,
        "Install the codex CLI, then rerun codex_status."),
    "codex_auth_required": ("authenticate", None, False,
        "Run `codex login` (ChatGPT or API key), then rerun codex_status."),
    "unexpanded_env_placeholder": ("update_plugin", None, False,
        "Set the referenced environment variable, or fix the plugin config."),
    "unsupported_tier": ("use_allowed_value", None, False,
        "Pass one of the tier's allowed_values."),
    "unsupported_sandbox": ("use_allowed_value", None, False,
        "Pass one of the sandbox's allowed_values."),
    "unsupported_isolation": ("use_allowed_value", None, False,
        "Pass one of isolation's allowed_values."),
    "unsupported_detail": ("use_allowed_value", None, False,
        "Pass one of detail's allowed_values."),
    "invalid_scope": ("correct_arguments", None, False, "Correct the scope argument."),
    "invalid_base": ("correct_arguments", None, False, "Correct the base argument."),
    "invalid_commit": ("correct_arguments", None, False, "Correct the commit argument."),
    "invalid_paths": ("correct_arguments", None, False, "Correct the paths argument."),
    "invalid_arguments": ("correct_arguments", None, False,
        "Check each tool's inputSchema (tools/list) or codex_capabilities, then retry."),
    "invalid_workspace_root": ("correct_arguments", None, False,
        "Pass an absolute path to an existing repository root."),
    "workspace_outside_roots": ("use_workspace_in_roots", None, False,
        "Pass a workspace_root inside one of candidate_roots."),
    "input_too_large": ("reduce_input", None, False,
        "Trim the input below limit_bytes, or raise the configured byte limit."),
    "not_a_git_repo": ("init_git_repo", None, False,
        "Point workspace_root at a git repository (propose needs one)."),
    "git_unavailable": ("install_git", None, False, "Install git and ensure it is on PATH."),
    "worktree_error": ("inspect_and_retry", None, False,
        "Retry; if it persists, inspect the repository state."),
    "context_too_large": ("reduce_input", None, False,
        "Narrow paths/scope so the gathered context fits."),
    "timeout": ("inspect_and_retry", None, True,
        "Narrow the task or raise timeout_seconds, then retry."),
    "nonzero_exit": ("inspect_and_retry", None, False,
        "Inspect the error; retry with a smaller or corrected task."),
    "invalid_json": ("retry_then_report", None, True,
        "Retry; if it persists, report a bug."),
    "schema_violation": ("retry_then_report", None, True,
        "Retry; if it persists, report a bug."),
    "internal_error": ("retry_then_report", None, True,
        "Retry; if it persists, run codex_status and inspect the repo."),
    "cli_contract_changed": ("update_plugin", None, False,
        "Update codex-in-claude (the installed codex CLI changed its contract)."),
    "codex_rate_limited": ("retry_after_delay", None, True,
        "Wait retry_after_ms before retrying; reduce concurrent codex calls."),
    "job_not_found": ("list_jobs", "codex_job_list", False,
        "Call codex_job_list to recover known job_ids in this workspace."),
    "job_running": ("poll_job_status", "codex_job_status", True,
        "Poll codex_job_status until result_available, honoring poll_after_ms."),
    "job_cancelled": ("start_new_job", None, False, "Start a new job."),
    "job_timeout": ("start_new_job", None, False, "Start a new job."),
    "job_failed": ("inspect_and_retry", None, False,
        "Inspect the failure detail; start a new job."),
}


def make_error(
    code: ErrorCode,
    message: str,
    *,
    retry_after_ms: int | None = None,
    temporary: bool | None = None,
    repair_arguments: dict[str, Any] | None = None,
    repair_alternative: str | None = None,
    details: ErrorDetail | None = None,
    invalid_arguments: list[InvalidArgument] | None = None,
    limit_bytes: int | None = None,
    actual_bytes: int | None = None,
    candidate_roots: list[str] | None = None,
) -> ErrorInfo:
    """Build the §6 error envelope for `code`, deriving the symbolic repair from the
    per-code table. `temporary` defaults to the table's value; pass it to override.
    `retry_after_ms` is honored only when the result is temporary."""
    next_step, tool, temp_default, alt_default = _REPAIR_BY_CODE[code]
    is_temp = temp_default if temporary is None else temporary
    backoff = retry_after_ms if is_temp else None
    repair = Repair(
        next_step=next_step,
        tool=tool,
        arguments=repair_arguments,
        alternative=repair_alternative or alt_default,
    )
    return ErrorInfo(
        code=code,
        message=message,
        temporary=is_temp,
        retry_after_ms=backoff,
        repair=repair,
        details=details,
        invalid_arguments=invalid_arguments,
        limit_bytes=limit_bytes,
        actual_bytes=actual_bytes,
        candidate_roots=candidate_roots,
    )


def serialize_error(result: ErrorResult) -> dict:
    """Serialize an ErrorResult, stripping absent optionals (§8) but ALWAYS retaining
    `error.retry_after_ms` (§6 wants the key present even when null)."""
    payload = result.model_dump(mode="json", exclude_none=True)
    payload.setdefault("error", {}).setdefault("retry_after_ms", None)
    return payload
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_errors.py -v`
Expected: PASS (6 tests). The `test_repair_map_covers_every_error_code` test guards completeness.

- [ ] **Step 5: Lint/type/format**

Run: `uv run ruff check src/codex_in_claude/errors.py tests/test_errors.py && uv run ruff format src/codex_in_claude/errors.py tests/test_errors.py && uv run ty check src/codex_in_claude/errors.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/codex_in_claude/errors.py tests/test_errors.py
git commit -m "feat(core): add central error factory and serializer"
```

---

## Task 3: Schema builder rewrite + constants + remove `default_errors`

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (`_object_union_schema` ~672-688; the `*_SCHEMA` block ~694-715; `StatusResult` ~421-441; `CapabilitiesResult` ~491-507)
- Test: `tests/test_schemas.py` (append)

**Interfaces:**
- Produces: `published_schema(*models)`, `_strip_schema_noise(node)`, `_OPAQUE_ERROR_BRANCH`,
  `ERROR_ENVELOPE_SCHEMA`; redefined `CONSULT_RESULT_SCHEMA`, `REVIEW_RESULT_SCHEMA`,
  `DELEGATE_RESULT_SCHEMA`, `JOB_RESULT_SCHEMA`, `STATUS_SCHEMA`, `CAPABILITIES_SCHEMA`,
  `MODEL_CATALOG_SCHEMA`, `JOB_STARTED_SCHEMA`, `JOB_STATUS_SCHEMA`, `DRY_RUN_SCHEMA`,
  `DELEGATE_DRY_RUN_SCHEMA`, `JOB_LIST_SCHEMA`; `CapabilitiesResult.error_envelope_resource`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_schemas.py (append)
import json
from codex_in_claude import schemas as s

_ALL_SCHEMAS = {
    "CONSULT_RESULT_SCHEMA": s.CONSULT_RESULT_SCHEMA,
    "REVIEW_RESULT_SCHEMA": s.REVIEW_RESULT_SCHEMA,
    "DELEGATE_RESULT_SCHEMA": s.DELEGATE_RESULT_SCHEMA,
    "JOB_RESULT_SCHEMA": s.JOB_RESULT_SCHEMA,
    "STATUS_SCHEMA": s.STATUS_SCHEMA,
    "CAPABILITIES_SCHEMA": s.CAPABILITIES_SCHEMA,
    "MODEL_CATALOG_SCHEMA": s.MODEL_CATALOG_SCHEMA,
    "JOB_STARTED_SCHEMA": s.JOB_STARTED_SCHEMA,
    "JOB_STATUS_SCHEMA": s.JOB_STATUS_SCHEMA,
    "DRY_RUN_SCHEMA": s.DRY_RUN_SCHEMA,
    "DELEGATE_DRY_RUN_SCHEMA": s.DELEGATE_DRY_RUN_SCHEMA,
    "JOB_LIST_SCHEMA": s.JOB_LIST_SCHEMA,
}


def _all_refs(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                yield v
            else:
                yield from _all_refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_refs(v)


def _has_key(node, key):
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_has_key(v, key) for v in node)
    return False


import pytest


@pytest.mark.parametrize("name,sch", _ALL_SCHEMAS.items())
def test_all_refs_resolve(name, sch):
    defs = set(sch.get("$defs", {}))
    for ref in _all_refs(sch):
        assert ref.startswith("#/$defs/"), f"{name}: non-local ref {ref}"
        assert ref.split("/")[-1] in defs, f"{name}: dangling ref {ref}"


@pytest.mark.parametrize("name,sch", _ALL_SCHEMAS.items())
def test_no_errorinfo_def_embedded(name, sch):
    assert "ErrorInfo" not in sch.get("$defs", {}), f"{name} still embeds ErrorInfo"


@pytest.mark.parametrize("name,sch", _ALL_SCHEMAS.items())
def test_noise_stripped_except_error_pointer(name, sch):
    assert not _has_key(sch, "title"), f"{name} has a title"
    assert not _has_key(sch, "default"), f"{name} has a default"
    # exactly one description survives: the opaque-error pointer
    text = json.dumps(sch)
    assert text.count('"description"') == 1
    assert "codex://error-envelope" in text


@pytest.mark.parametrize("name,sch", _ALL_SCHEMAS.items())
def test_opaque_error_branch_present(name, sch):
    branches = sch["anyOf"]
    err = [b for b in branches if b.get("properties", {}).get("ok", {}).get("const") is False]
    assert len(err) == 1, f"{name}: expected exactly one error branch"
    eb = err[0]
    assert eb["properties"]["error"] == {
        "type": "object",
        "description": "Populated error envelope; full schema at resource codex://error-envelope",
    }
    assert eb["properties"]["meta"] == {"type": "object"}
    assert set(eb["required"]) == {"ok", "error", "meta"}


def test_job_result_schema_has_four_branches():
    assert len(s.JOB_RESULT_SCHEMA["anyOf"]) == 4


def test_status_result_has_no_default_errors():
    assert "default_errors" not in s.StatusResult.model_fields


def test_error_envelope_schema_validates_runtime_error():
    from pydantic import TypeAdapter
    from codex_in_claude.errors import make_error, serialize_error
    from codex_in_claude.schemas import ErrorResult, Meta
    env = ErrorResult(
        error=make_error("job_running", "x", retry_after_ms=2000, repair_arguments={"job_id": "j"}),
        meta=Meta(cwd="/x", tier="consult", sandbox="read-only", isolation="inherit",
                  timeout_seconds=180, elapsed_ms=1),
    )
    payload = serialize_error(env)
    TypeAdapter(ErrorResult).validate_python(payload)  # round-trips against the model
    assert s.ERROR_ENVELOPE_SCHEMA["$defs"]  # full schema is published with defs
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: FAIL (old builder embeds `ErrorInfo`, has titles, single anyOf for some, `default_errors` exists).

- [ ] **Step 3: Implement the builder + constants**

In `schemas.py`, remove `default_errors` from `StatusResult` (delete the line
`default_errors: list[ErrorInfo] = Field(default_factory=list)` at ~436).

Add to `CapabilitiesResult` (after `deprecation_policy`):
```python
    error_envelope_resource: str = "codex://error-envelope"
```

Replace `_object_union_schema` and the `*_SCHEMA` block with:

```python
_OPAQUE_ERROR_BRANCH = {
    "type": "object",
    "required": ["ok", "error", "meta"],
    "properties": {
        "ok": {"const": False},
        "error": {
            "type": "object",
            "description": "Populated error envelope; full schema at resource codex://error-envelope",
        },
        "meta": {"type": "object"},
    },
}
_ERROR_POINTER_DESC = _OPAQUE_ERROR_BRANCH["properties"]["error"]["description"]


def _strip_schema_noise(node: object) -> object:
    """Recursively drop generated `title`/`description`/`default`, keeping only the one
    intentional error-pointer description."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in ("title", "default"):
                continue
            if k == "description" and v != _ERROR_POINTER_DESC:
                continue
            out[k] = _strip_schema_noise(v)
        return out
    if isinstance(node, list):
        return [_strip_schema_noise(v) for v in node]
    return node


def published_schema(*success_models: type[BaseModel]) -> dict:
    """Build a tool's advertised outputSchema: the success branch(es) plus ONE fully
    opaque error branch. The opaque branch references no $def, so $defs is exactly the
    success closure (no ErrorInfo, no dangling refs). Generated noise is stripped."""
    if len(success_models) == 1:
        adapter: TypeAdapter = TypeAdapter(success_models[0])
    else:
        union = success_models[0]
        for m in success_models[1:]:
            union = union | m  # type: ignore[operator]
        adapter = TypeAdapter(union)
    raw = adapter.json_schema(ref_template="#/$defs/{model}")
    if "anyOf" in raw:
        branches = list(raw["anyOf"])
    else:
        branches = [{k: v for k, v in raw.items() if k != "$defs"}]
    doc = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean", "description": "true = success result, false = error result"},
        },
        "required": ["ok"],
        "anyOf": [*branches, _OPAQUE_ERROR_BRANCH],
        "$defs": raw.get("$defs", {}),
    }
    return _strip_schema_noise(doc)  # type: ignore[return-value]


CONSULT_RESULT_SCHEMA = published_schema(ConsultResult)
REVIEW_RESULT_SCHEMA = published_schema(ReviewResult)
DELEGATE_RESULT_SCHEMA = published_schema(DelegateResult)
JOB_RESULT_SCHEMA = published_schema(DelegateResult, ConsultResult, ReviewResult)
STATUS_SCHEMA = published_schema(StatusResult)
CAPABILITIES_SCHEMA = published_schema(CapabilitiesResult)
MODEL_CATALOG_SCHEMA = published_schema(ModelCatalogResult)
JOB_STARTED_SCHEMA = published_schema(JobStarted)
JOB_STATUS_SCHEMA = published_schema(JobStatus)
DRY_RUN_SCHEMA = published_schema(DryRunResult)
DELEGATE_DRY_RUN_SCHEMA = published_schema(DelegateDryRunResult)
JOB_LIST_SCHEMA = published_schema(JobListResult)

# The full error envelope, published once (resource codex://error-envelope). Root is the
# outer ErrorResult (ok/error/meta) with all $defs — the canonical, discoverable contract.
ERROR_ENVELOPE_SCHEMA = TypeAdapter(ErrorResult).json_schema(ref_template="#/$defs/{model}")
```

Note: the error-pointer description must survive stripping. Since the opaque branch is a
shared literal, the `count('"description"') == 1` test confirms it. Also confirm the `ok`
discriminator description does not duplicate it — change the top-level `ok` description to be
stripped too, OR accept two descriptions. To keep exactly one, the top-level `ok` property's
description IS stripped by `_strip_schema_noise` (it differs from `_ERROR_POINTER_DESC`), so
only the pointer remains. Good.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 5: Sanity-measure the shrink (informational)**

Run:
```bash
uv run python -c "import json; from codex_in_claude import schemas as s; \
print(sum(len(json.dumps(v,separators=(',',':'))) for k,v in vars(s).items() if k.endswith('_SCHEMA')))"
```
Expected: well under the ~136,846 measured for the old constants (target ≈ 60–75K). Record the number for Task 4's cap.

- [ ] **Step 6: Lint/type/format + commit**

Run: `uv run ruff check . && uv run ruff format src/ tests/ && uv run ty check`
(If `ty` flags `server.py`/`orchestration.py` still using old fields, that's expected — those land in Tasks 5–6. If the gate must be green per-commit, defer this commit until after Task 6 and commit them together. Otherwise:)

```bash
git add src/codex_in_claude/schemas.py tests/test_schemas.py
git commit -m "feat(schemas): publish opaque-error output schemas; remove default_errors"
```

---

## Task 4: CI catalog-size gate

**Files:**
- Test: `tests/test_schemas.py` (append) — or `tests/test_packaging.py` if you prefer the packaging suite; this plan uses `test_schemas.py`.

**Interfaces:**
- Consumes: the `*_SCHEMA` constants and the server's tool registry.

- [ ] **Step 1: Write the failing test**

The catalog cap should measure the REAL `tools/list` wire bytes. Build it from the registered FastMCP tools.

```python
# tests/test_schemas.py (append)
import json


def _wire_catalog_bytes() -> int:
    from codex_in_claude.server import mcp
    import asyncio
    tools = asyncio.run(mcp.get_tools())  # name -> Tool
    catalog = []
    for name, tool in tools.items():
        entry = {"name": name, "description": tool.description or "",
                 "inputSchema": tool.parameters}
        out = getattr(tool, "output_schema", None)
        if out:
            entry["outputSchema"] = out
        catalog.append(entry)
    return len(json.dumps(catalog, separators=(",", ":")))


# Cap = measured post-change size + ~15% headroom. FILL FROM Task 3 Step 5 / a real run.
CATALOG_BYTE_CAP = 110_000  # placeholder; set to measured*1.15 below


def test_wire_catalog_under_cap():
    size = _wire_catalog_bytes()
    assert size <= CATALOG_BYTE_CAP, f"catalog grew to {size} bytes (cap {CATALOG_BYTE_CAP})"
```

- [ ] **Step 2: Run to measure the real size**

Run: `uv run pytest tests/test_schemas.py::test_wire_catalog_under_cap -v`
If the assertion fails or to learn the real number, temporarily print `size`. The exact FastMCP
accessor for a tool's output schema may differ (`tool.output_schema` vs `tool.outputSchema` vs
`tool.fn_metadata`); adjust `_wire_catalog_bytes` to read what FastMCP actually stores (inspect
with `uv run python -c "from codex_in_claude.server import mcp; import asyncio; t=asyncio.run(mcp.get_tools()); x=next(iter(t.values())); print([a for a in dir(x) if 'chema' in a.lower()])"`).

- [ ] **Step 3: Set the cap**

Set `CATALOG_BYTE_CAP = round(measured * 1.15)`. Confirm it is well under the old ~180,000.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_schemas.py::test_wire_catalog_under_cap -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_schemas.py
git commit -m "test(schemas): cap serialized tools/list catalog size"
```

---

## Task 5: Route library error sites through the factory (`codex.py`, `orchestration.py`, `delegate.py`, `_worker.py`)

**Files:**
- Modify: `src/codex_in_claude/codex.py` (`_auth_error` ~187, `_rate_limit_error` ~195, `contract_changed_error` ~206, nonzero-exit `ErrorInfo` ~256)
- Modify: `src/codex_in_claude/orchestration.py` (`_GITDIFF_REPAIR` map + sites ~50,174,249)
- Modify: `src/codex_in_claude/delegate.py` (sites ~96,106,134)
- Modify: `src/codex_in_claude/_worker.py` (crash site ~163)
- Test: `tests/test_codex.py`, `tests/test_orchestration.py`, `tests/test_delegate.py`, `tests/test_worker.py` (modify)

**Interfaces:**
- Consumes: `make_error`, `serialize_error` from `errors`.

- [ ] **Step 1: Update the tests first (new field names)**

In each test file, change assertions reading `error["retryable"]` → `error["temporary"]`,
`error["repair"]` (string) → `error["repair"]["next_step"]` (symbolic) / `["alternative"]`,
`error["repair_tool"]` → `error["repair"]["tool"]`,
`error["repair_tool_params"]` → `error["repair"]["arguments"]`,
`error["offending_param"]` → `error["details"]["field"]`. Add an assertion on the symbolic
`next_step` for at least one error per file (e.g. delegate `not_a_git_repo` → `init_git_repo`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_codex.py tests/test_orchestration.py tests/test_delegate.py tests/test_worker.py -v`
Expected: FAIL (old field names / direct `ErrorInfo(repair="...")` still produce old shape; some `ty`-level breakage).

- [ ] **Step 3: Convert `codex.py`**

```python
from codex_in_claude.errors import make_error
# _auth_error:
def _auth_error() -> ErrorInfo:
    return make_error("codex_auth_required",
        "Codex is not authenticated.")
# _rate_limit_error:
def _rate_limit_error(retry_after_ms: int) -> ErrorInfo:
    return make_error("codex_rate_limited",
        "Codex hit a usage/rate limit.", retry_after_ms=retry_after_ms)
# contract_changed_error: keep its message, use make_error("cli_contract_changed", <message>)
# nonzero-exit (~256):
    detail = (event_error or run.stderr or run.stdout).strip()[:300]
    return make_error("nonzero_exit", f"codex exited {run.exit_code}: {detail}")
```
(Preserve each site's existing `message` text where present; only the construction mechanism and
field names change. The CLI `detail` continues to pass through existing redaction upstream of this
call — do not add raw secrets.)

- [ ] **Step 4: Convert `orchestration.py`, `delegate.py`, `_worker.py`**

Replace each `ErrorResult(error=ErrorInfo(code=..., message=..., repair="prose", retryable=...), meta=meta).model_dump(mode="json")` with
`serialize_error(ErrorResult(error=make_error(code, message, ...), meta=meta))`.
For `_GITDIFF_REPAIR` (orchestration ~174): drop the prose map; call
`make_error(code, message)` and let the table supply `next_step`/`alternative` (add any missing
gitdiff codes to `_REPAIR_BY_CODE` in Task 2 if a code isn't already mapped — verify with
`test_repair_map_covers_every_error_code`).
For `input_too_large` (orchestration ~249): `make_error("input_too_large", msg, limit_bytes=..., actual_bytes=...)`.
For `_worker.py` (~163): `serialize_error(ErrorResult(error=make_error("internal_error", f"background worker crashed: {exc}"[:300]), meta=_meta_from_spec(spec)))`.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_codex.py tests/test_orchestration.py tests/test_delegate.py tests/test_worker.py -v`
Expected: PASS.

- [ ] **Step 6: Lint/type/format + commit**

Run: `uv run ruff check . && uv run ruff format src/ tests/ && uv run ty check`
```bash
git add src/codex_in_claude/codex.py src/codex_in_claude/orchestration.py \
        src/codex_in_claude/delegate.py src/codex_in_claude/_worker.py tests/
git commit -m "refactor(core): route library error paths through the factory"
```

---

## Task 6: Route `server.py` through the serializer + reshape `invalid_arguments` + job-result migration

**Files:**
- Modify: `src/codex_in_claude/server.py` (helpers ~337,568,585,624; `_invalid_arguments_envelope` ~269-348; `_STATE_TO_ERROR` ~2222 + uses ~2317,2442; `_job_result_impl` ~2435; all `ErrorResult(...).model_dump(mode="json")`)
- Test: `tests/test_server.py`, `tests/test_jobs.py` (modify); `tests/test_schemas.py` (append mechanical guard)

**Interfaces:**
- Consumes: `make_error`, `serialize_error`.

- [ ] **Step 1: Write the mechanical guard test + updated server tests**

```python
# tests/test_schemas.py (append) — prevents new bypasses
import pathlib, re


def test_no_raw_errorresult_model_dump_outside_serializer():
    src = pathlib.Path("src/codex_in_claude")
    offenders = []
    for p in src.rglob("*.py"):
        if p.name == "errors.py":
            continue
        text = p.read_text()
        # flag ErrorResult(...).model_dump( on one logical line/expression
        if re.search(r"ErrorResult\([^\n]*\)\s*\.model_dump\(", text):
            offenders.append(p.name)
        # also flag the multiline form via a simple heuristic
    assert not offenders, f"raw ErrorResult.model_dump outside errors.py: {offenders}"
```
Also update `tests/test_server.py` invalid-arguments assertions: `offending_param` →
`details.field`; `allowed_values` (top-level) → `details.allowed_values`; `retryable` →
`temporary`; `repair` prose → `repair.next_step == "correct_arguments"`. `invalid_arguments`
list entries are unchanged. Update `tests/test_jobs.py` job-state error assertions to
`repair.next_step` (`job_running` → `poll_job_status`, `job_not_found` → `list_jobs`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_server.py tests/test_jobs.py tests/test_schemas.py -v`
Expected: FAIL (raw `model_dump` offenders found; old field names).

- [ ] **Step 3: Convert all `server.py` error sites**

- Replace every `ErrorResult(error=<x>, meta=<m>).model_dump(mode="json")` with
  `serialize_error(ErrorResult(error=<x>, meta=<m>))`. Where `<x>` is an inline
  `ErrorInfo(...)`, convert it to `make_error(...)`.
- `_resolve_isolation`/`_resolve_detail` (return `ErrorInfo`): build via
  `make_error("unsupported_isolation"/"unsupported_detail", msg)` (drop `allowed_values=`
  sibling — fold into `details=ErrorDetail(allowed_values=[...])`).
- `_invalid_arguments_envelope` (~337): change the final return to:
  ```python
  first = items[0]
  return serialize_error(ErrorResult(
      error=make_error(
          "invalid_arguments",
          message[:300],
          repair_alternative=repair,   # the existing type-aware prose → alternative
          details=ErrorDetail(field=first.field, allowed_values=first.allowed_values),
          invalid_arguments=items,
      ),
      meta=meta,
  ))
  ```
- `_STATE_TO_ERROR` (~2222): values become `(code, message)` only — drop the prose repair
  string (the table in `errors.py` owns repair). Update both use sites (~2317, ~2442) to call
  `make_error(code, message)` (+ `repair_arguments={"job_id": job_id}` for `job_running`).
- `_job_result_impl` stored-error path (~2435): after `ErrorResult.model_validate(payload)`
  succeeds, return `serialize_error(<validated model>)` rather than the raw `payload`:
  ```python
  try:
      validated = ErrorResult.model_validate(payload)
  except ValidationError as exc:
      return _job_result_corrupt(f"stored error result was malformed: {exc}", meta)
  return serialize_error(validated)
  ```
  (A pre-upgrade schema-15 payload fails validation → corrupt path: the approved
  invalidate-documented migration.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_server.py tests/test_jobs.py tests/test_schemas.py -v`
Expected: PASS, including `test_no_raw_errorresult_model_dump_outside_serializer`.

- [ ] **Step 5: Full suite + gate**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run ty check`
Expected: all PASS, coverage ≥ 95%.

- [ ] **Step 6: Commit**

```bash
git add src/codex_in_claude/server.py tests/
git commit -m "refactor(tools): route server error paths through the serializer"
```

---

## Task 7: Publish the error envelope as a resource + capabilities pointer

**Files:**
- Modify: `src/codex_in_claude/server.py` (resource registration near `codex://models` ~1243; capabilities builder)
- Test: `tests/test_server.py` (append)

**Interfaces:**
- Consumes: `ERROR_ENVELOPE_SCHEMA`, `CapabilitiesResult.error_envelope_resource`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_server.py (append)
def test_error_envelope_resource_returns_full_schema():
    from codex_in_claude.server import error_envelope_resource
    schema = error_envelope_resource()
    assert schema["$defs"]
    assert "ErrorInfo" in schema["$defs"]  # full shape is here, not on the wire branches


def test_capabilities_advertises_error_envelope_pointer():
    # call the capabilities tool/impl and assert the pointer
    from codex_in_claude.server import _capabilities_payload  # or the tool fn
    caps = _capabilities_payload()
    assert caps["error_envelope_resource"] == "codex://error-envelope"
```
(Adjust import names to the actual capabilities builder; if capabilities is built inline in the
tool, factor a small `_capabilities_payload()` helper to make it testable.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_server.py -k error_envelope -v`
Expected: FAIL (no resource / no pointer).

- [ ] **Step 3: Implement**

```python
# near codex://models resource
@mcp.resource("codex://error-envelope", mime_type="application/schema+json")
def error_envelope_resource() -> dict:
    """The canonical full error envelope (ErrorResult). The per-tool outputSchemas carry
    only a compact opaque error branch; this is the discoverable full shape."""
    return ERROR_ENVELOPE_SCHEMA
```
Import `ERROR_ENVELOPE_SCHEMA` in the schemas import block. Ensure `CapabilitiesResult` is
constructed without overriding `error_envelope_resource` (the default `"codex://error-envelope"`
flows through), or set it explicitly.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_server.py -k error_envelope -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_in_claude/server.py tests/test_server.py
git commit -m "feat(tools): publish codex://error-envelope resource and pointer"
```

---

## Task 8: Docs, CHANGELOG, FINGERPRINT bump

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (`FINGERPRINT`)
- Modify: `CHANGELOG.md`, `COMPATIBILITY.md`, `README.md` (~158-159), `docs/REFERENCE.md` (~18-31)
- Test: `tests/test_schemas.py` (the fingerprint constant is asserted indirectly; add an explicit check)

- [ ] **Step 1: Bump FINGERPRINT**

In `schemas.py`: `FINGERPRINT = "codex-in-claude/0.1/schema-16"`.

- [ ] **Step 2: Update REFERENCE.md error section (~18-31)**

Replace the old field bullets with the new shape:
```markdown
- `code` — a stable error code from a fixed set.
- `message` — human-readable detail.
- `temporary` + `retry_after_ms` — whether retrying can succeed and how long to back off
  (`retry_after_ms` is always present; `null` unless `temporary` is true).
- `repair` — `{next_step, tool, arguments, alternative}`: `next_step` is a stable SYMBOLIC
  label you branch on (e.g. `poll_job_status`, `correct_arguments`); `tool`/`arguments` name a
  tool to call to recover; `alternative` is prose fallback. Omitted only when no corrective
  path exists.
- `details` — `{field, reason, allowed_values}` for a single offending field. The rejected
  `value` is deliberately never echoed (it may be a secret).
- `invalid_arguments` — set when `code` is `invalid_arguments`: a list of
  `{field, reason, allowed_values}` per offending argument; `details` mirrors the first.
- `limit_bytes`/`actual_bytes`/`candidate_roots` — size/roots context for the relevant codes.

Absent optional fields are omitted from the payload (no placeholder nulls), except
`retry_after_ms`. The full schema is published at the `codex://error-envelope` resource.
```

- [ ] **Step 3: Update README.md (~158-159)**

Change the error description sentence to:
```markdown
machine-actionable `error` — a stable `code`, `temporary`/`retry_after_ms`, a symbolic
`repair{next_step,tool,arguments,alternative}`, and `details{field,reason,allowed_values}`
for automated recovery (full schema at the `codex://error-envelope` resource).
```

- [ ] **Step 4: Update COMPATIBILITY.md**

In the failure-classification section, change `retryable=True` references to `temporary=True`.
Add a short subsection documenting the canonical error envelope, the opaque wire branch, the
`details.value` omission, and the invalidate-on-upgrade job-migration policy.

- [ ] **Step 5: Update CHANGELOG.md `## [Unreleased]`**

```markdown
### Changed
- **BREAKING:** Error envelope reshaped to the agent-friendly-mcp §6 contract: `retryable` →
  `temporary`; flat `repair`/`repair_tool`/`repair_tool_params`/`offending_param`/`allowed_values`
  fold into `repair{next_step,tool,arguments,alternative}` (symbolic `next_step`) and
  `details{field,reason,allowed_values}`. Absent optionals are stripped (placeholder nulls gone);
  `retry_after_ms` is always present. (#135)
- **BREAKING:** Per-tool `outputSchema`s now publish the success shape plus one compact opaque
  error branch; the full error schema moves to the `codex://error-envelope` resource. Cuts the
  preloaded `tools/list` catalog ~50%. (#137)
- **BREAKING:** Removed unused `StatusResult.default_errors`.
- Background-job results written by a pre-upgrade server are treated as expired/corrupt
  (invalidate-documented migration).
- `FINGERPRINT` → `codex-in-claude/0.1/schema-16`.

### Added
- `codex://error-envelope` resource publishing the full error schema; a pointer to it in
  `codex_capabilities`.
- CI gate capping the serialized `tools/list` catalog size.
```

- [ ] **Step 6: Verify + commit**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run ty check`
Expected: all PASS (any test asserting the fingerprint string now expects `schema-16`; update it).
```bash
git add src/codex_in_claude/schemas.py CHANGELOG.md COMPATIBILITY.md README.md docs/REFERENCE.md tests/
git commit -m "docs: document reshaped error envelope and catalog shrink"
```

---

## Task 9: Final verification gate

- [ ] **Step 1: Full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`
Expected: all PASS, coverage ≥ 95%.

- [ ] **Step 2: Confirm the shrink**

Run:
```bash
uv run python -c "import json,asyncio; from codex_in_claude.server import mcp; \
t=asyncio.run(mcp.get_tools()); print('tools:', len(t))"
uv run pytest tests/test_schemas.py::test_wire_catalog_under_cap -v
```
Expected: catalog well under the old ~180 KB; cap test green.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/schema-publishing-redesign
gh pr create --title "refactor(schemas)!: align error envelope (§6) and shrink tool catalog" \
  --label breaking-change --label enhancement \
  --body "$(cat <<'EOF'
Reshapes the error envelope to the agent-friendly-mcp §6 unified-repair contract and shrinks the
preloaded tools/list catalog ~50% via success-only output schemas with one shared opaque error
branch (full schema at codex://error-envelope).

Closes #135
Closes #137

Breaking: see CHANGELOG `## [Unreleased]` and FINGERPRINT schema-16. Per repo policy this PR does
NOT bump version literals (that is the release PR's job).

🤖 Generated with Claude Code
EOF
)"
```

- [ ] **Step 4: Iterate on Copilot review**

Address Copilot's review-on-push comments (verify each against the code; fix valid ones; reply to
each, including declined ones), re-push, repeat until no new actionable comments, then resolve
threads. **Do not merge** — the maintainer merges.

---

## Self-Review (completed during authoring)

- **Spec coverage:** A1 opaque branch → Task 3; A2 builder algo → Task 3; A3 stripping → Task 3;
  A4 resource+pointer → Tasks 3 (schema), 7 (resource/pointer); A5 CI cap → Task 4; B1 renames +
  symbolic next_step + table → Tasks 1, 2, 5, 6; B2 details/invalid_arguments → Tasks 1, 6; B3
  invariants → Task 1; B4 serializer + ~38 sites + mechanical test → Tasks 2, 5, 6; B5 persisted
  jobs/worker + migration → Tasks 5, 6; §6 divergence docs + security tests → Tasks 2, 6, 8;
  bookkeeping (FINGERPRINT/CHANGELOG/README/REFERENCE/COMPATIBILITY, no version-literal bump, `!`
  title) → Task 8, Task 9. `default_errors` removal → Task 3.
- **Placeholder scan:** the only deferred value is `CATALOG_BYTE_CAP` (Task 4), explicitly measured
  and set within the task — not a plan placeholder.
- **Type consistency:** `make_error`/`serialize_error`/`published_schema`/`_REPAIR_BY_CODE`/
  `Repair`/`ErrorDetail`/`RepairStep` names used consistently across Tasks 1–8.
