"""Config knobs: env defaults, clamps, tier/sandbox/isolation -> codex flags."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from codex_in_claude import cli_contract
from codex_in_claude._core import worktree
from codex_in_claude._core.jobs import JobStore

ENV_PREFIX = "CODEX_IN_CLAUDE_"

MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS = 10, 600
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_INPUT_BYTES = 200_000
# Byte ceiling for a subprocess's captured output (stdout+stderr aggregate), a
# robustness guard against OOM of the long-lived stdio server (#155). Separate
# from MAX_INPUT_BYTES (the diff/input budget) and deliberately generous: the
# JSONL event stream of a long codex run is large but bounded. Output past the
# cap is dropped (head+tail window kept); the run is NOT killed.
DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
# Byte cap for the diff a delegate run returns inline. Oversized diffs are
# truncated with meta.truncated/meta.truncation_hint so agent token cost stays
# bounded; the diffstat still reflects the full diff.
DEFAULT_MAX_DELEGATE_DIFF_BYTES = 200_000
DEFAULT_GIT_TIMEOUT_SECONDS = 60

# Background-job knobs. TTL: how long a terminal record is kept. MAX_SECONDS: a
# job's wall-clock cap (a poll past it reaps the job). MAX_COUNT: retained records
# per workspace (oldest terminal evicted first).
DEFAULT_JOB_TTL_SECONDS = 86_400
DEFAULT_JOB_MAX_SECONDS = 1_800
DEFAULT_JOB_MAX_COUNT = 50

VALID_TIERS = ("consult", "propose", "apply")
VALID_ISOLATIONS = ("inherit", "ignore-config", "ignore-rules")

# Diagnostic logging. Logs go to stderr (and optionally a file); never stdout,
# which is the stdio JSON-RPC channel. WARNING keeps a quiet default while still
# capturing the disconnect/timeout trail a future incident needs (#39).
DEFAULT_LOG_LEVEL = "WARNING"
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

DEFAULT_TIER = "consult"
DEFAULT_ISOLATION = "inherit"

# Default sandbox for each tier. consult is strictly read-only; propose/apply need
# write access (propose is confined to a temp worktree, apply to the live tree).
TIER_SANDBOX = {
    "consult": cli_contract.SANDBOX_READ_ONLY,
    "propose": cli_contract.SANDBOX_WORKSPACE_WRITE,
    "apply": cli_contract.SANDBOX_WORKSPACE_WRITE,
}


@dataclass
class Defaults:
    tier: str
    sandbox: str
    isolation: str
    model: str | None
    timeout_seconds: int


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def defaults() -> Defaults:
    tier = os.environ.get(f"{ENV_PREFIX}TIER_DEFAULT", DEFAULT_TIER)
    tier = tier if tier in VALID_TIERS else DEFAULT_TIER
    isolation = os.environ.get(f"{ENV_PREFIX}ISOLATION", DEFAULT_ISOLATION)
    isolation = isolation if isolation in VALID_ISOLATIONS else DEFAULT_ISOLATION
    sandbox = os.environ.get(f"{ENV_PREFIX}SANDBOX_DEFAULT") or TIER_SANDBOX[tier]
    sandbox = sandbox if sandbox in cli_contract.VALID_SANDBOXES else TIER_SANDBOX[tier]
    return Defaults(
        tier=tier,
        sandbox=sandbox,
        isolation=isolation,
        model=os.environ.get(f"{ENV_PREFIX}MODEL") or None,
        timeout_seconds=_env_int(f"{ENV_PREFIX}TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
    )


# A value the MCP host failed to expand: the literal `${VAR}` form delivered
# verbatim when the host does not perform ${...} substitution. The body must be a
# valid shell variable name so malformed forms are not misreported.
_ENV_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


def is_env_placeholder(value: str | None) -> bool:
    """True when an env value is an unexpanded `${...}` placeholder."""
    return value is not None and bool(_ENV_PLACEHOLDER_RE.match(value.strip()))


def placeholder_env_vars() -> list[str]:
    """Names of tracked `CODEX_IN_CLAUDE_*` env vars left as unexpanded `${...}`."""
    return sorted(
        name
        for name, value in os.environ.items()
        if name.startswith(ENV_PREFIX) and is_env_placeholder(value)
    )


ENV_PLACEHOLDER_REPAIR = (
    "These env vars are literal ${...}; your MCP host is not expanding env "
    "substitutions. Use an env_vars passthrough list, or set literal values."
)


def clamp_timeout(value: int) -> int:
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, value))


def max_input_bytes() -> int:
    return max(1_000, _env_int(f"{ENV_PREFIX}MAX_INPUT_BYTES", DEFAULT_MAX_INPUT_BYTES))


def max_output_bytes() -> int:
    return max(
        64 * 1024,
        _env_int(f"{ENV_PREFIX}MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES),
    )


def max_delegate_diff_bytes() -> int:
    return max(
        1_000,
        _env_int(f"{ENV_PREFIX}MAX_DELEGATE_DIFF_BYTES", DEFAULT_MAX_DELEGATE_DIFF_BYTES),
    )


def git_timeout_seconds() -> int:
    return max(1, _env_int(f"{ENV_PREFIX}GIT_TIMEOUT_SECONDS", DEFAULT_GIT_TIMEOUT_SECONDS))


def job_ttl_seconds() -> int:
    return max(60, _env_int(f"{ENV_PREFIX}JOB_TTL", DEFAULT_JOB_TTL_SECONDS))


def job_max_seconds() -> int:
    return max(60, min(7_200, _env_int(f"{ENV_PREFIX}JOB_MAX_SECONDS", DEFAULT_JOB_MAX_SECONDS)))


def job_max_count() -> int:
    return max(1, min(1_000, _env_int(f"{ENV_PREFIX}JOB_MAX_COUNT", DEFAULT_JOB_MAX_COUNT)))


def job_store() -> JobStore:
    """A JobStore wired to the resolved state dir and job knobs."""
    return JobStore(
        root=state_dir(),
        ttl_seconds=job_ttl_seconds(),
        max_seconds=job_max_seconds(),
        max_count=job_max_count(),
        cleanup_root=Path(tempfile.gettempdir()),
        cleanup_prefix=worktree.WORKTREE_PREFIX,
    )


def sandbox_for_tier(tier: str) -> str:
    """The default sandbox a tier runs under."""
    return TIER_SANDBOX.get(tier, cli_contract.SANDBOX_READ_ONLY)


def isolation_flags(isolation: str) -> list[str]:
    """Codex flags implementing an isolation level.

    inherit       -> [] (use the user's $CODEX_HOME config and project .rules)
    ignore-config -> --ignore-user-config (drop $CODEX_HOME/config.toml; auth kept)
    ignore-rules  -> also --ignore-rules (drop user/project execpolicy .rules)
    """
    if isolation == "inherit":
        return []
    if isolation == "ignore-config":
        return ["--ignore-user-config"]
    if isolation == "ignore-rules":
        return ["--ignore-user-config", "--ignore-rules"]
    raise ValueError(f"unsupported isolation: {isolation}")


def supported_versions() -> frozenset[tuple[int, int]]:
    """The `codex` (major, minor) versions this server is built against.

    Overridable via CODEX_IN_CLAUDE_SUPPORTED_VERSIONS (comma-separated
    "major.minor"). Any parse error falls back to the built-in set."""
    raw = os.environ.get(cli_contract.SUPPORTED_VERSIONS_ENV)
    if not raw:
        return cli_contract.SUPPORTED_VERSIONS
    parsed: set[tuple[int, int]] = set()
    for part in raw.split(","):
        bits = part.strip().split(".")
        if len(bits) < 2:
            continue
        try:
            parsed.add((int(bits[0]), int(bits[1])))
        except ValueError:
            return cli_contract.SUPPORTED_VERSIONS
    return frozenset(parsed) or cli_contract.SUPPORTED_VERSIONS


def parse_version(version: str | None) -> tuple[int, int] | None:
    """Extract (major, minor) from a `codex --version` string, or None."""
    if not version:
        return None
    match = re.search(r"(\d+)\.(\d+)\.\d+", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def version_supported(version: str | None) -> bool | None:
    """Whether the installed codex (major, minor) is in supported_versions().

    Returns None when unparseable. Advisory only — codex_status surfaces a mismatch
    as a warning and never blocks calls on it."""
    parsed = parse_version(version)
    if parsed is None:
        return None
    return parsed in supported_versions()


def log_level() -> str:
    """Resolved diagnostic log level (an invalid value falls back to the default)."""
    raw = os.environ.get(f"{ENV_PREFIX}LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    return raw if raw in VALID_LOG_LEVELS else DEFAULT_LOG_LEVEL


def log_file() -> str | None:
    """Optional file path mirroring the stderr log, or None (stderr only)."""
    value = os.environ.get(f"{ENV_PREFIX}LOG_FILE")
    return value or None


def state_dir() -> Path:
    """Directory for disk-backed background job records."""
    override = os.environ.get(f"{ENV_PREFIX}STATE_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "codex-in-claude" / "jobs"


def rate_limit_snapshot_file() -> Path:
    """Plugin-owned cache file for the latest Codex rate-limit snapshot (sibling of
    the jobs/ store; honors CODEX_IN_CLAUDE_RATE_LIMIT_FILE / STATE_DIR / XDG_CACHE_HOME)."""
    override = os.environ.get(f"{ENV_PREFIX}RATE_LIMIT_FILE")
    if override:
        return Path(override).expanduser()
    return state_dir().parent / "rate_limit_snapshot.json"


def rate_limit_stale_seconds() -> int:
    """Age (seconds) past which a cached snapshot is flagged is_stale. Advisory only —
    the reset-aware interpretation, not this threshold, is the real staleness guard."""
    raw = os.environ.get(f"{ENV_PREFIX}RATE_LIMIT_STALE_SECONDS")
    if raw and raw.isdigit():
        return int(raw)
    return 1800  # 30 minutes


def codex_home() -> Path:
    """Resolved CODEX_HOME (defaults to ~/.codex), used for snapshot provenance."""
    override = os.environ.get("CODEX_HOME")
    return Path(override).expanduser() if override else Path.home() / ".codex"


def worktree_base() -> Path | None:
    """Optional override for where temp worktrees are created (default: alongside
    the repo, managed by git). None means let the worktree module choose."""
    override = os.environ.get(f"{ENV_PREFIX}WORKTREE_BASE")
    return Path(override).expanduser() if override else None
