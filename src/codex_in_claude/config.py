"""Config knobs: env defaults, clamps, tier/sandbox/isolation -> codex flags."""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pontonier.core import redaction, worktree
from pontonier.core.jobs import JobStore

from codex_in_claude import cli_contract
from codex_in_claude.prompts import (
    contains_framing_marker as contains_framing_marker,  # noqa: PLC0414 — re-export
)

# This bridge's pinned worktree knobs. The values predate the pontonier extraction
# and are git-visible (temp-dir names a job runner constrains cleanup to; baseline
# commit authorship in delegate worktree history), so they must never drift.
WORKTREE_CONFIG = worktree.WorktreeConfig(
    prefix="cic-worktree-",
    identity_name="codex-in-claude",
    identity_email="codex-in-claude@local",
)

ENV_PREFIX = "CODEX_IN_CLAUDE_"

MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS = 10, 600
DEFAULT_TIMEOUT_SECONDS = 300
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
    reasoning_effort: str | None
    timeout_seconds: int


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Shape bounds for a reasoning-effort VALUE (#309), shared by the MCP params (which
# advertise and enforce them at the call boundary) and the pre-spend check on the
# resolved value — the only guard the CODEX_IN_CLAUDE_REASONING_EFFORT env default
# passes through, since env config never crosses the MCP boundary. The set stays open
# (the backend judges the value); these exclude only argv/serialization-hostile
# shapes: a NUL breaks Popen outright, other control characters have no place in a
# config override, an unpaired surrogate cannot be UTF-8-encoded (it breaks both argv
# encoding and envelope serialization), and an argv-scale string fails as a misleading
# codex_not_found. "Control character" means Unicode category Cc — C0, DEL, and the
# C1 block (U+0080-U+009F) alike; "surrogate" is category Cs (U+D800-U+DFFF). Real
# efforts are ≤ ~7 chars; 128 is generous headroom.
REASONING_EFFORT_MAX_LENGTH = 128
# The advertised JSON-Schema `pattern` for a string that must carry no Unicode Cc code point.
# One home for that fact: the reasoning-effort bound below and the #529 machine-identifier
# parameters (`field_policy.REJECT_PARAMS`) are the same requirement, so they share the literal
# rather than each spelling it out.
#
# ECMA-safe for the advertised JSON-Schema `pattern` (no \Z, which ECMA lacks).
# Deliberately does NOT name the surrogate range: under a non-`u`-flag ECMA engine a
# [\uD800-\uDFFF] class also matches the code UNITS of astral characters — legitimate
# values — so publishing it would make spec-compliant client validators reject them.
# A compliant UTF-8 JSON transport cannot deliver an unpaired surrogate anyway; the
# character-wise check below closes the residual (env defaults, in-process calls,
# lenient parsers).
CONTROL_CHAR_FREE_PATTERN = r"^[^\x00-\x1F\x7F-\x9F]*$"
REASONING_EFFORT_VALUE_PATTERN = CONTROL_CHAR_FREE_PATTERN


def reasoning_effort_shape_error(value: str) -> str | None:
    """Why `value` fails the reasoning-effort shape bounds, or None when it passes.

    Value-free (safe for an error message). Checked character-wise, not via the
    regex, so a trailing newline — which Python's `$` would admit — is caught too."""
    if len(value) > REASONING_EFFORT_MAX_LENGTH:
        return f"exceeds {REASONING_EFFORT_MAX_LENGTH} characters"
    if any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in value):
        return "contains a control character"
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        return "contains a surrogate code point"
    return None


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
        reasoning_effort=os.environ.get(f"{ENV_PREFIX}REASONING_EFFORT") or None,
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

# The allowlisted global options — all value-taking, and all verified `codex` global+exec options
# (re-probed against codex-cli 0.148.0 on 2026-08-19: every form below parses, while a bare or
# unknown flag is rejected as `unexpected argument`). THIS parser accepts short `-c`/`-p` only
# space-separated — the attached `-cKEY=VAL` is refused here as an unknown flag, because the
# attached-form split below fires only on long `--flag=value`. That is a deliberate narrowing of
# ours, NOT a CLI limit: codex itself accepts `-cKEY=VAL` (clap's attached short-option value),
# re-probed on 0.148.0 on 2026-08-19. The narrowing is safe-direction — we pass
# through strictly less than codex would take — so keep it, but do not restate it as a codex fact.
# The long forms accept both `--config VAL` and `--config=VAL`.
_EXTRA_CONFIG_FLAGS = ("-c", "--config")  # -c KEY=VALUE  (a dotted-path config override)
_EXTRA_PROFILE_FLAGS = ("-p", "--profile")  # -p NAME       (layer a named config profile)
_EXTRA_FEATURE_FLAGS = ("--enable", "--disable")  # --enable/--disable FEATURE

# Feature NAMES that are wholly plugin-owned, refused even though `--enable`/`--disable`/`-c`
# are allowlisted. The plugin forces each of these off on every model-bearing call
# (`cli_contract.MODEL_RUN_DISABLED_FEATURES` — the one inventory both this denylist and the
# argv builder derive from), so an operator override must not touch the feature in EITHER
# direction. `--enable` would be defeated anyway (`--disable` outranks it in any order), so
# allowing it would only be a silent no-op; `--disable` is redundant with the plugin but, if
# allowed, injects a passthrough descriptor that could misattribute a plugin-owned flag drift
# (an upstream rename → `Unknown feature flag`) to CODEX_IN_CLAUDE_EXTRA_ARGS — so both are
# refused. `--enable X` is exactly `-c features.X=true`, so the `-c` spellings are denied too
# (see _plugin_owned_feature_for_key below). NOTE: an opaque `--profile` can still re-enable
# them — the same documented operator-trust boundary that bounds the `-c` denials (see
# COMPATIBILITY.md).
_PLUGIN_OWNED_FEATURES = frozenset(cli_contract.MODEL_RUN_DISABLED_FEATURES)
# Why each owned feature is refused, in the operator's terms. remote_plugin is a documented
# SECURITY guarantee (#287); sleep_tool is SPEND hygiene (#587) and its refusal must not
# borrow the security wording. Keyed by feature so a new inventory entry without a reason
# fails the completeness test rather than emitting a wrong explanation.
_PLUGIN_OWNED_FEATURE_REASONS: dict[str, str] = {
    cli_contract.REMOTE_PLUGIN_FEATURE: (
        "the plugin disables the remote_plugin connectors as a security guarantee (#287); "
        "an operator override cannot re-enable them"
    ),
    cli_contract.SLEEP_TOOL_FEATURE: (
        "the plugin disables the sleep_tool feature on every model-bearing run so a native "
        "sleep (up to 12h) cannot burn the run's budget into a timeout (#587); an operator "
        "override cannot re-enable it"
    ),
}
# Both the dotted keys AND the bare `features` parent table are refused: `-c
# features={remote_plugin=true}` (a TOML inline table) reaches the same setting through the
# parent key, so denying only the dotted form leaves that inline-table bypass open. Denying
# bare `features` refuses the whole-table inline form; a different feature is still settable
# via its own dotted key (`-c features.some_other=true`), which no owned prefix matches.
_FEATURES_NAMESPACE = "features"


def _plugin_owned_key_denial(normalized: str, key: str) -> ExtraArgs | None:
    """The refusal for a `-c` KEY that reaches a plugin-owned feature, else None.

    The bare `features` parent table can reach EVERY owned feature at once, so its refusal
    names all of them; a dotted key names the one feature (and reason) it reaches."""
    if normalized == _FEATURES_NAMESPACE:
        owned_list = ", ".join(cli_contract.MODEL_RUN_DISABLED_FEATURES)
        return ExtraArgs(
            configured=True,
            error=(
                f"config key '{_safe_token(key.strip())}' is refused: the bare "
                f"features table can reach the plugin-owned features ({owned_list}) "
                "that the plugin forces off on every model-bearing run (#287, #587); "
                "set another feature by its own dotted key instead"
            ),
        )
    owned_feature = _plugin_owned_feature_for_key(normalized)
    if owned_feature is None:
        return None
    return ExtraArgs(
        configured=True,
        error=(
            f"config key '{_safe_token(key.strip())}' is refused: "
            f"{_PLUGIN_OWNED_FEATURE_REASONS[owned_feature]}"
        ),
    )


def _plugin_owned_feature_for_key(normalized: str) -> str | None:
    """The plugin-owned feature a normalized `-c` KEY reaches, or None.

    Matches `features.<owned>` and every dotted DESCENDANT (`features.sleep_tool.mode` is
    the exposure gate itself) on a segment boundary, so `features.sleep_toolbox.mode` and
    `features.sleep_tool_mode` — different keys sharing the prefix — stay allowed."""
    for feature in cli_contract.MODEL_RUN_DISABLED_FEATURES:
        owned = f"{_FEATURES_NAMESPACE}.{feature}"
        if normalized == owned or normalized.startswith(f"{owned}."):
            return feature
    return None


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

# Config keys refused because they would contradict provenance the result envelope reports
# (#310, #309): each has first-class, meta-reported controls — a per-call parameter and a
# CODEX_IN_CLAUDE_* env default — which flow into resolved_defaults and the named meta field.
# A passthrough `-c model=…` (or `-c model_reasoning_effort=…`) would run on the operator's
# value while the meta field still reports the per-call/server value (null in the common
# case). Deliberately EXACT keys, not a new root in _DENIED_CONFIG_KEY_ROOTS: the root
# machinery's `model_` prefix match would also refuse `model_provider` — the passthrough's
# motivating use case (#231, above) — and `model_verbosity`, which stay allowed. The check
# runs on the normalized key, so it also refuses lookalike spellings (`Model`, quoted
# segments) that codex's own `-c` parser — a naive '.'-split with literal, case-sensitive
# segments — would treat as junk keys rather than the real key: deliberate, harmless
# over-denial matching the #287 treatment. NOTE: an opaque `--profile` can still set these —
# the same documented operator-trust boundary that bounds every `-c` denial
# (COMPATIBILITY.md). Values are (meta field, env var, per-call parameter, issue) used to
# build the value-free refusal message.
# Instruction-bearing config keys refused because they would place operator prose ABOVE this
# server's framing (#555). Every framing string in `prompts` — including the untrusted-data
# clause — rides the user turn on stdin; `developer_instructions` lands as the FIRST
# developer-role message (verified with `codex debug prompt-input` on 0.151.0), and
# `model_instructions_file` (with its deprecated `experimental_instructions_file` alias)
# REPLACES the built-in instructions outright (documented; the prompt-input renderer does not
# show base instructions, so that one is denied on the documented semantics). `instructions` is
# documented "reserved for future use" — denied so a future meaning cannot fail open.
# `model_catalog_json` is an indirection to the same place (Codex review of #555): a catalog
# entry carries `base_instructions` / `model_messages.instructions_template` for its slug, so an
# operator catalog can redefine the selected model's built-in instructions. Meta
# records only that a valid passthrough was configured (`extra_args_configured`/`_count`/
# `_valid`), never which keys, so a run under any of these would be indistinguishable from a
# default run. Exact normalized keys, like _RESERVED_META_CONFIG_KEYS (a root would not fit:
# `instructions` is also a legitimate nested segment, e.g. `mcp_servers.X.instructions`), with
# the same conservative lookalike over-denial. A first-class, meta-reported per-call parameter
# is tracked in #556; until it lands there is no replacement control to point at. NOTE: an
# opaque `--profile`, and at the default `inherit` isolation the user's own `config.toml`, can
# still set these — the documented operator-trust boundary (COMPATIBILITY.md).
_DENIED_INSTRUCTION_CONFIG_KEYS = frozenset(
    {
        "developer_instructions",
        "model_instructions_file",
        "experimental_instructions_file",
        "instructions",
        "model_catalog_json",
    }
)
# --- Caller developer instructions bounds (#556) --------------------------------------
# The boundary half of the parameter: prompts.py owns the framing/markers/composition;
# these bounds are enforced once in the server's _prepare_* (on the normalized string)
# and mirrored in backend.CodexBackend.validate_request for direct adapter callers.

# Small on purpose: the text crosses from the untrusted request tier into the model's
# developer turn, so it is for a stance or focus directive, not for smuggling a payload
# past the input caps. Bytes, not characters.
MAX_DEVELOPER_INSTRUCTIONS_BYTES = 4096


def normalize_developer_instructions(text: str | None) -> str | None:
    """The one place caller developer-instruction text is canonicalized.

    Callers normalize BEFORE validating, hashing, persisting, or sending, so the bytes
    counted against the cap, the bytes hashed into meta, the bytes in the job spec, and
    the bytes that reach codex are the same string. Blank normalizes to None: it sends
    no developer override at all, so recording a fingerprint for it would attest a
    non-default run for a default one."""
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def developer_instructions_unsafe_reason(text: str) -> str | None:
    """Why `text` cannot be carried at all, or None when it can.

    A NUL is refused as instruction CONTENT policy, not an argv hazard: the TOML-string
    encoding in the builder would carry it as an escape and deliver a control character
    to the model's instruction layer (see the encoder note in codex.build_exec_command).
    Other C0 controls (except tab/LF/CR) and DEL are refused for the same reason plus
    a transport one: json.dumps does not escape U+007F, which TOML 1.0 forbids in a
    basic string — codex 0.151.0 tolerates it, but a stricter upstream parser would
    turn it into a config-load failure (Opus review). A lone surrogate is the hard
    transport hazard: it cannot be UTF-8-encoded, so Popen's argv encoding would raise
    after validation, unclassified."""
    if "\x00" in text:
        return "contains a NUL byte"
    if any((ch <= "\x1f" and ch not in "\t\n\r") or "\x7f" <= ch <= "\x9f" for ch in text):
        return "contains a control character (C0 other than tab/newline/CR, DEL, or C1)"
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return "is not valid UTF-8 (lone surrogate)"
    return None


_RESERVED_META_CONFIG_KEYS: dict[str, tuple[str, str, str, str]] = {
    "model": ("meta.model", f"{ENV_PREFIX}MODEL", "model", "#310"),
    cli_contract.MODEL_REASONING_EFFORT_CONFIG_KEY: (
        "meta.reasoning_effort",
        f"{ENV_PREFIX}REASONING_EFFORT",
        "reasoning_effort",
        "#309",
    ),
}


@dataclass(frozen=True)
class ExtraArgs:
    """Parsed CODEX_IN_CLAUDE_EXTRA_ARGS. `tokens` is the validated argv to inject
    (may carry secret `-c` VALUES — never echo it). `descriptors` are RAW identifiers
    (allowlisted flag names, config KEYS, profile/feature NAMES — never a `-c` value)
    used to match against a codex drift stderr.

    Raw is deliberate and load-bearing: a descriptor is an IDENTITY compared with codex's
    own rejection text, which quotes the operator's name with its exact spelling, so
    stripping or bounding one here changes what it matches and misattributes the
    operator's bad passthrough to plugin contract drift (#528). They are therefore NOT
    safe to surface as-is: every emission site must run them through `_safe_token` (or
    `codex._safe_echo`), which sanitizes and bounds a single-token echo.

    `error` is a value-free 'why invalid' string set only when the
    knob is present but failed to parse/validate; `configured` is True whenever the env
    var is set to a non-blank value."""

    tokens: tuple[str, ...] = ()
    descriptors: tuple[str, ...] = ()
    # The `-c` KEYS and `--profile` NAMES on their own, split out of the flat
    # `descriptors` blob (which mixes them with flag names) so an ownership question can
    # be asked precisely (#524). Matching a strict-config rejection against `descriptors`
    # would let the SHARED `-c` flag token — recorded for every config entry, and present
    # in codex's own rejection text — answer "yes" for a key that is really the plugin's.
    config_keys: tuple[str, ...] = ()
    profile_names: tuple[str, ...] = ()
    option_count: int = 0
    configured: bool = False
    error: str | None = None

    @property
    def valid(self) -> bool:
        """True when the knob is unset, or set and parsed/validated cleanly."""
        return self.error is None

    def owns_config_key(self, key: str) -> bool:
        """Whether `key` — as codex echoed it in a rejection — is one of OUR `-c` KEYS.

        Both sides are canonicalized with the same conservative normalization the
        denylist uses, so a cased/quoted/spaced echo still matches. A dotted DESCENDANT
        of one of our keys is ours too: a `-c t={k=v}` parent-table assignment is echoed
        by codex as the child path `t.k` (probed on 0.149.1, #550), and only whole
        segments count — `t.k` never owns `t.kx`. False whenever the knob is unset or
        failed to parse: an unattributable rejection must stay fail-loud rather than be
        blamed on a passthrough that never reached codex."""
        if not (self.configured and self.valid):
            return False
        target = _normalize_config_key(key)
        for own in self.config_keys:
            own_n = _normalize_config_key(own)
            if target == own_n or target.startswith(own_n + "."):
                return True
        return False

    def owns_profile_file(self, path: str | None) -> bool:
        """Whether `path` is the config FILE codex loads for a profile WE select.

        `--profile NAME` loads `$CODEX_HOME/NAME.config.toml` and, under
        `--strict-config`, validates it; an unselected `NAME.config.toml` is never read
        (both verified live on codex-cli 0.148.0, re-verified on 0.149.1). Matching on the
        basename keeps this
        independent of where `$CODEX_HOME` resolves."""
        if not (self.configured and self.valid) or not path:
            return False
        base = Path(path.strip()).name
        return any(base == f"{name}.config.toml" for name in self.profile_names)


def _extra_args_flag_kind(flag: str) -> str | None:
    if flag in _EXTRA_CONFIG_FLAGS:
        return "config"
    if flag in _EXTRA_PROFILE_FLAGS:
        return "profile"
    if flag in _EXTRA_FEATURE_FLAGS:
        return "feature"
    return None


def _safe_token(token: str) -> str:
    """A bounded, sanitized echo of an offending token for an error message.

    `sanitize_echo` strips control characters before redacting; the ordering and the reason
    for it live there (#528). The 60-character bound is this bridge's, and it comes LAST so
    a secret straddling the cut still had its tail when the redactor saw it."""
    return redaction.sanitize_echo(token)[:60]


def _normalize_config_key(key: str) -> str:
    """Conservatively canonicalize a dotted `-c` config KEY for denylist matching: trim each
    dotted segment, strip surrounding TOML quotes, and lowercase. This is deliberate
    OVER-matching, not a mirror of codex — codex's own `-c` parser (config_override.rs,
    verified at rust-v0.144.3) trims the whole key, then does a naive '.'-split with each
    segment used literally: case-sensitive, no quote handling. So a spaced/cased/quoted
    variant (`features . Remote_Plugin`, `features."remote_plugin"`, `"features".remote_plugin`)
    is a junk key codex's config never reads, not an alias of the real one; refusing such
    lookalikes anyway costs nothing and keeps the denylist unprobeable (#287, #312). shlex
    strips unescaped quotes before this, but an escaped/preserved quote can survive to here."""
    segments = []
    for seg in key.split("."):
        segments.append(seg.strip().strip("\"'").strip().lower())
    return ".".join(segments)


def _instruction_key_denial(normalized: str, key: str) -> ExtraArgs:
    """The refusal for an instruction-bearing `-c` key (#555), split by disposition:
    `developer_instructions` HAS a first-class home (#556) — the per-call parameter,
    which frames the text and records {sha256, bytes} in meta (deliberately no env
    var: the value is per-call caller data, not operator state) — while the file- and
    catalog-shaped keys replace instructions wholesale and get no control on purpose."""
    if normalized == "developer_instructions":
        return ExtraArgs(
            configured=True,
            error=(
                f"config key '{_safe_token(key.strip())}' is refused: a raw "
                "override would place operator prose above this server's "
                "framing with no record in the result envelope; use the "
                "developer_instructions parameter on codex_consult / "
                "codex_review_changes (and their _async twins) instead (#556)"
            ),
        )
    return ExtraArgs(
        configured=True,
        error=(
            f"config key '{_safe_token(key.strip())}' is refused: it could "
            "replace or redefine model instructions wholesale (or is "
            "reserved for that), and no first-class control exists for it "
            "on purpose (#555; rationale in COMPATIBILITY.md)"
        ),
    )


def _parse_extra_args(raw: str) -> ExtraArgs:
    """Tokenize + allowlist-validate a non-blank CODEX_IN_CLAUDE_EXTRA_ARGS value."""
    try:
        toks = shlex.split(raw)
    except ValueError:
        return ExtraArgs(configured=True, error="could not tokenize (unbalanced quotes?)")
    tokens: list[str] = []
    descriptors: list[str] = []
    config_keys: list[str] = []
    profile_names: list[str] = []
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
            # One canonicalization for every denylist check (#312): normalize the whole key
            # once and derive the root from it, so a quoted root ('"sandbox_mode"=…') is
            # refused with the same conservative over-matching as the exact-key denials
            # below (see _normalize_config_key — in codex those spellings are junk keys,
            # so this closes a silently-accepted no-op, not a sandbox bypass).
            normalized = _normalize_config_key(key)
            root = normalized.split(".", 1)[0]
            if any(root == d or root.startswith(f"{d}_") for d in _DENIED_CONFIG_KEY_ROOTS):
                return ExtraArgs(
                    configured=True,
                    error=(
                        f"config key '{_safe_token(key.strip())}' is refused: it could weaken the "
                        "sandbox / network / approval / host-env-isolation guarantees this "
                        "server advertises"
                    ),
                )
            if (owned_denial := _plugin_owned_key_denial(normalized, key)) is not None:
                return owned_denial
            if normalized in _DENIED_INSTRUCTION_CONFIG_KEYS:
                return _instruction_key_denial(normalized, key)
            reserved = _RESERVED_META_CONFIG_KEYS.get(normalized)
            if reserved is not None:
                meta_field, env_var, param, issue = reserved
                return ExtraArgs(
                    configured=True,
                    error=(
                        f"config key '{_safe_token(key.strip())}' is reserved — it would "
                        f"contradict the provenance reported in result envelopes "
                        f"({meta_field}); set "
                        f"{env_var} or the per-call {param} parameter instead ({issue})"
                    ),
                )
            tokens += [flag, value]
            # Record the flag too (not just the key), so a drift where codex rejects the
            # `-c`/`--config` flag token itself is still attributed to the passthrough.
            # The key is a config-path name (not a secret); the `-c` VALUE is never added.
            # Stored RAW on purpose. A descriptor is an IDENTITY matched against codex's
            # own rejection text, not display text: sanitizing or bounding it here changes
            # what it matches, so a control-bearing or >60-char name stops matching codex's
            # raw spelling and its rejection is misattributed to plugin drift
            # (`cli_contract_changed`) instead of the operator's passthrough. Sanitation
            # belongs at EMISSION — see `codex._extra_args_rejected_error` (#528).
            descriptors += [flag, key]
            config_keys.append(key)
        else:  # profile / feature — the value is a non-secret NAME
            if not value:
                return ExtraArgs(configured=True, error=f"{flag} requires a non-empty value")
            if kind == "feature" and value.strip().lower() in _PLUGIN_OWNED_FEATURES:
                return ExtraArgs(
                    configured=True,
                    error=(
                        f"feature '{_safe_token(value.strip())}' is managed by the plugin "
                        f"and cannot be set via {EXTRA_ARGS_ENV} (enable or disable): "
                        f"{_PLUGIN_OWNED_FEATURE_REASONS[value.strip().lower()]}"
                    ),
                )
            tokens += [flag, value]
            descriptors += [flag, value]
            if kind == "profile":
                profile_names.append(value)
        count += 1
        i += 1
    # De-dupe descriptors while preserving order (a stable, small match/echo set).
    seen: dict[str, None] = {}
    for d in descriptors:
        seen.setdefault(d, None)
    return ExtraArgs(
        tokens=tuple(tokens),
        descriptors=tuple(seen),
        config_keys=tuple(dict.fromkeys(config_keys)),
        profile_names=tuple(dict.fromkeys(profile_names)),
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
        cleanup_prefix=WORKTREE_CONFIG.prefix,
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


def rate_limit_stale_seconds() -> int:
    """Age (seconds) past which a cached snapshot is flagged is_stale. Advisory only —
    the reset-aware interpretation, not this threshold, is the real staleness guard."""
    raw = os.environ.get(f"{ENV_PREFIX}RATE_LIMIT_STALE_SECONDS")
    if raw and raw.isdigit():
        return int(raw)
    return 1800  # 30 minutes


def worktree_base() -> Path | None:
    """Optional override for where temp worktrees are created (default: alongside
    the repo, managed by git). None means let the worktree module choose."""
    override = os.environ.get(f"{ENV_PREFIX}WORKTREE_BASE")
    return Path(override).expanduser() if override else None
