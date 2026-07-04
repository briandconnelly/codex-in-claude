"""Disk-backed idempotency index for spend-committing runs (CLI-agnostic, _core).

A client passes an ``idempotency_key`` with a run; the index lets a retry after a
transport drop **replay** an existing run instead of starting (and paying for) a
duplicate. It sits beside the :class:`~codex_in_claude._core.jobs.JobStore` and is
driven by it — stdlib only, and (like everything in ``_core``) it never imports from
the parent package.

Dedup identity is (workspace, tool, argument-hash): the index lives in a per-workspace
directory the store owns, entries are keyed by ``sha256([tool, key])``, and each entry
records the ``arg_hash`` so the same key reused with *different* effective arguments is
a stable conflict rather than a mismatched replay.

Cross-process safety rests on ``O_EXCL``: exactly one creator wins a given entry. The
loser reads the winner's record and classifies it (replay / conflict / result-gone /
in-progress). A reserved-but-unpublished entry is treated as *in progress* and fails
closed — never silently re-run — until a conservative horizon has passed, after which
it is swept. There is deliberately no short-grace reclaim: a re-stat is not a
compare-and-delete, so a paused reserver could still publish, and reclaiming early
could double-spend.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_RECORD_VERSION = 1

# Outcome kinds the store maps onto the result envelope. "won" is internal: the caller
# that reserved must spawn the job and then publish() the job_id.
WON = "won"
REPLAY = "replay"
CONFLICT = "conflict"
UNAVAILABLE = "unavailable"
IN_PROGRESS = "in_progress"


def canonical_json(payload: object) -> str:
    """Deterministic JSON for hashing: sorted keys, compact, UTF-8, no NaN/Inf."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def arg_hash(payload: dict) -> str:
    """sha256 of the canonicalized effective run inputs. Raises ValueError on a
    non-finite value (allow_nan=False) so a NaN can never silently collide."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def key_digest(tool: str, key: str) -> str:
    """sha256 over an unambiguous encoding of (tool, key) — a JSON array, not a raw
    concatenation, so ("ab","c") and ("a","bc") never collide. The filename stem."""
    return hashlib.sha256(canonical_json([tool, key]).encode("utf-8")).hexdigest()


@dataclass
class Outcome:
    """Classification of a reserve()/lookup. ``kind`` is one of the module constants.
    ``path`` is set only for WON (so the caller can publish/remove it); ``job_id`` is
    set for WON-after-publish and REPLAY."""

    kind: str
    job_id: str | None = None
    path: Path | None = None


# A resolver the store injects: job_id -> (exists, terminal) or None when the job dir
# is gone (consumed or count-cap-evicted). ``terminal`` distinguishes a finished job
# (replayable while its record lives) from a still-running one.
@dataclass
class JobFacts:
    exists: bool
    terminal: bool


JobResolver = Callable[[str], "JobFacts | None"]


# Sentinel used internally by _classify to mean "entry is past the horizon; sweep it
# and (in reserve) retry the O_EXCL create". Never surfaced to callers.
_SWEEP = "sweep"


class IdempotencyIndex:
    """Filesystem-backed dedup index rooted at one ``.idem`` directory.

    The store owns the directory (one per workspace) and injects a ``resolve`` callback
    that maps a ``job_id`` to :class:`JobFacts` (or ``None`` when the job dir is gone).
    All methods are safe to call under the store's process lock; cross-process mutual
    exclusion on a first reservation comes from ``O_EXCL``, not the lock.

    ``horizon_seconds`` is the conservative retention floor: a reserved-but-unpublished
    or result-gone entry is honored (in-progress / unavailable) until it has aged past
    the horizon, and only then is it swept — never reclaimed early (a re-stat is not a
    compare-and-delete, so an early reclaim could double-spend against a paused owner).
    """

    def __init__(self, idem_dir: Path, *, horizon_seconds: float) -> None:
        self.dir = Path(idem_dir)
        self.horizon_seconds = float(horizon_seconds)

    # ------------------------------------------------------------------ paths
    def _path(self, tool: str, key: str) -> Path:
        return self.dir / f"{key_digest(tool, key)}.json"

    @staticmethod
    def _now() -> float:
        return time.time()

    # --------------------------------------------------------------- record io
    def _read(self, path: Path) -> tuple[str, dict | None]:
        """(status, record). status: 'ok' | 'empty' | 'corrupt' | 'missing'. 'empty'
        is a placeholder mid-setup (transient); 'corrupt' is a non-empty unparseable
        file (fail closed)."""
        try:
            text = path.read_text()
        except FileNotFoundError:
            return "missing", None
        except OSError:
            return "corrupt", None
        if text == "":
            return "empty", None
        try:
            rec = json.loads(text)
        except json.JSONDecodeError:
            return "corrupt", None
        if not isinstance(rec, dict):
            return "corrupt", None
        return "ok", rec

    def _atomic_write(self, path: Path, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        # mkstemp already creates the temp file 0o600 (owner-only), matching the record
        # secrecy the store enforces on the workspace dir.
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-", suffix=".json")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(canonical_json(rec))
            tmp_path.replace(path)  # atomic within the same directory
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise

    def remove(self, path: Path) -> None:
        with contextlib.suppress(OSError):
            path.unlink()

    def _mtime_age(self, path: Path, now: float) -> float | None:
        try:
            return now - path.stat().st_mtime
        except OSError:
            return None

    # ------------------------------------------------------------- operations
    def reserve(self, tool: str, key: str, arg_hash_: str, resolve: JobResolver) -> Outcome:
        """Reserve (tool, key) or classify an existing entry. Returns an :class:`Outcome`
        whose kind is WON (caller must spawn then :meth:`publish`), REPLAY, CONFLICT,
        UNAVAILABLE, or IN_PROGRESS."""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(tool, key)
        for attempt in (0, 1):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                decision = self._classify(path, arg_hash_, resolve)
                if decision == _SWEEP:
                    if attempt == 0:
                        self.remove(path)
                        continue
                    # A racing creator re-made it after our sweep; don't loop — report
                    # in-progress so the caller retries rather than double-spending.
                    return Outcome(kind=IN_PROGRESS, path=path)
                if decision == REPLAY:
                    status, rec = self._read(path)
                    jid = rec.get("job_id") if (status == "ok" and rec is not None) else None
                    return Outcome(kind=REPLAY, job_id=jid, path=path)
                return Outcome(kind=decision, path=path)
            else:
                os.close(fd)
                self._atomic_write(
                    path,
                    {
                        "version": _RECORD_VERSION,
                        "tool": tool,
                        "key_digest": key_digest(tool, key),
                        "arg_hash": arg_hash_,
                        "job_id": None,
                        "state": "reserved",
                        "reserved_epoch": self._now(),
                    },
                )
                return Outcome(kind=WON, path=path)
        return Outcome(kind=IN_PROGRESS, path=path)  # pragma: no cover - loop always returns

    def _classify(self, path: Path, arg_hash_: str, resolve: JobResolver) -> str:
        status, rec = self._read(path)
        now = self._now()
        if status == "missing":
            return _SWEEP  # vanished between O_EXCL failure and read; retry create
        if status == "empty":
            age = self._mtime_age(path, now)
            if age is None:
                return IN_PROGRESS
            return _SWEEP if age > self.horizon_seconds else IN_PROGRESS
        if status == "corrupt":
            return UNAVAILABLE  # non-empty but unparseable -> fail closed
        assert rec is not None
        reserved_epoch = rec.get("reserved_epoch") or 0.0
        within = (now - reserved_epoch) <= self.horizon_seconds
        job_id = rec.get("job_id")
        state = rec.get("state")
        if state == "active" and job_id:
            facts = resolve(job_id)
            if facts is not None and facts.exists:
                # A live-or-terminal job still on disk keeps the entry alive regardless
                # of age; classify by argument match.
                return CONFLICT if rec.get("arg_hash") != arg_hash_ else REPLAY
            # Job dir gone (consumed or count-cap evicted).
            if not within:
                return _SWEEP
            return CONFLICT if rec.get("arg_hash") != arg_hash_ else UNAVAILABLE
        # Reserved but not yet published.
        if not within:
            return _SWEEP
        return CONFLICT if rec.get("arg_hash") != arg_hash_ else IN_PROGRESS

    def publish(self, path: Path, job_id: str) -> None:
        """Promote a reserved entry to active, atomically. Only the O_EXCL winner ever
        publishes a given path, so this read-modify-write has no cross-process race."""
        status, rec = self._read(path)
        record = dict(rec) if (status == "ok" and rec is not None) else {}
        record["job_id"] = job_id
        record["state"] = "active"
        self._atomic_write(path, record)

    def sweep(self, resolve: JobResolver) -> None:
        """Drop entries whose backing job is gone and which have aged past the horizon;
        keep any entry still backed by a live-or-terminal on-disk job."""
        if not self.dir.is_dir():
            return
        now = self._now()
        for p in self.dir.iterdir():
            if not p.is_file() or not p.name.endswith(".json") or p.name.startswith(".tmp-"):
                continue
            status, rec = self._read(p)
            if status in ("empty", "corrupt"):
                age = self._mtime_age(p, now)
                if age is not None and age > self.horizon_seconds:
                    self.remove(p)
                continue
            if status != "ok" or rec is None:
                continue
            job_id = rec.get("job_id")
            if rec.get("state") == "active" and job_id:
                facts = resolve(job_id)
                if facts is not None and facts.exists:
                    continue
            reserved_epoch = rec.get("reserved_epoch") or 0.0
            if (now - reserved_epoch) > self.horizon_seconds:
                self.remove(p)
