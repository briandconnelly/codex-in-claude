#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "jsonschema>=4",
# ]
# ///
"""Validate an agent-friendly-mcp wire fixture against the skill's contract.

Checks the corrected outputSchema/error contract (contract-checklist.md
§3 output rules and §6 "one error envelope, two carriers") against the
MCP 2026-07-28 baseline:

  * both results carry the required resultType field with value "complete"
    (a final tool result is "complete" even when isError is true; interim
    MRTR results are out of this fixture's scope);
  * the success result's structuredContent conforms to the tool's outputSchema
    (any JSON value the schema permits, including null — absence of the key
    and a present null are distinguished);
  * the error result sets isError: true and carries the §6 envelope in
    structuredContent (or, only when wire.degraded_text_carrier is declared,
    as JSON text in content[0].text);
  * the error envelope conforms to its own schema, NOT to outputSchema;
  * the error envelope also satisfies the §6 cross-field invariants directly
    (required code/message/temporary/retry_after_ms; retry_after_ms is null
    when temporary is false, else a non-negative integer or null) — independent
    of the fixture-supplied schema, which may not encode them;
  * both results carry a non-empty `content` array that includes a textual
    fallback block, per the §3 output contract.

The JSON-RPC carrier (`wire.resource_error`) is checked against the SAME
`error_schema` after renaming `machine_code`/`human_message` back to
`code`/`message`. §6 permits exactly those two renames and requires "the same
name, shape, and cardinality on both surfaces," so one schema validating both
carriers makes divergence structurally impossible rather than merely tested.

A note on what is deliberately NOT closed: `error_schema.details` stays open
(`additionalProperties: true`) because `[6.details-field]` permits documented
error-code-specific keys such as `required_scopes` alongside `reason`. The
envelope root and `repair` are closed; `details` is not.

Only `fixture["wire"]` is contract-checked. A fixture's `expect_first_call`,
`inject_error`, `expect_repair`, and `metrics` are abbreviated declarations for
the agent-run harness — not wire payloads — and are out of scope by design.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator


@dataclass
class Issue:
    where: str
    message: str

    def format(self, fixture_path: str) -> str:
        return f"{fixture_path}: {self.where}: {self.message}"


def _schema_errors(instance, schema, where: str) -> list[Issue]:
    validator = Draft202012Validator(schema)
    return [
        Issue(where, f"schema violation at {list(e.path)}: {e.message}")
        for e in sorted(validator.iter_errors(instance), key=lambda e: [str(p) for p in e.path])
    ]


def _content_fallback_issue(result: dict, where: str) -> Issue | None:
    """The §3 output contract keeps `content` as a human/compatibility fallback
    alongside structuredContent: a result must carry a non-empty content array
    that includes at least one non-empty textual block."""
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return Issue(
            where,
            "result must carry a non-empty 'content' array (§3 human/compatibility fallback)",
        )
    has_text = any(
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block["text"].strip()
        for block in content
    )
    if not has_text:
        return Issue(
            where,
            "result 'content' must include a non-empty text block (§3 textual fallback)",
        )
    return None


def _envelope_invariant_issues(envelope: object, where: str) -> list[Issue]:
    """§6 cross-field invariants that a fixture-supplied error_schema may not encode."""
    if not isinstance(envelope, dict):
        return [Issue(where, "error envelope must be a JSON object")]
    issues: list[Issue] = []
    for field in ("code", "message", "temporary", "retry_after_ms"):
        if field not in envelope:
            issues.append(Issue(where, f"error envelope missing required §6 field '{field}'"))
    temporary = envelope.get("temporary")
    delay = envelope.get("retry_after_ms")
    if temporary is False and delay is not None:
        issues.append(Issue(where, "retry_after_ms must be null when temporary is false (§6)"))
    if delay is not None and (isinstance(delay, bool) or not isinstance(delay, int) or delay < 0):
        issues.append(
            Issue(where, "retry_after_ms must be a non-negative integer when present (§6)")
        )
    return issues


# §6 permits exactly these two renames on the JSON-RPC surface (`[6.rename]`).
_JSONRPC_RENAMES = {"machine_code": "code", "human_message": "message"}

# Fields the §6 table marks conditional: omitted entirely when they do not
# apply, never sent as a placeholder null or empty container (`[6.presence]`).
# `retry_after_ms` is excluded — it is the one nullable *required* field.
_CONDITIONAL_FIELDS = (
    "details",
    "repair",
    "rate_limit_remaining",
    "request_id",
    "resource_uri",
    "fingerprint",
)

# The `[6.jsonrpc-code-allocation]` partition of the JSON-RPC reserved range.
# That rule is the authority for these bands and for the assigned-code set; when a
# spec revision assigns a new code in the MCP band, update the rule first and then
# this constant — do not add one here alone.
_LEGACY_BAND = (-32019, -32000)  # closed: never allocate, never assume meaning
_MCP_BAND = (-32099, -32020)  # reserved for the MCP spec; only assigned codes are legal
_SPEC_ASSIGNED_CODES = {-32020, -32021, -32022}  # HeaderMismatch, MissingRequiredClientCapability,
# per-request _meta version mismatch
_RETIRED_CODES = {-32002, -32042}  # retired by earlier revisions; never emit


def _band_text(band: tuple[int, int]) -> str:
    """Render a `(low, high)` band the way `contract-checklist.md` writes it: the
    code nearest zero first, e.g. `(-32019, -32000)` -> `-32000..-32019`.

    The tuples are ordered low-to-high so the range checks read naturally; the
    diagnostics are ordered to match the rule text a reader will look it up in.
    """
    low, high = band
    return f"{high}..{low}"


def _details_issues(envelope: dict, where: str) -> list[Issue]:
    """`[6.details-field]`: emit exactly one of `field` or `fields`, never both."""
    details = envelope.get("details")
    if not isinstance(details, dict):
        return []
    has_field = "field" in details
    has_fields = "fields" in details
    if has_field and has_fields:
        return [Issue(where, "details emits both 'field' and 'fields' (§6 one-of rule)")]
    return []


def _presence_issues(envelope: dict, where: str) -> list[Issue]:
    """`[6.presence]`: a conditional field is omitted, not sent as null or empty."""
    issues: list[Issue] = []
    for field in _CONDITIONAL_FIELDS:
        if field not in envelope:
            continue
        value = envelope[field]
        if value is None:
            issues.append(
                Issue(where, f"conditional field '{field}' is null; omit it instead (§6 presence)")
            )
        elif isinstance(value, (dict, list)) and not value:
            issues.append(
                Issue(where, f"conditional field '{field}' is empty; omit it instead (§6 presence)")
            )
    return issues


def _repair_callability_issues(envelope: dict, tools: object, where: str) -> list[Issue]:
    """`[6.repair-callable]`: `repair.tool` names a real tool and `repair.arguments`
    are literally callable against that tool's published `inputSchema`.

    Checked against the fixture's own `wire.tools` catalog rather than by
    guessing at placeholder-shaped text: a name either resolves or it does not.
    """
    repair = envelope.get("repair")
    if not isinstance(repair, dict):
        return []
    if not isinstance(tools, list):
        return [
            Issue(
                where,
                "repair present but fixture declares no 'wire.tools' catalog to resolve it against",
            )
        ]

    catalog: dict[str, dict] = {}
    for entry in tools:
        if isinstance(entry, dict):
            entry_name = entry.get("name")
            if isinstance(entry_name, str):
                catalog[entry_name] = entry
    name = repair.get("tool")
    # Fail closed on a non-string name. `error_schema` also rejects it, but this
    # check runs first, and an unhashable value (a JSON object or array) would
    # raise on the membership test below before the schema ever reported it.
    if not isinstance(name, str) or not name:
        return [Issue(where, f"repair.tool must be a non-empty string naming a tool, got {name!r}")]
    if name not in catalog:
        return [
            Issue(
                where,
                f"repair.tool {name!r} does not resolve in the wire.tools catalog "
                f"({sorted(catalog)}) — repair must name a real callable surface",
            )
        ]

    schema = catalog[name].get("inputSchema")
    if not isinstance(schema, dict):
        return [Issue(where, f"tool {name!r} in wire.tools publishes no inputSchema")]
    return _schema_errors(
        repair.get("arguments"), schema, f"{where}: repair.arguments vs {name}.inputSchema"
    )


def _code_allocation_issues(code: object, where: str) -> list[Issue]:
    """`[6.jsonrpc-code-allocation]`: partition of the JSON-RPC reserved range."""
    if isinstance(code, bool) or not isinstance(code, int):
        return [Issue(where, f"JSON-RPC error.code must be an integer, got {code!r}")]
    if code in _RETIRED_CODES:
        return [Issue(where, f"error.code {code} is retired and must never be emitted (§6)")]
    if _LEGACY_BAND[0] <= code <= _LEGACY_BAND[1]:
        return [
            Issue(
                where,
                f"error.code {code} falls in the closed legacy band "
                f"{_band_text(_LEGACY_BAND)}; "
                "new implementations must not allocate there (§6)",
            )
        ]
    if _MCP_BAND[0] <= code <= _MCP_BAND[1] and code not in _SPEC_ASSIGNED_CODES:
        return [
            Issue(
                where,
                f"error.code {code} falls in the spec-reserved band "
                f"{_band_text(_MCP_BAND)} but is not a "
                f"spec-assigned code {sorted(_SPEC_ASSIGNED_CODES)} (§6)",
            )
        ]
    return []


def _jsonrpc_response_issues(resp: dict, where: str) -> list[Issue]:
    """Shape of the JSON-RPC response envelope itself, independent of `error.data`."""
    issues: list[Issue] = []
    if resp.get("jsonrpc") != "2.0":
        issues.append(Issue(where, f"jsonrpc must be '2.0', got {resp.get('jsonrpc')!r}"))
    if "id" not in resp:
        issues.append(Issue(where, "response missing 'id'"))
    has_result, has_error = "result" in resp, "error" in resp
    if has_result and has_error:
        issues.append(Issue(where, "response carries both 'result' and 'error'"))
    elif not has_result and not has_error:
        issues.append(Issue(where, "response carries neither 'result' nor 'error'"))
    return issues


def _normalize_jsonrpc_envelope(data: dict, where: str) -> tuple[dict, list[Issue]]:
    """Rename the JSON-RPC spellings back to the canonical ones so ONE schema
    validates both carriers, and enforce that the rename actually happened."""
    issues: list[Issue] = []
    for renamed, canonical in _JSONRPC_RENAMES.items():
        if canonical in data:
            issues.append(
                Issue(
                    where,
                    f"error.data carries '{canonical}'; the JSON-RPC surface "
                    f"must use '{renamed}' (`[6.rename]`)",
                )
            )
    normalized = {_JSONRPC_RENAMES.get(k, k): v for k, v in data.items()}
    return normalized, issues


def _envelope_contract_issues(
    envelope: object, error_schema: object, tools: object, where: str
) -> list[Issue]:
    """Every §6 obligation that applies to a canonical (tool-result-spelled) envelope."""
    issues = _envelope_invariant_issues(envelope, where)
    if not isinstance(envelope, dict):
        return issues
    issues += _details_issues(envelope, where)
    issues += _presence_issues(envelope, where)
    issues += _repair_callability_issues(envelope, tools, where)
    if isinstance(error_schema, dict):
        issues += _schema_errors(envelope, error_schema, f"{where} vs error_schema")
    else:
        issues.append(Issue("wire.error_schema", "missing error_schema"))
    return issues


def _resource_read_result_issues(wire: dict) -> list[Issue]:
    """`resources/read` success result: required resultType and cache hints, and a
    non-empty `contents` array of TextResourceContents / BlobResourceContents."""
    resp = wire.get("resource_read_result")
    if resp is None:
        return []
    if not isinstance(resp, dict):
        return [Issue("resource_read_result", "must be a JSON-RPC response object")]

    where = "resource_read_result"
    issues = _jsonrpc_response_issues(resp, where)
    result = resp.get("result")
    if not isinstance(result, dict):
        return [*issues, Issue(where, "missing 'result' object")]

    issues += _result_type_issues(result, f"{where}.result")

    ttl = result.get("ttlMs")
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0:
        issues.append(
            Issue(f"{where}.result", f"ttlMs must be a non-negative integer, got {ttl!r}")
        )
    if result.get("cacheScope") not in ("public", "private"):
        issues.append(
            Issue(
                f"{where}.result",
                f"cacheScope must be 'public' or 'private', got {result.get('cacheScope')!r}",
            )
        )

    contents = result.get("contents")
    if not isinstance(contents, list) or not contents:
        issues.append(Issue(f"{where}.result", "contents must be a non-empty array"))
        return issues
    for i, block in enumerate(contents):
        at = f"{where}.result.contents[{i}]"
        if not isinstance(block, dict):
            issues.append(Issue(at, "content block must be an object"))
            continue
        uri = block.get("uri")
        if not isinstance(uri, str) or not uri:
            issues.append(Issue(at, "content block missing required 'uri'"))
        has_text = isinstance(block.get("text"), str)
        has_blob = isinstance(block.get("blob"), str)
        if has_text == has_blob:
            issues.append(Issue(at, "content block must carry exactly one of 'text' or 'blob'"))
    return issues


def _resource_error_issues(wire: dict) -> list[Issue]:
    """§6 JSON-RPC carrier: the same envelope, renamed, inside `error.data`."""
    resp = wire.get("resource_error")
    if resp is None:
        return []
    if not isinstance(resp, dict):
        return [Issue("resource_error", "must be a JSON-RPC response object")]

    where = "resource_error"
    issues = _jsonrpc_response_issues(resp, where)
    error = resp.get("error")
    if not isinstance(error, dict):
        return [*issues, Issue(where, "missing 'error' object")]

    issues += _code_allocation_issues(error.get("code"), f"{where}.error.code")
    if not isinstance(error.get("message"), str) or not error["message"]:
        issues.append(Issue(f"{where}.error", "native error.message must be a non-empty string"))

    data = error.get("data")
    if not isinstance(data, dict):
        return [
            *issues,
            Issue(f"{where}.error", "error.data must carry the §6 envelope object"),
        ]

    normalized, rename_issues = _normalize_jsonrpc_envelope(data, f"{where}.error.data")
    issues += rename_issues
    issues += _envelope_contract_issues(
        normalized, wire.get("error_schema"), wire.get("tools"), f"{where}.error.data (normalized)"
    )
    return issues


def _result_type_issues(result: dict, where: str) -> list[Issue]:
    """2026-07-28 requires resultType on every result; a final tool result is 'complete'."""
    if "resultType" not in result:
        return [Issue(where, "result missing required 'resultType' field (2026-07-28)")]
    if result["resultType"] != "complete":
        return [
            Issue(
                where,
                f"final tool result must carry resultType 'complete', got {result['resultType']!r}",
            )
        ]
    return []


def _success_issues(wire: dict) -> list[Issue]:
    """§3 success path: resultType present; structuredContent conforms to outputSchema."""
    success = wire.get("success_result")
    if not isinstance(success, dict):
        return [Issue("success_result", "missing success_result")]

    issues: list[Issue] = []
    issues += _result_type_issues(success, "success_result")
    if success.get("isError") is True:
        issues.append(Issue("success_result", "success_result must not set isError: true"))
    content_issue = _content_fallback_issue(success, "success_result.content")
    if content_issue:
        issues.append(content_issue)
    output_schema = wire.get("output_schema")
    if "structuredContent" not in success:
        # Key absence is the violation; a present null is legal wherever the
        # outputSchema permits it (structuredContent is any JSON value under
        # 2026-07-28) and is judged by the schema below.
        issues.append(Issue("success_result", "success_result missing structuredContent"))
    elif isinstance(output_schema, dict):
        issues += _schema_errors(
            success["structuredContent"],
            output_schema,
            "success_result.structuredContent vs output_schema",
        )
    else:
        issues.append(Issue("wire.output_schema", "missing output_schema"))
    return issues


def _extract_envelope(error: dict, degraded: bool) -> tuple[object, list[Issue]]:
    """Locate the §6 error envelope on its declared carrier."""
    if "structuredContent" in error:
        # A present-but-null envelope is a real violation reported by the
        # invariant check, not a cue to fall through to the degraded carrier.
        return error["structuredContent"], []

    if not degraded:
        return None, [
            Issue(
                "error_result",
                "error envelope must be in structuredContent (native carrier); "
                "a content[0].text carrier is allowed only when "
                "wire.degraded_text_carrier is declared",
            )
        ]

    # disclosed degraded mode: envelope rides as JSON text in content[0].text
    content = error.get("content")
    first = content[0] if isinstance(content, list) and content else None
    text = first.get("text") if isinstance(first, dict) else None
    if text is None:
        return None, [
            Issue(
                "error_result",
                "degraded_text_carrier declared but content[0].text is missing",
            )
        ]
    try:
        return json.loads(text), []
    except (ValueError, TypeError):
        return None, [
            Issue(
                "error_result",
                "degraded_text_carrier content[0].text is not valid envelope JSON",
            )
        ]


def _error_issues(wire: dict) -> list[Issue]:
    """§6 error path: isError + envelope in structuredContent vs error_schema."""
    error = wire.get("error_result")
    if not isinstance(error, dict):
        return [Issue("error_result", "missing error_result")]

    issues: list[Issue] = []
    issues += _result_type_issues(error, "error_result")
    if error.get("isError") is not True:
        issues.append(Issue("error_result", "error_result must set isError: true"))

    error_content_issue = _content_fallback_issue(error, "error_result.content")
    if error_content_issue:
        issues.append(error_content_issue)

    degraded = bool(wire.get("degraded_text_carrier"))
    envelope, carrier_issues = _extract_envelope(error, degraded)
    issues += carrier_issues

    if not carrier_issues:
        issues += _envelope_contract_issues(
            envelope,
            wire.get("error_schema"),
            wire.get("tools"),
            "error_result envelope (§6)",
        )

    return issues


def validate(fixture: object) -> list[Issue]:
    if not isinstance(fixture, dict):
        return [Issue("fixture", "fixture root must be a JSON object")]
    wire = fixture.get("wire")
    if not isinstance(wire, dict):
        return [Issue("wire", "fixture has no 'wire' object")]

    issues: list[Issue] = []
    # The tool-result pair is optional as a unit — a resource-only fixture is legal —
    # but declaring either key requires both, so no fixture can show the success shape
    # and skip the error shape. Carrier coverage across the whole suite is asserted by
    # test_validate_fixture.py, not per fixture.
    if "success_result" in wire or "error_result" in wire:
        issues += _success_issues(wire) + _error_issues(wire)
    issues += _resource_read_result_issues(wire)
    issues += _resource_error_issues(wire)
    if not issues and not any(
        k in wire
        for k in ("success_result", "error_result", "resource_error", "resource_read_result")
    ):
        issues.append(Issue("wire", "fixture declares no wire results to check"))
    return issues


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: validate_fixture.py FIXTURE.json | DIR ...", file=sys.stderr)
        return 2

    paths: list[Path] = []
    for arg in argv:
        p = Path(arg)
        paths.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])

    errors = 0
    for path in paths:
        try:
            fixture = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"{path}: load: {exc}", file=sys.stderr)
            errors += 1
            continue
        for issue in validate(fixture):
            print(issue.format(str(path)))
            errors += 1

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
