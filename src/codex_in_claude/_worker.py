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
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pontonier.core import redaction
from pontonier.core.jobs import ActivityRecorder

from codex_in_claude import config, delegate, orchestration
from codex_in_claude.errors import make_error, serialize_error
from codex_in_claude.schemas import (
    DeveloperInstructions,
    ErrorResult,
    Meta,
    RootsSource,
    Sandbox,
    Tier,
    workspace_warning_for,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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
        # Absent from a legacy (pre-#309) spec and from any run without an override
        # (the key is written only when an effort was set, preserving idempotency
        # hashes); .get() reads both as None.
        reasoning_effort=spec.get("reasoning_effort"),
        # The audit fingerprint for the caller's developer instructions (#556). The
        # spec stores the NORMALIZED text (this worker needs it to build the run); the
        # delivered meta carries only {sha256, bytes}. Same .get() idiom: absent from
        # a pre-#556 spec and from any run without the parameter.
        developer_instructions=(
            # Normalize BEFORE hashing (Opus review): the backend re-normalizes what
            # it sends, so a hand-edited padded spec value must not attest different
            # bytes than the run received; server-produced specs are already
            # normalized, so this is identity for them.
            DeveloperInstructions.of(_di)
            if (_di := config.normalize_developer_instructions(spec.get("developer_instructions")))
            else None
        ),
        # The roots state the ORIGINATING call saw (#393). It reaches a caller only via
        # this spec round-trip: a delivered success/crash envelope is built here, not
        # from the meta the handler prepared. Absent from a pre-#393 spec; .get() reads
        # that as None, which slims away on delivery — no migration.
        roots_source=cast("RootsSource | None", spec.get("roots_source")),
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


def _invalid_arguments_conformant(error: dict) -> bool:
    """True when an `invalid_arguments` error dict satisfies BOTH halves of the contract
    `errors.make_error` enforces at construction (errors.py, the `code ==
    "invalid_arguments"` branch): a non-empty `invalid_arguments` list, AND `details`
    exactly mirroring entry [0]'s `field`/`reason`/`allowed_values`. A list-only check
    would pass a list-bearing envelope whose `details` is absent or disagrees with
    entry [0] — the exact drift this guard exists to catch, just moved into `details`
    instead of the list (#419 follow-up)."""
    invalid_arguments = error.get("invalid_arguments")
    if not isinstance(invalid_arguments, list) or not invalid_arguments:
        return False
    first = invalid_arguments[0]
    if not isinstance(first, dict):
        return False
    details = error.get("details")
    if not isinstance(details, dict):
        return False
    return all(details.get(key) == first.get(key) for key in ("field", "reason", "allowed_values"))


def _guard_invalid_arguments(payload: dict, spec: dict) -> dict:
    """Structural guard at the persistence boundary (#419).

    `errors.make_error` enforces the `invalid_arguments` contract (a non-empty
    per-argument list, with `details` mirroring entry [0]) for every envelope the SERVER
    builds, but a worker-executed path writes whatever `_run` returns straight to
    result.json with no validation — and replay (server.py) reconstructs stored records
    via `ErrorResult.model_validate`, deliberately bypassing that constructor guard so
    pre-existing records stay readable (see errors.make_error's docstring). So a worker
    path that ever produced a nonconformant `invalid_arguments` envelope would persist
    it, and replay would return it stamped with the CURRENT fingerprint — advertising a
    conformance it does not have. This catches that here, the last point before the
    bytes are written.

    Replaces a nonconformant `invalid_arguments` envelope (see
    `_invalid_arguments_conformant`) with a conformant `internal_error` one, preserving
    the job meta the original payload already carried (mirrors the crash sink above:
    `serialize_error(ErrorResult(error=make_error(...), meta=...))`). Any other payload
    passes through unchanged.

    Never raises: the payload's shape is of unknown provenance by the time it reaches
    here, so this navigates it defensively (`.get` chains, no indexing) and wraps the
    whole check in a broad `except Exception` — a broken guard must not be worse than no
    guard, since the crash sink above and this call both exist to guarantee a job always
    leaves *some* record. Any failure here falls through to the original payload as-is.
    """
    try:
        if payload.get("ok") is not False:
            return payload
        error = payload.get("error")
        if not isinstance(error, dict) or error.get("code") != "invalid_arguments":
            return payload
        if _invalid_arguments_conformant(error):
            return payload
        meta_payload = payload.get("meta")
        meta = Meta.model_validate(meta_payload) if isinstance(meta_payload, dict) else None
        if meta is None:
            meta = _meta_from_spec(spec)
        return serialize_error(
            ErrorResult(
                error=make_error(
                    "internal_error",
                    "background worker produced a nonconformant invalid_arguments envelope",
                ),
                meta=meta,
            )
        )
    except Exception:
        return payload


def _write_cleanup_manifest(job_dir: Path, parent: str) -> None:
    """Record the temp worktree parent so the JobStore can remove it if this worker
    is hard-killed before its own cleanup runs (see jobs.JobStore cleanup_root)."""
    _atomic_write(job_dir / "cleanup.json", {"paths": [parent]})


def _activity_observer(
    job_dir: Path,
) -> tuple[Callable[[str], None], ActivityRecorder]:
    """An observer for Codex's --json stdout stream that records event ACTIVITY only.

    A line counts as an event when it parses as a JSON object — tolerant (no
    dependence on a specific event *shape*), but a malformed line that merely starts
    with "{" does not inflate the count. Raw lines are never persisted; the recorder
    writes counts/timestamps to <job_dir>/activity.json."""
    recorder = ActivityRecorder(job_dir)

    def _observe(line: str) -> None:
        text = line.strip()
        if not text or text[0] != "{":
            return  # cheap pre-filter before the parse
        try:
            event = json.loads(text)
        except ValueError:
            return  # not valid JSON — don't count it
        if isinstance(event, dict):
            recorder.record(time.time())

    return _observe, recorder


async def _run(job_dir: Path, spec: dict, meta: Meta) -> dict:
    """Dispatch the job by kind, cancelling cleanly on SIGTERM so an in-flight
    `codex exec` (and, for delegate, the worktree teardown) is torn down. The
    JobStore sends SIGTERM (then SIGKILL after a grace) to cancel or time out."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
        loop.add_signal_handler(signal.SIGTERM, task.cancel)

    on_event, recorder = _activity_observer(job_dir)
    try:
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
                reasoning_effort=spec.get("reasoning_effort"),
                git_timeout=spec["git_timeout"],
                max_diff_bytes=spec.get("max_diff_bytes"),
                on_worktree_parent=lambda parent: _write_cleanup_manifest(job_dir, parent),
                on_event=on_event,
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
                reasoning_effort=spec.get("reasoning_effort"),
                developer_instructions=spec.get("developer_instructions"),
                extra_context=spec.get("extra_context", ""),
                on_event=on_event,
            )
        if kind == "codex_review_changes":
            return await orchestration.run_review(
                spec["cwd"],
                meta,
                scope=spec["scope"],
                base=spec.get("base"),
                commit=spec.get("commit"),
                paths=spec.get("paths"),
                # Pre-#319 specs lack this key; default preserves their behavior.
                untracked=spec.get("untracked", "explicit_only"),
                sandbox=spec["sandbox"],
                isolation=spec["isolation"],
                timeout_seconds=spec["timeout_seconds"],
                model=spec.get("model"),
                reasoning_effort=spec.get("reasoning_effort"),
                developer_instructions=spec.get("developer_instructions"),
                git_timeout=spec["git_timeout"],
                max_bytes=spec["max_bytes"],
                extra_context=spec.get("extra_context", ""),
                on_event=on_event,
            )
        raise ValueError(f"unknown job kind: {kind!r}")
    finally:
        recorder.flush()


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
        payload = serialize_error(
            ErrorResult(
                error=make_error(
                    "internal_error",
                    f"background worker crashed: {redaction.exc_summary(exc)}"[:300],
                ),
                meta=_meta_from_spec(spec),
            )
        )
    _atomic_write(job_dir / "result.json", _guard_invalid_arguments(payload, spec))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
