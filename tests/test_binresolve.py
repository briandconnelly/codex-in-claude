"""Ordered candidate-directory resolver for the `codex` CLI binary (#3).

Covers `binresolve.resolve_codex_bin()`: the WSL2-npm-shim workaround that
probes `$HOME/.local/bin`, `/usr/local/bin`, then the npm global bin dir
(derived from `npm prefix -g` + `"bin"` — `npm bin -g` was removed in npm 9)
(in that order, first hit wins), falling back to `shutil.which("codex")`, and
finally `None` when nothing is found. No candidate probe may ever raise — a
missing/failing `npm` must be treated as "no candidate from this source," not
a crash.

The three candidate-directory probes (home, usr-local, npm) only run when
`binresolve._running_under_wsl2_interop()` reports True. When it reports
False (e.g. native macOS/Linux, no WSL2 interop), `resolve_codex_bin()` skips
straight to `shutil.which("codex")` — a reviewer reproduced the unconditional
probe shadowing a newer Homebrew `codex` on macOS with an older one left
behind in `~/.local/bin`.

Every test isolates the module's external inputs so a real `codex` install on
the machine running these tests can never leak in and mask a bug:
  - `binresolve._running_under_wsl2_interop` (monkeypatched True by default in
    the `isolated` fixture, so existing candidate-order tests keep exercising
    the WSL2-active path; gating itself is tested separately below)
  - `$HOME` (monkeypatched to an empty temp dir)
  - `binresolve.USR_LOCAL_BIN` (the module-level constant standing in for the real
    `/usr/local/bin`, monkeypatched to a separate empty temp dir — the real path is
    never touched)
  - `binresolve.runtime.run_sync_capture` (the `npm prefix -g` subprocess seam, same
    monkeypatch pattern `preflight` uses for its own `run_sync_capture` probe)
  - `binresolve.shutil.which` (the final PATH fallback)
  - `$WSL_DISTRO_NAME` and `binresolve.PROC_VERSION_PATH` (the two signals
    `_running_under_wsl2_interop()` itself reads -- exercised directly, unmocked,
    in the WSL2-interop detection section below)
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
    winning earlier candidate short-circuits the later ones.

    Also pins `_running_under_wsl2_interop()` to True (`raising=False`, since the
    predicate does not exist on the pre-fix module) so the many candidate-order
    tests below keep exercising "under WSL2" behavior regardless of whether the
    gating fix has landed yet. The gating behavior itself (predicate True vs.
    False) is asserted directly in the WSL2 gating section below."""
    monkeypatch.setattr(binresolve, "_running_under_wsl2_interop", lambda: True, raising=False)

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


# --- WSL2-interop detection itself (B2) --------------------------------------
#
# `_running_under_wsl2_interop()`'s own verdict, NOT monkeypatched away, unlike
# every other test in this file. Pins the detection mechanism this contract
# requires (a test-implementer choice -- the spec left the exact mechanism to
# the implementer, but a monkeypatchable unit needs *some* concrete seam to
# test against): the predicate reads EXACTLY these two sources, and nothing
# else (no `platform.release()`, `os.uname()`, or `platform.uname().version` --
# on a real WSL2 dev machine those all also carry a "microsoft" signature, so
# adding one would make the "False" tests below fail locally while still
# passing on non-WSL2 CI runners, the worst kind of green-CI/red-local gap):
#   - a `WSL_DISTRO_NAME` env var (any non-empty value) -> True
#   - OR `binresolve.PROC_VERSION_PATH` (mirroring the `USR_LOCAL_BIN`
#     stand-in above) containing "microsoft" case-insensitively -- the
#     standard WSL2 kernel-version signature
# Neither signal present, or the file missing/unreadable (e.g. no `/proc` at
# all on macOS), means False. Returns a real `bool` (not a truthy env string).
# Never raises.


def test_wsl2_interop_true_when_wsl_distro_name_env_set(monkeypatch, tmp_path):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    missing_proc = tmp_path / "no-proc-version-here"
    monkeypatch.setattr(binresolve, "PROC_VERSION_PATH", missing_proc, raising=False)
    assert binresolve._running_under_wsl2_interop() is True


def test_wsl2_interop_true_when_proc_version_mentions_microsoft(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    proc_version = tmp_path / "proc_version"
    proc_version.write_text("Linux version 5.15.90.1-microsoft-standard-WSL2\n")
    monkeypatch.setattr(binresolve, "PROC_VERSION_PATH", proc_version, raising=False)
    assert binresolve._running_under_wsl2_interop() is True


def test_wsl2_interop_true_when_proc_version_mentions_microsoft_any_case(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    proc_version = tmp_path / "proc_version"
    proc_version.write_text("Linux version 5.15.90.1-Microsoft-standard-WSL2\n")
    monkeypatch.setattr(binresolve, "PROC_VERSION_PATH", proc_version, raising=False)
    assert binresolve._running_under_wsl2_interop() is True


def test_wsl2_interop_false_on_plain_linux_proc_version_and_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    proc_version = tmp_path / "proc_version"
    proc_version.write_text("Linux version 6.8.0-generic (buildd@lcy02)\n")
    monkeypatch.setattr(binresolve, "PROC_VERSION_PATH", proc_version, raising=False)
    assert binresolve._running_under_wsl2_interop() is False


def test_wsl2_interop_false_when_proc_version_path_absent(monkeypatch, tmp_path):
    """The macOS case: no `/proc` filesystem at all, and no WSL env var."""
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    missing_proc = tmp_path / "no-proc-version-here"
    monkeypatch.setattr(binresolve, "PROC_VERSION_PATH", missing_proc, raising=False)
    assert binresolve._running_under_wsl2_interop() is False


def test_wsl2_interop_false_when_proc_version_unreadable_does_not_raise(monkeypatch, tmp_path):
    """A path that exists but can't be read as text (e.g. a directory) must
    degrade to False, matching every other probe's never-raise contract."""
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    a_directory = tmp_path / "proc_version_is_a_dir"
    a_directory.mkdir()
    monkeypatch.setattr(binresolve, "PROC_VERSION_PATH", a_directory, raising=False)
    try:
        result = binresolve._running_under_wsl2_interop()
    except Exception as exc:  # the assertion IS "never raises" -- catch broadly on purpose
        pytest.fail(f"_running_under_wsl2_interop() must never raise, raised {exc!r}")
    assert result is False


def test_wsl2_interop_env_var_wins_even_with_a_plain_linux_proc_version(monkeypatch, tmp_path):
    """Either signal is sufficient -- the env var alone must be enough, even
    when /proc/version itself carries no WSL signature."""
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    proc_version = tmp_path / "proc_version"
    proc_version.write_text("Linux version 6.8.0-generic (buildd@lcy02)\n")
    monkeypatch.setattr(binresolve, "PROC_VERSION_PATH", proc_version, raising=False)
    assert binresolve._running_under_wsl2_interop() is True


# --- WSL2-interop gating (B2): candidate probes only run under WSL2 ----------
#
# A reviewer reproduced the unconditional candidate probe shadowing a newer
# Homebrew `codex` on macOS with a stale one in `~/.local/bin`. The fix gates
# the three candidate-directory probes behind `_running_under_wsl2_interop()`:
# False -> skip straight to `shutil.which`; True -> today's probe order.


def test_not_under_wsl2_skips_every_candidate_probe_and_uses_which_directly(isolated, monkeypatch):
    """False must bypass $HOME/.local/bin, /usr/local/bin, AND the npm probe --
    even though a winning candidate exists in every one of those locations --
    and resolve via `shutil.which` alone."""
    monkeypatch.setattr(binresolve, "_running_under_wsl2_interop", lambda: False, raising=False)
    _make_executable(isolated.home / ".local" / "bin" / "codex")
    _make_executable(isolated.usr_local / "codex")

    which_calls: list[str] = []

    def fake_which(name):
        which_calls.append(name)
        return "/opt/homebrew/bin/codex"

    monkeypatch.setattr(binresolve.shutil, "which", fake_which)

    assert binresolve.resolve_codex_bin() == "/opt/homebrew/bin/codex"
    assert isolated.npm_calls == []
    assert which_calls == ["codex"]


def test_not_under_wsl2_returns_none_when_which_also_misses(isolated, monkeypatch):
    monkeypatch.setattr(binresolve, "_running_under_wsl2_interop", lambda: False, raising=False)
    _make_executable(isolated.home / ".local" / "bin" / "codex")
    assert binresolve.resolve_codex_bin() is None
    assert isolated.npm_calls == []


def test_under_wsl2_preserves_todays_first_hit_wins_candidate_order(isolated, monkeypatch):
    """True must not change existing behavior: the candidate-directory probes
    still run, in the same order, before falling back to `shutil.which`."""
    monkeypatch.setattr(binresolve, "_running_under_wsl2_interop", lambda: True, raising=False)
    home_codex = _make_executable(isolated.home / ".local" / "bin" / "codex")
    _make_executable(isolated.usr_local / "codex")

    assert binresolve.resolve_codex_bin() == str(home_codex)
    assert isolated.npm_calls == []
    assert isolated.which_calls == []


def test_wsl2_gate_never_raises_regardless_of_verdict(isolated, monkeypatch):
    for verdict in (True, False):
        monkeypatch.setattr(
            binresolve, "_running_under_wsl2_interop", lambda v=verdict: v, raising=False
        )
        try:
            binresolve.resolve_codex_bin()
        except Exception as exc:  # the assertion IS "never raises" -- catch broadly on purpose
            pytest.fail(f"resolve_codex_bin() must never raise, raised {exc!r}")


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


def test_npm_prefix_g_candidate_wins_when_earlier_candidates_absent(
    isolated, tmp_path, monkeypatch
):
    """`npm bin -g` was removed in npm 9; the probe now runs `npm prefix -g` and
    derives the global bin dir as `<prefix>/bin` (npm's own `globalBin` logic on
    POSIX -- this probe only ever runs under WSL2, a POSIX environment)."""
    npm_prefix = tmp_path / "npm-global-prefix"
    npm_codex = _make_executable(npm_prefix / "bin" / "codex")

    # Recorded (not asserted inline): `_npm_global_bin_candidate()` wraps this call in a
    # broad `except Exception`, which would silently swallow an inline AssertionError and
    # report a confusing "None != <path>" failure instead of naming the actual bug (a
    # trailing check the implementer needs to read off THIS assertion).
    npm_argv: list[list[str]] = []

    def fake_npm(cmd, *args, **kwargs):
        npm_argv.append(list(cmd))
        return CommandRun(f"{npm_prefix}\n", "", 0, 5, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    result = binresolve.resolve_codex_bin()
    assert npm_argv == [["npm", "prefix", "-g"]]
    assert result == str(npm_codex)
    assert isolated.which_calls == []


def test_usr_local_bin_wins_over_npm_when_both_present(isolated, tmp_path, monkeypatch):
    usr_codex = _make_executable(isolated.usr_local / "codex")
    npm_prefix = tmp_path / "npm-global-prefix"
    _make_executable(npm_prefix / "bin" / "codex")

    def fake_npm(cmd, *args, **kwargs):
        return CommandRun(f"{npm_prefix}\n", "", 0, 5, False)

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
    npm_prefix = tmp_path / "npm-global-prefix"
    _make_executable(npm_prefix / "bin" / "codex")

    def fake_npm(cmd, *args, **kwargs):
        return CommandRun(f"{npm_prefix}\n", "", 0, 5, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    binresolve.resolve_codex_bin()
    assert isolated.which_calls == []


def test_returns_none_when_which_also_misses(isolated):
    assert binresolve.resolve_codex_bin() is None


# --- npm prefix -g failure handling: must never crash the resolver -------------


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


def test_npm_prefix_g_empty_output_does_not_crash(isolated, monkeypatch):
    def fake_npm(cmd, *args, **kwargs):
        return CommandRun("\n", "", 0, 1, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    assert binresolve.resolve_codex_bin() is None


def test_npm_prefix_g_dir_without_bin_codex_falls_through_to_which(isolated, tmp_path, monkeypatch):
    """The reported prefix exists but has no `bin/codex` beneath it (e.g. no
    global packages installed yet) -- must fall through, not crash."""
    npm_prefix = tmp_path / "npm-empty-prefix"
    npm_prefix.mkdir()

    def fake_npm(cmd, *args, **kwargs):
        return CommandRun(f"{npm_prefix}\n", "", 0, 1, False)

    monkeypatch.setattr(binresolve.runtime, "run_sync_capture", fake_npm)
    monkeypatch.setattr(binresolve.shutil, "which", lambda name: "/fallback/codex")
    assert binresolve.resolve_codex_bin() == "/fallback/codex"


# --- $HOME edge case: an unset HOME must not crash the probe -------------------


def test_home_unset_does_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(binresolve, "_running_under_wsl2_interop", lambda: True, raising=False)
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
