# M4 — Background Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add detached background execution for the propose tier — `codex_delegate_async` plus the job lifecycle tools (`codex_job_status`, `codex_job_result`, `codex_job_consume_result`, `codex_job_cancel`, `codex_job_list`) — disk-backed, surviving MCP server restarts.

**Architecture:** A generic, disk-backed job store lives in `_core/jobs.py` (a `JobStore` dataclass; no imports from the parent package). It spawns an arbitrary command detached (`start_new_session=True`), reconciles liveness via `waitpid`/`kill(0)`, enforces a wall-clock deadline, TTL eviction, and a per-workspace count cap. The propose orchestration (worktree → `codex exec` → diff → cleanup → final envelope) is too multi-step to be a single subprocess whose stdout is the answer, so it runs in a codex-specific background worker module `codex_in_claude/_worker.py` invoked as `python -m codex_in_claude._worker <job_dir>`. The worker writes the *already-normalized* result envelope to `result.json`; `_core/jobs.py` therefore treats `result.json` as an opaque finished-envelope dict and never needs to know the codex schema. The sync `codex_delegate` and the worker share one orchestration function so the worktree logic is not duplicated.

**Tech Stack:** Python 3.11+, FastMCP v3, pydantic v2, `uv`/`ruff`/`ty`, pytest (95% coverage floor).

## Global Constraints

- `_core/` must never import from its parent package `codex_in_claude` (one-way dependency; extraction seam for a future `agent-bridge`). `_core/jobs.py` therefore takes all config (state dir, ttl, caps) as parameters — it must not read `CODEX_IN_CLAUDE_*` env or import `schemas`/`config`.
- Use `uv` for everything. Lint/format with `ruff`, type-check with `ty`. All three must pass: `uv run ruff check . && uv run ruff format --check . && uv run ty check`.
- 95% test coverage floor (`uv run pytest`). Live `codex` tests are marked `integration` and excluded by default.
- All tools return the envelope in `schemas.py`. The job models (`JobStarted`, `JobStatus`, `JobSummary`, `JobListResult`) and the job `ErrorCode`s already exist — do not redefine them.
- Bump `FINGERPRINT` (currently `codex-in-claude/0.1/schema-3`) to `schema-4` because the tool set grows. Record in `CHANGELOG.md`.
- Conventional commits. Branch is `feat/m1-foundation` (continue on it). Never send `--dangerously-*`. Prompt over stdin only.
- Background jobs are bounded by the wall-clock deadline (`jobs.max_seconds()`), NOT the sync `timeout_seconds`; report the deadline consistently in `meta` and `JobStarted`/`JobStatus`.

---

## File structure

- Create `src/codex_in_claude/_core/jobs.py` — generic `JobStore` (disk lifecycle, PID reconciliation, TTL/caps).
- Create `src/codex_in_claude/_worker.py` — codex-specific background worker entry point (`python -m`).
- Modify `src/codex_in_claude/config.py` — add `job_ttl_seconds()`, `job_max_seconds()`, `job_max_count()`, and a `job_store()` factory.
- Modify `src/codex_in_claude/server.py` — extract shared delegate orchestration; add the six job tools; update `codex_capabilities`.
- Modify `src/codex_in_claude/schemas.py` — bump `FINGERPRINT` to `schema-4`; add `JOB_RESULT_SCHEMA` alias if needed (reuse `RESULT_SCHEMA`).
- Create `tests/test_jobs.py` — `JobStore` lifecycle (fast, no codex).
- Create `tests/test_worker.py` — worker envelope production (codex stubbed).
- Modify `tests/test_server.py` — async tool + lifecycle tools wiring.
- Modify `tests/test_config.py` — job config knobs.
- Modify `CHANGELOG.md`, `README.md`, `skills/collaborating-with-codex/SKILL.md`, `commands/codex/` — docs + slash commands.

---

## Task 1: Config job knobs

**Files:**
- Modify: `src/codex_in_claude/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `state_dir() -> Path` (exists), `_env_int` (exists), `ENV_PREFIX`.
- Produces: `job_ttl_seconds() -> int`, `job_max_seconds() -> int`, `job_max_count() -> int`. Defaults: TTL 86_400 (clamp ≥ 60), max_seconds 1_800 (clamp 60..7_200), max_count 50 (clamp 1..1_000). Env: `CODEX_IN_CLAUDE_JOB_TTL`, `CODEX_IN_CLAUDE_JOB_MAX_SECONDS`, `CODEX_IN_CLAUDE_JOB_MAX_COUNT`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py (add)
def test_job_defaults(monkeypatch):
    for k in ("JOB_TTL", "JOB_MAX_SECONDS", "JOB_MAX_COUNT"):
        monkeypatch.delenv(f"CODEX_IN_CLAUDE_{k}", raising=False)
    assert config.job_ttl_seconds() == 86_400
    assert config.job_max_seconds() == 1_800
    assert config.job_max_count() == 50

def test_job_knobs_clamp(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_JOB_MAX_SECONDS", "5")     # below floor
    monkeypatch.setenv("CODEX_IN_CLAUDE_JOB_MAX_COUNT", "0")        # below floor
    monkeypatch.setenv("CODEX_IN_CLAUDE_JOB_TTL", "10")            # below floor
    assert config.job_max_seconds() == 60
    assert config.job_max_count() == 1
    assert config.job_ttl_seconds() == 60
```

- [ ] **Step 2: Run — expect FAIL** `uv run pytest tests/test_config.py -k job -q`

- [ ] **Step 3: Implement**

```python
# config.py (add near other clamps)
DEFAULT_JOB_TTL_SECONDS = 86_400
DEFAULT_JOB_MAX_SECONDS = 1_800
DEFAULT_JOB_MAX_COUNT = 50

def job_ttl_seconds() -> int:
    return max(60, _env_int(f"{ENV_PREFIX}JOB_TTL", DEFAULT_JOB_TTL_SECONDS))

def job_max_seconds() -> int:
    return max(60, min(7_200, _env_int(f"{ENV_PREFIX}JOB_MAX_SECONDS", DEFAULT_JOB_MAX_SECONDS)))

def job_max_count() -> int:
    return max(1, min(1_000, _env_int(f"{ENV_PREFIX}JOB_MAX_COUNT", DEFAULT_JOB_MAX_COUNT)))
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit** `git commit -am "feat: add background-job config knobs (M4)"`

---

## Task 2: Generic disk-backed JobStore (`_core/jobs.py`)

**Files:**
- Create: `src/codex_in_claude/_core/jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: nothing from the parent package (generic). Stdlib only.
- Produces:
  - `@dataclass JobStore(root: Path, ttl_seconds: int, max_seconds: int, max_count: int)`
  - `JobStore.start(cmd: list[str], cwd: str, *, kind: str, extra: dict | None = None) -> tuple[str, str]` → `(job_id, started_at_iso)`. Spawns `cmd` detached, stdout→`result.json`, stderr→`stderr.log`, writes `meta.json` (job_id, kind, pid, started_epoch, started_at, deadline_epoch, completed_epoch=None, terminal_status=None, extra), `chmod 0700` the workspace dir, enforces count cap.
  - `JobStore.status(cwd, job_id) -> dict | None` → generic status dict: `{job_id, kind, status, started_at, started_epoch, elapsed_ms, deadline_seconds, completed_epoch, expires_at, extra}`; refreshes/reaps the single record; None if absent.
  - `JobStore.result_payload(cwd, job_id, *, consume: bool) -> tuple[dict | None, dict | None]` → `(record_status_dict, result_json_dict)`; `result_json_dict` is the parsed `result.json` (or None if not done/parseable). `record_status_dict` is None when the job does not exist. On `consume=True` and a done job, delete the record after reading.
  - `JobStore.cancel(cwd, job_id) -> dict | None` → kills the process group, marks `cancelled`, returns the status dict (or None if absent).
  - `JobStore.list(cwd) -> list[dict]` → status dicts newest-first; reaps the workspace first.
  - Status values: `running | done | failed | cancelled | timeout`. `done` = process gone AND `result.json` parses to a `dict`. Else `failed`.

Port the liveness/reap/cap logic from `/Users/bdc/projects/cc-plugin-codex/src/cc_plugin_codex/jobs.py` (`_pid_alive`, `_is_running`, `_kill_pid_tree`, `_status_of`, `_reap_workspace`, `_enforce_count_cap`, `_ws_dir`, `_rmtree`, `_read_meta`/`_write_meta`), but: (a) make them methods/free-functions taking the store's config rather than reading env; (b) `result.json` holds the FINAL envelope dict, so `_read_envelope` just parses it and `done`-detection checks it is a dict.

- [ ] **Step 1: Write failing tests** (these run without codex — use a trivial `cmd` like `["python", "-c", "import json,sys; open('result.json','w').write(json.dumps({'ok': True}))"]` with `cwd` = a tmp git-free dir; note the worker writes `result.json` relative to its own cwd, so tests spawn a command whose cwd is the job dir — see Step 3 note).

```python
# tests/test_jobs.py
import json, time
from pathlib import Path
from codex_in_claude._core.jobs import JobStore

def _store(tmp_path) -> JobStore:
    return JobStore(root=tmp_path / "jobs", ttl_seconds=3600, max_seconds=60, max_count=50)

def test_start_status_result_done(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    # a command that writes the final envelope to result.json in ITS cwd (the job dir)
    code = "import json; open('result.json','w').write(json.dumps({'ok': True, 'tool': 't'}))"
    job_id, started = store.start(["python", "-c", code], cwd, kind="codex_delegate")
    assert job_id and started
    # poll until terminal
    for _ in range(200):
        st = store.status(cwd, job_id)
        if st["status"] in {"done", "failed"}:
            break
        time.sleep(0.02)
    assert st["status"] == "done"
    rec, payload = store.result_payload(cwd, job_id, consume=False)
    assert payload == {"ok": True, "tool": "t"}
    assert rec["status"] == "done"

def test_failed_when_no_result(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(["python", "-c", "raise SystemExit(1)"], cwd, kind="k")
    for _ in range(200):
        st = store.status(cwd, job_id)
        if st["status"] in {"done", "failed"}:
            break
        time.sleep(0.02)
    assert st["status"] == "failed"

def test_cancel_running(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(["python", "-c", "import time; time.sleep(30)"], cwd, kind="k")
    st = store.cancel(cwd, job_id)
    assert st["status"] == "cancelled"

def test_consume_deletes(tmp_path):
    store = _store(tmp_path)
    cwd = str(tmp_path)
    code = "import json; open('result.json','w').write(json.dumps({'ok': True}))"
    job_id, _ = store.start(["python", "-c", code], cwd, kind="k")
    for _ in range(200):
        if store.status(cwd, job_id)["status"] == "done":
            break
        time.sleep(0.02)
    _, payload = store.result_payload(cwd, job_id, consume=True)
    assert payload == {"ok": True}
    assert store.status(cwd, job_id) is None

def test_missing_job(tmp_path):
    store = _store(tmp_path)
    assert store.status(str(tmp_path), "deadbeef") is None
    assert store.cancel(str(tmp_path), "deadbeef") is None

def test_deadline_timeout(tmp_path):
    store = JobStore(root=tmp_path / "jobs", ttl_seconds=3600, max_seconds=1, max_count=50)
    cwd = str(tmp_path)
    job_id, _ = store.start(["python", "-c", "import time; time.sleep(30)"], cwd, kind="k")
    time.sleep(1.2)
    assert store.status(cwd, job_id)["status"] == "timeout"

def test_list_newest_first_and_count_cap(tmp_path):
    store = JobStore(root=tmp_path / "jobs", ttl_seconds=3600, max_seconds=60, max_count=2)
    cwd = str(tmp_path)
    code = "import json; open('result.json','w').write(json.dumps({'ok': True}))"
    ids = []
    for _ in range(3):
        jid, _ = store.start(["python", "-c", code], cwd, kind="k")
        for _ in range(200):
            if store.status(cwd, jid)["status"] == "done":
                break
            time.sleep(0.02)
        ids.append(jid)
        time.sleep(0.01)
    listed = store.list(cwd)
    assert len(listed) <= 2  # oldest terminal evicted
```

NOTE on cwd: `start()` runs the command with `cwd=<the job dir>` so a relative `result.json` lands in the record dir. (The real worker is given its job dir as argv and writes `result.json` there; running with cwd=job_dir is the simplest contract. Document this in the docstring.) Adjust the reference's `Popen(cwd=cwd, stdout=result_path)` accordingly: keep stdout redirection to `result.json` for the generic case AND pass `cwd=job_dir`. The worker ignores stdout and writes `result.json` itself — so for the worker, redirect stdout to `stderr.log` too OR let the worker write the file. Decision: **the spawned command writes `result.json` itself; `start()` redirects child stdout+stderr to `stderr.log`.** Update the tests above to have the command write `result.json` (they already do).

- [ ] **Step 2: Run — expect FAIL** `uv run pytest tests/test_jobs.py -q`

- [ ] **Step 3: Implement `_core/jobs.py`** — port from the reference, restructured as a `JobStore` dataclass with the methods above. Key differences from the reference:
  - No `from cc_plugin_codex...` imports. No `schemas`/`normalize`. Pure stdlib + a module-level `threading.RLock`.
  - `start()` signature `(cmd, cwd, *, kind, extra=None)`; spawn with `cwd=<job_dir>`, `stdout`/`stderr` → `stderr.log`, `start_new_session=True`. No stdin needed (the worker reads its job dir from argv). Write `meta.json` with `extra` nested under `"extra"`.
  - `_read_envelope(jd)` parses `result.json` → `dict | None`.
  - status/result/cancel/list return plain dicts (primitive fields only) — the server maps them to pydantic models.
  - `deadline_seconds` computed from stored `deadline_epoch - started_epoch`.
  - `expires_at` = `completed_epoch + ttl_seconds` ISO, else None.

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit** `git commit -am "feat: add generic disk-backed JobStore in _core (M4)"`

---

## Task 3: Shared delegate orchestration + background worker

**Files:**
- Modify: `src/codex_in_claude/server.py` (extract `_run_delegate`)
- Create: `src/codex_in_claude/_worker.py`
- Modify: `src/codex_in_claude/config.py` (add `job_store()` factory)
- Test: `tests/test_worker.py`, `tests/test_server.py`

**Interfaces:**
- Produces in `server.py`: `_run_delegate(task: str, cwd: str, meta: Meta, *, sandbox: str, isolation: str, timeout_seconds: int, model: str | None, git_timeout: int) -> dict` — the existing `codex_delegate` body from worktree-create through envelope-build, returning the envelope dict. `codex_delegate` calls it; the worker calls it. Must be `async` (it awaits `codex.run_codex_exec`).
- Produces in `config.py`: `job_store() -> JobStore` = `JobStore(state_dir(), job_ttl_seconds(), job_max_seconds(), job_max_count())`.
- Produces `_worker.py`: `main(argv: list[str] | None = None) -> int`. Reads `<job_dir>` from argv, loads `spec.json` (keys: `task, cwd, workspace_source, sandbox, isolation, timeout_seconds, model, git_timeout`), rebuilds `Meta` via `server._base_meta`, runs `asyncio.run(server._run_delegate(...))`, writes the returned dict to `result.json` in `<job_dir>`, returns 0. On any unexpected exception, write an `internal_error` `ErrorResult` dict to `result.json` and return 0 (so a crash still yields a readable envelope; a true crash before writing → `_core` reports `failed`).

- [ ] **Step 1: Refactor `codex_delegate` to call `_run_delegate`** (pure refactor — existing server tests must stay green). Run `uv run pytest tests/test_server.py -k delegate -q` before and after; expect PASS both times.

- [ ] **Step 2: Write failing worker test** (stub `codex.run_codex_exec` and `worktree` via monkeypatch so no real codex/git is needed; or use a real tmp git repo + a fake codex that edits a file). Prefer stubbing `server._run_delegate` to assert the worker wires spec→envelope→result.json:

```python
# tests/test_worker.py
import json
from pathlib import Path
from codex_in_claude import _worker, server

def test_worker_writes_result(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    jd.mkdir()
    spec = {"task": "do x", "cwd": str(tmp_path), "workspace_source": "param",
            "sandbox": "workspace-write", "isolation": "inherit",
            "timeout_seconds": 60, "model": None, "git_timeout": 60}
    (jd / "spec.json").write_text(json.dumps(spec))

    async def fake_run_delegate(task, cwd, meta, **kw):
        return {"ok": True, "tool": "codex_delegate", "summary": task}
    monkeypatch.setattr(server, "_run_delegate", fake_run_delegate)

    rc = _worker.main([str(jd)])
    assert rc == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["summary"] == "do x"

def test_worker_crash_writes_error(tmp_path, monkeypatch):
    jd = tmp_path / "job"; jd.mkdir()
    (jd / "spec.json").write_text(json.dumps({"task": "x", "cwd": str(tmp_path),
        "workspace_source": "param", "sandbox": "workspace-write", "isolation": "inherit",
        "timeout_seconds": 60, "model": None, "git_timeout": 60}))
    async def boom(*a, **k): raise RuntimeError("kaboom")
    monkeypatch.setattr(server, "_run_delegate", boom)
    rc = _worker.main([str(jd)])
    assert rc == 0
    out = json.loads((jd / "result.json").read_text())
    assert out["ok"] is False and out["error"]["code"] == "internal_error"
```

- [ ] **Step 3: Run — expect FAIL** `uv run pytest tests/test_worker.py -q`

- [ ] **Step 4: Implement `_worker.py`** per the interface above (use `asyncio.run`, `argparse`/`sys.argv`, write `result.json` atomically: write to `result.json.tmp` then `os.replace`).

- [ ] **Step 5: Run — expect PASS** (worker tests + `tests/test_server.py -k delegate`)

- [ ] **Step 6: Commit** `git commit -am "feat: share delegate orchestration and add background worker (M4)"`

---

## Task 4: `codex_delegate_async` tool

**Files:**
- Modify: `src/codex_in_claude/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `config.job_store()`, `JobStore.start`, `_base_meta`, `_resolve_isolation`, `workspace.resolve_workspace`, `_placeholder_error`, `JobStarted` schema, `JOB_STARTED_SCHEMA`.
- Produces: `async def codex_delegate_async(task, ctx=None, workspace_root=None, model=None, isolation=None) -> dict`. Same validation as `codex_delegate` (isolation, workspace, placeholder, input size, **not-a-git-repo precheck** via `worktree.create`/`remove` is too costly — instead reuse a cheap `gitdiff`/`worktree` repo check, or let the worker surface `not_a_git_repo` on first poll). Decision: do a cheap synchronous repo precheck with `worktree._ensure_repo_with_head`-equivalent is private; instead call `gitdiff` is review-only. Simplest: skip precheck, document that repo errors surface in the job result. **But** prefer failing fast: add a public `worktree.ensure_repo_with_head(repo, *, timeout)` wrapper (thin, raises the same errors) and call it; map to `not_a_git_repo`/`worktree_error` synchronously. On success, write `spec.json` into the job dir BEFORE `start()` — so the store's `start()` must expose the job dir, or accept a `prepare` callback. Decision: add `JobStore.start(..., write_spec: dict | None = None)` that writes `spec.json` into the job dir before spawning. Then build the command `[sys.executable, "-m", "codex_in_claude._worker", "<job_dir>"]` — but the job dir is only known inside `start()`. Resolve by having `start()` accept a `cmd_factory: Callable[[Path], list[str]]` instead of a fixed `cmd`, so the command can reference its own job dir.

  **Final `JobStore.start` signature:** `start(self, cmd_factory: Callable[[Path], list[str]], cwd: str, *, kind: str, extra: dict | None = None, write_spec: dict | None = None) -> tuple[str, str]`. Update Task 2 tests to pass `lambda jd: ["python", "-c", code]` and (where they wrote result.json relative) keep cwd=job_dir. (Revise Task 2 accordingly — the factory receives the job dir `Path`.)

- [ ] **Step 1: Add `worktree.ensure_repo_with_head`** public wrapper + test in `tests/test_worktree.py`:

```python
def test_ensure_repo_with_head_raises_outside_repo(tmp_path):
    import pytest
    from codex_in_claude._core import worktree
    with pytest.raises(worktree.NotAGitRepoError):
        worktree.ensure_repo_with_head(str(tmp_path), timeout=10)
```

```python
# worktree.py
def ensure_repo_with_head(repo: str, *, timeout: int) -> None:
    """Public guard: raise NotAGitRepoError / NoCommitsError / WorktreeError."""
    _ensure_repo_with_head(repo, timeout)
```

- [ ] **Step 2: Write failing server test** (stub the store so no real worker spawns):

```python
# tests/test_server.py (add)
async def test_delegate_async_returns_job_id(tmp_git_repo, monkeypatch):
    # tmp_git_repo: a fixture with an initialized repo + one commit (add if absent)
    from codex_in_claude import server, config
    monkeypatch.setattr(server.config, "job_store", lambda: _FakeStore())
    res = await server.codex_delegate_async("do x", workspace_root=tmp_git_repo)
    assert res["ok"] is True and res["job_id"] and res["status"] == "running"
    assert res["kind"] == "codex_delegate"
```

(`_FakeStore.start` returns `("abc123", "2026-01-01T00:00:00+00:00")`.) Also test the not-a-git-repo path returns `ok:false, error.code == "not_a_git_repo"`.

- [ ] **Step 3: Run — expect FAIL**

- [ ] **Step 4: Implement `codex_delegate_async`** — validate (isolation, workspace, placeholder, input size, `worktree.ensure_repo_with_head`), build `extra`/`write_spec` dicts, call `store.start(cmd_factory, cwd, kind="codex_delegate", extra=..., write_spec=spec)` where `cmd_factory = lambda jd: [sys.executable, "-m", "codex_in_claude._worker", str(jd)]`, then return `JobStarted(...)` with deadline=`config.job_max_seconds()`, ttl=`config.job_ttl_seconds()`, meta. Annotate with a new `_ACTIVE_ASYNC` preset (`readOnlyHint=False, openWorldHint=True, destructiveHint=False, idempotentHint=False`).

- [ ] **Step 5: Run — expect PASS**

- [ ] **Step 6: Commit** `git commit -am "feat: add codex_delegate_async (M4)"`

---

## Task 5: Job lifecycle tools

**Files:**
- Modify: `src/codex_in_claude/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `codex_job_status`, `codex_job_result`, `codex_job_consume_result`, `codex_job_cancel`, `codex_job_list`. All accept `(job_id, ctx=None, workspace_root=None)` (list omits `job_id`). Each resolves the workspace like the async tool, calls the store, and maps the generic dict → pydantic model:
  - `codex_job_status` → `JobStatus` (or `job_not_found` `ErrorResult`). `result_available = status == "done"`. `deadline_seconds`/`ttl_seconds`/`poll_after_ms` from config/record.
  - `codex_job_result` / `codex_job_consume_result` → the stored `result.json` dict with `meta.job_id` patched in; for non-done states synthesize an `ErrorResult` from a `_STATE_TO_ERROR` map (`job_running`/`job_cancelled`/`job_timeout`/`job_failed`); `job_not_found` when absent. `consume=True` for the consume variant (only deletes done records).
  - `codex_job_cancel` → `JobStatus` after cancel (or `job_not_found`).
  - `codex_job_list` → `JobListResult` (newest first).
- Helper: `_job_not_found(job_id, meta) -> dict`, `_STATE_TO_ERROR: dict[str, tuple[code, message, repair]]` mirroring the reference but with `codex_*` tool names and `CODEX_IN_CLAUDE_JOB_MAX_SECONDS` in the timeout repair.
- For result-dict `meta.job_id` patch: the stored envelope already has a `meta` object; set `payload["meta"]["job_id"] = job_id` before returning.

- [ ] **Step 1: Write failing tests** — seed a fake store returning a done envelope; assert each tool's mapping, plus `job_not_found` for an unknown id, and the non-done → `job_running` error mapping.

```python
async def test_job_status_not_found(monkeypatch, tmp_git_repo):
    from codex_in_claude import server
    monkeypatch.setattr(server.config, "job_store", lambda: _FakeStore(status_dict=None))
    res = await server.codex_job_status("nope", workspace_root=tmp_git_repo)
    assert res["ok"] is False and res["error"]["code"] == "job_not_found"

async def test_job_result_done_patches_job_id(monkeypatch, tmp_git_repo):
    env = {"ok": True, "tool": "codex_delegate", "summary": "s",
           "meta": _MINIMAL_META}   # build a valid Meta dump
    store = _FakeStore(record={"status": "done"}, result_json=env)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("abc", workspace_root=tmp_git_repo)
    assert res["meta"]["job_id"] == "abc"

async def test_job_result_running_maps_error(monkeypatch, tmp_git_repo):
    store = _FakeStore(record={"status": "running"}, result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("abc", workspace_root=tmp_git_repo)
    assert res["ok"] is False and res["error"]["code"] == "job_running"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement the five tools + helpers.** Wrap store calls so blocking disk IO doesn't stall the event loop — use `anyio.to_thread.run_sync` (or `await asyncio.to_thread(...)`).

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit** `git commit -am "feat: add job lifecycle tools (M4)"`

---

## Task 6: Capabilities, fingerprint, docs, slash commands

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (FINGERPRINT → `schema-4`)
- Modify: `src/codex_in_claude/server.py` (`codex_capabilities`: add async + job tools to `active_tools`/`free_tools`/`tool_details`, update `negative_scope`)
- Modify: `CHANGELOG.md`, `README.md`, `skills/collaborating-with-codex/SKILL.md`
- Create: `commands/codex/delegate-async.md` (+ job commands if the existing command set warrants — match existing `commands/codex/` style)
- Test: `tests/test_server.py` (capabilities lists new tools), `tests/test_packaging.py` (fingerprint/commands if asserted)

- [ ] **Step 1: Update tests** asserting `codex_capabilities()["active_tools"]` includes `codex_delegate_async` and `free_tools` includes the five job tools; assert `FINGERPRINT.endswith("schema-4")`.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement** the capability additions, bump FINGERPRINT, write a `CHANGELOG.md` entry under an Unreleased/`0.1` section noting the M4 surface + fingerprint bump, document the job tools + new env knobs in `README.md` and the skill (guardrails: don't poll in a tight loop — honor `poll_after_ms`; `consume` to free state; jobs are bounded by `JOB_MAX_SECONDS`).

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit** `git commit -am "feat: surface M4 tools in capabilities + docs; bump fingerprint (M4)"`

---

## Task 7: Full gate + coverage + integration smoke

- [ ] **Step 1:** `uv run ruff check . && uv run ruff format --check . && uv run ty check` — all clean.
- [ ] **Step 2:** `uv run pytest` — green, coverage ≥ 95%. Add targeted tests to `tests/test_jobs.py`/`test_server.py` for any uncovered branch (PID-not-our-child fallback, count-cap eviction never killing a running job, `_rmtree` on a missing dir, consume on a non-done job not deleting).
- [ ] **Step 3 (optional, opt-in):** add an `integration`-marked test that runs a real `codex_delegate_async` against a tmp git repo, polls `codex_job_status` to `done`, fetches `codex_job_result`, asserts a diff is present and the live tree is untouched, and the worktree dir is gone. Run with `uv run pytest -m integration --no-cov`.
- [ ] **Step 4: Commit** `git commit -am "test: M4 coverage + integration smoke (M4)"`

---

## Self-review notes

- **Spec coverage:** `codex_delegate_async` + 5 job tools (Tasks 4–5); disk-backed/PID-reconciled/TTL/count-cap (Task 2); `config.state_dir()` reuse + JOB_* knobs (Task 1); fingerprint + docs + skill + commands (Task 6); 95% floor + integration (Task 7). ✔
- **One-way import rule:** `_core/jobs.py` takes config as params and imports no parent modules. ✔
- **Type consistency:** `JobStore.start` takes a `cmd_factory: Callable[[Path], list[str]]` (settled in Task 4; Task 2 tests pass `lambda jd: [...]`). `_run_delegate` is `async` and shared by `codex_delegate` and `_worker`. Status dicts use the same keys across `status`/`list`/`cancel`.
- **Risk:** `_worker` imports `server`, which constructs the FastMCP app at import time — acceptable (no network), but keep `_run_delegate` a module-level function so the worker can import it without invoking tool decorators' runtime. If import cost is a concern, move `_run_delegate` into a small `delegate.py` both import; not required for correctness.
