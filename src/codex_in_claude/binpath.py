"""Public `codex_bin()` entry point (#3): explicit-override precedence and
validation, delegation to the candidate resolver, and process-level caching.

Precedence:

  1. `CODEX_IN_CLAUDE_CODEX_BIN`, if set to a non-empty value, wins outright
     and is used EXACTLY as given -- no `shutil.which` re-resolution, even
     for a bare name with no path separators. If the given path does not
     exist on disk this raises loudly (`BinaryNotFoundError`) rather than
     silently falling through to the candidate resolver.
  2. Otherwise, delegate to `binresolve.resolve_codex_bin()`.
  3. If that returns None, fall back to the bare literal `"codex"`
     (`cli_contract.CODEX_BIN`) -- never regress to "no codex invocation
     possible".

The resolved value is cached for the process lifetime; `reset_cache()`
clears it (tests use this via the `_reset_binpath_cache` autouse fixture).
"""

from __future__ import annotations

import os
from pathlib import Path

from codex_in_claude import binresolve, cli_contract

ENV_VAR = "CODEX_IN_CLAUDE_CODEX_BIN"

_cache: str | None = None


class BinaryNotFoundError(RuntimeError):
    """Raised when an explicit `CODEX_IN_CLAUDE_CODEX_BIN` override names a
    path that does not exist on disk."""


def codex_bin() -> str:
    """Resolve the `codex` binary to invoke, cached for the process lifetime.

    Returns:
        The path (or bare literal `"codex"`) to spawn subprocesses with.

    Raises:
        BinaryNotFoundError: `CODEX_IN_CLAUDE_CODEX_BIN` is set to a
            non-empty value that does not exist on disk.
    """
    global _cache  # noqa: PLW0603 - intentional process-level memoization
    if _cache is not None:
        return _cache

    override = os.environ.get(ENV_VAR, "")
    if override:
        if not Path(override).exists():
            raise BinaryNotFoundError(
                f"{ENV_VAR} is set to {override!r}, but no file exists at that path."
            )
        _cache = override
        return _cache

    resolved = binresolve.resolve_codex_bin()
    _cache = resolved if resolved is not None else cli_contract.CODEX_BIN
    return _cache


def reset_cache() -> None:
    """Drop the cached resolution (used by tests)."""
    global _cache  # noqa: PLW0603 - resets the intentional module-level cache
    _cache = None
