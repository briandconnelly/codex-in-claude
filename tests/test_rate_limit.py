from pathlib import Path

from codex_in_claude import rate_limit
from codex_in_claude.schemas import RateLimitSnapshot, RateLimitWindowSnapshot


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
