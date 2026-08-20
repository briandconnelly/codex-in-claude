"""Unit tests for orchestration._stamp_meta (rate-limit capture)."""

from __future__ import annotations

import json

import anyio
from pontonier.core import redaction
from pontonier.core.gitdiff import DiffResult, DiffSummary, InvalidUntrackedError
from pontonier.core.runtime import CommandRun

from codex_in_claude import codex, orchestration
from codex_in_claude.schemas import Coverage, Meta

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


# --- gitdiff_error's invalid_arguments shape (#416) ---------------------------
# InvalidUntrackedError is the one gitdiff exception mapped to invalid_arguments, so its
# envelope owes the per-argument list docs/REFERENCE.md:34-35 promises. Reachable by a
# direct Python call that bypasses the Literal param (see
# test_dry_run_invalid_untracked_returns_structured_error in test_server.py).

# Probe for the no-echo test below (#418): deliberately NOT secret-shaped (mirrors the
# #432 `_PROBES` convention in test_redaction.py), so `redact_text` wouldn't strip it from
# a reused message regardless — an sk-...-shaped probe would pass against the very bug this
# guards (the #417 lesson). Shared by both tests below so the survives-redaction control
# can't silently drift from the value the no-echo test actually probes.
_UNTRACKED_PROBE = "an-ordinary-value-redaction-ignores"


def test_gitdiff_error_invalid_untracked_carries_the_per_argument_list():
    out = orchestration.gitdiff_error(
        InvalidUntrackedError("untracked must be one of [...], got 'bogus'"), _make_meta()
    )
    err = out["error"]
    assert err["code"] == "invalid_arguments"
    # Literal, NOT get_args(Untracked): an expectation derived from the same source the
    # implementation reads cannot fail when that source is wrong.
    assert err["invalid_arguments"] == [
        {
            "field": "untracked",
            "reason": "untracked must be one of 'exclude', 'explicit_only', 'include'.",
            "allowed_values": ["explicit_only", "include", "exclude"],
        }
    ]
    assert err["details"] == err["invalid_arguments"][0]


def test_gitdiff_error_invalid_untracked_never_echoes_the_rejected_value():
    """`InvalidArgument` and `ErrorDetail` promise the rejected value is never echoed (see
    their docstrings) — it may be a secret, and this path's value is caller-supplied free
    text. The exception text embeds it (`got {untracked!r}`), so the whole envelope —
    including the human-readable `message` — must be built without ever reusing `str(exc)`
    for this branch.

    See `_UNTRACKED_PROBE` above for why the probe is shaped the way it is;
    `test_probe_value_survives_redaction` below keeps that control honest, and this test
    additionally asserts the same property inline so the control can't drift from the
    exact value probed here."""
    value = _UNTRACKED_PROBE
    exc = InvalidUntrackedError(f"untracked must be one of [...], got {value!r}")
    out = orchestration.gitdiff_error(exc, _make_meta())

    # Positive control 1: the probe would have reached the message absent the fix — prove
    # it actually made it into the exception text `gitdiff_error` receives.
    assert value in str(exc)

    # Positive control 2 (inline, not just the sibling test): the absence assertion below
    # proves the no-echo rule only if `redact_text` isn't the one removing the probe.
    assert redaction.redact_text(value) == value

    # Whole-envelope absence: not just the machine fields, the entire serialized
    # envelope, including `message`.
    assert value not in json.dumps(out)


def test_probe_value_survives_redaction():
    # Positive control for the test above, using the SAME probe constant so this control
    # cannot drift from the value the no-echo test actually asserts absence of: without
    # this, that assertion proves nothing about the no-echo rule — `redact_text` could be
    # doing the work instead (the #417 lesson).
    assert redaction.redact_text(_UNTRACKED_PROBE) == _UNTRACKED_PROBE


def test_stamp_meta_leaves_rate_limit_none_even_with_legacy_events(monkeypatch):
    # #321: codex 0.144 removed the token_count event; the exec stream no longer carries
    # quota and we no longer scrape it. meta.rate_limit stays None even if a legacy-shaped
    # rate_limits block appears in the events (codex_status reads quota live instead).
    meta = _make_meta()
    result = _make_exec_result(events=_RATE_LIMIT_EVENTS, exit_code=0, last_message="hi")
    orchestration._stamp_meta(result, meta)
    assert meta.rate_limit is None


def test_stamp_meta_no_rate_limits_block_leaves_none(monkeypatch):

    meta = _make_meta()
    result = _make_exec_result(events="", exit_code=0, last_message="hi")
    orchestration._stamp_meta(result, meta)
    assert meta.rate_limit is None


def test_stamp_meta_clears_model_when_model_flag_dropped(monkeypatch):
    """When --model is dropped by help-gating, meta.model is reconciled to None so
    reported provenance matches the default model actually used (#158)."""

    meta = _make_meta()
    meta.model = "gpt-5.5"
    result = _make_exec_result(exit_code=0, dropped_flags=["--model"])
    orchestration._stamp_meta(result, meta)
    assert meta.model is None
    assert "--model" in meta.compat_warnings


def test_stamp_meta_preserves_model_when_not_dropped(monkeypatch):
    """A requested model survives when --model was not dropped (#158)."""

    meta = _make_meta()
    meta.model = "gpt-5.5"
    result = _make_exec_result(exit_code=0)
    orchestration._stamp_meta(result, meta)
    assert meta.model == "gpt-5.5"


def test_finalize_consult_raw_response_model_reflects_dropped_model(monkeypatch):
    """raw_response.model (derived from meta.model) is also None when --model was
    dropped, so the finalized envelope's provenance is consistent (#158)."""

    meta = _make_meta()
    meta.model = "gpt-5.5"
    result = _make_exec_result(exit_code=0, last_message="hello", dropped_flags=["--model"])
    out = orchestration.finalize_consult(result, meta=meta)
    assert out["meta"]["model"] is None
    assert out["raw_response"]["model"] is None
    assert "--model" in out["meta"]["compat_warnings"]


def test_stamp_meta_failure_path_leaves_rate_limit_none(monkeypatch):
    """The failure path stamps meta and returns an error; meta.rate_limit stays None (#321)."""

    meta = _make_meta()
    result = _make_exec_result(events=_RATE_LIMIT_EVENTS, exit_code=1, last_message="")
    err = orchestration._stamp_meta(result, meta)
    assert err is not None  # failure path returned an error
    assert meta.rate_limit is None
    # error envelope uses new shape: symbolic next_step, temporary flag
    assert err["error"]["repair"]["next_step"] == "inspect_and_retry"
    assert err["error"]["temporary"] is False


def test_finalize_review_rejects_exit0_unparseable_prose(monkeypatch):
    """exit-0 with non-JSON prose under the schema review path is an explicit
    invalid_json error, not a silently-downgraded success (#159)."""

    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="Here is my review in prose.")
    out = orchestration.finalize_review(result, meta=meta, coverage=_complete_cov())
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_json"
    # raw text preserved (redacted, bounded) for debugging
    assert "prose" in out["error"]["message"]


def test_finalize_review_rejects_exit0_empty_message(monkeypatch):
    """Missing/empty last message on exit 0 is invalid_json, not a phantom success."""

    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="")
    out = orchestration.finalize_review(result, meta=meta, coverage=_complete_cov())
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_json"


def test_finalize_review_rejects_exit0_non_object_json(monkeypatch):
    """Valid JSON that isn't the required object → schema_violation (#159)."""

    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="[1, 2, 3]")
    out = orchestration.finalize_review(result, meta=meta, coverage=_complete_cov())
    assert out["ok"] is False
    assert out["error"]["code"] == "schema_violation"


def test_finalize_review_redacts_secret_in_raw_preview(monkeypatch):
    """The raw-output preview embedded in the error message is secret-redacted."""

    secret = "sk-" + "d" * 32
    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message=f"prose with token={secret}")
    out = orchestration.finalize_review(result, meta=meta, coverage=_complete_cov())
    assert out["ok"] is False
    assert secret not in str(out)


def test_finalize_review_bounds_raw_preview(monkeypatch):
    """The raw-output preview embedded in the error message is bounded (~300 chars),
    so an unparseable multi-KB response can't bloat the error envelope (#159)."""

    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="z" * 5000)
    out = orchestration.finalize_review(result, meta=meta, coverage=_complete_cov())
    assert out["ok"] is False
    # The full 5000-char body must not appear verbatim; the preview is truncated.
    assert "z" * 5000 not in out["error"]["message"]
    assert out["error"]["message"].count("z") <= 300


def test_finalize_review_accepts_valid_structured_object(monkeypatch):
    """The happy path is unchanged: a schema-valid object still succeeds (#159)."""

    meta = _make_meta()
    result = _make_exec_result(
        exit_code=0,
        last_message='{"summary":"looks good","verdict":"pass","confidence":"high"}',
    )
    out = orchestration.finalize_review(result, meta=meta, coverage=_complete_cov())
    assert out["ok"] is True
    assert out["verdict"] == "pass"
    assert out["summary"] == "looks good"


def test_finalize_consult_keeps_prose_passthrough(monkeypatch):
    """consult stays lenient: exit-0 prose is a valid Q&A answer, not an error (#159)."""

    meta = _make_meta()
    result = _make_exec_result(exit_code=0, last_message="A plain-language answer.")
    out = orchestration.finalize_consult(result, meta=meta)
    assert out["ok"] is True
    assert out["summary"] == "A plain-language answer."


# --- coverage-aware verdict rules (#319) -------------------------------------
def _complete_cov() -> Coverage:
    return Coverage(
        status="complete",
        untracked_files_detected=0,
        untracked_files_included=0,
        untracked_files_omitted=0,
    )


def _partial_cov(reasons=("untracked_omitted",), detected=2) -> Coverage:
    return Coverage(
        status="partial",
        untracked_files_detected=detected,
        untracked_files_included=0,
        untracked_files_omitted=detected,
        omission_reasons=list(reasons),
    )


def _empty_diff(**kw):
    base = {"text": "", "summary": DiffSummary(files_changed=0)}
    base.update(kw)
    return DiffResult(**base)


def _run_review_empty(monkeypatch, diff):
    """Drive run_review with a stubbed gather_diff and a spy on the model call."""
    monkeypatch.setattr(orchestration.gitdiff, "gather_diff", lambda *a, **k: diff)
    calls = {"n": 0}

    async def fake_exec(*a, **k):
        calls["n"] += 1
        return _make_exec_result()

    monkeypatch.setattr(orchestration.codex, "run_codex_exec", fake_exec)
    out = anyio.run(
        lambda: orchestration.run_review(
            ".",
            _make_meta(),
            scope="working_tree",
            base=None,
            commit=None,
            paths=None,
            sandbox="read-only",
            isolation="inherit",
            timeout_seconds=10,
            model=None,
            git_timeout=10,
            max_bytes=200_000,
        )
    )
    return out, calls


def test_finalize_review_complete_coverage_keeps_model_pass(monkeypatch):
    result = _make_exec_result(
        last_message='{"summary":"looks good","verdict":"pass","confidence":"high"}'
    )
    out = orchestration.finalize_review(result, meta=_make_meta(), coverage=_complete_cov())
    assert out["verdict"] == "pass"
    assert out["confidence"] == "high"
    assert out["review_status"] == "completed"
    assert out["coverage"]["status"] == "complete"
    assert out["summary"] == "looks good"


def test_finalize_review_partial_coverage_downgrades_pass_to_unknown(monkeypatch):
    result = _make_exec_result(
        last_message='{"summary":"all changes look good","verdict":"pass","confidence":"high"}'
    )
    out = orchestration.finalize_review(result, meta=_make_meta(), coverage=_partial_cov())
    # A pass over partly-reviewed code is not a pass.
    assert out["verdict"] == "unknown"
    assert out["confidence"] == "low"
    assert out["review_status"] == "completed"
    # The model's all-clear must not stand unqualified beside verdict=unknown.
    assert out["summary"] != "all changes look good"
    assert "partial" in out["summary"].lower()
    assert "all changes look good" in out["summary"]  # original retained for context


def test_finalize_review_partial_coverage_retains_fail(monkeypatch):
    result = _make_exec_result(
        last_message=(
            '{"summary":"a real bug","verdict":"fail","confidence":"high",'
            '"findings":[{"severity":"high","title":"bug","evidence":"e",'
            '"risk":"r","recommendation":"fix it"}]}'
        )
    )
    out = orchestration.finalize_review(result, meta=_make_meta(), coverage=_partial_cov())
    # A demonstrated defect stands regardless of coverage.
    assert out["verdict"] == "fail"
    assert out["confidence"] == "high"
    assert len(out["findings"]) == 1
    assert out["summary"] == "a real bug"


def test_finalize_review_partial_coverage_retains_concerns(monkeypatch):
    result = _make_exec_result(
        last_message='{"summary":"some concern","verdict":"concerns","confidence":"medium"}'
    )
    out = orchestration.finalize_review(result, meta=_make_meta(), coverage=_partial_cov())
    assert out["verdict"] == "concerns"


def test_run_review_untracked_only_is_not_run_not_pass(monkeypatch):
    # THE BUG: an untracked-only tree previously returned pass/high with no model call.
    out, calls = _run_review_empty(
        monkeypatch, _empty_diff(untracked_detected=2, untracked_included=0)
    )
    assert calls["n"] == 0  # the model was never called
    assert out["review_status"] == "not_run"
    assert out["verdict"] == "unknown"  # NOT "pass"
    assert out["confidence"] == "low"
    assert out["coverage"]["status"] == "partial"
    assert out["coverage"]["untracked_files_omitted"] == 2
    assert "untracked" in out["summary"].lower()


def test_run_review_truly_clean_tree_is_not_run_but_complete(monkeypatch):
    out, calls = _run_review_empty(
        monkeypatch, _empty_diff(untracked_detected=0, untracked_included=0)
    )
    assert calls["n"] == 0
    assert out["review_status"] == "not_run"
    assert out["verdict"] == "unknown"  # no model judgment was made
    assert out["coverage"]["status"] == "complete"


def _run_review_empty_with_policy(monkeypatch, diff, untracked):
    monkeypatch.setattr(orchestration.gitdiff, "gather_diff", lambda *a, **k: diff)

    async def boom(*a, **k):
        raise AssertionError("model must not be called on an empty diff")

    monkeypatch.setattr(orchestration.codex, "run_codex_exec", boom)
    return anyio.run(
        lambda: orchestration.run_review(
            ".",
            _make_meta(),
            scope="working_tree",
            base=None,
            commit=None,
            paths=None,
            untracked=untracked,
            sandbox="read-only",
            isolation="inherit",
            timeout_seconds=10,
            model=None,
            git_timeout=10,
            max_bytes=200_000,
        )
    )


def test_run_review_not_run_exclude_does_not_advise_naming_paths(monkeypatch):
    # Under untracked="exclude", naming files in paths still won't review them, so the
    # repair guidance must point to `include`, not "name them in paths" (#322 F4).
    out = _run_review_empty_with_policy(
        monkeypatch, _empty_diff(untracked_detected=2, untracked_included=0), "exclude"
    )
    assert out["review_status"] == "not_run"
    summary = out["summary"].lower()
    assert "name them in paths" not in summary
    assert 'untracked="include"' in out["summary"]


def test_run_review_not_run_explicit_only_advises_paths_or_include(monkeypatch):
    out = _run_review_empty_with_policy(
        monkeypatch, _empty_diff(untracked_detected=2, untracked_included=0), "explicit_only"
    )
    assert out["review_status"] == "not_run"
    assert "paths" in out["summary"]
    assert 'untracked="include"' in out["summary"]


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

    from pontonier.core import gitdiff

    fake_diff = SimpleNamespace(
        summary=SimpleNamespace(files_changed=1, lines_added=1, lines_removed=0),
        untracked_detected=0,
        untracked_included=0,
        redacted_paths=[],
        withheld_paths=[],
        masked_paths=[],
        inline_masks=0,
        truncated=False,
        truncation_hint=None,
        tree_changed_during_gather=False,
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

    from pontonier.core import gitdiff

    captured: dict = {}

    async def fake_exec(prompt, **kwargs):
        captured["reasoning_effort"] = kwargs.get("reasoning_effort")
        return codex.CodexExecResult(
            run=CommandRun("", "", 0, 1, False),
            last_message='{"summary":"s","verdict":"pass","confidence":"high"}',
        )

    fake_diff = SimpleNamespace(
        summary=SimpleNamespace(files_changed=1, lines_added=1, lines_removed=0),
        untracked_detected=0,
        untracked_included=0,
        redacted_paths=[],
        withheld_paths=[],
        masked_paths=[],
        inline_masks=0,
        truncated=False,
        truncation_hint=None,
        tree_changed_during_gather=False,
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


# --- #528: echoed previews and exception text go through the echo sanitizer -----------


def _has_cc(text: str) -> bool:
    return any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in text)


def test_review_invalid_response_preview_strips_control_characters():
    """The preview quotes the model's raw output back into an error message — model text
    is the most directly attacker-shaped input this server echoes."""
    out = orchestration._review_invalid_response_error(
        "invalid_json", "not json \x1b[31mRED\x1b[0m \x07here", _make_meta()
    )
    message = out["error"]["message"]
    assert not _has_cc(message), repr(message)


def test_review_invalid_response_preview_redacts_a_control_split_secret():
    out = orchestration._review_invalid_response_error(
        "invalid_json", "leak sk-\x01ant-api03-" + "A" * 40, _make_meta()
    )
    assert "A" * 40 not in out["error"]["message"]


def test_structured_presentation_fields_are_sanitized_but_raw_response_is_exact():
    """#528, the success-channel half: the NORMALIZED fields are a presentation the server
    composes, and they are the ones most likely to be rendered — so control characters go.
    `raw_response.text` stays byte-exact, because an exact-content carrier is exactly what
    it is for; a caller that needs the model's literal output reads it there.
    """
    payload = '{"summary": "bad \\u001b[31mRED\\u001b[0m", "findings": [{"title": "x\\u0007y"}]}'
    result = codex.CodexExecResult(
        run=CommandRun("", "", 0, 1, False), last_message=payload, events=""
    )
    structured, raw = orchestration._success_common(result, _make_meta())
    assert structured is not None
    assert not _has_cc(str(structured["summary"]))
    assert not _has_cc(str(structured["findings"][0]["title"]))
    # The exact-content carrier keeps what the model actually said, byte for byte. (Here
    # that is the JSON SOURCE, where the control characters are `\\u001b` escape sequences
    # rather than literal bytes — which is exactly why the decoded presentation fields, not
    # this one, are where stripping matters.)
    assert raw.text == payload
