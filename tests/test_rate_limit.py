import pytest

from codex_in_claude import appserver, rate_limit
from codex_in_claude.schemas import (
    RateLimitSnapshot,
    RateLimitWindow,
    RateLimitWindowSnapshot,
)


def _win(used, resets):
    return RateLimitWindowSnapshot(used_percent=used, window_minutes=300, resets_at=resets)


def _interpreted_window_fixture(resets_at_epoch) -> RateLimitWindow:
    """Interpret a snapshot with a primary window carrying resets_at_epoch, going
    through the real rate_limit.interpret() path (not RateLimitWindow directly)."""
    snap = RateLimitSnapshot(plan_type="plus", primary=_win(10.0, resets_at_epoch), secondary=None)
    rl = rate_limit.interpret(snap, now_epoch=1000)
    assert rl.primary is not None
    return rl.primary


def _both(p_used, p_reset, s_used, s_reset):
    return RateLimitSnapshot(
        plan_type="plus",
        primary=_win(p_used, p_reset),
        secondary=_win(s_used, s_reset),
    )


def test_interpret_no_snapshot_is_unknown():
    rl = rate_limit.interpret(None, now_epoch=1000)
    assert rl.status == "unknown"
    assert rl.note  # carries a refresh hint
    assert rl.as_of is None


def test_interpret_available_requires_both_windows_open_and_healthy():
    # Use modern epoch so as_of ISO-8601 starts with "20" (brief used tiny epoch 900
    # which is 1970-01-01; arithmetic is preserved: age=100, secs_until_reset=8000).
    snap = _both(10.0, 1_700_009_000, 40.0, 1_700_009_000)
    rl = rate_limit.interpret(snap, now_epoch=1_700_001_000, captured_at=1_700_000_900)
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
        snap,
        now_epoch=10000,
        captured_at=1000,
        cache_home="/a/.codex",
        current_home="/b/.codex",
        stale_seconds=1800,
    )
    assert rl.is_stale is True
    assert rl.home_unverified is True


def test_interpret_single_present_window_can_be_available():
    # The #321 topology: the app-server reports only ONE binding window (here the weekly
    # `secondary`, with no primary). An absent window is NOT 'unobserved' — a single healthy
    # window must still yield 'available', not a permanent 'unknown'.
    snap = RateLimitSnapshot(
        plan_type="plus",
        primary=None,
        secondary=RateLimitWindowSnapshot(
            used_percent=6.0, window_minutes=10080, resets_at=1_700_009_000
        ),
    )
    rl = rate_limit.interpret(snap, now_epoch=1_700_001_000, captured_at=1_700_000_900)
    assert rl.status == "available"
    assert rl.limiting_window == "secondary"
    assert rl.primary is None
    assert rl.secondary is not None and rl.secondary.remaining_percent == 94.0


def test_interpret_reached_reason_enum_escalates_to_exhausted():
    # 0.144's rateLimitReachedType is a REASON code, not a window name. A non-empty reason
    # must escalate to 'exhausted' (a limit was hit), naming the binding open window.
    snap = _both(10.0, 1_700_009_000, 40.0, 1_700_009_000)
    snap.rate_limit_reached_type = "workspace_owner_usage_limit_reached"
    rl = rate_limit.interpret(snap, now_epoch=1_700_001_000, captured_at=1_700_000_900)
    assert rl.status == "exhausted"
    assert rl.limiting_window == "secondary"  # lower remaining (60 vs 90)
    assert rl.note is not None and "usage limit was reached" in rl.note


# --- spend control (#359) -------------------------------------------------------
# Only an explicit True is a verdict. None means the backend did not report the signal —
# absence of evidence, not evidence of a block — so it leaves the window-derived status
# intact and only discloses itself in `note`.


def test_interpret_spend_control_true_is_blocked_over_healthy_windows():
    # An administrative spend block outranks perfectly healthy quota: paid calls fail even
    # though every window says there is room.
    snap = _both(10.0, 1_700_009_000, 5.0, 1_700_009_000)
    snap.spend_control_reached = True
    rl = rate_limit.interpret(snap, now_epoch=1_700_001_000, captured_at=1_700_000_900)
    assert rl.status == "blocked"
    assert rl.spend_control_reached is True
    assert rl.limiting_window is None  # no window binds an administrative block
    assert rl.note is not None and "spend control" in rl.note.lower()
    # The windows stay visible for transparency — the block is orthogonal to quota.
    assert rl.primary is not None and rl.secondary is not None


def test_interpret_spend_control_true_outranks_reached_type():
    # Both signals present: report the one whose remedy differs from waiting.
    snap = _both(10.0, 1_700_009_000, 40.0, 1_700_009_000)
    snap.rate_limit_reached_type = "workspace_owner_usage_limit_reached"
    snap.spend_control_reached = True
    rl = rate_limit.interpret(snap, now_epoch=1_700_001_000, captured_at=1_700_000_900)
    assert rl.status == "blocked"


def test_interpret_spend_control_true_with_no_windows_is_blocked():
    # A windowless snapshot still yields 'blocked' — spend control is snapshot-level and needs
    # no window to bind it (contrast the rateLimitReachedType branch, which does).
    snap = RateLimitSnapshot(plan_type="plus", primary=None, secondary=None)
    snap.spend_control_reached = True
    rl = rate_limit.interpret(snap, now_epoch=1_700_001_000, captured_at=1_700_000_900)
    assert rl.status == "blocked"
    assert rl.limiting_window is None


def test_interpret_spend_control_false_leaves_status_untouched():
    snap = _both(10.0, 1_700_009_000, 40.0, 1_700_009_000)
    snap.spend_control_reached = False
    rl = rate_limit.interpret(snap, now_epoch=1_700_001_000, captured_at=1_700_000_900)
    assert rl.status == "available"
    assert rl.spend_control_reached is False
    assert rl.note is None  # nothing to disclose: the backend answered


def test_interpret_spend_control_none_keeps_available_but_discloses():
    # A CLI that omits the field (0.144) must not lose its usable window reading — #321 fixed
    # exactly this class of permanent 'unknown'. The gap is disclosed in `note` instead.
    snap = _both(10.0, 1_700_009_000, 40.0, 1_700_009_000)
    assert snap.spend_control_reached is None
    rl = rate_limit.interpret(snap, now_epoch=1_700_001_000, captured_at=1_700_000_900)
    assert rl.status == "available"
    assert rl.spend_control_reached is None
    assert rl.note is not None and "spend-control" in rl.note.lower()


def test_interpret_spend_control_none_does_not_overwrite_a_risk_note():
    # The disclosure is only for a would-be 'available'; a real risk verdict keeps its own
    # reasoning (here: a reached-type note) rather than being papered over.
    snap = _both(10.0, 1_700_009_000, 40.0, 1_700_009_000)
    snap.rate_limit_reached_type = "workspace_owner_usage_limit_reached"
    rl = rate_limit.interpret(snap, now_epoch=1_700_001_000, captured_at=1_700_000_900)
    assert rl.status == "exhausted"
    assert rl.note is not None and "usage limit was reached" in rl.note


def test_interpret_home_unverified_still_wins_over_spend_control_disclosure():
    # A cross-CODEX_HOME snapshot is already forced to 'unknown' with its own note; the
    # disclosure must not clobber that more serious caveat.
    snap = _both(10.0, 9_999_999_999, 40.0, 9_999_999_999)
    rl = rate_limit.interpret(
        snap, now_epoch=1000, captured_at=1000, cache_home="/a/.codex", current_home="/b/.codex"
    )
    assert rl.status == "unknown"
    assert rl.note is not None and "CODEX_HOME" in rl.note


def test_live_read_ok_interprets_ephemerally(monkeypatch):
    """live_read maps an OK app-server outcome to an interpreted, live-sourced RateLimit. It is
    EPHEMERAL — nothing is persisted (rate_limit has no save()), keeping codex_status read-only."""
    assert not hasattr(rate_limit, "save")  # persistence was removed (#321 review F7)
    snap = RateLimitSnapshot(
        plan_type="plus",
        secondary=RateLimitWindowSnapshot(
            used_percent=6.0, window_minutes=10080, resets_at=9_999_999_999
        ),
    )
    outcome = appserver.RateLimitReadOutcome(
        status=appserver.RateLimitReadStatus.OK, snapshot=snap, codex_home="/tmp/ch"
    )
    monkeypatch.setattr(appserver, "read_rate_limits", lambda **kw: outcome)
    rl = rate_limit.live_read(timeout_seconds=5, now_epoch=1000)
    assert rl.status == "available"
    assert rl.source == "app_server_live"
    assert rl.is_stale is False and rl.home_unverified is False


def test_live_read_no_quota_is_unavailable(monkeypatch):
    outcome = appserver.RateLimitReadOutcome(status=appserver.RateLimitReadStatus.NO_QUOTA)
    monkeypatch.setattr(appserver, "read_rate_limits", lambda **kw: outcome)
    rl = rate_limit.live_read(timeout_seconds=5)
    assert rl.status == "unavailable"
    assert rl.source == "app_server_live"


@pytest.mark.parametrize(
    "status",
    [appserver.RateLimitReadStatus.UNSUPPORTED, appserver.RateLimitReadStatus.PROTOCOL_ERROR],
)
def test_live_read_unsupported_or_drift_is_unavailable(monkeypatch, status):
    outcome = appserver.RateLimitReadOutcome(status=status)
    monkeypatch.setattr(appserver, "read_rate_limits", lambda **kw: outcome)
    rl = rate_limit.live_read(timeout_seconds=5)
    assert rl.status == "unavailable"


@pytest.mark.parametrize(
    "status",
    [appserver.RateLimitReadStatus.TIMED_OUT, appserver.RateLimitReadStatus.SPAWN_FAILED],
)
def test_live_read_transient_failure_is_unknown_never_stale_cache(monkeypatch, status):
    # A transient failure is honestly 'unknown' — never a (possibly stale) cached snapshot,
    # since persistence was removed (#321 review F3/F4).
    outcome = appserver.RateLimitReadOutcome(status=status)
    monkeypatch.setattr(appserver, "read_rate_limits", lambda **kw: outcome)
    rl = rate_limit.live_read(timeout_seconds=5)
    assert rl.status == "unknown"
    assert rl.source == "app_server_live"


def test_live_read_never_raises(monkeypatch):
    def boom(**kw):
        raise RuntimeError("injected")

    monkeypatch.setattr(appserver, "read_rate_limits", boom)
    rl = rate_limit.live_read(timeout_seconds=5)
    assert rl.status == "unknown"


def test_not_ready_is_static_unknown():
    rl = rate_limit.not_ready()
    assert rl.status == "unknown"
    assert rl.note is not None


# ---------------------------------------------------------------------------
# Finding 1: cross-CODEX_HOME snapshot must degrade to unknown
# ---------------------------------------------------------------------------


def test_interpret_cross_home_healthy_snapshot_is_unknown():
    """A healthy snapshot captured under a different CODEX_HOME must never report
    available — home_unverified=True overrides status to unknown regardless of
    window health, and keeps window objects for transparency."""
    snap = _both(10.0, 9999999999, 10.0, 9999999999)
    rl = rate_limit.interpret(
        snap,
        now_epoch=1000,
        captured_at=900,
        cache_home="/other/.codex",
        current_home="/current/.codex",
    )
    assert rl.status == "unknown"
    assert rl.limiting_window is None
    assert rl.home_unverified is True
    assert rl.note is not None and "CODEX_HOME" in rl.note
    # Window objects are preserved for transparency.
    assert rl.primary is not None
    assert rl.secondary is not None


def test_interpret_same_home_healthy_snapshot_is_available():
    """Same-home check: home_unverified=False must not degrade a healthy snapshot."""
    snap = _both(10.0, 1_700_009_000, 10.0, 1_700_009_000)
    rl = rate_limit.interpret(
        snap,
        now_epoch=1_700_001_000,
        captured_at=1_700_000_900,
        cache_home="/same/.codex",
        current_home="/same/.codex",
    )
    assert rl.status == "available"
    assert rl.home_unverified is False


class TestResetsAtRfc3339:
    def test_resets_at_is_rfc3339_string(self):
        # Build a snapshot the interpreter accepts; pick any existing test that
        # produces a populated window and reuse its fixture pattern.
        win = _interpreted_window_fixture(resets_at_epoch=1_750_000_000)
        assert isinstance(win.resets_at, str)
        assert win.resets_at == "2025-06-15T15:06:40+00:00"

    @pytest.mark.parametrize("bad", [1e30, -1e30, float("nan"), float("inf")])
    def test_out_of_range_epoch_degrades_to_null(self, bad):
        win = _interpreted_window_fixture(resets_at_epoch=bad)
        assert win.resets_at is None  # tolerant parsing preserved — never raises
