"""Generic subprocess runtime: success, timeout, missing binary."""

from __future__ import annotations

import contextlib
import os
import signal
import sys

import pytest

from codex_in_claude._core import runtime


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
