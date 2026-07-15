"""Read and interpret the current Codex rate-limit snapshot.

codex 0.144 removed the token_count event that once carried quota on the `codex exec`
stream (#321); the data now lives on the app-server protocol. :func:`live_read` fetches it
via `account/rateLimits/read` (a read-only call with no model spend) and interprets it
against each window's own resets_at. The read is EPHEMERAL — nothing is persisted, so
codex_status stays a genuinely read-only call and no stale cache can ever mislead a spend
decision. Windows arrive already classified by duration (see appserver.py); either the
shorter (`primary`) or longer (`secondary`) window may be absent, and an absent window is
not treated as unobserved."""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Literal, TypeGuard, cast

from codex_in_claude import appserver, config
from codex_in_claude.schemas import (
    RateLimit,
    RateLimitSnapshot,
    RateLimitStatus,
    RateLimitWindow,
    RateLimitWindowSnapshot,
)

# `current_run`/`plugin_cache` are retained for schema compatibility; live reads use
# `app_server_live` (nothing is persisted now, so `plugin_cache` is no longer produced).
_LiveSource = Literal["current_run", "plugin_cache", "app_server_live"]

# Wall-clock cap for the codex_status live read. codex_status is a free, no-model-spend
# call; the app-server read normally returns in ~1s, so this is only a ceiling that bounds a
# hung child. Deliberately well under the model-call timeout — status must stay responsive.
READ_TIMEOUT_SECONDS = 20.0

_REFRESH_HINT = (
    "No Codex rate-limit reading available; run codex_status to fetch the current quota."
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
    source: _LiveSource = "plugin_cache",
) -> RateLimit:
    """Turn a raw snapshot into the agent-facing RateLimit, reasoning about staleness
    against each window's own resets_at. A None snapshot yields status 'unknown'.

    Asymmetric: `available` only when every window PRESENT in the snapshot is open (not
    reset-passed, has resets_at) and healthy; a present-but-unobservable window degrades to
    `unknown`, while a window the source omits is not a binding constraint. Risk verdicts
    (`limited`/`exhausted`) come only from open windows, so they stay conservative under
    staleness."""
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


_UNAVAILABLE_NO_QUOTA = "This Codex account exposes no rate-limit windows."
_UNAVAILABLE_UNSUPPORTED = (
    "This codex version did not expose rate-limit data via the app-server; codex-in-claude may"
    " need an update for your CLI."
)
_TRANSIENT_HINT = (
    "Could not read Codex quota just now (the app-server read timed out or could not start); retry."
)
_NOT_READY_HINT = "Codex is not ready (not installed or not authenticated); quota can't be read."


def not_ready() -> RateLimit:
    """The rate_limit codex_status reports when codex is not ready to read quota (missing or
    unauthenticated). A static 'unknown' — no app-server subprocess is spawned."""
    return RateLimit(status="unknown", source="app_server_live", note=_NOT_READY_HINT)


def live_read(
    *,
    timeout_seconds: float,
    command: list[str] | None = None,
    now_epoch: int | None = None,
) -> RateLimit:
    """Fetch the current quota LIVE from `codex app-server` (a read-only call, no model
    spend) and interpret it for codex_status. EPHEMERAL — nothing is persisted, so codex_status
    stays read-only and no stale cache can mislead. Never raises — every failure is a typed
    :class:`RateLimit`, not a silent None (#321):

    * OK          -> interpret (source `app_server_live`).
    * NO_QUOTA    -> 'unavailable' (the account has no quota windows).
    * UNSUPPORTED / PROTOCOL_ERROR -> 'unavailable' (this codex exposes no such data / drift).
    * TIMED_OUT / SPAWN_FAILED     -> 'unknown' (a transient failure — retry).

    ``command`` is injectable for tests; ``now_epoch`` fixes the clock."""
    now = now_epoch if now_epoch is not None else int(time.time())
    try:
        outcome = appserver.read_rate_limits(command=command, timeout_seconds=timeout_seconds)
    except Exception:
        # A live-read fault must never break codex_status.
        return RateLimit(status="unknown", source="app_server_live", note=_TRANSIENT_HINT)
    status = outcome.status
    if status is appserver.RateLimitReadStatus.OK and outcome.snapshot is not None:
        return interpret(outcome.snapshot, now_epoch=now, captured_at=now, source="app_server_live")
    if status is appserver.RateLimitReadStatus.NO_QUOTA:
        return RateLimit(status="unavailable", source="app_server_live", note=_UNAVAILABLE_NO_QUOTA)
    if status in (
        appserver.RateLimitReadStatus.UNSUPPORTED,
        appserver.RateLimitReadStatus.PROTOCOL_ERROR,
    ):
        return RateLimit(
            status="unavailable", source="app_server_live", note=_UNAVAILABLE_UNSUPPORTED
        )
    # TIMED_OUT / SPAWN_FAILED: a transient failure. Honestly 'unknown' — never a stale cache.
    return RateLimit(status="unknown", source="app_server_live", note=_TRANSIENT_HINT)


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

    # 1. Codex flagged that a limit was reached.
    reached = (snapshot.rate_limit_reached_type or "").strip().lower()
    if reached:
        if reached in open_windows:
            # Legacy window-name form ("primary"/"secondary"): name that window.
            return "exhausted", cast('Literal["primary", "secondary"]', reached), None
        if reached in _EXPECTED_WINDOWS:
            # Named a window that has since reset or is absent -> not actionable.
            return (
                "unknown",
                None,
                f"codex reported '{reached}' reached its limit"
                " but that window is no longer observable; refresh.",
            )
        # A reason code that does not name a window (0.144's app-server enum, e.g.
        # 'rate_limit_reached' / '*_usage_limit_reached'): a limit WAS hit. Escalate to
        # exhausted only while a window is still observable to bind it to; once every window has
        # reset, a cached reason code is no longer actionable and degrades to unknown — matching
        # the legacy window-name branch above, not a permanent stale 'exhausted' (review F5).
        if not open_windows:
            return (
                "unknown",
                None,
                f"codex reported a usage limit was reached ({reached})"
                " but no window is currently observable; refresh.",
            )
        binding = min(open_windows, key=lambda k: _remaining(open_windows[k]))
        return (
            "exhausted",
            cast('Literal["primary", "secondary"]', binding),
            f"codex reports a usage limit was reached ({reached}).",
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

    # 3. No risk signal. `available` only if every window PRESENT in the snapshot is open and
    #    healthy. The app-server reports the windows that currently bind the account, so an
    #    ABSENT window is not a binding constraint (not 'unobserved') — but a present window we
    #    cannot currently observe (reset-passed / no resets_at) still degrades to unknown.
    unobserved = [n for n in present if n not in open_windows]
    if unobserved:
        return (
            "unknown",
            None,
            f"quota for the {', '.join(unobserved)} window(s) is present but unobservable"
            " (reset or stale); refresh before relying on availability.",
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
