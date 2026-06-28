"""Unit tests for orchestration._stamp_meta (rate-limit capture)."""

from __future__ import annotations

import anyio

from codex_in_claude import codex, orchestration, rate_limit
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
    *,
    events: str = "",
    exit_code: int = 0,
    last_message: str = "ok",
    dropped_flags: list[str] | None = None,
) -> codex.CodexExecResult:
    return codex.CodexExecResult(
        run=CommandRun(events, "", exit_code, 12, exit_code == -9),
        last_message=last_message,
        events=events,
        dropped_flags=dropped_flags or [],
    )


def test_gitdiff_error_redacts_secret():
    secret = "sk-" + "c" * 32
    out = orchestration.gitdiff_error(RuntimeError(f"git failed token={secret}"), _make_meta())
    assert secret not in str(out)
    assert "[redacted: secret value]" in str(out)


def test_stamp_meta_attaches_rate_limit(monkeypatch):

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(events=_RATE_LIMIT_EVENTS, exit_code=0, last_message="hi")
    orchestration._stamp_meta(result, meta)
    assert meta.rate_limit is not None
    assert meta.rate_limit.status == "available"
    assert meta.rate_limit.plan_type == "plus"
    assert meta.rate_limit.source == "current_run"


def test_stamp_meta_no_rate_limits_block_leaves_none(monkeypatch):

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(events="", exit_code=0, last_message="hi")
    orchestration._stamp_meta(result, meta)
    assert meta.rate_limit is None


def test_stamp_meta_clears_model_when_model_flag_dropped(monkeypatch):
    """When --model is dropped by help-gating, meta.model is reconciled to None so
    reported provenance matches the default model actually used (#158)."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    meta.model = "gpt-5.5"
    result = _make_exec_result(exit_code=0, dropped_flags=["--model"])
    orchestration._stamp_meta(result, meta)
    assert meta.model is None
    assert "--model" in meta.compat_warnings


def test_stamp_meta_preserves_model_when_not_dropped(monkeypatch):
    """A requested model survives when --model was not dropped (#158)."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    meta.model = "gpt-5.5"
    result = _make_exec_result(exit_code=0)
    orchestration._stamp_meta(result, meta)
    assert meta.model == "gpt-5.5"


def test_finalize_consult_raw_response_model_reflects_dropped_model(monkeypatch):
    """raw_response.model (derived from meta.model) is also None when --model was
    dropped, so the finalized envelope's provenance is consistent (#158)."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    meta.model = "gpt-5.5"
    result = _make_exec_result(exit_code=0, last_message="hello", dropped_flags=["--model"])
    out = orchestration.finalize_consult(result, meta=meta)
    assert out["meta"]["model"] is None
    assert out["raw_response"]["model"] is None
    assert "--model" in out["meta"]["compat_warnings"]


def test_stamp_meta_captures_rate_limit_even_on_failure(monkeypatch):
    """rate_limit is captured before the failure-path return."""

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


def test_run_consult_forwards_on_event(monkeypatch):
    captured: dict = {}

    async def fake_exec(prompt, **kwargs):
        captured["on_event"] = kwargs.get("on_event")
        return codex.CodexExecResult(run=CommandRun("", "", 0, 1, False), last_message=None)

    monkeypatch.setattr(orchestration.codex, "run_codex_exec", fake_exec)
    sentinel = lambda _l: None  # noqa: E731
    meta = Meta(
        cwd=".",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=10,
        elapsed_ms=0,
    )
    anyio.run(
        lambda: orchestration.run_consult(
            "q",
            ".",
            meta,
            sandbox="read-only",
            isolation="inherit",
            timeout_seconds=10,
            model=None,
            on_event=sentinel,
        )
    )
    assert captured["on_event"] is sentinel


def test_run_review_forwards_on_event(monkeypatch):
    captured: dict = {}

    async def fake_exec(prompt, **kwargs):
        captured["on_event"] = kwargs.get("on_event")
        return codex.CodexExecResult(run=CommandRun("", "", 0, 1, False), last_message=None)

    from types import SimpleNamespace

    from codex_in_claude._core import gitdiff

    fake_diff = SimpleNamespace(
        summary=SimpleNamespace(files_changed=1, lines_added=1, lines_removed=0),
        redacted_paths=[],
        truncated=False,
        truncation_hint=None,
        text="diff --git a/foo b/foo\n+added",
    )
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: fake_diff)
    monkeypatch.setattr(orchestration.codex, "run_codex_exec", fake_exec)
    sentinel = lambda _l: None  # noqa: E731
    meta = Meta(
        cwd=".",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=10,
        elapsed_ms=0,
    )
    anyio.run(
        lambda: orchestration.run_review(
            ".",
            meta,
            scope="working_tree",
            base=None,
            commit=None,
            paths=None,
            sandbox="read-only",
            isolation="inherit",
            timeout_seconds=10,
            model=None,
            git_timeout=30,
            max_bytes=1_000_000,
            on_event=sentinel,
        )
    )
    assert captured["on_event"] is sentinel
