"""Tests for the disk-backed idempotency index in _core/idempotency.py.

The index gives the six spend-committing tools a client-supplied `idempotency_key`
so a retry after a transport drop replays an existing run instead of paying for a
duplicate. It is _core machinery: stdlib only, no parent-package imports.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from codex_in_claude._core import idempotency as idem


# --------------------------------------------------------------- pure helpers
def test_arg_hash_is_order_independent():
    a = idem.arg_hash({"model": "gpt", "task": "x", "timeout_seconds": 30})
    b = idem.arg_hash({"timeout_seconds": 30, "task": "x", "model": "gpt"})
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_arg_hash_differs_on_value_change():
    a = idem.arg_hash({"task": "x"})
    b = idem.arg_hash({"task": "y"})
    assert a != b


def test_arg_hash_rejects_non_finite():
    with pytest.raises(ValueError):
        idem.arg_hash({"x": float("nan")})


def test_key_digest_is_unambiguous_across_tool_and_key():
    # tool="ab", key="c" must not collide with tool="a", key="bc" (naive concat would)
    assert idem.key_digest("ab", "c") != idem.key_digest("a", "bc")
    assert idem.key_digest("codex_consult", "k1") == idem.key_digest("codex_consult", "k1")
    assert len(idem.key_digest("t", "k")) == 64


# --------------------------------------------------------------- the index
def _resolver(**facts):
    """Build a JobResolver returning JobFacts for known ids, None otherwise."""

    def resolve(job_id):
        f = facts.get(job_id)
        return idem.JobFacts(**f) if f is not None else None

    return resolve


def _idx(tmp_path, horizon=3600.0):
    return idem.IdempotencyIndex(tmp_path / "ws" / ".idem", horizon_seconds=horizon)


def _publish(idx, out, job_id):
    idx.publish(out, job_id)


def test_first_reserve_wins_and_writes_reserved_record(tmp_path):
    idx = _idx(tmp_path)
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.WON
    rec = json.loads(out.path.read_text())
    assert rec["job_id"] is None and rec["state"] == "reserved" and rec["arg_hash"] == "AH1"
    # raw key is never persisted
    assert "k1" not in out.path.read_text()


def test_published_identical_call_replays(tmp_path):
    idx = _idx(tmp_path)
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    _publish(idx, out, "job-123")
    again = idx.reserve(
        "codex_consult", "k1", "AH1", _resolver(**{"job-123": {"exists": True, "terminal": False}})
    )
    assert again.kind == idem.REPLAY and again.job_id == "job-123"


def test_same_key_different_args_conflicts(tmp_path):
    idx = _idx(tmp_path)
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    _publish(idx, out, "job-123")
    clash = idx.reserve(
        "codex_consult", "k1", "AH2", _resolver(**{"job-123": {"exists": True, "terminal": True}})
    )
    assert clash.kind == idem.CONFLICT


def test_consumed_job_is_result_unavailable_within_window(tmp_path):
    idx = _idx(tmp_path)
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    _publish(idx, out, "job-123")
    # job dir gone (consumed / count-cap evicted) => resolver returns None
    gone = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert gone.kind == idem.UNAVAILABLE


def test_reserved_but_unpublished_is_in_progress(tmp_path):
    idx = _idx(tmp_path)
    idx.reserve("codex_consult", "k1", "AH1", _resolver())  # won, never published
    second = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert second.kind == idem.IN_PROGRESS


def test_empty_placeholder_reads_as_in_progress(tmp_path):
    idx = _idx(tmp_path)
    d = tmp_path / "ws" / ".idem"
    d.mkdir(parents=True)
    (d / f"{idem.key_digest('codex_consult', 'k1')}.json").write_text("")  # torn/mid-write
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.IN_PROGRESS


def test_corrupt_record_fails_closed_unavailable(tmp_path):
    idx = _idx(tmp_path)
    d = tmp_path / "ws" / ".idem"
    d.mkdir(parents=True)
    (d / f"{idem.key_digest('codex_consult', 'k1')}.json").write_text("{not json")
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.UNAVAILABLE


def test_undecodable_record_fails_closed_unavailable(tmp_path):
    idx = _idx(tmp_path)
    d = tmp_path / "ws" / ".idem"
    d.mkdir(parents=True)
    (d / f"{idem.key_digest('codex_consult', 'k1')}.json").write_bytes(b"\xff\xfe\xfa")
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.UNAVAILABLE


def test_stale_reservation_past_horizon_is_swept_and_rewon(tmp_path):
    idx = _idx(tmp_path, horizon=0.0)  # everything is immediately past horizon
    idx.reserve("codex_consult", "k1", "AH1", _resolver())  # won, unpublished, stale
    time.sleep(0.01)
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.WON  # reclaimed only after the full horizon


def test_sweep_removes_past_horizon_entries(tmp_path):
    idx = _idx(tmp_path, horizon=0.0)
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    _publish(idx, out, "job-1")
    time.sleep(0.01)
    idx.sweep(_resolver())  # job-1 gone, past horizon
    assert not out.path.exists()


def test_sweep_removes_undecodable_record_past_horizon(tmp_path):
    idx = _idx(tmp_path, horizon=0.0)
    d = tmp_path / "ws" / ".idem"
    d.mkdir(parents=True)
    path = d / f"{idem.key_digest('codex_consult', 'k1')}.json"
    path.write_bytes(b"\xff\xfe\xfa")

    time.sleep(0.01)
    idx.sweep(_resolver())

    assert not path.exists()


def _write_raw(tmp_path, text):
    d = tmp_path / "ws" / ".idem"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{idem.key_digest('codex_consult', 'k1')}.json"
    p.write_text(text)
    return p


def test_empty_json_object_fails_closed(tmp_path):
    # A parseable-but-structurally-invalid record must NOT be reclaimed as a fresh miss
    # (it would default reserved_epoch=0, read as past-horizon, and re-spawn).
    idx = _idx(tmp_path)
    _write_raw(tmp_path, "{}")
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.UNAVAILABLE


def test_reserved_record_missing_epoch_fails_closed(tmp_path):
    idx = _idx(tmp_path)
    _write_raw(tmp_path, json.dumps({"version": 1, "state": "reserved", "arg_hash": "AH1"}))
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.UNAVAILABLE


def test_unknown_future_version_fails_closed(tmp_path):
    idx = _idx(tmp_path)
    _write_raw(
        tmp_path,
        json.dumps(
            {
                "version": 99,
                "state": "active",
                "arg_hash": "AH1",
                "reserved_epoch": 1.0,
                "job_id": "j",
            }
        ),
    )
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver(j={"exists": True, "terminal": True}))
    assert out.kind == idem.UNAVAILABLE


def test_lock_is_an_exclusive_cross_process_flock(tmp_path):
    import fcntl
    import os

    idx = _idx(tmp_path)
    lock_file = idx.dir / ".lock"
    with idx.lock():
        # A second open-file-description (what another process would hold) must not be
        # able to grab the same advisory lock while we hold it.
        fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    # released outside the context: now acquirable
    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # no raise
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def test_replay_never_returns_null_job_id(tmp_path):
    # Copilot review: an active record lacking a job_id must not classify as a REPLAY
    # with job_id=None (the caller would dereference it). _well_formed rejects it, so it
    # fails closed as unavailable instead.
    idx = _idx(tmp_path)
    _write_raw(
        tmp_path,
        json.dumps(
            {
                "version": 1,
                "state": "active",
                "arg_hash": "AH1",
                "reserved_epoch": time.time(),
                "job_id": None,
            }
        ),
    )
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.UNAVAILABLE
    assert out.job_id is None


# ----------------------------------------------------- bounded lock acquisition
def _hold_lock(idx):
    """Open a *second* file description on the index lockfile and hold LOCK_EX,
    mimicking a sibling process that grabbed the cross-process flock. Returns the fd;
    the caller must unlock+close it."""
    import fcntl
    import os

    idx.dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(idx.dir / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release_lock(fd):
    import fcntl
    import os

    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def test_lock_with_timeout_acquires_when_free(tmp_path):
    idx = _idx(tmp_path)
    with idx.lock(timeout=1.0):
        entered = True
    assert entered
    # released on exit -> re-acquirable with a bound
    with idx.lock(timeout=1.0):
        pass


def test_lock_timeout_raises_when_held_by_sibling(tmp_path):
    # A sibling holding the flock must make a bounded acquire fail fast (LockTimeout)
    # instead of hanging indefinitely on the unbounded LOCK_EX.
    idx = _idx(tmp_path)
    fd = _hold_lock(idx)
    try:
        start = time.monotonic()
        with pytest.raises(idem.LockTimeout), idx.lock(timeout=0.2):
            pass  # pragma: no cover - body never runs; acquire times out
        elapsed = time.monotonic() - start
        assert 0.2 <= elapsed < 2.0  # bounded to the timeout, not hung
    finally:
        _release_lock(fd)


def test_lock_zero_timeout_attempts_once_and_fails_when_held(tmp_path):
    idx = _idx(tmp_path)
    fd = _hold_lock(idx)
    try:
        with pytest.raises(idem.LockTimeout), idx.lock(timeout=0.0):
            pass  # pragma: no cover - acquire fails on the single non-blocking try
    finally:
        _release_lock(fd)


def test_lock_zero_timeout_acquires_when_free(tmp_path):
    idx = _idx(tmp_path)
    with idx.lock(timeout=0.0):  # single non-blocking attempt succeeds uncontended
        pass


def test_lock_degrades_when_fcntl_unavailable(tmp_path, monkeypatch):
    """A fcntl-less platform (non-POSIX) degrades to no cross-process lock instead of
    crashing on `import fcntl` (#232). Mirrors the worker-lock shim in _core/jobs.py
    and the killpg-less simulation in tests/test_gitdiff.py. The server startup guard
    rejects non-POSIX platforms before this path is reached; this keeps _core internally
    consistent and extractable."""
    import sys

    # `import fcntl` raises ImportError when None occupies its sys.modules slot.
    monkeypatch.setitem(sys.modules, "fcntl", None)
    idx = _idx(tmp_path)

    # _acquire_flock no-ops rather than raising ImportError on a fcntl-less platform.
    fd = os.open(tmp_path / "fd", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        idem._acquire_flock(fd, None)  # must not raise
    finally:
        os.close(fd)

    # The lock() contextmanager still yields (no cross-process lock, but no crash) and
    # its release path skips the LOCK_UN that would otherwise NameError on `fcntl`.
    # Initialize before entering so a raise inside lock() surfaces the real exception
    # rather than an UnboundLocalError on the assert.
    entered = False
    with idx.lock():
        entered = True
    assert entered


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_lock_rejects_non_finite_or_negative_timeout(tmp_path, bad):
    idx = _idx(tmp_path)
    with pytest.raises(ValueError), idx.lock(timeout=bad):
        pass  # pragma: no cover - validation raises before the body


def test_lock_releases_after_body_exception_with_timeout(tmp_path):
    idx = _idx(tmp_path)
    with pytest.raises(RuntimeError), idx.lock(timeout=1.0):
        raise RuntimeError("boom")
    # released despite the exception -> re-acquirable
    with idx.lock(timeout=1.0):
        pass


# ---------------------------------------------- partial-failure rollback (#200)
def _raise(exc):
    def _fn(*_a, **_k):
        raise exc

    return _fn


def test_reserve_removes_placeholder_when_initial_write_fails(tmp_path, monkeypatch):
    # If populating the just-created O_EXCL placeholder fails (ENOSPC/EACCES), the
    # empty file must be rolled back — otherwise it reads as IN_PROGRESS until the
    # horizon and every retry with this key is stranded with no job running.
    idx = _idx(tmp_path)
    boom = OSError("ENOSPC")
    monkeypatch.setattr(idem.IdempotencyIndex, "_atomic_write", _raise(boom))
    with pytest.raises(OSError) as excinfo:
        idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert excinfo.value is boom  # original error propagates unchanged
    assert not idx._path("codex_consult", "k1").exists()  # placeholder rolled back
    monkeypatch.undo()
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.WON  # a clean fresh win, not a stranded in_progress


def test_reserve_logs_when_placeholder_cleanup_fails(tmp_path, monkeypatch, caplog):
    # A cleanup that itself fails (EROFS, etc.) must not mask the original write
    # error, but should leave a breadcrumb.
    idx = _idx(tmp_path)
    monkeypatch.setattr(idem.IdempotencyIndex, "_atomic_write", _raise(OSError("write")))
    monkeypatch.setattr(idem.Path, "unlink", _raise(OSError("unlink")))
    with caplog.at_level("WARNING"), pytest.raises(OSError, match="write"):
        idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert any("placeholder" in r.getMessage() for r in caplog.records)


def test_publish_self_heals_vanished_reservation(tmp_path):
    # If the reservation file disappears between reserve() and publish() (external
    # deletion or a fault under the held lock), publish must still write a COMPLETE
    # active record from the reservation it holds — not leave the path empty, which would
    # let a same-key retry win a fresh O_EXCL create and double-spend against the running
    # job. A same-args retry must therefore replay, a different-args retry must conflict.
    idx = _idx(tmp_path)
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    assert out.kind == idem.WON
    out.path.unlink()  # reservation vanishes before publish
    idx.publish(out, "job-1")
    live = _resolver(**{"job-1": {"exists": True, "terminal": False}})
    replay = idx.reserve("codex_consult", "k1", "AH1", live)
    assert replay.kind == idem.REPLAY and replay.job_id == "job-1"  # not a second win
    clash = idx.reserve("codex_consult", "k1", "OTHER", live)
    assert clash.kind == idem.CONFLICT  # arg_hash survived the rewrite


def test_publish_writes_full_active_record_without_reread(tmp_path):
    # publish() rewrites the complete record even over a corrupted file, so the mapping
    # (arg_hash/version) is never dropped for a paid job.
    idx = _idx(tmp_path)
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    out.path.write_text("{ corrupt")  # file unreadable at publish time
    idx.publish(out, "job-1")
    rec = json.loads(out.path.read_text())
    assert rec["state"] == "active" and rec["job_id"] == "job-1" and rec["arg_hash"] == "AH1"


# ---------------------------------------------- transient read OSError (#202)
def test_transient_read_oserror_is_io_error_not_unavailable(tmp_path, monkeypatch):
    # A momentary OSError (EIO on flaky/network storage, a permissions race) while
    # reading the record of a healthy, replayable completed run must NOT be mapped to
    # UNAVAILABLE ("permanent, use a new key") — that invites a duplicate paid run.
    # It must surface as IO_ERROR (temporary, retry the same key), and a subsequent
    # read with the error cleared must replay the stored result.
    idx = _idx(tmp_path)
    live = _resolver(**{"job-1": {"exists": True, "terminal": True}})
    out = idx.reserve("codex_consult", "k1", "AH1", live)
    assert out.kind == idem.WON
    _publish(idx, out, "job-1")

    # Make the next read_text on the record file raise OSError once, then pass through.
    real_read_text = idem.Path.read_text
    calls = {"n": 0}

    def flaky_read_text(self, *args, **kwargs):
        if self == out.path:  # pin to this exact record, not a parent/suffix filter
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient EIO")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(idem.Path, "read_text", flaky_read_text)

    out2 = idx.reserve("codex_consult", "k1", "AH1", live)
    assert out2.kind == idem.IO_ERROR  # not UNAVAILABLE — the key is preserved

    # After the transient error clears, the same key replays the stored result.
    out3 = idx.reserve("codex_consult", "k1", "AH1", live)
    assert out3.kind == idem.REPLAY
    assert out3.job_id == "job-1"


def test_sweep_keeps_io_error_entry_within_grace(tmp_path, monkeypatch):
    # Within a generous multiple of the horizon, a transient read failure is NOT
    # reclaimed — the record may be intact and a retry could replay it (#202).
    idx = _idx(tmp_path, horizon=100.0)  # grace window = 3 * 100 = 300s
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    _publish(idx, out, "job-1")

    monkeypatch.setattr(idem.Path, "read_text", _raise(OSError("transient EIO")))
    idx.sweep(_resolver())  # job-1 gone, but well within the 300s grace window

    assert out.path.exists()  # survives: a transient read failure is not corruption


def test_sweep_reclaims_io_error_entry_past_grace(tmp_path, monkeypatch):
    # Past the generous grace window, a still-unreadable entry IS reclaimed (unlink
    # needs directory write permission, not file read permission, so it succeeds on an
    # unreadable file) — un-wedging the key for a fresh reservation. Without this bound,
    # a persistent read failure (e.g. a permission error) would wedge the key behind an
    # infinitely-retryable "temporary" error forever (#202).
    idx = _idx(tmp_path, horizon=0.0)  # grace window = 3 * 0 = 0s
    out = idx.reserve("codex_consult", "k1", "AH1", _resolver())
    _publish(idx, out, "job-1")
    # Backdate the record so it is unambiguously past the 0s grace window.
    os.utime(out.path, (time.time() - 10, time.time() - 10))

    monkeypatch.setattr(idem.Path, "read_text", _raise(OSError("persistent EIO")))
    idx.sweep(_resolver())  # job-1 gone, past grace, read still failing

    assert not out.path.exists()  # reclaimed: the key is un-wedged
