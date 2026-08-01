"""Tests for validate_fixture.validate — run with:
uv run --with pytest --with jsonschema pytest \
    agent-friendly-mcp/tests/test_validate_fixture.py -v
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validate_fixture import validate

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "github_issues.json").read_text(encoding="utf-8")
)


def test_conforming_fixture_has_no_issues():
    assert validate(copy.deepcopy(FIXTURE)) == []


def test_missing_result_type_rejected_on_both_results():
    for key in ("success_result", "error_result"):
        bad = copy.deepcopy(FIXTURE)
        del bad["wire"][key]["resultType"]
        issues = validate(bad)
        assert any(i.where == key and "resultType" in i.message for i in issues)


def test_wrong_result_type_rejected():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["success_result"]["resultType"] = "task"
    issues = validate(bad)
    assert any("resultType 'complete'" in i.message for i in issues)


def test_present_null_structuredcontent_is_judged_by_schema_not_reported_missing():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["success_result"]["structuredContent"] = None
    issues = validate(bad)
    # The object-typed outputSchema rejects null, but the violation is a schema
    # mismatch — never the 'missing structuredContent' report reserved for key absence.
    assert not any("missing structuredContent" in i.message for i in issues)
    assert any("output_schema" in i.where for i in issues)


def test_absent_structuredcontent_key_still_reported_missing():
    bad = copy.deepcopy(FIXTURE)
    del bad["wire"]["success_result"]["structuredContent"]
    issues = validate(bad)
    assert any("missing structuredContent" in i.message for i in issues)


def test_null_error_envelope_is_invariant_violation_not_degraded_fallthrough():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["error_result"]["structuredContent"] = None
    issues = validate(bad)
    assert any("must be a JSON object" in i.message for i in issues)


def test_success_structuredcontent_must_match_output_schema():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["success_result"]["structuredContent"]["state"] = "reopened"
    issues = validate(bad)
    assert any("output_schema" in i.where for i in issues)


def test_error_result_must_set_iserror_true():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["error_result"]["isError"] = False
    issues = validate(bad)
    assert any("isError" in i.message for i in issues)


def test_error_envelope_must_live_in_structuredcontent():
    bad = copy.deepcopy(FIXTURE)
    del bad["wire"]["error_result"]["structuredContent"]
    issues = validate(bad)
    assert any("structuredContent" in i.message for i in issues)


def test_error_envelope_must_match_error_schema():
    bad = copy.deepcopy(FIXTURE)
    del bad["wire"]["error_result"]["structuredContent"]["temporary"]
    issues = validate(bad)
    assert any("error_schema" in i.where for i in issues)


def test_disclosed_degraded_text_carrier_is_accepted():
    ok = copy.deepcopy(FIXTURE)
    err = ok["wire"]["error_result"]
    envelope = err.pop("structuredContent")
    err["content"] = [{"type": "text", "text": json.dumps(envelope)}]
    ok["wire"]["degraded_text_carrier"] = True
    assert validate(ok) == []


def test_degraded_carrier_with_non_list_content_fails_closed():
    bad = copy.deepcopy(FIXTURE)
    err = bad["wire"]["error_result"]
    del err["structuredContent"]
    err["content"] = {"type": "text", "text": "{}"}  # dict, not a list of blocks
    bad["wire"]["degraded_text_carrier"] = True
    issues = validate(bad)  # must report issues, not raise
    assert any("content[0].text is missing" in i.message for i in issues)


def test_non_dict_fixture_root_fails_closed():
    for bad_root in ([], "not-an-object", 42):
        issues = validate(bad_root)
        assert issues
        assert any("root must be a JSON object" in i.message for i in issues)


def test_success_result_requires_content_fallback():
    bad = copy.deepcopy(FIXTURE)
    del bad["wire"]["success_result"]["content"]
    issues = validate(bad)
    assert any(i.where == "success_result.content" for i in issues)


def test_error_result_requires_content_fallback():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["error_result"]["content"] = []
    issues = validate(bad)
    assert any(i.where == "error_result.content" for i in issues)


def test_success_content_must_include_text_block():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["success_result"]["content"] = [
        {"type": "image", "data": "x", "mimeType": "image/png"}
    ]
    issues = validate(bad)
    assert any(i.where == "success_result.content" and "text block" in i.message for i in issues)


def test_error_content_must_include_text_block():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["error_result"]["content"] = [
        {"type": "image", "data": "x", "mimeType": "image/png"}
    ]
    issues = validate(bad)
    assert any(i.where == "error_result.content" and "text block" in i.message for i in issues)


def test_permanent_error_must_have_null_retry_after_ms():
    bad = copy.deepcopy(FIXTURE)
    env = bad["wire"]["error_result"]["structuredContent"]
    env["temporary"] = False
    env["retry_after_ms"] = 100  # schema permits it; the §6 invariant must reject it
    issues = validate(bad)
    assert any(
        "§6" in i.where and "must be null when temporary is false" in i.message for i in issues
    )


def test_negative_retry_after_ms_rejected():
    bad = copy.deepcopy(FIXTURE)
    env = bad["wire"]["error_result"]["structuredContent"]
    env["temporary"] = True
    env["retry_after_ms"] = -5  # schema permits a negative integer; the §6 invariant must reject it
    issues = validate(bad)
    assert any("§6" in i.where and "non-negative integer" in i.message for i in issues)


# --- §6 second carrier: JSON-RPC error.data (issue #123) ---

RESOURCE = json.loads(
    (Path(__file__).parent / "fixtures" / "github_repo_resource.json").read_text(encoding="utf-8")
)


def test_conforming_resource_fixture_has_no_issues():
    assert validate(copy.deepcopy(RESOURCE)) == []


def test_both_carriers_are_exercised_somewhere_in_the_fixture_suite():
    """An optional carrier must not silently stop being covered."""
    carriers = {"error_result": False, "resource_error": False}
    for path in sorted((Path(__file__).parent / "fixtures").glob("*.json")):
        wire = json.loads(path.read_text(encoding="utf-8")).get("wire", {})
        for key in carriers:
            if key in wire:
                carriers[key] = True
    assert all(carriers.values()), f"carrier(s) never exercised: {carriers}"


def test_declaring_one_tool_result_key_requires_the_other():
    """The tool-result pair is all-or-nothing. A resource-only fixture is legal
    (pinned by test_conforming_resource_fixture_has_no_issues), but a fixture that
    declares either key must declare both — it cannot show the success shape and
    skip the error shape.
    """
    for present, dropped in (
        ("success_result", "error_result"),
        ("error_result", "success_result"),
    ):
        partial = copy.deepcopy(FIXTURE)
        del partial["wire"][dropped]
        issues = validate(partial)
        assert any(f"missing {dropped}" in i.message for i in issues), (
            f"declaring {present} without {dropped} was accepted"
        )


def test_jsonrpc_envelope_must_use_the_renamed_keys():
    bad = copy.deepcopy(RESOURCE)
    data = bad["wire"]["resource_error"]["error"]["data"]
    data["code"] = data.pop("machine_code")  # the forbidden alias
    issues = validate(bad)
    assert any("[6.rename]" in i.message for i in issues)


def test_jsonrpc_error_data_validates_against_the_same_closed_schema():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_error"]["error"]["data"]["recoverable"] = True  # invented alias
    issues = validate(bad)
    assert any("error_schema" in i.where for i in issues)


def test_legacy_band_error_code_rejected():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_error"]["error"]["code"] = -32004
    issues = validate(bad)
    assert any("closed legacy band" in i.message for i in issues)
    # The band renders in the order `contract-checklist.md` states it, nearest
    # zero first, so the diagnostic does not read as an inverted range.
    assert any("-32000..-32019" in i.message for i in issues)


def test_retired_error_code_rejected():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_error"]["error"]["code"] = -32042
    issues = validate(bad)
    assert any("retired" in i.message for i in issues)


def test_unassigned_spec_band_error_code_rejected():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_error"]["error"]["code"] = -32055
    issues = validate(bad)
    assert any("spec-reserved band" in i.message for i in issues)
    assert any("-32020..-32099" in i.message for i in issues)


def test_spec_assigned_code_accepted():
    """Negative control: -32021 is assigned, so the band check must NOT fire."""
    ok = copy.deepcopy(RESOURCE)
    ok["wire"]["resource_error"]["error"]["code"] = -32021
    assert not any("band" in i.message for i in validate(ok))


def test_response_with_both_result_and_error_rejected():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_error"]["result"] = {"resultType": "complete"}
    issues = validate(bad)
    assert any("both 'result' and 'error'" in i.message for i in issues)


def test_bad_jsonrpc_version_rejected():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_error"]["jsonrpc"] = "1.0"
    issues = validate(bad)
    assert any("jsonrpc must be '2.0'" in i.message for i in issues)


# --- repair callability (issue #123) ---


def test_repair_naming_an_unresolvable_tool_rejected():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["error_result"]["structuredContent"]["repair"]["tool"] = "github_no_such_tool"
    issues = validate(bad)
    assert any("does not resolve in the wire.tools catalog" in i.message for i in issues)


def test_non_string_repair_tool_fails_closed_instead_of_raising():
    """An unhashable `repair.tool` must be reported, not crash the membership test.

    The callability check runs before schema validation, so a JSON object or array
    here reached `name not in catalog` and raised TypeError — the validator died
    instead of reporting the fixture as bad.
    """
    for bad_name in ([1, 2], {"a": 1}, 42, None, ""):
        bad = copy.deepcopy(FIXTURE)
        bad["wire"]["error_result"]["structuredContent"]["repair"]["tool"] = bad_name
        issues = validate(bad)  # must report, not raise
        assert any("repair.tool must be a non-empty string" in i.message for i in issues), (
            f"repair.tool={bad_name!r} was not reported"
        )


def test_repair_arguments_must_validate_against_the_named_tools_input_schema():
    bad = copy.deepcopy(FIXTURE)
    # 'state' is an enum; a placeholder is not a literally callable value
    bad["wire"]["error_result"]["structuredContent"]["repair"]["arguments"]["state"] = (
        "<one of the listed states>"
    )
    issues = validate(bad)
    assert any("repair.arguments vs github_list_issues.inputSchema" in i.where for i in issues)


def test_repair_missing_arguments_rejected():
    bad = copy.deepcopy(FIXTURE)
    del bad["wire"]["error_result"]["structuredContent"]["repair"]["arguments"]
    issues = validate(bad)
    assert any("error_schema" in i.where and "arguments" in i.message for i in issues)


def test_repair_without_a_tools_catalog_is_reported():
    bad = copy.deepcopy(FIXTURE)
    del bad["wire"]["tools"]
    issues = validate(bad)
    assert any("declares no 'wire.tools' catalog" in i.message for i in issues)


# --- §6 presence and details one-of (issue #123) ---


def test_null_conditional_field_rejected():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["error_result"]["structuredContent"]["repair"] = None
    issues = validate(bad)
    assert any("omit it instead" in i.message for i in issues)


def test_empty_conditional_field_rejected():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["error_result"]["structuredContent"]["details"] = {}
    issues = validate(bad)
    assert any("empty" in i.message and "omit it instead" in i.message for i in issues)


def test_details_emitting_both_field_and_fields_rejected():
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["error_result"]["structuredContent"]["details"]["fields"] = ["repo", "issue_number"]
    issues = validate(bad)
    assert any("one-of rule" in i.message for i in issues)


def test_documented_code_specific_detail_key_accepted():
    """Negative control: `[6.details-field]` permits documented extra detail keys,
    so `details` must stay open even though the envelope root is closed."""
    ok = copy.deepcopy(FIXTURE)
    ok["wire"]["error_result"]["structuredContent"]["details"]["required_scopes"] = ["repo:read"]
    assert validate(ok) == []


def test_unknown_envelope_root_key_rejected():
    """The root IS closed, unlike details."""
    bad = copy.deepcopy(FIXTURE)
    bad["wire"]["error_result"]["structuredContent"]["retryable"] = True
    issues = validate(bad)
    assert any("error_schema" in i.where for i in issues)


# --- resources/read success result (issue #123) ---


def test_resource_read_requires_cache_hints():
    for field in ("ttlMs", "cacheScope"):
        bad = copy.deepcopy(RESOURCE)
        del bad["wire"]["resource_read_result"]["result"][field]
        issues = validate(bad)
        assert any(field in i.message for i in issues), field


def test_resource_read_negative_ttl_rejected():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_read_result"]["result"]["ttlMs"] = -1
    issues = validate(bad)
    assert any("ttlMs must be a non-negative integer" in i.message for i in issues)


def test_resource_read_bad_cache_scope_rejected():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_read_result"]["result"]["cacheScope"] = "shared"
    issues = validate(bad)
    assert any("cacheScope must be" in i.message for i in issues)


def test_resource_read_empty_contents_rejected():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_read_result"]["result"]["contents"] = []
    issues = validate(bad)
    assert any("contents must be a non-empty array" in i.message for i in issues)


def test_resource_read_content_block_needs_exactly_one_of_text_or_blob():
    bad = copy.deepcopy(RESOURCE)
    bad["wire"]["resource_read_result"]["result"]["contents"][0]["blob"] = "eA=="
    issues = validate(bad)
    assert any("exactly one of 'text' or 'blob'" in i.message for i in issues)


def test_resource_read_content_block_needs_uri():
    bad = copy.deepcopy(RESOURCE)
    del bad["wire"]["resource_read_result"]["result"]["contents"][0]["uri"]
    issues = validate(bad)
    assert any("missing required 'uri'" in i.message for i in issues)
