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
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import anyio
from anyio.to_thread import run_sync

from codex_in_claude._core import streamcap

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TextIO

# Default cap for captured stdout; the caller (config-aware layer) normally
# overrides this with CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES.
DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
# Separate fixed reserve for stderr capture, independent of the stdout cap
# (not necessarily smaller if a caller sets a tiny max_output_bytes).
_STDERR_RESERVE = 1 * 1024 * 1024
# F2: byte budget for the observer queue. A slow on_stdout_line callback can cause
# queue entries to pile up; this cap ensures at most 8 MiB waits in the queue at
# any time, complementing the existing count limit (maxsize=10_000).
_OBSERVER_QUEUE_BYTES = 8 * 1024 * 1024

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
    output_truncated: bool = field(default=False)

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


def _kill_group(proc: subprocess.Popen) -> None:
    """Best-effort SIGKILL the whole process group by proc.pid (== pgid because the
    child is spawned with start_new_session=True). Unlike kill_process_tree, this does
    NOT early-return when the direct child has exited and does NOT call os.getpgid
    (which raises ESRCH on a zombie): a descendant that inherited a pipe must still be
    killed even after the leader becomes a zombie."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            proc.kill()


def _wait_streaming(  # noqa: PLR0915
    proc: subprocess.Popen,
    stdin_text: str | None,
    on_stdout_line: Callable[[str], None] | None,
    timeout_seconds: int,
    max_output_bytes: int,
) -> tuple[str, str, bool, bool]:
    """Drain stdout/stderr concurrently under independent byte caps, optionally
    calling ``on_stdout_line`` per stdout line. Returns ``(stdout, stderr,
    timed_out, output_truncated)``. Stdout is captured up to ``max_output_bytes``
    bytes; stderr is captured up to a separate ``_STDERR_RESERVE`` (~1 MiB) —
    worst-case retained is ``max_output_bytes + _STDERR_RESERVE``. Both use
    head+tail windows so a flooding process cannot exhaust memory. The timeout
    is deadline-based: the main thread waits for the direct child and joins the
    pump threads within the remaining budget; if the deadline is exceeded, the
    whole process group is killed via ``_kill_group``, which closes any pipes
    held by descendants so the pumps reach EOF and the joins complete. The
    observer queue is bounded and drops under flood (it needs counts/timestamps
    only)."""
    stdout_cap = max_output_bytes
    stderr_cap = _STDERR_RESERVE
    out = streamcap.BoundedCapture(stdout_cap)
    err = streamcap.BoundedCapture(stderr_cap)
    observe = on_stdout_line is not None
    line_queue: queue.Queue[str] = queue.Queue(maxsize=10_000)
    # Non-blocking signal: set by _pump_stdout's finally once stdout is fully drained.
    # The observer uses this (with timed get()) instead of a queued sentinel so that
    # the pump's finally never blocks waiting for the observer to drain the queue.
    _pump_done = threading.Event()
    # F2: byte budget for the observer queue — a slow callback can cause queue entries
    # to pile up; this limits the total bytes queued at any time. Uses a list so the
    # nested closures can mutate it without a `nonlocal` declaration.
    _queued_bytes: list[int] = [0]
    _qb_lock = threading.Lock()

    def _pump_stdout() -> None:
        try:
            if proc.stdout is not None:
                for line in streamcap.iter_bounded_lines(cast("TextIO", proc.stdout), stdout_cap):
                    out.add(line)
                    if observe:
                        # F2: byte-bound the queue; drop silently under flood, never
                        # stall draining. Also keep the count guard (queue.Full).
                        n = len(line.encode("utf-8", "replace"))
                        with _qb_lock:
                            if _queued_bytes[0] + n <= _OBSERVER_QUEUE_BYTES:
                                try:
                                    line_queue.put_nowait(line)
                                    _queued_bytes[0] += n
                                except queue.Full:
                                    pass  # count guard: drop silently
        finally:
            if observe:
                _pump_done.set()  # non-blocking: pump never waits on the observer

    # Capture a narrowed local so _observe is type-safe: _observe is only started
    # when observe=True, which means on_stdout_line is not None here.
    _callback = on_stdout_line

    def _observe() -> None:
        while True:
            try:
                item = line_queue.get(timeout=0.1)
            except queue.Empty:
                # No item available: if the pump is done and the queue is empty, we
                # have seen everything — exit.  Otherwise keep polling.
                if _pump_done.is_set():
                    return
                continue
            # F2: decrement byte budget after consuming a line.
            with _qb_lock:
                _queued_bytes[0] -= len(item.encode("utf-8", "replace"))
            with contextlib.suppress(Exception):
                if _callback is not None:  # narrowing guard for the type checker
                    _callback(item)

    def _pump_stderr() -> None:
        if proc.stderr is not None:
            for line in streamcap.iter_bounded_lines(cast("TextIO", proc.stderr), stderr_cap):
                err.add(line)

    def _write_stdin() -> None:
        if proc.stdin is None:
            return
        with contextlib.suppress(OSError):
            if stdin_text is not None:
                proc.stdin.write(stdin_text)
            proc.stdin.close()

    t_stdin = threading.Thread(target=_write_stdin, daemon=True)
    t_out = threading.Thread(target=_pump_stdout, daemon=True)
    t_err = threading.Thread(target=_pump_stderr, daemon=True)
    # subprocess-bound: liveness reflects whether the child/descendants are still
    # running or holding pipes.  These are the ONLY threads that factor into the
    # timeout/kill decision.
    pumps = [t_stdin, t_out, t_err]
    observer = threading.Thread(target=_observe, daemon=True) if observe else None
    for t in pumps:
        t.start()
    if observer is not None:
        observer.start()
    deadline = time.monotonic() + timeout_seconds
    # 1. Wait for the DIRECT child, bounded by the timeout.
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=timeout_seconds)
    # 2. Join only the subprocess-bound pump threads within the remaining budget.
    #    A descendant that inherited a pipe can keep a pump blocked past the child's
    #    own exit, and a child can close its fds yet keep running — both gaps are
    #    bounded here.  The observer is intentionally excluded: a slow on_stdout_line
    #    callback must never cause a successful run to be marked timed_out.
    for t in pumps:
        t.join(timeout=max(0.0, deadline - time.monotonic()))
    # 3. If the child is still running OR a pump is still blocked, the deadline was
    #    exceeded: kill the whole process group and reap.  This runs on the MAIN thread
    #    and proc is still unreaped, so proc.pid is a valid pgid and there is no
    #    killpg-after-reap race.
    timed_out = proc.poll() is None or any(t.is_alive() for t in pumps)
    if timed_out:
        logger.warning(
            "subprocess pid=%s exceeded %ss; killing process group", proc.pid, timeout_seconds
        )
        # Use _kill_group: it does NOT early-return when the direct child has already
        # exited and does NOT call os.getpgid (which raises ESRCH on a zombie), so
        # pipe-holding descendants are killed even after the leader exits.
        _kill_group(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        for t in pumps:
            t.join(timeout=5)
    else:
        proc.wait()  # already exited; reap (instant)
    # Observer is in-process and daemon: drain it within the remaining budget, but
    # never let a slow activity callback delay the result unboundedly or mark the
    # run timed out.  _pump_done is set by _pump_stdout's finally (non-blocking), and
    # _observe exits once the event is set and the queue is empty.
    if observer is not None:
        observer.join(timeout=max(0.0, deadline - time.monotonic()))
    truncated = out.truncated or err.truncated
    if truncated:
        logger.warning(
            "subprocess pid=%s output exceeded %s bytes; capture bounded",
            proc.pid,
            max_output_bytes,
        )
    return out.result(), err.result(), timed_out, truncated


async def run_async(
    cmd: list[str],
    cwd: str,
    timeout_seconds: int,
    stdin_text: str | None = None,
    *,
    env: dict[str, str] | None = None,
    on_stdout_line: Callable[[str], None] | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> CommandRun:
    """Run `cmd` as a subprocess, returning a CommandRun. Never raises for process
    failures; a missing binary or timeout is reported via the CommandRun fields.
    Captured output is bounded to `max_output_bytes` (head+tail window) so a runaway
    process cannot OOM the server (#155); exceeding the cap sets `output_truncated`
    but does NOT kill the process."""
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

    def _wait() -> tuple[str, str, bool, bool]:
        return _wait_streaming(proc, stdin_text, on_stdout_line, timeout_seconds, max_output_bytes)

    try:
        out, err, timed_out, truncated = await run_sync(_wait, abandon_on_cancel=True)
    except anyio.get_cancelled_exc_class():
        logger.warning("subprocess pid=%s cancelled; killing process group", proc.pid)
        # _kill_group does NOT early-return when the direct child has already exited
        # (poll() is not None) — a descendant holding an inherited pipe is killed even
        # after the leader becomes a zombie.  Narrow residual: with abandon_on_cancel=True
        # the worker reaps at its own deadline; the cancel kill normally happens-before that
        # reap (causing the exit the worker then reaps).  A killpg-after-reap PID-reuse
        # race only opens if the process exits naturally at the cancel instant — the same
        # narrow window accepted on the timeout path.
        _kill_group(proc)
        raise
    elapsed = int((time.monotonic() - start) * 1000)
    if timed_out:
        return CommandRun(out, TIMED_OUT, -9, elapsed, True, output_truncated=truncated)
    logger.debug(
        "subprocess pid=%s exited code=%s elapsed_ms=%s stdout_bytes=%s",
        proc.pid,
        proc.returncode,
        elapsed,
        len(out or ""),
    )
    return CommandRun(out, err, proc.returncode, elapsed, False, output_truncated=truncated)


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
