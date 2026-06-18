"""Tests for the background worker entry point."""

from __future__ import annotations

import json

from codex_in_claude import _worker, delegate

_SPEC = {
    "task": "do x",
    "cwd": "/tmp/repo",
    "workspace_source": "param",
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
        return {"ok": True, "tool": "codex_delegate", "summary": task}

    monkeypatch.setattr(delegate, "run_delegate", fake_run_delegate)

    rc = _worker.main([str(jd)])
    assert rc == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["summary"] == "do x"


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


def test_worker_no_args_returns_error_code():
    assert _worker.main([]) == 2


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
