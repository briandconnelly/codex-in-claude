"""Persist, load, and interpret the latest Codex rate-limit snapshot.

Capture is opportunistic: paid calls already parse the token_count event for token
usage, so we lift the sibling rate_limits block at no extra spend, persist the latest,
and interpret it against each window's own resets_at when read — so a stale cache
can't mislead: an unobserved (reset-passed or missing) window never reports as
available, while conservative limited/exhausted verdicts from open windows survive."""

from __future__ import annotations

import contextlib
import json
import math
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeGuard, cast

from codex_in_claude import config, normalize
from codex_in_claude.schemas import (
    RateLimit,
    RateLimitSnapshot,
    RateLimitStatus,
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
    try:
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
            Path(tmp).replace(target)
        except Exception:
            with contextlib.suppress(Exception):
                Path(tmp).unlink()
    except Exception:
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
    source: Literal["current_run", "plugin_cache"] = "plugin_cache",
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
    if home_unverified:
        status = "unknown"
        limiting = None
        note = (
            "cached rate-limit snapshot came from a different CODEX_HOME;"
            " refresh before relying on availability."
        )
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
    if (
        isinstance(captured_at, bool)
        or not isinstance(captured_at, (int, float))
        or not math.isfinite(captured_at)
    ):
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
    try:
        # best-effort: metadata capture must never fail a paid call
        now = now_epoch if now_epoch is not None else int(time.time())
        snapshot = normalize.parse_rate_limit(events)
        if snapshot is None:
            return None
        save(snapshot, now_epoch=now)
        return live(snapshot, now_epoch=now)
    except Exception:
        return None


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
            resets_at=_iso_or_none(resets),
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
        resets_at=_iso_or_none(resets),
        seconds_until_reset=secs,
        reset_passed=False,
    )


def _is_open(w: RateLimitWindow | None) -> TypeGuard[RateLimitWindow]:
    """A window usable for a current decision: present, not rolled over, with a usable
    resets_at (so we can trust its freshness) and a known remaining."""
    return (
        w is not None
        and not w.reset_passed
        and w.resets_at is not None
        and w.remaining_percent is not None
    )


def _remaining(w: RateLimitWindow) -> float:
    """remaining_percent of a window; 0.0 when None (open windows guarantee non-None)."""
    return w.remaining_percent if w.remaining_percent is not None else 0.0


def _status(
    snapshot: RateLimitSnapshot,
    primary: RateLimitWindow | None,
    secondary: RateLimitWindow | None,
) -> tuple[RateLimitStatus, Literal["primary", "secondary"] | None, str | None]:
    """Return (status, limiting_window, note)."""
    windows = dict(zip(_EXPECTED_WINDOWS, (primary, secondary), strict=True))
    present = {name: w for name, w in windows.items() if w is not None}
    if not present:
        return "unknown", None, _REFRESH_HINT
    open_windows: dict[str, RateLimitWindow] = {
        name: w for name, w in windows.items() if _is_open(w)
    }

    # 1. Codex explicitly named the window that hit its limit.
    reached = (snapshot.rate_limit_reached_type or "").strip().lower()
    if reached:
        if reached in open_windows:
            return (
                "exhausted",
                cast('Literal["primary", "secondary"]', reached),
                None,
            )
        # The reached window has since reset or is absent -> snapshot not actionable.
        return (
            "unknown",
            None,
            f"codex reported '{reached}' reached its limit"
            " but that window is no longer observable; refresh.",
        )

    # 2. Conservative risk from open windows (safe even if stale: captured usage is a
    #    lower bound on current usage within an open window).
    exhausted = {n: w for n, w in open_windows.items() if _remaining(w) <= 0}
    if exhausted:
        n = min(exhausted, key=lambda k: _remaining(exhausted[k]))
        return "exhausted", cast('Literal["primary", "secondary"]', n), None
    limited = {n: w for n, w in open_windows.items() if _remaining(w) < _LIMITED_THRESHOLD}
    if limited:
        n = min(limited, key=lambda k: _remaining(limited[k]))
        return "limited", cast('Literal["primary", "secondary"]', n), None

    # 3. No risk signal. `available` only if EVERY expected window is open and healthy;
    #    any unobserved window could be the binding constraint -> unknown.
    unobserved = [n for n in _EXPECTED_WINDOWS if n not in open_windows]
    if unobserved:
        return (
            "unknown",
            None,
            f"quota for the {', '.join(unobserved)} window(s) is unobserved"
            " (reset, missing, or stale); refresh before relying on availability.",
        )
    n = min(open_windows, key=lambda k: _remaining(open_windows[k]))
    return "available", cast('Literal["primary", "secondary"]', n), None


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _iso_or_none(epoch: float | int | None) -> str | None:
    """RFC3339 UTC for a captured epoch, or None when absent/unrepresentable.

    The raw snapshot accepts any finite numeric; datetime.fromtimestamp raises
    OverflowError/OSError/ValueError outside its range — degrade, never raise."""
    if epoch is None or not math.isfinite(epoch):
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None
