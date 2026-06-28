"""Config defaults, clamps, env handling, and flag mappings."""

from __future__ import annotations

import pytest

from codex_in_claude import config


def test_job_store_configures_worktree_cleanup(clean_env):
    import tempfile
    from pathlib import Path

    from codex_in_claude._core import worktree

    store = config.job_store()
    # The store may clean up only the throwaway-worktree temp area.
    assert store.cleanup_root == Path(tempfile.gettempdir())
    assert store.cleanup_prefix == worktree.WORKTREE_PREFIX


def test_defaults_builtin(clean_env):
    d = config.defaults()
    assert d.tier == "consult"
    assert d.sandbox == "read-only"
    assert d.isolation == "inherit"
    assert d.model is None
    assert d.timeout_seconds == config.DEFAULT_TIMEOUT_SECONDS


def test_defaults_env_overrides(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_TIER_DEFAULT", "propose")
    clean_env.setenv("CODEX_IN_CLAUDE_MODEL", "gpt-5.4")
    clean_env.setenv("CODEX_IN_CLAUDE_TIMEOUT_SECONDS", "42")
    d = config.defaults()
    assert d.tier == "propose"
    assert d.sandbox == "workspace-write"  # tier default
    assert d.model == "gpt-5.4"
    assert d.timeout_seconds == 42


def test_invalid_tier_falls_back(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_TIER_DEFAULT", "nonsense")
    assert config.defaults().tier == "consult"


def test_sandbox_default_override_validated(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_SANDBOX_DEFAULT", "bogus")
    # invalid override -> falls back to the tier's sandbox
    assert config.defaults().sandbox == "read-only"


def test_clamp_timeout():
    assert config.clamp_timeout(1) == config.MIN_TIMEOUT_SECONDS
    assert config.clamp_timeout(99999) == config.MAX_TIMEOUT_SECONDS
    assert config.clamp_timeout(120) == 120


@pytest.mark.parametrize(
    "iso,expected",
    [
        ("inherit", []),
        ("ignore-config", ["--ignore-user-config"]),
        ("ignore-rules", ["--ignore-user-config", "--ignore-rules"]),
    ],
)
def test_isolation_flags(iso, expected):
    assert config.isolation_flags(iso) == expected


def test_isolation_flags_invalid():
    with pytest.raises(ValueError, match="unsupported isolation"):
        config.isolation_flags("nope")


def test_sandbox_for_tier():
    assert config.sandbox_for_tier("consult") == "read-only"
    assert config.sandbox_for_tier("propose") == "workspace-write"
    assert config.sandbox_for_tier("apply") == "workspace-write"


@pytest.mark.parametrize(
    "value,expected",
    [("${FOO}", True), ("${FOO_BAR2}", True), ("plain", False), ("${}", False), (None, False)],
)
def test_is_env_placeholder(value, expected):
    assert config.is_env_placeholder(value) is expected


def test_placeholder_env_vars(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    clean_env.setenv("CODEX_IN_CLAUDE_TIMEOUT_SECONDS", "60")
    assert config.placeholder_env_vars() == ["CODEX_IN_CLAUDE_MODEL"]


@pytest.mark.parametrize(
    "version,expected",
    [("codex-cli 0.142.0", True), ("codex-cli 0.999.0", False), ("garbage", None), (None, None)],
)
def test_version_supported(version, expected, clean_env):
    assert config.version_supported(version) is expected


def test_supported_versions_env_override(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_SUPPORTED_VERSIONS", "0.999")
    assert config.version_supported("codex-cli 0.999.3") is True
    assert config.version_supported("codex-cli 0.142.0") is False


def test_supported_versions_bad_env_falls_back(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_SUPPORTED_VERSIONS", "garbage")
    assert config.version_supported("codex-cli 0.142.0") is True


def test_state_dir_default(clean_env, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    p = config.state_dir()
    assert p.name == "jobs"
    assert "codex-in-claude" in str(p)


def test_state_dir_override(clean_env, tmp_path):
    clean_env.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "jobs"))
    assert config.state_dir() == tmp_path / "jobs"


def test_max_input_bytes_floor(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "5")
    assert config.max_input_bytes() == 1_000


def test_max_delegate_diff_bytes_default(clean_env):
    assert config.max_delegate_diff_bytes() == config.DEFAULT_MAX_DELEGATE_DIFF_BYTES


def test_max_delegate_diff_bytes_override(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "12345")
    assert config.max_delegate_diff_bytes() == 12345


def test_max_delegate_diff_bytes_invalid_falls_back(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "notanint")
    assert config.max_delegate_diff_bytes() == config.DEFAULT_MAX_DELEGATE_DIFF_BYTES


def test_max_delegate_diff_bytes_floor(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "5")
    assert config.max_delegate_diff_bytes() == 1_000


def test_job_defaults(clean_env):
    assert config.job_ttl_seconds() == config.DEFAULT_JOB_TTL_SECONDS
    assert config.job_max_seconds() == config.DEFAULT_JOB_MAX_SECONDS
    assert config.job_max_count() == config.DEFAULT_JOB_MAX_COUNT


def test_job_knobs_clamp_low(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_TTL", "10")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_SECONDS", "5")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_COUNT", "0")
    assert config.job_ttl_seconds() == 60
    assert config.job_max_seconds() == 60
    assert config.job_max_count() == 1


def test_job_knobs_clamp_high(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_SECONDS", "999999")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_COUNT", "999999")
    assert config.job_max_seconds() == 7_200
    assert config.job_max_count() == 1_000


def test_job_knobs_env_override(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_TTL", "3600")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_SECONDS", "600")
    clean_env.setenv("CODEX_IN_CLAUDE_JOB_MAX_COUNT", "10")
    assert config.job_ttl_seconds() == 3600
    assert config.job_max_seconds() == 600
    assert config.job_max_count() == 10


def test_max_output_bytes_default(monkeypatch):
    monkeypatch.delenv("CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES", raising=False)
    assert config.max_output_bytes() == 10 * 1024 * 1024


def test_max_output_bytes_env_override(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES", "500000")
    assert config.max_output_bytes() == 500_000


def test_max_output_bytes_floor(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES", "10")
    assert config.max_output_bytes() == 64 * 1024


def test_max_output_bytes_bad_value(monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_OUTPUT_BYTES", "notanint")
    assert config.max_output_bytes() == 10 * 1024 * 1024
