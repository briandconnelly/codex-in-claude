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
import signal
import sys
from pathlib import Path
from typing import cast

from codex_in_claude import delegate
from codex_in_claude.schemas import (
    ErrorCode,
    ErrorInfo,
    ErrorResult,
    Meta,
    Sandbox,
    workspace_warning_for,
)


def _meta_from_spec(spec: dict) -> Meta:
    cwd = spec["cwd"]
    source = spec.get("workspace_source")
    return Meta(
        cwd=cwd,
        workspace_source=source,
        workspace_warning=workspace_warning_for(source, cwd),
        tier="propose",
        sandbox=cast("Sandbox", spec["sandbox"]),
        isolation=spec["isolation"],
        model=spec.get("model"),
        timeout_seconds=spec["timeout_seconds"],
        elapsed_ms=0,
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
    """Run the delegate, cancelling cleanly on SIGTERM so run_delegate's worktree
    teardown runs. The JobStore sends SIGTERM (then SIGKILL after a grace) to
    cancel or time out the job."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
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


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        return 2
    job_dir = Path(args[0])
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
