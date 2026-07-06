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
import logging
import math
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_RECORD_VERSION = 1

# Outcome kinds the store maps onto the result envelope. "won" is internal: the caller
# that reserved must spawn the job and then publish() the job_id. "io_error" is a
# transient read failure (OSError other than FileNotFoundError): the record may be
# intact, so callers retry the same key rather than fail closed to a fresh one.
WON = "won"
REPLAY = "replay"
CONFLICT = "conflict"
UNAVAILABLE = "unavailable"
IN_PROGRESS = "in_progress"
IO_ERROR = "io_error"

# A transient read failure (io_error) may clear in moments, so sweep() gives such
# entries a generous multiple of the horizon before reclaiming them — long enough that
# a momentary blip never deletes a replayable record, but bounded so a persistently
# unreadable entry (e.g. a permission error) cannot wedge its key behind an
# infinitely-retryable "temporary" error forever (#202).
_IO_ERROR_SWEEP_MULT = 3

# Backoff between non-blocking flock retries while a bounded acquire waits for the
# cross-process lock. Small enough that a lock freed mid-wait is taken promptly.
_LOCK_POLL_SECONDS = 0.025


class LockTimeout(Exception):
    """A bounded :meth:`IdempotencyIndex.lock` could not take the cross-process flock
    before its deadline — a sibling holds it. Callers degrade this to a retryable
    in-progress outcome rather than blocking indefinitely."""


def _acquire_flock(fd: int, timeout: float | None) -> None:
    """Take ``LOCK_EX`` on ``fd``. With ``timeout`` None, block indefinitely (original
    semantics). Otherwise poll ``LOCK_EX | LOCK_NB`` until a monotonic deadline and raise
    :class:`LockTimeout` on expiry; ``0`` is a single non-blocking attempt. A non-finite
    or negative ``timeout`` is a ``ValueError``. Only contention (``BlockingIOError``) is
    retried — any other ``OSError`` propagates so a real filesystem fault is not masked."""
    import fcntl  # noqa: PLC0415 - POSIX only, lazy like the job store's worker lock

    if timeout is None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError(f"lock timeout must be finite and non-negative, got {timeout!r}")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LockTimeout("timed out acquiring the idempotency index lock") from None
            time.sleep(min(_LOCK_POLL_SECONDS, remaining))


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
    ``path`` is the entry's path — always set (every reserve() outcome carries it), so it is
    a required, non-optional field; only a WON entry may be *mutated* by the caller
    (publish() or remove() it), the others are read-only classifications. ``job_id`` is set
    for WON-after-publish and REPLAY. ``record`` is the reserved record the winner just
    wrote — carried on a WON so :meth:`publish` can rewrite a complete active record without
    re-reading the file (see #200)."""

    kind: str
    path: Path
    job_id: str | None = None
    record: dict | None = None


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

    Horizon arithmetic uses wall-clock time (``time.time()``/``st_mtime``), consistent
    with the job store; a large clock step can shorten or extend the window —
    cross-process on-disk expiry has no monotonic alternative.
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
    @staticmethod
    def _well_formed(rec: dict) -> bool:
        """Whether a parsed record has every field a classification depends on, with a
        supported version and a finite timestamp. A structurally invalid but parseable
        record (e.g. `{}` or a missing/NaN `reserved_epoch`) must NOT be treated as a
        valid entry — otherwise it defaults to epoch 0, reads as past the horizon, and
        gets reclaimed instead of failing closed."""
        version = rec.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version > _RECORD_VERSION:
            return False
        if rec.get("state") not in ("reserved", "active"):
            return False
        if not isinstance(rec.get("arg_hash"), str):
            return False
        epoch = rec.get("reserved_epoch")
        if (
            isinstance(epoch, bool)
            or not isinstance(epoch, (int, float))
            or not math.isfinite(epoch)
        ):
            return False
        if rec.get("state") == "active":
            return isinstance(rec.get("job_id"), str) and bool(rec["job_id"])
        return True

    def _read(self, path: Path) -> tuple[str, dict | None]:
        """(status, record). status: 'ok' | 'empty' | 'corrupt' | 'missing' | 'io_error'.
        'empty' is a placeholder mid-setup (transient); 'corrupt' is a non-empty file that
        is undecodable, unparseable, or structurally invalid — either way fail closed (never
        reclaimed as a fresh miss within the window). 'io_error' is a transient read failure
        (OSError other than FileNotFoundError); the record may be intact, so callers retry
        the same key rather than fail closed."""
        try:
            text = path.read_text()
        except FileNotFoundError:
            return "missing", None
        except UnicodeDecodeError:
            return "corrupt", None
        except OSError:
            return "io_error", None
        if text == "":
            return "empty", None
        try:
            rec = json.loads(text)
        except json.JSONDecodeError:
            return "corrupt", None
        if not isinstance(rec, dict) or not self._well_formed(rec):
            return "corrupt", None
        return "ok", rec

    @contextlib.contextmanager
    def lock(self, timeout: float | None = None) -> Iterator[None]:
        """Exclusive cross-process lock over this index directory, held across a full
        reserve → spawn → publish cycle. ``O_EXCL`` alone makes a *first* create atomic,
        but a stale-entry reclaim (unlink + re-create) and a publish are multi-step: two
        processes could otherwise both delete and re-create the same key and both win.
        The store wraps its keyed-start critical section in this lock so that can't
        happen; a paused holder that dies releases it (advisory ``flock`` on a lockfile,
        POSIX).

        ``timeout`` bounds *lock acquisition only* (not the protected body):

        - ``None`` (default): block on ``LOCK_EX`` indefinitely — the original semantics,
          for callers that must wait.
        - a finite, non-negative float: poll ``LOCK_EX | LOCK_NB`` until a monotonic
          deadline and raise :class:`LockTimeout` if the lock cannot be taken in time, so
          a sibling stuck mid-critical-section degrades to a fast, retryable failure
          instead of an indefinite hang. ``0`` makes a single non-blocking attempt. A
          non-finite or negative value is a ``ValueError``.
        """
        import fcntl  # noqa: PLC0415 - POSIX only, lazy like the job store's worker lock

        self.dir.mkdir(parents=True, exist_ok=True)
        # Not a ``*.json`` record, so sweep()/reserve() never treat it as an entry.
        fd = os.open(self.dir / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _acquire_flock(fd, timeout)
        except BaseException:
            # Acquisition failed (LockTimeout / ValueError / an unexpected error): we never
            # held the lock, so just close the fd and propagate — nothing to unlock.
            os.close(fd)
            raise
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

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
        UNAVAILABLE, IN_PROGRESS, or IO_ERROR (a transient read failure — retry the same
        key)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(tool, key)
        for attempt in (0, 1):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                decision, replay_job_id = self._classify(path, arg_hash_, resolve)
                if decision == _SWEEP:
                    if attempt == 0:
                        self.remove(path)
                        continue
                    # A racing creator re-made it after our sweep; don't loop — report
                    # in-progress so the caller retries rather than double-spending.
                    return Outcome(kind=IN_PROGRESS, path=path)
                if decision == REPLAY:
                    # The job_id comes from the same read that decided REPLAY (no second
                    # read that could race to None). _well_formed guarantees an active
                    # record has one; if it is somehow absent, fail closed rather than
                    # hand back a null job_id the caller would dereference.
                    if not replay_job_id:  # pragma: no cover - defensive; _well_formed
                        return Outcome(kind=UNAVAILABLE, path=path)  # guarantees active has one
                    return Outcome(kind=REPLAY, job_id=replay_job_id, path=path)
                return Outcome(kind=decision, path=path)
            else:
                rec = {
                    "version": _RECORD_VERSION,
                    "tool": tool,
                    "key_digest": key_digest(tool, key),
                    "arg_hash": arg_hash_,
                    "job_id": None,
                    "state": "reserved",
                    "reserved_epoch": self._now(),
                }
                try:
                    os.close(fd)
                    self._atomic_write(path, rec)
                except BaseException:
                    # Roll back the empty O_EXCL placeholder: a failed initial write
                    # (ENOSPC/EACCES, or a rare close failure) would otherwise strand it
                    # as a phantom IN_PROGRESS until the horizon sweep, and every retry
                    # with this key would loop with no job running (#200). We hold the
                    # index lock and the placeholder is ours, so the unlink is race-safe.
                    # A cleanup that itself fails must not mask the original error.
                    try:
                        path.unlink()
                    except OSError:
                        logger.warning(
                            "failed to remove idempotency placeholder after write error: %s",
                            path,
                            exc_info=True,
                        )
                    raise
                return Outcome(kind=WON, path=path, record=rec)
        return Outcome(kind=IN_PROGRESS, path=path)  # pragma: no cover - loop always returns

    def _classify(self, path: Path, arg_hash_: str, resolve: JobResolver) -> tuple[str, str | None]:
        """Classify an existing entry. Returns (decision, job_id); job_id is set only for
        a REPLAY and is read from the SAME record the decision was made on, so reserve()
        never has to re-read (which could race the record to a null job_id)."""
        status, rec = self._read(path)
        now = self._now()
        if status == "missing":
            return _SWEEP, None  # vanished between O_EXCL failure and read; retry create
        if status == "empty":
            age = self._mtime_age(path, now)
            if age is None:
                return IN_PROGRESS, None
            return (_SWEEP if age > self.horizon_seconds else IN_PROGRESS), None
        if status == "corrupt":
            return UNAVAILABLE, None  # non-empty but unparseable -> fail closed
        if status == "io_error":
            # Transient read failure (OSError other than FileNotFoundError): the record
            # may be intact. Surface a temporary error so the caller retries the same key
            # instead of starting a new paid run under a fresh key. sweep() reclaims the
            # entry only past a generous multiple of the horizon, so a persistent read
            # failure cannot wedge the key forever (#202).
            return IO_ERROR, None
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
                if rec.get("arg_hash") != arg_hash_:
                    return CONFLICT, None
                return REPLAY, job_id
            # Job dir gone (consumed or count-cap evicted).
            if not within:
                return _SWEEP, None
            return (CONFLICT if rec.get("arg_hash") != arg_hash_ else UNAVAILABLE), None
        # Reserved but not yet published.
        if not within:
            return _SWEEP, None
        return (CONFLICT if rec.get("arg_hash") != arg_hash_ else IN_PROGRESS), None

    def publish(self, outcome: Outcome, job_id: str) -> None:
        """Promote a WON reservation to active, atomically. Writes a complete active
        record from the reservation the winner already holds (``outcome.record``) rather
        than re-reading the file. Only the O_EXCL winner ever publishes a given path, so
        there is no cross-process race.

        Not re-reading is deliberate (#200): if the reservation file were corrupted or
        deleted between reserve() and publish(), a read-modify-write would either drop the
        idempotency mapping (a stub built from `{}` lacks `arg_hash`/`version` and reads
        back as corrupt) or, if the file is *missing*, leave nothing on disk — letting a
        same-key retry win a fresh O_EXCL create and double-spend against the running job.
        Rewriting the full record instead self-heals both cases. If ``_atomic_write``
        itself fails, the original reserved record stays on disk and the entry fails
        closed (in-progress until the horizon) for other callers."""
        assert outcome.record is not None  # invariant of a WON outcome (path is always set)
        record = {**outcome.record, "job_id": job_id, "state": "active"}
        self._atomic_write(outcome.path, record)

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
            if status == "io_error":
                # Transient read failure: the record may be intact, so give it a
                # generous multiple of the horizon to clear before reclaiming. The bound
                # stops a persistently unreadable entry (e.g. a permission error) from
                # wedging its key behind an infinitely-retryable "temporary" error;
                # unlink needs directory write permission, not file read permission, so
                # remove() succeeds on an unreadable file (#202).
                age = self._mtime_age(p, now)
                if age is not None and age > self.horizon_seconds * _IO_ERROR_SWEEP_MULT:
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
