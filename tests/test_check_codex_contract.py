"""Exit-code contract for scripts/check_codex_contract.py.

The script lives under scripts/ (not the package), so coverage doesn't track it;
these tests pin its 0/1/2 behavior directly. It is loaded by path and its single
external dependency — runtime.run_sync_capture — is monkeypatched per probe."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from codex_in_claude._core.runtime import BINARY_NOT_FOUND, TIMED_OUT, CommandRun

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_codex_contract.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_codex_contract", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A help blob that satisfies the whole contract: every ALWAYS_SEND flag, the
# HELP_GATED --model, and the three sandbox values in a possible-values list.
FULL_HELP = """
Run Codex non-interactively
  --json
  -s, --sandbox <SANDBOX_MODE>  [possible values: read-only, workspace-write, danger-full-access]
  -C, --cd <DIR>
  -o, --output-last-message <FILE>
  --ephemeral
  --ignore-user-config
  --ignore-rules
  --add-dir <DIR>
  --skip-git-repo-check
  --output-schema <FILE>
  -m, --model <MODEL>
"""

VERSION = "codex-cli 0.141.0"


def _ok(stdout: str) -> CommandRun:
    return CommandRun(stdout, "", 0, 1, False)


def _patch_probes(monkeypatch, *, version: CommandRun, help_run: CommandRun):
    """Route run_sync_capture to a version- or help-specific CommandRun by argv."""
    check = _load_script()

    def fake(cmd, timeout_seconds, **kwargs):
        return version if "--version" in cmd else help_run

    monkeypatch.setattr(check.runtime, "run_sync_capture", fake)
    return check


def test_success_returns_0(monkeypatch):
    check = _patch_probes(monkeypatch, version=_ok(VERSION), help_run=_ok(FULL_HELP))
    assert check.main() == 0


def test_missing_always_send_flag_returns_1(monkeypatch):
    help_text = FULL_HELP.replace("  --output-schema <FILE>\n", "")
    check = _patch_probes(monkeypatch, version=_ok(VERSION), help_run=_ok(help_text))
    assert check.main() == 1


def test_missing_sandbox_value_returns_1(monkeypatch):
    help_text = FULL_HELP.replace("danger-full-access", "")
    check = _patch_probes(monkeypatch, version=_ok(VERSION), help_run=_ok(help_text))
    assert check.main() == 1


def test_untracked_version_warns_but_returns_0(monkeypatch):
    check = _patch_probes(monkeypatch, version=_ok("codex-cli 0.999.0"), help_run=_ok(FULL_HELP))
    assert check.main() == 0


def test_binary_missing_returns_2(monkeypatch):
    missing = CommandRun("", BINARY_NOT_FOUND, 127, 1, False)
    check = _patch_probes(monkeypatch, version=missing, help_run=missing)
    assert check.main() == 2


def test_empty_help_returns_2(monkeypatch):
    check = _patch_probes(monkeypatch, version=_ok(VERSION), help_run=_ok(""))
    assert check.main() == 2


def test_help_timeout_returns_2_not_drift(monkeypatch, capsys):
    """Regression: a hung help probe is a probe failure (exit 2), not drift (exit 1)."""
    timed_out = CommandRun("", TIMED_OUT, -9, 1, True)
    check = _patch_probes(monkeypatch, version=_ok(VERSION), help_run=timed_out)
    assert check.main() == 2
    assert "could not probe" in capsys.readouterr().out


def test_version_timeout_returns_2(monkeypatch):
    timed_out = CommandRun("", TIMED_OUT, -9, 1, True)
    check = _patch_probes(monkeypatch, version=timed_out, help_run=_ok(FULL_HELP))
    assert check.main() == 2
