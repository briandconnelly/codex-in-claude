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


# --- explicitly-named untracked files (#74) ---------------------------------
def test_working_tree_named_untracked_file_reviewed(repo):
    # A brand-new (never-staged) file named in paths must be reviewed, not silently dropped.
    (repo / "fresh.py").write_text("def f():\n    return 42\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["fresh.py"], timeout=30, max_bytes=200_000
    )
    assert "fresh.py" in res.text
    assert "return 42" in res.text
    assert "new file" in res.text
    assert res.summary.files_changed == 1
    assert res.summary.lines_added == 2


def test_working_tree_named_untracked_combined_with_tracked(repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # tracked, modified
    (repo / "fresh.py").write_text("x = 1\n")  # untracked
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["calc.py", "fresh.py"], timeout=30, max_bytes=200_000
    )
    assert "return a - b" in res.text
    assert "fresh.py" in res.text
    assert res.summary.files_changed == 2


def test_working_tree_untracked_under_named_directory(repo):
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("y = 2\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["pkg"], timeout=30, max_bytes=200_000
    )
    assert "pkg/mod.py" in res.text
    assert "y = 2" in res.text


def test_untracked_not_included_without_paths(repo):
    # Default behavior is unchanged: no paths => only tracked changes, untracked invisible.
    (repo / "fresh.py").write_text("z = 3\n")
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert "fresh.py" not in res.text
    assert res.summary.files_changed == 0


def test_named_ignored_untracked_file_excluded(repo):
    (repo / ".gitignore").write_text("ignored.py\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore")
    (repo / "ignored.py").write_text("secret = 1\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["ignored.py"], timeout=30, max_bytes=200_000
    )
    # exclude-standard: a gitignored file named in paths is not surfaced.
    assert "ignored.py" not in res.text
    assert res.summary.files_changed == 0


def test_named_untracked_secret_file_redacted(repo):
    (repo / ".env").write_text("SECRET_TOKEN=supersecretvalue1234567890\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=[".env"], timeout=30, max_bytes=200_000
    )
    assert "supersecretvalue" not in res.text
    assert ".env" in res.redacted_paths


def test_named_untracked_symlink_to_dir_reviewed(repo):
    # A `git diff --no-index` against a symlink-to-directory fails with an access error;
    # the symlink must instead be surfaced as a `mode 120000` new-file patch (#74).
    (repo / "realdir").mkdir()
    (repo / "link").symlink_to("realdir", target_is_directory=True)
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["link"], timeout=30, max_bytes=200_000
    )
    assert "b/link" in res.text
    assert "120000" in res.text
    assert "+realdir" in res.text
    assert res.summary.files_changed == 1
    assert res.summary.lines_added == 1


def test_named_untracked_symlink_multiline_target(repo):
    # A POSIX symlink target may contain newlines; every line must be `+`-prefixed and
    # the hunk count must match so the synthesized diff stays well-formed.
    (repo / "link").symlink_to("first\nsecond")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["link"], timeout=30, max_bytes=200_000
    )
    assert "@@ -0,0 +1,2 @@" in res.text
    assert "+first" in res.text
    assert "+second" in res.text
    # No target line may slip in unprefixed (would let crafted text spoof diff structure).
    assert "\nsecond\n" not in res.text
    assert res.summary.lines_added == 2


def test_untracked_file_clean_filter_not_applied(repo):
    # Gathering must not run configured gitattributes clean filters (a code-exec surface),
    # and must show the raw working-tree bytes, not the filtered/normalized form.
    _git(repo, "config", "filter.evil.clean", "sed s/SECRET/MANGLED/")
    (repo / ".gitattributes").write_text("*.sec filter=evil\n")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-qm", "attr")
    (repo / "data.sec").write_text("has SECRET here\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["data.sec"], timeout=30, max_bytes=200_000
    )
    assert "has SECRET here" in res.text  # raw bytes
    assert "MANGLED" not in res.text  # clean filter never ran


def test_untracked_file_blob_not_persisted_in_repo(repo):
    # Gathering must not leave the raw (pre-redaction) bytes of an untracked file as a
    # blob in the repo's own object store, where it could outlive the redacted review.
    (repo / "leak.txt").write_text("TOP SECRET LEAK value\n")
    gitdiff.gather_diff(
        str(repo), "working_tree", paths=["leak.txt"], timeout=30, max_bytes=200_000
    )
    sha = subprocess.run(
        ["git", "hash-object", "leak.txt"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    present = subprocess.run(["git", "cat-file", "-e", sha], cwd=repo, check=False).returncode == 0
    assert not present, "raw untracked blob leaked into repo .git/objects"


def test_untracked_content_line_starting_with_plus_counted(repo):
    # A content line that begins with `+` becomes `++...` in the diff; it must still be
    # counted as added (git numstat is authoritative, not a `+++` prefix filter).
    (repo / "plus.py").write_text("+value = 1\nnormal = 2\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["plus.py"], timeout=30, max_bytes=200_000
    )
    assert "++value = 1" in res.text
    assert res.summary.lines_added == 2


def test_untracked_symlink_with_newline_in_name(repo):
    # A control-character path must be git-quoted in the header, not interpolated raw
    # (which could inject a fake `diff --git` line). git emits the quoted form.
    (repo / "a\nb").symlink_to("target")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["a\nb"], timeout=30, max_bytes=200_000
    )
    # git C-quotes the path (\n escaped to two chars), so the header stays one physical
    # line and the newline can't forge a second `diff --git` entry.
    assert '"a/a\\nb"' in res.text
    assert '\nb" "b/' not in res.text
    assert res.summary.files_changed == 1
    assert res.summary.lines_added == 1


def test_named_untracked_inaccessible_file_raises(repo):
    # An unreadable untracked file makes `--no-index` exit 1 with empty stdout; that is
    # a real error and must surface, not be silently dropped.
    import os

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("file permissions are not enforced for root")
    bad = repo / "locked.py"
    bad.write_text("x = 1\n")
    bad.chmod(0o000)
    try:
        with pytest.raises(RuntimeError):
            gitdiff.gather_diff(
                str(repo), "working_tree", paths=["locked.py"], timeout=30, max_bytes=200_000
            )
    finally:
        bad.chmod(0o644)


def test_branch_scope_ignores_untracked(repo):
    # The untracked-file augmentation is working_tree-only.
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "fresh.py").write_text("w = 9\n")
    res = gitdiff.gather_diff(
        str(repo), "branch", base=base, paths=["fresh.py"], timeout=30, max_bytes=200_000
    )
    assert "fresh.py" not in res.text
