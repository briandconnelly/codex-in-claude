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

# Errnos a failed *spawn* produces. ENOENT/ENOTDIR are already upstream's own two cases
# and are listed for completeness, not because they can reach the conversion below.
#   EACCES  the path is a directory, or a file without the execute bit
#   EISDIR  a directory named directly
#   ENOEXEC the execute bit is set on something the kernel cannot exec
#   EPERM   an exec denied by policy
_SPAWN_ERRNOS = frozenset(
    {errno.EACCES, errno.EISDIR, errno.ENOEXEC, errno.ENOENT, errno.ENOTDIR, errno.EPERM}
)


def _is_spawn_failure(exc: OSError, cmd: list[str]) -> bool:
    """Whether `exc` is evidence that the SPAWN failed, rather than a later I/O error.

    Deliberately conservative. A pipe/communicate `OSError` reported as `binary_missing`
    would hand the agent a repair step ("install codex") that cannot fix the real fault,
    so conversion requires an errno a spawn actually produces AND a filename that does
    not contradict `cmd[0]`. CPython does not always populate `filename` on the exec-phase
    error; an absent one is treated as non-contradicting, which is the safe direction --
    the alternative is a raw crash out of a probe documented never to raise.
    """
    if exc.errno not in _SPAWN_ERRNOS:
        return False
    if exc.filename is None:
        return True
    target = cmd[0] if cmd else ""
    return os.fspath(exc.filename) == target


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
