"""Generic subprocess runtime: success, timeout, missing binary."""

from __future__ import annotations

import contextlib
import os
import signal
import sys

import anyio
import pytest

from codex_in_claude._core import runtime


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


async def test_run_async_success(tmp_path):
    run = await runtime.run_async(
        [sys.executable, "-c", "import sys; sys.stdout.write('hi'); sys.stderr.write('e')"],
        cwd=str(tmp_path),
        timeout_seconds=10,
    )
    assert run.exit_code == 0
    assert run.stdout == "hi"
    assert run.stderr == "e"
    assert not run.timed_out
    assert not run.binary_missing


async def test_run_async_stdin(tmp_path):
    run = await runtime.run_async(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        cwd=str(tmp_path),
        timeout_seconds=10,
        stdin_text="abc",
    )
    assert run.stdout == "ABC"


async def test_run_async_timeout_kills(tmp_path):
    run = await runtime.run_async(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        timeout_seconds=1,
    )
    assert run.timed_out
    assert run.stderr == runtime.TIMED_OUT


async def test_run_async_missing_binary(tmp_path):
    run = await runtime.run_async(
        ["definitely-not-a-real-binary-xyz"], cwd=str(tmp_path), timeout_seconds=5
    )
    assert run.binary_missing
    assert run.exit_code == 127


def test_run_sync_capture_success():
    run = runtime.run_sync_capture([sys.executable, "-c", "print('ok')"], timeout_seconds=10)
    assert run.exit_code == 0
    assert "ok" in run.stdout


def test_run_sync_capture_missing_binary():
    run = runtime.run_sync_capture(["definitely-not-a-real-binary-xyz"], timeout_seconds=5)
    assert run.binary_missing


def test_run_sync_capture_timeout():
    run = runtime.run_sync_capture(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout_seconds=1
    )
    assert run.timed_out


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - exists but not ours
        return True
    return True


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="process-group kill is POSIX-only")
async def test_run_async_cancellation_kills_process_group(tmp_path):
    """Cancelling an in-flight run kills the whole process GROUP, not just the
    direct child: a grandchild (which a parent-only kill would orphan) must also
    die, proving the `start_new_session` + `killpg` teardown. CancelledError must
    still propagate rather than being swallowed (#39)."""
    import asyncio

    pidfile = tmp_path / "grandchild.pid"
    # The direct child spawns a long-lived grandchild in the same process group,
    # records its pid, then blocks. Only a process-group kill reaps both.
    child_src = (
        "import subprocess, sys, time, pathlib;"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(g.pid));"
        "time.sleep(60)"
    )

    grandchild_pid: int | None = None
    try:
        task = asyncio.create_task(
            runtime.run_async(
                [sys.executable, "-c", child_src], cwd=str(tmp_path), timeout_seconds=30
            )
        )
        # Wait until the child has spawned the grandchild and recorded its pid,
        # capturing it inside the loop so a fall-through fails clearly rather than
        # raising FileNotFoundError on a missing pidfile.
        for _ in range(100):
            text = pidfile.read_text().strip() if pidfile.exists() else ""
            if text:
                grandchild_pid = int(text)
                break
            await asyncio.sleep(0.05)
        assert grandchild_pid is not None, "child never recorded the grandchild pid"
        assert _pid_alive(grandchild_pid)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The grandchild must be gone — a parent-only kill would have orphaned it.
        for _ in range(100):
            if not _pid_alive(grandchild_pid):
                break
            await asyncio.sleep(0.05)
        assert not _pid_alive(grandchild_pid)
    finally:
        if grandchild_pid is not None and _pid_alive(grandchild_pid):  # pragma: no cover - cleanup
            with contextlib.suppress(ProcessLookupError):
                os.kill(grandchild_pid, signal.SIGKILL)


def test_run_async_observer_receives_each_stdout_line():
    lines: list[str] = []
    code = "import sys\nfor i in range(5):\n    print(f'line{i}')\nsys.stdout.flush()"
    run = anyio.run(
        lambda: runtime.run_async(
            _py(code), cwd=".", timeout_seconds=10, on_stdout_line=lines.append
        )
    )
    assert run.exit_code == 0
    assert [ln.strip() for ln in lines] == [f"line{i}" for i in range(5)]
    # Full stream is still captured intact.
    assert run.stdout.splitlines() == [f"line{i}" for i in range(5)]


def test_run_async_observer_handles_large_simultaneous_stdout_stderr():
    # Interleaved heavy output on both pipes must not deadlock.
    code = (
        "import sys\n"
        "for i in range(2000):\n"
        "    sys.stdout.write('o'*200+'\\n'); sys.stderr.write('e'*200+'\\n')\n"
    )
    seen: list[str] = []
    run = anyio.run(
        lambda: runtime.run_async(
            _py(code), cwd=".", timeout_seconds=30, on_stdout_line=seen.append
        )
    )
    assert run.exit_code == 0
    assert len(seen) == 2000
    assert run.stdout.count("o" * 200) == 2000
    assert run.stderr.count("e" * 200) == 2000


def test_run_async_observer_path_honors_timeout():
    code = "import time\nprint('start', flush=True)\ntime.sleep(30)"
    run = anyio.run(
        lambda: runtime.run_async(
            _py(code), cwd=".", timeout_seconds=1, on_stdout_line=lambda _l: None
        )
    )
    assert run.timed_out is True


def test_run_async_observer_forwards_stdin():
    code = "import sys\nsys.stdout.write(sys.stdin.read().upper())"
    lines: list[str] = []
    run = anyio.run(
        lambda: runtime.run_async(
            _py(code),
            cwd=".",
            timeout_seconds=10,
            stdin_text="hello\n",
            on_stdout_line=lines.append,
        )
    )
    assert run.stdout.strip() == "HELLO"


def test_run_async_without_observer_is_unchanged():
    code = "print('plain')"
    run = anyio.run(lambda: runtime.run_async(_py(code), cwd=".", timeout_seconds=10))
    assert run.stdout.strip() == "plain"
    assert run.exit_code == 0


def test_run_async_slow_observer_does_not_truncate_stdout():
    # Regression: a slow observer must NOT truncate captured stdout. Pipe draining
    # is decoupled from observation, so all 10 lines are captured regardless of how
    # long the callback takes, and the observer eventually sees every line. The
    # total callback time (~1.5s) exceeds the old fixed 1s join that caused the bug.
    import time

    seen: list[str] = []

    def slow(line: str) -> None:
        time.sleep(0.15)
        seen.append(line)

    code = "import sys\nfor i in range(10):\n    print(f'line{i}')\nsys.stdout.flush()"
    run = anyio.run(
        lambda: runtime.run_async(_py(code), cwd=".", timeout_seconds=20, on_stdout_line=slow)
    )
    assert run.exit_code == 0
    assert run.timed_out is False
    # Complete capture — not truncated by the slow observer.
    assert run.stdout.splitlines() == [f"line{i}" for i in range(10)]
    # Observation is decoupled but still complete on a clean exit.
    assert [ln.strip() for ln in seen] == [f"line{i}" for i in range(10)]


async def test_run_async_caps_flooding_stdout(tmp_path):
    # Emit ~5 MB of lines but cap capture at 50 KB: bounded, not killed, completes.
    code = "import sys\n" + "for i in range(200000): sys.stdout.write(f'line{i}\\n')\n"
    run = await runtime.run_async(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        timeout_seconds=30,
        max_output_bytes=50_000,
    )
    assert run.exit_code == 0
    assert not run.timed_out
    assert run.output_truncated
    assert len(run.stdout.encode("utf-8")) <= 50_000 + len(b"[output truncated]\n")
    assert "line0\n" in run.stdout  # head preserved
    assert run.stdout.rstrip().endswith("199999")  # tail preserved


async def test_run_async_huge_single_line_bounded(tmp_path):
    code = "import sys; sys.stdout.write('x' * 5_000_000 + '\\n')"
    run = await runtime.run_async(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        timeout_seconds=30,
        max_output_bytes=50_000,
    )
    assert run.exit_code == 0
    assert len(run.stdout.encode("utf-8")) <= 100_000  # bounded despite one giant line


async def test_run_async_streaming_caps_and_survives_tail(tmp_path):
    seen: list[str] = []
    code = "import sys\n" + "for i in range(100000): sys.stdout.write(f'{{\"i\": {i}}}\\n')\n"
    run = await runtime.run_async(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        timeout_seconds=30,
        on_stdout_line=seen.append,
        max_output_bytes=40_000,
    )
    assert run.exit_code == 0
    assert run.output_truncated
    assert seen  # observer still invoked
    assert '{"i": 99999}' in run.stdout  # final event survives in the tail


async def test_run_async_caps_stderr(tmp_path):
    code = "import sys\n" + "for i in range(200000): sys.stderr.write(f'e{i}\\n')\n"
    run = await runtime.run_async(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        timeout_seconds=30,
        max_output_bytes=50_000,
    )
    # stderr is bounded by _STDERR_RESERVE (~1 MiB), not by max_output_bytes.
    assert len(run.stderr.encode("utf-8")) <= runtime._STDERR_RESERVE + 100


async def test_run_async_stdout_cap_is_full_max_output_bytes(tmp_path):
    """F2 regression: stdout-only output just under max_output_bytes must NOT be
    truncated. Under the old partition, stdout_cap was reduced by stderr's share,
    so stdout output under the full cap was falsely truncated."""
    # Old code: stderr_cap = min(1MB, 40000//2) = 20000; stdout_cap = 20000.
    # A 30000-byte stdout write exceeded the 20000 stdout_cap → falsely truncated.
    # New code: stdout_cap = max_output_bytes = 40000; 30000 bytes fits.
    code = "import sys; sys.stdout.write('o' * 30_000)"
    run = await runtime.run_async(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        timeout_seconds=10,
        max_output_bytes=40_000,
    )
    assert run.exit_code == 0
    assert not run.output_truncated, "stdout under the cap should not be truncated"
    assert len(run.stdout.encode("utf-8")) == 30_000


async def test_run_async_stderr_cap_is_independent_reserve(tmp_path):
    """F2 regression: stderr-only output up to the reserve must NOT be falsely
    truncated when max_output_bytes is small. Under the old partition,
    stderr_cap = min(1MB, max_output_bytes//2), so a small max_output_bytes
    imposed a tiny stderr cap below the advertised reserve."""
    # Old code: stderr_cap = min(1MB, 40000//2) = 20000.
    # 30000 bytes of stderr exceeded the 20000 cap → falsely truncated.
    # New code: stderr_cap = _STDERR_RESERVE (1MB); 30000 bytes fits.
    code = "import sys; sys.stderr.write('e' * 30_000)"
    run = await runtime.run_async(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        timeout_seconds=10,
        max_output_bytes=40_000,
    )
    assert run.exit_code == 0
    assert not run.output_truncated, "stderr under the reserve should not be truncated"
    assert len(run.stderr.encode("utf-8")) == 30_000


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="process-group kill is POSIX-only")
async def test_run_async_timeout_descendant_holds_pipe(tmp_path):
    """F1 regression: a parent that exits immediately but leaves a descendant
    holding the stdout pipe open must still time out promptly and return
    timed_out=True. Under the old code, proc.wait() returned immediately (parent
    exited), but t.join() blocked on the pump thread which blocked on the pipe
    held by the descendant — bypassing the configured timeout entirely."""
    import time

    # The parent spawns a grandchild (inherits stdout pipe fd 1) then exits at once.
    # The grandchild sleeps for 10s, holding the write end of our stdout pipe open.
    # Without the fix: proc.wait(timeout=2) returns immediately (parent exited),
    # then t.join() hangs ~10s until the grandchild exits, returning timed_out=False.
    # With the fix: the watchdog fires after 2s, kills the process group (including
    # the grandchild), closing the pipe so the pump thread reaches EOF.
    cmd = _py(
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)']);"
        "sys.exit(0)"
    )
    start = time.monotonic()
    run = await runtime.run_async(cmd, cwd=str(tmp_path), timeout_seconds=2)
    elapsed = time.monotonic() - start

    assert run.timed_out is True, (
        f"expected timed_out=True (descendant held pipe); got timed_out={run.timed_out}, "
        f"elapsed={elapsed:.1f}s"
    )
    # Should return within a few seconds of the timeout, not 10s later.
    assert elapsed < 7, (
        f"expected return well before descendant's 10s lifetime; elapsed={elapsed:.1f}s"
    )


async def test_f2_observer_queue_byte_bounded(tmp_path, monkeypatch):
    """F2: the observer queue must be byte-bounded, not just count-bounded. Patching
    _OBSERVER_QUEUE_BYTES to a tiny value verifies that lines exceeding the byte
    budget are dropped rather than queued indefinitely.
    Before fix: _OBSERVER_QUEUE_BYTES does not exist → AttributeError (RED).
    After fix: the constant exists; patching to 500 bytes allows only ~2 lines of
    200 bytes; the observer sees fewer than all 50 lines (some dropped)."""
    import time

    seen: list[str] = []

    def slow(line: str) -> None:
        time.sleep(0.002)  # deliberately slow: causes queue pressure
        seen.append(line)

    # Shrink the byte budget to 500 bytes: only 2 lines of 200 bytes fit at once.
    monkeypatch.setattr(runtime, "_OBSERVER_QUEUE_BYTES", 500)

    # 50 lines of 200 bytes. The subprocess emits all lines fast; with a 500-byte queue
    # budget and a slow observer, most lines are dropped by the byte guard.
    code = (
        "import sys\n"
        "for i in range(50):\n"
        "    sys.stdout.write('x' * 200 + '\\n')\n"
        "sys.stdout.flush()\n"
    )
    run = await runtime.run_async(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        timeout_seconds=30,
        on_stdout_line=slow,
        max_output_bytes=50_000,
    )
    assert run.exit_code == 0
    assert not run.timed_out
    # The observer must have received some (not necessarily all) events.
    assert len(seen) > 0
    # With a 500-byte budget and 200-byte lines, the observer should see fewer than
    # all 50 lines (the byte guard drops lines when the budget is exhausted).
    assert len(seen) < 50
