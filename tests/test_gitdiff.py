"""Git diff gathering across scopes, validation, and bounding."""

from __future__ import annotations

import subprocess

import pytest

from codex_in_claude._core import gitdiff


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_working_tree_scope(repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert "return a - b" in res.text
    assert res.summary.files_changed == 1
    assert res.summary.lines_added >= 1
    assert res.summary.lines_removed >= 1


def test_working_tree_empty(repo):
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert res.summary.files_changed == 0
    assert res.text.strip() == ""


def test_branch_scope(repo):
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b + 1\n")
    _git(repo, "commit", "-qam", "tweak")
    res = gitdiff.gather_diff(str(repo), "branch", base=base_sha, timeout=30, max_bytes=200_000)
    assert "a + b + 1" in res.text
    assert res.summary.files_changed == 1


def test_branch_invalid_base(repo):
    with pytest.raises(gitdiff.InvalidBaseError):
        gitdiff.gather_diff(str(repo), "branch", base="-bad", timeout=30, max_bytes=200_000)


def test_branch_nonexistent_base(repo):
    with pytest.raises(gitdiff.InvalidBaseError):
        gitdiff.gather_diff(
            str(repo), "branch", base="no-such-branch", timeout=30, max_bytes=200_000
        )


def test_commit_scope(repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    res = gitdiff.gather_diff(str(repo), "commit", commit=head, timeout=30, max_bytes=200_000)
    assert "def add" in res.text
    assert res.summary.files_changed == 1


def test_commit_invalid(repo):
    with pytest.raises(gitdiff.InvalidCommitError):
        gitdiff.gather_diff(str(repo), "commit", commit="zzzz", timeout=30, max_bytes=200_000)


def test_invalid_scope(repo):
    with pytest.raises(gitdiff.InvalidScopeError):
        gitdiff.gather_diff(str(repo), "bogus", timeout=30, max_bytes=200_000)


def test_not_a_git_repo(tmp_path):
    with pytest.raises(gitdiff.NotAGitRepoError):
        gitdiff.gather_diff(str(tmp_path), "working_tree", timeout=30, max_bytes=200_000)


@pytest.mark.parametrize("bad", ["../escape", "/abs/path", ":(top)", "a\\b", "-x"])
def test_invalid_paths(repo, bad):
    with pytest.raises(gitdiff.InvalidPathsError):
        gitdiff.gather_diff(str(repo), "working_tree", paths=[bad], timeout=30, max_bytes=200_000)


def test_truncation(repo):
    big = "def add(a, b):\n" + "\n".join(f"    x{i} = {i}" for i in range(500)) + "\n"
    (repo / "calc.py").write_text(big)
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200)
    assert res.truncated
    assert res.truncation_hint
    assert len(res.text.encode("utf-8")) <= 200
    assert res.diff_bytes > 200


def test_path_filter(repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "other.py").write_text("x = 1\n")
    _git(repo, "add", "other.py")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["calc.py"], timeout=30, max_bytes=200_000
    )
    assert "calc.py" in res.text
    assert "other.py" not in res.text
