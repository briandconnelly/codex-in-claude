"""Generic subprocess runtime: spawn, communicate with a timeout, kill the tree.

CLI-agnostic. The subprocess is started in its own session (process group) so that,
on a timeout OR an MCP request cancellation, the whole tree is terminated rather
than orphaning a running child — the failure mode that dominates the official
codex plugin's open issues.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass

import anyio
from anyio.to_thread import run_sync

# Generic module: log via the stdlib only (no parent imports). Records propagate
# to the `codex_in_claude` logger, whose handlers go to stderr — never stdout, the
# stdio JSON-RPC channel. This trail is what a future disconnect needs (#39).
logger = logging.getLogger(__name__)

# stderr sentinel returned when the binary is not on PATH (spawn raised OSError).
BINARY_NOT_FOUND = "__binary_not_found__"
# stderr sentinel returned when the run exceeded its timeout and was killed.
TIMED_OUT = "__timed_out__"


@dataclass
class CommandRun:
    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: int
    timed_out: bool

    @property
    def binary_missing(self) -> bool:
        return self.stderr == BINARY_NOT_FOUND


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Best-effort terminate the process and its children. POSIX: kill the
    process group (the child is its own session leader). Falls back to killing
    just the process where process groups are unavailable (e.g. Windows)."""
    if proc.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            proc.kill()
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


async def run_async(
    cmd: list[str],
    cwd: str,
    timeout_seconds: int,
    stdin_text: str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> CommandRun:
    """Run `cmd` as a subprocess, returning a CommandRun. Never raises for process
    failures; a missing binary or timeout is reported via the CommandRun fields."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.PIPE if stdin_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            start_new_session=True,
        )
    except OSError:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.debug("spawn failed (binary missing): %s", cmd[0])
        return CommandRun("", BINARY_NOT_FOUND, 127, elapsed, False)

    logger.debug("spawned pid=%s cmd=%s timeout=%ss", proc.pid, cmd[0], timeout_seconds)

    def _wait() -> tuple[str, str, bool]:
        try:
            out, err = proc.communicate(input=stdin_text, timeout=timeout_seconds)
            return out, err, False
        except subprocess.TimeoutExpired:
            logger.warning(
                "subprocess pid=%s exceeded %ss; killing process group", proc.pid, timeout_seconds
            )
            kill_process_tree(proc)
            out, err = proc.communicate()
            return out, err, True

    try:
        out, err, timed_out = await run_sync(_wait, abandon_on_cancel=True)
    except anyio.get_cancelled_exc_class():
        # Client cancellation/disconnect: tear down the whole tree so no codex
        # subprocess is orphaned, then re-raise to preserve cancel semantics.
        logger.warning("subprocess pid=%s cancelled; killing process group", proc.pid)
        kill_process_tree(proc)
        raise
    elapsed = int((time.monotonic() - start) * 1000)
    if timed_out:
        return CommandRun(out, TIMED_OUT, -9, elapsed, True)
    logger.debug(
        "subprocess pid=%s exited code=%s elapsed_ms=%s stdout_bytes=%s",
        proc.pid,
        proc.returncode,
        elapsed,
        len(out or ""),
    )
    return CommandRun(out, err, proc.returncode, elapsed, False)


def run_sync_capture(
    cmd: list[str],
    timeout_seconds: int,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
) -> CommandRun:
    """Blocking variant for cheap, local probes (version/help/auth/git).

    Returns a CommandRun with binary_missing/timed_out set rather than raising, so
    callers can branch on the same shape as run_async."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
            input=stdin_text,
        )
    except (FileNotFoundError, NotADirectoryError):
        elapsed = int((time.monotonic() - start) * 1000)
        return CommandRun("", BINARY_NOT_FOUND, 127, elapsed, False)
    except subprocess.TimeoutExpired:
        elapsed = int((time.monotonic() - start) * 1000)
        return CommandRun("", TIMED_OUT, -9, elapsed, True)
    elapsed = int((time.monotonic() - start) * 1000)
    return CommandRun(proc.stdout or "", proc.stderr or "", proc.returncode, elapsed, False)
