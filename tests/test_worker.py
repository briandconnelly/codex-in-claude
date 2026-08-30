"""Tests for the background worker entry point."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import threading

import pytest

from codex_in_claude import _worker, delegate
from codex_in_claude.errors import make_error
from codex_in_claude.schemas import ErrorResult, InvalidArgument, Meta

_SPEC = {
    "kind": "codex_delegate",
    "task": "do x",
    "cwd": "/tmp/repo",
    "workspace_source": "param",
    "tier": "propose",
    "sandbox": "workspace-write",
    "isolation": "inherit",
    "timeout_seconds": 60,
    "model": None,
    "git_timeout": 60,
}


def _write_spec(job_dir, **overrides):
    spec = {**_SPEC, **overrides}
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "spec.json").write_text(json.dumps(spec))
    return spec


def test_worker_writes_result(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))

    async def fake_run_delegate(task, cwd, meta, **kw):
        assert task == "do x"
        assert kw["sandbox"] == "workspace-write"
        assert callable(kw["on_event"])
        return {"ok": True, "tool": "codex_delegate", "summary": task}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)

    rc = _worker.main([str(jd)])
    assert rc == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["summary"] == "do x"


def test_worker_threads_max_diff_bytes(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path), max_diff_bytes=4096)

    seen = {}

    async def fake_run_delegate(task, cwd, meta, **kw):
        seen["max_diff_bytes"] = kw.get("max_diff_bytes")
        return {"ok": True, "tool": "codex_delegate", "summary": task}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)
    assert _worker.main([str(jd)]) == 0
    assert seen["max_diff_bytes"] == 4096


def test_worker_max_diff_bytes_absent_is_none(tmp_path, monkeypatch):
    # Older specs lack the key; the worker forwards None so run_delegate defaults it.
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))

    seen = {}

    async def fake_run_delegate(task, cwd, meta, **kw):
        seen["max_diff_bytes"] = kw.get("max_diff_bytes", "MISSING")
        return {"ok": True, "tool": "codex_delegate", "summary": task}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)
    assert _worker.main([str(jd)]) == 0
    assert seen["max_diff_bytes"] is None


def test_worker_crash_writes_error(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(delegate, "run_delegate", boom)

    rc = _worker.main([str(jd)])
    assert rc == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["ok"] is False
    assert out["error"]["code"] == "internal_error"
    assert "kaboom" in out["error"]["message"]
    assert out["error"]["repair"]["next_step"] == "retry_then_report"
    assert out["error"]["temporary"] is True


def test_worker_crash_redacts_secret_in_message(tmp_path, monkeypatch):
    # F10: the worker's crash sink writes result.json, which the server returns to the
    # client unchanged — so a secret in the exception message must be redacted at write time.
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))

    async def boom(*a, **k):
        raise RuntimeError("crashed with token AKIAIOSFODNN7EXAMPLE")

    monkeypatch.setattr(delegate, "run_delegate", boom)

    assert _worker.main([str(jd)]) == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["error"]["code"] == "internal_error"
    assert "AKIAIOSFODNN7EXAMPLE" not in out["error"]["message"]
    assert "[redacted: secret value]" in out["error"]["message"]
    # The safe exception class name is preserved, consistent with the other sinks.
    assert "RuntimeError" in out["error"]["message"]


def test_worker_crash_omits_empty_exception_detail(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))

    async def boom(*a, **k):
        raise RuntimeError()

    monkeypatch.setattr(delegate, "run_delegate", boom)

    assert _worker.main([str(jd)]) == 0
    out = json.loads((jd / "result.json").read_text())
    msg = out["error"]["message"]
    assert msg == "background worker crashed: RuntimeError"
    assert not msg.endswith(": ")


def test_worker_no_args_returns_error_code():
    assert _worker.main([]) == 2


def test_worker_writes_cleanup_manifest(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))

    async def fake_run_delegate(task, cwd, meta, **kw):
        kw["on_worktree_parent"]("/tmp/cic-worktree-abc")
        return {"ok": True}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)
    _worker.main([str(jd)])
    manifest = json.loads((jd / "cleanup.json").read_text())
    assert manifest == {"paths": ["/tmp/cic-worktree-abc"]}


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="POSIX signals only")
def test_worker_sigterm_runs_worktree_cleanup(tmp_path, monkeypatch):
    # SIGTERM must cancel the run cleanly so run_delegate's finally tears down the
    # worktree — the whole point of the graceful-termination contract.
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))
    parent = tmp_path / "wt-parent"
    state = {"cleaned": False}

    async def fake_run_delegate(task, cwd, meta, **kw):
        parent.mkdir()
        kw["on_worktree_parent"](str(parent))
        try:
            await asyncio.sleep(10)
        finally:  # mimics worktree.remove() in run_delegate's finally
            shutil.rmtree(parent, ignore_errors=True)
            state["cleaned"] = True

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)
    threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    rc = _worker.main([str(jd)])

    assert rc == 0
    assert state["cleaned"] is True  # the finally ran despite termination
    assert not parent.exists()  # worktree removed
    assert not (jd / "result.json").exists()  # cancelled jobs leave no result


def test_worker_meta_carries_workspace_warning(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path), workspace_source="cwd")

    captured = {}

    async def fake_run_delegate(task, cwd, meta, **kw):
        captured["meta"] = meta
        return {"ok": True}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)
    _worker.main([str(jd)])
    assert captured["meta"].workspace_warning is not None
    assert captured["meta"].tier == "propose"


def test_worker_dispatches_consult(tmp_path, monkeypatch):
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_consult",
        question="why?",
        extra_context="ctx",
        tier="consult",
        sandbox="read-only",
        cwd=str(tmp_path),
    )

    async def fake_run_consult(question, cwd, meta, **kw):
        assert question == "why?"
        assert kw["extra_context"] == "ctx"
        assert kw["sandbox"] == "read-only"
        assert callable(kw["on_event"])
        return {"ok": True, "tool": "codex_consult", "summary": question}

    monkeypatch.setattr(orchestration, "run_consult", fake_run_consult)
    rc = _worker.main([str(jd)])
    assert rc == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["tool"] == "codex_consult"


def test_worker_dispatches_review(tmp_path, monkeypatch):
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_review_changes",
        scope="working_tree",
        base=None,
        commit=None,
        paths=None,
        tier="consult",
        sandbox="read-only",
        max_bytes=200000,
        cwd=str(tmp_path),
    )

    async def fake_run_review(cwd, meta, **kw):
        assert kw["scope"] == "working_tree"
        assert kw["max_bytes"] == 200000
        assert callable(kw["on_event"])
        return {"ok": True, "tool": "codex_review_changes", "summary": "reviewed"}

    monkeypatch.setattr(orchestration, "run_review", fake_run_review)
    rc = _worker.main([str(jd)])
    assert rc == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["tool"] == "codex_review_changes"


def test_worker_unknown_kind_writes_error(tmp_path):
    jd = tmp_path / "job"
    _write_spec(jd, kind="codex_bogus", cwd=str(tmp_path))
    rc = _worker.main([str(jd)])
    assert rc == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["ok"] is False
    assert out["error"]["code"] == "internal_error"
    assert out["error"]["repair"]["next_step"] == "retry_then_report"


def test_worker_makes_observer_that_counts_jsonl_event_lines(tmp_path):
    rec_dir = tmp_path
    observer, recorder = _worker._activity_observer(rec_dir)
    observer('{"type":"token_count"}\n')  # counts (parses as JSON object)
    observer("\n")  # blank — ignored
    observer("not-json line\n")  # non-object — ignored
    observer("{not json\n")  # starts with { but does NOT parse — ignored
    observer("[1, 2, 3]\n")  # valid JSON but not an object — ignored
    observer('{"type":"agent_message"}\n')  # counts
    recorder.flush()
    data = json.loads((rec_dir / "activity.json").read_text())
    assert data["events_seen"] == 2


# --- Reasoning-effort threading (#309) ---------------------------------------------
def test_worker_threads_reasoning_effort(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path), reasoning_effort="high")

    seen = {}

    async def fake_run_delegate(task, cwd, meta, **kw):
        seen["reasoning_effort"] = kw.get("reasoning_effort")
        seen["meta_reasoning_effort"] = meta.reasoning_effort
        return {"ok": True, "tool": "codex_delegate", "summary": task}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)
    assert _worker.main([str(jd)]) == 0
    assert seen["reasoning_effort"] == "high"
    assert seen["meta_reasoning_effort"] == "high"


def test_worker_reasoning_effort_absent_is_none(tmp_path, monkeypatch):
    # A legacy spec (pre-#309) lacks the key entirely; the worker forwards None.
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))

    seen = {}

    async def fake_run_delegate(task, cwd, meta, **kw):
        seen["reasoning_effort"] = kw.get("reasoning_effort", "MISSING")
        seen["meta_reasoning_effort"] = meta.reasoning_effort
        return {"ok": True, "tool": "codex_delegate", "summary": task}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)
    assert _worker.main([str(jd)]) == 0
    assert seen["reasoning_effort"] is None
    assert seen["meta_reasoning_effort"] is None


def test_worker_threads_reasoning_effort_consult_and_review(tmp_path, monkeypatch):
    from codex_in_claude import orchestration

    seen = {}

    async def fake_run_consult(question, cwd, meta, **kw):
        seen["consult"] = kw.get("reasoning_effort")
        return {"ok": True, "tool": "codex_consult", "summary": "s"}

    async def fake_run_review(cwd, meta, **kw):
        seen["review"] = kw.get("reasoning_effort")
        return {"ok": True, "tool": "codex_review_changes", "summary": "s"}

    monkeypatch.setattr(orchestration, "run_consult", fake_run_consult)
    monkeypatch.setattr(orchestration, "run_review", fake_run_review)

    jd = tmp_path / "consult-job"
    _write_spec(
        jd,
        kind="codex_consult",
        question="q",
        cwd=str(tmp_path),
        tier="consult",
        sandbox="read-only",
        reasoning_effort="low",
    )
    assert _worker.main([str(jd)]) == 0

    jd2 = tmp_path / "review-job"
    _write_spec(
        jd2,
        kind="codex_review_changes",
        cwd=str(tmp_path),
        tier="consult",
        sandbox="read-only",
        scope="working_tree",
        max_bytes=1000,
        reasoning_effort="medium",
    )
    assert _worker.main([str(jd2)]) == 0

    assert seen["consult"] == "low"
    assert seen["review"] == "medium"


# --- roots_source provenance in the worker-written envelope (#393) -------------------
# The worker's Meta is the one a DELIVERED paid success (or crash) envelope carries, so
# roots_source has to survive the spec round-trip to reach a caller at all.


def test_worker_threads_roots_source(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path), roots_source="client")

    seen = {}

    async def fake_run_delegate(task, cwd, meta, **kw):
        seen["meta_roots_source"] = meta.roots_source
        return {"ok": True, "tool": "codex_delegate", "summary": task}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)
    assert _worker.main([str(jd)]) == 0
    assert seen["meta_roots_source"] == "client"


def test_worker_roots_source_absent_is_none(tmp_path, monkeypatch):
    # A legacy spec (written before #393) lacks the key; the worker reads it as None,
    # which slims away on delivery — absence, not a wrong value. No migration needed.
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))

    seen = {}

    async def fake_run_delegate(task, cwd, meta, **kw):
        seen["meta_roots_source"] = meta.roots_source
        return {"ok": True, "tool": "codex_delegate", "summary": task}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)
    assert _worker.main([str(jd)]) == 0
    assert seen["meta_roots_source"] is None


def test_worker_crash_error_carries_roots_source(tmp_path, monkeypatch):
    # The crash sink builds its own Meta from the spec through a SEPARATE serializer
    # branch (serialize_error, not dump_success), so a paid failure could lose the field
    # while every success test still passed.
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path), roots_source="probe_failed")

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(delegate, "run_delegate", boom)
    assert _worker.main([str(jd)]) == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["ok"] is False
    assert out["meta"]["roots_source"] == "probe_failed"


def test_worker_crash_error_omits_roots_source_for_legacy_spec(tmp_path, monkeypatch):
    # Guards the guard above: serialize_error uses exclude_none, so a legacy spec must
    # leave the key ABSENT rather than emit an explicit null.
    jd = tmp_path / "job"
    _write_spec(jd, cwd=str(tmp_path))

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(delegate, "run_delegate", boom)
    assert _worker.main([str(jd)]) == 0
    out = json.loads((jd / "result.json").read_text())
    assert "roots_source" not in out["meta"]


# --- Persistence-boundary guard against nonconformant invalid_arguments envelopes (#419) --
# errors.make_error enforces a non-empty invalid_arguments list for every SERVER-BUILT
# invalid_arguments envelope, but nothing stops a worker-executed path from writing a
# malformed one straight to result.json, and replay (server.py) reconstructs stored
# records via ErrorResult.model_validate, deliberately bypassing that constructor guard.
# The worker's own persistence boundary is the last place that can catch this.

_GOOD_META = {
    "cwd": "/tmp/repo",
    "tier": "consult",
    "sandbox": "read-only",
    "isolation": "inherit",
    "timeout_seconds": 60,
    "elapsed_ms": 42,
}


@pytest.mark.parametrize(
    "error_fields",
    [
        pytest.param({}, id="missing-key"),
        pytest.param({"invalid_arguments": []}, id="empty-list"),
    ],
)
def test_worker_guards_nonconformant_invalid_arguments_envelope(
    tmp_path, monkeypatch, error_fields
):
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_review_changes",
        scope="working_tree",
        base=None,
        commit=None,
        paths=None,
        tier="consult",
        sandbox="read-only",
        max_bytes=200000,
        cwd=str(tmp_path),
    )

    malformed = {
        "ok": False,
        "error": {
            "code": "invalid_arguments",
            "message": "bad args",
            "temporary": False,
            "retry_after_ms": None,
            # The nonconformant shape #419 guards: either the list key is missing
            # entirely, or present but empty — both are "no list" per the contract.
            **error_fields,
        },
        "meta": {**_GOOD_META, "cwd": str(tmp_path)},
    }
    called = {"count": 0}

    async def fake_run_review(cwd, meta, **kw):
        called["count"] += 1
        return malformed

    monkeypatch.setattr(orchestration, "run_review", fake_run_review)
    rc = _worker.main([str(jd)])
    assert rc == 0
    # Mutation-check: the malformed payload actually reached the persistence boundary,
    # so this test can't pass vacuously if the dispatch plumbing changes underneath it.
    assert called["count"] == 1

    out = json.loads((jd / "result.json").read_text())
    invalid_arguments = out.get("error", {}).get("invalid_arguments")
    is_conformant = bool(invalid_arguments) or out["error"]["code"] != "invalid_arguments"
    assert is_conformant
    # Pin the actual normalization the guard performs.
    assert out["ok"] is False
    assert out["error"]["code"] == "internal_error"
    assert "nonconformant invalid_arguments" in out["error"]["message"]

    # Replay leg: the persisted record round-trips through the same model replay uses.
    replayed = ErrorResult.model_validate(out)
    assert replayed.error.code == "internal_error"
    assert not replayed.error.invalid_arguments


def test_worker_conformant_invalid_arguments_envelope_passes_through_unchanged(
    tmp_path, monkeypatch
):
    from codex_in_claude import orchestration
    from codex_in_claude.errors import serialize_error

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_review_changes",
        scope="working_tree",
        base=None,
        commit=None,
        paths=None,
        tier="consult",
        sandbox="read-only",
        max_bytes=200000,
        cwd=str(tmp_path),
    )

    conformant = serialize_error(
        ErrorResult(
            error=make_error(
                "invalid_arguments",
                "bad scope",
                invalid_arguments=[InvalidArgument(field="scope", reason="must be a known enum")],
            ),
            meta=Meta.model_validate({**_GOOD_META, "cwd": str(tmp_path)}),
        )
    )

    async def fake_run_review(cwd, meta, **kw):
        return conformant

    monkeypatch.setattr(orchestration, "run_review", fake_run_review)
    rc = _worker.main([str(jd)])
    assert rc == 0

    out = json.loads((jd / "result.json").read_text())
    assert out == conformant  # untouched — no false positives on an already-conformant envelope
    assert out["error"]["invalid_arguments"]
    # The fixture's details is make_error's own auto-derived mirror of entry [0] — the
    # exact shape the guard's conformance check requires alongside the non-empty list.
    assert out["error"]["details"] == {
        "field": "scope",
        "reason": "must be a known enum",
        "field_withheld": False,
    }


@pytest.mark.parametrize(
    "error_overrides",
    [
        pytest.param({}, id="details-absent"),  # base malformed dict has no "details" key
        pytest.param(
            {"details": {"field": "other_field", "reason": "must be a known enum"}},
            id="details-mismatched-field",
        ),
    ],
)
def test_worker_guards_invalid_arguments_with_nonmirrored_details(
    tmp_path, monkeypatch, error_overrides
):
    # A list-only conformance check would pass these through: the list is non-empty, but
    # `details` either doesn't mirror entry [0] or is missing entirely — the exact drift
    # the guard exists to catch, just moved from the list into `details` (Codex review
    # follow-up on #419).
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_review_changes",
        scope="working_tree",
        base=None,
        commit=None,
        paths=None,
        tier="consult",
        sandbox="read-only",
        max_bytes=200000,
        cwd=str(tmp_path),
    )

    malformed = {
        "ok": False,
        "error": {
            "code": "invalid_arguments",
            "message": "bad scope",
            "temporary": False,
            "retry_after_ms": None,
            "invalid_arguments": [{"field": "scope", "reason": "must be a known enum"}],
            **error_overrides,
        },
        "meta": {**_GOOD_META, "cwd": str(tmp_path)},
    }
    called = {"count": 0}

    async def fake_run_review(cwd, meta, **kw):
        called["count"] += 1
        return malformed

    monkeypatch.setattr(orchestration, "run_review", fake_run_review)
    rc = _worker.main([str(jd)])
    assert rc == 0
    assert called["count"] == 1  # mutation-check: the malformed payload reached the boundary

    out = json.loads((jd / "result.json").read_text())
    assert out["ok"] is False
    assert out["error"]["code"] == "internal_error"
    assert "nonconformant invalid_arguments" in out["error"]["message"]

    replayed = ErrorResult.model_validate(out)
    assert replayed.error.code == "internal_error"


def test_worker_guard_falls_through_on_pathological_error_shape(tmp_path, monkeypatch):
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_review_changes",
        scope="working_tree",
        base=None,
        commit=None,
        paths=None,
        tier="consult",
        sandbox="read-only",
        max_bytes=200000,
        cwd=str(tmp_path),
    )

    # `error` is not a dict at all — the guard must navigate this defensively and never
    # raise; a broken guard must not be worse than no guard, so it falls through to
    # persisting the original payload unchanged.
    pathological = {
        "ok": False,
        "error": "not-a-dict",
        "meta": {**_GOOD_META, "cwd": str(tmp_path)},
    }

    async def fake_run_review(cwd, meta, **kw):
        return pathological

    monkeypatch.setattr(orchestration, "run_review", fake_run_review)
    rc = _worker.main([str(jd)])
    assert rc == 0

    out = json.loads((jd / "result.json").read_text())
    assert out == pathological


def test_worker_guard_falls_through_when_meta_validation_raises(tmp_path, monkeypatch):
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_review_changes",
        scope="working_tree",
        base=None,
        commit=None,
        paths=None,
        tier="consult",
        sandbox="read-only",
        max_bytes=200000,
        cwd=str(tmp_path),
    )

    # `meta` is a dict (passes the isinstance check) but missing every required field, so
    # Meta.model_validate raises inside the guard's try block — exercising the actual
    # except-and-fall-through path, not just an early isinstance return.
    malformed = {
        "ok": False,
        "error": {"code": "invalid_arguments", "message": "bad args"},
        "meta": {"not_a_real_field": True},
    }

    async def fake_run_review(cwd, meta, **kw):
        return malformed

    monkeypatch.setattr(orchestration, "run_review", fake_run_review)
    rc = _worker.main([str(jd)])
    assert rc == 0

    out = json.loads((jd / "result.json").read_text())
    assert out == malformed


def test_worker_guard_normalizes_with_spec_meta_when_meta_missing(tmp_path, monkeypatch):
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_review_changes",
        scope="working_tree",
        base=None,
        commit=None,
        paths=None,
        tier="consult",
        sandbox="read-only",
        max_bytes=200000,
        cwd=str(tmp_path),
    )

    # No `meta` key at all: the guard still normalizes, falling back to a meta built
    # from the job spec (mirroring the crash sink) rather than losing the record.
    malformed = {
        "ok": False,
        "error": {"code": "invalid_arguments", "message": "bad args"},
    }

    async def fake_run_review(cwd, meta, **kw):
        return malformed

    monkeypatch.setattr(orchestration, "run_review", fake_run_review)
    rc = _worker.main([str(jd)])
    assert rc == 0

    out = json.loads((jd / "result.json").read_text())
    assert out["ok"] is False
    assert out["error"]["code"] == "internal_error"
    assert out["meta"]["cwd"] == str(tmp_path)


# --- #556: the job spec carries the text; the delivered meta carries the fingerprint --


def test_worker_meta_rebuilds_developer_instructions_fingerprint(tmp_path):
    from codex_in_claude import _worker as w
    from codex_in_claude.schemas import DeveloperInstructions

    spec = {
        "kind": "codex_consult",
        "cwd": str(tmp_path),
        "tier": "consult",
        "sandbox": "read-only",
        "isolation": "inherit",
        "timeout_seconds": 60,
        "developer_instructions": "focus",
    }
    meta = w._meta_from_spec(spec)
    assert meta.developer_instructions == DeveloperInstructions.of("focus")
    # A legacy (pre-#556) or no-instruction spec reads as None — same .get() idiom as
    # reasoning_effort.
    del spec["developer_instructions"]
    assert w._meta_from_spec(spec).developer_instructions is None


def test_worker_dispatches_consult_with_developer_instructions(tmp_path, monkeypatch):
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_consult",
        question="why?",
        tier="consult",
        sandbox="read-only",
        cwd=str(tmp_path),
        developer_instructions="focus on locking",
    )

    async def fake_run_consult(question, cwd, meta, **kw):
        assert kw["developer_instructions"] == "focus on locking"
        # The fingerprint the caller will read is rebuilt HERE, not at launch.
        assert meta.developer_instructions is not None
        return {"ok": True, "tool": "codex_consult", "summary": question}

    monkeypatch.setattr(orchestration, "run_consult", fake_run_consult)
    assert _worker.main([str(jd)]) == 0
    out = json.loads((jd / "result.json").read_text())
    assert out.get("ok") is True, out
    # The crash sink also exits 0, so the written result is the real assertion: an
    # unforwarded kwarg would surface here as an internal_error envelope.
    out = json.loads((jd / "result.json").read_text())
    assert out.get("ok") is True, out


def test_worker_dispatches_review_with_developer_instructions(tmp_path, monkeypatch):
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_review_changes",
        tier="consult",
        sandbox="read-only",
        cwd=str(tmp_path),
        scope="working_tree",
        git_timeout=5,
        max_bytes=1000,
        developer_instructions="focus",
    )

    async def fake_run_review(cwd, meta, **kw):
        assert kw["developer_instructions"] == "focus"
        return {"ok": True, "tool": "codex_review_changes", "summary": "s"}

    monkeypatch.setattr(orchestration, "run_review", fake_run_review)
    assert _worker.main([str(jd)]) == 0
    out = json.loads((jd / "result.json").read_text())
    assert out.get("ok") is True, out


def test_worker_dispatch_without_key_passes_none(tmp_path, monkeypatch):
    from codex_in_claude import orchestration

    jd = tmp_path / "job"
    _write_spec(
        jd,
        kind="codex_consult",
        question="q",
        tier="consult",
        sandbox="read-only",
        cwd=str(tmp_path),
    )

    async def fake_run_consult(question, cwd, meta, **kw):
        assert kw["developer_instructions"] is None
        assert meta.developer_instructions is None
        return {"ok": True, "tool": "codex_consult", "summary": question}

    monkeypatch.setattr(orchestration, "run_consult", fake_run_consult)
    assert _worker.main([str(jd)]) == 0
    out = json.loads((jd / "result.json").read_text())
    assert out.get("ok") is True, out


def test_worker_meta_fingerprint_hashes_the_normalized_text(tmp_path):
    # Opus review: a hand-edited spec of "  x  " must not attest sha256("  x  ")
    # while the backend sends the normalized "x" — the fingerprint describes the
    # bytes SENT.
    from codex_in_claude import _worker as w
    from codex_in_claude.schemas import DeveloperInstructions

    spec = {
        "kind": "codex_consult",
        "cwd": str(tmp_path),
        "tier": "consult",
        "sandbox": "read-only",
        "isolation": "inherit",
        "timeout_seconds": 60,
        "developer_instructions": "  focus  ",
    }
    assert w._meta_from_spec(spec).developer_instructions == DeveloperInstructions.of("focus")
    # Whitespace-only normalizes to absent, not to a fingerprint of whitespace.
    spec["developer_instructions"] = "   "
    assert w._meta_from_spec(spec).developer_instructions is None
