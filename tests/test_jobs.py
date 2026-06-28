"""Tests for the generic disk-backed JobStore in _core/jobs.py.

These run without codex/git: the spawned command is a tiny python snippet whose
cwd is its own job dir, so writing ``result.json`` there mirrors what the real
worker does.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from codex_in_claude._core.jobs import JobStore

# A snippet (run with cwd=job_dir) that writes the final envelope to result.json.
_WRITE_DONE = "import json; open('result.json','w').write(json.dumps({'ok': True, 'tool': 't'}))"


def _store(tmp_path, **kw) -> JobStore:
    opts = {"ttl_seconds": 3600, "max_seconds": 60, "max_count": 50}
    opts.update(kw)
    return JobStore(root=tmp_path / "jobs", **opts)


def _wait_terminal(store: JobStore, cwd: str, job_id: str, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = store.status(cwd, job_id)
        assert st is not None
        if st["status"] != "running":
            return st["status"]
        time.sleep(0.02)
    raise AssertionError("job did not terminate in time")


def _factory(code: str):
    return lambda _jd: [sys.executable, "-c", code]


# A snippet (run with cwd=job_dir) that creates a throwaway dir under the cleanup
# root, declares it in cleanup.json, then sleeps — mimicking the real worker
# holding a temp worktree open while the job runs.
_DECLARE_AND_SLEEP = (
    "import json, sys, tempfile, time\n"
    "d = tempfile.mkdtemp(prefix=sys.argv[2], dir=sys.argv[1])\n"
    "open('cleanup.json', 'w').write(json.dumps({'paths': [d]}))\n"
    "open(d + '/marker', 'w').write('x')\n"
    "print(d, flush=True)\n"
    "time.sleep(30)\n"
)


def _declare_factory(root: str, prefix: str = "wt-"):
    return lambda _jd: [sys.executable, "-c", _DECLARE_AND_SLEEP, root, prefix]


def _declared_path(store: JobStore, cwd: str, job_id: str) -> str:
    """The external path a _DECLARE_AND_SLEEP job recorded in its manifest."""

    jd = store._job_dir(cwd, job_id)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            return json.loads((jd / "cleanup.json").read_text())["paths"][0]
        except (OSError, ValueError, KeyError):
            time.sleep(0.02)
    raise AssertionError("job did not declare a cleanup path in time")


def test_start_status_result_done(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, started = store.start(_factory(_WRITE_DONE), cwd, kind="codex_delegate")
    assert job_id and started
    assert _wait_terminal(store, cwd, job_id) == "done"
    rec, payload = store.result_payload(cwd, job_id, consume=False)
    assert rec["status"] == "done"
    assert payload == {"ok": True, "tool": "t"}
    assert rec["result_available"] is True


def test_failed_when_no_result(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory("raise SystemExit(1)"), cwd, kind="k")
    assert _wait_terminal(store, cwd, job_id) == "failed"
    rec, payload = store.result_payload(cwd, job_id, consume=False)
    assert rec["status"] == "failed"
    assert payload is None


def test_cancel_running(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory("import time; time.sleep(30)"), cwd, kind="k")
    st = store.cancel(cwd, job_id)
    assert st["status"] == "cancelled"
    # cancelling again returns the terminal record unchanged
    assert store.cancel(cwd, job_id)["status"] == "cancelled"


def test_cancel_removes_declared_external_paths(tmp_path):
    root = tmp_path / "wtroot"
    root.mkdir()
    store = _store(tmp_path, cleanup_root=root, cleanup_prefix="wt-")
    cwd = str(tmp_path)
    job_id, _ = store.start(_declare_factory(str(root)), cwd, kind="codex_delegate")
    wt = Path(_declared_path(store, cwd, job_id))
    assert wt.is_dir()
    st = store.cancel(cwd, job_id)
    assert st["status"] == "cancelled"
    assert not wt.exists()  # the declared worktree dir is gone
    assert st["cleanup_warnings"] == []


def test_cancel_rereads_late_declared_manifest(tmp_path):
    # The worker may declare its worktree only AFTER cancel begins (e.g. it is still
    # creating it). Cancel must re-read the manifest after termination, or the
    # fallback cleanup misses the late path and the worktree still leaks.
    root = tmp_path / "wtroot"
    root.mkdir()
    code = (
        "import json, signal, sys, tempfile, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"  # survive the grace
        "time.sleep(0.5)\n"  # declare only after cancel's first manifest read
        "d = tempfile.mkdtemp(prefix=sys.argv[2], dir=sys.argv[1])\n"
        "open(d + '/marker', 'w').write('x')\n"
        "open('cleanup.json', 'w').write(json.dumps({'paths': [d]}))\n"
        "time.sleep(30)\n"
    )
    store = _store(tmp_path, cleanup_root=root, cleanup_prefix="wt-", terminate_grace_seconds=2.0)
    cwd = str(tmp_path)
    factory = lambda _jd: [sys.executable, "-c", code, str(root), "wt-"]  # noqa: E731
    job_id, _ = store.start(factory, cwd, kind="k")
    time.sleep(0.25)  # let the worker install SIG_IGN before we cancel
    st = store.cancel(cwd, job_id)
    assert st["status"] == "cancelled"
    assert list(root.iterdir()) == []  # the late-declared worktree was still cleaned up


def test_timeout_removes_declared_external_paths(tmp_path):
    root = tmp_path / "wtroot"
    root.mkdir()
    store = _store(tmp_path, max_seconds=1, cleanup_root=root, cleanup_prefix="wt-")
    cwd = str(tmp_path)
    job_id, _ = store.start(_declare_factory(str(root)), cwd, kind="k")
    wt = Path(_declared_path(store, cwd, job_id))
    time.sleep(1.2)
    st = store.status(cwd, job_id)
    assert st["status"] == "timeout"
    assert not wt.exists()
    assert st["cleanup_warnings"] == []


def test_terminate_escalates_to_sigkill(tmp_path):
    # A process that ignores SIGTERM must still be force-killed once the grace ends.
    import subprocess

    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
    proc = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    try:
        assert jobs._is_running(proc.pid)
        jobs._terminate_pid_tree(proc.pid, grace_seconds=0.0)  # grace=0 -> escalate now
        assert not jobs._is_running(proc.pid)  # SIGKILL fallback reaped the survivor
    finally:
        jobs._kill_pid_tree(proc.pid)


def test_terminate_pid_tree_none_is_noop():
    jobs._terminate_pid_tree(None, grace_seconds=1.0)  # must not raise


def test_terminate_pid_tree_dead_pid_is_safe():
    # killpg on a long-dead pid raises ProcessLookupError; terminate must absorb it.
    jobs._terminate_pid_tree(2**30, grace_seconds=0.1)  # must not raise


def test_within_cleanup_root_requires_configured_root(tmp_path):
    store = _store(tmp_path)  # cleanup_root is None
    assert store._within_cleanup_root(str(tmp_path / "anything")) is False


def test_cleanup_refuses_path_outside_root(tmp_path):
    root = tmp_path / "wtroot"
    root.mkdir()
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep").write_text("x")
    store = _store(tmp_path, cleanup_root=root, cleanup_prefix="wt-")
    warnings = store._cleanup_external_paths([str(outside)])
    assert outside.is_dir()  # never touched
    assert any(str(outside) in w for w in warnings)


def test_cleanup_refuses_path_without_prefix(tmp_path):
    root = tmp_path / "wtroot"
    root.mkdir()
    stray = root / "not-a-worktree"
    stray.mkdir()
    store = _store(tmp_path, cleanup_root=root, cleanup_prefix="wt-")
    warnings = store._cleanup_external_paths([str(stray)])
    assert stray.is_dir()
    assert any(str(stray) in w for w in warnings)


def test_cleanup_warns_when_removal_fails(tmp_path, monkeypatch):
    root = tmp_path / "wtroot"
    root.mkdir()
    target = root / "wt-stuck"
    target.mkdir()
    store = _store(tmp_path, cleanup_root=root, cleanup_prefix="wt-")
    monkeypatch.setattr(jobs.shutil, "rmtree", lambda *a, **k: None)  # removal no-ops
    warnings = store._cleanup_external_paths([str(target)])
    assert target.is_dir()
    assert any(str(target) in w for w in warnings)


def test_cancel_surfaces_cleanup_warning(tmp_path, monkeypatch):
    root = tmp_path / "wtroot"
    root.mkdir()
    store = _store(tmp_path, cleanup_root=root, cleanup_prefix="wt-")
    cwd = str(tmp_path)
    job_id, _ = store.start(_declare_factory(str(root)), cwd, kind="k")
    wt = _declared_path(store, cwd, job_id)
    monkeypatch.setattr(jobs.shutil, "rmtree", lambda *a, **k: None)  # removal fails
    st = store.cancel(cwd, job_id)
    assert st["status"] == "cancelled"
    assert any(wt in w for w in st["cleanup_warnings"])  # leak named in the result


def test_cancel_preserves_result_completed_during_grace(tmp_path, monkeypatch):
    # If the worker finishes on its own during the grace window, cancel must NOT
    # mask the completed result as "cancelled".
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory("import time; time.sleep(30)"), cwd, kind="k")
    jd = store._job_dir(cwd, job_id)

    def fake_terminate(pid, grace_seconds, **kwargs):
        # Simulate the worker completing (writing result.json) before we kill it.
        (jd / "result.json").write_text('{"ok": true, "tool": "t"}')
        jobs._kill_pid_tree(pid)

    monkeypatch.setattr(jobs, "_terminate_pid_tree", fake_terminate)
    st = store.cancel(cwd, job_id)
    assert st["status"] == "done"  # completed result preserved, not overwritten
    assert st["result_available"] is True


def test_cancel_returns_none_if_record_removed_during_grace(tmp_path, monkeypatch):
    # If the record is consumed/deleted during the grace window, cancel must not
    # crash writing meta into a removed job dir.
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory("import time; time.sleep(30)"), cwd, kind="k")
    jd = store._job_dir(cwd, job_id)

    def fake_terminate(pid, grace_seconds, **kwargs):
        jobs._kill_pid_tree(pid)
        store._rmtree(jd)  # record removed mid-cancel

    monkeypatch.setattr(jobs, "_terminate_pid_tree", fake_terminate)
    assert store.cancel(cwd, job_id) is None


def test_within_cleanup_root_refuses_on_resolve_error(tmp_path, monkeypatch):
    root = tmp_path / "wtroot"
    root.mkdir()
    store = _store(tmp_path, cleanup_root=root, cleanup_prefix="wt-")

    def boom(self, *a, **k):
        raise OSError("symlink loop")

    monkeypatch.setattr(jobs.Path, "resolve", boom)
    assert store._within_cleanup_root(str(root / "wt-x")) is False  # refuse, do not raise


def test_cleanup_noop_without_cleanup_root(tmp_path):
    target = tmp_path / "wt-orphan"
    target.mkdir()
    store = _store(tmp_path)  # no cleanup_root configured
    warnings = store._cleanup_external_paths([str(target)])
    assert target.is_dir()  # nothing removed
    assert warnings == []


def test_consume_deletes(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory(_WRITE_DONE), cwd, kind="k")
    assert _wait_terminal(store, cwd, job_id) == "done"
    _, payload = store.result_payload(cwd, job_id, consume=True)
    assert payload == {"ok": True, "tool": "t"}
    assert store.status(cwd, job_id) is None


def test_consume_nondone_keeps_record(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory("import time; time.sleep(30)"), cwd, kind="k")
    store.cancel(cwd, job_id)
    rec, payload = store.result_payload(cwd, job_id, consume=True)
    assert rec["status"] == "cancelled"
    assert payload is None
    # not deleted (non-done)
    assert store.status(cwd, job_id) is not None


def test_missing_job(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    assert store.status(cwd, "deadbeef") is None
    assert store.cancel(cwd, "deadbeef") is None
    rec, payload = store.result_payload(cwd, "deadbeef", consume=False)
    assert rec is None and payload is None


def test_deadline_timeout(tmp_path):
    store = _store(tmp_path, max_seconds=1)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory("import time; time.sleep(30)"), cwd, kind="k")
    time.sleep(1.2)
    assert store.status(cwd, job_id)["status"] == "timeout"


def test_extra_roundtrips(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory(_WRITE_DONE), cwd, kind="k", extra={"foo": "bar"})
    _wait_terminal(store, cwd, job_id)
    assert store.status(cwd, job_id)["extra"] == {"foo": "bar"}


def test_write_spec_lands_in_job_dir(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    seen = {}

    def factory(jd):
        seen["jd"] = jd
        return [sys.executable, "-c", _WRITE_DONE]

    job_id, _ = store.start(factory, cwd, kind="k", write_spec={"task": "x"})
    _wait_terminal(store, cwd, job_id)

    assert json.loads((seen["jd"] / "spec.json").read_text()) == {"task": "x"}


def test_list_newest_first_and_count_cap(tmp_path):
    store = _store(tmp_path, max_count=2)
    cwd = str(tmp_path)
    ids = []
    for _ in range(3):
        jid, _ = store.start(_factory(_WRITE_DONE), cwd, kind="k")
        _wait_terminal(store, cwd, jid)
        ids.append(jid)
        time.sleep(0.01)
    listed = store.list_jobs(cwd)
    assert len(listed) <= 2  # oldest terminal evicted at the cap
    # newest first
    epochs = [j["started_epoch"] for j in listed]
    assert epochs == sorted(epochs, reverse=True)


def test_ttl_eviction(tmp_path):
    store = _store(tmp_path, ttl_seconds=60)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory(_WRITE_DONE), cwd, kind="k")
    _wait_terminal(store, cwd, job_id)
    # Force the completion far into the past, then a list() reap should drop it.
    store.list_jobs(cwd)
    jd = store._job_dir(cwd, job_id)

    meta = json.loads((jd / "meta.json").read_text())
    meta["completed_epoch"] = time.time() - 10_000
    (jd / "meta.json").write_text(json.dumps(meta))
    store.list_jobs(cwd)
    assert store.status(cwd, job_id) is None


def test_list_empty_workspace(tmp_path):
    store = _store(tmp_path)
    assert store.list_jobs(str(tmp_path)) == []


def test_start_oserror_cleans_up(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    with pytest.raises(OSError):
        # a non-existent executable path makes Popen raise OSError
        store.start(lambda _jd: ["/nonexistent/bin/zzz-not-real"], cwd, kind="k")


# --- defensive helpers / edge branches ---------------------------------------
import os  # noqa: E402

from codex_in_claude._core import jobs  # noqa: E402


def test_pid_alive_none_and_dead():
    assert jobs._pid_alive(None) is False
    # An almost-certainly-unused PID raises ProcessLookupError -> False.
    assert jobs._pid_alive(2**30) is False


def test_pid_alive_self():
    # Our own PID is alive (and not reapable via kill(0)).
    assert jobs._pid_alive(os.getpid()) is True


def test_is_running_none_and_not_our_child():
    assert jobs._is_running(None) is False
    # os.getpid() is alive but not our child: waitpid raises ChildProcessError,
    # then the kill(0) liveness probe reports it alive.
    assert jobs._is_running(os.getpid()) is True
    # A dead PID: waitpid raises ChildProcessError, liveness probe returns False.
    assert jobs._is_running(2**30) is False


def test_kill_pid_tree_none_is_noop():
    jobs._kill_pid_tree(None)  # must not raise


# --- restart / PID-reuse hardening (#55) -------------------------------------
def _persist_job(store, cwd, job_id, *, pid, owner, **over):
    jd = store._job_dir(cwd, job_id)
    jd.mkdir(parents=True, exist_ok=True)
    meta = {
        "job_id": job_id,
        "kind": "k",
        "pid": pid,
        "owner": owner,
        "started_epoch": time.time(),
        "started_at": "x",
        "deadline_epoch": time.time() + 999,
        "completed_epoch": None,
        "terminal_status": None,
        "extra": {},
    }
    meta.update(over)
    store._write_meta(jd, meta)
    return jd, store._read_meta(jd)


def test_unowned_live_pid_is_not_running(tmp_path):
    # Core #55 fix: after a restart, a persisted PID this server instance did not start
    # (unowned) and that holds no per-job worker lock must NOT be treated as the running
    # worker — even when that PID is alive (here, our own test process). The old kill(0)
    # fallback reported it running, which let cancel/timeout signal an unrelated process.
    store = _store(tmp_path)
    cwd = str(tmp_path)
    jd, meta = _persist_job(store, cwd, "jid", pid=os.getpid(), owner="other-instance")
    assert store._job_running(jd, meta) is False


def test_unowned_live_pid_status_does_not_signal(tmp_path, monkeypatch):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    _persist_job(store, cwd, "jid", pid=os.getpid(), owner="other-instance")
    calls = []
    monkeypatch.setattr(jobs, "_terminate_pid_tree", lambda *a, **k: calls.append(a))
    st = store.status(cwd, "jid")
    assert st["status"] != "running"  # not reported as the running worker
    assert calls == []  # and never signaled


def test_owned_child_no_lock_is_running(tmp_path):
    # A job THIS instance started (owned) is its own child; with no lock file yet the
    # existence probe is trustworthy, so a live owned child is reported running.
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory("import time; time.sleep(30)"), cwd, kind="k")
    jd = store._job_dir(cwd, job_id)
    meta = store._read_meta(jd)
    try:
        assert meta.get("owner")  # start() stamps the owner token
        assert store._job_running(jd, meta) is True
    finally:
        jobs._kill_pid_tree(meta.get("pid"))


def test_ownership_is_per_process_not_per_store(tmp_path):
    # The server builds a fresh JobStore per tool call, so ownership must be a
    # per-process identity: a job started by one store must still be "owned" (its own
    # child) when read through a different store in the same process — otherwise a
    # just-started, still-running job would be misreported as not running.
    store1 = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store1.start(_factory("import time; time.sleep(30)"), cwd, kind="k")
    store2 = _store(tmp_path)
    jd = store2._job_dir(cwd, job_id)
    meta = store2._read_meta(jd)
    try:
        assert store2._owned(meta) is True
        assert store2._job_running(jd, meta) is True  # no lock yet, but it's our child
        assert store2.status(cwd, job_id)["status"] == "running"
    finally:
        jobs._kill_pid_tree(meta.get("pid"))


def test_worker_lock_held_states(tmp_path):
    import fcntl

    lock = tmp_path / "worker.lock"
    assert jobs._worker_lock_held(lock) is None  # missing file -> indeterminate
    lock.write_bytes(b"")
    assert jobs._worker_lock_held(lock) is False  # created but unheld -> free
    fd = os.open(str(lock), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert jobs._worker_lock_held(lock) is True  # held by a live fd -> alive
    finally:
        os.close(fd)
    assert jobs._worker_lock_held(lock) is False  # released after close -> free


def test_terminate_does_not_signal_when_predicate_reports_dead(monkeypatch):
    # The grace/escalation path must stay gated on verified liveness: if the worker
    # is no longer (lock-)alive, neither SIGTERM nor SIGKILL is sent — so a PID reused
    # after the worker exits during the grace window is never signaled.
    calls = []
    monkeypatch.setattr(jobs, "_signal_proc", lambda *a: calls.append(("term", *a)))
    monkeypatch.setattr(jobs, "_kill_pid_tree", lambda *a: calls.append(("kill", *a)))
    jobs._terminate_pid_tree(424242, grace_seconds=0.5, is_alive=lambda: False)
    assert calls == []


def test_timeout_grace_completion_prefers_result(tmp_path, monkeypatch):
    # If the worker completes during the grace window (writes result.json), the
    # deadline path must surface that result as "done", not mask it as "timeout".
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(_factory("import time; time.sleep(30)"), cwd, kind="k")
    jd = store._job_dir(cwd, job_id)
    meta = store._read_meta(jd)
    pid = meta["pid"]
    try:
        meta["deadline_epoch"] = time.time() - 1  # already overran
        store._write_meta(jd, meta)
        (jd / "result.json").write_text('{"ok": true, "tool": "t"}')
        monkeypatch.setattr(jobs, "_terminate_pid_tree", lambda *a, **k: None)
        st = store.status(cwd, job_id)
        assert st["status"] == "done"  # result preserved, not a timeout
    finally:
        jobs._kill_pid_tree(pid)


def test_lock_held_marks_unowned_job_running(tmp_path):
    # Even unowned (post-restart), a job whose worker still holds the per-job lock is
    # positively verified as alive and IS running/signalable.
    import fcntl

    store = _store(tmp_path)
    cwd = str(tmp_path)
    jd, meta = _persist_job(store, cwd, "jid", pid=os.getpid(), owner="other-instance")
    lock = jd / "worker.lock"
    lock.write_bytes(b"")
    fd = os.open(str(lock), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert store._job_running(jd, meta) is True
    finally:
        os.close(fd)


def test_read_meta_and_envelope_missing(tmp_path):
    assert JobStore._read_meta(tmp_path / "nope") is None
    assert JobStore._read_envelope(tmp_path / "nope") is None


def test_read_envelope_garbage_and_nonobject(tmp_path):
    (tmp_path / "result.json").write_text("not json {")
    assert JobStore._read_envelope(tmp_path) is None
    (tmp_path / "result.json").write_text("[1, 2, 3]")  # valid JSON, not an object
    assert JobStore._read_envelope(tmp_path) is None
    (tmp_path / "result.json").write_text("   ")  # blank
    assert JobStore._read_envelope(tmp_path) is None


def test_rmtree_missing_is_silent(tmp_path):
    JobStore._rmtree(tmp_path / "does-not-exist")  # must not raise


def test_reap_skips_non_dir_entries(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    # create the workspace dir, then drop a stray file beside job dirs
    job_id, _ = store.start(_factory(_WRITE_DONE), cwd, kind="k")
    _wait_terminal(store, cwd, job_id)
    ws = store._ws_dir(cwd)
    (ws / "stray.txt").write_text("x")
    # list_jobs reaps the workspace and must ignore the stray file
    listed = store.list_jobs(cwd)
    assert any(j["job_id"] == job_id for j in listed)


def test_read_meta_unparseable(tmp_path):
    jd = tmp_path / "jd"
    jd.mkdir()
    (jd / "meta.json").write_text("{bad json")
    assert JobStore._read_meta(jd) is None


def test_count_cap_keeps_running_job(tmp_path):
    # max_count=1 with one running + one done: the running job is never evicted.
    store = _store(tmp_path, max_count=1)
    cwd = str(tmp_path)
    running_id, _ = store.start(_factory("import time; time.sleep(30)"), cwd, kind="k")
    done_id, _ = store.start(_factory(_WRITE_DONE), cwd, kind="k")
    _wait_terminal(store, cwd, done_id)
    # starting a third (done) job triggers the cap; the running job stays.
    third_id, _ = store.start(_factory(_WRITE_DONE), cwd, kind="k")
    _wait_terminal(store, cwd, third_id)
    assert store.status(cwd, running_id) is not None
    store.cancel(cwd, running_id)


def test_deadline_and_expiry_helpers(tmp_path):
    store = _store(tmp_path)
    # missing started/deadline -> falls back to max_seconds
    assert store._deadline_seconds({}) == store.max_seconds
    # missing completed_epoch -> no expiry, not expired
    assert store._expires_at({}) is None
    assert store._expired({}) is False


def test_poll_backoff_grows_and_is_bounded():
    from codex_in_claude._core.jobs import (
        DEFAULT_POLL_AFTER_MS,
        MAX_POLL_AFTER_MS,
        poll_backoff_ms,
    )

    # Floored at the base for a just-started job, then grows with elapsed, then caps.
    assert poll_backoff_ms(0) == DEFAULT_POLL_AFTER_MS
    assert poll_backoff_ms(DEFAULT_POLL_AFTER_MS // 2) == DEFAULT_POLL_AFTER_MS
    assert poll_backoff_ms(5000) == 5000
    assert poll_backoff_ms(10_000_000) == MAX_POLL_AFTER_MS
    # Honors a custom base/cap.
    assert poll_backoff_ms(0, base=2000) == 2000
    assert poll_backoff_ms(9999, base=1, cap=3000) == 3000
    # A cap below the base never drops the result under the base (floor wins).
    assert poll_backoff_ms(0, base=5000, cap=1000) == 5000
    assert poll_backoff_ms(9999, base=5000, cap=1000) == 5000


def test_status_running_poll_after_ms_grows(tmp_path):
    from codex_in_claude._core.jobs import DEFAULT_POLL_AFTER_MS, MAX_POLL_AFTER_MS

    store = _store(tmp_path)
    # A running job ~6s in gets a grown poll hint, bounded by the cap.
    running = {"job_id": "j", "started_epoch": time.time() - 6}
    d = store._status_dict(tmp_path, running, "running")
    assert DEFAULT_POLL_AFTER_MS < d["poll_after_ms"] <= MAX_POLL_AFTER_MS
    # A terminal job is not polled, so it keeps the flat base.
    done = {"job_id": "j", "started_epoch": time.time() - 6, "completed_epoch": time.time()}
    assert store._status_dict(tmp_path, done, "done")["poll_after_ms"] == store.poll_after_ms


def test_read_envelope_oserror(tmp_path):
    # result.json is a directory -> reading raises OSError, handled as None
    (tmp_path / "result.json").mkdir()
    assert JobStore._read_envelope(tmp_path) is None


def test_reap_and_list_skip_unparseable_meta(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    # a healthy done job, plus a job dir with unparseable meta
    good_id, _ = store.start(_factory(_WRITE_DONE), cwd, kind="k")
    _wait_terminal(store, cwd, good_id)
    bad = store._ws_dir(cwd) / "deadbeef"
    bad.mkdir()
    (bad / "meta.json").write_text("{not json")
    listed = store.list_jobs(cwd)  # exercises reap + list skip-None-meta branches
    ids = {j["job_id"] for j in listed}
    assert good_id in ids
    assert "deadbeef" not in ids


# --- ActivityRecorder + activity read ----------------------------------------


def test_activity_recorder_writes_counts_and_timestamp(tmp_path: Path):
    rec = jobs.ActivityRecorder(tmp_path)
    t = time.time()
    rec.record(t)
    rec.flush()
    data = json.loads((tmp_path / "activity.json").read_text())
    assert data["events_seen"] == 1
    assert abs(data["last_event_epoch"] - t) < 1.0


def test_activity_recorder_counts_monotonically_and_never_writes_raw_events(tmp_path: Path):
    rec = jobs.ActivityRecorder(tmp_path)
    for i in range(10):
        rec.record(1000.0 + i)
    rec.flush()
    data = json.loads((tmp_path / "activity.json").read_text())
    assert data["events_seen"] == 10
    assert set(data) == {"events_seen", "last_event_epoch"}  # counters/timestamps only


def test_status_dict_includes_activity_fields(tmp_path: Path):
    store = jobs.JobStore(root=tmp_path, ttl_seconds=60, max_seconds=60, max_count=10)
    jid, _ = store.start(lambda jd: ["true"], cwd=str(tmp_path), kind="codex_consult")
    jd = store._job_dir(str(tmp_path), jid)
    rec = jobs.ActivityRecorder(jd)
    rec.record(time.time())
    rec.flush()
    status = store.status(str(tmp_path), jid)
    assert status is not None
    assert status["events_seen"] == 1
    assert status["last_event_at"] is not None
    assert status["event_age_ms"] is not None and status["event_age_ms"] >= 0


def test_status_dict_activity_defaults_when_no_file(tmp_path: Path):
    store = jobs.JobStore(root=tmp_path, ttl_seconds=60, max_seconds=60, max_count=10)
    jid, _ = store.start(lambda jd: ["true"], cwd=str(tmp_path), kind="codex_consult")
    status = store.status(str(tmp_path), jid)
    assert status is not None
    assert status["events_seen"] == 0
    assert status["last_event_at"] is None
    assert status["event_age_ms"] is None


def test_read_activity_tolerates_corrupt_file(tmp_path: Path):
    (tmp_path / "activity.json").write_text("{not json")
    assert jobs.JobStore._read_activity(tmp_path) == (0, None)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_read_activity_degrades_nonfinite_epoch(tmp_path: Path, bad: str):
    # json.loads accepts NaN/Infinity by default; a non-finite epoch must degrade to
    # None (not crash datetime.fromtimestamp/int downstream). The count stays valid.
    (tmp_path / "activity.json").write_text(f'{{"events_seen": 1, "last_event_epoch": {bad}}}')
    assert jobs.JobStore._read_activity(tmp_path) == (1, None)


def test_read_activity_degrades_negative_count(tmp_path: Path):
    (tmp_path / "activity.json").write_text('{"events_seen": -5, "last_event_epoch": 1000.0}')
    assert jobs.JobStore._read_activity(tmp_path) == (0, 1000.0)


@pytest.mark.parametrize("bad", ["1e308", "-1e308", "1e18", "1e15"])
def test_read_activity_degrades_unrepresentable_epoch(tmp_path: Path, bad: str):
    # A finite epoch can still be out of range for datetime.fromtimestamp(), which
    # raises OverflowError/OSError/ValueError depending on the platform. Such an
    # epoch must degrade to None so status/list don't crash. The count stays valid.
    (tmp_path / "activity.json").write_text(f'{{"events_seen": 1, "last_event_epoch": {bad}}}')
    assert jobs.JobStore._read_activity(tmp_path) == (1, None)


def test_status_survives_nonfinite_epoch(tmp_path: Path):
    # Regression: a corrupt activity.json with a non-finite epoch must not crash status.
    store = jobs.JobStore(root=tmp_path, ttl_seconds=60, max_seconds=60, max_count=10)
    jid, _ = store.start(lambda jd: ["true"], cwd=str(tmp_path), kind="codex_consult")
    jd = store._job_dir(str(tmp_path), jid)
    (jd / "activity.json").write_text('{"events_seen": 1, "last_event_epoch": NaN}')
    status = store.status(str(tmp_path), jid)
    assert status is not None
    assert status["events_seen"] == 1
    assert status["last_event_at"] is None
    assert status["event_age_ms"] is None


def test_status_survives_unrepresentable_epoch(tmp_path: Path):
    # Regression (#150): a finite-but-out-of-range epoch (e.g. 1e308) used to reach
    # datetime.fromtimestamp() and crash status/list with internal_error.
    store = jobs.JobStore(root=tmp_path, ttl_seconds=60, max_seconds=60, max_count=10)
    jid, _ = store.start(lambda jd: ["true"], cwd=str(tmp_path), kind="codex_consult")
    jd = store._job_dir(str(tmp_path), jid)
    (jd / "activity.json").write_text('{"events_seen": 1, "last_event_epoch": 1e308}')
    status = store.status(str(tmp_path), jid)
    assert status is not None
    assert status["events_seen"] == 1
    assert status["last_event_at"] is None
    assert status["event_age_ms"] is None


def test_activity_observer_end_to_end_into_job_store(tmp_path: Path):
    """End-to-end: _worker._activity_observer drives ActivityRecorder → activity.json
    → JobStore.status surfaces events_seen, last_event_at, and event_age_ms."""
    from codex_in_claude import _worker

    store = jobs.JobStore(root=tmp_path / "jobs", ttl_seconds=3600, max_seconds=60, max_count=10)
    cwd = str(tmp_path)
    # Use 'true' (a fast no-op) so the process exits quickly with no result.json;
    # the worker subprocess is NOT the point — we simulate the activity write directly.
    job_id, _ = store.start(lambda jd: ["true"], cwd=cwd, kind="codex_delegate")
    jd = store._job_dir(cwd, job_id)

    observer, recorder = _worker._activity_observer(jd)

    # Three lines: two JSONL objects (count) + one non-object (ignored).
    observer('{"type":"token_count"}\n')  # counts
    observer("plain text line\n")  # ignored
    observer('{"type":"agent_message"}\n')  # counts

    recorder.flush()

    status = store.status(cwd, job_id)
    assert status is not None
    assert status["events_seen"] == 2
    assert status["last_event_at"] is not None
    assert status["event_age_ms"] is not None and status["event_age_ms"] >= 0
