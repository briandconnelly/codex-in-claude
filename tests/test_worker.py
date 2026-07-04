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
