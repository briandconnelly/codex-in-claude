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
"""

from __future__ import annotations

import contextlib
import hashlib
import json
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

CmdFactory = Callable[[Path], list[str]]


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


def _kill_pid_tree(pid: int | None) -> None:
    """Kill the detached job's process group, then reap it if it was our child so
    it does not linger as a zombie."""
    if not pid:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, 0)


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

    poll_after_ms: int = 1000

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
    def _status_of(self, jd: Path, meta: dict) -> str:
        """Compute the live status, killing + marking jobs that overran."""
        terminal = meta.get("terminal_status")
        if terminal:
            return terminal
        if _is_running(meta.get("pid")):
            if time.time() > meta.get("deadline_epoch", float("inf")):
                _kill_pid_tree(meta.get("pid"))
                meta["terminal_status"] = "timeout"
                meta["completed_epoch"] = time.time()
                self._write_meta(jd, meta)
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
        return {
            "job_id": meta.get("job_id", jd.name),
            "kind": meta.get("kind", ""),
            "status": state,
            "started_at": meta.get("started_at", ""),
            "started_epoch": meta.get("started_epoch", 0.0),
            "elapsed_ms": self._elapsed_ms(meta),
            "deadline_seconds": self._deadline_seconds(meta),
            "completed_epoch": meta.get("completed_epoch"),
            "expires_at": self._expires_at(meta),
            "result_available": state == "done",
            "poll_after_ms": self.poll_after_ms,
            "ttl_seconds": self.ttl_seconds,
            "extra": meta.get("extra", {}),
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
            if state not in _TERMINAL:
                _kill_pid_tree(meta.get("pid"))
                meta["terminal_status"] = "cancelled"
                meta["completed_epoch"] = time.time()
                self._write_meta(jd, meta)
                state = "cancelled"
            return self._status_dict(jd, meta, state)

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
