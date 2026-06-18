"""Worktree lifecycle: create (seeded from live state), capture diff, remove."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codex_in_claude._core import worktree


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_create_and_remove(repo):
    wt = worktree.create(str(repo), timeout=30)
    assert Path(wt.path).is_dir()
    assert (Path(wt.path) / "a.py").read_text() == "x = 1\n"
    worktree.remove(str(repo), wt, timeout=30)
    assert not Path(wt.path).exists()


def test_seeds_uncommitted_tracked_changes(repo):
    (repo / "a.py").write_text("x = 2\n")  # uncommitted change in live tree
    wt = worktree.create(str(repo), timeout=30)
    try:
        assert (Path(wt.path) / "a.py").read_text() == "x = 2\n"
        assert wt.baseline_warning is None
    finally:
        worktree.remove(str(repo), wt, timeout=30)


def test_capture_diff_isolates_agent_changes(repo):
    (repo / "a.py").write_text("x = 2\n")  # pre-existing uncommitted change
    wt = worktree.create(str(repo), timeout=30)
    try:
        # Simulate the agent editing inside the worktree.
        (Path(wt.path) / "a.py").write_text("x = 2\ny = 9\n")
        (Path(wt.path) / "new.py").write_text("print('new')\n")
        diff = worktree.capture_diff(wt.path, timeout=30)
        # Only the agent's changes (not the pre-existing baseline) are additions.
        assert "+y = 9" in diff
        assert "new.py" in diff
        assert "+x = 2" not in diff  # baseline was committed, not re-reported as added
    finally:
        worktree.remove(str(repo), wt, timeout=30)


def test_capture_diff_excludes_build_artifacts(repo):
    wt = worktree.create(str(repo), timeout=30)
    try:
        (Path(wt.path) / "real.py").write_text("v = 1\n")
        cache = Path(wt.path) / "__pycache__"
        cache.mkdir()
        (cache / "real.cpython-314.pyc").write_bytes(b"\x00\x01junk")
        (Path(wt.path) / "a.pyc").write_bytes(b"\x00")
        diff = worktree.capture_diff(wt.path, timeout=30)
        assert "real.py" in diff
        assert "__pycache__" not in diff
        assert ".pyc" not in diff
    finally:
        worktree.remove(str(repo), wt, timeout=30)


def test_capture_diff_empty_when_no_changes(repo):
    wt = worktree.create(str(repo), timeout=30)
    try:
        assert worktree.capture_diff(wt.path, timeout=30).strip() == ""
    finally:
        worktree.remove(str(repo), wt, timeout=30)


def test_not_a_git_repo(tmp_path):
    with pytest.raises(worktree.NotAGitRepoError):
        worktree.create(str(tmp_path), timeout=30)


def test_no_commits(tmp_path):
    _git(tmp_path, "init", "-q")
    with pytest.raises(worktree.NoCommitsError):
        worktree.create(str(tmp_path), timeout=30)


def test_remove_is_idempotent(repo):
    wt = worktree.create(str(repo), timeout=30)
    worktree.remove(str(repo), wt, timeout=30)
    # Second remove must not raise.
    worktree.remove(str(repo), wt, timeout=30)


def test_ensure_repo_with_head_raises_outside_repo(tmp_path):
    import pytest

    from codex_in_claude._core import worktree

    with pytest.raises(worktree.NotAGitRepoError):
        worktree.ensure_repo_with_head(str(tmp_path), timeout=10)


# --- baseline seeding must never silently misattribute live changes ----------


def _fail_git_on(monkeypatch, predicate, stderr="simulated git failure"):
    """Wrap worktree._git so calls matching predicate(args) fail; others run real."""
    real = worktree._git

    def fake(repo, args, timeout):
        if predicate(args):
            return subprocess.CompletedProcess(["git", *args], 1, "", stderr)
        return real(repo, args, timeout)

    monkeypatch.setattr(worktree, "_git", fake)


def _worktree_count(repo):
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    return len([ln for ln in out.splitlines() if ln.strip()])


def test_seed_commit_failure_raises_and_does_not_leak(repo, monkeypatch):
    # A live uncommitted change exists, the patch applies, but the baseline commit
    # fails. The worktree must NOT be left holding the live change (it would later
    # be misattributed to the agent) — create() raises and cleans up.
    (repo / "a.py").write_text("x = 2\n")
    _fail_git_on(monkeypatch, lambda args: "commit" in args)
    with pytest.raises(worktree.WorktreeError, match="baseline"):
        worktree.create(str(repo), timeout=30)
    assert _worktree_count(repo) == 1  # throwaway worktree was removed


def test_seed_add_failure_raises(repo, monkeypatch):
    (repo / "a.py").write_text("x = 2\n")
    _fail_git_on(monkeypatch, lambda args: args[:2] == ["add", "-A"])
    with pytest.raises(worktree.WorktreeError, match="baseline"):
        worktree.create(str(repo), timeout=30)
    assert _worktree_count(repo) == 1


def test_seed_dirty_after_commit_raises(repo, monkeypatch):
    # commit reports success but is a no-op, leaving staged changes behind. The
    # porcelain-status guard must catch the partial seed rather than let the agent
    # run on top of un-baselined live changes.
    (repo / "a.py").write_text("x = 2\n")
    real = worktree._git

    def fake(r, args, timeout):
        if "commit" in args:
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        return real(r, args, timeout)

    monkeypatch.setattr(worktree, "_git", fake)
    with pytest.raises(worktree.WorktreeError, match="dirty"):
        worktree.create(str(repo), timeout=30)
    assert _worktree_count(repo) == 1


def test_seed_unexpected_exception_cleans_up(repo, monkeypatch):
    # A non-WorktreeError during seeding (e.g. a git subprocess timeout) must still
    # tear down the throwaway worktree rather than leak it.
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(worktree, "_seed_uncommitted", boom)
    with pytest.raises(subprocess.TimeoutExpired):
        worktree.create(str(repo), timeout=30)
    assert _worktree_count(repo) == 1


def test_capture_diff_add_failure_raises(repo, monkeypatch):
    wt = worktree.create(str(repo), timeout=30)
    try:
        _fail_git_on(monkeypatch, lambda args: args[:2] == ["add", "-A"])
        with pytest.raises(worktree.WorktreeError):
            worktree.capture_diff(wt.path, timeout=30)
    finally:
        worktree.remove(str(repo), wt, timeout=30)
