"""Bounded git-subprocess runner: streams stdout as bounded lines, drains stderr
concurrently under a cap, and kills/reaps the process group on timeout or consumer
failure. Ports the lifecycle guarantees of gitdiff._stream_redacted_diff (#326)."""

from __future__ import annotations

import os
import sys
import time

import pytest

from codex_in_claude._core import gitproc, streamcap

_ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}


def _emit(script: str) -> list[str]:
    """A python child whose stdout is `script`-controlled, used as a fake git."""
    return [sys.executable, "-c", script]


def _count_lines(lines) -> int:
    return sum(1 for _ in lines)


def test_run_lines_counts_multichunk_output_exactly():
    # A listing far larger than one 64 KiB read chunk must be counted exactly by the
    # bounded streaming reader — never materialized whole.
    n = 5000
    cmd = _emit(f"import sys\nfor i in range({n}): sys.stdout.write(f'line{{i}}\\n')")
    got = gitproc.run_lines(
        cmd, cwd=".", env=_ENV, timeout=30, max_line_bytes=1 << 20, consume=_count_lines
    )
    assert got == n


def test_run_lines_returns_consumer_value():
    cmd = _emit("import sys\nsys.stdout.write('a\\nbb\\nccc\\n')")
    total = gitproc.run_lines(
        cmd,
        cwd=".",
        env=_ENV,
        timeout=30,
        max_line_bytes=1 << 20,
        consume=lambda lines: sum(len(x.strip()) for x in lines),
    )
    assert total == 6


def test_run_lines_raises_git_stream_failed_on_nonzero_exit():
    cmd = _emit("import sys\nsys.stderr.write('boom\\n')\nsys.exit(3)")
    with pytest.raises(gitproc.GitStreamFailed) as ei:
        gitproc.run_lines(
            cmd, cwd=".", env=_ENV, timeout=30, max_line_bytes=1 << 20, consume=_count_lines
        )
    assert ei.value.returncode == 3
    assert "boom" in ei.value.stderr


def test_run_lines_raises_binary_not_found():
    with pytest.raises(gitproc.GitBinaryNotFound):
        gitproc.run_lines(
            ["definitely-not-a-real-binary-zzz"],
            cwd=".",
            env=_ENV,
            timeout=30,
            max_line_bytes=1 << 20,
            consume=_count_lines,
        )


def test_run_lines_timeout_kills_stalled_process():
    # Writes one line, then stalls without closing stdout: the watchdog must kill the
    # group so run_lines raises GitStreamTimeout promptly — well within the stall.
    cmd = _emit(
        "import sys, time\nsys.stdout.write('partial\\n')\nsys.stdout.flush()\ntime.sleep(30)"
    )
    start = time.monotonic()
    with pytest.raises(gitproc.GitStreamTimeout):
        gitproc.run_lines(
            cmd, cwd=".", env=_ENV, timeout=1, max_line_bytes=1 << 20, consume=_count_lines
        )
    assert time.monotonic() - start < 10


def test_run_lines_timeout_kills_descendant_holding_pipe():
    # Parent exits immediately after spawning a grandchild that inherits the stdout pipe
    # and sleeps. killpg(proc.pid) must still terminate the group so we don't hang for
    # the grandchild's lifetime (zombie-leader / inherited-pipe regression).
    parent = _emit(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])\n"
        "sys.exit(0)"
    )
    start = time.monotonic()
    with pytest.raises(gitproc.GitStreamTimeout):
        gitproc.run_lines(
            parent, cwd=".", env=_ENV, timeout=2, max_line_bytes=1 << 20, consume=_count_lines
        )
    assert time.monotonic() - start < 7


def test_run_lines_reraises_and_reaps_when_consumer_raises():
    # A consumer that raises mid-stream must not hang or be swallowed: the original
    # exception propagates and the call returns promptly (process killed + reaped).
    # Emit a full read-chunk (>64 KiB) so the first line surfaces immediately, then stall
    # — mirroring a real git that has produced output but not yet exited.
    cmd = _emit(
        "import sys, time\nsys.stdout.write('l\\n' * 40000)\nsys.stdout.flush()\ntime.sleep(30)"
    )

    def boom(lines):
        next(iter(lines))  # read one line
        raise ValueError("consumer exploded")

    start = time.monotonic()
    with pytest.raises(ValueError, match="consumer exploded"):
        gitproc.run_lines(cmd, cwd=".", env=_ENV, timeout=30, max_line_bytes=1 << 20, consume=boom)
    assert time.monotonic() - start < 10  # did not wait out the 30s sleep


def test_run_lines_drains_remainder_when_consumer_stops_early():
    # A consumer that returns after reading one line must not deadlock: run_lines drains
    # the unread remainder itself (bounded, discarded) and returns the consumer's value.
    n = 2000
    cmd = _emit(f"import sys\nfor i in range({n}): sys.stdout.write(f'l{{i}}\\n')")

    def first_only(lines):
        return next(iter(lines)).strip()

    got = gitproc.run_lines(
        cmd, cwd=".", env=_ENV, timeout=30, max_line_bytes=1 << 20, consume=first_only
    )
    assert got == "l0"


def test_run_lines_bounds_large_stderr():
    # A pathological stderr must be retained under the cap, not materialized whole.
    cmd = _emit(
        "import sys\n"
        "sys.stderr.write('E' * (5 * 1024 * 1024))\n"
        "sys.stderr.write('\\n')\n"
        "sys.exit(1)"
    )
    with pytest.raises(gitproc.GitStreamFailed) as ei:
        gitproc.run_lines(
            cmd, cwd=".", env=_ENV, timeout=30, max_line_bytes=1 << 20, consume=_count_lines
        )
    assert len(ei.value.stderr.encode("utf-8")) <= gitproc._STDERR_CAP + 64


def test_run_lines_caps_oversized_single_line():
    # A single logical line larger than max_line_bytes is truncated (bounded) but still
    # surfaces as exactly one line — memory stays O(max_line_bytes).
    cmd = _emit("import sys\nsys.stdout.write('Z' * (1024 * 1024))\nsys.stdout.write('\\n')")
    lines = gitproc.run_lines(cmd, cwd=".", env=_ENV, timeout=30, max_line_bytes=4096, consume=list)
    assert len(lines) == 1
    assert len(lines[0].encode("utf-8")) <= 4096
    assert streamcap._LINE_TRUNC_MARKER.strip() in lines[0]
