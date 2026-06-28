"""Generic, disk-backed background-job lifecycle.

This module is part of ``_core`` and MUST NOT import from the parent package: it
takes all configuration (state root, TTL, deadline, count cap) as parameters so it
can later be extracted into a shared ``agent-bridge`` package.

A job is an arbitrary command spawned detached (its own session leader). The
command is expected to write a final, already-normalized result envelope to
``result.json`` *in its own job directory* (the command is run with
``cwd=<job_dir>``). This store therefore treats ``result.json`` as opaque: a job is
``done`` when the process is gone and ``result.json`` parses to a JSON object;
otherwise the process exiting without one means ``failed``. Cancel/deadline reaps
mark ``cancelled``/``timeout``.

State lives on disk keyed by workspace, so status/result/cancel survive MCP server
restarts. There is no daemon: single-job calls refresh and TTL-clean the requested
record, list calls clean the whole workspace, and the count cap is enforced when
jobs start.

Restart recovery: after a restart a prior worker is no longer this process's child,
so a persisted PID alone cannot be trusted — a PID reused by an unrelated process
must never be mistaken for the worker or signaled. Each worker therefore holds an
exclusive advisory lock on ``<job_dir>/worker.lock`` for its whole life; the store
treats that lock as the authority for liveness (a reused PID cannot hold this job's
lock). A PID is trusted without the lock only for jobs this instance itself started
(its own children); an unowned record whose lock is absent/indeterminate is treated
as not-running rather than signaled, so cancel/timeout never touch an unrelated
process. The lock needs a local filesystem to be reliable and uses POSIX ``fcntl``;
platforms without it degrade to the owned-child check (and never signal unowned PIDs).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_TERMINAL = frozenset({"done", "failed", "cancelled", "timeout"})
_LOCK = threading.RLock()

# Per-process identity stamped onto every job this process starts and compared on
# read to decide ownership. Generated ONCE at import, so it is stable across the many
# short-lived JobStore instances the server builds (one per tool call) and changes
# only across a real restart (a new process re-imports this module). Persisted into
# meta.json; a record whose owner differs was started by some other/earlier process.
_PROCESS_OWNER = uuid4().hex

# Default poll/backoff interval (ms) a job advertises to clients. The single source
# for this value; the parent package re-exports it as the agent-visible constant.
DEFAULT_POLL_AFTER_MS = 1000
# Upper bound for the growing poll backoff (ms). A delegate often runs ~20s, so the
# hint ramps up to here rather than staying at the flat default and inviting ~20 polls.
MAX_POLL_AFTER_MS = 10000
# Min seconds between activity.json disk writes while a job runs; the first event
# and the final flush always write. Keeps the hot path off the disk on every line.
ACTIVITY_WRITE_THROTTLE_S = 0.5

CmdFactory = Callable[[Path], list[str]]


def _usable_epoch(epoch: int | float) -> bool:
    """True if ``epoch`` is finite AND in range for datetime.fromtimestamp().

    json.loads accepts NaN/Infinity, and a finite epoch can still be out of range
    (1e308 raises OverflowError/OSError/ValueError depending on the platform). The
    cheap finiteness check rejects the common corrupt case; the conversion probe
    catches the rare out-of-range finite value so a corrupt activity.json can never
    crash the job-status API."""
    if not math.isfinite(epoch):
        return False
    try:
        datetime.fromtimestamp(epoch, UTC)
    except (OverflowError, OSError, ValueError):
        return False
    return True


def poll_backoff_ms(
    elapsed_ms: int,
    *,
    base: int = DEFAULT_POLL_AFTER_MS,
    cap: int = MAX_POLL_AFTER_MS,
) -> int:
    """Suggested delay (ms) before the next codex_job_status poll for a running job.

    Grows with how long the job has already run — roughly "wait about as long as it
    has already taken" — so a polling agent naturally backs off instead of tight-
    looping at the flat ``base``. Bounded to ``[base, cap]``; if a caller passes a
    ``cap`` below ``base`` (e.g. a configured base above the default cap), the base
    wins so the result is never below the configured floor."""
    return min(max(base, elapsed_ms), max(base, cap))


@dataclass
class ActivityRecorder:
    """Persists a job's Codex event activity as counters/timestamps ONLY.

    The worker calls ``record`` per observed event and ``flush`` at the end. Writes
    are throttled and atomic (temp + replace) so a concurrent JobStore reader sees
    either the old or new file, never a torn one. Raw events are never written."""

    job_dir: Path
    _count: int = 0
    _last_epoch: float = 0.0
    _last_write: float = 0.0

    def record(self, now_epoch: float) -> None:
        self._count += 1
        self._last_epoch = now_epoch
        if self._count == 1 or (now_epoch - self._last_write) >= ACTIVITY_WRITE_THROTTLE_S:
            self._write(now_epoch)

    def flush(self) -> None:
        if self._count:
            self._write(self._last_epoch or time.time())

    def _write(self, now_epoch: float) -> None:
        payload = {"events_seen": self._count, "last_event_epoch": self._last_epoch}
        tmp = self.job_dir / "activity.json.tmp"
        with contextlib.suppress(OSError):
            tmp.write_text(json.dumps(payload))
            tmp.replace(self.job_dir / "activity.json")
            self._last_write = now_epoch


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_running(pid: int | None) -> bool:
    """Whether the job process is still running.

    The job is detached but still our child until it exits, so we reap it with
    waitpid — otherwise it lingers as a zombie that kill(0) reports as alive
    forever. waitpid(WNOHANG) returns (pid, _) once it exits (reaping it), (0, 0)
    while it runs, and raises ChildProcessError when it is not our child (e.g.
    after a server restart), where we fall back to a kill(0) liveness probe.
    """
    if not pid:
        return False
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
        if reaped == 0:
            return True
    except ChildProcessError:
        pass  # not our child — use the liveness probe below
    except OSError:
        return False
    return _pid_alive(pid)


def _worker_lock_held(lock_path: Path) -> bool | None:
    """Whether a live worker still holds the per-job lock at ``lock_path``.

    ``True`` = held, so the worker is alive AND positively identified — a PID reused
    by an unrelated process after a server restart cannot be holding *this* job's
    lock. ``False`` = free (no live holder). ``None`` = indeterminate: the lock file
    does not exist yet (worker still starting, or a job from before this hardening)
    or ``fcntl`` is unavailable. Advisory locks need a local filesystem to be
    reliable; on a network filesystem the result may be ``None``/unreliable, which
    callers treat conservatively (no signaling of unverified PIDs)."""
    try:
        import fcntl  # noqa: PLC0415 - platform-guarded lazy import (POSIX only)
    except ImportError:  # pragma: no cover - non-POSIX
        return None
    try:
        fd = os.open(str(lock_path), os.O_RDONLY)
    except OSError:
        return None  # missing (worker not yet locked) or unreadable
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True  # someone holds it -> the worker is alive
        except OSError:
            return None
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)  # we grabbed it -> no holder; let it go
        return False
    finally:
        os.close(fd)


def _signal_proc(pid: int, sig: int) -> None:
    """Send ``sig`` to the job's process group, but only when ``pid`` is its own group
    leader — our workers are session leaders (``start_new_session=True``, so
    ``pgid == pid``). If the PID was reused by a non-leader process we fall back to
    signaling just that single PID rather than its unrelated group, narrowing the
    residual window where a reused PID could be signaled."""
    try:
        leader = hasattr(os, "killpg") and os.getpgid(pid) == pid
    except (ProcessLookupError, OSError):
        return  # already gone / not signalable
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if leader:
            os.killpg(pid, sig)
        else:  # pragma: no cover - reused non-leader PID / non-POSIX
            os.kill(pid, sig)


def _kill_pid_tree(pid: int | None) -> None:
    """SIGKILL the detached job via :func:`_signal_proc` (its process group when
    ``pid`` is the group leader — our workers are — else just that PID), then reap
    it if it was our child so it does not linger as a zombie."""
    if not pid:
        return
    _signal_proc(pid, signal.SIGKILL)
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, 0)


def _terminate_pid_tree(
    pid: int | None,
    grace_seconds: float,
    *,
    is_alive: Callable[[], bool] | None = None,
) -> None:
    """Stop the detached job's process group gracefully, then force-kill if it
    overstays.

    A plain SIGKILL (``_kill_pid_tree``) gives the worker no chance to run its own
    cleanup — for the propose tier that means a leaked temp worktree. So we send
    SIGTERM first and poll for up to ``grace_seconds`` (the worker cancels its run,
    tears down its worktree, and exits), then SIGKILL any survivor. Either way the
    process is reaped so it cannot linger as a zombie.

    ``is_alive`` is the liveness predicate used throughout — including before the
    initial SIGTERM and before the SIGKILL escalation. Callers pass a lock-aware
    check (``_job_running``) so that if the verified worker exits during the grace
    window and its PID is then reused, we do not signal the unrelated process. It
    defaults to the bare existence probe for direct/legacy callers."""
    if not pid:
        return
    alive = is_alive if is_alive is not None else (lambda: _is_running(pid))
    if not alive():  # already gone (or no longer the verified worker)
        return
    _signal_proc(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        if not alive():  # exited gracefully (and was reaped)
            return
        time.sleep(0.02)
    if alive():  # still the verified worker after grace — force-kill it
        _kill_pid_tree(pid)


@dataclass
class JobStore:
    """Disk-backed job lifecycle rooted at ``root``.

    ttl_seconds: how long a terminal record is kept after completion.
    max_seconds: a job's wall-clock cap (a status poll past it reaps the job).
    max_count: retained records per workspace (oldest terminal evicted first).
    """

    root: Path
    ttl_seconds: int
    max_seconds: int
    max_count: int

    poll_after_ms: int = DEFAULT_POLL_AFTER_MS

    # External paths a job declares it owns (in <job_dir>/cleanup.json) are removed
    # when the job is cancelled/timed out, but only when they resolve strictly
    # inside ``cleanup_root`` and (if set) carry ``cleanup_prefix`` — a guard so a
    # malformed manifest can never delete outside the throwaway-worktree temp area.
    cleanup_root: Path | None = None
    cleanup_prefix: str = ""
    terminate_grace_seconds: float = 5.0

    # ------------------------------------------------------------------ paths
    def _ws_dir(self, cwd: str) -> Path:
        canonical = os.path.realpath(cwd)
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
        base = os.path.basename(canonical.rstrip("/")) or "workspace"  # noqa: PTH119
        safe = "".join(c if (c.isalnum() or c in "._-") else "-" for c in base)[:40] or "ws"
        return self.root / f"{safe}-{digest}"

    def _job_dir(self, cwd: str, job_id: str) -> Path:
        return self._ws_dir(cwd) / job_id

    # --------------------------------------------------------------- meta i/o
    @staticmethod
    def _read_meta(jd: Path) -> dict | None:
        try:
            return json.loads((jd / "meta.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_meta(jd: Path, meta: dict) -> None:
        (jd / "meta.json").write_text(json.dumps(meta))

    @staticmethod
    def _read_envelope(jd: Path) -> dict | None:
        """Parse the final result envelope from result.json, or None if absent/
        partial/non-object."""
        try:
            text = (jd / "result.json").read_text()
        except OSError:
            return None
        text = text.strip()
        if not text:
            return None
        try:
            env = json.loads(text)
        except json.JSONDecodeError:
            return None
        return env if isinstance(env, dict) else None

    @staticmethod
    def _read_cleanup_manifest(jd: Path) -> list[str]:
        """External paths a job declared it owns, from <job_dir>/cleanup.json.

        The store treats this as opaque caller-declared state: it never learns the
        paths are git worktrees, only that the job asked for them to be removed when
        it is reaped. Missing/garbage manifest -> no declared paths."""
        try:
            data = json.loads((jd / "cleanup.json").read_text())
        except (OSError, json.JSONDecodeError):
            return []
        paths = data.get("paths") if isinstance(data, dict) else None
        return [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []

    @staticmethod
    def _read_activity(jd: Path) -> tuple[int, float | None]:
        """(events_seen, last_event_epoch) from activity.json; (0, None) if absent/
        corrupt. Treated as opaque caller-declared state, like cleanup.json.

        Values are validated, not trusted: a non-int/negative count degrades to 0,
        and an unusable epoch degrades to None — otherwise the downstream
        datetime.fromtimestamp()/int() in _status_dict would raise and take the whole
        job-status API down with a corrupt file. An epoch is unusable if it is
        non-finite (json.loads accepts NaN/Infinity) OR finite but out of range for
        datetime.fromtimestamp() (e.g. 1e308 raises OverflowError/OSError/ValueError
        depending on the platform)."""
        try:
            data = json.loads((jd / "activity.json").read_text())
        except (OSError, json.JSONDecodeError):
            return 0, None
        if not isinstance(data, dict):
            return 0, None
        count = data.get("events_seen")
        epoch = data.get("last_event_epoch")
        count = (
            count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 0
        )
        is_number = isinstance(epoch, (int, float)) and not isinstance(epoch, bool)
        epoch = epoch if is_number and _usable_epoch(epoch) else None
        return count, epoch

    def _within_cleanup_root(self, path: str) -> bool:
        if self.cleanup_root is None:
            return False
        # The manifest is opaque caller-declared state; a path that cannot be
        # resolved (symlink loop, permission error) is treated as outside the root
        # and refused rather than crashing cleanup.
        try:
            root = Path(self.cleanup_root).resolve()
            target = Path(path).resolve()
        except OSError:
            return False
        if not target.name.startswith(self.cleanup_prefix):
            return False
        return target != root and target.is_relative_to(root)

    def _cleanup_external_paths(self, paths: list[str]) -> list[str]:
        """Remove a reaped job's declared external paths, refusing anything not
        strictly inside ``cleanup_root``. Returns warnings for paths that were
        refused or survived removal (so a caller can surface the leak)."""
        if self.cleanup_root is None:
            return []  # external cleanup not configured; the store made no promise
        warnings: list[str] = []
        for path in paths:
            if not self._within_cleanup_root(path):
                warnings.append(f"refused to remove path outside the cleanup root: {path}")
                continue
            shutil.rmtree(path, ignore_errors=True)
            if Path(path).exists():
                warnings.append(f"could not remove temporary path: {path}")
        return warnings

    def _finalize_cleanup(self, jd: Path, meta: dict, status: str, manifest: list[str]) -> None:
        """Mark the record terminal and remove any external paths it declared,
        recording cleanup warnings in meta so status/result can surface them."""
        warnings = self._cleanup_external_paths(manifest)
        meta["terminal_status"] = status
        meta["completed_epoch"] = time.time()
        if warnings:
            meta["cleanup_warnings"] = warnings
        self._write_meta(jd, meta)

    @staticmethod
    def _rmtree(jd: Path) -> None:
        try:
            for child in jd.iterdir():
                child.unlink(missing_ok=True)
            jd.rmdir()
        except OSError:
            pass

    # --------------------------------------------------------------- spawning
    def start(
        self,
        cmd_factory: CmdFactory,
        cwd: str,
        *,
        kind: str,
        extra: dict | None = None,
        write_spec: dict | None = None,
    ) -> tuple[str, str]:
        """Spawn ``cmd_factory(job_dir)`` detached and persist its record.

        The command runs with ``cwd=<job_dir>`` (so a relative ``result.json`` lands
        in the record). If ``write_spec`` is given, it is written to
        ``<job_dir>/spec.json`` before the command starts. Returns
        ``(job_id, started_at_iso)``.
        """
        with _LOCK:
            job_id = uuid4().hex
            jd = self._job_dir(cwd, job_id)
            jd.mkdir(parents=True, exist_ok=True)
            # Results can contain a diff; keep the workspace tree user-only.
            with contextlib.suppress(OSError):
                self._ws_dir(cwd).chmod(0o700)
            if write_spec is not None:
                (jd / "spec.json").write_text(json.dumps(write_spec))
            cmd = cmd_factory(jd)
            started = time.time()
            log_path = jd / "stderr.log"
            try:
                with log_path.open("w") as ef:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=str(jd),
                        stdin=subprocess.DEVNULL,
                        stdout=ef,
                        stderr=ef,
                        start_new_session=True,
                    )
            except OSError:
                shutil.rmtree(jd, ignore_errors=True)
                raise
            meta = {
                "job_id": job_id,
                "kind": kind,
                "pid": proc.pid,
                "owner": _PROCESS_OWNER,
                "started_epoch": started,
                "started_at": datetime.now(UTC).isoformat(),
                "deadline_epoch": started + self.max_seconds,
                "completed_epoch": None,
                "terminal_status": None,
                "extra": extra or {},
            }
            self._write_meta(jd, meta)
            self._enforce_count_cap(cwd)
            return job_id, meta["started_at"]

    # ------------------------------------------------------------ status calc
    @staticmethod
    def _owned(meta: dict) -> bool:
        """Whether THIS process started the job (its worker is our own child)."""
        owner = meta.get("owner")
        return bool(owner) and owner == _PROCESS_OWNER

    def _job_running(self, jd: Path, meta: dict) -> bool:
        """Whether the job's worker is alive AND safe to signal.

        A held per-job lock is authoritative for ANY job: a PID reused by an unrelated
        process after a restart cannot hold it, so such a PID is never mistaken for the
        worker. For a job THIS process started (owned), the PID is our own child, so the
        existence probe (``_is_running``, which reaps) is authoritative — and stays
        correct during the worker's tiny create-then-``flock`` startup window, when the
        lock briefly reads as free. An unowned job is treated as running only while its
        worker still holds the lock; otherwise it is not running, so cancel/timeout
        never signal a stale or reused PID."""
        pid = meta.get("pid")
        if not pid:
            return False
        if _worker_lock_held(jd / "worker.lock") is True:
            return True  # positively verified alive (a reused PID can't hold this lock)
        if self._owned(meta):
            return _is_running(pid)  # our own child: the PID probe is authoritative
        return False  # unowned + no verified lock -> never reported running or signaled

    def _status_of(self, jd: Path, meta: dict) -> str:
        """Compute the live status, killing + marking jobs that overran."""
        terminal = meta.get("terminal_status")
        if terminal:
            return terminal
        if self._job_running(jd, meta):
            if time.time() > meta.get("deadline_epoch", float("inf")):
                _terminate_pid_tree(
                    meta.get("pid"),
                    self.terminate_grace_seconds,
                    is_alive=lambda: self._job_running(jd, meta),
                )
                # The worker may have completed during the grace window — prefer its
                # result over masking it as a timeout (result-first finalization).
                if self._read_envelope(jd) is not None:
                    if meta.get("completed_epoch") is None:
                        meta["completed_epoch"] = time.time()
                        self._write_meta(jd, meta)
                    return "done"
                self._finalize_cleanup(jd, meta, "timeout", self._read_cleanup_manifest(jd))
                return "timeout"
            return "running"
        if meta.get("completed_epoch") is None:
            meta["completed_epoch"] = time.time()
            self._write_meta(jd, meta)
        return "done" if self._read_envelope(jd) is not None else "failed"

    @staticmethod
    def _elapsed_ms(meta: dict) -> int:
        end = meta.get("completed_epoch") or time.time()
        return max(0, int((end - meta.get("started_epoch", end)) * 1000))

    def _deadline_seconds(self, meta: dict) -> int:
        """The window the job was STARTED with (deadline minus start), not the
        current config — so status stays consistent if config later changes."""
        started = meta.get("started_epoch")
        deadline = meta.get("deadline_epoch")
        if started is not None and deadline is not None:
            return max(0, round(deadline - started))
        return self.max_seconds

    def _expires_at(self, meta: dict) -> str | None:
        completed = meta.get("completed_epoch")
        if completed is None:
            return None
        return datetime.fromtimestamp(completed + self.ttl_seconds, UTC).isoformat()

    def _expired(self, meta: dict) -> bool:
        completed = meta.get("completed_epoch")
        if completed is None:
            return False
        return time.time() - completed > self.ttl_seconds

    def _status_dict(self, jd: Path, meta: dict, state: str) -> dict:
        elapsed_ms = self._elapsed_ms(meta)
        # Running jobs get a growing poll hint so a polling agent backs off; terminal
        # jobs don't need polling, so the flat base is fine there.
        poll = (
            poll_backoff_ms(elapsed_ms, base=self.poll_after_ms)
            if state == "running"
            else self.poll_after_ms
        )
        events_seen, last_epoch = self._read_activity(jd)
        end = meta.get("completed_epoch") or time.time()
        last_event_at = (
            datetime.fromtimestamp(last_epoch, UTC).isoformat() if last_epoch is not None else None
        )
        event_age_ms = max(0, int((end - last_epoch) * 1000)) if last_epoch is not None else None
        return {
            "job_id": meta.get("job_id", jd.name),
            "kind": meta.get("kind", ""),
            "status": state,
            "started_at": meta.get("started_at", ""),
            "started_epoch": meta.get("started_epoch", 0.0),
            "elapsed_ms": elapsed_ms,
            "deadline_seconds": self._deadline_seconds(meta),
            "completed_epoch": meta.get("completed_epoch"),
            "expires_at": self._expires_at(meta),
            "result_available": state == "done",
            "poll_after_ms": poll,
            "ttl_seconds": self.ttl_seconds,
            "cleanup_warnings": meta.get("cleanup_warnings", []),
            "extra": meta.get("extra", {}),
            "events_seen": events_seen,
            "last_event_at": last_event_at,
            "event_age_ms": event_age_ms,
        }

    # ----------------------------------------------------------- maintenance
    def _read_live_job(self, cwd: str, job_id: str) -> tuple[Path, dict, str] | None:
        """Read + refresh a single record; drop it if terminal and expired."""
        jd = self._job_dir(cwd, job_id)
        meta = self._read_meta(jd)
        if meta is None:
            return None
        state = self._status_of(jd, meta)
        if state in _TERMINAL and self._expired(meta):
            self._rmtree(jd)
            return None
        return jd, meta, state

    def _reap_workspace(self, cwd: str) -> None:
        ws = self._ws_dir(cwd)
        if not ws.is_dir():
            return
        now = time.time()
        for jd in ws.iterdir():
            if not jd.is_dir():
                continue
            meta = self._read_meta(jd)
            if meta is None:
                continue
            state = self._status_of(jd, meta)
            if state in _TERMINAL:
                end = meta.get("completed_epoch") or meta.get("started_epoch") or now
                if now - end > self.ttl_seconds:
                    self._rmtree(jd)

    def _enforce_count_cap(self, cwd: str) -> None:
        ws = self._ws_dir(cwd)
        dirs = [d for d in ws.iterdir() if d.is_dir()] if ws.is_dir() else []
        if len(dirs) <= self.max_count:
            return
        scored = []
        for jd in dirs:
            meta = self._read_meta(jd) or {}
            state = self._status_of(jd, meta)
            scored.append((state in _TERMINAL, meta.get("started_epoch", 0.0), jd))
        scored.sort(key=lambda t: (not t[0], t[1]))  # terminal first, then oldest
        for is_terminal, _epoch, jd in scored[: max(0, len(dirs) - self.max_count)]:
            if is_terminal:  # never kill a still-running job to make room
                self._rmtree(jd)

    # -------------------------------------------------------------- public API
    def status(self, cwd: str, job_id: str) -> dict | None:
        with _LOCK:
            live = self._read_live_job(cwd, job_id)
            if live is None:
                return None
            jd, meta, state = live
            return self._status_dict(jd, meta, state)

    def result_payload(
        self, cwd: str, job_id: str, *, consume: bool
    ) -> tuple[dict | None, dict | None]:
        """Return (status_dict, result_envelope).

        status_dict is None when the job does not exist. result_envelope is the
        parsed result.json (only when status == done), else None. With
        ``consume=True`` a done record is deleted after reading.
        """
        with _LOCK:
            live = self._read_live_job(cwd, job_id)
            if live is None:
                return None, None
            jd, meta, state = live
            rec = self._status_dict(jd, meta, state)
            if state != "done":
                return rec, None
            payload = self._read_envelope(jd)
            if consume:
                self._rmtree(jd)
            return rec, payload

    def cancel(self, cwd: str, job_id: str) -> dict | None:
        with _LOCK:
            live = self._read_live_job(cwd, job_id)
            if live is None:
                return None
            jd, meta, state = live
            if state in _TERMINAL:
                return self._status_dict(jd, meta, state)
            pid = meta.get("pid")
        # Terminate with the lock released: the graceful-shutdown grace wait must
        # not block status/list/result calls for every workspace. Liveness stays
        # lock-aware throughout, so a worker that exits mid-grace (and a PID then
        # reused) is never signaled.
        _terminate_pid_tree(
            pid,
            self.terminate_grace_seconds,
            is_alive=lambda m=meta: self._job_running(jd, m),
        )
        with _LOCK:
            # Re-validate: during the unlocked grace window the record may have been
            # consumed/evicted, finalized by another path, or the worker may have
            # finished on its own. Re-read fresh state and never clobber it.
            meta = self._read_meta(jd)
            if meta is None:
                return None  # consumed or expired while we waited
            terminal = meta.get("terminal_status")
            if terminal is None and self._read_envelope(jd) is not None:
                # The worker completed during the window — preserve its result
                # rather than masking it as cancelled.
                meta["completed_epoch"] = meta.get("completed_epoch") or time.time()
                meta["terminal_status"] = terminal = "done"
                self._write_meta(jd, meta)
            if terminal is not None:
                return self._status_dict(jd, meta, terminal)
            # Re-read the manifest now: the worker may have declared its worktree
            # only after cancellation began (e.g. it was still creating it), so a
            # snapshot taken before termination could miss the path and leak it.
            manifest = self._read_cleanup_manifest(jd)
            self._finalize_cleanup(jd, meta, "cancelled", manifest)
            return self._status_dict(jd, meta, "cancelled")

    def list_jobs(self, cwd: str) -> list[dict]:
        with _LOCK:
            self._reap_workspace(cwd)
            ws = self._ws_dir(cwd)
            summaries: list[dict] = []
            if ws.is_dir():
                for jd in ws.iterdir():
                    if not jd.is_dir():
                        continue
                    meta = self._read_meta(jd)
                    if meta is None:
                        continue
                    state = self._status_of(jd, meta)
                    summaries.append(self._status_dict(jd, meta, state))
            summaries.sort(key=lambda s: s["started_epoch"], reverse=True)  # newest first
            return summaries
