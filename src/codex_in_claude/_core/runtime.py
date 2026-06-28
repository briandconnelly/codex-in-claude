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

# Sentinel enqueued after the stdout pump hits EOF, telling the observer thread to stop.
_STREAM_DONE = object()

# Default aggregate cap for captured stdout+stderr; the caller (config-aware layer)
# normally overrides this with CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES.
DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
# Share of the aggregate cap reserved for stderr (diagnostic, smaller than stdout).
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
    head+tail windows so a flooding process cannot exhaust memory. A watchdog
    timer kills the process GROUP after ``timeout_seconds``; this closes any
    pipes held by descendants so the pump threads reach EOF and the joins
    complete within the deadline. The observer queue is bounded and drops under
    flood (it needs counts/timestamps only)."""
    stdout_cap = max_output_bytes
    stderr_cap = _STDERR_RESERVE
    out = streamcap.BoundedCapture(stdout_cap)
    err = streamcap.BoundedCapture(stderr_cap)
    observe = on_stdout_line is not None
    line_queue: queue.Queue = queue.Queue(maxsize=10_000)
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
                line_queue.put(_STREAM_DONE)  # observer keeps draining, so this lands

    # Capture a narrowed local so _observe is type-safe: _observe is only started
    # when observe=True, which means on_stdout_line is not None here.
    _callback = on_stdout_line

    def _observe() -> None:
        while True:
            item = line_queue.get()
            if item is _STREAM_DONE:
                return
            # F2: decrement byte budget after consuming a real line (not the sentinel).
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

    readers = [
        threading.Thread(target=_write_stdin, daemon=True),
        threading.Thread(target=_pump_stdout, daemon=True),
        threading.Thread(target=_pump_stderr, daemon=True),
    ]
    threads = list(readers)
    if observe:
        threads.append(threading.Thread(target=_observe, daemon=True))
    # F1 watchdog: a threading.Timer fires after timeout_seconds and kills the
    # process GROUP (closing any pipes held by descendants), so the pump threads
    # reach EOF and the joins complete within the deadline.  Using a Timer rather
    # than proc.wait(timeout=…) means the deadline covers the full drain+join
    # lifecycle — not just the direct child's exit — so a descendant holding an
    # inherited pipe cannot bypass the configured timeout.
    _timed_out_event = threading.Event()

    def _on_timeout() -> None:
        _timed_out_event.set()
        logger.warning(
            "subprocess pid=%s exceeded %ss; killing process group", proc.pid, timeout_seconds
        )
        # Kill the process GROUP unconditionally, using proc.pid directly as the
        # pgid. Because proc was spawned with start_new_session=True, it is its
        # own process-group leader, so pgid == proc.pid. Descendants that did not
        # start a new session inherit that pgid. Critically, proc.pid is used
        # instead of os.getpgid(proc.pid) because on macOS (and possibly other
        # platforms) getpgid raises ESRCH on a zombie, whereas the process group
        # itself is still live as long as any member survives — including a
        # descendant holding an inherited pipe. kill_process_tree is intentionally
        # NOT used here: its proc.poll() guard short-circuits when the direct child
        # has already exited, leaving pipe-holding descendants alive.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # pragma: no cover - non-POSIX fallback
                proc.kill()

    for t in threads:
        t.start()
    timer = threading.Timer(timeout_seconds, _on_timeout)
    try:
        timer.start()
        for t in threads:
            t.join()
        proc.wait()  # reap the direct child; pipes closed so this is instant
    finally:
        timer.cancel()
    timed_out = _timed_out_event.is_set()
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
        kill_process_tree(proc)
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
