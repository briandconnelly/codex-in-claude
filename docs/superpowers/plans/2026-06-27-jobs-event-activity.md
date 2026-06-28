# Polled Event-Activity Signal for Async Jobs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give polling agents an advisory liveness signal — is an async job *producing output* or has it gone quiet — by persisting Codex `--json` event activity to the job directory and surfacing it in `JobStatus`.

**Architecture:** A detached worker observes Codex's stdout event stream through an *observer-gated* streaming path in `_core/runtime.py` (the synchronous paid tools keep the existing `communicate()` path byte-for-byte). The worker records counts/timestamps only into `<job_dir>/activity.json`; `JobStore` reads that opaque file on each poll and maps it into three advisory `JobStatus` fields. Native MCP progress is unchanged; the new signal is advertised under a separate `AsyncLifecycle.activity_support`.

**Tech Stack:** Python 3.11+, FastMCP, pydantic, anyio, pytest, `uv`, `ruff`, `ty`.

## Global Constraints

- Tooling: `uv` only. Gate before any task is "done": `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`.
- Tests: TDD — failing test first. 95% coverage floor (CI-enforced). Test files mirror the module (`tests/test_<module>.py`).
- `_core` boundary: `src/codex_in_claude/_core/*` MUST NOT import from the parent `codex_in_claude` package (one-way dependency).
- Tolerant events: never depend on a specific Codex event *shape*. Activity counting treats a line as an "event" by a cheap structural check, not by parsing a known schema.
- Data retention: persist counters/timestamps ONLY in `activity.json`. Never write raw Codex events to disk.
- Advisory semantics: the new fields are advisory. Silence ≠ stall. Nothing auto-cancels on them. Say so in docstrings.
- Surface/contract: any agent-visible surface change bumps `FINGERPRINT` (currently `"codex-in-claude/0.1/schema-16"`) and regenerates `tests/fixtures/manifest_snapshot.json` in the SAME commit. Add the change under `## [Unreleased]` in `CHANGELOG.md`. Do NOT bump version literals (`pyproject.toml`, `.claude-plugin/plugin.json`, `.mcp.json` pin) — this is a feature PR, not the release PR.
- Conventional Commits; breaking change pre-1.0 ⇒ minor, marked `feat(jobs)!` / `BREAKING CHANGE:`.
- Branch already created: `feat/jobs-event-activity`.

---

### Task 1: Observer-gated streaming in `_core/runtime.py`

Add an optional stdout line-observer to `run_async`. When absent, behavior is unchanged (the `communicate()` path). When present, drain stdout/stderr concurrently, invoking the callback per stdout line, while preserving full captured streams, timeout, cancellation, and process-group teardown.

**Files:**
- Modify: `src/codex_in_claude/_core/runtime.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `run_async(..., on_stdout_line: Callable[[str], None] | None = None)`. When set, each stdout line (including its trailing newline) is passed to the callback as it is read. Return type unchanged (`CommandRun`); `stdout`/`stderr` carry the complete captured streams exactly as the non-observer path would.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_runtime.py`:

```python
import sys

import anyio

from codex_in_claude._core import runtime


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_run_async_observer_receives_each_stdout_line():
    lines: list[str] = []
    code = "import sys\nfor i in range(5):\n    print(f'line{i}')\nsys.stdout.flush()"
    run = anyio.run(
        lambda: runtime.run_async(
            _py(code), cwd=".", timeout_seconds=10, on_stdout_line=lines.append
        )
    )
    assert run.exit_code == 0
    assert [ln.strip() for ln in lines] == [f"line{i}" for i in range(5)]
    # Full stream is still captured intact.
    assert run.stdout.splitlines() == [f"line{i}" for i in range(5)]


def test_run_async_observer_handles_large_simultaneous_stdout_stderr():
    # Interleaved heavy output on both pipes must not deadlock.
    code = (
        "import sys\n"
        "for i in range(2000):\n"
        "    sys.stdout.write('o'*200+'\\n'); sys.stderr.write('e'*200+'\\n')\n"
    )
    seen: list[str] = []
    run = anyio.run(
        lambda: runtime.run_async(
            _py(code), cwd=".", timeout_seconds=30, on_stdout_line=seen.append
        )
    )
    assert run.exit_code == 0
    assert len(seen) == 2000
    assert run.stdout.count("o" * 200) == 2000
    assert run.stderr.count("e" * 200) == 2000


def test_run_async_observer_path_honors_timeout():
    code = "import time\nprint('start', flush=True)\ntime.sleep(30)"
    run = anyio.run(
        lambda: runtime.run_async(
            _py(code), cwd=".", timeout_seconds=1, on_stdout_line=lambda _l: None
        )
    )
    assert run.timed_out is True


def test_run_async_observer_forwards_stdin():
    code = "import sys\nsys.stdout.write(sys.stdin.read().upper())"
    lines: list[str] = []
    run = anyio.run(
        lambda: runtime.run_async(
            _py(code),
            cwd=".",
            timeout_seconds=10,
            stdin_text="hello\n",
            on_stdout_line=lines.append,
        )
    )
    assert run.stdout.strip() == "HELLO"


def test_run_async_without_observer_is_unchanged():
    code = "print('plain')"
    run = anyio.run(lambda: runtime.run_async(_py(code), cwd=".", timeout_seconds=10))
    assert run.stdout.strip() == "plain"
    assert run.exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runtime.py -k observer -v`
Expected: FAIL — `run_async() got an unexpected keyword argument 'on_stdout_line'`.

- [ ] **Step 3: Implement the streaming path**

In `src/codex_in_claude/_core/runtime.py`, add `import threading` to the imports, then add this module-level helper (after `kill_process_tree`):

```python
def _wait_streaming(
    proc: subprocess.Popen,
    stdin_text: str | None,
    on_stdout_line: Callable[[str], None],
    timeout_seconds: int,
) -> tuple[str, str, bool]:
    """Drain stdout/stderr concurrently, calling ``on_stdout_line`` per stdout line.

    Three daemon threads (stdin writer, stdout reader, stderr reader) avoid the
    classic pipe-buffer deadlock. The complete streams are reassembled and returned
    so the caller's final parse is identical to the communicate() path. On timeout
    the tree is killed and ``timed_out`` is True."""
    out_chunks: list[str] = []
    err_chunks: list[str] = []

    def _pump_stdout() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            out_chunks.append(line)
            with contextlib.suppress(Exception):
                on_stdout_line(line)

    def _pump_stderr() -> None:
        if proc.stderr is not None:
            err_chunks.append(proc.stderr.read())

    def _write_stdin() -> None:
        if proc.stdin is None:
            return
        with contextlib.suppress(OSError):
            if stdin_text is not None:
                proc.stdin.write(stdin_text)
            proc.stdin.close()

    threads = [
        threading.Thread(target=_write_stdin, daemon=True),
        threading.Thread(target=_pump_stdout, daemon=True),
        threading.Thread(target=_pump_stderr, daemon=True),
    ]
    for t in threads:
        t.start()
    try:
        proc.wait(timeout=timeout_seconds)
        timed_out = False
    except subprocess.TimeoutExpired:
        logger.warning(
            "subprocess pid=%s exceeded %ss; killing process group", proc.pid, timeout_seconds
        )
        kill_process_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        timed_out = True
    for t in threads:
        t.join(timeout=1)
    return "".join(out_chunks), "".join(err_chunks), timed_out
```

Add the parameter to `run_async` and branch inside its inner `_wait`:

```python
async def run_async(
    cmd: list[str],
    cwd: str,
    timeout_seconds: int,
    stdin_text: str | None = None,
    *,
    env: dict[str, str] | None = None,
    on_stdout_line: Callable[[str], None] | None = None,
) -> CommandRun:
```

Replace the body of the inner `_wait` so the streaming path is taken only when an observer is supplied:

```python
    def _wait() -> tuple[str, str, bool]:
        if on_stdout_line is not None:
            return _wait_streaming(proc, stdin_text, on_stdout_line, timeout_seconds)
        try:
            out, err = proc.communicate(input=stdin_text, timeout=timeout_seconds)
            return out, err, False
        except subprocess.TimeoutExpired:
            logger.warning(
                "subprocess pid=%s exceeded %ss; killing process group", proc.pid, timeout_seconds
            )
            kill_process_tree(proc)
            out, err = proc.communicate()
            return out, err, True
```

Add the import at the top of the module: `from collections.abc import Callable`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: PASS (new and existing tests).

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest tests/test_runtime.py
git add src/codex_in_claude/_core/runtime.py tests/test_runtime.py
git commit -m "feat(core): add observer-gated stdout streaming to run_async"
```

---

### Task 2: `ActivityRecorder` + activity read in `_core/jobs.py`

Own the `activity.json` storage contract in `_core`: a writer the worker drives, and a reader folded into the status dict. No agent-visible surface change yet (the status dict is internal).

**Files:**
- Modify: `src/codex_in_claude/_core/jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `ActivityRecorder(job_dir: Path)` with `.record(now_epoch: float) -> None` (increments an in-memory counter, throttled atomic write of `{"events_seen": int, "last_event_epoch": float}`) and `.flush() -> None` (always writes the current state).
  - `JobStore._status_dict` now includes keys `events_seen: int`, `last_event_at: str | None` (ISO-8601), `event_age_ms: int | None` (measured to `completed_epoch or now`; `None` when no events yet).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_jobs.py`:

```python
import json
import time
from pathlib import Path

from codex_in_claude._core import jobs


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_jobs.py -k "activity or status_dict" -v`
Expected: FAIL — `module 'codex_in_claude._core.jobs' has no attribute 'ActivityRecorder'`.

- [ ] **Step 3: Implement the recorder and reader**

In `src/codex_in_claude/_core/jobs.py`, add a throttle constant near the other module constants:

```python
# Min seconds between activity.json disk writes while a job runs; the first event
# and the final flush always write. Keeps the hot path off the disk on every line.
ACTIVITY_WRITE_THROTTLE_S = 0.5
```

Add the recorder class (after `poll_backoff_ms`):

```python
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
```

Add the reader as a `@staticmethod` on `JobStore`:

```python
    @staticmethod
    def _read_activity(jd: Path) -> tuple[int, float | None]:
        """(events_seen, last_event_epoch) from activity.json; (0, None) if absent/
        corrupt. Treated as opaque caller-declared state, like cleanup.json."""
        try:
            data = json.loads((jd / "activity.json").read_text())
        except (OSError, json.JSONDecodeError):
            return 0, None
        if not isinstance(data, dict):
            return 0, None
        count = data.get("events_seen")
        epoch = data.get("last_event_epoch")
        count = count if isinstance(count, int) and not isinstance(count, bool) else 0
        epoch = epoch if isinstance(epoch, (int, float)) and not isinstance(epoch, bool) else None
        return count, epoch
```

Fold the activity into `_status_dict` (compute before the `return`, then add the three keys):

```python
        events_seen, last_epoch = self._read_activity(jd)
        end = meta.get("completed_epoch") or time.time()
        last_event_at = (
            datetime.fromtimestamp(last_epoch, UTC).isoformat() if last_epoch is not None else None
        )
        event_age_ms = (
            max(0, int((end - last_epoch) * 1000)) if last_epoch is not None else None
        )
```

Add to the returned dict:

```python
            "events_seen": events_seen,
            "last_event_at": last_event_at,
            "event_age_ms": event_age_ms,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest tests/test_jobs.py
git add src/codex_in_claude/_core/jobs.py tests/test_jobs.py
git commit -m "feat(core): record and read polled job event activity"
```

---

### Task 3: Surface change — schemas, server mapping, lifecycle, manifest, changelog

The single atomic surface commit: add the advisory `JobStatus` fields and `AsyncLifecycle.activity_support`, map them in the server, bump `FINGERPRINT`, regenerate the manifest snapshot, and update docs. The server mapping uses `.get(...)` defaults so it is correct even before the worker (Task 6) populates the values.

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (JobStatus, AsyncLifecycle, FINGERPRINT)
- Modify: `src/codex_in_claude/server.py` (`_job_status_model`, `_ASYNC_LIFECYCLE`)
- Modify: `tests/fixtures/manifest_snapshot.json` (regenerated), `tests/test_manifest.py` (`EXPECTED_MANIFEST_HASH`)
- Modify: `CHANGELOG.md`
- Test: `tests/test_schemas.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: `JobStore._status_dict` keys from Task 2 (`events_seen`, `last_event_at`, `event_age_ms`).
- Produces: `JobStatus.events_seen: int = 0`, `JobStatus.last_event_at: str | None = None`, `JobStatus.event_age_ms: int | None = None`; `AsyncLifecycle.activity_support: Literal["codex_events"] = "codex_events"`, `AsyncLifecycle.event_count_field/last_event_field/event_age_field: str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_schemas.py`:

```python
from codex_in_claude.schemas import AsyncLifecycle, JobStatus, FINGERPRINT


def test_jobstatus_has_advisory_activity_fields_defaulting_safely():
    s = JobStatus(
        job_id="j", kind="codex_consult", status="running", started_at="t",
        elapsed_ms=1, deadline_seconds=60, ttl_seconds=60,
        workspace={"cwd": "/x", "source": "param"},
    )
    assert s.events_seen == 0
    assert s.last_event_at is None
    assert s.event_age_ms is None


def test_async_lifecycle_advertises_activity_without_touching_progress_support():
    lc = AsyncLifecycle(
        poll_tool="p", result_tool="r", consume_tool="c", cancel_tool="x", list_tool="l",
        status_field="status", result_ready_field="result_available",
        poll_after_field="poll_after_ms",
        activity_support="codex_events",
        event_count_field="events_seen", last_event_field="last_event_at",
        event_age_field="event_age_ms",
    )
    assert lc.progress_support == "none"  # native progress meaning preserved
    assert lc.activity_support == "codex_events"


def test_fingerprint_bumped_to_schema_17():
    assert FINGERPRINT == "codex-in-claude/0.1/schema-17"
```

Add to `tests/test_server.py`:

```python
def test_job_status_model_maps_activity_fields():
    from codex_in_claude.server import _job_status_model
    from codex_in_claude.schemas import Workspace

    data = {
        "job_id": "j", "kind": "codex_consult", "status": "running",
        "started_at": "t", "elapsed_ms": 5, "deadline_seconds": 60,
        "poll_after_ms": 1000, "ttl_seconds": 60, "expires_at": None,
        "result_available": False, "cleanup_warnings": [],
        "events_seen": 3, "last_event_at": "2026-06-27T00:00:00+00:00", "event_age_ms": 250,
    }
    model = _job_status_model(data, Workspace(cwd="/x", source="param"))
    assert model.events_seen == 3
    assert model.last_event_at == "2026-06-27T00:00:00+00:00"
    assert model.event_age_ms == 250
```

(Confirm the `Workspace` constructor args against `schemas.py` and adjust the literal if its fields differ.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schemas.py -k "activity or fingerprint" tests/test_server.py -k activity -v`
Expected: FAIL — unexpected keyword args / `FINGERPRINT` mismatch.

- [ ] **Step 3: Implement the schema and server changes**

In `src/codex_in_claude/schemas.py`, bump the fingerprint:

```python
FINGERPRINT = "codex-in-claude/0.1/schema-17"
```

Add to `JobStatus` (after `cleanup_warnings`, before `workspace`):

```python
    # Advisory polled event-activity (#139). Derived from Codex's --json stream;
    # silence is NOT proof of a stall and nothing auto-cancels on these. They show
    # RECENT output, complementing elapsed_ms (total runtime).
    events_seen: int = 0  # monotonic count of Codex events observed
    last_event_at: str | None = None  # ISO-8601 of the most recent event, or None
    event_age_ms: int | None = None  # now - last_event (to completion if terminal)
```

Add to `AsyncLifecycle` (after `poll_after_field`):

```python
    # Polled event-activity (#139). SEPARATE from progress_support: this is not
    # native notifications/progress, it is a disk-persisted, poll-read activity
    # signal. progress_support stays "none" so the native-progress meaning is intact.
    activity_support: Literal["codex_events"] = "codex_events"
    event_count_field: str  # "events_seen"
    last_event_field: str  # "last_event_at"
    event_age_field: str  # "event_age_ms"
```

Update the `AsyncLifecycle` class docstring to note the activity signal is polled, not native progress.

In `src/codex_in_claude/server.py`, extend `_job_status_model`'s `JobStatus(...)` with:

```python
        events_seen=data.get("events_seen", 0),
        last_event_at=data.get("last_event_at"),
        event_age_ms=data.get("event_age_ms"),
```

Extend `_ASYNC_LIFECYCLE` with:

```python
    activity_support="codex_events",
    event_count_field="events_seen",
    last_event_field="last_event_at",
    event_age_field="event_age_ms",
```

- [ ] **Step 4: Regenerate the manifest snapshot and update its hash**

```bash
uv run python -m codex_in_claude.manifest > tests/fixtures/manifest_snapshot.json
uv run pytest tests/test_manifest.py -v
```

The test fails with the new expected hash. Copy that hash into `EXPECTED_MANIFEST_HASH` in `tests/test_manifest.py`, then re-run:

```bash
uv run pytest tests/test_manifest.py -v
```
Expected: PASS.

- [ ] **Step 5: Update CHANGELOG.md**

Under `## [Unreleased]`, add (create an `### Added` subsection if absent):

```markdown
### Added
- `codex_job_status` now reports advisory polled event-activity for async jobs —
  `events_seen`, `last_event_at`, `event_age_ms` — so a long-running job can be told
  apart from a stalled one. Advertised via `AsyncLifecycle.activity_support`
  (`"codex_events"`); native `progress_support` is unchanged (`"none"`). (#139)
```

- [ ] **Step 6: Run the full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`
Expected: PASS, including `tests/test_manifest.py`.

- [ ] **Step 7: Commit**

```bash
git add src/codex_in_claude/schemas.py src/codex_in_claude/server.py \
        tests/fixtures/manifest_snapshot.json tests/test_manifest.py \
        tests/test_schemas.py tests/test_server.py CHANGELOG.md
git commit -m "feat(jobs)!: surface advisory polled event-activity in JobStatus

Adds events_seen/last_event_at/event_age_ms and AsyncLifecycle.activity_support;
progress_support stays \"none\". Bumps FINGERPRINT to schema-17.

BREAKING CHANGE: JobStatus and AsyncLifecycle gain agent-visible fields."
```

---

### Task 4: Thread an event observer through `codex.run_codex_exec`

**Files:**
- Modify: `src/codex_in_claude/codex.py`
- Test: `tests/test_codex.py`

**Interfaces:**
- Consumes: `runtime.run_async(..., on_stdout_line=...)` from Task 1.
- Produces: `run_codex_exec(..., on_event: Callable[[str], None] | None = None)`, forwarded to `run_async` as `on_stdout_line`. Default `None` ⇒ synchronous paths unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_codex.py`:

```python
import sys

import anyio

from codex_in_claude import codex


def test_run_codex_exec_forwards_on_event(monkeypatch):
    captured = {}

    async def fake_run_async(cmd, *, cwd, timeout_seconds, stdin_text=None, on_stdout_line=None):
        captured["on_stdout_line"] = on_stdout_line
        from codex_in_claude._core.runtime import CommandRun
        return CommandRun("", "", 0, 1, False)

    monkeypatch.setattr(codex.runtime, "run_async", fake_run_async)
    sentinel = lambda _l: None
    anyio.run(
        lambda: codex.run_codex_exec(
            "p", cwd=".", sandbox="read-only", isolation="inherit",
            timeout_seconds=10, on_event=sentinel,
        )
    )
    assert captured["on_stdout_line"] is sentinel
```

(Match `fake_run_async`'s signature to `run_async` after Task 1; adjust kwargs if needed.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_codex.py -k on_event -v`
Expected: FAIL — `run_codex_exec() got an unexpected keyword argument 'on_event'`.

- [ ] **Step 3: Implement**

In `src/codex_in_claude/codex.py`, add to the `run_codex_exec` signature (keyword-only block):

```python
    on_event: Callable[[str], None] | None = None,
```

Add `from collections.abc import Callable` to the imports (guarded under `TYPE_CHECKING` is fine since it is only an annotation; if `ty` complains, import it unconditionally). Forward it in the `runtime.run_async(...)` call:

```python
        run = await runtime.run_async(
            cmd, cwd=cwd, timeout_seconds=timeout_seconds, stdin_text=prompt,
            on_stdout_line=on_event,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_codex.py -v`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest tests/test_codex.py
git add src/codex_in_claude/codex.py tests/test_codex.py
git commit -m "feat(core): forward an event observer through run_codex_exec"
```

---

### Task 5: Thread the observer through the three orchestrations

**Files:**
- Modify: `src/codex_in_claude/orchestration.py` (`run_consult`, `run_review`)
- Modify: `src/codex_in_claude/delegate.py` (`run_delegate`)
- Test: `tests/test_orchestration.py`, `tests/test_delegate.py`

**Interfaces:**
- Consumes: `run_codex_exec(..., on_event=...)` from Task 4.
- Produces: `run_consult(..., on_event=None)`, `run_review(..., on_event=None)`, `run_delegate(..., on_event=None)` — each forwarding to its `run_codex_exec` call.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_orchestration.py`:

```python
import anyio

from codex_in_claude import orchestration
from codex_in_claude.schemas import Meta


def test_run_consult_forwards_on_event(monkeypatch):
    captured = {}

    async def fake_exec(prompt, **kwargs):
        captured["on_event"] = kwargs.get("on_event")
        from codex_in_claude._core.runtime import CommandRun
        return orchestration.codex.CodexExecResult(run=CommandRun("", "", 0, 1, False), last_message=None)

    monkeypatch.setattr(orchestration.codex, "run_codex_exec", fake_exec)
    sentinel = lambda _l: None
    meta = Meta(cwd=".", tier="consult", sandbox="read-only", isolation="inherit",
                timeout_seconds=10, elapsed_ms=0)
    anyio.run(
        lambda: orchestration.run_consult(
            "q", ".", meta, sandbox="read-only", isolation="inherit",
            timeout_seconds=10, model=None, on_event=sentinel,
        )
    )
    assert captured["on_event"] is sentinel
```

(Build `Meta` with whatever required fields it has — copy from an existing orchestration test. Add an analogous `test_run_delegate_forwards_on_event` in `tests/test_delegate.py` and a review variant if review tests already stub `run_codex_exec`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestration.py -k on_event tests/test_delegate.py -k on_event -v`
Expected: FAIL — unexpected keyword argument `on_event`.

- [ ] **Step 3: Implement**

`orchestration.run_consult` — add `on_event: Callable[[str], None] | None = None` to the keyword-only block and pass `on_event=on_event` to its `run_codex_exec(...)` call.

`orchestration.run_review` — same: add the param and pass `on_event=on_event` to its `run_codex_exec(...)` call.

`delegate.run_delegate` — add `on_event: Callable[[str], None] | None = None` to the keyword-only block and pass `on_event=on_event` to its `run_codex_exec(...)` call.

Ensure `Callable` is imported in both modules (`delegate.py` already imports it; add to `orchestration.py` if missing).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestration.py tests/test_delegate.py -v`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest tests/test_orchestration.py tests/test_delegate.py
git add src/codex_in_claude/orchestration.py src/codex_in_claude/delegate.py \
        tests/test_orchestration.py tests/test_delegate.py
git commit -m "feat(core): thread the event observer through consult/review/delegate"
```

---

### Task 6: Wire the worker to record activity end-to-end

The worker builds an `ActivityRecorder` for its job dir and passes an observer that records each JSONL event line, flushing at the end. This closes the loop: a running async job now populates `events_seen`/`last_event_at`/`event_age_ms`.

**Files:**
- Modify: `src/codex_in_claude/_worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `jobs.ActivityRecorder` (Task 2); `on_event=` on `run_delegate`/`run_consult`/`run_review` (Task 5).
- Produces: after a worker run, `<job_dir>/activity.json` reflects the events the Codex stream emitted.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_worker.py`:

```python
import json
from pathlib import Path

from codex_in_claude import _worker


def test_worker_makes_observer_that_counts_jsonl_event_lines(tmp_path: Path):
    rec_dir = tmp_path
    observer, recorder = _worker._activity_observer(rec_dir)
    observer('{"type":"token_count"}\n')   # counts (JSONL object)
    observer("\n")                          # blank — ignored
    observer("not-json line\n")             # non-object — ignored
    observer('{"type":"agent_message"}\n')  # counts
    recorder.flush()
    data = json.loads((rec_dir / "activity.json").read_text())
    assert data["events_seen"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worker.py -k observer -v`
Expected: FAIL — `module 'codex_in_claude._worker' has no attribute '_activity_observer'`.

- [ ] **Step 3: Implement**

In `src/codex_in_claude/_worker.py`, add `import time` and `from collections.abc import Callable`, import the recorder (`from codex_in_claude._core.jobs import ActivityRecorder`), and add the factory:

```python
def _activity_observer(job_dir: Path) -> tuple[Callable[[str], None], ActivityRecorder]:
    """An observer for Codex's --json stdout stream that records event ACTIVITY only.

    A line counts as an event when it is a JSONL object (cheap structural check — no
    dependence on a specific event shape). Raw lines are never persisted; the
    recorder writes counts/timestamps to <job_dir>/activity.json."""
    recorder = ActivityRecorder(job_dir)

    def _observe(line: str) -> None:
        if line.strip().startswith("{"):
            recorder.record(time.time())

    return _observe, recorder
```

In `_run`, build the observer once and pass it to whichever orchestration runs, flushing in a `finally`:

```python
async def _run(job_dir: Path, spec: dict, meta: Meta) -> dict:
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
                spec["task"], spec["cwd"], meta,
                sandbox=spec["sandbox"], isolation=spec["isolation"],
                timeout_seconds=spec["timeout_seconds"], model=spec.get("model"),
                git_timeout=spec["git_timeout"], max_diff_bytes=spec.get("max_diff_bytes"),
                on_worktree_parent=lambda parent: _write_cleanup_manifest(job_dir, parent),
                on_event=on_event,
            )
        if kind == "codex_consult":
            return await orchestration.run_consult(
                spec["question"], spec["cwd"], meta,
                sandbox=spec["sandbox"], isolation=spec["isolation"],
                timeout_seconds=spec["timeout_seconds"], model=spec.get("model"),
                extra_context=spec.get("extra_context", ""), on_event=on_event,
            )
        if kind == "codex_review_changes":
            return await orchestration.run_review(
                spec["cwd"], meta,
                scope=spec["scope"], base=spec.get("base"), commit=spec.get("commit"),
                paths=spec.get("paths"), sandbox=spec["sandbox"], isolation=spec["isolation"],
                timeout_seconds=spec["timeout_seconds"], model=spec.get("model"),
                git_timeout=spec["git_timeout"], max_bytes=spec["max_bytes"],
                extra_context=spec.get("extra_context", ""), on_event=on_event,
            )
        raise ValueError(f"unknown job kind: {kind!r}")
    finally:
        recorder.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_worker.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`
Expected: PASS (coverage ≥ 95%).

- [ ] **Step 6: Commit**

```bash
git add src/codex_in_claude/_worker.py tests/test_worker.py
git commit -m "feat(jobs): record Codex event activity from the background worker"
```

---

## Self-Review

**Spec coverage:**
- Three advisory `JobStatus` fields → Task 3 (schema) + Task 2 (values) + Task 6 (populated end-to-end). ✓
- `progress_support` stays `"none"`; separate `activity_support` → Task 3. ✓
- Drop named phases / `taskSupport:"forbidden"` → not implemented by design (no task). ✓
- Observer-gated `run_async` → Task 1. ✓
- Worker writes `activity.json`, counters/timestamps only, throttled atomic + final flush → Task 2 (recorder) + Task 6 (wiring). ✓
- JobStore reads opaque file; `_core` boundary preserved → Task 2. ✓
- FINGERPRINT bump + manifest regen + CHANGELOG; no version-literal bump → Task 3. ✓
- Tests for zero/multiple events, corrupt/missing file, monotonic counts, large stdout/stderr, timeout, cancellation → Tasks 1, 2. ✓

**Placeholder scan:** none — every code step shows the code; commands have expected output.

**Type consistency:** field names `events_seen` / `last_event_at` / `event_age_ms` and `activity_support` / `event_count_field` / `last_event_field` / `event_age_field` are used identically across Tasks 2–6; `on_event` (public) maps to `on_stdout_line` (runtime) consistently; `ActivityRecorder.record(now_epoch)` / `.flush()` signatures match between definition (Task 2) and use (Task 6).

**Open verification notes for the implementer** (confirm against the code, adjust literal test fixtures if needed): the exact `Workspace` and `Meta` constructor fields in the Task 3/5 tests; whether `Callable` should be a runtime import (not under `TYPE_CHECKING`) in `codex.py`/`orchestration.py` to satisfy `ty`.
