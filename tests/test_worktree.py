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


def test_git_ok_redacts_secret_in_error(repo, monkeypatch):
    secret = "sk-" + "b" * 32
    fake = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr=f"fatal: token={secret}"
    )
    monkeypatch.setattr(worktree, "_git", lambda *a, **k: fake)
    with pytest.raises(worktree.WorktreeError) as ei:
        worktree._git_ok(str(repo), ["status"], 30)
    assert secret not in str(ei.value)
    assert "[redacted: secret value]" in str(ei.value)


def test_git_ok_redacts_secret_straddling_truncation_boundary(repo, monkeypatch):
    # A secret crossing the 200-char cut must still be redacted (redact, then truncate).
    secret = "sk-" + "a" * 40
    fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="x" * 190 + secret)
    monkeypatch.setattr(worktree, "_git", lambda *a, **k: fake)
    with pytest.raises(worktree.WorktreeError) as ei:
        worktree._git_ok(str(repo), ["status"], 30)
    assert "sk-aaaaaaa" not in str(ei.value)


def test_create_cleans_parent_on_worktree_add_timeout(repo, monkeypatch):
    # A git hang during `worktree add` raises TimeoutExpired (not WorktreeError); the
    # cleanup must still fire so the temp parent dir does not leak.
    real_git_ok = worktree._git_ok

    def fake_git_ok(repo_arg, args, timeout):
        if args[:2] == ["worktree", "add"]:
            raise subprocess.TimeoutExpired(cmd="git worktree add", timeout=timeout)
        return real_git_ok(repo_arg, args, timeout)

    monkeypatch.setattr(worktree, "_git_ok", fake_git_ok)
    seen: list[str] = []
    with pytest.raises(subprocess.TimeoutExpired):
        worktree.create(str(repo), timeout=30, on_parent=seen.append)
    assert seen and not Path(seen[0]).exists()


def test_create_and_remove(repo):
    wt = worktree.create(str(repo), timeout=30)
    assert Path(wt.path).is_dir()
    assert (Path(wt.path) / "a.py").read_text() == "x = 1\n"
    worktree.remove(str(repo), wt, timeout=30)
    assert not Path(wt.path).exists()


def test_create_reports_parent_early(repo):
    # The on_parent hook fires as soon as the temp parent exists, so a caller can
    # record it for cleanup even if the worker is hard-killed mid-create.
    seen: list[str] = []
    wt = worktree.create(str(repo), timeout=30, on_parent=seen.append)
    try:
        assert seen == [wt.parent]
        assert Path(wt.parent).is_dir()
    finally:
        worktree.remove(str(repo), wt, timeout=30)


def test_create_cleans_temp_parent_if_on_parent_raises(repo):
    # If the on_parent hook raises (e.g. disk-full writing the manifest), the temp
    # parent must not leak — that is the very leak this hook exists to prevent.
    seen: list[str] = []

    def boom(parent):
        seen.append(parent)
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        worktree.create(str(repo), timeout=30, on_parent=boom)
    assert seen and not Path(seen[0]).exists()


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


# --- plan(): read-only baseline preview, no worktree created ------------------


def test_plan_clean_repo(repo):
    plan = worktree.plan(str(repo), timeout=30)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert plan.head_commit == head
    assert plan.head_subject == "init"
    assert plan.tracked_files == 1
    assert plan.tracked_bytes == len(b"x = 1\n")
    assert plan.uncommitted_tracked_files == 0
    assert plan.untracked_files == 0


def test_plan_counts_uncommitted_and_untracked(repo):
    (repo / "a.py").write_text("x = 2\n")  # uncommitted tracked change
    (repo / "new.txt").write_text("hi\n")  # untracked
    plan = worktree.plan(str(repo), timeout=30)
    assert plan.uncommitted_tracked_files == 1
    assert plan.untracked_files == 1
    # tracked_files/bytes reflect the HEAD baseline, not the dirty working tree.
    assert plan.tracked_files == 1
    assert plan.tracked_bytes == len(b"x = 1\n")


def test_plan_counts_staged_changes_as_uncommitted(repo):
    (repo / "a.py").write_text("x = 99\n")
    _git(repo, "add", "-A")  # staged but not committed
    plan = worktree.plan(str(repo), timeout=30)
    assert plan.uncommitted_tracked_files == 1


def test_plan_does_not_create_a_worktree(repo, monkeypatch):
    def boom(*a, **k):  # plan must never call create()
        raise AssertionError("plan must not create a worktree")

    monkeypatch.setattr(worktree, "create", boom)
    worktree.plan(str(repo), timeout=30)
    # And no stray worktrees were registered.
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert out.count("\n") == 1  # only the main worktree


def test_tracked_files_and_bytes_counts_gitlinks_and_skips_malformed(repo, monkeypatch):
    # A submodule gitlink is counted as a file but contributes 0 bytes (size '-');
    # a malformed line (no tab) is ignored entirely.
    listing = (
        "100644 blob abc123 5\ta.py\n"
        "160000 commit deadbeef -\tvendor/sub\n"  # submodule: counted, 0 bytes
        "garbage line without tab\n"  # malformed: skipped
    )
    monkeypatch.setattr(worktree, "_git_ok", lambda *a, **k: listing)
    files, total = worktree._tracked_files_and_bytes(str(repo), 30)
    assert files == 2
    assert total == 5


def test_count_nonempty_lines_treats_git_failure_as_zero(repo):
    import subprocess

    failed = subprocess.CompletedProcess(["git"], 1, "", "boom")
    assert worktree._count_nonempty_lines(failed) == 0
    ok = subprocess.CompletedProcess(["git"], 0, "x\n\n y \n", "")
    assert worktree._count_nonempty_lines(ok) == 2


def test_plan_not_a_git_repo(tmp_path):
    with pytest.raises(worktree.NotAGitRepoError):
        worktree.plan(str(tmp_path), timeout=30)


def test_plan_no_commits(tmp_path):
    _git(tmp_path, "init", "-q")
    with pytest.raises(worktree.NoCommitsError):
        worktree.plan(str(tmp_path), timeout=30)


def test_plan_maps_git_infra_failure_to_worktree_error(repo, monkeypatch):
    # A missing git binary / subprocess timeout must surface as WorktreeError (a
    # structured error the dry-run tool maps to worktree_error), not escape raw.
    def boom(*a, **k):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(worktree, "_git", boom)
    with pytest.raises(worktree.WorktreeError):
        worktree.plan(str(repo), timeout=30)


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
