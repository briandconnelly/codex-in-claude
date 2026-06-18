"""Generic subprocess runtime: success, timeout, missing binary."""

from __future__ import annotations

import sys

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
