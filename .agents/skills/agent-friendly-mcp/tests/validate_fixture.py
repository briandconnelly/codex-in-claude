#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "jsonschema>=4",
# ]
# ///
"""Validate an agent-friendly-mcp wire fixture against the skill's contract.

Checks the corrected outputSchema/error contract (contract-checklist.md
§3 output rules and §6 "one error envelope, two carriers"):

  * the success result's structuredContent conforms to the tool's outputSchema;
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


def _success_issues(wire: dict) -> list[Issue]:
    """§3 success path: structuredContent conforms to outputSchema."""
    success = wire.get("success_result")
    if not isinstance(success, dict):
        return [Issue("success_result", "missing success_result")]

    issues: list[Issue] = []
    if success.get("isError") is True:
        issues.append(Issue("success_result", "success_result must not set isError: true"))
    content_issue = _content_fallback_issue(success, "success_result.content")
    if content_issue:
        issues.append(content_issue)
    sc = success.get("structuredContent")
    output_schema = wire.get("output_schema")
    if sc is None:
        issues.append(Issue("success_result", "success_result missing structuredContent"))
    elif isinstance(output_schema, dict):
        issues += _schema_errors(
            sc, output_schema, "success_result.structuredContent vs output_schema"
        )
    else:
        issues.append(Issue("wire.output_schema", "missing output_schema"))
    return issues


def _extract_envelope(error: dict, degraded: bool) -> tuple[object, list[Issue]]:
    """Locate the §6 error envelope on its declared carrier."""
    envelope = error.get("structuredContent")
    if envelope is not None:
        return envelope, []

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
    if error.get("isError") is not True:
        issues.append(Issue("error_result", "error_result must set isError: true"))

    error_content_issue = _content_fallback_issue(error, "error_result.content")
    if error_content_issue:
        issues.append(error_content_issue)

    degraded = bool(wire.get("degraded_text_carrier"))
    envelope, carrier_issues = _extract_envelope(error, degraded)
    issues += carrier_issues

    if envelope is not None:
        issues += _envelope_invariant_issues(envelope, "error_result envelope (§6 invariants)")
        error_schema = wire.get("error_schema")
        if isinstance(error_schema, dict):
            issues += _schema_errors(
                envelope, error_schema, "error_result envelope vs error_schema"
            )
        else:
            issues.append(Issue("wire.error_schema", "missing error_schema"))

    return issues


def validate(fixture: object) -> list[Issue]:
    if not isinstance(fixture, dict):
        return [Issue("fixture", "fixture root must be a JSON object")]
    wire = fixture.get("wire")
    if not isinstance(wire, dict):
        return [Issue("wire", "fixture has no 'wire' object")]
    return _success_issues(wire) + _error_issues(wire)


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
