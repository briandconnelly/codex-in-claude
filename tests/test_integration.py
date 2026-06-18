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
