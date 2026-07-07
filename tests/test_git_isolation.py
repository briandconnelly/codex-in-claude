"""Regression tests for #229: the suite must never let an inherited GIT_DIR (e.g. from
a pre-push hook run in a linked worktree) redirect a fixture's git commands onto the
invoking repository, which corrupted its index and config."""

from __future__ import annotations

import subprocess

from conftest import GIT_ISOLATION_VARS, run_git, scrubbed_git_env


def _init_repo(path, filename: str) -> None:
    run_git(path, "init", "-q")
    run_git(path, "config", "user.email", "t@t.co")
    run_git(path, "config", "user.name", "t")
    (path / filename).write_text("original\n")
    run_git(path, "add", "-A")
    run_git(path, "commit", "-qm", "init")


def test_scrubbed_git_env_drops_isolation_vars(monkeypatch):
    for var in GIT_ISOLATION_VARS:
        monkeypatch.setenv(var, "/somewhere/.git")
    env = scrubbed_git_env()
    assert not any(var in env for var in GIT_ISOLATION_VARS)
    # Unrelated vars survive.
    monkeypatch.setenv("PATH", "/usr/bin")
    assert scrubbed_git_env().get("PATH") == "/usr/bin"


def test_run_git_ignores_inherited_git_dir(tmp_path, monkeypatch):
    # The "outer" repo the hook environment points GIT_DIR at.
    outer = tmp_path / "outer"
    outer.mkdir()
    _init_repo(outer, "real.txt")
    outer_head = run_git(outer, "rev-parse", "HEAD").stdout.strip()

    # An unrelated sandbox repo a fixture would work in.
    work = tmp_path / "work"
    work.mkdir()
    _init_repo(work, "base.txt")

    # Simulate the hook: GIT_DIR (and friends) exported, pointing at the outer repo.
    for var in GIT_ISOLATION_VARS:
        monkeypatch.setenv(var, str(outer / ".git"))

    # A destructive fixture-style operation in the sandbox.
    (work / "new.txt").write_text("added\n")
    run_git(work, "add", "-A")
    run_git(work, "commit", "-qm", "sandbox change")

    # The outer repo must be pristine: HEAD unmoved and nothing staged. Verify with an
    # explicitly-anchored, scrubbed-env git so the check itself can't be misdirected.
    def outer_git(*args) -> str:
        return subprocess.run(
            ["git", "--git-dir", str(outer / ".git"), "--work-tree", str(outer), *args],
            capture_output=True,
            text=True,
            check=True,
            env=scrubbed_git_env(),
        ).stdout

    assert outer_git("rev-parse", "HEAD").strip() == outer_head
    assert outer_git("diff", "--cached", "--name-only").strip() == ""
    assert outer_git("status", "--porcelain").strip() == ""
