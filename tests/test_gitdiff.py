"""Git diff gathering across scopes, validation, and bounding."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from codex_in_claude._core import gitdiff, streamcap
from codex_in_claude._core.redaction import DiffRedactor


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

    def spy(stream, max_line_bytes, chunk_size=65536):
        seen_chunked["used"] = True
        yield from real_iter(stream, max_line_bytes, chunk_size)

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

    def small_chunk_iter(stream, max_line_bytes, chunk_size=65536):
        yield from real_iter(stream, max_line_bytes, chunk_size=90)

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

    def small_chunk_iter(stream, max_line_bytes, chunk_size=65536):
        yield from real_iter(stream, max_line_bytes, chunk_size=60)

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

    def counting_iter(stream, max_line_bytes, chunk_size=65536):
        call_count["n"] += 1
        yield from real_iter(stream, max_line_bytes, chunk_size)

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
