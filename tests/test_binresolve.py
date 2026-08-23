"""Ordered candidate-directory resolver for the `codex` CLI binary (#3).

Covers `binresolve.resolve_codex_bin()`: the WSL2-npm-shim workaround that
probes `$HOME/.local/bin`, `/usr/local/bin`, then `npm bin -g` (in that order,
first hit wins), falling back to `shutil.which("codex")`, and finally `None` when
nothing is found. No candidate probe may ever raise — a missing/failing `npm`
must be treated as "no candidate from this source," not a crash.

Every test isolates the module's three external inputs so a real `codex` install
on the machine running these tests can never leak in and mask a bug:
  - `$HOME` (monkeypatched to an empty temp dir)
  - `binresolve.USR_LOCAL_BIN` (the module-level constant standing in for the real
    `/usr/local/bin`, monkeypatched to a separate empty temp dir — the real path is
    never touched)
  - `binresolve.runtime.run_sync_capture` (the `npm bin -g` subprocess seam, same
    monkeypatch pattern `preflight` uses for its own `run_sync_capture` probe)
  - `binresolve.shutil.which` (the final PATH fallback)
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest
from pontonier.core.runtime import BINARY_NOT_FOUND, CommandRun

from codex_in_claude import binresolve


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho codex\n")
    path.chmod(0o755)
    return path


def _make_non_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a real binary\n")
    path.chmod(0o644)
    return path


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Baseline where every candidate source misses, so an individual test can
    layer in exactly the one candidate it means to test. Also tracks whether the
    npm probe and the `which` fallback were consulted, so tests can assert a
    winning earlier candidate short-circuits the later ones."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    usr_local = tmp_path / "usr_local_bin"
    usr_local.mkdir()
    monkeypatch.setattr(binresolve, "USR_LOCAL_BIN", usr_local)

    npm_calls: list[list[str]] = []

    def fake_npm(cmd, *args, **kwargs):
        npm_calls.append(list(cmd))
        return CommandRun("", "npm: command not found", 127, 1, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)

    which_calls: list[str] = []

    def fake_which(name):
        which_calls.append(name)

    monkeypatch.setattr(binresolve.shutil, "which", fake_which)

    return types.SimpleNamespace(
        home=home,
        usr_local=usr_local,
        npm_calls=npm_calls,
        which_calls=which_calls,
    )


# --- nothing found -----------------------------------------------------------


def test_returns_none_when_no_candidate_and_which_also_misses(isolated):
    assert binresolve.resolve_codex_bin() is None


def test_returns_none_does_not_raise(isolated):
    # Explicit non-raise contract: callers decide how to fail (per spec).
    try:
        result = binresolve.resolve_codex_bin()
    except Exception as exc:  # the assertion IS "never raises" -- catch broadly on purpose
        pytest.fail(f"resolve_codex_bin() must never raise, raised {exc!r}")
    assert result is None


# --- candidate order: each position wins when earlier ones are absent --------


def test_home_local_bin_candidate_wins_when_present(isolated):
    codex_path = _make_executable(isolated.home / ".local" / "bin" / "codex")
    result = binresolve.resolve_codex_bin()
    assert result == str(codex_path)
    assert isinstance(result, str)
    # Short-circuited: neither later source was consulted.
    assert isolated.npm_calls == []
    assert isolated.which_calls == []


def test_usr_local_bin_candidate_wins_when_home_candidate_absent(isolated):
    codex_path = _make_executable(isolated.usr_local / "codex")
    result = binresolve.resolve_codex_bin()
    assert result == str(codex_path)
    assert isolated.npm_calls == []
    assert isolated.which_calls == []


def test_home_local_bin_wins_over_usr_local_bin_when_both_present(isolated):
    home_codex = _make_executable(isolated.home / ".local" / "bin" / "codex")
    _make_executable(isolated.usr_local / "codex")
    assert binresolve.resolve_codex_bin() == str(home_codex)


def test_npm_bin_g_candidate_wins_when_earlier_candidates_absent(isolated, tmp_path, monkeypatch):
    npm_dir = tmp_path / "npm-global-bin"
    npm_codex = _make_executable(npm_dir / "codex")

    def fake_npm(cmd, *args, **kwargs):
        assert list(cmd)[:3] == ["npm", "bin", "-g"]
        return CommandRun(f"{npm_dir}\n", "", 0, 5, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    assert binresolve.resolve_codex_bin() == str(npm_codex)
    assert isolated.which_calls == []


def test_usr_local_bin_wins_over_npm_when_both_present(isolated, tmp_path, monkeypatch):
    usr_codex = _make_executable(isolated.usr_local / "codex")
    npm_dir = tmp_path / "npm-global-bin"
    _make_executable(npm_dir / "codex")

    def fake_npm(cmd, *args, **kwargs):
        return CommandRun(f"{npm_dir}\n", "", 0, 5, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    assert binresolve.resolve_codex_bin() == str(usr_codex)


# --- executable check ---------------------------------------------------------


def test_home_candidate_present_but_not_executable_falls_through(isolated):
    _make_non_executable(isolated.home / ".local" / "bin" / "codex")
    usr_codex = _make_executable(isolated.usr_local / "codex")
    assert binresolve.resolve_codex_bin() == str(usr_codex)


# --- shutil.which fallback -----------------------------------------------------


def test_which_fallback_used_when_no_candidate_dir_or_npm_hit(isolated, monkeypatch):
    monkeypatch.setattr(binresolve.shutil, "which", lambda name: "/usr/bin/codex")
    assert binresolve.resolve_codex_bin() == "/usr/bin/codex"


def test_which_fallback_fires_only_after_candidates_are_exhausted(isolated, monkeypatch):
    calls: list[str] = []

    def fake_which(name):
        calls.append(name)
        return "/usr/bin/codex"

    monkeypatch.setattr(binresolve.shutil, "which", fake_which)
    binresolve.resolve_codex_bin()
    # which() is consulted (candidates exhausted), and asked for "codex".
    assert calls == ["codex"]


def test_which_not_consulted_when_a_candidate_directory_wins(isolated):
    _make_executable(isolated.home / ".local" / "bin" / "codex")
    binresolve.resolve_codex_bin()
    assert isolated.which_calls == []


def test_which_not_consulted_when_npm_candidate_wins(isolated, tmp_path, monkeypatch):
    npm_dir = tmp_path / "npm-global-bin"
    _make_executable(npm_dir / "codex")

    def fake_npm(cmd, *args, **kwargs):
        return CommandRun(f"{npm_dir}\n", "", 0, 5, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    binresolve.resolve_codex_bin()
    assert isolated.which_calls == []


def test_returns_none_when_which_also_misses(isolated):
    assert binresolve.resolve_codex_bin() is None


# --- npm bin -g failure handling: must never crash the resolver ----------------


def test_npm_nonzero_exit_does_not_crash_and_falls_through_to_which(isolated, monkeypatch):
    def fake_npm(cmd, *args, **kwargs):
        return CommandRun("", "npm ERR! could not determine executable to run", 1, 5, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    monkeypatch.setattr(binresolve.shutil, "which", lambda name: "/opt/homebrew/bin/codex")
    assert binresolve.resolve_codex_bin() == "/opt/homebrew/bin/codex"


def test_npm_not_installed_does_not_crash_and_falls_through_to_which(isolated, monkeypatch):
    def fake_npm(cmd, *args, **kwargs):
        return CommandRun("", BINARY_NOT_FOUND, 127, 1, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    monkeypatch.setattr(binresolve.shutil, "which", lambda name: "/opt/homebrew/bin/codex")
    assert binresolve.resolve_codex_bin() == "/opt/homebrew/bin/codex"


def test_npm_bin_g_empty_output_does_not_crash(isolated, monkeypatch):
    def fake_npm(cmd, *args, **kwargs):
        return CommandRun("\n", "", 0, 1, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    assert binresolve.resolve_codex_bin() is None


def test_npm_bin_g_dir_without_codex_falls_through_to_which(isolated, tmp_path, monkeypatch):
    npm_dir = tmp_path / "npm-empty"
    npm_dir.mkdir()

    def fake_npm(cmd, *args, **kwargs):
        return CommandRun(f"{npm_dir}\n", "", 0, 1, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    monkeypatch.setattr(binresolve.shutil, "which", lambda name: "/fallback/codex")
    assert binresolve.resolve_codex_bin() == "/fallback/codex"


# --- $HOME edge case: an unset HOME must not crash the probe -------------------


def test_home_unset_does_not_crash(monkeypatch, tmp_path):
    monkeypatch.delenv("HOME", raising=False)
    usr_local = tmp_path / "usr_local_bin"
    usr_local.mkdir()
    monkeypatch.setattr(binresolve, "USR_LOCAL_BIN", usr_local)
    monkeypatch.setattr(
        binresolve.runtime,
        "run_sync_capture",
        lambda *a, **k: CommandRun("", "npm: command not found", 127, 1, False),
    )
    monkeypatch.setattr(binresolve.shutil, "which", lambda name: None)
    assert binresolve.resolve_codex_bin() is None
