"""Flag-support probe: parsing, fail-open, missing-flag diagnostics."""

from __future__ import annotations

import pytest
from pontonier.core.runtime import CommandRun

from codex_in_claude import cli_contract, preflight

_HELP = """
Run Codex non-interactively
  --json
  --sandbox <SANDBOX_MODE>
  --cd <DIR>
  --output-last-message <FILE>
  --ephemeral
  --ignore-user-config
  --ignore-rules
  --add-dir <DIR>
  --skip-git-repo-check
  --output-schema <FILE>
  --disable <FEATURE>
  --strict-config
  -m, --model <MODEL>
"""


def _patch_help(monkeypatch, text: str | None):
    def fake(cmd, timeout_seconds):
        if text is None:
            return CommandRun("", preflight.runtime.BINARY_NOT_FOUND, 127, 1, False)
        return CommandRun(text, "", 0, 1, False)

    monkeypatch.setattr(preflight.runtime, "run_sync_capture", fake)


def test_flag_support_parses(monkeypatch):
    _patch_help(monkeypatch, _HELP)
    fs = preflight.flag_support(force=True)
    assert fs.help_parsed
    assert "--model" in fs.supported
    assert "--sandbox" in fs.supported


def test_is_supported_present(monkeypatch):
    _patch_help(monkeypatch, _HELP)
    fs = preflight.flag_support(force=True)
    assert preflight.is_supported("--model", fs)


def test_is_supported_fail_open_when_probe_fails(monkeypatch):
    _patch_help(monkeypatch, None)
    fs = preflight.flag_support(force=True)
    assert not fs.help_parsed
    # Fail open: unknown flags treated as supported.
    assert preflight.is_supported("--anything", fs)


def test_missing_expected_flags_none_when_all_present(monkeypatch):
    _patch_help(monkeypatch, _HELP)
    fs = preflight.flag_support(force=True)
    assert preflight.missing_expected_flags(fs) == []


def test_missing_expected_flags_detects_gap(monkeypatch):
    _patch_help(monkeypatch, "Run Codex\n  --json\n  --cd <DIR>\n")
    fs = preflight.flag_support(force=True)
    missing = preflight.missing_expected_flags(fs)
    assert "--sandbox" in missing
    assert all(f in cli_contract.ALWAYS_SEND_FLAGS for f in missing)


def test_missing_expected_flags_empty_on_failed_probe(monkeypatch):
    _patch_help(monkeypatch, None)
    fs = preflight.flag_support(force=True)
    assert preflight.missing_expected_flags(fs) == []


# --- _probe_help never raises, even when binpath.codex_bin() does (B4-adjacent) -


def test_probe_help_returns_empty_string_on_bad_codex_bin_override(clean_env, tmp_path):
    """`_probe_help()`'s own docstring guarantees "" on any failure, but it called
    `binpath.codex_bin()` unguarded -- a bad CODEX_IN_CLAUDE_CODEX_BIN override
    (a path that doesn't exist) raised BinaryNotFoundError instead of returning ""."""
    from codex_in_claude import binpath

    missing = tmp_path / "does-not-exist"
    clean_env.setenv(binpath.ENV_VAR, str(missing))
    try:
        result = preflight._probe_help()
    except binpath.BinaryNotFoundError as exc:
        pytest.fail(f"_probe_help() must never raise, raised {exc!r}")
    assert result == ""


def test_flag_support_fails_open_on_bad_codex_bin_override(clean_env, tmp_path):
    """The observable, documented behavior at the level callers actually use:
    a probe failure -- of any kind -- must fail open (help_parsed=False), never
    raise out of flag_support()."""
    from codex_in_claude import binpath

    missing = tmp_path / "does-not-exist"
    clean_env.setenv(binpath.ENV_VAR, str(missing))
    try:
        fs = preflight.flag_support(force=True)
    except binpath.BinaryNotFoundError as exc:
        pytest.fail(f"flag_support() must never raise, raised {exc!r}")
    assert fs.help_parsed is False
    assert preflight.missing_expected_flags(fs) == []


def test_cache_reused(monkeypatch):
    calls = {"n": 0}

    def fake(cmd, timeout_seconds):
        calls["n"] += 1
        return CommandRun(_HELP, "", 0, 1, False)

    # Pin binary resolution so a machine with no real `codex` install doesn't fall
    # through to the npm-probe candidate, which shares this same `runtime.run_sync_capture`
    # seam and would otherwise inflate the call count this test is asserting on.
    monkeypatch.setattr(preflight.binpath, "codex_bin", lambda: "codex")
    monkeypatch.setattr(preflight.runtime, "run_sync_capture", fake)
    preflight.reset_cache()
    preflight.flag_support()
    preflight.flag_support()
    assert calls["n"] == 1  # second call served from cache
