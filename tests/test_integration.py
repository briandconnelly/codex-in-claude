"""Live tests that call the real `codex` CLI. Opt in with:

    uv run pytest -m integration --no-cov

They require codex to be installed and authenticated (`codex login`). They spend
tokens, so they are excluded from the default run.
"""

from __future__ import annotations

import pytest

from codex_in_claude import codex, server

pytestmark = pytest.mark.integration


def test_status_live():
    res = server.codex_status()
    assert res["codex_found"] is True
    assert res["ready"] is True, res["readiness_detail"]


async def test_consult_live(tmp_path):
    res = await server.codex_consult(
        "Reply concisely in one sentence: what does the DRY principle mean?",
        workspace_root=str(tmp_path),
        timeout_seconds=150,
    )
    assert res["ok"] is True, res.get("error")
    assert res["summary"]
    assert res["meta"]["sandbox"] == "read-only"
    assert res["meta"]["session_id"]


def test_login_status_live():
    logged_in, _ = codex.login_status()
    assert logged_in is True


async def test_review_changes_live(tmp_path):
    import subprocess

    def g(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t.co")
    g("config", "user.name", "t")
    (tmp_path / "m.py").write_text("def f(xs):\n    return xs[0]\n")
    g("add", "-A")
    g("commit", "-qm", "init")
    # Introduce an obvious off-by-one bug.
    (tmp_path / "m.py").write_text(
        "def f(xs):\n"
        "    out = []\n"
        "    for i in range(len(xs) + 1):\n"
        "        out.append(xs[i])\n"
        "    return out\n"
    )
    res = await server.codex_review_changes(
        scope="working_tree", workspace_root=str(tmp_path), timeout_seconds=150
    )
    assert res["ok"] is True, res.get("error")
    assert res["meta"]["context_summary"]["files_changed"] == 1


async def test_delegate_live(tmp_path):
    import subprocess

    def g(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t.co")
    g("config", "user.name", "t")
    (tmp_path / "greet.py").write_text('def greet(n):\n    return "hi " + n\n')
    g("add", "-A")
    g("commit", "-qm", "init")
    before = (tmp_path / "greet.py").read_text()
    res = await server.codex_delegate(
        "Add a farewell(name) function returning 'bye ' + name to greet.py.",
        workspace_root=str(tmp_path),
        timeout_seconds=180,
    )
    assert res["ok"] is True, res.get("error")
    assert res["diff"]  # a proposed patch came back
    assert (tmp_path / "greet.py").read_text() == before  # live tree untouched
    # worktree cleaned up: only the main worktree remains
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout
    assert out.strip().count("\n") == 0


async def test_delegate_async_live(tmp_path, monkeypatch):
    import subprocess
    import time

    # keep job state out of the user's real cache dir
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "jobs"))

    def g(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t.co")
    g("config", "user.name", "t")
    (tmp_path / "greet.py").write_text('def greet(n):\n    return "hi " + n\n')
    g("add", "-A")
    g("commit", "-qm", "init")
    before = (tmp_path / "greet.py").read_text()

    started = await server.codex_delegate_async(
        "Add a farewell(name) function returning 'bye ' + name to greet.py.",
        workspace_root=str(tmp_path),
    )
    assert started["ok"] is True, started.get("error")
    job_id = started["job_id"]

    # poll to completion (bounded)
    deadline = time.monotonic() + 240
    status = None
    while time.monotonic() < deadline:
        status = await server.codex_job_status(job_id, workspace_root=str(tmp_path))
        if status["status"] != "running":
            break
        time.sleep(status.get("poll_after_ms", 1000) / 1000)
    assert status is not None and status["status"] == "done", status

    res = await server.codex_job_result(job_id, workspace_root=str(tmp_path))
    assert res["ok"] is True, res.get("error")
    assert res["diff"]
    assert res["meta"]["job_id"] == job_id
    assert (tmp_path / "greet.py").read_text() == before  # live tree untouched
    # worktree cleaned up
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout
    assert out.strip().count("\n") == 0

    # consume deletes the record
    await server.codex_job_consume_result(job_id, workspace_root=str(tmp_path))
    gone = await server.codex_job_status(job_id, workspace_root=str(tmp_path))
    assert gone["ok"] is False and gone["error"]["code"] == "job_not_found"


async def test_unknown_model_returns_envelope_not_exception(tmp_path):
    """An unknown slug surfaces a structured envelope (likely ok:false), never a crash.

    Opt-in — calls the real codex CLI and may spend. Run with:
        uv run pytest -m integration --no-cov -k unknown_model
    """
    res = await server.codex_consult(
        "ping",
        model="definitely-not-a-real-model-zzz",
        workspace_root=str(tmp_path),
    )
    assert "ok" in res  # structured envelope, not an exception
