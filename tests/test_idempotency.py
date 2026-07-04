"""Tests for the disk-backed idempotency index in _core/idempotency.py.

The index gives the six spend-committing tools a client-supplied `idempotency_key`
so a retry after a transport drop replays an existing run instead of paying for a
duplicate. It is _core machinery: stdlib only, no parent-package imports.
"""

from __future__ import annotations

import json
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
    idx.publish(out.path, job_id)


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
