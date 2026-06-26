from pathlib import Path

from codex_in_claude import rate_limit
from codex_in_claude.schemas import RateLimitSnapshot, RateLimitWindowSnapshot


def _win(used, resets):
    return RateLimitWindowSnapshot(used_percent=used, window_minutes=300, resets_at=resets)


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


def test_live_age_zero_not_stale_and_source_current_run():
    snap = _both(10.0, 9999999, 10.0, 9999999)
    rl = rate_limit.live(snap, now_epoch=1000)
    assert rl.age_seconds == 0 and rl.is_stale is False
    assert rl.source == "current_run"


def test_live_none_when_no_snapshot():
    assert rate_limit.live(None, now_epoch=1000) is None


def test_save_hardening_does_not_raise_when_model_dump_fails(tmp_path: Path, monkeypatch):
    """save() must never raise, even if payload construction fails (directive-2 regression).
    Pydantic v2 blocks instance-level patching of model_dump, so we inject a failure via
    config.codex_home (called when home=None), which sits inside the same outer try guard."""

    def boom():
        raise RuntimeError("injected failure")

    monkeypatch.setattr(rate_limit.config, "codex_home", boom)
    # home=None forces the code path through config.codex_home() which raises.
    # save() must absorb the exception without propagating it.
    rate_limit.save(_snap(), now_epoch=1, path=tmp_path / "snap.json", home=None)


def _snap() -> RateLimitSnapshot:
    return RateLimitSnapshot(
        plan_type="plus",
        primary=RateLimitWindowSnapshot(
            used_percent=12.0, window_minutes=300, resets_at=1780534461
        ),
        secondary=RateLimitWindowSnapshot(
            used_percent=8.0, window_minutes=10080, resets_at=1780864628
        ),
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
