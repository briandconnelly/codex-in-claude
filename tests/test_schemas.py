import json
import re

import pytest
from pydantic import ValidationError

from codex_in_claude import __version__
from codex_in_claude import schemas as s
from codex_in_claude.schemas import (
    ERROR_ENVELOPE_SCHEMA,
    RESULT_META_SCHEMA,
    ErrorDetail,
    ErrorInfo,
    Meta,
    Repair,
)


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
# Task 1: server_version on every fingerprint-bearing model
# ---------------------------------------------------------------------------


@pytest.fixture
def meta_factory():
    def _make(**overrides):
        defaults = dict(
            cwd="/tmp",
            tier="consult",
            sandbox="read-only",
            isolation="inherit",
            timeout_seconds=180,
            elapsed_ms=0,
        )
        defaults.update(overrides)
        return Meta(**defaults)

    return _make


# Every result model that carries a contract fingerprint must also carry a release
# version: `fingerprint` answers "which surface?", `server_version` answers "which build?".
#
# Discovered by INTROSPECTING schemas.py (see _fingerprint_bearing_models below), not a
# hand-written list — a hardcoded tuple would go stale the moment a new fingerprint-bearing
# model is added without being appended here, silently narrowing the guard below back to
# exactly the gap this test exists to close (#304). VERSION_BEARING_MODELS is kept only as
# a pinned expectation (test_fingerprint_bearing_models_matches_known_set) so an addition or
# removal to the discovered set is a visible, reviewable diff rather than a silent expansion.
VERSION_BEARING_MODELS = (
    "Meta",
    "StatusResult",
    "CapabilitiesResult",
    "ModelCatalogResult",
    "TransferResult",
    "JobStarted",
    "JobStatus",
    "DryRunResult",
    "DelegateDryRunResult",
    "JobListResult",
)


def _fingerprint_bearing_models():
    """Every pydantic model in schemas.py that declares a top-level `fingerprint` field
    defaulting to FINGERPRINT — the actual contract surface, found by introspecting the
    live module rather than trusting a maintained list to stay in sync with it."""
    from pydantic import BaseModel

    from codex_in_claude import schemas

    return [
        obj
        for obj in vars(schemas).values()
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and "fingerprint" in obj.model_fields
        and obj.model_fields["fingerprint"].default == schemas.FINGERPRINT
    ]


def test_fingerprint_bearing_models_matches_known_set():
    """Pin the discovered set against VERSION_BEARING_MODELS so an addition/removal of a
    fingerprint-bearing model is a visible diff here, not a silent change in what the
    structural guard below covers."""
    discovered = {m.__name__ for m in _fingerprint_bearing_models()}
    assert discovered == set(VERSION_BEARING_MODELS)


def test_every_fingerprint_bearing_model_carries_server_version():
    """Every model declaring `fingerprint` must also declare `server_version`.

    Assert on the FIELD DECLARATION, not on `model()` — most of these models have
    required fields (StatusResult, JobStatus, …) and cannot be constructed bare.
    Runtime population is asserted on real emitted envelopes in Task 2, which is
    where it actually matters.

    The model set is discovered by introspection (`_fingerprint_bearing_models`), so this
    genuinely covers every current and future fingerprint-bearing model — not just the
    ten named in VERSION_BEARING_MODELS at the time this test was written.
    """
    models = _fingerprint_bearing_models()
    assert models  # the introspection itself must find something, or this is vacuous

    for model in models:
        name = model.__name__
        assert "server_version" in model.model_fields, f"{name} has no server_version"
        factory = model.model_fields["server_version"].default_factory
        assert factory is not None, f"{name}.server_version must use a default_factory"
        assert factory() == __version__


def test_server_version_is_populated_by_default(meta_factory):
    # `meta_factory` builds a valid Meta (cwd/tier/sandbox/isolation/timeout/elapsed are
    # required, so `Meta()` cannot be constructed bare).
    assert meta_factory().server_version == __version__


def test_server_version_emits_no_schema_default():
    """THE regression guard for release churn.

    A literal default would embed the release into the published schema; that schema is
    snapshotted and guarded, so every release would trip the manifest guard. Assert on the
    LIVE published schemas, not just the snapshot — fixing only the snapshot would leave
    the real codex://result-meta schema churning while CI stayed green.
    """
    for schema in (RESULT_META_SCHEMA, ERROR_ENVELOPE_SCHEMA):
        prop = _find_server_version_property(schema)
        assert prop is not None, "server_version missing from a published schema"
        assert "default" not in prop, f"server_version must not publish a default: {prop}"


def _find_server_version_property(schema):
    """The server_version property, wherever the model sits in the schema's $defs."""
    if "properties" in schema and "server_version" in schema["properties"]:
        return schema["properties"]["server_version"]
    for definition in (schema.get("$defs") or {}).values():
        props = definition.get("properties") or {}
        if "server_version" in props:
            return props["server_version"]
    return None


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


def _annotation_descriptions(node):
    """Yield object-level ``description`` annotation values (not property NAMES)."""
    if isinstance(node, dict):
        _MAPS = frozenset(
            ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas")
        )
        if "description" in node and isinstance(node["description"], str):
            yield node["description"]
        for k, v in node.items():
            if k in _MAPS and isinstance(v, dict):
                for sub in v.values():
                    yield from _annotation_descriptions(sub)
            elif k != "description":
                yield from _annotation_descriptions(v)
    elif isinstance(node, list):
        for v in node:
            yield from _annotation_descriptions(v)


@pytest.mark.parametrize("name,sch", _ALL_SCHEMAS.items())
def test_noise_stripped_except_pointers(name, sch):
    assert not _annotation_title_present(sch), f"{name} has a title annotation"
    assert not _has_key(sch, "default"), f"{name} has a default"
    text = json.dumps(sch)
    assert "codex://error-envelope" in text
    # Every surviving description is an intentional kept pointer, never generated noise.
    for desc in _annotation_descriptions(sch):
        assert desc in s._KEPT_DESCRIPTIONS, f"{name}: unexpected description {desc!r}"
    if _is_meta_bearing(sch):
        assert "codex://result-meta" in text
        assert '"Meta":' not in text, f"{name}: Meta $def should be pruned"
    if "Finding" in sch.get("$defs", {}):
        finding_def = sch["$defs"]["Finding"]
        assert "title" not in finding_def
        assert "title" in finding_def.get("properties", {})


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
    WITHOUT going through an opaqued field must survive. DryRunResult references
    ContextSummary directly and keeps it (StatusResult's RateLimit is now opaqued —
    see test_status_capabilities_closure_pruned_from_defs)."""
    assert "ContextSummary" in s.DRY_RUN_SCHEMA["$defs"], "ContextSummary must survive in dry_run"


# ---------------------------------------------------------------------------
# #242: opaque nested payload fields on the two free discovery tools
# ---------------------------------------------------------------------------

_OPAQUE_FIELD_SCHEMAS = {
    "CAPABILITIES_SCHEMA": (s.CAPABILITIES_SCHEMA, {"tool_details": "array"}),
    "STATUS_SCHEMA": (
        s.STATUS_SCHEMA,
        {"rate_limit": "object", "raw_defaults": "object", "resolved_defaults": "object"},
    ),
}


def _success_branch(sch):
    return next(
        b for b in sch["anyOf"] if b.get("properties", {}).get("ok", {}).get("const") is True
    )


@pytest.mark.parametrize("name,pair", _OPAQUE_FIELD_SCHEMAS.items())
def test_opaque_payload_fields_are_pointers(name, pair):
    sch, fields = pair
    props = _success_branch(sch)["properties"]
    for field, json_type in fields.items():
        stub = props[field]
        assert set(stub) == {"type", "description"}, f"{name}.{field} not fully opaque: {stub}"
        assert stub["type"] == json_type
        assert stub["description"] in s._KEPT_DESCRIPTIONS
        assert "codex://" in stub["description"]


def test_status_capabilities_closure_pruned_from_defs():
    """Opaquing the nested fields orphans their whole $def closure, which pruning drops."""
    assert set(s.CAPABILITIES_SCHEMA.get("$defs", {})) == set()
    assert set(s.STATUS_SCHEMA.get("$defs", {})) == set()


def test_full_result_schemas_are_complete_contracts():
    """The on-demand full schemas keep every field + the $defs the wire schema drops."""
    cap = s.CAPABILITIES_RESULT_SCHEMA
    assert cap["$schema"] == s.JSON_SCHEMA_DIALECT
    assert "tool_details" in cap["properties"]
    assert "ToolCapability" in cap["$defs"]
    st = s.STATUS_RESULT_SCHEMA
    assert st["$schema"] == s.JSON_SCHEMA_DIALECT
    assert "rate_limit" in st["properties"]
    assert "RateLimit" in st["$defs"]


def test_loosened_schemas_accept_real_payloads():
    """Widening must not reject the emitted payload (the core guarantee)."""
    import jsonschema

    from codex_in_claude.server import codex_capabilities, codex_status

    jsonschema.validate(codex_capabilities(), s.CAPABILITIES_SCHEMA)
    jsonschema.validate(codex_status(), s.STATUS_SCHEMA)


def test_capabilities_result_schema_accepts_both_detail_modes():
    """A client that fetches CAPABILITIES_RESULT_SCHEMA and validates the very same
    response against it must not fail in either detail mode.

    `detail="summary"` (the default) strips `use_when`/`returns` from every
    `tool_details` entry before the response ships; only `detail="full"` carries them.
    The published schema has to model BOTH shapes rather than only the fuller one —
    a schema that requires fields the default response never sends is a bug in the
    schema, not in the response (the wire bytes are unchanged either way)."""
    import jsonschema

    from codex_in_claude.server import codex_capabilities

    summary = codex_capabilities(include_schemas=["capabilities-result"])
    full = codex_capabilities(detail="full", include_schemas=["capabilities-result"])
    schema = summary["schemas"]["capabilities-result"]
    assert schema == full["schemas"]["capabilities-result"], (
        "the published schema itself must not vary by detail mode"
    )

    # Genuine JSON Schema validation (jsonschema is already resolved into this project's
    # environment — see the other jsonschema.validate calls in this file — so this is the
    # real contract check, not a field-presence proxy).
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(summary)
    validator.validate(full)

    # And pin the specific shape that made this a bug: the schema's required list must
    # not demand a field the default response omits.
    tool_capability = schema["$defs"]["ToolCapability"]
    assert set(tool_capability["required"]) == {"name", "cost"}
    for entry in summary["tool_details"]:
        assert "use_when" not in entry
        assert "returns" not in entry
    for entry in full["tool_details"]:
        assert isinstance(entry["use_when"], str)
        assert isinstance(entry["returns"], str)


def test_loosened_schemas_stay_under_byte_budget():
    """A future change must not silently reintroduce the $defs bloat (was 3864/3429 B).

    Budget raised 2000 -> 2100 B (#423): `protocol_revision` added a described `str`
    field to `CapabilitiesResult`. Its description is stripped from this loosened
    schema by `_strip_schema_noise` (not registered in `_KEPT_DESCRIPTIONS`), so only
    the bare `{"type":"string"}` property remains — CAPABILITIES_SCHEMA moved
    1968 -> 2006 B, tripping the prior 2000 B cap by 6 B. STATUS_SCHEMA is unaffected
    (1659 B, unchanged).

    Measured again (#426): `annotations_reading` added a second described `str` field
    to `CapabilitiesResult`, stripped the same way — CAPABILITIES_SCHEMA moved
    2006 -> 2046 B, still under the 2100 B cap (no raise needed this time).
    STATUS_SCHEMA remains unaffected (1659 B, unchanged)."""
    for name, (sch, _) in _OPAQUE_FIELD_SCHEMAS.items():
        size = len(json.dumps(sch, separators=(",", ":")))
        assert size < 2100, f"{name} is {size} B, over the 2100 B budget"


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
    from codex_in_claude.schemas import (
        ERROR_ENVELOPE_SCHEMA,
        ErrorResult,
        InvalidArgument,
        Meta,
    )

    env = ErrorResult(
        error=make_error(
            "invalid_arguments",
            "bad args",
            invalid_arguments=[InvalidArgument(field="scope", reason="bad")],
        ),
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


def test_json_schema_dialect_constant_is_2020_12():
    """The shared dialect constant is the single source of truth for both the input-schema
    middleware and every advertised output schema (audit N4, #185)."""
    assert s.JSON_SCHEMA_DIALECT == "https://json-schema.org/draft/2020-12/schema"


# Every advertised outputSchema — the published_schema()-built ones AND the hand-built
# JOB_RESULT_SCHEMA — must declare the dialect at its root, symmetric with the input
# schemas (audit N4, #185). Without a declared draft a client can't know how to validate.
_ALL_OUTPUT_SCHEMAS = {**_ALL_SCHEMAS, "JOB_RESULT_SCHEMA": s.JOB_RESULT_SCHEMA}


@pytest.mark.parametrize("name,sch", _ALL_OUTPUT_SCHEMAS.items())
def test_output_schema_declares_dialect(name, sch):
    assert sch.get("$schema") == s.JSON_SCHEMA_DIALECT, f"{name} declares no dialect"


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
# + ~9% headroom. History: ~180,266 → ~103,526 (JOB_RESULT slim) → ~57,570 (opaque meta
# branch + docstring dedup) → ~64,211 (output-schema `$schema` dialect, audit N4/#185:
# ~54 B x 16 tools) → ~73,088 (#309: described reasoning_effort param on the paid tools +
# both dry-runs, model on codex_dry_run, dry-run model/effort echo fields) → ~80,187
# (#319/#320: the `untracked` input policy on the two review tools + codex_dry_run, and the
# `review_status`/`coverage` review-coverage disclosure plus codex_dry_run's
# `would_call_model`/`coverage`). The last step also absorbed headroom earlier PRs consumed
# without resetting the cap. → ~76,714 (#333: idempotency_key/reasoning_effort inline
# descriptions compressed to codex://params summaries, sync/async tool docstrings slimmed,
# workspace_root/isolation tightened — guarantees frozen by tests/test_server.py). → ~83,912
# (#396: codex_job_list's `limit` became nullable so a no-arg call returns every retained job
# — the integer-or-null schema plus the reworded param description, docstring, and the
# now-explicit "running jobs are never evicted" ceiling caveat; the bytes buy the client the
# knowledge that the default listing is complete and that `truncated` is a cap with no
# cursor). → ~84,309 (#411: QuestionParam/TaskParam/TaskDryRunParam state that a blank
# `question`/`task` is rejected before any model call; the rule is enforced at runtime, not by a
# schema `minLength`, so the description is the only place a client can discover it pre-spend).
# → ~84,757 (#414: the three sync tools' Progress & recovery paragraphs name `codex_job_status`
# as the recovery chain's polling step and state that some MCP clients background a long call
# before the server's own deadline, so `timeout_seconds` bounds the run, not necessarily the
# client's inline wait — the job record covers that case too). → 84,795 (#423:
# `protocol_revision`'s bare `{"type":"string"}` property on `codex_capabilities`'
# outputSchema — same ~38 B this cap's comment history skipped narrating at the time).
# Measured again 2026-08-01 (#426: `annotations_reading`, another bare `{"type":"string"}`
# property on the same outputSchema — its description is stripped the same way
# `protocol_revision`'s is): 84,835 bytes — still within budget, no further change.
# Measured again 2026-08-02 (#427: the six egress tool docstrings converged onto one shared
# skills-discovery sentence pair — see tests/test_wire_size.py's TOOLS_LIST_BYTE_BUDGET/TARGET
# history for the tightening story, which applies identically here): 84,835 -> 84,992 bytes
# (+157 B) — fits under the existing cap, no raise.
# Measured again 2026-08-02 (#342: `deadline_advisory` added to `DryRunResult` and
# `DelegateDryRunResult`, described identically on both, plus one docstring sentence per
# dry-run tool — see tests/test_wire_size.py's TOOLS_LIST_BYTE_BUDGET/TARGET history for
# the same field's justification, which applies identically here: genuinely new
# pre-spend disclosure, not wording flab): 84,992 -> 86,435 bytes (+1,443 B, incl. an
# accuracy reword of the field description) — over cap;
# cap raised to the next 500 above the measured value.
# Measured again 2026-08-02 (#342 round-3, Codex review concerns/1 medium, verified
# valid — see tests/test_wire_size.py's TOOLS_LIST_BYTE_BUDGET/TARGET history for the
# same correction's justification, which applies identically here: the advisory now
# names the previewed PAID tool's own `_async` counterpart verbatim instead of a
# generic, wrong-tool-pointing phrase): 86,435 -> 86,766 bytes (+331 B) — over cap;
# cap raised to the next 500 above the measured value.
# Measured again 2026-08-02 (#433: `Coverage` gained `redaction: RedactionSummary |
# None` — see tests/test_wire_size.py's TOOLS_LIST_BYTE_BUDGET/TARGET history for the
# same field's justification, which applies identically here: a new nested object
# INLINED (no `$ref`/`$defs` on the wire) into both `codex_review_changes`' and
# `codex_dry_run`'s outputSchema, whose field descriptions cost nothing (not in
# `_KEPT_DESCRIPTIONS`) — the delta is pure schema structure): 86,766 -> 87,274 bytes
# (+508 B) — over cap; cap raised to the next 500 above the measured value.
# Measured again 2026-08-02 (#433 review C4 — see tests/test_wire_size.py's
# TOOLS_LIST_BYTE_BUDGET/TARGET history for the same constraint's justification, which
# applies identically here: `RedactionSummary.inline_masks` gained `Field(ge=0)`, a
# validated `"minimum":0` property, not description prose): 87,274 -> 87,298 bytes
# (+24 B) — still within cap, no further change.
# Measured again 2026-08-19 (#501 — see tests/test_wire_size.py's TOOLS_LIST_BYTE_BUDGET
# history for the justification, which applies identically here: every egress tool
# description now states how a skill's BODY arrives, which four of the six did not
# disclose before): 87,298 -> 87,811 bytes (+513 B) — over cap; cap raised to the next
# 500 above the measured value.
# Measured again 2026-08-19 (#472 — see tests/test_wire_size.py's TOOLS_LIST_BYTE_BUDGET
# history for the justification, which applies identically here: the canonical egress
# sentence now names all THREE AGENTS.md sources, two of which the previous wording
# omitted in the unsafe direction): 87,811 -> 88,699 bytes (+888 B) — over cap; cap
# raised to the next 500 above the measured value.
# Measured again 2026-08-19 (#472, Copilot review: the canonical sentence repeats
# `$CODEX_HOME/` before the second guidance filename and says `, else` rather than `or`,
# removing an ambiguity in the repo's most copy-propagated sentence): 88,699 -> 88,795 bytes
# (+96 B) — still within cap, no further change.
# Tighten deliberately when the surface legitimately shrinks; a bump means the wire
# response grew — justify it.
# Measured again 2026-08-19 (#509, the read-scope correction — every carrier now states
# that Codex's reads are not bounded by the workspace, replacing the scoped clauses that
# read as a bound): 88,795 -> 91,212 bytes (+2,417 B) — over cap; cap raised to the next
# 1,000 above the measured value. See tests/test_wire_size.py for why the sentence was not
# compressed to fit instead. Then 91,212 -> 91,464 (+252 B) scoping the shared
# `WorkspaceRootParam` clause to active calls (Codex review) — still within cap.
# Measured again 2026-08-19 (#513, the dry-run preview-scope correction — both dry-run
# descriptions now state that a preview neither enumerates nor bounds the files the model
# itself reads during the paid run, plus the read-scope fact those two tools never carried;
# see tests/test_wire_size.py for why the sentence was not compressed to fit): 91,464 ->
# 92,306 bytes (+842 B) — over cap; cap raised to the next 1,000 above the measured value.
# Measured again 2026-08-19 (#523, the write-scope correction — both delegate
# descriptions now carry `cli_contract.WORKSPACE_WRITE_SCOPE_FACT` plus the temp-root
# persistence clause, replacing the two "only inside a throwaway worktree" exclusivity
# sentences): 92,306 -> 92,616 bytes (+310 B) — still within cap, no further change.
# Measured again 2026-08-20 (#529, the machine-identifier control-character boundary — six
# parameters advertise `config.CONTROL_CHAR_FREE_PATTERN` and four note that the value is
# rejected rather than stripped; see tests/test_wire_size.py for why the pattern half cannot
# be compacted away): 92,616 -> 94,147 bytes (+1,531 B) — over cap; cap raised to the next
# 1,000 above the measured value.
CATALOG_BYTE_CAP = 95_000


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
        review_status="completed",
        coverage=s.Coverage(status="complete"),
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
        result_ok=None,
        workspace={"cwd": "/x", "workspace_source": "param"},
    )
    assert s_obj.events_seen == 0
    assert s_obj.last_event_at is None
    assert s_obj.event_age_ms is None


def test_result_ok_is_required_nullable_on_job_models():
    # #335: result_ok has defined null meaning and must always be present, so it is
    # declared without a default — a missed mapping site fails construction, not
    # silently defaults to null.
    from codex_in_claude.schemas import JobSummary

    with pytest.raises(ValidationError):
        JobStatus(
            job_id="j",
            kind="codex_consult",
            status="done",
            started_at="t",
            elapsed_ms=1,
            deadline_seconds=60,
            ttl_seconds=60,
            workspace={"cwd": "/x", "workspace_source": "param"},
        )  # no result_ok
    with pytest.raises(ValidationError):
        # no result_ok
        JobSummary(job_id="j", kind="k", status="done", started_at="t", elapsed_ms=1)
    ok = JobSummary(
        job_id="j", kind="k", status="done", started_at="t", elapsed_ms=1, result_ok=False
    )
    assert ok.result_ok is False


def test_async_lifecycle_advertises_activity_without_touching_progress_support():
    lc = AsyncLifecycle(
        poll_tool="p",
        result_tool="r",
        consume_tool="c",
        cancel_tool="x",
        list_tool="l",
        status_field="status",
        result_ready_field="result_available",
        result_ok_field="result_ok",
        poll_after_field="poll_after_ms",
        activity_support="codex_events",
        event_count_field="events_seen",
        last_event_field="last_event_at",
        event_age_field="event_age_ms",
    )
    assert lc.progress_support == "none"  # native progress meaning preserved
    assert lc.activity_support == "codex_events"
    assert lc.result_ok_field == "result_ok"  # #335 outcome-triage field, discoverable


def test_fingerprint_is_pinned():
    assert FINGERPRINT == "codex-in-claude/0.1/schema-84"


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


def test_fingerprint_covers_description_discloses_the_release_identity_carveout():
    """The coverage promise is only honest if the release-identity carve-out travels with
    it (#337): `serverInfo.version`/`version`/`server_version` change every release without
    moving the fingerprint, because it tracks CONTRACT identity, not release identity.

    Boundary-aware matching: a bare `in` check for "version" is satisfied by the mention of
    `server_version`, so deleting the standalone mention would pass falsely."""
    from codex_in_claude.schemas import _FINGERPRINT_COVERS_DESC

    desc = _FINGERPRINT_COVERS_DESC
    for field in ("serverInfo.version", "server_version"):
        assert field in desc, f"the carve-out does not name {field}"
    # Standalone `version` — `(?<![\w.])` rejects both `server_version` and `serverInfo.version`.
    assert re.search(r"(?<![\w.])version(?![\w])", desc), (
        "the carve-out does not name the bare capabilities `version` field"
    )
    # The carve-out is scoped, NOT a claim to enumerate every unguarded detail: the manifest
    # also strips framework `_meta` and normalizes set-like array order. Promising
    # exhaustiveness would recreate the very overclaim this field exists to correct.
    assert "contract" in desc.lower()
    # The FORWARD invariant is the one a caching client needs: an unchanged fingerprint must
    # mean the cached contract is still valid. Stating only what a change "may signal" leaves
    # that unpromised, so the direction is asserted explicitly.
    assert "changes the fingerprint" in desc


def test_fingerprint_covers_description_survives_schema_noise_stripping():
    """`_strip_schema_noise` drops every description not in `_KEPT_DESCRIPTIONS`, matched by
    exact string value. Without registration the carve-out would reach the
    codex://capabilities-result resource but be stripped from the advertised
    `codex_capabilities` outputSchema — the surface most schema-reading clients use.

    This also guards the silent-no-op failure mode: if the Field description and the kept
    constant ever drift apart, stripping silently resumes and only this test notices."""
    from codex_in_claude.schemas import (
        _FINGERPRINT_COVERS_DESC,
        _KEPT_DESCRIPTIONS,
        CapabilitiesResult,
        published_schema,
    )

    assert _FINGERPRINT_COVERS_DESC in _KEPT_DESCRIPTIONS
    # The Field carries the exact same string value, not merely a similar one.
    field = CapabilitiesResult.model_fields["fingerprint_covers"]
    assert field.description == _FINGERPRINT_COVERS_DESC
    # published_schema returns success branch(es) + one opaque error branch under anyOf.
    success = published_schema(CapabilitiesResult)["anyOf"][0]
    covers = success["properties"]["fingerprint_covers"]
    assert covers.get("description") == _FINGERPRINT_COVERS_DESC
    # Positive control: the stripper is live on this very schema — generated `title` noise
    # is gone, so the assertion above cannot pass by the stripper being inert.
    assert "title" not in covers


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


class TestSlimMeta:
    """`slim_meta` — the delivered-envelope contract (#334).

    Only `meta` sheds its null-valued keys, and only on a completed
    consult/review/delegate success. Every top-level field (including `diff`) and all
    of `raw_response` are delivered verbatim, so no payload-bearing key can vanish."""

    @staticmethod
    def _envelope(**over):
        env = {
            "ok": True,
            "tool": "codex_consult",
            "summary": "s",
            "findings": [],
            "raw_response": {"text": None, "session_id": None, "model": None},
            "meta": {
                "cwd": "/repo",
                "tier": "consult",
                "sandbox": "read-only",
                "isolation": "inherit",
                "timeout_seconds": 1,
                "elapsed_ms": 1,
                "truncated": False,
                "session_id": None,
                "usage": None,
                "compat_warnings": [],
            },
        }
        env.update(over)
        return env

    def test_drops_null_meta_keys(self):
        out = s.slim_meta(self._envelope())
        assert "session_id" not in out["meta"]
        assert "usage" not in out["meta"]

    def test_keeps_required_meta_values(self):
        out = s.slim_meta(self._envelope())
        assert out["meta"]["cwd"] == "/repo"
        assert out["meta"]["elapsed_ms"] == 1

    def test_strips_on_none_not_falsiness(self):
        # truncated=False and elapsed_ms=0 are meaningful values, not absences. A
        # falsiness-based filter would silently delete both.
        env = self._envelope()
        env["meta"]["elapsed_ms"] = 0
        out = s.slim_meta(env)
        assert out["meta"]["truncated"] is False
        assert out["meta"]["elapsed_ms"] == 0

    def test_retains_empty_collections_in_meta(self):
        out = s.slim_meta(self._envelope())
        assert out["meta"]["compat_warnings"] == []

    def test_raw_response_is_delivered_verbatim(self):
        # The apply_detail guarantee: raw_response stays present, text stays nulled,
        # session_id/model are still echoed there.
        out = s.slim_meta(self._envelope())
        assert out["raw_response"] == {"text": None, "session_id": None, "model": None}

    def test_delegate_diff_survives_even_when_null(self):
        # `result["diff"]` is the delegate access pattern and next_steps tells the
        # caller to review it; the key must never vanish.
        env = self._envelope(tool="codex_delegate", diff=None)
        out = s.slim_meta(env)
        assert "diff" in out and out["diff"] is None

    def test_top_level_nulls_are_never_dropped(self):
        env = self._envelope(some_future_field=None)
        out = s.slim_meta(env)
        assert "some_future_field" in out

    def test_error_envelopes_pass_through_untouched(self):
        err = {"ok": False, "error": {"code": "internal_error"}, "meta": {"session_id": None}}
        assert s.slim_meta(err) == err

    def test_non_result_ok_true_payloads_pass_through_untouched(self):
        # JobStarted is ok:Literal[True] with a full null-laden Meta and must NOT slim
        # — the rule is scoped to the three result envelopes structurally, not by prose.
        started = {"ok": True, "job_id": "j", "kind": "codex_consult", "meta": {"model": None}}
        assert s.slim_meta(started) == started

    @pytest.mark.parametrize("tool", ["codex_consult", "codex_review_changes", "codex_delegate"])
    def test_applies_to_each_result_tool(self, tool):
        out = s.slim_meta(self._envelope(tool=tool))
        assert "session_id" not in out["meta"]

    def test_missing_or_non_dict_meta_is_tolerated(self):
        assert s.slim_meta({"ok": True, "tool": "codex_consult"}) == {
            "ok": True,
            "tool": "codex_consult",
        }
        odd = {"ok": True, "tool": "codex_consult", "meta": "not-a-dict"}
        assert s.slim_meta(odd) == odd


# The optional `Meta` fields — those whose absence from a delivered envelope is
# indistinguishable from their being null, and which are therefore the ONLY fields a
# deletion regression can destroy invisibly (#400). Derived from the model rather than
# listed, so adding an optional field to `Meta` fails the completeness check below until
# the vector covers it.
def _null_defaulting_meta_fields() -> set[str]:
    return {name for name, f in Meta.model_fields.items() if f.default is None}


# Every one of those fields, populated with a schema-appropriate NON-NULL value. Built
# through `Meta` (not a raw dict) so `extra="forbid"` proves this fully-populated state is
# actually constructible — a hand-rolled dict could pin a shape the model cannot produce.
# Values are fixed literals: nothing here may be derived from the clock or the
# environment, or `test_populated_optional_meta_values_all_survive` would churn.
def _fully_populated_meta() -> Meta:
    return Meta(
        cwd="/repo",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=1,
        elapsed_ms=1,
        workspace_source="param",
        workspace_warning="fell back to the server cwd",
        roots_source="client",
        model="a-model",
        reasoning_effort="high",
        scope="branch",
        base="main",
        commit="0" * 40,
        paths=["src/a.py"],
        command_exit_code=0,
        session_id="sess-1",
        truncation_hint="output was cut off",
        usage=s.Usage(input_tokens=1, output_tokens=2, cached_input_tokens=3, total_tokens=6),
        rate_limit=s.RateLimit(status="available"),
        context_summary=s.ContextSummary(files_changed=1, lines_added=2, lines_removed=3),
        job_id="0" * 32,
        job_kind="codex_delegate",
        idempotency_replayed=True,
    )


class TestSlimMetaPopulatedOptionals:
    """Deletion of a POPULATED optional `meta` key must be detectable (#400).

    `TestSlimMeta` above pins that nulls go and that falsy values stay. Neither states
    the third case: a non-null optional must arrive with its value. Nothing did — a
    `slim_meta` that unconditionally dropped `model` and `session_id` from every
    delivered envelope passed the entire suite, because every representative meta in the
    repo left all 18 optionals null, and a null key is already absent by design."""

    def test_the_vector_covers_exactly_the_optional_meta_fields(self):
        # Completeness. Exact equality in BOTH directions: a new optional field on `Meta`
        # is uncovered until added here, and a key left behind after a field is removed
        # fails too rather than lingering as dead weight.
        populated = _fully_populated_meta().model_dump(mode="json")
        covered = {k for k in _null_defaulting_meta_fields() if populated.get(k) is not None}
        assert covered == _null_defaulting_meta_fields()

    def test_the_field_derivation_is_not_vacuous(self):
        # The check above is only evidence if the derivation actually finds fields: a
        # mis-written filter (inverted, or reading the wrong attribute) yields an empty
        # set, against which `covered == derived` passes while covering nothing.
        derived = _null_defaulting_meta_fields()
        assert {"model", "session_id", "usage", "roots_source"} <= derived
        assert len(derived) >= 18
        # Required members and defaulted non-null ones are NOT optional in this sense —
        # their absence would be a contract violation, not an omission.
        assert derived.isdisjoint({"cwd", "tier", "truncated", "fingerprint", "compat_warnings"})

    def test_populated_optional_meta_values_all_survive(self):
        # Whole-dict equality, not a per-key membership sweep: equality also catches
        # deletion of a key OUTSIDE the derived set (`request_id`, `fingerprint`,
        # `server_version`), which a completeness-driven loop would never look at.
        meta = _fully_populated_meta().model_dump(mode="json")
        assert None not in meta.values()  # the probe below must be the only null
        expected = dict(meta)
        env = TestSlimMeta._envelope(meta={**meta, "a_null_probe": None})
        out = s.slim_meta(env)
        assert out["meta"] == expected

    def test_slimming_still_ran(self):
        # Guards the guard: if slimming stopped running, the equality above would still
        # hold (nothing to drop), so pin that the null probe was in fact removed.
        meta = _fully_populated_meta().model_dump(mode="json")
        out = s.slim_meta(TestSlimMeta._envelope(meta={**meta, "a_null_probe": None}))
        assert "a_null_probe" not in out["meta"]
