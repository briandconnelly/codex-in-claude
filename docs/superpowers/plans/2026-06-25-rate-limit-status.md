# Rate-limit quota reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report how much of the account's Codex rate-limit quota remains, surfaced by the free `codex_status` tool and on every active call's `Meta`, captured opportunistically from paid calls at zero extra spend.

**Architecture:** Every paid `codex exec` turn streams a `token_count` JSONL event whose `payload.rate_limits` block carries account-wide quota usage for a 5-hour (`primary`) and weekly (`secondary`) window. We parse that block, persist the latest snapshot to a plugin-owned JSON file, and interpret it against each window's own `resets_at` with an asymmetric rule: an unobserved window (reset-passed or missing) never yields a positive `available` signal (it degrades to `unknown`), while conservative `limited`/`exhausted` verdicts from still-open windows survive staleness — so a stale file can't mislead an agent into spending on a throttled account.

**Tech Stack:** Python 3.11+, Pydantic v2, FastMCP, `uv`, `pytest`, `ruff`, `ty`.

## Global Constraints

- `requires-python>=3.11`. Follow existing patterns in each file.
- `_core/` must never import from its parent package (`codex_in_claude`). The new code lives in the parent package, not `_core`, so it may import freely from the package.
- Tolerant parsing: external-CLI-derived data degrades to `None`, never raises.
- All assumptions about the `codex` CLI live in `cli_contract.py`.
- A change to the agent-visible surface bumps `FINGERPRINT` and is a breaking change (minor pre-1.0): commit `!`/`BREAKING CHANGE:`, `breaking-change` PR label.
- Release-coordination version set must move together: `pyproject.toml`, `.claude-plugin/plugin.json`, the `.mcp.json` PyPI pin, `CHANGELOG.md`, `FINGERPRINT`. Target version: `0.4.1` → `0.5.0`; `FINGERPRINT` `codex-in-claude/0.1/schema-11` → `codex-in-claude/0.1/schema-12`.
- TDD: failing test first, then minimal code. Tests mirror the module (`tests/test_<module>.py`). 95% coverage floor: `uv run pytest`.
- Gate before any task is done: `uv run ruff check . && uv run ruff format --check . && uv run ty check`.
- Conventional Commits; squash-merge; branch `feat/rate-limit-status` (already created).

---

### Task 1: Parse the `rate_limits` block from the event stream

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (add raw snapshot models, near the `Usage` model ~line 120)
- Modify: `src/codex_in_claude/cli_contract.py` (add field-name constants near `USAGE_EVENT_MARKERS` ~line 124)
- Modify: `src/codex_in_claude/normalize.py` (add `parse_rate_limit` + helpers)
- Test: `tests/test_normalize.py`

**Interfaces:**
- Produces: `schemas.RateLimitWindowSnapshot(used_percent: float|None, window_minutes: int|None, resets_at: int|None)`; `schemas.RateLimitSnapshot(plan_type: str|None, rate_limit_reached_type: str|None, primary: RateLimitWindowSnapshot|None, secondary: RateLimitWindowSnapshot|None)`; `normalize.parse_rate_limit(events: str) -> RateLimitSnapshot | None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_normalize.py`:

```python
from codex_in_claude import normalize

_TOKEN_COUNT_LINE = (
    '{"type":"event_msg","payload":{"type":"token_count",'
    '"info":{"total_token_usage":{"input_tokens":17866,"output_tokens":308,"total_tokens":18174}},'
    '"rate_limits":{"limit_id":"codex","limit_name":null,'
    '"primary":{"used_percent":12.0,"window_minutes":300,"resets_at":1780534461},'
    '"secondary":{"used_percent":8.0,"window_minutes":10080,"resets_at":1780864628},'
    '"credits":null,"plan_type":"plus","rate_limit_reached_type":null}}}'
)


def test_parse_rate_limit_extracts_both_windows():
    snap = normalize.parse_rate_limit(_TOKEN_COUNT_LINE)
    assert snap is not None
    assert snap.plan_type == "plus"
    assert snap.rate_limit_reached_type is None
    assert snap.primary.used_percent == 12.0
    assert snap.primary.window_minutes == 300
    assert snap.primary.resets_at == 1780534461
    assert snap.secondary.used_percent == 8.0
    assert snap.secondary.window_minutes == 10080


def test_parse_rate_limit_absent_returns_none():
    assert normalize.parse_rate_limit('{"type":"event_msg","payload":{"type":"agent_message"}}') is None


def test_parse_rate_limit_last_event_wins():
    second = _TOKEN_COUNT_LINE.replace('"used_percent":12.0', '"used_percent":40.0')
    snap = normalize.parse_rate_limit(_TOKEN_COUNT_LINE + "\n" + second)
    assert snap.primary.used_percent == 40.0


def test_parse_rate_limit_tolerates_malformed_lines():
    assert normalize.parse_rate_limit("not json\n{bad\n" + _TOKEN_COUNT_LINE) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_normalize.py -k rate_limit -v`
Expected: FAIL with `AttributeError: module 'codex_in_claude.normalize' has no attribute 'parse_rate_limit'`

- [ ] **Step 3: Add the raw models to `schemas.py`**

Insert after the `Usage` model (after its closing line ~127). Note `extra="ignore"` (not `forbid`): this parses external CLI output, so unknown fields like `credits` must be tolerated for forward-compat.

```python
class RateLimitWindowSnapshot(BaseModel):
    """Raw per-window quota as emitted by codex's token_count event (one of the
    primary/secondary windows). Parsed tolerantly; unknown fields ignored."""

    model_config = ConfigDict(extra="ignore")
    used_percent: float | None = None
    window_minutes: int | None = None
    resets_at: int | None = None  # epoch seconds


class RateLimitSnapshot(BaseModel):
    """Raw rate_limits block from a token_count event; what we persist/replay."""

    model_config = ConfigDict(extra="ignore")
    plan_type: str | None = None
    rate_limit_reached_type: str | None = None
    primary: RateLimitWindowSnapshot | None = None  # 5-hour window
    secondary: RateLimitWindowSnapshot | None = None  # weekly window
```

- [ ] **Step 4: Add field-name constants to `cli_contract.py`**

Insert after `USAGE_EVENT_MARKERS` (~line 124):

```python
# The rate-limit quota block rides inside the same token_count event as token usage,
# at payload.rate_limits, with `primary` (5h) / `secondary` (weekly) sub-objects each
# carrying used_percent / window_minutes / resets_at. Parsed tolerantly in normalize.
RATE_LIMIT_EVENT_KEY = "rate_limits"
```

- [ ] **Step 5: Implement `parse_rate_limit` in `normalize.py`**

Add the import to the existing schemas import line:

```python
from codex_in_claude.schemas import (
    Finding,
    RateLimitSnapshot,
    RateLimitWindowSnapshot,
    Usage,
)
```

Add these functions (after `parse_event_metadata`):

```python
def parse_rate_limit(events: str) -> RateLimitSnapshot | None:
    """Tolerantly scan JSONL events for the latest rate_limits block. Never raises;
    malformed lines are skipped. Last event carrying the block wins."""
    snapshot: RateLimitSnapshot | None = None
    for raw_line in events.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        found = _find_rate_limit(event)
        if found is not None:
            snapshot = found
    return snapshot


def _find_rate_limit(event: dict) -> RateLimitSnapshot | None:
    blob = event.get(cli_contract.RATE_LIMIT_EVENT_KEY)
    if isinstance(blob, dict):
        snap = _snapshot_from(blob)
        if snap is not None:
            return snap
    for nest in ("msg", "payload", "data"):
        inner = event.get(nest)
        if isinstance(inner, dict):
            found = _find_rate_limit(inner)
            if found is not None:
                return found
    return None


def _snapshot_from(blob: dict) -> RateLimitSnapshot | None:
    primary = _window_from(blob.get("primary"))
    secondary = _window_from(blob.get("secondary"))
    if primary is None and secondary is None:
        return None
    plan = blob.get("plan_type")
    reached = blob.get("rate_limit_reached_type")
    return RateLimitSnapshot(
        plan_type=plan if isinstance(plan, str) else None,
        rate_limit_reached_type=reached if isinstance(reached, str) else None,
        primary=primary,
        secondary=secondary,
    )


def _window_from(blob: object) -> RateLimitWindowSnapshot | None:
    if not isinstance(blob, dict):
        return None
    used = blob.get("used_percent")
    window = blob.get("window_minutes")
    resets = blob.get("resets_at")
    used_f = float(used) if isinstance(used, (int, float)) and not isinstance(used, bool) else None
    window_i = window if isinstance(window, int) and not isinstance(window, bool) else None
    resets_i = int(resets) if isinstance(resets, (int, float)) and not isinstance(resets, bool) else None
    if used_f is None and resets_i is None:
        return None
    return RateLimitWindowSnapshot(used_percent=used_f, window_minutes=window_i, resets_at=resets_i)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_normalize.py -k rate_limit -v`
Expected: PASS (4 passed)

- [ ] **Step 7: Run the gate**

Run: `uv run ruff check . && uv run ruff format . && uv run ty check`
Expected: all pass (ruff format may reformat — re-run `ruff format --check .` to confirm clean)

- [ ] **Step 8: Commit**

```bash
git add src/codex_in_claude/schemas.py src/codex_in_claude/cli_contract.py src/codex_in_claude/normalize.py tests/test_normalize.py
git commit -m "feat(schemas): parse rate_limits block from codex event stream"
```

---

### Task 2: Persist and load the snapshot cache

**Files:**
- Modify: `src/codex_in_claude/config.py` (path + threshold + codex_home helpers, near `state_dir` ~line 232)
- Create: `src/codex_in_claude/rate_limit.py`
- Test: `tests/test_rate_limit.py`

**Interfaces:**
- Consumes: `schemas.RateLimitSnapshot` (Task 1).
- Produces: `config.rate_limit_snapshot_file() -> Path`; `config.rate_limit_stale_seconds() -> int`; `config.codex_home() -> Path`; `rate_limit.save(snapshot, *, now_epoch, path=None, home=None) -> None`; `rate_limit._load_raw(path=None) -> dict | None`; module constant `rate_limit.CACHE_VERSION = 1`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rate_limit.py`:

```python
from pathlib import Path

from codex_in_claude import rate_limit
from codex_in_claude.schemas import RateLimitSnapshot, RateLimitWindowSnapshot


def _snap() -> RateLimitSnapshot:
    return RateLimitSnapshot(
        plan_type="plus",
        primary=RateLimitWindowSnapshot(used_percent=12.0, window_minutes=300, resets_at=1780534461),
        secondary=RateLimitWindowSnapshot(used_percent=8.0, window_minutes=10080, resets_at=1780864628),
    )


def test_save_then_load_roundtrips(tmp_path: Path):
    target = tmp_path / "snap.json"
    rate_limit.save(_snap(), now_epoch=1780530000, path=target, home="/home/.codex")
    raw = rate_limit._load_raw(target)
    assert raw["version"] == rate_limit.CACHE_VERSION
    assert raw["captured_at"] == 1780530000
    assert raw["codex_home"] == "/home/.codex"
    assert raw["snapshot"]["primary"]["used_percent"] == 12.0


def test_load_missing_file_returns_none(tmp_path: Path):
    assert rate_limit._load_raw(tmp_path / "absent.json") is None


def test_load_corrupt_file_returns_none(tmp_path: Path):
    target = tmp_path / "snap.json"
    target.write_text("{not json", encoding="utf-8")
    assert rate_limit._load_raw(target) is None


def test_load_wrong_version_returns_none(tmp_path: Path):
    target = tmp_path / "snap.json"
    target.write_text('{"version": 999, "snapshot": {}}', encoding="utf-8")
    assert rate_limit._load_raw(target) is None


def test_save_is_best_effort_on_unwritable_path(tmp_path: Path):
    # A path whose parent is a file, not a dir, cannot be created — save must not raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    rate_limit.save(_snap(), now_epoch=1, path=blocker / "nested" / "snap.json", home="/h")


def test_save_leaves_no_temp_files(tmp_path: Path):
    target = tmp_path / "snap.json"
    rate_limit.save(_snap(), now_epoch=1, path=target, home="/h")
    assert target.exists()
    assert list(tmp_path.glob("*.tmp")) == []  # atomic write cleaned up its temp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rate_limit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codex_in_claude.rate_limit'`

- [ ] **Step 3: Add config helpers**

In `src/codex_in_claude/config.py`, after `state_dir` (~line 239):

```python
def rate_limit_snapshot_file() -> Path:
    """Plugin-owned cache file for the latest Codex rate-limit snapshot (sibling of
    the jobs/ store; honors CODEX_IN_CLAUDE_RATE_LIMIT_FILE / STATE_DIR / XDG_CACHE_HOME)."""
    override = os.environ.get(f"{ENV_PREFIX}RATE_LIMIT_FILE")
    if override:
        return Path(override).expanduser()
    return state_dir().parent / "rate_limit_snapshot.json"


def rate_limit_stale_seconds() -> int:
    """Age (seconds) past which a cached snapshot is flagged is_stale. Advisory only —
    the reset-aware interpretation, not this threshold, is the real staleness guard."""
    raw = os.environ.get(f"{ENV_PREFIX}RATE_LIMIT_STALE_SECONDS")
    if raw and raw.isdigit():
        return int(raw)
    return 1800  # 30 minutes


def codex_home() -> Path:
    """Resolved CODEX_HOME (defaults to ~/.codex), used for snapshot provenance."""
    override = os.environ.get("CODEX_HOME")
    return Path(override).expanduser() if override else Path.home() / ".codex"
```

- [ ] **Step 4: Create `rate_limit.py` with persistence only**

```python
"""Persist, load, and interpret the latest Codex rate-limit snapshot.

Capture is opportunistic: paid calls already parse the token_count event for token
usage, so we lift the sibling rate_limits block at no extra spend, persist the latest,
and interpret it against each window's own resets_at when read — so a stale cache
can't mislead: an unobserved (reset-passed or missing) window never reports as
available, while conservative limited/exhausted verdicts from open windows survive."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from codex_in_claude import config, normalize
from codex_in_claude.schemas import (
    RateLimit,
    RateLimitSnapshot,
    RateLimitWindow,
    RateLimitWindowSnapshot,
)

CACHE_VERSION = 1


def save(
    snapshot: RateLimitSnapshot,
    *,
    now_epoch: int,
    path: Path | None = None,
    home: str | None = None,
) -> None:
    """Persist the latest snapshot, best-effort and atomically. Never raises: a write
    failure must never fail the underlying paid call. Uses a unique temp file +
    os.replace (mirroring _worker._atomic_write) so a concurrent paid call or a
    codex_status read never observes a truncated file. Last writer wins."""
    target = path or config.rate_limit_snapshot_file()
    home_str = home if home is not None else str(config.codex_home())
    payload = {
        "version": CACHE_VERSION,
        "captured_at": now_epoch,
        "codex_home": home_str,
        "snapshot": snapshot.model_dump(mode="json"),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    except OSError:
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload))
        os.replace(tmp, target)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _load_raw(path: Path | None = None) -> dict | None:
    target = path or config.rate_limit_snapshot_file()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("version") == CACHE_VERSION:
        return data
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_rate_limit.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Run the gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/codex_in_claude/config.py src/codex_in_claude/rate_limit.py tests/test_rate_limit.py
git commit -m "feat(config): persist latest codex rate-limit snapshot to cache file"
```

---

### Task 3: Interpret a snapshot into the agent-facing `RateLimit`

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (agent-facing models, after `RateLimitSnapshot`)
- Modify: `src/codex_in_claude/rate_limit.py` (`interpret`, `live`, `current`, window/status helpers)
- Test: `tests/test_rate_limit.py`

**Interfaces:**
- Produces: `schemas.RateLimitWindow`, `schemas.RateLimit` (status `Literal["available","limited","exhausted","unknown"]`, source `Literal["current_run","plugin_cache"]`, field `home_unverified`); `rate_limit.interpret(snapshot, *, now_epoch, captured_at=None, cache_home=None, current_home=None, stale_seconds=None, source="plugin_cache") -> RateLimit`; `rate_limit.live(snapshot, *, now_epoch) -> RateLimit | None` (source `current_run`); `rate_limit.current() -> RateLimit`; `rate_limit.capture(events: str, *, now_epoch=None) -> RateLimit | None`. Internal `_status(...) -> tuple[status, limiting_window|None, note|None]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_rate_limit.py`:

```python
from codex_in_claude import rate_limit
from codex_in_claude.schemas import RateLimit, RateLimitSnapshot, RateLimitWindowSnapshot


def _win(used, resets):
    return RateLimitWindowSnapshot(used_percent=used, window_minutes=300, resets_at=resets)


def _both(p_used, p_reset, s_used, s_reset):
    return RateLimitSnapshot(plan_type="plus", primary=_win(p_used, p_reset), secondary=_win(s_used, s_reset))


def test_interpret_no_snapshot_is_unknown():
    rl = rate_limit.interpret(None, now_epoch=1000)
    assert rl.status == "unknown"
    assert rl.note  # carries a refresh hint
    assert rl.as_of is None


def test_interpret_available_requires_both_windows_open_and_healthy():
    snap = _both(10.0, 9000, 40.0, 9000)  # both open (now < resets), both > 25% remaining
    rl = rate_limit.interpret(snap, now_epoch=1000, captured_at=900)
    assert rl.status == "available"
    assert rl.limiting_window == "secondary"  # lower remaining (60 vs 90)
    assert rl.secondary.remaining_percent == 60.0
    assert rl.primary.seconds_until_reset == 8000
    assert rl.age_seconds == 100
    assert rl.as_of.startswith("20")  # ISO-8601


def test_interpret_limited_when_open_window_below_25():
    snap = _both(80.0, 9000, 5.0, 9000)  # primary 20% remaining -> limited
    rl = rate_limit.interpret(snap, now_epoch=1000)
    assert rl.status == "limited"
    assert rl.limiting_window == "primary"


def test_interpret_exhausted_on_reached_type_names_open_window():
    snap = _both(100.0, 9000, 5.0, 9000)
    snap.rate_limit_reached_type = "primary"
    rl = rate_limit.interpret(snap, now_epoch=1000)
    assert rl.status == "exhausted"
    assert rl.limiting_window == "primary"


def test_interpret_reached_type_on_reset_window_degrades_to_unknown():
    # Codex said primary hit its limit, but primary has since reset -> not actionable.
    snap = _both(100.0, 500, 5.0, 9000)  # now=1000 > primary reset 500
    snap.rate_limit_reached_type = "primary"
    rl = rate_limit.interpret(snap, now_epoch=1000)
    assert rl.status == "unknown"


def test_interpret_all_windows_reset_passed_is_unknown_not_healthy():
    snap = _both(10.0, 500, 10.0, 600)  # now=1000 past both resets
    rl = rate_limit.interpret(snap, now_epoch=1000)
    assert rl.status == "unknown"  # post-reset usage unobserved; never 'available'
    assert rl.primary.reset_passed is True
    assert rl.primary.remaining_percent is None  # nulled on reset
    assert rl.primary.used_percent is None
    assert rl.limiting_window is None


def test_interpret_one_window_reset_blocks_available():
    # primary reset (unobserved), secondary open and healthy -> still unknown,
    # because the unobserved 5h window could already be re-exhausted.
    snap = _both(10.0, 500, 10.0, 9000)
    rl = rate_limit.interpret(snap, now_epoch=1000)
    assert rl.status == "unknown"


def test_interpret_open_risk_wins_even_with_other_window_reset():
    # secondary open and exhausted -> conservative 'exhausted' despite primary reset.
    snap = _both(10.0, 500, 100.0, 9000)
    rl = rate_limit.interpret(snap, now_epoch=1000)
    assert rl.status == "exhausted"
    assert rl.limiting_window == "secondary"


def test_interpret_missing_resets_at_cannot_be_available():
    snap = RateLimitSnapshot(
        primary=RateLimitWindowSnapshot(used_percent=10.0, window_minutes=300, resets_at=None),
        secondary=_win(10.0, 9000),
    )
    rl = rate_limit.interpret(snap, now_epoch=1000)
    assert rl.status == "unknown"  # primary freshness unverifiable
    assert rl.primary.reset_passed is False
    assert rl.primary.seconds_until_reset is None


def test_interpret_clamps_negative_seconds_until_reset_on_open_window():
    # resets_at exactly now -> reset_passed true, seconds 0
    snap = _both(10.0, 1000, 10.0, 1000)
    rl = rate_limit.interpret(snap, now_epoch=1000)
    assert rl.primary.seconds_until_reset == 0
    assert rl.primary.reset_passed is True


def test_interpret_flags_stale_and_home_unverified():
    snap = _both(10.0, 9999999, 10.0, 9999999)
    rl = rate_limit.interpret(
        snap, now_epoch=10000, captured_at=1000, cache_home="/a/.codex",
        current_home="/b/.codex", stale_seconds=1800,
    )
    assert rl.is_stale is True
    assert rl.home_unverified is True


def test_live_age_zero_not_stale_and_source_current_run():
    snap = _both(10.0, 9999999, 10.0, 9999999)
    rl = rate_limit.live(snap, now_epoch=1000)
    assert rl.age_seconds == 0 and rl.is_stale is False
    assert rl.source == "current_run"


def test_live_none_when_no_snapshot():
    assert rate_limit.live(None, now_epoch=1000) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rate_limit.py -k "interpret or live" -v`
Expected: FAIL with `AttributeError: module 'codex_in_claude.rate_limit' has no attribute 'interpret'`

- [ ] **Step 3: Add agent-facing models to `schemas.py`**

Insert after the `RateLimitSnapshot` model from Task 1:

```python
RateLimitStatus = Literal["available", "limited", "exhausted", "unknown"]


class RateLimitWindow(BaseModel):
    """One quota window, interpreted for an agent. used_percent/remaining_percent are
    current-ish (as of `as_of`) for an open window and are NULLED when reset_passed is
    true (the window rolled over since capture, so its captured usage is obsolete and
    its post-reset usage is unobserved). One source of truth: a present percentage
    always means current-ish, never stale."""

    model_config = ConfigDict(extra="forbid")
    used_percent: float | None = None
    remaining_percent: float | None = None  # max(0, 100 - used_percent); None if reset_passed
    window_minutes: int | None = None
    resets_at: int | None = None  # epoch seconds
    seconds_until_reset: int | None = None  # clamped >= 0; 0 if reset_passed; None if resets_at unknown
    reset_passed: bool = False


class RateLimit(BaseModel):
    """Agent-facing rate-limit quota. A snapshot captured opportunistically from a
    paid call, interpreted against each window's reset clock. NOT a live query.

    Asymmetric by design: `available` is reported only when every binding window is
    observed and healthy; an unobserved window (reset-passed, missing, or lacking
    resets_at) never yields `available` — it degrades to `unknown`. `limited`/
    `exhausted` come only from still-open windows, so they stay conservative even when
    the snapshot is stale (captured usage is a lower bound on current usage)."""

    model_config = ConfigDict(extra="forbid")
    status: RateLimitStatus
    source: Literal["current_run", "plugin_cache"] = "plugin_cache"
    as_of: str | None = None  # ISO-8601 capture time; None when no snapshot
    age_seconds: int | None = None
    is_stale: bool = False  # older than the configured warn threshold (advisory)
    plan_type: str | None = None  # captured metadata, NOT a verified current plan
    home_unverified: bool = False  # cached CODEX_HOME differs from the current environment
    limiting_window: Literal["primary", "secondary"] | None = None
    primary: RateLimitWindow | None = None  # 5-hour window
    secondary: RateLimitWindow | None = None  # weekly window
    note: str | None = None
```

- [ ] **Step 4: Add interpretation to `rate_limit.py`**

Append to `src/codex_in_claude/rate_limit.py`:

```python
_REFRESH_HINT = (
    "No Codex rate-limit data yet; run any Codex call (consult/review/delegate) to populate it."
)
_LIMITED_THRESHOLD = 25.0  # remaining_percent below this on an open window -> 'limited'
_EXPECTED_WINDOWS = ("primary", "secondary")


def interpret(
    snapshot: RateLimitSnapshot | None,
    *,
    now_epoch: int,
    captured_at: int | None = None,
    cache_home: str | None = None,
    current_home: str | None = None,
    stale_seconds: int | None = None,
    source: str = "plugin_cache",
) -> RateLimit:
    """Turn a raw snapshot into the agent-facing RateLimit, reasoning about staleness
    against each window's own resets_at. A None snapshot yields status 'unknown'.

    Asymmetric: `available` only when every expected window is open (not reset-passed,
    has resets_at) and healthy; an unobserved window degrades to `unknown`. Risk
    verdicts (`limited`/`exhausted`) come only from open windows, so they stay
    conservative under staleness."""
    if snapshot is None:
        return RateLimit(status="unknown", source=source, note=_REFRESH_HINT)
    threshold = stale_seconds if stale_seconds is not None else config.rate_limit_stale_seconds()
    age = max(0, now_epoch - captured_at) if captured_at is not None else None
    is_stale = age is not None and age > threshold
    home_unverified = bool(cache_home and current_home and cache_home != current_home)
    primary = _window(snapshot.primary, now_epoch)
    secondary = _window(snapshot.secondary, now_epoch)
    status, limiting, note = _status(snapshot, primary, secondary)
    return RateLimit(
        status=status,
        source=source,
        as_of=_iso(captured_at) if captured_at is not None else None,
        age_seconds=age,
        is_stale=is_stale,
        plan_type=snapshot.plan_type,
        home_unverified=home_unverified,
        limiting_window=limiting,
        primary=primary,
        secondary=secondary,
        note=note,
    )


def live(snapshot: RateLimitSnapshot | None, *, now_epoch: int) -> RateLimit | None:
    """RateLimit for a just-captured snapshot (for Meta): age 0, never stale, source
    'current_run'. None when there is no snapshot."""
    if snapshot is None:
        return None
    return interpret(snapshot, now_epoch=now_epoch, captured_at=now_epoch, source="current_run")


def current() -> RateLimit:
    """Load and interpret the cached snapshot for codex_status (free, local). Tolerant:
    a missing/corrupt cache or bad envelope types degrade to 'unknown', never raise."""
    now = int(time.time())
    raw = _load_raw()
    if raw is None:
        return interpret(None, now_epoch=now)
    captured_at = raw.get("captured_at")
    if isinstance(captured_at, bool) or not isinstance(captured_at, (int, float)):
        captured_at = None
    else:
        captured_at = int(captured_at)
    cache_home = raw.get("codex_home")
    if not isinstance(cache_home, str):
        cache_home = None
    try:
        snapshot = RateLimitSnapshot.model_validate(raw.get("snapshot"))
    except Exception:
        return interpret(None, now_epoch=now)
    return interpret(
        snapshot,
        now_epoch=now,
        captured_at=captured_at,
        cache_home=cache_home,
        current_home=str(config.codex_home()),
    )


def capture(events: str, *, now_epoch: int | None = None) -> RateLimit | None:
    """Parse a paid run's events for a rate_limits block; persist it (best-effort) and
    return the live RateLimit for the call's Meta. None when no block was emitted."""
    now = now_epoch if now_epoch is not None else int(time.time())
    snapshot = normalize.parse_rate_limit(events)
    if snapshot is None:
        return None
    save(snapshot, now_epoch=now)
    return live(snapshot, now_epoch=now)


def _window(snap: RateLimitWindowSnapshot | None, now_epoch: int) -> RateLimitWindow | None:
    if snap is None:
        return None
    resets = snap.resets_at
    reset_passed = resets is not None and now_epoch >= resets
    if reset_passed:
        # The window rolled over since capture: captured usage is obsolete, post-reset
        # usage is unobserved. Null the percentages so a present value always means
        # current-ish.
        return RateLimitWindow(
            used_percent=None,
            remaining_percent=None,
            window_minutes=snap.window_minutes,
            resets_at=resets,
            seconds_until_reset=0,
            reset_passed=True,
        )
    used = snap.used_percent
    remaining = max(0.0, 100.0 - used) if used is not None else None
    secs = max(0, resets - now_epoch) if resets is not None else None
    return RateLimitWindow(
        used_percent=used,
        remaining_percent=remaining,
        window_minutes=snap.window_minutes,
        resets_at=resets,
        seconds_until_reset=secs,
        reset_passed=False,
    )


def _is_open(w: RateLimitWindow | None) -> bool:
    """A window usable for a current decision: present, not rolled over, with a usable
    resets_at (so we can trust its freshness) and a known remaining."""
    return (
        w is not None
        and not w.reset_passed
        and w.resets_at is not None
        and w.remaining_percent is not None
    )


def _status(
    snapshot: RateLimitSnapshot,
    primary: RateLimitWindow | None,
    secondary: RateLimitWindow | None,
) -> tuple[str, str | None, str | None]:
    """Return (status, limiting_window, note)."""
    windows = dict(zip(_EXPECTED_WINDOWS, (primary, secondary), strict=True))
    present = {name: w for name, w in windows.items() if w is not None}
    if not present:
        return "unknown", None, _REFRESH_HINT
    open_windows = {name: w for name, w in windows.items() if _is_open(w)}

    # 1. Codex explicitly named the window that hit its limit.
    reached = (snapshot.rate_limit_reached_type or "").strip().lower()
    if reached:
        if reached in open_windows:
            return "exhausted", reached, None
        # The reached window has since reset or is absent -> snapshot not actionable.
        return "unknown", None, f"codex reported '{reached}' reached its limit but that window is no longer observable; refresh."

    # 2. Conservative risk from open windows (safe even if stale: captured usage is a
    #    lower bound on current usage within an open window).
    exhausted = {n: w for n, w in open_windows.items() if w.remaining_percent <= 0}
    if exhausted:
        n = min(exhausted, key=lambda k: exhausted[k].remaining_percent)
        return "exhausted", n, None
    limited = {n: w for n, w in open_windows.items() if w.remaining_percent < _LIMITED_THRESHOLD}
    if limited:
        n = min(limited, key=lambda k: limited[k].remaining_percent)
        return "limited", n, None

    # 3. No risk signal. `available` only if EVERY expected window is open and healthy;
    #    any unobserved window could be the binding constraint -> unknown.
    unobserved = [n for n in _EXPECTED_WINDOWS if n not in open_windows]
    if unobserved:
        return "unknown", None, f"quota for the {', '.join(unobserved)} window(s) is unobserved (reset, missing, or stale); refresh before relying on availability."
    n = min(open_windows, key=lambda k: open_windows[k].remaining_percent)
    return "available", n, None


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_rate_limit.py -v`
Expected: PASS (all, including Task 2's)

- [ ] **Step 6: Run the gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/codex_in_claude/schemas.py src/codex_in_claude/rate_limit.py tests/test_rate_limit.py
git commit -m "feat(schemas): interpret rate-limit snapshot with reset-aware staleness"
```

---

### Task 4: Capture on every paid call and attach to `Meta`

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (add `rate_limit` field to `Meta`, ~line 196 before `context_summary`)
- Modify: `src/codex_in_claude/orchestration.py` (`_stamp_meta`, ~line 35)
- Modify: `src/codex_in_claude/delegate.py` (`_apply_run_meta`, ~line 59)
- Test: `tests/test_orchestration.py`, `tests/test_delegate.py`

**Interfaces:**
- Consumes: `rate_limit.capture(events) -> RateLimit | None` (Task 3).
- Produces: `Meta.rate_limit: RateLimit | None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_orchestration.py` (match the existing pattern for building a `CodexExecResult`/`Meta` in that file; the assertion is the new behavior):

```python
def test_stamp_meta_attaches_rate_limit(monkeypatch):
    from codex_in_claude import orchestration
    from codex_in_claude.schemas import Meta

    events = (
        '{"type":"event_msg","payload":{"type":"token_count",'
        '"rate_limits":{"primary":{"used_percent":10.0,"window_minutes":300,"resets_at":9999999999},'
        '"secondary":{"used_percent":5.0,"window_minutes":10080,"resets_at":9999999999},'
        '"plan_type":"plus"}}}'
    )
    # Don't touch the real cache file during the test.
    monkeypatch.setattr("codex_in_claude.rate_limit.save", lambda *a, **k: None)
    meta = Meta(cwd="/x", tier="consult", sandbox="read-only", isolation="inherit", timeout_seconds=180, elapsed_ms=0)
    result = _make_exec_result(events=events, exit_code=0, last_message="hi")  # existing helper
    orchestration._stamp_meta(result, meta)
    assert meta.rate_limit is not None
    assert meta.rate_limit.status == "available"
    assert meta.rate_limit.plan_type == "plus"
```

If `tests/test_orchestration.py` has no `_make_exec_result` helper, build the `codex.CodexExecResult` inline the way the file's other tests do.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestration.py -k rate_limit -v`
Expected: FAIL with `AttributeError: 'Meta' object has no attribute 'rate_limit'`

- [ ] **Step 3: Add the `Meta.rate_limit` field**

In `schemas.py`, in `Meta`, immediately before `context_summary: ContextSummary | None = None`:

```python
    # Live rate-limit quota snapshot captured from this call's event stream (the same
    # data codex_status reports from cache). None when codex emitted no rate_limits block.
    rate_limit: RateLimit | None = None
```

- [ ] **Step 4: Wire capture into `_stamp_meta`**

In `orchestration.py`, add `rate_limit` to the package import:

```python
from codex_in_claude import codex, normalize, prompts, rate_limit
```

In `_stamp_meta`, after `meta.session_id = session_id`:

```python
    meta.rate_limit = rate_limit.capture(result.events)
```

- [ ] **Step 5: Wire capture into delegate `_apply_run_meta`**

In `delegate.py`, add to the package import (match the existing import line) `rate_limit`, then in `_apply_run_meta` after `meta.session_id = session_id`:

```python
    meta.rate_limit = rate_limit.capture(result.events)
```

- [ ] **Step 6: Add the delegate test**

Add to `tests/test_delegate.py` a test mirroring Step 1 against `delegate._apply_run_meta` (same `events`, same `monkeypatch` of `rate_limit.save`), asserting `meta.rate_limit.status == "available"`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestration.py tests/test_delegate.py -k rate_limit -v`
Expected: PASS

- [ ] **Step 8: Run the gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add src/codex_in_claude/schemas.py src/codex_in_claude/orchestration.py src/codex_in_claude/delegate.py tests/test_orchestration.py tests/test_delegate.py
git commit -m "feat(tools): capture rate-limit snapshot on paid calls and attach to meta"
```

---

### Task 5: Report quota in `codex_status`

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (add `rate_limit` field to `StatusResult`, ~line 306 before `caveat`)
- Modify: `src/codex_in_claude/server.py` (`codex_status`, ~line 505–565)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `rate_limit.current() -> RateLimit` (Task 3).
- Produces: `StatusResult.rate_limit: RateLimit | None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_codex_status_includes_rate_limit_unknown_without_cache(monkeypatch):
    from codex_in_claude import rate_limit, server

    monkeypatch.setattr(rate_limit, "_load_raw", lambda path=None: None)
    result = server.codex_status()
    assert result["rate_limit"]["status"] == "unknown"
    assert result["rate_limit"]["note"]


def test_codex_status_reports_cached_snapshot(monkeypatch):
    from codex_in_claude import rate_limit, server

    monkeypatch.setattr(
        rate_limit,
        "_load_raw",
        lambda path=None: {
            "version": rate_limit.CACHE_VERSION,
            "captured_at": 1,
            "codex_home": str(__import__("codex_in_claude.config", fromlist=["codex_home"]).codex_home()),
            "snapshot": {
                "plan_type": "plus",
                "primary": {"used_percent": 10.0, "window_minutes": 300, "resets_at": 9999999999},
                "secondary": {"used_percent": 5.0, "window_minutes": 10080, "resets_at": 9999999999},
            },
        },
    )
    result = server.codex_status()
    assert result["rate_limit"]["status"] == "available"
    assert result["rate_limit"]["plan_type"] == "plus"
    assert result["rate_limit"]["source"] == "plugin_cache"
    assert result["rate_limit"]["home_unverified"] is False


def test_codex_status_tolerates_corrupt_cache_envelope(monkeypatch):
    from codex_in_claude import rate_limit, server

    # captured_at as a string would crash arithmetic if not validated -> must degrade.
    monkeypatch.setattr(
        rate_limit,
        "_load_raw",
        lambda path=None: {
            "version": rate_limit.CACHE_VERSION,
            "captured_at": "not-a-number",
            "codex_home": ["bad"],
            "snapshot": {"primary": {"used_percent": 10.0, "resets_at": 9999999999}},
        },
    )
    result = server.codex_status()
    # captured_at invalid -> as_of/age drop out, but interpretation must not raise.
    assert result["rate_limit"]["as_of"] is None
    assert result["rate_limit"]["status"] in {"unknown", "available", "limited", "exhausted"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -k rate_limit -v`
Expected: FAIL with `KeyError: 'rate_limit'`

- [ ] **Step 3: Add the `StatusResult.rate_limit` field**

In `schemas.py`, in `StatusResult`, immediately before `caveat: str`:

```python
    rate_limit: RateLimit = Field(  # always present; status 'unknown' when no cache
        default_factory=lambda: RateLimit(status="unknown")
    )
```

- [ ] **Step 4: Populate it in `codex_status`**

In `server.py`, add `rate_limit` to the package import (the line importing `config`, `codex`, `preflight`, etc.), then in the `StatusResult(...)` constructor (~line 541), add:

```python
        rate_limit=rate_limit.current(),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -k rate_limit -v`
Expected: PASS

- [ ] **Step 6: Run the full suite (catch FINGERPRINT/snapshot assertions)**

Run: `uv run pytest -q`
Expected: PASS. If a capabilities/fingerprint snapshot test fails, it is expected to fail until Task 6 bumps `FINGERPRINT`; note it and continue.

- [ ] **Step 7: Run the gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add src/codex_in_claude/schemas.py src/codex_in_claude/server.py tests/test_server.py
git commit -m "feat(tools): report rate-limit quota from codex_status"
```

---

### Task 6: Bump the surface fingerprint, version, and changelog

**Files:**
- Modify: `src/codex_in_claude/schemas.py` (`FINGERPRINT`)
- Modify: `pyproject.toml`, `.claude-plugin/plugin.json`, `.mcp.json`
- Modify: `CHANGELOG.md`
- Test: existing fingerprint/capabilities tests

**Interfaces:** none new — this finalizes the surface change from Tasks 1–5.

- [ ] **Step 1: Bump `FINGERPRINT`**

In `schemas.py`: `FINGERPRINT = "codex-in-claude/0.1/schema-12"`.

- [ ] **Step 2: Bump the version literals**

- `pyproject.toml`: `version = "0.5.0"`
- `.claude-plugin/plugin.json`: `"version": "0.5.0"`
- `.mcp.json`: `"codex-in-claude==0.5.0"`

- [ ] **Step 3: Add the changelog entry**

Under `## [Unreleased]` in `CHANGELOG.md`, add:

```markdown
### Added

- **`codex_status` now reports Codex rate-limit quota.** A new `rate_limit` block reports how much of
  the 5-hour (`primary`) and weekly (`secondary`) windows remains, with `status`
  (`available`/`limited`/`exhausted`/`unknown`), per-window `remaining_percent`,
  `resets_at`/`seconds_until_reset`, `is_stale`, and `home_unverified` (provenance) flags. The
  snapshot is captured opportunistically from paid
  `codex_consult`/`codex_review_changes`/`codex_delegate` calls (zero extra spend) and cached
  locally; the live snapshot is also attached to each active call's `meta.rate_limit` (`source`
  distinguishes `current_run` from `plugin_cache`). Staleness is interpreted against each window's own
  reset clock with an asymmetric rule — an unobserved (reset-passed or missing) window degrades to
  `unknown` rather than reporting as available — so an old snapshot can't mislead. Configurable via
  `CODEX_IN_CLAUDE_RATE_LIMIT_FILE` and `CODEX_IN_CLAUDE_RATE_LIMIT_STALE_SECONDS`.

### Changed

- The result `fingerprint` changes (`codex-in-claude/0.1/schema-11` → `codex-in-claude/0.1/schema-12`)
  because the agent-visible surface gained the `rate_limit` block on `codex_status` and `meta`.
```

- [ ] **Step 4: Update any fingerprint-pinned test**

If a test asserts the literal `schema-11` (e.g. `tests/test_schemas.py` or a capabilities snapshot), update it to `schema-12`. Find with: `grep -rn "schema-11" tests/`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, coverage ≥ 95%.

- [ ] **Step 6: Run the gate + lock check**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv lock --check`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/codex_in_claude/schemas.py pyproject.toml .claude-plugin/plugin.json .mcp.json CHANGELOG.md tests/
git commit -m "feat(schemas)!: surface rate-limit quota (fingerprint schema-12, v0.5.0)

BREAKING CHANGE: codex_status and meta gain a rate_limit block; fingerprint
bumps codex-in-claude/0.1/schema-11 -> codex-in-claude/0.1/schema-12."
```

---

## Self-Review

This plan incorporates a cross-model (Codex) review of the original draft; the
status model was revised so an unobserved window never yields a positive spend
signal (no `replenished` "healthy" state), `rate_limit_reached_type` selects the
window, reset-passed windows null their percentages, cache writes are atomic, and the
cache envelope is type-validated.

**Spec coverage:**
- B1 opportunistic capture → Task 4 (`rate_limit.capture` on both paid paths, covering sync + async via `_stamp_meta`/`_apply_run_meta`). ✓
- Plugin-owned cache file, provenance stamp, atomic write → Task 2 (`save` with `captured_at`/`codex_home`/`version`, temp+`os.replace`). ✓
- Report in `codex_status` → Task 5; on `Meta` (source `current_run`) → Task 4. ✓
- Per-window reset awareness → Task 3 (`_window.reset_passed` nulls percentages, `_is_open`). ✓
- Asymmetric status: `available` only when both windows open+healthy; unobserved → `unknown`; conservative `limited`/`exhausted` from open windows; `rate_limit_reached_type` selects/degrades → Task 3 (`_status`). ✓
- Freshness fields (`as_of`/`age_seconds`/`is_stale`) → Task 3. ✓
- Provenance guard (`home_unverified`, CODEX_HOME-only) → Task 3. ✓
- Clock-skew clamp → Task 3 (`seconds_until_reset = max(0, …)`, 0 when reset_passed). ✓
- Tolerant cache reads (type-validated envelope, corrupt → unknown, never raises) → Task 3 `current()` / Task 5 test. ✓
- `remaining_percent` + `used_percent` (nulled on reset), two windows, `limiting_window` → Task 3. ✓
- `cli_contract.py` owns the field name → Task 1. ✓
- Tolerant parsing → Task 1 (`extra="ignore"`, try/except). ✓
- FINGERPRINT + version set + CHANGELOG → Task 6. ✓
- B2/B3 session scanning, paid refresh, history excluded → not implemented (out of scope). ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. Task 4 Step 1 references the file's existing `CodexExecResult` construction pattern with an explicit fallback instruction. ✓

**Type consistency:** `RateLimitSnapshot`/`RateLimitWindowSnapshot` (raw) and `RateLimit`/`RateLimitWindow` (agent-facing) used consistently; status enum is `available|limited|exhausted|unknown` everywhere (no `replenished`); `interpret` takes `source` and returns a `RateLimit` whose `note` comes from `_status`'s 3-tuple; `home_unverified` (not `unverified`) in schema + interpret + tests; `capture`/`live`/`interpret`/`current` signatures match across Tasks 3–5; `Meta.rate_limit` and `StatusResult.rate_limit` both typed `RateLimit`. ✓
