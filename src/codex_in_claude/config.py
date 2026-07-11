"""Config knobs: env defaults, clamps, tier/sandbox/isolation -> codex flags."""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from codex_in_claude import cli_contract
from codex_in_claude._core import redaction, worktree
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


# --- Opt-in extra `codex` args passthrough (CODEX_IN_CLAUDE_EXTRA_ARGS, #231) ----
# An operator-only knob to add extra global `codex` options to every PAID exec
# invocation (consult/review/delegate) — its motivating use is selecting a
# model_provider/profile when isolation sends --ignore-user-config (which drops the
# user's config.toml, leaving CLI -c overrides the only lever). Deliberately an
# allowlist, not arbitrary argv: a bare positional or unknown flag could clobber the
# envelope-bearing plugin flags (--json/--cd/--sandbox/--output-schema/…) or smuggle a
# prompt, hollowing out the fail-loud CLI contract.
EXTRA_ARGS_ENV = f"{ENV_PREFIX}EXTRA_ARGS"

# The allowlisted global options. All four are verified `codex` global+exec options
# (codex-cli 0.144.1). Value-taking; a bare/unknown flag is rejected. Short `-c`/`-p`
# are accepted only space-separated (an attached `-cKEY=VAL` is undocumented and
# rejected); the long forms accept both `--config VAL` and `--config=VAL`.
_EXTRA_CONFIG_FLAGS = ("-c", "--config")  # -c KEY=VALUE  (a dotted-path config override)
_EXTRA_PROFILE_FLAGS = ("-p", "--profile")  # -p NAME       (layer a named config profile)
_EXTRA_FEATURE_FLAGS = ("--enable", "--disable")  # --enable/--disable FEATURE
_EXTRA_ENABLE_FLAGS = ("--enable",)  # the re-enabling half of _EXTRA_FEATURE_FLAGS

# Feature NAMES an operator may not re-enable, and the exact config KEYS that re-enable
# them, refused even though `--enable`/`-c` are allowlisted: the plugin disables the
# remote_plugin connectors on every model-bearing call as a documented security guarantee
# (#287), and an operator override must not silently defeat it. `--enable X` is exactly
# `-c features.X=true`, so both spellings are denied; `--disable remote_plugin` stays
# allowed (it only agrees with the plugin). Whole-key match (not the root-segment match
# _DENIED_CONFIG_KEY_ROOTS uses) so only this one feature is refused, not all of
# `features.*`. NOTE: an opaque `--profile` can still re-enable it — the same documented
# operator-trust boundary that already bounds the `-c` denials (see COMPATIBILITY.md).
_DENIED_ENABLE_FEATURES = frozenset({cli_contract.REMOTE_PLUGIN_FEATURE})
# Both the dotted key AND the bare `features` parent table are refused: `-c
# features={remote_plugin=true}` (a TOML inline table) reaches the same setting through the
# parent key, so denying only the dotted form leaves that inline-table bypass open. Denying
# bare `features` refuses the whole-table inline form; a different feature is still settable
# via its own dotted key (`-c features.some_other=true`), which is NOT in this set.
_FEATURES_NAMESPACE = "features"
_DENIED_CONFIG_KEYS = frozenset(
    {_FEATURES_NAMESPACE, f"{_FEATURES_NAMESPACE}.{cli_contract.REMOTE_PLUGIN_FEATURE}"}
)

# Config-key roots refused even though `-c/--config` is allowlisted: a `-c` value can
# override ANY dotted config path, and these would weaken a guarantee this server
# advertises — the sandbox capability boundary and the no-network-egress promise
# (sandbox_workspace_write.network_access lives under `sandbox`), the approval posture,
# or the host-env isolation of commands codex runs (shell_environment_policy.inherit
# could expose the server's environment, secrets included). Refused at parse time so
# they never reach codex. NOTE: `--profile` layers an opaque on-disk TOML this parser
# cannot inspect, so a profile remains a documented operator-trust boundary (see
# COMPATIBILITY.md); this denylist covers only the inspectable `-c` surface.
_DENIED_CONFIG_KEY_ROOTS = frozenset({"sandbox", "approval_policy", "shell_environment_policy"})


@dataclass(frozen=True)
class ExtraArgs:
    """Parsed CODEX_IN_CLAUDE_EXTRA_ARGS. `tokens` is the validated argv to inject
    (may carry secret `-c` VALUES — never echo it). `descriptors` are sanitized
    identifiers (allowlisted flag names, config KEYS, profile/feature NAMES — never a
    `-c` value) safe to surface in codex_status / an error envelope and to match against
    a codex drift stderr. `error` is a value-free 'why invalid' string set only when the
    knob is present but failed to parse/validate; `configured` is True whenever the env
    var is set to a non-blank value."""

    tokens: tuple[str, ...] = ()
    descriptors: tuple[str, ...] = ()
    option_count: int = 0
    configured: bool = False
    error: str | None = None

    @property
    def valid(self) -> bool:
        """True when the knob is unset, or set and parsed/validated cleanly."""
        return self.error is None


def _extra_args_flag_kind(flag: str) -> str | None:
    if flag in _EXTRA_CONFIG_FLAGS:
        return "config"
    if flag in _EXTRA_PROFILE_FLAGS:
        return "profile"
    if flag in _EXTRA_FEATURE_FLAGS:
        return "feature"
    return None


def _safe_token(token: str) -> str:
    """A bounded, secret-redacted echo of an offending token for an error message."""
    return (redaction.redact_text(token) or "")[:60]


def _normalize_config_key(key: str) -> str:
    """Normalize a dotted `-c` config KEY the way codex's own parser does — trim each
    segment and lowercase — so a leading/trailing/embedded space can't slip a denied key
    (e.g. `features . Remote_Plugin`) past the whole-key denylist."""
    return ".".join(seg.strip() for seg in key.split(".")).lower()


def _parse_extra_args(raw: str) -> ExtraArgs:
    """Tokenize + allowlist-validate a non-blank CODEX_IN_CLAUDE_EXTRA_ARGS value."""
    try:
        toks = shlex.split(raw)
    except ValueError:
        return ExtraArgs(configured=True, error="could not tokenize (unbalanced quotes?)")
    tokens: list[str] = []
    descriptors: list[str] = []
    count = 0
    i = 0
    while i < len(toks):
        tok = toks[i]
        # Long `--flag=value` attached form → one token; split on the FIRST `=`.
        attached = tok.startswith("--") and "=" in tok
        if attached:
            flag, value = tok.split("=", 1)
        else:
            flag = tok
        kind = _extra_args_flag_kind(flag)
        if kind is None:
            return ExtraArgs(configured=True, error=f"unsupported argument: {_safe_token(tok)}")
        if not attached:
            if i + 1 >= len(toks):
                return ExtraArgs(configured=True, error=f"{flag} requires a value")
            value = toks[i + 1]
            i += 1
        # A value that itself looks like a flag is a smuggled option, not a value.
        if value.startswith("-"):
            return ExtraArgs(configured=True, error=f"{flag} value looks like a flag")
        if kind == "config":
            if "=" not in value:
                return ExtraArgs(configured=True, error=f"{flag} expects KEY=VALUE")
            key = value.split("=", 1)[0]
            if not key.strip():
                return ExtraArgs(configured=True, error=f"{flag} has an empty config key")
            # Normalize the root segment the way codex's own `-c` parser does (it trims
            # keys), so a leading/segment space can't slip a denied key past the check.
            root = key.split(".", 1)[0].strip().lower()
            if any(root == d or root.startswith(f"{d}_") for d in _DENIED_CONFIG_KEY_ROOTS):
                return ExtraArgs(
                    configured=True,
                    error=(
                        f"config key '{key.strip()}' is refused: it could weaken the sandbox / "
                        "network / approval / host-env-isolation guarantees this server advertises"
                    ),
                )
            if _normalize_config_key(key) in _DENIED_CONFIG_KEYS:
                return ExtraArgs(
                    configured=True,
                    error=(
                        f"config key '{key.strip()}' is refused: the plugin disables the "
                        "remote_plugin connectors as a security guarantee (#287); an operator "
                        "override cannot re-enable them"
                    ),
                )
            tokens += [flag, value]
            # Record the flag too (not just the key), so a drift where codex rejects the
            # `-c`/`--config` flag token itself is still attributed to the passthrough.
            # The key is a config-path name (not a secret); the `-c` VALUE is never added.
            descriptors += [flag, key]
        else:  # profile / feature — the value is a non-secret NAME
            if not value:
                return ExtraArgs(configured=True, error=f"{flag} requires a non-empty value")
            if (
                kind == "feature"
                and flag in _EXTRA_ENABLE_FLAGS
                and value.strip().lower() in _DENIED_ENABLE_FEATURES
            ):
                return ExtraArgs(
                    configured=True,
                    error=(
                        f"feature '{value.strip()}' cannot be enabled via {EXTRA_ARGS_ENV}: the "
                        "plugin disables the remote_plugin connectors as a security guarantee "
                        "(#287)"
                    ),
                )
            tokens += [flag, value]
            descriptors += [flag, value]
        count += 1
        i += 1
    # De-dupe descriptors while preserving order (a stable, small match/echo set).
    seen: dict[str, None] = {}
    for d in descriptors:
        seen.setdefault(d, None)
    return ExtraArgs(
        tokens=tuple(tokens),
        descriptors=tuple(seen),
        option_count=count,
        configured=True,
    )


def extra_args() -> ExtraArgs:
    """Resolve CODEX_IN_CLAUDE_EXTRA_ARGS. Blank/unset → an empty, valid ExtraArgs."""
    raw = os.environ.get(EXTRA_ARGS_ENV)
    if raw is None or not raw.strip():
        return ExtraArgs()
    return _parse_extra_args(raw)


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
