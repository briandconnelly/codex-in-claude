"""A bounded runner for a git subprocess whose stdout must be streamed rather than
captured whole.

CLI-agnostic; no parent-package imports (keeps ``_core`` extractable). It exists so a
count over a repo-cardinality git listing (``ls-tree``, ``diff --numstat``,
``ls-files``) stays O(one line + one chunk) in memory regardless of how many entries
the workspace has, instead of materializing the whole output via
``subprocess.run(capture_output=True)``.

The process lifecycle is ported verbatim in spirit from
``gitdiff._stream_redacted_diff`` — that code paid for these guarantees in several
subtle bug fixes, and a second, weaker copy would silently regress them:

- ``start_new_session=True`` so the child is its own process-group leader, and a
  timeout kills the *group* (``killpg(proc.pid)``) — reaching a grandchild that
  inherited and still holds the stdout pipe. ``proc.pid`` is used directly as the pgid
  rather than ``os.getpgid`` because on macOS ``getpgid`` raises ``ESRCH`` on a zombie
  leader while the group is still live.
- A kill/reap lock plus a ``_finished`` flag so the watchdog ``Timer`` can never signal
  a already-reaped (and possibly reused) PID.
- stderr drained **concurrently** on a thread under a byte cap. A post-EOF read would
  deadlock if git filled the >64 KiB stderr pipe before the caller drained stdout; the
  concurrent drain removes that condition while bounding retained bytes.
- A deadline-bounded wait that still kills the group if the child closed stdout but
  stayed alive, or a descendant holds a pipe open past EOF.

The caller supplies the full argv (``["git", *hardening_flags, *args]``) and env, so
this module carries no project config or git-hardening policy of its own.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from typing import TYPE_CHECKING, TypeVar, cast

from codex_in_claude._core import streamcap

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import TextIO

T = TypeVar("T")

# Bounded stderr retention. git diagnostics are tiny, but an untrusted workspace could
# provoke a pathological stderr; keep at most this many bytes (head + tail window).
_STDERR_CAP = 64 * 1024


class GitBinaryNotFound(RuntimeError):
    """The git executable could not be launched (spawn raised ``FileNotFoundError``)."""


class GitStreamTimeout(RuntimeError):
    """The command exceeded its timeout and its process group was killed."""


class GitStreamFailed(RuntimeError):
    """The command exited non-zero. ``returncode`` and the bounded ``stderr`` are kept
    so a caller can map to its own domain error (e.g. detect "not a git repository")."""

    def __init__(self, returncode: int, stderr: str) -> None:
        super().__init__(stderr.strip() or "git failed")
        self.returncode = returncode
        self.stderr = stderr


def run_lines(  # noqa: PLR0915
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    max_line_bytes: int,
    consume: Callable[[Iterator[str]], T],
) -> T:
    """Run ``argv`` under ``cwd``/``env`` and feed its stdout to ``consume`` as a bounded
    line iterator (each line capped at ``max_line_bytes``), returning ``consume``'s value.

    ``consume`` MAY stop iterating early — the runner drains and discards any remainder
    so the child never blocks on a full stdout pipe. If ``consume`` raises, the process
    group is killed and reaped before the exception propagates, so nothing is orphaned.

    Raises ``GitBinaryNotFound`` if git cannot be launched, ``GitStreamTimeout`` if the
    run exceeds ``timeout`` (the group is killed), and ``GitStreamFailed`` on a non-zero
    exit. Memory stays O(``max_line_bytes`` + chunk + ``_STDERR_CAP``).
    """
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise GitBinaryNotFound("git executable not found") from exc

    deadline = time.monotonic() + timeout
    timed_out = threading.Event()
    stderr_buf: list[str] = []
    # Guard kill+reap with a lock and a flag so the Timer callback cannot signal a
    # reaped (potentially reused) PID after the main thread has moved on.
    kill_lock = threading.Lock()
    finished = [False]

    def _sigkill_group() -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL)  # proc is its own group leader
            else:  # pragma: no cover - non-POSIX fallback
                proc.kill()

    def _watchdog() -> None:
        with kill_lock:
            if finished[0]:
                return
            timed_out.set()
            _sigkill_group()

    def _drain_stderr() -> None:
        # Drain to EOF (avoids the >64 KiB pipe-buffer deadlock) while retaining at most
        # _STDERR_CAP bytes so a large diagnostic cannot OOM the server.
        if proc.stderr is not None:
            cap = streamcap.BoundedCapture(_STDERR_CAP)
            for line in streamcap.iter_bounded_lines(cast("TextIO", proc.stderr), _STDERR_CAP):
                cap.add(line)
            stderr_buf.append(cap.result())

    timer = threading.Timer(timeout, _watchdog)
    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    try:
        assert proc.stdout is not None
        timer.start()
        stderr_thread.start()
        lines = streamcap.iter_bounded_lines(cast("TextIO", proc.stdout), max_line_bytes)
        try:
            result = consume(lines)
            # Drain whatever consume left unread so the child is not blocked on a full
            # pipe; bounded per line, discarded, so memory stays O(max_line_bytes).
            for _ in lines:
                pass
        except BaseException:
            # Consumer (or the underlying read) failed: stop the watchdog, then kill and
            # reap so no subprocess outlives the raised exception.
            with kill_lock:
                finished[0] = True
            timer.cancel()
            _sigkill_group()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
            stderr_thread.join(timeout=5)
            raise
        # stdout drained. Disable the watchdog first so the wait+kill below is
        # main-thread-only (no killpg-after-reap race), then bound the remaining work by
        # the deadline: git may have closed stdout yet still run, or a descendant may
        # hold stderr open.
        with kill_lock:
            finished[0] = True
        timer.cancel()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        stderr_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if proc.poll() is None or stderr_thread.is_alive():
            timed_out.set()
            _sigkill_group()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
            stderr_thread.join(timeout=5)
    finally:
        timer.cancel()  # idempotent: cleans up on exception paths
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
    if timed_out.is_set():
        raise GitStreamTimeout(f"git command timed out after {timeout}s")
    stderr = "".join(stderr_buf)
    if proc.returncode != 0:
        raise GitStreamFailed(proc.returncode, stderr)
    return result
