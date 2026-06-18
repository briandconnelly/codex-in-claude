"""Feature-detect which `codex exec` flags exist, by parsing its --help once.

Only the HELP_GATED flags (depth/cosmetic) are gated on this probe: dropping one
when absent keeps the server working across a minor upstream change. The
guarantee-bearing ALWAYS_SEND flags are never gated here — their removal is caught
loudly at run time (cli_contract_changed), not silently pre-empted, because
`--help` parsing is fuzzy and a false negative must never drop a safety/isolation
flag.

Everything degrades, nothing crashes: any probe failure yields help_parsed=False,
which makes is_supported() return True for every flag (fail open)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from codex_in_claude import cli_contract
from codex_in_claude._core import runtime

_LONG_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]+")


@dataclass(frozen=True)
class FlagSupport:
    supported: frozenset[str]
    help_parsed: bool  # False => probe failed; callers must fail open


# Process-level cache: (monotonic_timestamp, FlagSupport). A long-lived MCP server
# re-probes after HELP_CACHE_TTL_SECONDS so an in-place `codex` upgrade is noticed.
_cache: tuple[float, FlagSupport] | None = None


def _probe_help() -> str:
    """Return the combined `codex exec --help` text, or "" on any failure."""
    run = runtime.run_sync_capture(
        [cli_contract.CODEX_BIN, *cli_contract.EXEC_HELP_ARGS], timeout_seconds=10
    )
    if run.binary_missing:
        return ""
    return f"{run.stdout}\n{run.stderr}"


def _parse_supported(help_text: str) -> frozenset[str]:
    """Extract long-flag names from help text. Deliberately tolerant: this only
    governs HELP_GATED flags, where a stray/missing match drops a harmless flag."""
    return frozenset(_LONG_FLAG_RE.findall(help_text))


def flag_support(force: bool = False) -> FlagSupport:
    """Cached FlagSupport for the installed `codex`. force=True bypasses the cache."""
    global _cache  # noqa: PLW0603 — intentional process-level memoization of the help probe
    now = time.monotonic()
    if not force and _cache is not None:
        stamped, value = _cache
        if now - stamped < cli_contract.HELP_CACHE_TTL_SECONDS:
            return value
    help_text = _probe_help()
    if not help_text.strip():
        value = FlagSupport(supported=frozenset(), help_parsed=False)
    else:
        value = FlagSupport(supported=_parse_supported(help_text), help_parsed=True)
    _cache = (now, value)
    return value


def reset_cache() -> None:
    """Drop the cached probe (used by tests)."""
    global _cache  # noqa: PLW0603 — resets the intentional module-level cache
    _cache = None


def is_supported(flag: str, fs: FlagSupport) -> bool:
    """Whether `flag` may be sent. Fails OPEN: when the probe could not run
    (help_parsed=False) every flag is treated as supported."""
    return (not fs.help_parsed) or (flag in fs.supported)


def missing_expected_flags(fs: FlagSupport) -> list[str]:
    """Guarantee-bearing ALWAYS_SEND flags that `--help` did not list. Empty when
    the probe could not run. Diagnostic only — surfaced by codex_status, it does
    NOT gate execution."""
    if not fs.help_parsed:
        return []
    return sorted(f for f in cli_contract.ALWAYS_SEND_FLAGS if f not in fs.supported)
