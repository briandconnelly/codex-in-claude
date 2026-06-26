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


_TOKEN_COUNT_LINE = (
    '{"type":"event_msg","payload":{"type":"token_count",'
    '"info":{"total_token_usage":{"input_tokens":17866,"output_tokens":308,"total_tokens":18174}},'
    '"rate_limits":{"limit_id":"codex","limit_name":null,'
    '"primary":{"used_percent":12.0,"window_minutes":300,"resets_at":1780534461},'
    '"secondary":{"used_percent":8.0,"window_minutes":10080,"resets_at":1780864628},'
    '"credits":null,"plan_type":"plus","rate_limit_reached_type":null}}}'
)


def test_parse_rate_limit_extracts_both_windows():
    snap = normalize.parse_rate_limit(_TOKEN_COUNT_LINE)
    assert snap is not None
    assert snap.plan_type == "plus"
    assert snap.rate_limit_reached_type is None
    assert snap.primary.used_percent == 12.0
    assert snap.primary.window_minutes == 300
    assert snap.primary.resets_at == 1780534461
    assert snap.secondary.used_percent == 8.0
    assert snap.secondary.window_minutes == 10080


def test_parse_rate_limit_absent_returns_none():
    no_limits = '{"type":"event_msg","payload":{"type":"agent_message"}}'
    assert normalize.parse_rate_limit(no_limits) is None


def test_parse_rate_limit_last_event_wins():
    second = _TOKEN_COUNT_LINE.replace('"used_percent":12.0', '"used_percent":40.0')
    snap = normalize.parse_rate_limit(_TOKEN_COUNT_LINE + "\n" + second)
    assert snap.primary.used_percent == 40.0


def test_parse_rate_limit_tolerates_malformed_lines():
    assert normalize.parse_rate_limit("not json\n{bad\n" + _TOKEN_COUNT_LINE) is not None


# ---------------------------------------------------------------------------
# Non-finite float regression tests (NaN / Infinity)
# ---------------------------------------------------------------------------


def _rate_limit_event(resets_at_token: str, used_percent_token: str = "12.0") -> str:
    """Build a token_count event string with raw JSON literal tokens so json.loads
    parses them as NaN/Infinity (Python's json.loads accepts those by default)."""
    return (
        '{"type":"event_msg","payload":{"type":"token_count",'
        '"rate_limits":{"primary":{"used_percent":'
        + used_percent_token
        + ',"window_minutes":300,"resets_at":'
        + resets_at_token
        + "}}}}"
    )


def test_parse_rate_limit_resets_at_nan_does_not_raise():
    """resets_at=NaN must NOT raise; the window should degrade to resets_at=None."""
    event = _rate_limit_event("NaN")
    snap = normalize.parse_rate_limit(event)
    # A non-finite resets_at is treated as absent; used_percent is still present so the
    # window and snapshot survive (not dropped entirely).
    assert snap is not None
    assert snap.primary is not None
    assert snap.primary.resets_at is None


def test_parse_rate_limit_resets_at_infinity_does_not_raise():
    """resets_at=Infinity must NOT raise; the window should degrade to resets_at=None."""
    event = _rate_limit_event("Infinity")
    snap = normalize.parse_rate_limit(event)
    assert snap is not None
    assert snap.primary is not None
    assert snap.primary.resets_at is None


def test_parse_rate_limit_used_percent_nan_does_not_raise():
    """used_percent=NaN must NOT raise; the window should degrade to used_percent=None."""
    event = _rate_limit_event("1780534461", used_percent_token="NaN")
    snap = normalize.parse_rate_limit(event)
    # used_percent=NaN -> None; resets_at is a valid int so the window survives.
    assert snap is not None
    assert snap.primary is not None
    assert snap.primary.used_percent is None


def test_parse_rate_limit_used_percent_negative_is_none():
    """used_percent=-50 (out-of-range) must be coerced to None; window survives via resets_at."""
    event = _rate_limit_event("1780534461", used_percent_token="-50.0")
    snap = normalize.parse_rate_limit(event)
    assert snap is not None
    assert snap.primary is not None
    assert snap.primary.used_percent is None


def test_parse_rate_limit_used_percent_over_100_is_none():
    """used_percent=150 (out-of-range) must be coerced to None; window survives via resets_at."""
    event = _rate_limit_event("1780534461", used_percent_token="150.0")
    snap = normalize.parse_rate_limit(event)
    assert snap is not None
    assert snap.primary is not None
    assert snap.primary.used_percent is None
