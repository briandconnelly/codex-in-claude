"""Detached background worker for the propose tier.

Invoked as ``python -m codex_in_claude._worker <job_dir>`` by the JobStore. Reads
``<job_dir>/spec.json``, runs the propose orchestration (worktree → codex exec →
diff → cleanup) via :func:`codex_in_claude.delegate.run_delegate`, and writes the
final result envelope to ``<job_dir>/result.json`` (atomically). It is import-light
— it does NOT construct the FastMCP app.

The worker always tries to leave a readable envelope: an unexpected crash before
writing result.json is reported by the JobStore as ``failed`` instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path
from typing import cast

from codex_in_claude import delegate, orchestration
from codex_in_claude.schemas import (
    ErrorCode,
    ErrorInfo,
    ErrorResult,
    Meta,
    Sandbox,
    Tier,
    workspace_warning_for,
)

# Open fds whose flock keeps each per-job lock held for this process's whole life;
# the OS releases them on exit. A list (not a `global` rebind) so the JobStore can
# verify THIS worker is alive independently of the PID.
_held_locks: list[int] = []


def _hold_job_lock(job_dir: Path) -> None:
    """Take an exclusive advisory lock on ``<job_dir>/worker.lock`` and keep it for
    this process's lifetime. PID reuse after a server restart can't hold this job's
    lock, so the JobStore can tell our worker apart from an unrelated process on the
    same (reused) PID. Best-effort: a platform without ``fcntl`` simply skips it."""
    try:
        import fcntl  # noqa: PLC0415 - platform-guarded lazy import (POSIX only)
    except ImportError:  # pragma: no cover - non-POSIX
        return
    with contextlib.suppress(OSError):
        fd = os.open(str(job_dir / "worker.lock"), os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # pragma: no cover - unexpected contention on our own job lock
            os.close(fd)
            return
        _held_locks.append(fd)  # kept open == lock held until this process exits


def _meta_from_spec(spec: dict) -> Meta:
    cwd = spec["cwd"]
    source = spec.get("workspace_source")
    return Meta(
        cwd=cwd,
        workspace_source=source,
        workspace_warning=workspace_warning_for(source, cwd),
        # tier/sandbox come from the spec: delegate runs propose/workspace-write,
        # consult/review run consult/read-only.
        tier=cast("Tier", spec.get("tier", "propose")),
        sandbox=cast("Sandbox", spec["sandbox"]),
        isolation=spec["isolation"],
        model=spec.get("model"),
        timeout_seconds=spec["timeout_seconds"],
        elapsed_ms=0,
        scope=spec.get("scope"),
        base=spec.get("base"),
        commit=spec.get("commit"),
        paths=spec.get("paths"),
    )


def _atomic_write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def _write_cleanup_manifest(job_dir: Path, parent: str) -> None:
    """Record the temp worktree parent so the JobStore can remove it if this worker
    is hard-killed before its own cleanup runs (see jobs.JobStore cleanup_root)."""
    _atomic_write(job_dir / "cleanup.json", {"paths": [parent]})


async def _run(job_dir: Path, spec: dict, meta: Meta) -> dict:
    """Dispatch the job by kind, cancelling cleanly on SIGTERM so an in-flight
    `codex exec` (and, for delegate, the worktree teardown) is torn down. The
    JobStore sends SIGTERM (then SIGKILL after a grace) to cancel or time out."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
        loop.add_signal_handler(signal.SIGTERM, task.cancel)

    kind = spec.get("kind")
    if kind == "codex_delegate":
        return await delegate.run_delegate(
            spec["task"],
            spec["cwd"],
            meta,
            sandbox=spec["sandbox"],
            isolation=spec["isolation"],
            timeout_seconds=spec["timeout_seconds"],
            model=spec.get("model"),
            git_timeout=spec["git_timeout"],
            max_diff_bytes=spec.get("max_diff_bytes"),
            on_worktree_parent=lambda parent: _write_cleanup_manifest(job_dir, parent),
        )
    if kind == "codex_consult":
        return await orchestration.run_consult(
            spec["question"],
            spec["cwd"],
            meta,
            sandbox=spec["sandbox"],
            isolation=spec["isolation"],
            timeout_seconds=spec["timeout_seconds"],
            model=spec.get("model"),
            extra_context=spec.get("extra_context", ""),
        )
    if kind == "codex_review_changes":
        return await orchestration.run_review(
            spec["cwd"],
            meta,
            scope=spec["scope"],
            base=spec.get("base"),
            commit=spec.get("commit"),
            paths=spec.get("paths"),
            sandbox=spec["sandbox"],
            isolation=spec["isolation"],
            timeout_seconds=spec["timeout_seconds"],
            model=spec.get("model"),
            git_timeout=spec["git_timeout"],
            max_bytes=spec["max_bytes"],
            extra_context=spec.get("extra_context", ""),
        )
    raise ValueError(f"unknown job kind: {kind!r}")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        return 2
    job_dir = Path(args[0])
    _hold_job_lock(job_dir)
    spec = json.loads((job_dir / "spec.json").read_text())
    try:
        meta = _meta_from_spec(spec)
        payload = asyncio.run(_run(job_dir, spec, meta))
    except asyncio.CancelledError:
        # Graceful termination (cancel/timeout): run_delegate's finally already tore
        # down the worktree, and the JobStore owns the terminal status — leave no
        # result.json behind.
        return 0
    except Exception as exc:
        payload = ErrorResult(
            error=ErrorInfo(
                code=cast("ErrorCode", "internal_error"),
                message=f"background worker crashed: {exc}"[:300],
                repair="Retry the job; if it persists, run codex_status and inspect the repo.",
                retryable=True,
            ),
            meta=_meta_from_spec(spec),
        ).model_dump(mode="json")
    _atomic_write(job_dir / "result.json", payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
