"""Ordered candidate-directory resolver for the `codex` CLI binary (#3).

Under WSL2, every `codex` subprocess spawn used the bare literal `"codex"`.
WSL2's PATH interop forwards the Windows `PATH` into the WSL `PATH`, so a
bare-name lookup can resolve to a Windows-side npm-global `codex` shim
instead of the WSL-native install, which then fails confusingly instead of
cleanly.

`resolve_codex_bin()` probes, in order, first hit wins: `$HOME/.local/bin/codex`,
`USR_LOCAL_BIN/codex`, the npm global bin dir (derived from `npm prefix -g`,
since `npm bin -g` was removed in npm 9+), `shutil.which("codex")`, otherwise
`None`. Each directory candidate must exist AND be executable
(`os.access(path, os.X_OK)`); no probe step may ever raise -- a missing or
failing `$HOME`, `npm`, or `which` lookup just yields "no candidate from
this source." Callers decide how to handle a `None` result (see
`binpath.codex_bin()`).

The three candidate-directory probes (home, usr-local, npm) only run when
`_running_under_wsl2_interop()` reports True -- they exist to work around
WSL2's PATH interop shadowing the WSL-native binary, so on a plain
macOS/Linux host they must not run at all (a stale `~/.local/bin/codex`
there would otherwise shadow a newer, correctly-resolved `codex`). When
`_running_under_wsl2_interop()` reports False, resolution skips straight to
`shutil.which("codex")`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pontonier.core import runtime

# Stands in for the real `/usr/local/bin` so tests can monkeypatch it.
USR_LOCAL_BIN = Path("/usr/local/bin")

# Stands in for the real `/proc/version` so tests can monkeypatch it.
PROC_VERSION_PATH = Path("/proc/version")

# Cheap, local probe (mirrors preflight._probe_help's timeout budget).
_NPM_BIN_TIMEOUT_SECONDS = 5


def _is_executable_file(path: Path) -> bool:
    """Whether `path` exists as a file and is executable. Never raises."""
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _home_local_bin_candidate() -> Path | None:
    """`$HOME/.local/bin/codex`, or None if `$HOME` is unset."""
    home = os.environ.get("HOME")
    if not home:
        return None
    return Path(home) / ".local" / "bin" / "codex"


def _usr_local_bin_candidate() -> Path:
    """`USR_LOCAL_BIN/codex`, read live so tests' monkeypatch takes effect."""
    return USR_LOCAL_BIN / "codex"


def _running_under_wsl2_interop() -> bool:
    """Whether this process is likely running under WSL2 with Windows PATH
    interop enabled -- the scenario `resolve_codex_bin()`'s candidate-directory
    probes exist to work around. Reads exactly two signals, live (so tests'
    monkeypatch takes effect), and never raises:

      - `WSL_DISTRO_NAME` env var, any non-empty value.
      - `PROC_VERSION_PATH` containing "microsoft" (case-insensitive), the
        standard WSL2 kernel-version signature.

    False on a plain macOS/Linux host, or if `/proc/version` is missing or
    unreadable.
    """
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in PROC_VERSION_PATH.read_text().lower()
    except OSError:
        return False


def _npm_global_bin_candidate() -> Path | None:
    """`<npm prefix -g>/bin/codex`, or None (never raises) if the probe misses.

    `npm bin -g` was removed in npm 9+; `npm prefix -g` plus `bin` matches
    npm's own global-bin derivation on POSIX (the only platform this probe
    ever runs on -- it is gated behind `_running_under_wsl2_interop()`).
    """
    try:
        run = runtime.run_sync_capture(
            ["npm", "prefix", "-g"], timeout_seconds=_NPM_BIN_TIMEOUT_SECONDS
        )
    except Exception:
        return None
    if run.binary_missing or run.exit_code != 0:
        return None
    npm_prefix = run.stdout.strip()
    if not npm_prefix:
        return None
    return Path(npm_prefix) / "bin" / "codex"


def _which_fallback() -> str | None:
    """`shutil.which("codex")`, swallowing any unexpected failure."""
    try:
        return shutil.which("codex")
    except Exception:
        return None


def resolve_codex_bin() -> str | None:
    """Resolve the WSL2-native `codex` binary, or `None` if nothing is found.

    The three candidate-directory probes (home, usr-local, npm) only run
    under `_running_under_wsl2_interop()` -- outside WSL2 they exist only to
    shadow the correct binary (e.g. a stale `~/.local/bin/codex` masking a
    newer Homebrew install), so resolution skips straight to
    `shutil.which("codex")` instead. Candidates are evaluated lazily, so a
    winning earlier candidate never triggers the `npm` subprocess call or the
    `which` lookup. Never raises.
    """
    try:
        if _running_under_wsl2_interop():
            home_candidate = _home_local_bin_candidate()
            if home_candidate is not None and _is_executable_file(home_candidate):
                return str(home_candidate)

            usr_local_candidate = _usr_local_bin_candidate()
            if _is_executable_file(usr_local_candidate):
                return str(usr_local_candidate)

            npm_candidate = _npm_global_bin_candidate()
            if npm_candidate is not None and _is_executable_file(npm_candidate):
                return str(npm_candidate)

        return _which_fallback()
    except Exception:
        # Belt-and-suspenders: resolve_codex_bin() must never raise, even if
        # a future edit to one of the helpers above forgets to guard itself.
        return None
