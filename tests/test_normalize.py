"""Event metadata parsing and structured-findings extraction."""

from __future__ import annotations

from codex_in_claude import normalize


def test_parse_event_metadata_usage_and_session():
    events = "\n".join(
        [
            '{"type":"session.created","session_id":"sess-123"}',
            '{"type":"token_count","usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}',
            "not json",
            "",
        ]
    )
    usage, session_id = normalize.parse_event_metadata(events)
    assert session_id == "sess-123"
    assert usage is not None
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.total_tokens == 15


def test_usage_total_derived_when_cli_omits_it():
    # The current codex CLI emits token_count without a total; derive it from
    # input + output (cached is a subset of input and must not be added). (#28)
    events = (
        '{"type":"token_count","usage":'
        '{"input_tokens":100,"output_tokens":20,"cached_input_tokens":80}}'
    )
    usage, _ = normalize.parse_event_metadata(events)
    assert usage is not None
    assert usage.total_tokens == 120  # 100 + 20, NOT + 80 cached


def test_usage_explicit_cli_total_wins_over_derivation():
    # A CLI-provided total is forward-compat and must be honored verbatim, even if
    # it does not equal input + output.
    events = (
        '{"type":"token_count","usage":{"input_tokens":100,"output_tokens":20,"total_tokens":999}}'
    )
    usage, _ = normalize.parse_event_metadata(events)
    assert usage is not None
    assert usage.total_tokens == 999


def test_usage_total_not_derived_without_both_input_and_output():
    # With only one of input/output present there is no meaningful total to derive.
    events = '{"type":"token_count","usage":{"input_tokens":100}}'
    usage, _ = normalize.parse_event_metadata(events)
    assert usage is not None
    assert usage.total_tokens is None


def test_parse_event_metadata_nested_session():
    events = '{"type":"x","msg":{"thread_id":"t-9"}}'
    _, session_id = normalize.parse_event_metadata(events)
    assert session_id == "t-9"


def test_parse_event_metadata_empty():
    usage, session_id = normalize.parse_event_metadata("")
    assert usage is None
    assert session_id is None


def test_parse_structured_plain_json():
    obj = normalize.parse_structured('{"summary":"ok","verdict":"pass"}')
    assert obj == {"summary": "ok", "verdict": "pass"}


def test_parse_structured_code_fence():
    fenced = '```json\n{"summary":"ok"}\n```'
    assert normalize.parse_structured(fenced) == {"summary": "ok"}


def test_parse_structured_non_object():
    assert normalize.parse_structured('"just a string"') is None
    assert normalize.parse_structured("not json at all") is None
    assert normalize.parse_structured(None) is None


def test_classify_structured_ok():
    status, parsed = normalize.classify_structured('{"summary":"ok","verdict":"pass"}')
    assert status == "ok"
    assert parsed == {"summary": "ok", "verdict": "pass"}


def test_classify_structured_ok_code_fence():
    status, parsed = normalize.classify_structured('```json\n{"summary":"ok"}\n```')
    assert status == "ok"
    assert parsed == {"summary": "ok"}


def test_classify_structured_invalid_json_when_absent_or_empty():
    assert normalize.classify_structured(None) == ("invalid_json", None)
    assert normalize.classify_structured("") == ("invalid_json", None)
    assert normalize.classify_structured("   \n  ") == ("invalid_json", None)


def test_classify_structured_invalid_json_when_not_parseable():
    assert normalize.classify_structured("not json at all") == ("invalid_json", None)


def test_classify_structured_schema_violation_when_not_object():
    # Valid JSON, but not the object the schema requires.
    assert normalize.classify_structured('"just a string"') == ("schema_violation", None)
    assert normalize.classify_structured("[1, 2, 3]") == ("schema_violation", None)
    assert normalize.classify_structured("42") == ("schema_violation", None)
    # JSON null and booleans are valid JSON scalars, not objects → schema_violation.
    assert normalize.classify_structured("null") == ("schema_violation", None)
    assert normalize.classify_structured("true") == ("schema_violation", None)
    assert normalize.classify_structured("false") == ("schema_violation", None)


def test_coerce_findings_valid_and_invalid():
    raw = [
        {
            "severity": "high",
            "title": "bug",
            "evidence": "line 3",
            "risk": "crash",
            "recommendation": "fix it",
        },
        {"severity": "not-a-severity", "title": "bad"},  # dropped
        "garbage",  # dropped
    ]
    findings = normalize.coerce_findings(raw)
    assert len(findings) == 1
    assert findings[0].title == "bug"


def test_coerce_findings_non_list():
    assert normalize.coerce_findings(None) == []
    assert normalize.coerce_findings("x") == []


def test_extract_error_message_plain():
    events = '{"type":"turn.failed","error":{"message":"boom happened"}}'
    assert normalize.extract_error_message(events) == "boom happened"


def test_extract_error_message_unwraps_nested_json():
    inner = '{"type":"error","error":{"type":"invalid_request_error","message":"bad schema"}}'
    events = '{"type":"error","message":' + repr_json(inner) + "}"
    assert normalize.extract_error_message(events) == "bad schema"


def test_extract_error_message_none_when_absent():
    events = '{"type":"turn.completed"}\n{"type":"item.completed"}'
    assert normalize.extract_error_message(events) is None


def repr_json(s: str) -> str:
    import json

    return json.dumps(s)


def test_a_replacement_bearing_session_id_is_rejected_not_published():
    """A lossy identifier must read as absent, never as a different valid-looking id (#578).

    pontonier 0.8.0 decodes captured output with `errors="replace"`, so one invalid UTF-8
    byte inside a `thread_id` now yields U+FFFD *inside an otherwise parseable event* --
    a shape 0.7.0 could not produce, because it lost the whole capture instead. `session_id`
    is not prose: an agent resumes a Codex thread with it, so a corrupted-but-plausible id
    is worse than None. It sends the agent to `codex resume` against an id that never
    existed, with nothing in the envelope hinting a byte was substituted.
    """
    events = '{"thread_id":"01a05a8d-e1d9-x�y-99b8-d2549f463b71"}'
    _, session_id = normalize.parse_event_metadata(events)
    assert session_id is None


def test_an_intact_session_id_still_survives():
    """Positive control: the rejection above must not swallow ordinary identifiers.

    Without this, a `_find_session_id` that returned None unconditionally would pass the
    rejection test, and the guard would prove nothing.
    """
    events = '{"thread_id":"01a05a8d-e1d9-75f2-99b8-d2549f463b71"}'
    _, session_id = normalize.parse_event_metadata(events)
    assert session_id == "01a05a8d-e1d9-75f2-99b8-d2549f463b71"


def test_a_rejected_session_id_does_not_suppress_a_later_intact_one():
    """Rejection drops the lossy value only -- the scan keeps looking (#578).

    `parse_event_metadata` keeps the FIRST id it finds, so a naive guard could have made a
    corrupted early event poison a stream whose later events carry the id intact.
    """
    events = '{"thread_id":"01a05a8d-x�y"}\n{"thread_id":"01a05a8d-e1d9-75f2-99b8-d2549f463b71"}'
    _, session_id = normalize.parse_event_metadata(events)
    assert session_id == "01a05a8d-e1d9-75f2-99b8-d2549f463b71"


def test_a_replacement_bearing_nested_session_id_is_also_rejected():
    """The guard must cover the nested `msg`/`payload`/`data` path, not just top level.

    `_find_session_id` recurses, so a guard applied at only one level would leave the other
    reachable -- and codex nests these events in practice.
    """
    events = '{"msg":{"thread_id":"01a05a8d-x�y-99b8"}}'
    _, session_id = normalize.parse_event_metadata(events)
    assert session_id is None
