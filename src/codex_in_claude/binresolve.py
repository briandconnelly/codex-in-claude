"""Ordered candidate-directory resolver for the `codex` CLI binary (#3).

Under WSL2, every `codex` subprocess spawn used the bare literal `"codex"`.
WSL2's PATH interop forwards the Windows `PATH` into the WSL `PATH`, so a
bare-name lookup can resolve to a Windows-side npm-global `codex` shim
instead of the WSL-native install, which then fails confusingly instead of
cleanly.

`resolve_codex_bin()` probes, in order, first hit wins: `$HOME/.local/bin/codex`,
`USR_LOCAL_BIN/codex`, the `npm bin -g` directory, `shutil.which("codex")`,
otherwise `None`. Each directory candidate must exist AND be executable
(`os.access(path, os.X_OK)`); no probe step may ever raise -- a missing or
failing `$HOME`, `npm`, or `which` lookup just yields "no candidate from
this source." Callers decide how to handle a `None` result (see
`binpath.codex_bin()`).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pontonier.core import runtime

# Stands in for the real `/usr/local/bin` so tests can monkeypatch it.
USR_LOCAL_BIN = Path("/usr/local/bin")

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


def _npm_global_bin_candidate() -> Path | None:
    """`<npm bin -g>/codex`, or None (never raises) if the probe misses."""
    try:
        run = runtime.run_sync_capture(
            ["npm", "bin", "-g"], timeout_seconds=_NPM_BIN_TIMEOUT_SECONDS
        )
    except Exception:
        return None
    if run.binary_missing or run.exit_code != 0:
        return None
    npm_dir = run.stdout.strip()
    if not npm_dir:
        return None
    return Path(npm_dir) / "codex"


def _which_fallback() -> str | None:
    """`shutil.which("codex")`, swallowing any unexpected failure."""
    try:
        return shutil.which("codex")
    except Exception:
        return None


def resolve_codex_bin() -> str | None:
    """Resolve the WSL2-native `codex` binary, or `None` if nothing is found.

    Candidates are evaluated lazily, so a winning earlier candidate never
    triggers the `npm` subprocess call or the `which` lookup. Never raises.
    """
    try:
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
