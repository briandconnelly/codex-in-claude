"""Single source of truth for the external `codex` CLI contract.

Every assumption this server makes about the `codex` CLI — its subcommands,
flags, sandbox values, the event/result extraction surface, supported major
versions, and the stderr phrasings that mean the contract drifted — lives here so
an upstream breaking change is a one-file, greppable, testable edit. See
COMPATIBILITY.md for the assumption -> upstream-source map.

Verified against `codex-cli 0.144.1`.
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

# --- app-server (session transfer) ----------------------------------------------
# `codex app-server` speaks newline-delimited JSON-RPC 2.0 over stdio (one JSON object
# per line, no Content-Length framing). We drive it for ONE thing only — importing a
# Claude Code session transcript into a resumable Codex thread (codex_transfer). The
# whole surface below is EXPERIMENTAL upstream (`codex app-server` is labeled
# [experimental] and the import method rides behind the `experimentalApi` capability),
# so every wire assumption lives here; see COMPATIBILITY.md. Verified against
# codex-cli 0.144.1 on 2026-07-10 via `codex app-server generate-json-schema --out <DIR>`
# (the generator now requires an --out directory instead of writing to stdout).
APP_SERVER_SUBCOMMAND = ("app-server",)
# JSON-RPC handshake (v1) + the experimental import request/notifications (v2).
APP_SERVER_INITIALIZE_METHOD = "initialize"
APP_SERVER_INITIALIZED_NOTIFICATION = "initialized"
APP_SERVER_IMPORT_METHOD = "externalAgentConfig/import"
APP_SERVER_IMPORT_PROGRESS_NOTIFICATION = "externalAgentConfig/import/progress"
APP_SERVER_IMPORT_COMPLETED_NOTIFICATION = "externalAgentConfig/import/completed"
# We opt into experimental methods/fields; without it the import method is absent and
# the success `target` (imported thread id) is filtered out of the completed
# notification. Value True is sent in initialize `capabilities`.
APP_SERVER_EXPERIMENTAL_CAPABILITY = "experimentalApi"
# The migration item type for a whole-session transfer, and the JSON field names we
# read tolerantly (.get()) off the wire. Listing them keeps the consumed surface
# greppable and anchors the fake-app-server tests.
IMPORT_SESSION_ITEM_TYPE = "SESSIONS"
# initialize response → absolute $CODEX_HOME (so we never guess where the ledger lives).
APP_SERVER_CODEX_HOME_KEY = "codexHome"
# import response → the async import's correlation id (echoed by the notifications).
IMPORT_ID_KEY = "importId"
# completed/progress notification payload → per-item-type success/failure buckets.
IMPORT_ITEM_RESULTS_KEY = "itemTypeResults"
IMPORT_ITEM_TYPE_KEY = "itemType"
IMPORT_SUCCESSES_KEY = "successes"
IMPORT_FAILURES_KEY = "failures"
# A success entry carries {source: <abs transcript path>, target: <imported thread id>};
# a failure entry carries {message, failureStage, errorType}. `target` is the PRIMARY,
# schema-emitted thread-id source (present only on a FRESH import, since Codex dedups a
# byte-identical transcript to a silent no-op with no success entry).
IMPORT_SOURCE_KEY = "source"
IMPORT_TARGET_KEY = "target"
IMPORT_MESSAGE_KEY = "message"
# JSON-RPC error code Codex returns when the import method is absent (older CLI): the
# hard backstop behind the advisory SUPPORTED_VERSIONS gate.
JSONRPC_METHOD_NOT_FOUND = -32601
# JSON-RPC 2.0 reserves -32768..-32000 for protocol/framework errors (parse error,
# invalid request, invalid params, internal error, and server-defined -32000..-32099).
# An import-request error in this range — or a malformed error with no integer code —
# means our REQUEST or the app-server framework is at fault (contract drift), so it maps
# to cli_contract_changed. An application-range code is a genuine import rejection and
# maps to transfer_failed instead. (JSONRPC_METHOD_NOT_FOUND is handled separately.)
JSONRPC_RESERVED_ERROR_MIN = -32768
JSONRPC_RESERVED_ERROR_MAX = -32000

# --- App-server: account rate-limits read (0.144+) ------------------------------
# codex 0.144 removed the `token_count` JSONL event; quota no longer rides the
# `codex exec --json` stream (that event is gone — only `turn.completed.usage` for token
# counts remains). Quota moved onto the app-server protocol: `account/rateLimits/read`
# (params: null) is a READ-ONLY, no-model-spend request that returns the current quota
# snapshot after the same initialize/initialized handshake codex_transfer uses. Verified
# against codex-cli 0.144.4 on 2026-07-14 via `codex app-server`. See #321, COMPATIBILITY.md.
APP_SERVER_RATE_LIMITS_READ_METHOD = "account/rateLimits/read"
# read response → `result.rateLimits` is the single-bucket RateLimitSnapshot. Its windows
# are `primary`/`secondary`, but — unlike the old exec-stream block, which fixed primary=5h
# and secondary=weekly — the app-server's slot order is NOT stable (a Plus account was
# observed reporting only the weekly window, in the `primary` slot). We therefore classify
# windows by RATE_LIMIT_WINDOW_DURATION_MINS_KEY, not slot position (see appserver.py).
RATE_LIMITS_RESULT_KEY = "rateLimits"
RATE_LIMIT_PRIMARY_KEY = "primary"
RATE_LIMIT_SECONDARY_KEY = "secondary"
RATE_LIMIT_PLAN_TYPE_KEY = "planType"
RATE_LIMIT_REACHED_TYPE_KEY = "rateLimitReachedType"
# per-window fields (camelCase on the app-server protocol; snake_case in our schema).
RATE_LIMIT_WINDOW_USED_PERCENT_KEY = "usedPercent"
RATE_LIMIT_WINDOW_DURATION_MINS_KEY = "windowDurationMins"
RATE_LIMIT_WINDOW_RESETS_AT_KEY = "resetsAt"
# Duration boundary (minutes) separating a short/rolling window (historically the 5-hour
# limit) from a long window (historically weekly). A window at or below this maps to our
# `primary` slot, above it to `secondary`. 1 day is a wide margin between a ~5h and a ~weekly
# window, so it survives upstream retuning the exact durations without remisclassifying.
RATE_LIMIT_SHORT_WINDOW_MAX_MINUTES = 1440
# The recognized `rateLimitReachedType` values (app-server enum, lower-cased) PLUS the legacy
# exec-stream window-name form the interpreter still honors. The value is agent-visible and
# gets interpolated into prose, so an unrecognized value from a drifting/hostile child is
# dropped (treated as no signal) rather than trusted as a real "limit reached" reason.
RATE_LIMIT_REACHED_TYPES = frozenset(
    {
        "rate_limit_reached",
        "workspace_owner_credits_depleted",
        "workspace_member_credits_depleted",
        "workspace_owner_usage_limit_reached",
        "workspace_member_usage_limit_reached",
        "primary",  # legacy exec-stream window-name form
        "secondary",
    }
)
# Defensive length cap for the free-form `planType` string before it reaches an envelope
# (the wire value is untrusted and the input line cap is 8 MiB). Real values are short
# identifiers ("plus", "self_serve_business_usage_based").
RATE_LIMIT_PLAN_TYPE_MAX_BYTES = 64

# --- App-server identifier bounds (defensive policy, not a documented protocol limit) -
# Upstream publishes no length cap on these ids/paths, so we pick generous ceilings that
# reject implausible or hostile values — an id or path far past these is drift, not a real
# identifier — WITHOUT pinning a specific id format (a ULID/UUID scheme change must not
# false-positive). Surfaced in codex_transfer's result; see #279.
# opaque ids (imported thread id / importId); ULIDs/UUIDs are ~26-36 bytes
TRANSFER_ID_MAX_BYTES = 512
# a filesystem path; ~PATH_MAX headroom. Absolute-ness is the real invariant.
CODEX_HOME_MAX_BYTES = 4096

# --- Import ledger (undocumented dedup fallback) ---------------------------------
# $CODEX_HOME/external_agent_session_imports.json maps an imported transcript to its
# thread id: {"records": [{source_path, content_sha256, imported_thread_id}]}. Same
# drift class as models_cache.json — an UNDOCUMENTED internal file — so we read it only
# as the FALLBACK when a re-import of a byte-identical transcript produced no fresh
# `target` in the completed notification, and always tolerantly (bounds below). The
# notification `target` is the primary path; this is best-effort recovery.
IMPORT_LEDGER_FILENAME = "external_agent_session_imports.json"
IMPORT_LEDGER_RECORDS_KEY = "records"
IMPORT_LEDGER_SOURCE_PATH_KEY = "source_path"
IMPORT_LEDGER_CONTENT_SHA_KEY = "content_sha256"
IMPORT_LEDGER_THREAD_ID_KEY = "imported_thread_id"
# Defensive bounds for that env-controlled file (real file is a few KB/record).
IMPORT_LEDGER_MAX_BYTES = 5_000_000
IMPORT_LEDGER_MAX_RECORDS = 10_000

# --- Sandbox modes (security boundary) ------------------------------------------
# The `--sandbox` value is the capability boundary for a run. read-only is the safe
# default; workspace-write is used only for the propose/apply tiers. We NEVER pass
# danger-full-access or --dangerously-bypass-* by default.
SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_DANGER_FULL = "danger-full-access"
VALID_SANDBOXES = (SANDBOX_READ_ONLY, SANDBOX_WORKSPACE_WRITE, SANDBOX_DANGER_FULL)

# --- Remote-plugin isolation (issue #287) ---------------------------------------
# Codex 0.143+ flipped the `remote_plugin` feature to default-on, which makes named
# third-party connectors (GitHub, Gmail, Google Drive, Slack, Notion, …) available to
# the model on every run. Those connectors are network side-effect / data-disclosure
# channels that live OUTSIDE the local `--sandbox` filesystem boundary, so they are
# incompatible with this server's advertised safe, read-only-by-default posture. The
# existing `--ignore-user-config` isolation does NOT neutralize them (plugins load from
# marketplace snapshots, not `$CODEX_HOME/config.toml`). We therefore disable the feature
# on EVERY model-bearing `codex exec` call, unconditionally, via the plugin-owned
# `--disable remote_plugin` (verified `== -c features.remote_plugin=false`; it wins over
# `--enable`/`-c ...=true` regardless of order, and an unknown feature name fails loud as
# `Error: Unknown feature flag`, giving us ALWAYS_SEND fail-closed drift). The guarantee is
# bounded by the documented `--profile` operator-trust boundary (an opaque profile this
# server cannot inspect); see COMPATIBILITY.md.
DISABLE_FEATURE_FLAG = "--disable"  # `--disable <FEATURE>`; == `-c features.<FEATURE>=false`
REMOTE_PLUGIN_FEATURE = "remote_plugin"

# --- Auto-loaded workspace context (issue #300) ----------------------------------
# `codex exec` automatically loads the resolved workspace's `AGENTS.md` into model
# context and auto-discovers skills under `.agents/skills/` (per upstream docs:
# name/description metadata up front, a skill's body when it is selected). It needs no
# tool-directed read, and every model-bearing call here runs `codex exec` — so that
# content can reach OpenAI even when the caller's prompt never mentions those files.
# Verified empirically against codex-cli 0.144.1 (2026-07-12) via marker probes;
# invisible in `codex exec --help` (no flag, no subcommand), so the mechanical
# help-drift check CANNOT catch upstream changes to it. The isolation flags do NOT
# suppress it: `--ignore-user-config` drops `$CODEX_HOME/config.toml` and
# `--ignore-rules` drops execpolicy `.rules`; neither touches project-level `AGENTS.md`
# or `.agents/skills/`. Upstream docs:
# https://developers.openai.com/codex/concepts/customization#agents-guidance and
# https://developers.openai.com/codex/concepts/customization#skills.
# Reader-facing detail — the re-verification probe and the unverified edge cases
# (`.claude/skills/`, parent-dir `AGENTS.md`, `project_doc_max_bytes=0`) — lives in
# COMPATIBILITY.md, "Auto-loaded workspace context"; keep that section the single home
# for both.
#
# RULE: every egress-caveat prose site — the server instructions, the codex_status
# caveat, the tool capability descriptions and docstrings, README.md, COMPATIBILITY.md,
# SECURITY.md, and the collaborating-with-codex skill — must disclose this. No
# feature-detection logic exists here by design.

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
        DISABLE_FEATURE_FLAG,  # isolation: disable remote_plugin connectors (#287)
    }
)

# HELP_GATED: dropping one only reduces depth/cosmetics or relies on a still-present
# primary guard — never a safety/isolation regression. The value is whether the
# flag takes an argument (so the gate skips the value token too). These are the ONLY
# flags gated on `codex exec --help`; a false negative here merely drops a harmless
# flag.
# The model-selection flag, named so the help-gating drop and the downstream
# provenance reconciliation (meta.model) reference one constant, not a literal.
MODEL_FLAG = "--model"
HELP_GATED_FLAGS = {
    MODEL_FLAG: True,  # falls back to the configured/default Codex model
}

# --- Reasoning-effort config override (issue #309) --------------------------------
# `codex exec` 0.144.3 has no dedicated reasoning-effort flag (verified against
# `codex exec --help` 2026-07-13); the only route is the `model_reasoning_effort`
# config key, sent as `-c model_reasoning_effort=<value>`. A config KEY cannot be
# help-gated — `--help` advertises flags, not config keys — so when a caller (or the
# CODEX_IN_CLAUDE_REASONING_EFFORT default) requests an effort it is sent
# unconditionally. Drift coverage is NARROWER than ALWAYS_SEND: only removal of the
# `-c` flag itself fails loudly (arg-parse, zero spend, cli_contract_changed). A
# rename/removal of the KEY drifts SILENTLY — codex tolerates unknown `-c` keys as
# junk it never reads (verified for 0.144.3, #312) — so the effort would be quietly
# ignored; the manual re-verification probe in docs/UPGRADING-CODEX.md is the only
# guard for that case.
#
# The VALUE's semantic set is open — the plugin enforces only transport-shape bounds
# (config.reasoning_effort_shape_error) and allowlists nothing: the CLI accepts any
# in-shape string silently, and the backend judges it at request time; its accepted
# set varies by model and account (probed 2026-07-13: gpt-5.5 via
# ChatGPT advertises none|minimal|low|medium|high|xhigh; the models cache advertises
# max/ultra for other slugs). Discovery is advisory only (codex_models).
MODEL_REASONING_EFFORT_CONFIG_KEY = "model_reasoning_effort"

# Markers identifying the BACKEND's rejection of a bad reasoning-effort VALUE (a
# caller error), as distinct from a CLI rejection of the config key itself (contract
# drift). The backend 400 reads "[ReasoningEffortParam] [reasoning.effort]
# [invalid_enum_value] Invalid value: '<v>'..." — which also matches the "invalid
# value" drift pattern above, so classify_failure must check these markers first or a
# caller's typo would be misreported as cli_contract_changed. ALL markers must appear
# in their bracketed `[…]` field form — a marker as a free substring is how an
# operator passthrough naming one (`--enable reasoning.effort`, a profile so named)
# would impersonate the backend signature and steal an extra_args_rejected
# attribution (#313). Deliberately EXCLUDES "model_reasoning_effort": a rejection
# naming only the key means codex no longer accepts the key — genuine drift that
# must stay fail-loud.
REASONING_EFFORT_REJECTION_MARKERS = ("reasoning.effort", "reasoningeffortparam")

# Conservative shape for an effort token read from the UNDOCUMENTED models cache
# (same defensive posture as MODEL_SLUG_PATTERN): entries failing it are dropped so a
# malformed/hostile cache cannot surface junk to an agent. Never applied to the
# caller's own reasoning_effort parameter, which is passed through for the backend to
# validate.
# \Z, not $: `$` also matches before a trailing newline, so a malformed cache token
# like "high\n" would slip the shape check under re.match.
REASONING_EFFORT_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
# Ignore anything past this many supported-effort entries per model (the real cache
# advertises ≤ 6).
SUPPORTED_EFFORTS_MAX_ENTRIES = 16

# --- Model catalog (advisory discovery) -----------------------------------------
# Codex caches its authoritative model list at $CODEX_HOME/models_cache.json (default
# ~/.codex). It is an UNDOCUMENTED internal file, written lazily by real Codex sessions
# (a fresh install has none) and NOT regenerated by `codex doctor`. We read it only to
# help an agent DISCOVER valid `--model` slugs; `codex exec` remains the real validator,
# so we never reject a slug merely because it is absent here.
MODELS_CACHE_FILENAME = "models_cache.json"
# Defensive bounds for that env-controlled file (consumed in codex_models via
# _core.jsoncache). The real file is ~150 KB; 1 MB is generous headroom.
MODELS_CACHE_MAX_BYTES = 1_000_000
MODELS_CACHE_MAX_ENTRIES = 256  # ignore anything past this many model entries
# A conservative slug shape; entries failing it are dropped (defends against a
# malformed/hostile cache surfacing junk to an agent).
MODEL_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Bundled advisory fallback used ONLY when the on-disk cache is absent/unreadable.
# Copied from codex-cli 0.144.1's models_cache.json on 2026-07-10 (cache order preserved).
# NOT authoritative and will age: it documents what shipped with the pinned CLI, not the
# live account's available models. Keep in lockstep with SUPPORTED_VERSIONS when bumping
# the CLI.
KNOWN_MODEL_SLUGS: tuple[str, ...] = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "codex-auto-review",
)

# Cache TTL for the `codex exec --help` probe, so a long-lived server re-probes
# after an in-place CLI upgrade instead of trusting a stale snapshot forever.
HELP_CACHE_TTL_SECONDS = 300

# --- Supported `codex` major version(s) -----------------------------------------
# Codex is pre-1.0 and ships as 0.x; the "feature" version is the minor (0.144.x).
# We track the minor as the compatibility axis and keep the env override so a user
# can opt into an untested version themselves. Advisory only: a mismatch warns but
# never blocks (auth + binary presence decide readiness).
SUPPORTED_VERSIONS = frozenset({(0, 144)})
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
# NOTE: codex 0.144 removed the token_count event that once carried the rate-limit quota
# block on this stream (#321). Quota is now read from the app-server (see the
# APP_SERVER_RATE_LIMITS_* constants above), not scraped from these events.
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
    # A `--disable/--enable <FEATURE>` whose feature name codex no longer knows (e.g. an
    # upstream rename/removal of remote_plugin) prints this instead of a clap arg-parse error;
    # matching it keeps the remote_plugin isolation guarantee fail-closed as cli_contract_changed
    # rather than a confusing nonzero_exit (#287).
    "unknown feature flag",
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


def is_reasoning_effort_rejection(*texts: str | None) -> bool:
    """Whether the provided texts carry the backend's bad-reasoning-effort signature.

    True only for the request-level rejection of an effort VALUE: every marker in
    REASONING_EFFORT_REJECTION_MARKERS present in its bracketed `[…]` field form.
    A marker as a free substring (an operator passthrough naming it) does not
    match, and a rejection naming only the config key is contract drift and
    deliberately does not match either."""
    blob = "\n".join(t for t in texts if t).lower()
    return all(f"[{marker}]" in blob for marker in REASONING_EFFORT_REJECTION_MARKERS)


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
