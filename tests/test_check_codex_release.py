"""Behavior contract for scripts/check_codex_release.py.

The script lives under scripts/ (not the package), so coverage doesn't track it;
these tests pin its decision logic and exit codes directly. It compares a
candidate `codex` version (fetched from npm by the workflow) against the
structured `cli_contract.SUPPORTED_VERSIONS` and reports whether a new minor is
available — the trigger for the docs/UPGRADING-CODEX.md procedure."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_codex_release.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_codex_release", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_version_strips_prefixes():
    mod = _load_script()
    assert mod.parse_version("0.141.0") == (0, 141, 0)
    assert mod.parse_version("v0.142.3") == (0, 142, 3)
    assert mod.parse_version("codex-cli 0.143.1") == (0, 143, 1)


def test_newer_minor_is_flagged():
    mod = _load_script()
    result = mod.evaluate("0.142.0", tracked={(0, 141)})
    assert result["new"] is True
    assert result["latest_minor"] == "0.142"
    assert result["tracked"] == "0.141"


def test_same_minor_is_not_flagged():
    mod = _load_script()
    # A patch bump within the tracked minor is a softer "may refresh", not a new-minor trigger.
    assert mod.evaluate("0.141.7", tracked={(0, 141)})["new"] is False


def test_older_minor_is_not_flagged():
    mod = _load_script()
    assert mod.evaluate("0.140.0", tracked={(0, 141)})["new"] is False


def test_newer_major_is_flagged():
    mod = _load_script()
    assert mod.evaluate("1.0.0", tracked={(0, 141)})["new"] is True


def test_evaluate_uses_max_of_multiple_tracked_minors():
    mod = _load_script()
    assert mod.evaluate("0.142.0", tracked={(0, 140), (0, 141)})["new"] is True
    assert mod.evaluate("0.141.0", tracked={(0, 140), (0, 141)})["new"] is False


def test_main_writes_github_output(tmp_path, monkeypatch, capsys):
    mod = _load_script()
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    rc = mod.main(["--latest", "0.142.0", "--tracked", "0.141"])
    assert rc == 0
    written = out.read_text()
    assert "new=true" in written
    assert "latest_minor=0.142" in written


def test_main_no_new_release_reports_false(tmp_path, monkeypatch):
    mod = _load_script()
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    rc = mod.main(["--latest", "0.141.0", "--tracked", "0.141"])
    assert rc == 0
    assert "new=false" in out.read_text()


def test_main_malformed_version_exits_2(capsys):
    mod = _load_script()
    rc = mod.main(["--latest", "not-a-version", "--tracked", "0.141"])
    assert rc == 2


def test_main_defaults_tracked_to_contract():
    # With no --tracked, the script reads cli_contract.SUPPORTED_VERSIONS itself.
    mod = _load_script()
    rc = mod.main(["--latest", "0.0.1"])
    assert rc == 0  # an ancient version is simply "not new"
