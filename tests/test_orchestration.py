"""Unit tests for orchestration._stamp_meta (rate-limit capture)."""

from __future__ import annotations

from codex_in_claude import codex, rate_limit
from codex_in_claude._core.runtime import CommandRun
from codex_in_claude.schemas import Meta

# events string containing a token_count event with a rate_limits block
_RATE_LIMIT_EVENTS = (
    '{"type":"event_msg","payload":{"type":"token_count",'
    '"rate_limits":{"primary":{"used_percent":10.0,"window_minutes":300,"resets_at":9999999999},'
    '"secondary":{"used_percent":5.0,"window_minutes":10080,"resets_at":9999999999},'
    '"plan_type":"plus"}}}'
)


def _make_meta() -> Meta:
    return Meta(
        cwd="/x",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=180,
        elapsed_ms=0,
    )


def _make_exec_result(
    *, events: str = "", exit_code: int = 0, last_message: str = "ok"
) -> codex.CodexExecResult:
    return codex.CodexExecResult(
        run=CommandRun(events, "", exit_code, 12, exit_code == -9),
        last_message=last_message,
        events=events,
    )


def test_stamp_meta_attaches_rate_limit(monkeypatch):
    from codex_in_claude import orchestration

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(events=_RATE_LIMIT_EVENTS, exit_code=0, last_message="hi")
    orchestration._stamp_meta(result, meta)
    assert meta.rate_limit is not None
    assert meta.rate_limit.status == "available"
    assert meta.rate_limit.plan_type == "plus"
    assert meta.rate_limit.source == "current_run"


def test_stamp_meta_no_rate_limits_block_leaves_none(monkeypatch):
    from codex_in_claude import orchestration

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(events="", exit_code=0, last_message="hi")
    orchestration._stamp_meta(result, meta)
    assert meta.rate_limit is None


def test_stamp_meta_captures_rate_limit_even_on_failure(monkeypatch):
    """rate_limit is captured before the failure-path return."""
    from codex_in_claude import orchestration

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(events=_RATE_LIMIT_EVENTS, exit_code=1, last_message="")
    err = orchestration._stamp_meta(result, meta)
    assert err is not None  # failure path returned an error
    assert meta.rate_limit is not None
    assert meta.rate_limit.source == "current_run"
    # error envelope uses new shape: symbolic next_step, temporary flag
    assert err["error"]["repair"]["next_step"] == "inspect_and_retry"
    assert err["error"]["temporary"] is False
