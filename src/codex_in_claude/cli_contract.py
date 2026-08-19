"""Single source of truth for the external `codex` CLI contract.

Every assumption this server makes about the `codex` CLI — its subcommands,
flags, sandbox values, the event/result extraction surface, supported major
versions, and the stderr phrasings that mean the contract drifted — lives here so
an upstream breaking change is centralized, greppable, and testable. Revising it
takes the lockstep procedure in docs/UPGRADING-CODEX.md, not an edit to this file
alone. See COMPATIBILITY.md for the assumption -> upstream-source map.

Verified against `codex-cli 0.148.0`.
"""

from __future__ import annotations

import re

from pontonier.backend import contract as _pontonier_contract

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
# codex-cli 0.148.0 on 2026-08-19 via `codex app-server generate-json-schema --out <DIR>`
# (the generator requires an --out directory instead of writing to stdout). The 0.147.0 -> 0.148.0
# diff added three v2 messages we do NOT consume (`NullableGetAccountTokenUsageParams`,
# `ThreadQueueChangedNotification`, `ThreadRevertedNotification`) and left every consumed
# schema byte-identical after canonicalization. The 0.146.0 -> 0.147.0
# schema diff was additive only for the surface consumed here: an optional `extensions` map on
# `InitializeParams` (MCP extension settings declared by the client; we do not send it, and the
# pre-existing form-elicitation capability beside it is now documented as its legacy alias), an
# optional `title` on the import progress/completed per-item results (read tolerantly, ignored),
# and two values added to the `PlanType` enum — `self_serve_business_prolite` and
# `enterprise_cbp_automation` (read as a free-form capped string, not an enum — see
# RATE_LIMIT_PLAN_TYPE_MAX_BYTES). Ten new v2 messages appeared (`ThreadSection*{Params,Response}`
# for create/delete/list/move/update); none is consumed.
#
# The generated schemas this plugin actually consumes — the files to diff on an upgrade, out of
# the ~270 the generator emits (see docs/UPGRADING-CODEX.md step 2A; paths are relative to the
# generator's --out directory). This list tracks the code below, not a codex version: adding or
# dropping an app-server read changes it.
#   v1/InitializeParams.json                                 (sent: clientInfo, capabilities)
#   v1/InitializeResponse.json                               (read: codexHome)
#   v2/ExternalAgentConfigImportParams.json                  (sent)
#   v2/ExternalAgentConfigImportResponse.json                (read)
#   v2/ExternalAgentConfigImportProgressNotification.json    (read)
#   v2/ExternalAgentConfigImportCompletedNotification.json   (read)
#   v2/GetAccountRateLimitsResponse.json                     (read; see the rate-limits block below)
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
# against codex-cli 0.148.0 on 2026-08-19 via `codex app-server` (live read, the `integration`
# suite's rate-limits roundtrip) and the generated schema. See #321, COMPATIBILITY.md.
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
# Backend-enforced SPEND control (0.145+, #359) — distinct from a rate/usage window: waiting for
# a window to reset does not clear it. Upstream types it `boolean | null` and documents null as
# "unavailable, not a sparse-update recovery", so the tri-state is load-bearing: only an explicit
# `true` is a block, and null/absent (every 0.144 response) means the backend did not say.
RATE_LIMIT_SPEND_CONTROL_REACHED_KEY = "spendControlReached"
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
# Nor `--approve-for-me` (added in 0.147.0, still present at 0.148.0), which routes approval
# requests through an automatic
# review under the workspace-write sandbox: it would let a read-only-tier run acquire write
# capability without the caller electing a write tier, so it stays deliberately unadopted.
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

# --- Implicit Codex context (issues #300, #358, #472) ----------------------------
# `codex exec` automatically loads guidance from THREE `AGENTS.md` sources into model
# context and auto-discovers skills from TWO roots (per upstream docs: name/description
# metadata up front, a skill's body when it is selected):
#   - the resolved workspace's own `AGENTS.md`, and
#   - INSIDE a repository, every ancestor `AGENTS.md` from the workspace up to the
#     repository root — the walk crosses ABOVE the directory the caller selected and
#     stops AT the repository root without crossing it, so narrowing `workspace_root` to
#     a subdirectory to bound egress still ships the repo-root file. Outside a
#     repository there is no walk: only the workspace's own file loads (#472), and
#   - `$CODEX_HOME/AGENTS.override.md`, else `$CODEX_HOME/AGENTS.md` — user-global, on
#     EVERY call from ANY workspace, the `AGENTS.md` twin of the global skills root
#     below. `-c project_doc_max_bytes=0` suppresses the first two and NOT this one
#     (#472); `--ignore-user-config` suppresses none of the three.
#   - the workspace's `.agents/skills/` (project-level), and
#   - `$CODEX_HOME/skills/` (default `~/.codex/skills/`) — user-global, discovered from
#     OUTSIDE the workspace, so no workspace choice excludes it (#358).
# The CALLER directs none of it, and every model-bearing call here runs `codex exec` —
# so that content can reach OpenAI even when the caller's prompt never mentions those
# files. The two halves arrive differently: `AGENTS.md` content and skill name/description
# are already in context when the turn begins — codex reads them itself while assembling
# the prompt, so the MODEL issues no read for them — while a selected skill's BODY was
# observed arriving via a read the MODEL issues (0.147.0, 0.148.0). Both are unrequested egress;
# only the first is auto-loading. Verified empirically against codex-cli 0.148.0 (2026-08-19)
# via marker probes — including an A/B against 0.147.0, whose presence matrix was
# identical; the global-skill discovery is pre-existing, not a 0.148 regression.
# Marker probes are the only way to see any of this;
# invisible in `codex exec --help` (no flag, no subcommand), so the mechanical
# help-drift check CANNOT catch upstream changes to it. The isolation flags do NOT
# suppress it: `--ignore-user-config` drops `$CODEX_HOME/config.toml` and
# `--ignore-rules` drops execpolicy `.rules`; neither touches `AGENTS.md` or EITHER
# skills root — a probe under `--ignore-user-config` still discovered a `$CODEX_HOME/skills/`
# skill by name, and its body still reached the model once selected. Upstream docs:
# https://developers.openai.com/codex/concepts/customization#agents-guidance and
# https://developers.openai.com/codex/concepts/customization#skills.
# Reader-facing detail — the re-verification probe, the verified negatives, and what
# remains unverified — lives in COMPATIBILITY.md, "Implicit Codex context"; keep that
# section the single home for all of it. Do not re-list its probe results here: they are
# expected to change on the next Codex upgrade.
#
# RULE: every egress-caveat prose site — the server instructions, the codex_status
# caveat, the tool capability descriptions and docstrings, codex_capabilities'
# negative_scope, README.md, COMPATIBILITY.md, SECURITY.md, and the
# collaborating-with-codex skill — must disclose BOTH skills roots. No feature-detection
# logic exists here by design. The canonical sentence pair every code-side site imports
# lives just below (SKILLS_DISCOVERY_FACT / SKILLS_DISCOVERY_FACT_FULL / SKILL_BODY_FACT);
# the doc-side sites restate it in their own prose instead, checked for the same two roots
# by tests/test_docs_disclosure.py. Each site must also state HOW a skill's body arrives
# (SKILL_BODY_FACT) — calling the whole thing "auto-loading" is the #498/#501 defect.

# Canonical wording for the disclosure the RULE above requires (#427). Every code-side site
# it names imports these rather than hand-copying the fact in its own words, so a Codex
# upgrade that changes this behavior has exactly one place in code to fix — a stale hand-copy
# is precisely the drift risk the RULE exists to prevent. `SKILLS_DISCOVERY_FACT` states the
# discovery alone (both roots, the default path, and that the global root is reachable from
# outside the workspace); `SKILLS_ISOLATION_NOTE` is the separate isolation-flags sentence,
# appended only where a site's disclosure needs it — the three `_async` tool docstrings
# deliberately carry a lighter subset (see `_REQUIRED_GUARANTEES` in tests/test_server.py) and
# use the fact alone, everywhere else uses `SKILLS_DISCOVERY_FACT_FULL`. The six egress tool
# docstrings can't import these directly: FastMCP captures `fn.__doc__` eagerly at decoration
# time (`wrapper.__doc__ = getattr(fn, "__doc__", ...)` in its FunctionTool), so a docstring
# can't interpolate an f-string and still register as `__doc__`. Those six are hand-copied
# literal text instead, pinned against drift by tests/test_server.py's
# `test_sync_tool_docstring_matches_full_skills_discovery_constant` /
# `test_async_tool_docstring_matches_fact_only_not_full`.
SKILLS_DISCOVERY_FACT = (
    "Codex auto-loads the resolved workspace's AGENTS.md and, in a repository, ancestor "
    "AGENTS.md files through its root, plus a user-global $CODEX_HOME/AGENTS.override.md "
    "or AGENTS.md; it discovers skills in the workspace's .agents/skills/ and user-global "
    "$CODEX_HOME/skills/ (default ~/.codex/skills/), reachable from outside the workspace."
)
SKILLS_ISOLATION_NOTE = "The plugin's isolation flags don't suppress any of it."
# The mechanism half of the same disclosure (#480/#501). The two halves of the implicit
# context arrive DIFFERENTLY, and the difference is security-relevant: `AGENTS.md` content
# is auto-LOADED (already in context before the turn), while a skill is auto-DISCOVERED as
# name and description only — its BODY follows a read the MODEL itself issues once it
# selects the skill. Calling the whole thing "auto-loading" contradicts that, which is the
# defect #498 corrected in prose and #501 corrects on the wire. Every carrier site states
# it; the six egress docstrings hand-copy it for the FastMCP reason above, pinned by
# tests/test_server.py.
SKILL_BODY_FACT = (
    "A skill's name and description arrive up front; selecting one makes the model read "
    "its body, which can reach OpenAI even if your inputs never mention it."
)
SKILLS_DISCOVERY_FACT_FULL = f"{SKILLS_DISCOVERY_FACT} {SKILLS_ISOLATION_NOTE}"

# The whole disclosure in one string, for a carrier that has room for exactly one — the
# declarative `BackendContract.implicit_context_disclosure`, whose own contract is "what
# the CLI auto-loads that isolation cannot suppress". That framing is precisely what
# #501 corrects: half of what arrives is not auto-loaded at all, so the mechanism has to
# travel with the discovery fact or a contract consumer re-learns the wrong model.
IMPLICIT_CONTEXT_DISCLOSURE = f"{SKILLS_DISCOVERY_FACT_FULL} {SKILL_BODY_FACT}"

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
# `codex exec` 0.148.0 has no dedicated reasoning-effort flag (verified against
# `codex exec --help` 2026-08-19; the 0.147.0 -> 0.148.0 diff adds no exec flag at all —
# only the `codex exec fork` subcommand, which we never invoke); the only route is the
# `model_reasoning_effort`
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
# pontonier.core.jsoncache). The real file is ~150 KB; 1 MB is generous headroom.
MODELS_CACHE_MAX_BYTES = 1_000_000
MODELS_CACHE_MAX_ENTRIES = 256  # ignore anything past this many model entries
# A conservative slug shape; entries failing it are dropped (defends against a
# malformed/hostile cache surfacing junk to an agent).
MODEL_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Bundled advisory fallback used ONLY when the on-disk cache is absent/unreadable.
# Copied from codex-cli 0.148.0's models_cache.json on 2026-08-19 (cache order preserved);
# `gpt-5.6-sol-wm` was DROPPED in that refresh (it had been added in the 0.147.0-era refresh).
# The catalog is served by the backend, not shipped in the binary, so a slug CAN appear or vanish
# without a CLI release. Whether THIS drop was such a move is not established: the only observation
# is the 0.148.0-written cache, with no contemporaneous 0.147.0 read to compare (contrast the
# 0.147 addition, where a 0.146.0-written cache already carried the slug). Re-diff the slug set on
# every upgrade anyway — this is the pass that catches it.
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
# Codex is pre-1.0 and ships as 0.x; the "feature" version is the minor (0.148.x).
# We track the minor as the compatibility axis and keep the env override so a user
# can opt into an untested version themselves. Advisory only: a mismatch warns but
# never blocks (auth + binary presence decide readiness).
SUPPORTED_VERSIONS = frozenset({(0, 148)})
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


# --- Shared-library contract (pontonier) -------------------------------------------
# Wire prose that would contradict this contract. Two classes: cross-bridge
# contamination canaries (this code now shares a library with the Kimi and Claude
# bridges, so wrong-direction vocabulary can ride a backport — exactly how
# moonbridge shipped "kimi exec"), and claims of a mechanism this plugin refuses
# (delegate NEVER applies its diff; nothing here bypasses sandbox/approvals).
FORBIDDEN_SURFACE_PHRASES = (
    "kimi",
    "moonbridge",
    "applies the diff to your working tree",
    "--dangerously-bypass",
)

# The declarative half of this contract, in the shared shape the pontonier
# conformance/honesty kits consume. Values are DERIVED from the constants above —
# tests/test_surface_honesty.py pins the derivations so the two can never drift.
# Behavior (command build, classification) lives in codex.py and is reached through
# `backend.CodexBackend` on the pontonier AgentBackend lifecycle, frozen at
# contract_api_version = 1.
PONTONIER_CONTRACT = _pontonier_contract.BackendContract(
    backend_id="codex",
    display_name="Codex",
    bin_name=CODEX_BIN,
    env_prefix="CODEX_IN_CLAUDE_",
    exec_argv_prefix=EXEC_SUBCOMMAND,
    always_send_flags=tuple(sorted(ALWAYS_SEND_FLAGS)),
    help_gated_flags=tuple(sorted(HELP_GATED_FLAGS)),
    forbidden_surface_phrases=FORBIDDEN_SURFACE_PHRASES,
    supported_features=frozenset({"delegate", "transfer", "usage_accounting"}),
    readonly_honesty_statement=(
        "Read-only runs under codex's --sandbox read-only OS sandbox. Redaction of "
        "gathered diffs and returned output is best-effort defense-in-depth; it never "
        "covers supplied inputs or files Codex reads itself."
    ),
    implicit_context_disclosure=IMPLICIT_CONTEXT_DISCLOSURE,
    structured_output="argv_flag",
    model_catalog=_pontonier_contract.ModelCatalog(
        strategy="cache_with_static_fallback",
        model_identifier_authority="advisory",
        effort_metadata_authority="advisory",
    ),
    isolation_policy=_pontonier_contract.IsolationPolicy.SANDBOX_FLAG,
    needs_orphan_sweep=False,
    # Codex rejects a bad effort VALUE loudly via the backend path; only a rename of
    # the `-c model_reasoning_effort` KEY drifts silently, which the UPGRADING-CODEX
    # probe covers. Local validation is therefore shape-only, not enumerated.
    effort_silently_ignored_upstream=False,
    effort_validation="shape_only",
    usage_event_markers=USAGE_EVENT_MARKERS,
    failure_signatures=_pontonier_contract.FailureSignatures(
        auth=tuple(f"(?i){re.escape(p)}" for p in AUTH_FAILURE_PATTERNS),
        contract_drift=tuple(f"(?i){re.escape(p)}" for p in CONTRACT_DRIFT_STDERR_PATTERNS),
        rate_limited=tuple(f"(?i){re.escape(p)}" for p in RATE_LIMIT_PATTERNS),
    ),
)
