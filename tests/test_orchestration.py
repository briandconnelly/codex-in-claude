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


def test_finalize_review_rejects_exit0_unparseable_prose(monkeypatch):
    """exit-0 with non-JSON prose under the schema review path is an explicit
    invalid_json error, not a silently-downgraded success (#159)."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="Here is my review in prose.")
    out = orchestration.finalize_review(result, meta=meta)
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_json"
    # raw text preserved (redacted, bounded) for debugging
    assert "prose" in out["error"]["message"]


def test_finalize_review_rejects_exit0_empty_message(monkeypatch):
    """Missing/empty last message on exit 0 is invalid_json, not a phantom success."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="")
    out = orchestration.finalize_review(result, meta=meta)
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_json"


def test_finalize_review_rejects_exit0_non_object_json(monkeypatch):
    """Valid JSON that isn't the required object → schema_violation (#159)."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="[1, 2, 3]")
    out = orchestration.finalize_review(result, meta=meta)
    assert out["ok"] is False
    assert out["error"]["code"] == "schema_violation"


def test_finalize_review_redacts_secret_in_raw_preview(monkeypatch):
    """The raw-output preview embedded in the error message is secret-redacted."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    secret = "sk-" + "d" * 32
    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message=f"prose with token={secret}")
    out = orchestration.finalize_review(result, meta=meta)
    assert out["ok"] is False
    assert secret not in str(out)


def test_finalize_review_bounds_raw_preview(monkeypatch):
    """The raw-output preview embedded in the error message is bounded (~300 chars),
    so an unparseable multi-KB response can't bloat the error envelope (#159)."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="z" * 5000)
    out = orchestration.finalize_review(result, meta=meta)
    assert out["ok"] is False
    # The full 5000-char body must not appear verbatim; the preview is truncated.
    assert "z" * 5000 not in out["error"]["message"]
    assert out["error"]["message"].count("z") <= 300


def test_finalize_review_accepts_valid_structured_object(monkeypatch):
    """The happy path is unchanged: a schema-valid object still succeeds (#159)."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(
        exit_code=0,
        last_message='{"summary":"looks good","verdict":"pass","confidence":"high"}',
    )
    out = orchestration.finalize_review(result, meta=meta)
    assert out["ok"] is True
    assert out["verdict"] == "pass"
    assert out["summary"] == "looks good"


def test_finalize_consult_keeps_prose_passthrough(monkeypatch):
    """consult stays lenient: exit-0 prose is a valid Q&A answer, not an error (#159)."""

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="A plain-language answer.")
    out = orchestration.finalize_consult(result, meta=meta)
    assert out["ok"] is True
    assert out["summary"] == "A plain-language answer."


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


# --- Reasoning-effort threading (#309) ---------------------------------------------
def test_run_consult_forwards_reasoning_effort(monkeypatch):
    captured: dict = {}

    async def fake_exec(prompt, **kwargs):
        captured["reasoning_effort"] = kwargs.get("reasoning_effort")
        return codex.CodexExecResult(run=CommandRun("", "", 0, 1, False), last_message=None)

    monkeypatch.setattr(orchestration.codex, "run_codex_exec", fake_exec)
    meta = _make_meta()
    anyio.run(
        lambda: orchestration.run_consult(
            "q",
            ".",
            meta,
            sandbox="read-only",
            isolation="inherit",
            timeout_seconds=10,
            model=None,
            reasoning_effort="high",
        )
    )
    assert captured["reasoning_effort"] == "high"


def test_run_review_forwards_reasoning_effort(monkeypatch):
    from types import SimpleNamespace

    from codex_in_claude._core import gitdiff

    captured: dict = {}

    async def fake_exec(prompt, **kwargs):
        captured["reasoning_effort"] = kwargs.get("reasoning_effort")
        return codex.CodexExecResult(
            run=CommandRun("", "", 0, 1, False),
            last_message='{"summary":"s","verdict":"pass","confidence":"high"}',
        )

    fake_diff = SimpleNamespace(
        summary=SimpleNamespace(files_changed=1, lines_added=1, lines_removed=0),
        redacted_paths=[],
        truncated=False,
        truncation_hint=None,
        text="diff --git a/foo b/foo\n+added",
    )
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: fake_diff)
    monkeypatch.setattr(orchestration.codex, "run_codex_exec", fake_exec)
    meta = _make_meta()
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
            reasoning_effort="xhigh",
            git_timeout=30,
            max_bytes=1_000_000,
        )
    )
    assert captured["reasoning_effort"] == "xhigh"


def test_stamp_meta_classifies_effort_rejection_from_meta(monkeypatch):
    # The failure classifier must learn the sent effort from meta, so a backend
    # effort rejection maps to invalid_reasoning_effort — not cli_contract_changed.
    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    rejection = (
        '{"type":"error","message":"[ReasoningEffortParam] [reasoning.effort] '
        "[invalid_enum_value] Invalid value: 'bogus'.\"}"
    )
    meta = _make_meta()
    meta.reasoning_effort = "bogus"
    result = codex.CodexExecResult(
        run=CommandRun(rejection, "", 1, 12, False), last_message=None, events=rejection
    )
    out = orchestration._stamp_meta(result, meta)
    assert out is not None
    assert out["error"]["code"] == "invalid_reasoning_effort"


def test_stamp_meta_effort_rejection_without_sent_effort_is_drift(monkeypatch):
    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    rejection = (
        '{"type":"error","message":"[reasoning.effort] [invalid_enum_value] '
        "Invalid value: 'bogus'.\"}"
    )
    meta = _make_meta()  # meta.reasoning_effort is None
    result = codex.CodexExecResult(
        run=CommandRun(rejection, "", 1, 12, False), last_message=None, events=rejection
    )
    out = orchestration._stamp_meta(result, meta)
    assert out is not None
    assert out["error"]["code"] == "cli_contract_changed"
