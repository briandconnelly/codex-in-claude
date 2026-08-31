"""Synchronous spawn shim for this bridge's cheap `codex` probes (#541).

`pontonier.core.runtime.run_sync_capture` documents that it returns a `CommandRun`
"rather than raising, so callers can branch on the same shape as run_async" -- but it
catches only `(FileNotFoundError, NotADirectoryError)`, while its own `run_async` catches
broad `OSError` around `Popen` (and `appserver`'s two `Popen` sites catch
`(OSError, BinaryNotFoundError)`). The synchronous probe path is therefore the ONLY place
in this package where a spawn failure escapes as a raised exception.

That matters because `binpath.codex_bin()` deliberately falls back to the bare literal
`"codex"` when resolution finds nothing, and `subprocess` then performs its own `PATH`
lookup WITHOUT the `is_file()` + `X_OK` predicate `binresolve` applies to its own
candidates. An executable directory named `codex` on `PATH` (or a file with the execute
bit set that the kernel cannot exec) reaches the spawn and raises `PermissionError` /
`OSError(ENOEXEC)` out of a probe whose entire contract is to answer "is codex usable?".

WORKAROUND, pending upstream: briandconnelly/pontonier#16 asks that `run_sync_capture`
isolate the `Popen` phase from `communicate` so each can be classified on its own. Until
that ships and this repo's exact pin moves, the phase is not visible from out here --
`run_sync_capture` wraps both phases in one `try` -- so this module infers it from spawn
EVIDENCE and converts nothing without it. Delete this module's conversion (not
necessarily the module) once the upstream fix is pinned.
"""

from __future__ import annotations

import errno
import os
from typing import Any

from pontonier.core import runtime

# Errnos an `execve` can fail with, used ONLY when the exception carries no filename to
# reason about (see `_is_spawn_failure`). Enumerating exec errnos by hand is exactly the
# kind of list that goes stale -- ETXTBSY was missing from the first version of it -- which
# is why it is the fallback rule here rather than the primary one.
#   EACCES        a directory, or a file without the execute bit
#   EISDIR        a directory named directly
#   ENOEXEC       the execute bit is set on something the kernel cannot exec
#   ENOENT        no such path (upstream's own case)
#   ENOTDIR       a path component is not a directory (upstream's own case)
#   EPERM         exec denied by policy
#   ETXTBSY       the executable is open for writing (Linux/WSL, e.g. mid-install)
#   ELOOP         too many symlinks resolving the path
#   ENAMETOOLONG  the path exceeds the system limit
_SPAWN_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EISDIR,
        errno.ENOEXEC,
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EPERM,
        errno.ETXTBSY,
        errno.ELOOP,
        errno.ENAMETOOLONG,
    }
)


def _is_spawn_failure(exc: OSError, cmd: list[str]) -> bool:
    """Whether `exc` is evidence that the SPAWN failed, rather than a later I/O error.

    Deliberately conservative: a pipe/communicate `OSError` reported as `binary_missing`
    would hand the agent a repair step ("install codex") that cannot fix the real fault.

    Two rules, in order. A filename identifies what the error is ABOUT, so an error naming
    `cmd[0]` is a spawn failure whatever its errno, and one naming a different path is not
    ours to reclassify. Only when there is no filename does the errno decide, against the
    enumerated exec set -- the weaker rule, kept second because a hand-written errno list
    goes stale (it first shipped without ETXTBSY, and an exec against an executable open
    for writing therefore escaped).
    """
    target = cmd[0] if cmd else ""
    if exc.filename is not None:
        # CPython names the EXECUTABLE on an exec-phase failure (subprocess passes argv[0]
        # through as err_filename), while a later pipe/communicate error does not. So an
        # OSError naming the binary we asked to spawn is about that binary whatever its
        # errno -- which is the rule that matters, because it needs no correct guess about
        # which errnos an exec can produce.
        return os.fspath(exc.filename) == target
    # Nothing names the target, so fall back to the enumerated exec errnos above. A
    # communicate-phase failure (EIO, EPIPE) is absent from that set and propagates.
    return exc.errno in _SPAWN_ERRNOS


def run_probe(
    cmd: list[str],
    timeout_seconds: int,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
) -> runtime.CommandRun:
    """`runtime.run_sync_capture` with spawn failures kept as a `CommandRun` fact.

    Returns exactly what upstream returns, except that an escaping spawn-phase `OSError`
    becomes the same `binary_missing` `CommandRun` upstream already returns for a missing
    binary. Every other exception -- an `OSError` without spawn evidence, a `ValueError`
    from an argv-hostile value -- propagates untouched.
    """
    # Only the arguments the caller actually supplied are forwarded, so the upstream call
    # this shim makes is identical to the one the call site would have made on its own.
    optional: dict[str, Any] = {
        key: value
        for key, value in (("cwd", cwd), ("env", env), ("stdin_text", stdin_text))
        if value is not None
    }
    try:
        return runtime.run_sync_capture(cmd, timeout_seconds=timeout_seconds, **optional)
    except OSError as exc:
        if not _is_spawn_failure(exc, cmd):
            raise
        # The same shape upstream builds for a missing binary. elapsed_ms is reported as 0
        # rather than measured: a spawn that never happened has no meaningful duration, and
        # callers branch on `binary_missing`, never on the timing of a failure.
        return runtime.CommandRun("", runtime.BINARY_NOT_FOUND, 127, 0, False)
