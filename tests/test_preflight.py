"""Flag-support probe: parsing, fail-open, missing-flag diagnostics."""

from __future__ import annotations

from codex_in_claude import cli_contract, preflight
from codex_in_claude._core.runtime import CommandRun

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


def test_cache_reused(monkeypatch):
    calls = {"n": 0}

    def fake(cmd, timeout_seconds):
        calls["n"] += 1
        return CommandRun(_HELP, "", 0, 1, False)

    monkeypatch.setattr(preflight.runtime, "run_sync_capture", fake)
    preflight.reset_cache()
    preflight.flag_support()
    preflight.flag_support()
    assert calls["n"] == 1  # second call served from cache
