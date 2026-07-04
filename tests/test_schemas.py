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


def test_errordetail_accepts_field_or_fields_alone():
    # F2: `field` names one offending input; `fields` names a set whose
    # combination is invalid. Each is valid on its own.
    assert ErrorDetail(field="question").field == "question"
    assert ErrorDetail(fields=["question", "extra_context"]).fields == [
        "question",
        "extra_context",
    ]


def test_errordetail_rejects_both_field_and_fields():
    # F2: at most one of field/fields, never both.
    with pytest.raises(ValidationError):
        ErrorDetail(field="question", fields=["question", "extra_context"])


def test_errordetail_allows_neither_carrier():
    # F2: neither is required — an enum failure may carry only allowed_values (e.g.
    # gitdiff_error's invalid_scope path). This must stay valid.
    d = ErrorDetail(allowed_values=["working_tree", "branch", "commit"])
    assert d.field is None and d.fields is None


def test_errordetail_rejects_empty_fields():
    # A "combination" of zero inputs is meaningless; the constraint is published as
    # minItems: 1 (not merely runtime-enforced) so the advertised contract is honest.
    with pytest.raises(ValidationError):
        ErrorDetail(fields=[])
    assert "minItems" in json.dumps(ErrorDetail.model_json_schema()["properties"]["fields"])


def test_errordetail_rejects_duplicate_fields():
    with pytest.raises(ValidationError):
        ErrorDetail(fields=["question", "question"])
    # Uniqueness is advertised in the published schema, not merely runtime-enforced,
    # so schema-driven clients see the same contract (Copilot review).
    assert "uniqueItems" in json.dumps(ErrorDetail.model_json_schema()["properties"]["fields"])


# ---------------------------------------------------------------------------
# Task 3: published_schema / opaque-error branch tests
# ---------------------------------------------------------------------------

_ALL_SCHEMAS = {
    "CONSULT_RESULT_SCHEMA": s.CONSULT_RESULT_SCHEMA,
    "REVIEW_RESULT_SCHEMA": s.REVIEW_RESULT_SCHEMA,
    "DELEGATE_RESULT_SCHEMA": s.DELEGATE_RESULT_SCHEMA,
    # JOB_RESULT_SCHEMA is deliberately excluded: it is a handcrafted opaque-branch
    # dict (not built via published_schema), so the noise-stripping/$defs invariants
    # below don't apply to it. See TestJobResultSchemaSlim for its own contract.
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


def _annotation_title_present(node: object) -> bool:
    """Return True if any schema OBJECT has a top-level ``title`` annotation key.

    Distinguishes annotation ``title`` (a key directly in a schema dict alongside
    ``type``/``properties``/etc.) from a property NAME that happens to be ``title``
    (a key inside a ``properties`` or ``$defs`` mapping).  The latter is legitimate
    and must not be flagged.
    """
    if isinstance(node, dict):
        # Keys inside these maps are property/def names, not annotations.
        _SUBSCHEMA_MAP_KEYS = frozenset(
            ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas")
        )
        if "title" in node:
            return True
        for k, v in node.items():
            if k in _SUBSCHEMA_MAP_KEYS and isinstance(v, dict):
                # Recurse into sub-schema values only, not into the map keys.
                if any(_annotation_title_present(sub) for sub in v.values()):
                    return True
            elif _annotation_title_present(v):
                return True
    elif isinstance(node, list):
        return any(_annotation_title_present(v) for v in node)
    return False


def _is_meta_bearing(sch) -> bool:
    """True if any success (ok:true) branch carries a `meta` property — those are the
    schemas whose meta is opaqued to a codex://result-meta pointer (audit F1)."""
    for br in sch.get("anyOf", []):
        props = br.get("properties", {})
        if props.get("ok", {}).get("const") is True and "meta" in props:
            return True
    return False


@pytest.mark.parametrize("name,sch", _ALL_SCHEMAS.items())
def test_noise_stripped_except_pointers(name, sch):
    # No schema-object-level ``title`` annotations (generated Pydantic noise).
    assert not _annotation_title_present(sch), f"{name} has a title annotation"
    # No ``default`` annotations anywhere (a field named ``default`` is fine but
    # Pydantic models here do not use that name, so _has_key is safe for defaults).
    assert not _has_key(sch, "default"), f"{name} has a default"
    text = json.dumps(sch)
    # The opaque-error pointer description always survives.
    assert "codex://error-envelope" in text
    # A meta-bearing schema also keeps its opaque result-meta pointer (audit F1): two
    # surviving descriptions. A schema without a success-branch meta keeps only one.
    if _is_meta_bearing(sch):
        assert text.count('"description"') == 2, f"{name}: expected error + meta pointer"
        assert "codex://result-meta" in text
        assert '"$defs":{"Meta"' not in text.replace(" ", "")
        assert '"Meta":' not in text, f"{name}: Meta $def should be pruned"
    else:
        assert text.count('"description"') == 1, f"{name}: expected only error pointer"
    # Finding.title property is preserved in schemas that carry findings.
    if "Finding" in sch.get("$defs", {}):
        finding_def = sch["$defs"]["Finding"]
        assert "title" not in finding_def, (
            "Finding $def must not have an object-level title annotation"
        )
        assert "title" in finding_def.get("properties", {}), (
            "Finding.title property must be present"
        )


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


def test_job_result_schema_has_two_branches():
    # Opaque success branch + the shared opaque error branch (audit F1).
    assert len(s.JOB_RESULT_SCHEMA["anyOf"]) == 2


# ---------------------------------------------------------------------------
# Audit F1 (#173): opaque meta branch + $defs pruning
# ---------------------------------------------------------------------------

_META_BEARING = {
    "CONSULT_RESULT_SCHEMA": s.CONSULT_RESULT_SCHEMA,
    "REVIEW_RESULT_SCHEMA": s.REVIEW_RESULT_SCHEMA,
    "DELEGATE_RESULT_SCHEMA": s.DELEGATE_RESULT_SCHEMA,
    "JOB_STARTED_SCHEMA": s.JOB_STARTED_SCHEMA,
}


@pytest.mark.parametrize("name,sch", _META_BEARING.items())
def test_success_branch_meta_is_opaque_pointer(name, sch):
    """The success branch's meta is a compact opaque stub pointing at the resource,
    not the full inlined Meta object (~3.5KB per branch pre-shrink)."""
    success = [
        b for b in sch["anyOf"] if b.get("properties", {}).get("ok", {}).get("const") is True
    ]
    assert success, f"{name}: no success branch"
    for br in success:
        meta = br["properties"]["meta"]
        assert meta.get("type") == "object"
        assert meta.get("description") == s._RESULT_META_POINTER_DESC
        # Fully opaque: no inlined properties / required / additionalProperties.
        assert set(meta) == {"type", "description"}, f"{name}: meta not fully opaque"


@pytest.mark.parametrize("name,sch", _META_BEARING.items())
def test_meta_closure_pruned_from_defs(name, sch):
    """Replacing the Meta ref orphans its closure; pruning drops the unreachable defs."""
    defs = sch.get("$defs", {})
    for orphan in ("Meta", "RateLimit", "RateLimitWindow", "Usage", "ContextSummary"):
        assert orphan not in defs, f"{name}: {orphan} should be pruned (unreachable)"


def test_prune_keeps_defs_referenced_outside_meta():
    """Pruning is per-schema reachability, not a hardcoded drop-list: a def reachable
    WITHOUT going through Meta must survive.  StatusResult references RateLimit directly
    (no meta field), and DryRunResult references ContextSummary directly."""
    assert "RateLimit" in s.STATUS_SCHEMA["$defs"], "RateLimit must survive in STATUS_SCHEMA"
    assert "RateLimitWindow" in s.STATUS_SCHEMA["$defs"]
    assert "ContextSummary" in s.DRY_RUN_SCHEMA["$defs"], "ContextSummary must survive in dry_run"


def test_result_meta_schema_is_full_meta_contract():
    """RESULT_META_SCHEMA is the canonical, complete Meta shape published once at the
    codex://result-meta resource (the counterpart to ERROR_ENVELOPE_SCHEMA)."""
    rm = s.RESULT_META_SCHEMA
    assert rm["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    props = rm["properties"]
    # A representative sample of the fields the opaque wire stub hides.
    for field in ("cwd", "tier", "sandbox", "isolation", "usage", "rate_limit", "fingerprint"):
        assert field in props, f"result-meta missing {field}"


def test_opaque_meta_refs_replaces_nested_ref():
    """The transform swaps a Meta ref wherever it appears — including inside another $def
    (the shape a future multi-success-model union would produce)."""
    doc = {"$defs": {"Wrapper": {"properties": {"meta": dict(s._META_REF)}}}}
    out = s._opaque_meta_refs(doc)
    assert out["$defs"]["Wrapper"]["properties"]["meta"] == s._OPAQUE_META


def test_prune_defs_noop_without_defs():
    doc = {"type": "object"}
    assert s._prune_defs(doc) is doc


def test_prune_defs_handles_shared_and_orphan_defs():
    """A def reachable via two paths is visited once (the `name in reachable` guard); an
    unreferenced def is dropped."""
    doc = {
        "anyOf": [{"$ref": "#/$defs/A"}, {"$ref": "#/$defs/A"}],
        "$defs": {
            "A": {"properties": {"b": {"$ref": "#/$defs/B"}}},
            "B": {"type": "object"},
            "Orphan": {"type": "object"},
        },
    }
    pruned = s._prune_defs(doc)
    assert set(pruned["$defs"]) == {"A", "B"}


def test_meta_bearing_success_payload_still_validates():
    """Making meta opaque must not break strict clients: a real full Meta object still
    validates against {'type': 'object'} (audit F1 correctness question A)."""
    import jsonschema

    result = s.ConsultResult(summary="ok", meta=_make_meta())
    jsonschema.validate(result.model_dump(mode="json"), s.CONSULT_RESULT_SCHEMA)


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
    import ast
    import pathlib

    src = pathlib.Path("src/codex_in_claude")
    offenders = []
    for p in src.rglob("*.py"):
        if p.name == "errors.py":
            continue
        tree = ast.parse(p.read_text(), filename=str(p))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "model_dump"
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "ErrorResult"
            ):
                offenders.append(f"{p}:{node.lineno}")
    assert not offenders, f"raw ErrorResult.model_dump outside errors.py: {offenders}"


# ---------------------------------------------------------------------------
# ERROR_ENVELOPE_SCHEMA hardening tests (jsonschema)
# ---------------------------------------------------------------------------


def test_error_envelope_validates_temporary_error():
    """A valid rate-limited (temporary=True, retry_after_ms set) envelope validates."""
    import jsonschema

    from codex_in_claude.errors import make_error, serialize_error
    from codex_in_claude.schemas import ERROR_ENVELOPE_SCHEMA, ErrorResult, Meta

    env = ErrorResult(
        error=make_error("codex_rate_limited", "rate limited", retry_after_ms=5000),
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
    jsonschema.Draft202012Validator(ERROR_ENVELOPE_SCHEMA).validate(payload)  # must not raise


def test_error_envelope_validates_non_temporary_error():
    """A valid non-temporary error (invalid_arguments, retry_after_ms=null) validates."""
    import jsonschema

    from codex_in_claude.errors import make_error, serialize_error
    from codex_in_claude.schemas import ERROR_ENVELOPE_SCHEMA, ErrorResult, Meta

    env = ErrorResult(
        error=make_error("invalid_arguments", "bad args"),
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
    jsonschema.Draft202012Validator(ERROR_ENVELOPE_SCHEMA).validate(payload)  # must not raise


def test_error_envelope_rejects_missing_ok():
    """An envelope missing the required 'ok' field is rejected by the schema."""
    import jsonschema

    from codex_in_claude.schemas import ERROR_ENVELOPE_SCHEMA

    bad = {
        "error": {
            "code": "internal_error",
            "message": "x",
            "temporary": False,
            "retry_after_ms": None,
        },
        "meta": {
            "cwd": "/x",
            "tier": "consult",
            "sandbox": "read-only",
            "isolation": "inherit",
            "timeout_seconds": 180,
            "elapsed_ms": 1,
        },
    }
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.Draft202012Validator(ERROR_ENVELOPE_SCHEMA).validate(bad)


def test_error_envelope_rejects_invariant_violation():
    """An envelope with temporary=False and retry_after_ms=5 violates the model invariant."""
    import jsonschema

    from codex_in_claude.schemas import ERROR_ENVELOPE_SCHEMA

    bad = {
        "ok": False,
        "error": {
            "code": "internal_error",
            "message": "x",
            "temporary": False,
            "retry_after_ms": 5,
        },
        "meta": {
            "cwd": "/x",
            "tier": "consult",
            "sandbox": "read-only",
            "isolation": "inherit",
            "timeout_seconds": 180,
            "elapsed_ms": 1,
        },
    }
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.Draft202012Validator(ERROR_ENVELOPE_SCHEMA).validate(bad)


def test_error_envelope_schema_has_dialect():
    """ERROR_ENVELOPE_SCHEMA declares the 2020-12 dialect.

    Pydantic v2 emits $defs → 2020-12-style references.
    """
    from codex_in_claude.schemas import ERROR_ENVELOPE_SCHEMA

    assert ERROR_ENVELOPE_SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"


# ---------------------------------------------------------------------------
# Task 4: CI catalog-size gate
# ---------------------------------------------------------------------------


def _wire_catalog_bytes() -> int:
    import asyncio

    from codex_in_claude.server import mcp

    tools = asyncio.run(mcp.list_tools())
    catalog = [
        t.to_mcp_tool().model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools
    ]
    return len(json.dumps(catalog, separators=(",", ":")).encode("utf-8"))


# Serialized-size budget for the tools/list wire response (audit F1, #173) — a weight
# gate the content-only manifest snapshot does not provide. Cap = real MCP wire catalog
# (~57,570 bytes, incl. annotations/_meta) + ~11% headroom. History: ~180,266 → ~103,526
# (JOB_RESULT slim) → ~57,570 (opaque meta branch + docstring dedup). Tighten deliberately
# when the surface legitimately shrinks; a bump means the wire response grew — justify it.
CATALOG_BYTE_CAP = 64_000


def test_wire_catalog_under_cap():
    size = _wire_catalog_bytes()
    assert size <= CATALOG_BYTE_CAP, f"catalog grew to {size} bytes (cap {CATALOG_BYTE_CAP})"


# ---------------------------------------------------------------------------
# Success-schema validation with non-empty findings (Finding.title regression)
# Verifies that _strip_schema_noise preserves the Finding.title property so
# jsonschema.validate does not reject a real payload as an additional property.
# ---------------------------------------------------------------------------


def _make_meta() -> s.Meta:
    return s.Meta(
        cwd="/repo",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=180,
        elapsed_ms=42,
    )


def _make_finding() -> s.Finding:
    return s.Finding(
        severity="high",
        title="Example finding title",
        file="src/foo.py",
        line=10,
        evidence="some evidence",
        risk="breaks strict MCP clients",
        recommendation="fix the stripper",
    )


def test_consult_result_with_findings_validates_against_schema():
    """CONSULT_RESULT_SCHEMA must accept a ConsultResult that has a non-empty findings list."""
    import jsonschema

    result = s.ConsultResult(
        summary="All good",
        findings=[_make_finding()],
        meta=_make_meta(),
    )
    payload = result.model_dump(mode="json")
    jsonschema.validate(payload, s.CONSULT_RESULT_SCHEMA)


def test_review_result_with_findings_validates_against_schema():
    """REVIEW_RESULT_SCHEMA must accept a ReviewResult with verdict/confidence and findings."""
    import jsonschema

    result = s.ReviewResult(
        summary="Review done",
        verdict="concerns",
        confidence="high",
        findings=[_make_finding()],
        meta=_make_meta(),
    )
    payload = result.model_dump(mode="json")
    jsonschema.validate(payload, s.REVIEW_RESULT_SCHEMA)


def test_delegate_result_with_findings_validates_against_schema():
    """DELEGATE_RESULT_SCHEMA must accept a DelegateResult with a diff and findings."""
    import jsonschema

    result = s.DelegateResult(
        summary="Delegate done",
        diff="--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n",
        findings=[_make_finding()],
        meta=_make_meta(),
    )
    payload = result.model_dump(mode="json")
    jsonschema.validate(payload, s.DELEGATE_RESULT_SCHEMA)


# --------------------------------------------------------------------------- #
# Advisory polled event-activity fields (Task 3 / #139)
# --------------------------------------------------------------------------- #
from codex_in_claude.schemas import (  # noqa: E402
    FINGERPRINT,
    FINGERPRINT_COVERS,
    AsyncLifecycle,
    CapabilitiesResult,
    JobStatus,
)


def test_jobstatus_has_advisory_activity_fields_defaulting_safely():
    s_obj = JobStatus(
        job_id="j",
        kind="codex_consult",
        status="running",
        started_at="t",
        elapsed_ms=1,
        deadline_seconds=60,
        ttl_seconds=60,
        workspace={"cwd": "/x", "workspace_source": "param"},
    )
    assert s_obj.events_seen == 0
    assert s_obj.last_event_at is None
    assert s_obj.event_age_ms is None


def test_async_lifecycle_advertises_activity_without_touching_progress_support():
    lc = AsyncLifecycle(
        poll_tool="p",
        result_tool="r",
        consume_tool="c",
        cancel_tool="x",
        list_tool="l",
        status_field="status",
        result_ready_field="result_available",
        poll_after_field="poll_after_ms",
        activity_support="codex_events",
        event_count_field="events_seen",
        last_event_field="last_event_at",
        event_age_field="event_age_ms",
    )
    assert lc.progress_support == "none"  # native progress meaning preserved
    assert lc.activity_support == "codex_events"


def test_fingerprint_bumped_to_schema_26():
    assert FINGERPRINT == "codex-in-claude/0.1/schema-26"


def test_fingerprint_covers_is_a_nonempty_stable_tuple():
    # The coverage enumeration is the single source of truth (#178, audit F6): it is
    # an immutable tuple of granular, machine-readable identifiers.
    assert isinstance(FINGERPRINT_COVERS, tuple)
    assert FINGERPRINT_COVERS  # non-empty
    assert all(isinstance(c, str) and c for c in FINGERPRINT_COVERS)
    # Deduplicated and machine-readable (snake_case tokens, no prose).
    assert len(set(FINGERPRINT_COVERS)) == len(FINGERPRINT_COVERS)
    assert all(c == c.lower() and " " not in c for c in FINGERPRINT_COVERS)
    # Completeness relative to the actual fingerprint guard (the manifest surface) is
    # asserted structurally in test_manifest.py::test_fingerprint_covers_accounts_for_every_section.


def test_capabilities_result_exposes_fingerprint_covers_derived_from_constant():
    caps = CapabilitiesResult(
        name="codex-in-claude",
        version="0.0.0",
        transport="stdio",
        stability="alpha",
        active_tools=[],
        free_tools=[],
        tiers=[],
        sandboxes=[],
        scope=[],
        negative_scope=[],
        prerequisites=[],
        deprecation_policy="x",
    )
    # The field derives from the constant but is an independent list (no shared mutable state).
    assert caps.fingerprint_covers == list(FINGERPRINT_COVERS)
    caps.fingerprint_covers.append("mutated")
    assert "mutated" not in FINGERPRINT_COVERS


# ---------------------------------------------------------------------------
# Task 2 (audit F1): slim JOB_RESULT_SCHEMA to an opaque tool-branch union
# ---------------------------------------------------------------------------


class TestJobResultSchemaSlim:
    def test_opaque_success_branch_with_tool_enum(self):
        branches = s.JOB_RESULT_SCHEMA["anyOf"]
        success = branches[0]
        assert success["properties"]["ok"] == {"const": True}
        assert sorted(success["properties"]["tool"]["enum"]) == [
            "codex_consult",
            "codex_delegate",
            "codex_review_changes",
        ]

    def test_no_embedded_result_defs(self):
        assert s.JOB_RESULT_SCHEMA.get("$defs", {}) == {}

    def test_serialized_size_ceiling(self):
        assert len(json.dumps(s.JOB_RESULT_SCHEMA)) < 2000  # was ~14,576 bytes
