"""Single source of truth for the external `codex` CLI contract.

Every assumption this server makes about the `codex` CLI — its subcommands,
flags, sandbox values, the event/result extraction surface, supported major
versions, and the stderr phrasings that mean the contract drifted — lives here so
an upstream breaking change is a one-file, greppable, testable edit. See
COMPATIBILITY.md for the assumption -> upstream-source map.

Verified against `codex-cli 0.140.0`.
"""

from __future__ import annotations

import re

CODEX_BIN = "codex"

# Core non-interactive invocation. `exec` runs Codex headlessly; if it disappears
# upstream the server cannot function, so a run must fail loudly rather than
# silently degrade.
EXEC_SUBCOMMAND = ("exec",)
REVIEW_SUBCOMMAND = ("review",)
END_OF_OPTIONS = "--"
# Sentinel telling `codex exec` to read the prompt from stdin (keeps gathered
# context/diffs out of argv and local process listings).
STDIN_PROMPT = "-"

# Subcommands / probes (free; no model call).
VERSION_ARGS = ("--version",)
LOGIN_STATUS_ARGS = ("login", "status")
EXEC_HELP_ARGS = ("exec", "--help")

# --- Sandbox modes (security boundary) ------------------------------------------
# The `--sandbox` value is the capability boundary for a run. read-only is the safe
# default; workspace-write is used only for the propose/apply tiers. We NEVER pass
# danger-full-access or --dangerously-bypass-* by default.
SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_DANGER_FULL = "danger-full-access"
VALID_SANDBOXES = (SANDBOX_READ_ONLY, SANDBOX_WORKSPACE_WRITE, SANDBOX_DANGER_FULL)

# --- Flag classes (see COMPATIBILITY.md) ----------------------------------------
# ALWAYS_SEND: guarantee-bearing flags, sent unconditionally for the invocations
# that use them and NEVER gated on `--help` parsing. If upstream removes/renames
# one, `codex` rejects it at arg-parse BEFORE any model call (zero spend) and
# classify_failure() labels it cli_contract_changed. Gating these on the
# (inherently fuzzy) --help parse could silently drop a security/isolation/result
# guarantee, so we never do. The status diagnostic checks them against parsed
# `codex exec --help`.
ALWAYS_SEND_FLAGS = frozenset(
    {
        "--sandbox",  # capability boundary (read-only / workspace-write)
        "--cd",  # explicit working root (never trust ambient cwd)
        "--json",  # structured JSONL event stream we parse for metadata
        "--output-last-message",  # clean final-message extraction (decoupled from event schema)
        "--skip-git-repo-check",  # allow non-repo / worktree roots deliberately
        "--ephemeral",  # do not persist session files (isolation)
        "--ignore-user-config",  # isolation: drop $CODEX_HOME/config.toml
        "--ignore-rules",  # isolation: drop user/project execpolicy .rules
        "--add-dir",  # extra writable dir for the propose/apply tiers
        "--output-schema",  # enforce a JSON Schema on the final response (structured findings)
    }
)

# HELP_GATED: dropping one only reduces depth/cosmetics or relies on a still-present
# primary guard — never a safety/isolation regression. The value is whether the
# flag takes an argument (so the gate skips the value token too). These are the ONLY
# flags gated on `codex exec --help`; a false negative here merely drops a harmless
# flag.
HELP_GATED_FLAGS = {
    "--model": True,  # falls back to the configured/default Codex model
}

# Cache TTL for the `codex exec --help` probe, so a long-lived server re-probes
# after an in-place CLI upgrade instead of trusting a stale snapshot forever.
HELP_CACHE_TTL_SECONDS = 300

# --- Supported `codex` major version(s) -----------------------------------------
# Codex is pre-1.0 and ships as 0.x; the "feature" version is the minor (0.140.x).
# We track the minor as the compatibility axis and keep the env override so a user
# can opt into an untested version themselves. Advisory only: a mismatch warns but
# never blocks (auth + binary presence decide readiness).
SUPPORTED_VERSIONS = frozenset({(0, 140)})
SUPPORTED_VERSIONS_ENV = "CODEX_IN_CLAUDE_SUPPORTED_VERSIONS"

# --- Result / event extraction surface ------------------------------------------
# The final agent answer is read from the --output-last-message FILE (stable,
# documented). The --json JSONL stream is parsed TOLERANTLY for optional metadata
# only (token usage, session id, error text); we never depend on a specific event
# shape, so an event-schema change degrades metadata rather than breaking a run.
# These key names are the tolerant `.get()` lookups; listing them keeps the
# consumed surface greppable and anchors the golden-event test.
USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "total_tokens",
    }
)
# Substrings that, in a JSONL event's "type"/"msg" discriminator, mark it as
# carrying token-usage or the final agent message. Matched case-insensitively.
USAGE_EVENT_MARKERS = ("token_count", "usage")
FINAL_MESSAGE_EVENT_MARKERS = ("agent_message", "task_complete")
ERROR_EVENT_MARKERS = ("error", "stream_error")

# --- Login-status signatures ----------------------------------------------------
# `codex login status` exits 0 when authenticated and prints a NON-identifying
# method line ("Logged in using ChatGPT" / "Logged in using API key"). We report
# the method keyword but never echo the raw line (it may include account detail).
LOGIN_METHOD_CHATGPT = "ChatGPT"
LOGIN_METHOD_API_KEY = "API key"

# --- Contract-drift stderr signatures (clap, Codex's arg parser) ----------------
# Phrasings clap prints when it rejects a flag/value/subcommand we sent. Matching
# any (case-insensitive) reclassifies an otherwise-generic failure as
# cli_contract_changed, telling the user the plugin needs an update for their CLI
# rather than leaving a confusing nonzero_exit.
CONTRACT_DRIFT_STDERR_PATTERNS = (
    "unexpected argument",
    "unrecognized subcommand",
    "unrecognized option",
    "unknown option",
    "unknown flag",
    "invalid value",
    "invalid choice",
    "no such subcommand",
    "found argument",
)

# --- Auth-failure stderr/stdout signatures --------------------------------------
AUTH_FAILURE_PATTERNS = (
    "not logged in",
    "not authenticated",
    "please run `codex login`",
    "please run codex login",
    "run `codex login`",
    "401",
    "unauthorized",
)

# --- Rate-limit stderr/stdout/event signatures ----------------------------------
# Phrasings that mean the account hit a usage/rate limit (ChatGPT 5-hour window or
# an API-key 429) rather than a hard failure. Matching any (case-insensitive)
# reclassifies an otherwise-generic failure as a retryable codex_rate_limited so a
# calling agent can back off deterministically instead of retry-storming.
RATE_LIMIT_PATTERNS = (
    "rate limit",
    "too many requests",
    "usage limit",
    "quota",
    "retry-after",
)
# "429" is matched separately with word boundaries so it doesn't fire on an
# incidental digit run (a filename like file429.py, a version, a longer code like
# 4290); the phrase patterns above are specific enough as plain substrings.
_HTTP_429_PATTERN = re.compile(r"\b429\b")

# Backoff (ms) suggested when codex reports a rate limit but provides no parseable
# Retry-After value. Conservative: rate limits commonly reset on minute/hour
# windows, so 60s avoids an immediate re-hit while staying responsive.
RATE_LIMIT_DEFAULT_BACKOFF_MS = 60_000

# Matches a delay codex may surface alongside a rate limit: an HTTP-style
# "Retry-After: <seconds>" header, or prose like "retry after 5s" / "try again in
# 12 seconds". Captures the number and any immediately following unit token so the
# parser can REJECT non-second units (minutes/hours) rather than misread them as
# seconds, and so an HTTP-date "Retry-After:" header (no leading number) never
# matches. The gap before the number is restricted to whitespace/colon so a date
# or unrelated text breaks the match instead of yielding a far-off number.
_SECOND_UNITS = frozenset({"", "s", "sec", "secs", "second", "seconds"})
# The unit group also consumes a hyphen-joined word (e.g. "5-minute") so such a
# token is captured and rejected, not silently skipped as a bare-seconds value.
_RETRY_AFTER_PATTERN = re.compile(
    r"(?:retry[-\s]?after|try\s+again\s+in)[\s:]*?(\d+)[ \t]*(-?[a-z]+)?",
    re.IGNORECASE,
)


def is_contract_drift(*texts: str | None) -> bool:
    """Whether any provided text carries a contract-drift signature.

    Used on every failure path so drift is labelled consistently no matter where
    `codex` surfaces it."""
    blob = "\n".join(t for t in texts if t).lower()
    return any(pattern in blob for pattern in CONTRACT_DRIFT_STDERR_PATTERNS)


def is_auth_failure(*texts: str | None) -> bool:
    """Whether any provided text indicates a Codex authentication failure."""
    blob = "\n".join(t for t in texts if t).lower()
    return any(pattern in blob for pattern in AUTH_FAILURE_PATTERNS)


def is_rate_limited(*texts: str | None) -> bool:
    """Whether any provided text indicates a Codex usage/rate-limit failure."""
    blob = "\n".join(t for t in texts if t).lower()
    if any(pattern in blob for pattern in RATE_LIMIT_PATTERNS):
        return True
    return _HTTP_429_PATTERN.search(blob) is not None


def parse_retry_after_ms(*texts: str | None) -> int | None:
    """Suggested backoff in ms parsed from a seconds-valued Retry-After, or None.

    Only second-valued delays are honored; a non-second unit (minutes/hours) or a
    non-numeric (HTTP-date) Retry-After returns None so callers fall back to the
    documented RATE_LIMIT_DEFAULT_BACKOFF_MS rather than a wildly wrong backoff."""
    blob = "\n".join(t for t in texts if t)
    match = _RETRY_AFTER_PATTERN.search(blob)
    if match is None or (match.group(2) or "").lower() not in _SECOND_UNITS:
        return None
    return int(match.group(1)) * 1000
