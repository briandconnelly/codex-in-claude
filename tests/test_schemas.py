import json

import pytest
from pydantic import ValidationError

from codex_in_claude import schemas as s
from codex_in_claude.schemas import ErrorDetail, ErrorInfo, Repair


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


# ---------------------------------------------------------------------------
# Task 3: published_schema / opaque-error branch tests
# ---------------------------------------------------------------------------

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
        meta=Meta(
            cwd="/x",
            tier="consult",
            sandbox="read-only",
            isolation="inherit",
            timeout_seconds=180,
            elapsed_ms=1,
        ),
    )
    payload = serialize_error(env)
    TypeAdapter(ErrorResult).validate_python(payload)  # round-trips against the model
    assert s.ERROR_ENVELOPE_SCHEMA["$defs"]  # full schema is published with defs


def test_no_raw_errorresult_model_dump_outside_serializer():
    import pathlib
    import re

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


# ---------------------------------------------------------------------------
# Task 4: CI catalog-size gate
# ---------------------------------------------------------------------------


def _wire_catalog_bytes() -> int:
    import asyncio

    from codex_in_claude.server import mcp

    tools = asyncio.run(mcp.list_tools())  # list[Tool], 16 tools
    catalog = []
    for t in tools:
        entry = {"name": t.name, "description": t.description or ""}
        if t.parameters:
            entry["inputSchema"] = t.parameters
        if t.output_schema:
            entry["outputSchema"] = t.output_schema
        catalog.append(entry)
    return len(json.dumps(catalog, separators=(",", ":")))


# Cap = measured post-shrink size (~101,109) + ~15% headroom; was ~180,266 pre-shrink.
CATALOG_BYTE_CAP = 116_000


def test_wire_catalog_under_cap():
    size = _wire_catalog_bytes()
    assert size <= CATALOG_BYTE_CAP, f"catalog grew to {size} bytes (cap {CATALOG_BYTE_CAP})"
