"""Git diff gathering across scopes, validation, and bounding."""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
from pathlib import Path

import pytest

from codex_in_claude._core import gitdiff, streamcap
from codex_in_claude._core.redaction import DiffRedactor
from conftest import run_git


def _git(cwd, *args):
    run_git(cwd, *args)


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


@pytest.mark.parametrize("omitted", [None, ""])
def test_branch_omitted_base_message_says_omitted(repo, omitted):
    # An omitted base (None or "") reads "omitted" and must not leak its Python repr (F6).
    with pytest.raises(gitdiff.InvalidBaseError, match="omitted") as exc:
        gitdiff.gather_diff(str(repo), "branch", base=omitted, timeout=30, max_bytes=200_000)
    assert repr(omitted) not in str(exc.value)


def test_branch_invalid_base_message_keeps_value(repo):
    # A present-but-invalid base still surfaces its repr so stray whitespace/quoting shows.
    with pytest.raises(gitdiff.InvalidBaseError, match="-bad"):
        gitdiff.gather_diff(str(repo), "branch", base="-bad", timeout=30, max_bytes=200_000)


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


@pytest.mark.parametrize("omitted", [None, ""])
def test_commit_omitted_message_says_omitted(repo, omitted):
    # An omitted commit (None or "") reads "omitted" and must not leak its Python repr (F6).
    with pytest.raises(gitdiff.InvalidCommitError, match="omitted") as exc:
        gitdiff.gather_diff(str(repo), "commit", commit=omitted, timeout=30, max_bytes=200_000)
    assert repr(omitted) not in str(exc.value)


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


# --- egress-free untracked inventory (coverage signal, #319) -----------------
def test_count_untracked_counts_new_files(repo):
    # A count-only disclosure of the blind spot: no file contents are read or sent.
    (repo / "a.py").write_text("a = 1\n")
    (repo / "b.py").write_text("b = 2\n")
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 2


def test_count_untracked_zero_on_clean_tree(repo):
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 0


def test_count_untracked_ignores_tracked_modifications(repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # tracked, modified
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 0


def test_count_untracked_excludes_gitignored(repo):
    (repo / ".gitignore").write_text("ig.py\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore")
    (repo / "ig.py").write_text("x = 1\n")  # ignored -> not counted
    (repo / "keep.py").write_text("y = 1\n")  # untracked -> counted
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 1


def test_count_untracked_respects_pathspec(repo):
    (repo / "sub").mkdir()
    (repo / "sub" / "inside.py").write_text("i = 1\n")
    (repo / "outside.py").write_text("o = 1\n")
    assert gitdiff.count_untracked(str(repo), ["sub"], timeout=30) == 1


def test_count_untracked_streams_large_listing_exactly(repo):
    # A listing larger than one read chunk must be counted exactly by the bounded
    # streaming reader — never materialized whole (#322 F1).
    n = 4000
    for i in range(n):
        (repo / f"pkg_{i:05d}_module.py").write_text("x = 1\n")
    assert gitdiff.count_untracked(str(repo), None, timeout=60) == n


def test_count_untracked_newline_in_filename_counts_once(repo):
    # NUL-delimited: a filename containing a newline is ONE entry, not two. A line-count
    # would over-report here and break the coverage arithmetic (detected=included+omitted).
    (repo / "we\nird.py").write_text("w = 1\n")
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 1


def test_count_untracked_does_not_run_repo_fsmonitor(repo, tmp_path):
    # A working-tree review of an untrusted repo must not execute a repo-configured
    # fsmonitor program in the server process, outside the Codex sandbox.
    sentinel = tmp_path / "fsmonitor_ran"
    hook = tmp_path / "evil-fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {sentinel}\n")
    hook.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(hook))
    (repo / "new.py").write_text("n = 1\n")
    gitdiff.count_untracked(str(repo), None, timeout=30)
    assert not sentinel.exists(), "repo-configured fsmonitor program must not execute"


# --- working_tree snapshot-consistency detection (#336) ----------------------
def test_working_tree_detects_mutation_during_gather(repo, monkeypatch):
    # An edit landing BETWEEN the summary and the transmitted diff is a genuine inconsistency:
    # the summary describes the pre-edit tree while the diff Codex reviewed describes the
    # post-edit tree. The detector must flag it so coverage is not reported `complete`.
    orig_stream = gitdiff._stream_redacted_diff

    def mutating_stream(cwd, diff_args, timeout, acc, **kw):
        # After _summary has read the (clean) tree, a second session edits it before the diff.
        (repo / "calc.py").write_text("def add(a, b):\n    return a * b\n")
        return orig_stream(cwd, diff_args, timeout, acc, **kw)

    monkeypatch.setattr(gitdiff, "_stream_redacted_diff", mutating_stream)
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert res.tree_changed_during_gather is True


def test_concurrent_staging_detected_as_modification(repo, monkeypatch):
    # A concurrent `git add` mid-gather does not change the reviewed `git diff HEAD` patch, but
    # it IS a real concurrent modification of the repo. The porcelain token trips (` M` -> `M `),
    # and we disclose it conservatively rather than silently claim a consistent snapshot (#336
    # Codex review). This pins that intended behavior — not a false positive under the tool's
    # best-effort "modified during gather" semantics.
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # already dirty
    orig_stream = gitdiff._stream_redacted_diff

    def staging_stream(cwd, diff_args, timeout, acc, **kw):
        _git(repo, "add", "calc.py")  # index-only transition; diff-vs-HEAD is unchanged
        return orig_stream(cwd, diff_args, timeout, acc, **kw)

    monkeypatch.setattr(gitdiff, "_stream_redacted_diff", staging_stream)
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert res.tree_changed_during_gather is True


def test_content_reedit_of_already_dirty_file_is_a_known_miss(repo, monkeypatch):
    # HONEST LIMITATION (Codex review #336): the token is a porcelain CLASSIFICATION, not a
    # content hash. Re-editing a file that is ALREADY modified leaves its status code ` M`
    # unchanged, so this concurrent edit is NOT detected. This pins the documented gap so a
    # future change that silently "fixes" it (e.g. switching to a content hash) is noticed.
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # already dirty
    orig_summary = gitdiff._summary

    def mutating_summary(cwd, diff_args, timeout):
        (repo / "calc.py").write_text("def add(a, b):\n    return a * b\n")  # still ` M`
        return orig_summary(cwd, diff_args, timeout)

    monkeypatch.setattr(gitdiff, "_summary", mutating_summary)
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert res.tree_changed_during_gather is False  # undetected, by design


def test_working_tree_clean_gather_reports_stable(repo):
    # Negative control: an untouched tree during gather is not flagged as changed.
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert res.tree_changed_during_gather is False


def test_out_of_scope_mutation_does_not_trip_detection(repo, monkeypatch):
    # The detector is scoped to the review's pathspec, exactly like the gathered diff — an
    # edit outside `paths` must not downgrade a legitimate, consistent in-scope review.
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "other.py").write_text("o = 1\n")
    _git(repo, "add", "other.py")
    _git(repo, "commit", "-qm", "other")
    orig_summary = gitdiff._summary

    def mutating_summary(cwd, diff_args, timeout):
        (repo / "other.py").write_text("o = 2\n")  # outside paths=["calc.py"]
        return orig_summary(cwd, diff_args, timeout)

    monkeypatch.setattr(gitdiff, "_summary", mutating_summary)
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["calc.py"], timeout=30, max_bytes=200_000
    )
    assert res.tree_changed_during_gather is False


def test_nested_untracked_added_mid_gather_trips_detection(repo, monkeypatch):
    # A file appearing inside an ALREADY-untracked directory changes the file set that
    # `ls-files --others` enumerates. The token must use --untracked-files=all so it does
    # not collapse the directory to one entry and miss the addition (Codex review #336).
    (repo / "sub").mkdir()
    (repo / "sub" / "a.txt").write_text("a\n")  # `sub/` is untracked before gather
    orig_summary = gitdiff._summary

    def mutating_summary(cwd, diff_args, timeout):
        (repo / "sub" / "b.txt").write_text("b\n")  # new file in the untracked dir
        return orig_summary(cwd, diff_args, timeout)

    monkeypatch.setattr(gitdiff, "_summary", mutating_summary)
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert res.tree_changed_during_gather is True


def test_branch_scope_skips_snapshot_detection(repo):
    # branch/commit scopes read immutable objects; the flag stays False and no status is run.
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b + 1\n")
    res = gitdiff.gather_diff(str(repo), "branch", base=base, timeout=30, max_bytes=200_000)
    assert res.tree_changed_during_gather is False


def test_working_tree_pins_head_across_concurrent_commit(repo, monkeypatch):
    # #355: working_tree diffs against HEAD. If a concurrent commit advances HEAD BETWEEN the
    # summary and the transmitted diff, a symbolic `git diff HEAD` would make the two describe
    # different base objects: the summary records one changed file, but by the time the diff
    # runs the change is committed, so `git diff HEAD` (now the new commit) shows NOTHING.
    # Pinning HEAD to a resolved object ID once keeps both consistent.
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # working-tree change
    orig_summary = gitdiff._summary

    def committing_summary(cwd, diff_args, timeout):
        s = orig_summary(cwd, diff_args, timeout)
        _git(repo, "commit", "-qam", "concurrent")  # advances HEAD after the summary
        return s

    monkeypatch.setattr(gitdiff, "_summary", committing_summary)
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert res.summary.files_changed == 1
    assert "return a - b" in res.text  # diff still describes the pinned pre-commit HEAD


def test_branch_pins_head_across_concurrent_commit(repo, monkeypatch):
    # #355: a branch diff is `base...HEAD`. A concurrent commit advancing HEAD between the summary
    # and the diff would, with a symbolic `HEAD`, splice the later commit's files into the reviewed
    # patch while the summary counted only the earlier ones. Pinning both ends to resolved object
    # IDs keeps the summary and the diff describing the same range.
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b + 1\n")
    _git(repo, "commit", "-qam", "h1")  # HEAD = H1; base...HEAD covers only calc.py
    orig_summary = gitdiff._summary

    def committing_summary(cwd, diff_args, timeout):
        s = orig_summary(cwd, diff_args, timeout)
        (repo / "extra.py").write_text("x = 1\n")
        _git(repo, "add", "extra.py")
        _git(repo, "commit", "-qm", "h2")  # advances HEAD to H2, adding extra.py
        return s

    monkeypatch.setattr(gitdiff, "_summary", committing_summary)
    res = gitdiff.gather_diff(str(repo), "branch", base=base, timeout=30, max_bytes=200_000)
    assert res.summary.files_changed == 1
    assert "extra.py" not in res.text  # H2's file must not appear in the H1-pinned diff


def test_commit_pins_symbolic_ref_across_concurrent_move(repo, monkeypatch):
    # #355: commit scope accepts a symbolic ref. If that ref is force-moved to a different commit
    # between the summary and the `git show` diff, a symbolic ref would make the two describe
    # different commits. Pinning the ref to its resolved object ID once keeps them consistent.
    _git(repo, "branch", "target")  # target -> C1 (the init commit that adds calc.py)
    orig_summary = gitdiff._summary

    def moving_summary(cwd, diff_args, timeout):
        s = orig_summary(cwd, diff_args, timeout)
        (repo / "newfile.py").write_text("y = 2\n")
        _git(repo, "add", "newfile.py")
        _git(repo, "commit", "-qm", "c2")
        _git(repo, "branch", "-f", "target", "HEAD")  # target -> C2 (adds newfile.py)
        return s

    monkeypatch.setattr(gitdiff, "_summary", moving_summary)
    res = gitdiff.gather_diff(str(repo), "commit", commit="target", timeout=30, max_bytes=200_000)
    assert "calc.py" in res.text  # pinned to C1
    assert "newfile.py" not in res.text  # C2's file must not appear


def test_state_token_streams_large_listing(repo):
    # The token capture is bounded like count_untracked: a listing far larger than one read
    # chunk must digest without materializing the whole status output (#336 / #322 F1).
    for i in range(4000):
        (repo / f"pkg_{i:05d}_module.py").write_text("x = 1\n")
    tok = gitdiff._worktree_state_token(str(repo), None, [], timeout=60)
    assert tok == gitdiff._worktree_state_token(str(repo), None, [], timeout=60)  # stable


def test_state_token_non_utf8_path_digests(repo):
    # A non-UTF-8 filename must fold into the digest via surrogateescape, not raise.
    (repo / "blob.bin").write_bytes(b"\xff\xfe raw\n")
    tok = gitdiff._worktree_state_token(str(repo), None, [], timeout=30)
    assert isinstance(tok, str) and tok


def test_state_token_does_not_run_repo_fsmonitor(repo, tmp_path):
    # `git status` for the token must not execute a repo-configured fsmonitor program in the
    # server process (outside the Codex sandbox), just like the untracked enumeration.
    sentinel = tmp_path / "fsmonitor_ran"
    hook = tmp_path / "evil-fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {sentinel}\n")
    hook.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(hook))
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    gitdiff._worktree_state_token(str(repo), None, [], timeout=30)
    assert not sentinel.exists(), "repo-configured fsmonitor program must not execute"


# --- untracked policy + coverage counts on DiffResult (#319) -----------------
def test_gather_explicit_only_default_omits_untracked_but_counts_it(repo):
    # The bug's shape: an untracked-only tree. Default policy omits it (preserving #74's
    # egress posture) but now DISCLOSES it via counts instead of silently hiding it.
    (repo / "new.py").write_text("n = 1\n")
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert "new.py" not in res.text
    assert res.untracked_detected == 1
    assert res.untracked_included == 0


def test_gather_explicit_only_named_untracked_is_included_and_counted(repo):
    (repo / "new.py").write_text("n = 1\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["new.py"], timeout=30, max_bytes=200_000
    )
    assert "new.py" in res.text
    # detected is scoped to the pathspec; the named file is both detected and included.
    assert res.untracked_detected == 1
    assert res.untracked_included == 1


def test_gather_include_gathers_all_untracked_without_paths(repo):
    (repo / "new.py").write_text("n = 1\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", untracked="include", timeout=30, max_bytes=200_000
    )
    assert "new.py" in res.text  # opt-in egress
    assert res.untracked_detected == 1
    assert res.untracked_included == 1


def test_gather_exclude_never_includes_even_named_untracked(repo):
    (repo / "new.py").write_text("n = 1\n")
    res = gitdiff.gather_diff(
        str(repo),
        "working_tree",
        paths=["new.py"],
        untracked="exclude",
        timeout=30,
        max_bytes=200_000,
    )
    assert "new.py" not in res.text
    assert res.untracked_detected == 1
    assert res.untracked_included == 0


def test_gather_untracked_counts_none_for_commit_scope(repo):
    # Untracked files are irrelevant to a committed-range review; counts are N/A, not 0.
    res = gitdiff.gather_diff(str(repo), "commit", commit="HEAD", timeout=30, max_bytes=200_000)
    assert res.untracked_detected is None
    assert res.untracked_included == 0


# --- F2: invalid untracked policy fails loudly, not silently as exclude -------
@pytest.mark.parametrize("bad", ["bogus", "Include", "", None, 3])
def test_gather_invalid_untracked_policy_raises(repo, bad):
    # A mistyped policy from a raw worker spec or a direct _core caller must be an
    # error, not a silently-degraded partial review that looks like exclude.
    with pytest.raises(gitdiff.InvalidUntrackedError):
        gitdiff.gather_diff(str(repo), "working_tree", untracked=bad, timeout=30, max_bytes=200_000)


# --- F3: coverage counts come from ONE enumeration (no TOCTOU) ----------------
def test_gather_gathering_path_does_not_separately_count(repo, monkeypatch):
    # When untracked files are gathered, `detected` must be derived from that same
    # enumeration (detected == included), NOT a second `count_untracked` call whose
    # result could disagree under concurrent working-tree mutation.
    (repo / "a.py").write_text("a = 1\n")
    calls = {"n": 0}
    real = gitdiff.count_untracked

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(gitdiff, "count_untracked", spy)
    res = gitdiff.gather_diff(
        str(repo), "working_tree", untracked="include", timeout=30, max_bytes=200_000
    )
    assert res.untracked_included == 1
    assert res.untracked_detected == res.untracked_included  # invariant, no clamp
    assert calls["n"] == 0  # no separate enumeration -> no race window


def test_gather_non_gathering_path_counts_exactly_once(repo, monkeypatch):
    # The default (no gather) still needs one count to disclose the omitted set.
    (repo / "a.py").write_text("a = 1\n")
    calls = {"n": 0}
    real = gitdiff.count_untracked

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(gitdiff, "count_untracked", spy)
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert res.untracked_detected == 1
    assert res.untracked_included == 0
    assert calls["n"] == 1


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
        # Match production hashing (`hash-object --no-filters`) so the expected SHA is
        # independent of any global gitattributes/clean filter the host has configured.
        ["git", "hash-object", "--no-filters", "leak.txt"],
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


def test_quotepath_quoting_forced_despite_user_config(repo):
    # `core.quotepath` governs whether high-bit (non-ASCII) path bytes are C-quoted.
    # A user setting `core.quotepath=false` would otherwise emit raw UTF-8 bytes in the
    # `diff --git` header, making the reviewed text depend on caller config. We force
    # `-c core.quotepath=true`, so quoting is deterministic regardless of that config.
    _git(repo, "config", "core.quotepath", "false")
    (repo / "café.py").write_text("v = 1\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["café.py"], timeout=30, max_bytes=200_000
    )
    # Forced quoting renders the non-ASCII byte as an escaped octal sequence, never raw.
    assert "café.py" not in res.text
    assert "caf\\303\\251.py" in res.text
    assert res.summary.files_changed == 1


def test_named_untracked_non_utf8_content_roundtrips(repo):
    # An untracked file with non-UTF-8 bytes must not raise UnicodeDecodeError while
    # gathering: surrogateescape lets git's output round-trip and the diff is bounded.
    (repo / "blob.bin").write_bytes(b"\xff\xfe\x00raw\x80bytes\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["blob.bin"], timeout=30, max_bytes=200_000
    )
    assert "blob.bin" in res.text
    assert res.summary.files_changed == 1


def test_named_untracked_inaccessible_file_raises(repo):
    # An unreadable untracked file makes `--no-index` exit 1 with empty stdout; that is
    # a real error and must surface, not be silently dropped.

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


def test_large_diff_is_memory_bounded(repo, monkeypatch):
    # A diff far larger than the cap: text is bounded to whole lines <= max_bytes,
    # but diff_bytes still reports the exact full redacted size.
    big = "def f():\n" + "\n".join(f"    v{i} = {i}" for i in range(5000)) + "\n"
    (repo / "calc.py").write_text(big)

    real_iter = streamcap.iter_bounded_lines
    seen_chunked = {"used": False}

    def spy(stream, max_line_bytes, chunk_size=65536, *, sep="\n"):
        seen_chunked["used"] = True
        yield from real_iter(stream, max_line_bytes, chunk_size, sep=sep)

    monkeypatch.setattr(gitdiff.streamcap, "iter_bounded_lines", spy)
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=500)
    assert res.truncated
    assert len(res.text.encode("utf-8")) <= 500  # bounded, line-aligned
    assert res.diff_bytes > 500  # exact full size still reported
    assert seen_chunked["used"]  # went through the bounded reader


def test_diff_bytes_exact_count(repo):
    """diff_bytes must equal len("\n".join(all_redacted_lines).encode("utf-8","replace"))
    for a small deterministic diff — pinning the exact-count invariant."""
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")

    # Collect the raw git diff as the function would see it (same flags/env).
    raw = subprocess.run(
        ["git", "-c", "core.quotepath=true", "diff"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        env={
            "LC_ALL": "C",
            "LANG": "C",
            "PATH": gitdiff._path(),  # type: ignore[attr-defined]
        },
        check=True,
    ).stdout

    # Feed every logical line through DiffRedactor to get the full redacted sequence.
    redactor = DiffRedactor()
    all_redacted: list[str] = []
    for physical in raw.splitlines():
        for logical in physical.splitlines() or [""]:
            all_redacted.extend(redactor.feed(logical))

    expected_bytes = len("\n".join(all_redacted).encode("utf-8", "replace"))

    # gather_diff with a huge budget so nothing is truncated.
    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=200_000)
    assert not res.truncated
    assert res.diff_bytes == expected_bytes


def test_stream_timeout_watchdog_kills_stalled_process(tmp_path, monkeypatch):
    """A process that opens stdout, writes a line, then stalls without closing
    stdout must be killed by the watchdog so _stream_redacted_diff raises
    RuntimeError('... timed out ...') promptly — well within the 30-second stall.

    RED (pre-fix): the function blocks indefinitely because proc.wait(timeout=…)
    only runs AFTER stdout drains to EOF, which never happens.
    GREEN (post-fix): a threading.Timer fires, kills the process group, which
    closes the pipe, unblocks the drain loop, and the timed_out flag triggers
    the RuntimeError.
    """
    real_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        # Spawn a real child that writes one line, flushes, then sleeps 30s
        # without closing stdout — simulating a mid-stream git stall.
        stall_cmd = [
            sys.executable,
            "-c",
            (
                "import sys, time; "
                "sys.stdout.write('partial\\n'); "
                "sys.stdout.flush(); "
                "time.sleep(30)"
            ),
        ]
        return real_popen(stall_cmd, **kwargs)

    monkeypatch.setattr(gitdiff.subprocess, "Popen", fake_popen)

    acc = gitdiff._BoundedDiffAccumulator(1000)  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="timed out"):
        gitdiff._stream_redacted_diff(str(tmp_path), ["diff"], timeout=1, acc=acc)  # type: ignore[attr-defined]


def test_stream_timeout_watchdog_kills_descendant_holding_pipe(tmp_path, monkeypatch):
    """F1 descendant-hang regression: a fake git parent that exits immediately
    after spawning a grandchild which inherits and holds the stdout pipe open
    must still time out promptly.

    RED (pre-fix, getpgid): os.getpgid(proc.pid) raises ESRCH on a zombie
    (macOS behaviour) → suppressed → no kill → the grandchild keeps holding the
    pipe → the iter_bounded_lines loop never reaches EOF → the function hangs
    for ~10 s (the grandchild's sleep duration).

    GREEN (post-fix): os.killpg(proc.pid, SIGKILL) uses proc.pid directly as
    the pgid (valid because start_new_session=True makes the process its own
    group leader).  This kills the still-live grandchild even after the leader
    is a zombie, closing the pipe and unblocking the drain loop within the
    configured timeout.
    """
    import time

    real_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        # Parent exits immediately; grandchild (inheriting fd 1 = the pipe
        # write-end) sleeps 10 s, simulating a git helper that outlives git.
        parent_cmd = [
            sys.executable,
            "-c",
            (
                "import subprocess, sys, time; "
                "subprocess.Popen(["
                "sys.executable, '-c', 'import time; time.sleep(10)'"
                "]); "
                "sys.exit(0)"
            ),
        ]
        return real_popen(parent_cmd, **kwargs)

    monkeypatch.setattr(gitdiff.subprocess, "Popen", fake_popen)

    acc = gitdiff._BoundedDiffAccumulator(1000)  # type: ignore[attr-defined]
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        gitdiff._stream_redacted_diff(str(tmp_path), ["diff"], timeout=2, acc=acc)  # type: ignore[attr-defined]
    elapsed = time.monotonic() - start
    assert elapsed < 7, (
        f"expected return well before grandchild's 10 s lifetime; elapsed={elapsed:.1f}s"
    )


# ---------------------------------------------------------------------------
# F3: long single line breaks diff_bytes + boundary redaction
# ---------------------------------------------------------------------------


def test_f3_long_line_diff_bytes_exact(repo, monkeypatch):
    """F3(a): a single diff line longer than max_bytes must still be counted exactly
    in diff_bytes. Before fix: max_line_bytes == max_bytes, so iter_bounded_lines
    truncates the long line before the accumulator sees it; diff_bytes undercounts.
    After fix: max_line_bytes == 8 MiB, so the full line reaches the accumulator."""
    # Content line: "+" + "a"*300 = 301 chars > max_bytes=200.
    (repo / "calc.py").write_text("a" * 300 + "\n")
    max_bytes = 200

    # Patch iter_bounded_lines with a small chunk_size (90) so the 301-char line spans
    # multiple chunks and the per-line cap is actually enforced.
    real_iter = streamcap.iter_bounded_lines

    def small_chunk_iter(stream, max_line_bytes, chunk_size=65536, *, sep="\n"):
        yield from real_iter(stream, max_line_bytes, chunk_size=90, sep=sep)

    monkeypatch.setattr(gitdiff.streamcap, "iter_bounded_lines", small_chunk_iter)

    # Compute expected diff_bytes by feeding the full git diff through DiffRedactor.
    import subprocess as _subprocess

    raw = _subprocess.run(
        ["git", "-c", "core.quotepath=true", "diff", "--end-of-options", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        env={"LC_ALL": "C", "LANG": "C", "PATH": gitdiff._path()},  # type: ignore[attr-defined]
        check=True,
    ).stdout
    redactor = DiffRedactor()
    all_redacted: list[str] = []
    for physical in raw.splitlines():
        for logical in physical.splitlines() or [""]:
            all_redacted.extend(redactor.feed(logical))
    expected = len("\n".join(all_redacted).encode("utf-8", "replace"))

    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=max_bytes)
    assert res.truncated
    assert len(res.text.encode("utf-8")) <= max_bytes
    # After fix: exact count even for a line that exceeds max_bytes.
    assert res.diff_bytes == expected


def test_f3_long_line_secret_beyond_per_line_cap_redacted(repo, monkeypatch):
    """F3(b): a secret that starts beyond the old per-line cap (max_bytes) must be
    fully redacted. Before fix: iter_bounded_lines truncates the line before the
    secret's end; the regex needs 20+ chars but only a few are visible, so the secret
    is not detected and calc.py is NOT in redacted_paths. After fix: the full line
    reaches the redactor, the secret is matched and calc.py IS in redacted_paths."""
    max_bytes = 100
    # Diff line: "+" + "a"*100 + "sk-" + "A"*25 = 129 chars (no newline yet).
    # With chunk_size=60 and old max_line_bytes=100:
    #   chunk1 (60 chars, no newline): pending_bytes=60, not overflowing
    #   chunk2 (60 chars, no newline): pending_bytes=120 > 100 → overflow!
    #     truncated to 100 chars = "+" + "a"*99 (no "sk-" visible)
    #   chunk3 (10 chars, has "\n"): overflowing → yield truncated marker
    # The partial secret "sk-AAAA" (4 A's, needs 20+) never reaches the redactor:
    # calc.py NOT in redacted_paths (RED before fix).
    padding = "a" * 100
    secret = "sk-" + "A" * 25  # needs 20+ A's for sk-[A-Za-z0-9]{20,}
    (repo / "calc.py").write_text(padding + secret + "\n")

    real_iter = streamcap.iter_bounded_lines

    def small_chunk_iter(stream, max_line_bytes, chunk_size=65536, *, sep="\n"):
        yield from real_iter(stream, max_line_bytes, chunk_size=60, sep=sep)

    monkeypatch.setattr(gitdiff.streamcap, "iter_bounded_lines", small_chunk_iter)

    res = gitdiff.gather_diff(str(repo), "working_tree", timeout=30, max_bytes=max_bytes)
    assert res.truncated
    # After fix: full line reaches the redactor; secret is found and fully redacted.
    assert "calc.py" in res.redacted_paths


# ---------------------------------------------------------------------------
# F1b: explicitly-named untracked file materialized whole (streaming fix)
# ---------------------------------------------------------------------------


def test_stream_timeout_watchdog_closes_fds_but_stays_alive(tmp_path, monkeypatch):
    """Regression for #155: a fake git that closes its stdout/stderr file descriptors
    but stays alive (sleeps 30s) must cause _stream_redacted_diff to raise RuntimeError
    matching 'timed out' promptly — well within the 30-second sleep.

    RED (pre-fix): stdout drain sees EOF immediately; proc.wait() is unbounded —
    hangs ~30s until the child naturally exits.
    GREEN (post-fix): the remaining-deadline bounded wait expires; the group is killed;
    raises RuntimeError within a few seconds.
    """
    import time

    real_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        close_cmd = [
            sys.executable,
            "-c",
            "import os, time; os.close(1); os.close(2); time.sleep(30)",
        ]
        return real_popen(close_cmd, **kwargs)

    monkeypatch.setattr(gitdiff.subprocess, "Popen", fake_popen)

    acc = gitdiff._BoundedDiffAccumulator(1000)  # type: ignore[attr-defined]
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        gitdiff._stream_redacted_diff(str(tmp_path), ["diff"], timeout=2, acc=acc)  # type: ignore[attr-defined]
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"expected return well before the 30s sleep; elapsed={elapsed:.1f}s"


def test_f1b_large_untracked_uses_bounded_reader(repo, monkeypatch):
    """F1b: the untracked diff path must stream through iter_bounded_lines (bounded
    reader), not materialise the whole diff as a string first. Before fix: only the
    tracked-diff call uses the bounded reader (1 call); after fix: the untracked path
    also calls it (2 calls total when tracked diff is empty)."""
    # No tracked changes, only a large untracked file — so any bounded-reader call
    # beyond the first (empty tracked diff) must come from the untracked path.
    big = "x" * 5000  # large content
    (repo / "large_untracked.py").write_text(big + "\n")

    call_count: dict[str, int] = {"n": 0}
    real_iter = streamcap.iter_bounded_lines

    def counting_iter(stream, max_line_bytes, chunk_size=65536, *, sep="\n"):
        call_count["n"] += 1
        yield from real_iter(stream, max_line_bytes, chunk_size, sep=sep)

    monkeypatch.setattr(gitdiff.streamcap, "iter_bounded_lines", counting_iter)

    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["large_untracked.py"], timeout=30, max_bytes=500
    )
    assert res.truncated
    assert len(res.text.encode("utf-8")) <= 500
    # After fix: iter_bounded_lines called at least twice (tracked + untracked paths).
    assert call_count["n"] >= 2


def test_f1b_large_untracked_file_text_bounded(repo):
    """F1b: a large named untracked file produces bounded text and has diff_bytes > max_bytes.
    This verifies the accumulator correctly bounds output from the streaming untracked path."""
    (repo / "big_untracked.txt").write_text("y" * 2000 + "\n")
    max_bytes = 300
    res = gitdiff.gather_diff(
        str(repo), "working_tree", paths=["big_untracked.txt"], timeout=30, max_bytes=max_bytes
    )
    assert res.truncated
    assert len(res.text.encode("utf-8")) <= max_bytes
    assert res.diff_bytes > max_bytes
    assert res.summary.files_changed == 1


# ---------------------------------------------------------------------------
# #331: the untracked listing and numstat are streamed, not captured whole
# ---------------------------------------------------------------------------


def test_untracked_listing_and_numstat_stream_through_run_lines(repo, monkeypatch):
    """The untracked-gather path must route its `ls-files -z` listing and its `--numstat`
    through the bounded `gitproc.run_lines` runner — never `_git` (whole capture). RED
    before #331: both used `_git`, so only the excludes resolver reaches run_lines."""
    for i in range(5):
        (repo / f"u_{i}.py").write_text("x = 1\n")

    calls: list[tuple[list[str], str]] = []
    real = gitdiff.gitproc.run_lines

    def spy(argv, **kwargs):
        calls.append((list(argv), kwargs.get("sep", "\n")))
        return real(argv, **kwargs)

    monkeypatch.setattr(gitdiff.gitproc, "run_lines", spy)
    res = gitdiff.gather_diff(
        str(repo), "working_tree", untracked="include", timeout=30, max_bytes=200_000
    )
    assert res.summary.files_changed == 5
    # The NUL-delimited listing streamed with sep="\0"...
    assert any("ls-files" in argv and sep == "\0" for argv, sep in calls)
    # ...and the numstat streamed (newline-delimited).
    assert any("--numstat" in argv for argv, _ in calls)


def test_untracked_index_build_bounded_by_deadline(repo, monkeypatch):
    """#331 / Codex HIGH: the composed producer+consumer phase (ls-files streamed into
    per-path hashing) must be bounded by ONE deadline, not merely the producer's watchdog.
    With slow per-path work and a short timeout, the whole call must bail near the timeout
    instead of grinding through every buffered path. RED before the deadline check: the
    consumer runs to completion (~files * sleep) before run_lines reports the timeout."""
    import time as _time

    for i in range(20):
        (repo / f"slow_{i:02d}.py").write_text("x = 1\n")

    real_git = gitdiff._git
    budgets: list[tuple[str, float]] = []

    def slow_git(cwd, args, timeout, extra_env=None, stdin=None):
        # Simulate slow per-path index work; leave enumeration/rev-parse untouched.
        if args and args[0] in ("hash-object", "update-index"):
            budgets.append((args[0], timeout))
            _time.sleep(0.3)
        return real_git(cwd, args, timeout, extra_env=extra_env, stdin=stdin)

    monkeypatch.setattr(gitdiff, "_git", slow_git)

    start = _time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        gitdiff.gather_diff(
            str(repo), "working_tree", untracked="include", timeout=1, max_bytes=200_000
        )
    elapsed = _time.monotonic() - start
    # 20 files * 0.3s = 6s unbounded; the shared deadline must cut it near the 1s timeout.
    assert elapsed < 2.5, f"phase not deadline-bounded: {elapsed:.1f}s"
    # The budget must be recomputed per nested call (not a stale per-path value reused for both
    # hash-object and update-index), so every git call receives strictly less time than the last.
    passed = [t for _, t in budgets]
    assert len(passed) >= 2, f"need >=2 nested calls to prove recomputation, got {budgets}"
    assert all(a > b for a, b in itertools.pairwise(passed)), (
        f"budgets not strictly shrinking (a stale per-path timeout was reused): {budgets}"
    )


def test_untracked_reject_ceiling_below_reader_cap():
    """#331 / fable review: the oversized-path reject is only sound while the reader's byte cap
    stays above the path ceiling — a truncated record is ~reader-cap bytes, so it must exceed the
    ceiling to be rejected. Pin the invariant so a future cap change can't silently reopen the
    corrupt-name hole with every other test still green."""
    assert gitdiff._UNTRACKED_RECORD_MAX > gitdiff._MAX_UNTRACKED_PATH_BYTES + 64


def test_untracked_newline_in_regular_filename_gathered_once(repo):
    """A regular (non-symlink) untracked file whose name contains a newline is gathered as
    ONE file — the NUL-delimited listing must keep the embedded newline intact (#331)."""
    (repo / "we\nird.py").write_text("w = 1\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", untracked="include", timeout=30, max_bytes=200_000
    )
    assert res.summary.files_changed == 1
    assert res.untracked_included == 1
    # git C-quotes the newline in the diff header (the \n renders as two chars \ + n), so the
    # header stays one physical line and a raw newline can't forge a second `diff --git` entry.
    assert '"a/we\\nird.py"' in res.text
    assert "we\nird.py" not in res.text  # the raw (unquoted) newline never reaches the output


def test_untracked_carriage_return_in_regular_filename_gathered_once(repo):
    """An untracked file whose name contains a raw carriage return is gathered as ONE file.
    Universal-newline text mode rewrites \\r -> \\n before the NUL-splitter sees it, so the
    throwaway-index build would stat a nonexistent `we\\nird.py` and raise an unstructured
    FileNotFoundError instead of gathering the file (#353)."""
    (repo / "we\rird.py").write_text("w = 1\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", untracked="include", timeout=30, max_bytes=200_000
    )
    assert res.summary.files_changed == 1
    assert res.untracked_included == 1
    # git C-quotes the carriage return in the diff header (\r renders as two chars \ + r).
    assert '"a/we\\rird.py"' in res.text
    assert "we\rird.py" not in res.text  # the raw (unquoted) CR never reaches the output


def test_untracked_cr_and_lf_named_files_both_gathered(repo):
    """Two DISTINCT untracked files "we\\rird.py" and "we\\nird.py" must both be gathered.
    Under \\r -> \\n translation the CR record collapses onto the LF name: the LF file is
    hashed twice and the CR file is silently omitted while still counted as included, a quiet
    detected==included coverage-contract violation the #322 F3 invariant cannot catch (#353)."""
    (repo / "we\rird.py").write_text("r = 1\n")
    (repo / "we\nird.py").write_text("n = 1\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", untracked="include", timeout=30, max_bytes=200_000
    )
    assert res.summary.files_changed == 2
    assert res.untracked_included == 2
    assert '"a/we\\rird.py"' in res.text
    assert '"a/we\\nird.py"' in res.text


def test_untracked_vanished_path_raises_structured_error(repo, monkeypatch):
    """A concurrently-deleted untracked file — enumerated by ls-files, gone before the index
    build stats it — must surface as a RuntimeError (in orchestration.GITDIFF_EXCEPTIONS, so it
    maps to a structured envelope), not a raw FileNotFoundError escaping the gather (#353)."""
    (repo / "fresh.py").write_text("x = 1\n")
    real_stat = Path.stat

    def vanish(self, *args, **kwargs):
        if self.name == "fresh.py":
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanish)
    with pytest.raises(RuntimeError):
        gitdiff.gather_diff(
            str(repo), "working_tree", untracked="include", timeout=30, max_bytes=200_000
        )


def test_untracked_oversized_path_record_fails_loudly(repo, monkeypatch):
    """#331 / Codex MEDIUM: an over-long untracked path (a genuinely absurd one, or one the
    bounded reader truncated) must be rejected loudly rather than hashed under a corrupt name.
    A real path is < PATH_MAX, so force the ceiling low to exercise the reject."""
    (repo / "an_untracked_file.py").write_text("x = 1\n")
    monkeypatch.setattr(gitdiff, "_MAX_UNTRACKED_PATH_BYTES", 4)  # any real path now exceeds it
    with pytest.raises(RuntimeError, match="exceeds"):
        gitdiff.gather_diff(
            str(repo), "working_tree", untracked="include", timeout=30, max_bytes=200_000
        )


def test_untracked_filename_ending_in_truncation_marker_text_is_gathered(repo):
    """#331 / Codex MEDIUM regression: a legitimate (short, non-truncated) filename that ends
    in the reader's truncation-marker text must still be gathered — the reject is a pure length
    test, not a content sniff, so it cannot false-positive this name."""
    name = f"report{streamcap._LINE_TRUNC_SENTINEL}.txt"
    (repo / name).write_text("ok = 1\n")
    res = gitdiff.gather_diff(
        str(repo), "working_tree", untracked="include", timeout=30, max_bytes=200_000
    )
    assert res.summary.files_changed == 1
    assert res.untracked_included == 1


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (gitdiff.gitproc.GitBinaryNotFound("nope"), gitdiff.GitUnavailableError),
        (gitdiff.gitproc.GitStreamTimeout("slow"), RuntimeError),
        (
            gitdiff.gitproc.GitStreamFailed(128, "fatal: not a git repository"),
            gitdiff.NotAGitRepoError,
        ),
        (gitdiff.gitproc.GitStreamFailed(1, "some other failure"), RuntimeError),
    ],
)
def test_run_git_lines_maps_gitproc_errors_to_module_vocabulary(monkeypatch, raised, expected):
    """The streamed calls must fail with the same error types `_git` raises (#331), so a
    caller sees one vocabulary whether a git output was captured or streamed."""

    def boom(*_args, **_kwargs):
        raise raised

    monkeypatch.setattr(gitdiff.gitproc, "run_lines", boom)
    with pytest.raises(expected):
        gitdiff._run_git_lines(
            ["ls-files"],
            cwd=".",
            env={},
            timeout=5,
            sep="\n",
            consume=list,
        )


def test_run_git_lines_timeout_message_names_the_git_args(monkeypatch):
    """The streamed timeout message names the actual git args, matching `_git` — not a short
    label — so a streamed and a captured call are equally debuggable (Copilot #352)."""

    def boom(*_args, **_kwargs):
        raise gitdiff.gitproc.GitStreamTimeout("slow")

    monkeypatch.setattr(gitdiff.gitproc, "run_lines", boom)
    with pytest.raises(RuntimeError, match=r"git ls-files --others .* timed out after 5s"):
        gitdiff._run_git_lines(
            ["ls-files", "--others", "-z"],
            cwd=".",
            env={},
            timeout=5,
            sep="\0",
            consume=list,
        )


# ---------------------------------------------------------------------------
# Fix 2: stderr-only descendant holds the pipe past the configured timeout
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="process-group kill is POSIX-only")
def test_stream_timeout_stderr_only_descendant_times_out(tmp_path, monkeypatch):
    """Fix 2 regression: a fake git parent that exits immediately after spawning a
    grandchild inheriting ONLY stderr (stdout closed) must raise RuntimeError
    matching 'timed out' promptly rather than returning success after 5 s.

    RED (pre-fix): stdout drain sees EOF fast; proc.wait() returns immediately (parent
    exited); stderr_thread.join(timeout=5) waits 5 s but the grandchild still holds
    stderr; timed_out is never set; function returns normally — wrong.
    GREEN (post-fix): stderr drain is bounded by the remaining deadline; if
    stderr_thread is still alive, kill the group, set timed_out, raise RuntimeError.
    """
    import time

    real_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        # Parent: spawns grandchild with stderr=2 (write end of our stderr pipe) and
        # stdout=DEVNULL so it does NOT hold our stdout pipe open.  Parent then closes
        # its own stdout (releasing the stdout pipe) and exits.  Grandchild sleeps 30 s
        # holding stderr open, simulating a git descendant that outlives git itself.
        parent_code = (
            "import os, subprocess, sys; "
            "subprocess.Popen("
            "    [sys.executable, '-c', 'import time; time.sleep(30)'],"
            "    stdout=subprocess.DEVNULL, stderr=2, close_fds=True"
            "); "
            "os.close(1); "
            "sys.exit(0)"
        )
        return real_popen([sys.executable, "-c", parent_code], **kwargs)

    monkeypatch.setattr(gitdiff.subprocess, "Popen", fake_popen)

    acc = gitdiff._BoundedDiffAccumulator(1000)  # type: ignore[attr-defined]
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        gitdiff._stream_redacted_diff(str(tmp_path), ["diff"], timeout=2, acc=acc)  # type: ignore[attr-defined]
    elapsed = time.monotonic() - start
    # Must return well before the grandchild's 30 s sleep.
    assert elapsed < 10, (
        f"expected return well before grandchild's 30 s sleep; elapsed={elapsed:.1f}s"
    )


# ---------------------------------------------------------------------------
# Fix 4: os.killpg must be guarded for non-POSIX platforms
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not hasattr(os, "killpg"), reason="tests killpg fallback — irrelevant if killpg absent"
)
def test_stream_timeout_no_killpg_falls_back_to_proc_kill(tmp_path, monkeypatch):
    """Fix 4 regression: when os.killpg is unavailable (non-POSIX), gitdiff must not
    crash with AttributeError. Instead it must fall back to proc.kill() so the timeout
    path still terminates the process and raises RuntimeError('timed out').

    RED (pre-fix): os.killpg is called unconditionally; AttributeError is NOT caught by
    contextlib.suppress(ProcessLookupError, PermissionError), so the Timer thread crashes
    silently without killing the process — the process runs to completion (3 s) before
    the drain loop exits. proc.kill() is never called.
    GREEN (post-fix): hasattr(os, "killpg") guard → proc.kill() is called, process is
    killed promptly, RuntimeError raised within 1 s.
    """
    import time

    real_popen = subprocess.Popen
    kill_called: dict[str, int] = {"n": 0}

    class _ProcProxy:
        """Wrap a real Popen, counting proc.kill() calls."""

        def __init__(self, proc: subprocess.Popen) -> None:
            self._proc = proc

        def kill(self) -> None:
            kill_called["n"] += 1
            self._proc.kill()

        def __getattr__(self, name: str):  # type: ignore[override]
            return getattr(self._proc, name)

    def fake_popen(cmd, **kwargs):
        stall_cmd = [
            sys.executable,
            "-c",
            ("import sys, time; sys.stdout.write('line\\n'); sys.stdout.flush(); time.sleep(3)"),
        ]
        return _ProcProxy(real_popen(stall_cmd, **kwargs))

    # Shim: delegates everything to os except killpg (simulates non-POSIX platform).
    class _OsWithoutKillpg:
        def __getattr__(self, name: str):  # type: ignore[override]
            if name == "killpg":
                raise AttributeError(name)
            return getattr(os, name)

    monkeypatch.setattr(gitdiff, "os", _OsWithoutKillpg())
    monkeypatch.setattr(gitdiff.subprocess, "Popen", fake_popen)

    acc = gitdiff._BoundedDiffAccumulator(1000)  # type: ignore[attr-defined]
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        gitdiff._stream_redacted_diff(str(tmp_path), ["diff"], timeout=1, acc=acc)  # type: ignore[attr-defined]
    elapsed = time.monotonic() - start

    # After fix: killed promptly (< 3 s stall duration); before fix: hangs 3 s then passes
    # anyway (stall exits naturally, timed_out already set) — so timing distinguishes RED/GREEN.
    assert elapsed < 3, f"expected prompt kill via proc.kill(); elapsed={elapsed:.1f}s"
    # After fix: proc.kill() was called as the fallback; before fix: it was never called.
    assert kill_called["n"] > 0, "proc.kill() was not called as the killpg fallback"


# --- #330: honor the user's GLOBAL git excludes despite the HOME-stripped child env ---
#
# The enumeration children run with a replacement env that has no HOME/XDG, so git cannot
# find the user's global excludes on its own. The fix resolves the effective
# core.excludesFile from the SERVER's env and injects it via `-c core.excludesFile=...`.
# These tests neutralize the autouse global-excludes isolation (conftest) by overriding the
# specific env var the branch under test resolves from.


@pytest.fixture
def outside(tmp_path_factory):
    """A scratch directory OUTSIDE any `repo` fixture (which aliases `tmp_path`), for
    global config / ignore / XDG files that must not themselves become untracked entries
    in the repo under test."""
    return tmp_path_factory.mktemp("global-git-cfg")


def _write_global_excludesfile(monkeypatch, base, patterns, *, name="globalconfig"):
    """Point GIT_CONFIG_GLOBAL at a config (under ``base``) whose core.excludesFile lists
    ``patterns``. Returns the ignore-file path."""
    ignore = base / f"{name}_ignore"
    ignore.write_text("".join(f"{p}\n" for p in patterns))
    config = base / name
    config.write_text(f"[core]\n\texcludesFile = {ignore}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    return ignore


def test_global_excludes_flags_configured_value(tmp_path, monkeypatch):
    ignore = _write_global_excludesfile(monkeypatch, tmp_path, ["x"])
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    assert flags == ["-c", f"core.excludesFile={ignore}"]


def test_global_excludes_flags_unset_uses_xdg_default(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)  # key unset -> default location used
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    # Passed even though the default file does not exist: git no-ops on a missing file,
    # and this faithfully reproduces "unset -> default location" under the stripped child.
    assert flags == ["-c", f"core.excludesFile={xdg / 'git' / 'ignore'}"]


def test_global_excludes_flags_unset_uses_home_default_when_no_xdg(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    assert flags == ["-c", f"core.excludesFile={home / '.config' / 'git' / 'ignore'}"]


def test_global_excludes_flags_expands_tilde(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    ignore = home / "my_ignore"
    ignore.write_text("x\n")
    monkeypatch.setenv("HOME", str(home))
    config = tmp_path / "gc"
    config.write_text("[core]\n\texcludesFile = ~/my_ignore\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    # `git config --path` expands ~ in the SERVER (which has HOME); the stripped child
    # could not have expanded it itself.
    assert flags == ["-c", f"core.excludesFile={ignore}"]


def test_global_excludes_flags_relative_value_passed_through(tmp_path, monkeypatch):
    config = tmp_path / "gc"
    config.write_text("[core]\n\texcludesFile = sub/ignore\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    # A relative value is passed UNCHANGED: git resolves a relative excludesFile against the
    # process cwd, and the enumeration child runs with the SAME cwd, so it resolves
    # identically. Joining onto cwd here would double-prefix under a relative cwd (#330 review).
    assert flags == ["-c", "core.excludesFile=sub/ignore"]


def test_global_excludes_flags_rejects_oversized_value(tmp_path, monkeypatch):
    # Merged config includes the untrusted repo-local layer; an oversized core.excludesFile
    # must be rejected (fail-closed), not materialized and interpolated into git argv where
    # it could spike memory / hit E2BIG (#330 review).
    huge = "/" + "x" * 9000  # > _EXCLUDES_VALUE_MAX (8192)
    config = tmp_path / "gc"
    config.write_text(f"[core]\n\texcludesFile = {huge}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    with pytest.raises(RuntimeError, match="size cap"):
        gitdiff._global_excludes_flags(str(tmp_path), timeout=30)


def test_global_excludes_flags_rejects_oversized_multibyte_value(tmp_path, monkeypatch):
    # The cap is BYTE-based: a value whose CHARACTER count is under the cap but whose UTF-8
    # byte length exceeds it must still be rejected (#330 review). 3000 emoji ~= 3001 chars
    # but ~12001 bytes.
    value = "/" + "\U0001f600" * 3000
    config = tmp_path / "gc"
    config.write_text(f"[core]\n\texcludesFile = {value}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    with pytest.raises(RuntimeError, match="size cap"):
        gitdiff._global_excludes_flags(str(tmp_path), timeout=30)


def test_global_excludes_flags_sanitizes_inherited_git_dir(tmp_path, monkeypatch):
    # An inherited GIT_DIR must NOT redirect config resolution to another repo — otherwise
    # a stray GIT_DIR could inject a foreign repo's core.excludesFile at highest precedence
    # into the target review (issue #330 security review).
    other = tmp_path / "other"
    other.mkdir()
    run_git(other, "init", "-q")
    foreign_ignore = other / "foreign_ignore"
    foreign_ignore.write_text("x\n")
    run_git(other, "config", "--local", "core.excludesFile", str(foreign_ignore))
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))  # the trap
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    # Resolution stays anchored to the passed cwd (no local excludesFile there) -> falls to
    # the XDG default, NOT the foreign repo's setting.
    assert flags == ["-c", f"core.excludesFile={xdg / 'git' / 'ignore'}"]


def test_resolver_env_adds_only_global_config_allowlist_over_base(monkeypatch):
    # The resolver env must be the enumeration child's base env plus ONLY the global-config
    # source vars, so repo discovery and the system/local config view match the child and
    # nothing can divert resolution to a different repo/config (#330 review). Asserted on the
    # DELTA (resolver env minus base env) so it is robust to whatever the base env contains
    # (e.g. the conftest system-isolation seam) — the invariant is purely about what the
    # allowlist ADDS.
    diverting = [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_PARAMETERS",
        # System-config selection: the child reads git's compiled-in system config and honors
        # neither of these, so the resolver must not add them from the ambient environment
        # (else it could miss a system core.excludesFile the child applies).
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
    ]
    for var in diverting:
        monkeypatch.setenv(var, "diverting-value")
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/u/.config")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/home/u/.gitconfig")
    base = gitdiff._base_git_env()
    env = gitdiff._resolver_env()
    # Everything in the base env is present unchanged.
    for key, value in base.items():
        assert env[key] == value
    # The ONLY keys added or overridden on top of base are the global-config allowlist —
    # so no diverting var (whose ambient value is "diverting-value") leaks in.
    added = {k for k in env if k not in base or env[k] != base[k]}
    assert added == {"HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL"}
    assert env["HOME"] == "/home/u"
    assert env["XDG_CONFIG_HOME"] == "/home/u/.config"
    assert env["GIT_CONFIG_GLOBAL"] == "/home/u/.gitconfig"


def test_global_excludes_flags_ignores_git_config_system(tmp_path, monkeypatch):
    # GIT_CONFIG_SYSTEM relocates the SYSTEM config for git commands that honor it, but the
    # stripped ls-files child never does; the resolver must match, so it must ignore an
    # inherited GIT_CONFIG_SYSTEM (#330 review). A fake system config here must NOT win — the
    # resolver falls to the (isolated, empty) default location instead.
    sys_ignore = tmp_path / "sys_ignore"
    sys_ignore.write_text("x\n")
    sys_config = tmp_path / "sys_config"
    sys_config.write_text(f"[core]\n\texcludesFile = {sys_ignore}\n")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(sys_config))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)  # no global -> unset -> default
    default = Path(os.environ["XDG_CONFIG_HOME"]) / "git" / "ignore"
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    assert flags == ["-c", f"core.excludesFile={default}"]


def test_count_untracked_honors_repo_local_excludesfile_despite_ceiling(repo, monkeypatch):
    # A subdirectory workspace plus an inherited GIT_CEILING_DIRECTORIES at the repo root
    # must NOT stop the resolver from seeing the repo-local core.excludesFile the enumeration
    # child honors. The resolver's env allowlist omits the ceiling, so it discovers the repo
    # exactly like the child (#330 review). Before the allowlist, the resolver would fail
    # discovery, inject the default, and mask the local excludes -> secret.txt re-counted.
    repo_real = repo.resolve()
    sub = repo_real / "sub"
    sub.mkdir()
    ignore = repo_real / "excl"
    ignore.write_text("secret.txt\n")
    _git(repo_real, "config", "--local", "core.excludesFile", str(ignore))
    (sub / "secret.txt").write_text("s\n")  # ignored via repo-local excludesFile
    (sub / "keep.py").write_text("k\n")  # untracked -> counted
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(repo_real))
    assert gitdiff.count_untracked(str(sub), None, timeout=30) == 1


def test_global_excludes_flags_ignores_inherited_git_config(tmp_path, monkeypatch):
    # GIT_CONFIG relocates ONLY `git config`'s view (not `ls-files`'). The resolver must
    # strip it, or an inherited GIT_CONFIG would hide the user's real global excludesFile
    # and inject a wrong fallback at command precedence (#330 review). Here GIT_CONFIG_GLOBAL
    # holds the real setting and GIT_CONFIG points at an empty file: the real one must win.
    ignore = _write_global_excludesfile(monkeypatch, tmp_path, ["x"])
    empty = tmp_path / "empty_gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG", str(empty))  # the trap
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    assert flags == ["-c", f"core.excludesFile={ignore}"]


def test_count_untracked_ignores_inherited_git_config_override(repo, outside, monkeypatch):
    # End-to-end: an ambient GIT_CONFIG must not let a globally-ignored file slip back into
    # the count (and, under include, the egress). RED if the resolver honors GIT_CONFIG.
    _write_global_excludesfile(monkeypatch, outside, ["secret.txt"])  # real global excludes
    empty = outside / "empty_gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG", str(empty))  # the trap
    (repo / "secret.txt").write_text("shh\n")  # globally ignored -> not counted
    (repo / "keep.py").write_text("k = 1\n")  # untracked -> counted (positive control)
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 1


def test_global_excludes_flags_preserves_newline_in_value(tmp_path, monkeypatch):
    # A core.excludesFile value can contain an embedded newline; the resolver must preserve
    # the WHOLE value (via -z), not silently drop everything after the first newline — which
    # would point at a different ignore file and re-open the egress this fixes (#330 review).
    weird = f"{tmp_path / 'alpha'}\n{tmp_path / 'beta'}"  # absolute path with an embedded \n
    config = tmp_path / "gc"
    run_git(tmp_path, "config", "-f", str(config), "core.excludesFile", weird)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    assert flags == ["-c", f"core.excludesFile={weird}"]


def test_global_excludes_flags_preserves_carriage_return_in_value(tmp_path, monkeypatch):
    # A core.excludesFile value can contain a raw carriage return. It is read via `git config
    # -z` through the same run_lines runner, so universal-newline text mode would rewrite \r ->
    # \n and corrupt the path. The whole byte-exact value must survive (#353).
    weird = f"{tmp_path / 'al'}\r{tmp_path / 'pha'}"  # absolute path with an embedded \r
    config = tmp_path / "gc"
    run_git(tmp_path, "config", "-f", str(config), "core.excludesFile", weird)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    flags = gitdiff._global_excludes_flags(str(tmp_path), timeout=30)
    assert flags == ["-c", f"core.excludesFile={weird}"]


def test_global_excludes_flags_rejects_oversized_multiline_value(tmp_path, monkeypatch):
    # The size cap must hold even when the value is spread across many physical lines — the
    # accumulation counts the total, so a multi-line value cannot evade the bound (#330 review).
    value = ("/" + "a" * 60 + "\n") * 200  # ~12200 bytes across 200 lines, > cap (8192)
    config = tmp_path / "gc"
    run_git(tmp_path, "config", "-f", str(config), "core.excludesFile", value)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    with pytest.raises(RuntimeError, match="size cap"):
        gitdiff._global_excludes_flags(str(tmp_path), timeout=30)


def test_default_excludes_path_empty_home_is_root_relative(monkeypatch):
    # git treats HOME="" as present (empty home == "/", so /.config/git/ignore), distinct
    # from unset; the resolver must preserve that path, not drop the default (#330 review).
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", "")
    assert gitdiff._default_excludes_path() == "/.config/git/ignore"


def test_default_excludes_path_unset_home_and_xdg_returns_none(monkeypatch):
    # Truly-unset HOME (and no XDG) is the only case with no computable default.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    assert gitdiff._default_excludes_path() is None


def test_global_excludes_flags_no_default_returns_empty(tmp_path, monkeypatch):
    # Unset key AND no XDG/HOME to compute a default location -> no flag (the stripped
    # child then has no global layer, matching git under the same env).
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)  # key unset
    assert gitdiff._global_excludes_flags(str(tmp_path), timeout=30) == []


def test_global_excludes_flags_fails_loud_on_bad_config(tmp_path, monkeypatch):
    # A `git config` outcome other than found (0) / absent (1) must NOT be swallowed as
    # "unset" — that would silently restore the egress bug. A malformed config exits 128.
    bad = tmp_path / "bad_config"
    bad.write_text("this is not valid git config\n[[[\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(bad))
    with pytest.raises(RuntimeError, match="config"):
        gitdiff._global_excludes_flags(str(tmp_path), timeout=30)


def test_count_untracked_respects_global_core_excludesfile(repo, outside, monkeypatch):
    # RED before the fix: the stripped child never reads global config, so the globally
    # ignored file is counted (== 2). GREEN after: it is excluded (== 1).
    _write_global_excludesfile(monkeypatch, outside, ["secret.txt"])
    (repo / "secret.txt").write_text("shh\n")  # globally ignored -> not counted
    (repo / "keep.py").write_text("y = 1\n")  # untracked -> counted (positive control)
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 1


def test_count_untracked_respects_xdg_default_ignore(repo, outside, monkeypatch):
    xdg = outside / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "ignore").write_text("secret.txt\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)  # unset -> default location
    (repo / "secret.txt").write_text("shh\n")
    (repo / "keep.py").write_text("y = 1\n")
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 1


def test_count_untracked_local_excludesfile_wins_over_global(repo, outside, monkeypatch):
    # Merged resolution must respect git's precedence (local over global). A naive
    # `git config --global` resolver would inject the GLOBAL file at highest precedence,
    # ignoring the two global-only names and counting the wrong set (== 1 here). Correct
    # behavior counts the two files only global would ignore (== 2).
    _write_global_excludesfile(monkeypatch, outside, ["aaa.txt", "ccc.txt"])
    local_ignore = outside / "local_ignore"
    local_ignore.write_text("bbb.txt\n")
    _git(repo, "config", "--local", "core.excludesFile", str(local_ignore))
    (repo / "aaa.txt").write_text("a\n")  # only GLOBAL ignores it -> local wins -> counted
    (repo / "bbb.txt").write_text("b\n")  # LOCAL ignores it -> not counted
    (repo / "ccc.txt").write_text("c\n")  # only GLOBAL ignores it -> local wins -> counted
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 2


def test_count_untracked_explicit_missing_excludesfile_suppresses_default(
    repo, outside, monkeypatch
):
    # An explicitly-set-but-missing core.excludesFile means "no global ignore" in git's own
    # semantics — the XDG default is NOT consulted. Verified empirically: `-c
    # core.excludesFile=/missing` emits an otherwise-globally-ignored file. So the file
    # must be counted despite a matching XDG default existing.
    xdg = outside / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "ignore").write_text("secret.txt\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    config = outside / "gc"
    config.write_text(f"[core]\n\texcludesFile = {outside / 'does-not-exist'}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    (repo / "secret.txt").write_text("s\n")
    assert gitdiff.count_untracked(str(repo), None, timeout=30) == 1


def test_count_untracked_honors_relative_excludesfile_under_relative_cwd(repo, monkeypatch):
    # Regression for the cwd double-prefix (#330 review): with a RELATIVE cwd and a relative
    # `core.excludesFile`, the resolver and the ls-files child share the cwd, so the value
    # must pass through unchanged. Joining it onto the relative cwd would double-prefix and
    # drop the excludes, re-counting (and, under include, re-sending) the ignored file.
    (repo / "cfg").mkdir()
    (repo / "cfg" / "ignore").write_text("secret.txt\ncfg/\n")  # ignore the secret AND cfg/ itself
    _git(repo, "config", "--local", "core.excludesFile", "cfg/ignore")  # relative value
    (repo / "secret.txt").write_text("s\n")  # ignored -> not counted
    (repo / "keep.py").write_text("k\n")  # untracked -> counted
    monkeypatch.chdir(repo.parent)
    assert gitdiff.count_untracked(repo.name, None, timeout=30) == 1


def test_gather_include_does_not_gather_globally_ignored_file(repo, outside, monkeypatch):
    # The egress consequence: with untracked="include", a globally-ignored file must NOT be
    # gathered (its contents would be sent to OpenAI). RED before the fix.
    _write_global_excludesfile(monkeypatch, outside, ["secret.env"])
    (repo / "secret.env").write_text("TOKEN=abcdef\n")  # must NOT be sent
    (repo / "keep.py").write_text("k = 1\n")  # gathered (positive control)
    res = gitdiff.gather_diff(
        str(repo), "working_tree", untracked="include", timeout=30, max_bytes=1_000_000
    )
    assert "secret.env" not in res.text
    assert "TOKEN=abcdef" not in res.text
    assert res.untracked_included == 1
    assert "keep.py" in res.text
