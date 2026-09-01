"""Build and run the `codex` CLI invocation; probe version/auth; classify failures."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pontonier.backend.protocol import RunRequest
from pontonier.core import redaction, runtime

from codex_in_claude import (
    binpath,
    cli_contract,
    config,
    normalize,
    preflight,
    prompts,
)
from codex_in_claude.config import isolation_flags
from codex_in_claude.errors import make_error
from codex_in_claude.schemas import ErrorDetail

if TYPE_CHECKING:
    from collections.abc import Callable

    from pontonier.core.runtime import CommandRun

    from codex_in_claude.preflight import FlagSupport
    from codex_in_claude.schemas import ErrorInfo, Meta


@dataclass
class CodexExecResult:
    """Outcome of a `codex exec` run: the raw process result plus the cleanly
    extracted final agent message and the JSONL event text (for tolerant metadata
    parsing)."""

    run: CommandRun
    last_message: str | None
    events: str = ""
    dropped_flags: list[str] = field(default_factory=list)
    # The DISPLAY copy of `codex --version` observed immediately before this run's exec,
    # from the same argv token/cwd/env it was about to spawn (#519). None when the probe
    # could not run or the spawn found no binary. Best-effort observation, never
    # attestation -- see `probe_version_for_run`.
    codex_version: str | None = None


def _gate_optional(tokens: list[str], fs: FlagSupport) -> tuple[list[str], list[str]]:
    """Drop any HELP_GATED flag (and its value) the installed `codex` does not
    advertise. Returns (kept_tokens, dropped_flags). ALWAYS_SEND flags are never in
    HELP_GATED_FLAGS, so they always survive."""
    kept: list[str] = []
    dropped: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        takes_value = cli_contract.HELP_GATED_FLAGS.get(token)
        if takes_value is not None and not preflight.is_supported(token, fs):
            dropped.append(token)
            i += 2 if takes_value else 1
            continue
        kept.append(token)
        i += 1
    return kept, dropped


def reconcile_dropped_model(result: CodexExecResult, meta: Meta) -> None:
    """Reconcile meta.model when --model was dropped by help-gating.

    If the installed `codex` did not advertise --model, `_gate_optional` drops the
    flag and the run proceeds on Codex's default model — not the requested slug. Reset
    meta.model to None (we cannot know the default the CLI picked) so reported
    provenance, and the raw_response.model derived from it, match the model actually
    used rather than the unfulfilled request. The drop is already surfaced in
    meta.compat_warnings (#158)."""
    if cli_contract.MODEL_FLAG in result.dropped_flags:
        meta.model = None


# The two spellings of a config override, in the split form both the builder and
# config._parse_extra_args emit (`-c KEY=VALUE`, never the attached `-cKEY=VALUE`).
_CONFIG_OVERRIDE_FLAGS = ("-c", "--config")


def build_exec_command(
    *,
    cwd: str,
    sandbox: str,
    isolation: str,
    output_last_message_path: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    developer_instructions: str | None = None,
    output_schema_path: str | None = None,
    add_dirs: tuple[str, ...] = (),
    skip_git_repo_check: bool = False,
    ephemeral: bool = True,
    extra_args: tuple[str, ...] = (),
    flag_support: FlagSupport | None = None,
) -> tuple[list[str], list[str]]:
    """Build the `codex exec` invocation. Returns (cmd, dropped_optional_flags).

    The prompt is supplied over stdin (the trailing ``-`` sentinel) by the runner,
    keeping gathered context/diffs out of argv and local process listings.
    Guarantee-bearing flags are sent unconditionally; HELP_GATED (depth) flags are
    dropped when the installed CLI does not list them.

    ``extra_args`` are the operator's allowlist-validated CODEX_IN_CLAUDE_EXTRA_ARGS
    tokens (#231). They are appended AFTER help-gating the plugin-owned tokens — so a
    profile/feature value can never be mistaken for a gated flag's value and dropped —
    and before the stdin ``-`` sentinel, so they can add config/profile/feature options
    without displacing the envelope-bearing flags."""
    fs = flag_support if flag_support is not None else preflight.flag_support()
    tokens = [binpath.codex_bin(), *cli_contract.EXEC_SUBCOMMAND]
    # Set wherever this function appends a real `-c` pair; read by the strict-config
    # decision below. Tracking the appends is what keeps a flag-shaped VALUE from being
    # mistaken for an override (see that comment).
    plugin_config_override = False
    tokens += ["--json"]
    tokens += ["--sandbox", sandbox]
    tokens += ["--cd", cwd]
    tokens += ["--output-last-message", output_last_message_path]
    if ephemeral:
        tokens += ["--ephemeral"]
    # Disable third-party connectors on every model-bearing call, regardless of tier or
    # isolation (codex 0.143+ defaults `remote_plugin` on; #287). Guarantee-bearing and
    # order-independent — `--disable` wins over any operator `--enable`/`-c ...=true`.
    tokens += [cli_contract.DISABLE_FEATURE_FLAG, cli_contract.REMOTE_PLUGIN_FEATURE]
    # Pin the no-network-egress guarantee for workspace-write runs (#518): at `inherit`
    # isolation codex reads $CODEX_HOME/config.toml, where the user's own
    # `network_access = true` would silently grant the delegate egress. The `-c`
    # override outranks the config file AND --profile (verified 0.148.0), so this holds
    # at every isolation level. A config key cannot be help-gated, so upstream renaming
    # the key used to drift silently; the strict-config guard below now catches that
    # (#524) — see the constant's note in cli_contract.
    # Pin the writes-stay-in-the-workspace boundary the same way (#520): the user's own
    # `writable_roots = [...]` in config.toml (or a --profile) would silently widen the
    # sandbox outside the workspace. `[]` is codex's default, so default-config runs are
    # unchanged; the `-c` override outranks the config file AND --profile (verified
    # 0.148.0). Both pins are guarded against a silent KEY rename by the same
    # strict-config flag — see the constants' notes in cli_contract.
    if sandbox == cli_contract.SANDBOX_WORKSPACE_WRITE:
        tokens += ["-c", f"{cli_contract.WORKSPACE_WRITE_NETWORK_ACCESS_CONFIG_KEY}=false"]
        tokens += ["-c", f"{cli_contract.WORKSPACE_WRITE_WRITABLE_ROOTS_CONFIG_KEY}=[]"]
        plugin_config_override = True
    tokens += isolation_flags(isolation)
    if skip_git_repo_check:
        tokens += ["--skip-git-repo-check"]
    # --add-dir rides the FLAG layer, which outranks the writable_roots config pin
    # above (verified 0.148.0) — so a caller passing add_dirs widens the sandbox
    # DESPITE the pin. No model-bearing caller does today; adopting add_dirs on one is
    # a contract change to the writes-stay-in-the-workspace boundary, not just a new
    # argument (per-tuple review under FINGERPRINT_COVERS applies).
    for d in add_dirs:
        tokens += ["--add-dir", d]
    if output_schema_path:
        tokens += ["--output-schema", output_schema_path]
    if model:
        tokens += [cli_contract.MODEL_FLAG, model]
    # Reasoning effort rides the `model_reasoning_effort` config key (0.148 still has no
    # dedicated flag — `codex exec --help` re-checked 2026-08-19). A config key cannot be
    # help-gated, so it is sent whenever the
    # caller/server requested one — including an explicit "" after shared shape
    # validation. Loss of the shared `-c` flag fails loudly, and the strict-config guard
    # below makes a rename/removal of this key fail loudly too (#524; see cli_contract).
    # The value is TOML-string-encoded (JSON string syntax is valid TOML): codex
    # TOML-parses the `-c` right-hand side and falls back to a string only when that
    # parse fails, so a raw interpolation would retype boolean/numeric/collection-
    # shaped values and silently unwrap quoted ones instead of round-tripping the
    # advertised open string exactly. ensure_ascii=False is load-bearing: the default
    # \uXXXX escaping emits surrogate PAIRS for astral characters, which TOML rejects
    # (escapes must be scalar values), silently degrading to the raw-string fallback.
    if reasoning_effort is not None:
        tokens += [
            "-c",
            f"{cli_contract.MODEL_REASONING_EFFORT_CONFIG_KEY}="
            f"{json.dumps(reasoning_effort, ensure_ascii=False)}",
        ]
        plugin_config_override = True
    # Caller developer instructions (#556): one `-c` pair carrying ONE composed string —
    # the server's framing leads, the caller's normalized text is delimited inside it
    # (prompts.compose_developer_instructions), so ordering is a property of the value.
    # Emitted only when text was supplied: the common run stays byte-identical to the
    # pre-#556 argv (no framing-only developer turn, no strict-config arming — see the
    # guard below and the availability note on its comment). Same TOML-string encoding
    # as the effort override above, same reasons.
    if developer_instructions is not None:
        tokens += [
            "-c",
            f"{cli_contract.DEVELOPER_INSTRUCTIONS_CONFIG_KEY}="
            + json.dumps(
                prompts.compose_developer_instructions(developer_instructions),
                ensure_ascii=False,
            ),
        ]
        plugin_config_override = True
    # Guard every `-c` KEY this argv carries (#524). `--strict-config` turns codex's
    # silent tolerance of an unknown key into a zero-spend startup failure, which is what
    # converts a silent upstream rename of a guarantee-bearing pin above
    # (network_access / writable_roots / model_reasoning_effort) into a loud
    # cli_contract_changed. It is emitted ONLY when an override actually rides — the
    # plugin's own pins or an operator `-c` — because at the default `inherit` isolation
    # the flag ALSO hard-fails on an unknown key anywhere in the user's own config.toml
    # (unselected [profiles.X] tables included, verified 0.148.0), so sending it on an
    # override-free run would risk the user's availability while guarding nothing.
    # Operator `-c` tokens are inspected here, before they are appended below, so the
    # decision reflects the whole argv.
    #
    # The plugin's own half is tracked as this function APPENDS each pin
    # (`plugin_config_override`), never by searching the built token list: a search cannot
    # tell an option from a VALUE that merely looks like one, and `model` accepts any
    # string — `model="-c"` would arm the guard on a run carrying no override at all. The
    # operator's half reads only the FLAG positions of `extra_args`, which
    # config._parse_extra_args emits as a flat sequence of `[flag, value]` pairs (its
    # values can never themselves be flags: a value starting with `-` is refused at parse
    # time as a smuggled option).
    operator_config_override = any(
        extra_args[i] in _CONFIG_OVERRIDE_FLAGS for i in range(0, len(extra_args), 2)
    )
    if plugin_config_override or operator_config_override:
        tokens += [cli_contract.STRICT_CONFIG_FLAG]
    cmd, dropped = _gate_optional(tokens, fs)
    # Operator passthrough goes in AFTER gating (never gated/dropped) and before the
    # stdin sentinel; already allowlist-validated in config.extra_args().
    cmd += list(extra_args)
    # Prompt comes from stdin; the trailing sentinel tells codex exec to read it.
    cmd += [cli_contract.STDIN_PROMPT]
    return cmd, dropped


async def run_codex_exec(
    prompt: str,
    *,
    kind: str,
    cwd: str,
    sandbox: str,
    isolation: str,
    timeout_seconds: int,
    model: str | None = None,
    reasoning_effort: str | None = None,
    developer_instructions: str | None = None,
    output_schema: dict | None = None,
    on_event: Callable[[str], None] | None = None,
) -> CodexExecResult:
    """Run `codex exec` through the CodexBackend adapter lifecycle.

    The adapter's `prepare()` stages everything (temp last-message/schema files,
    argv from the shared builder, prompt over stdin, help-gate drops); this
    function owns only the execution step — `runtime.run_async` with the
    consumer's own timeout/byte caps and event streaming — and folds the outcome
    back into the bridge's `CodexExecResult`. `kind` is the canonical verb
    ("consult" | "review_changes" | "delegate"); the backend policy it drives
    from this entrypoint is the consult-only repo-check skip. (The adapter also
    defaults the sandbox by `kind`, but only for a caller that leaves
    `RunRequest.access` unset — `sandbox` is required here and always passed
    through, so that fallback never fires on this path.)
    """
    # Runtime import: backend.py imports this module's builders, so a top-level
    # import here would be a cycle. Cached by the import system after first use.
    from codex_in_claude.backend import BACKEND  # noqa: PLC0415

    request = RunRequest(
        kind=kind,
        prompt=prompt,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        schema=output_schema,
        model=model,
        reasoning_effort=reasoning_effort,
        access=sandbox,
        isolation=isolation,
        # Normalized caller text or None (#556), on the first-class pontonier 0.7.0
        # field: the adapter folds it into the builder's composed `-c` value. NOT an
        # extra_args descriptor — that channel is operator-owned by the protocol.
        instructions_append=developer_instructions,
    )
    # Resolve the binary here, ahead of `prepare()` (whose `build_exec_command()` call
    # then hits the cache). A bad CODEX_IN_CLAUDE_CODEX_BIN override otherwise raised
    # `BinaryNotFoundError` out of the run -- an `internal_error` ("retry") at the tool
    # guard, for a misconfiguration no retry clears. It is the same run-level fact as a
    # spawn that finds no binary, so it is returned in that exact shape (the
    # binary-missing CommandRun `runtime.run_async` returns) and classifies as
    # `codex_not_found`, zero spend.
    try:
        binpath.codex_bin()
    except binpath.BinaryNotFoundError:
        return CodexExecResult(
            run=runtime.CommandRun("", runtime.BINARY_NOT_FOUND, 127, 0, False),
            last_message=None,
        )
    async with BACKEND.prepare(request) as prepared:
        # Observe the version BEFORE the exec, on the same token/cwd/env this run is
        # about to spawn (#519). Deliberately uncached: every run -- sync included --
        # executes in a fresh detached worker, so a process cache would save nothing on
        # the paid path while widening the window in which the stamped version is not the
        # one that served the run.
        version = probe_version_for_run(prepared.argv[0], cwd=prepared.cwd, env=prepared.env)
        run = await runtime.run_async(
            list(prepared.argv),
            cwd=prepared.cwd,
            timeout_seconds=timeout_seconds,
            stdin_text=prepared.stdin_text,
            env=prepared.env,
            on_stdout_line=on_event,
            max_output_bytes=config.max_output_bytes(),
        )
        # Named lookup, inside the context on purpose: the staged temp dir (and the
        # last-message file with it) is torn down when prepare() exits.
        last_message = _read_last_message(prepared.artifact_paths["last-message"])
    return CodexExecResult(
        run=run,
        last_message=last_message,
        events=run.stdout,
        dropped_flags=list(prepared.dropped_flags),
        # Withheld when the spawn found no binary: the probe answering is not evidence
        # the exec did, and naming a version for a run that never launched would be a
        # fabricated attestation.
        codex_version=None if run.binary_missing else version,
    )


def _read_last_message(path: str) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def codex_version(timeout_seconds: int = 10) -> str | None:
    """Probe `codex --version`. Returns the trimmed version string, or None."""
    try:
        codex_path = binpath.codex_bin()
    except binpath.BinaryNotFoundError:
        # A bad CODEX_IN_CLAUDE_CODEX_BIN override is a probe failure like any
        # other -- this function's contract is None on any failure, never a raise.
        return None
    run = runtime.run_sync_capture(
        [codex_path, *cli_contract.VERSION_ARGS], timeout_seconds=timeout_seconds
    )
    if run.binary_missing or run.exit_code != 0:
        return None
    return run.stdout.strip() or None


def probe_version_for_run(
    argv0: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 10,
) -> str | None:
    """The DISPLAY copy of `codex --version` for the executable a run is about to spawn.

    Takes `argv0` rather than re-resolving through `binpath`: the caller has already
    decided which token it will spawn, and interrogating a separately resolved one could
    name a different binary than the one that serves the run.

    BEST-EFFORT, NOT ATTESTATION, and the field built from it says so. This is a second
    process: between it and the exec, the path can be replaced, a symlink or npm shim
    retargeted, or the file rewritten in place. Probing immediately before the spawn
    shrinks that window; nothing available from the CLI closes it, because the
    `codex exec --json` stream carries no version, model, or binary field at all
    (re-verified at codex-cli 0.151.0 -- see #519).

    Returns the sanitized, bounded copy `version_display` produces, or None on any
    probe failure. A failed probe must never fail the run: the paid answer still ships,
    honestly unstamped.
    """
    run = runtime.run_sync_capture(
        [argv0, *cli_contract.VERSION_ARGS],
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        env=env,
    )
    if run.binary_missing or run.exit_code != 0:
        return None
    return version_display(run.stdout.strip() or None)


def login_status(timeout_seconds: int = 10) -> tuple[bool | None, str | None]:
    """Probe `codex login status` without a model call.

    Returns (logged_in, detail). logged_in is None when the probe could not run
    (codex missing/timeout). detail is a NON-identifying phrase derived from the
    exit code and method keyword — never the raw output, which may name an account.
    """
    try:
        codex_path = binpath.codex_bin()
    except binpath.BinaryNotFoundError:
        # A bad CODEX_IN_CLAUDE_CODEX_BIN override is a probe failure like any
        # other -- this function's contract is (None, None) on any failure,
        # never a raise.
        return None, None
    run = runtime.run_sync_capture(
        [codex_path, *cli_contract.LOGIN_STATUS_ARGS], timeout_seconds=timeout_seconds
    )
    if run.binary_missing or run.timed_out:
        return None, None
    if run.exit_code != 0:
        return False, "Codex reports no authenticated session; run `codex login`."
    blob = f"{run.stdout}\n{run.stderr}"
    if cli_contract.LOGIN_METHOD_CHATGPT.lower() in blob.lower():
        method = "ChatGPT"
    elif cli_contract.LOGIN_METHOD_API_KEY.lower() in blob.lower():
        method = "API key"
    else:
        method = None
    detail = (
        f"Codex reports an authenticated session ({method})."
        if method
        else "Codex reports an authenticated session."
    )
    return True, detail


def _auth_error() -> ErrorInfo:
    return make_error("codex_auth_required", "codex is not authenticated.")


def _rate_limit_error(retry_after_ms: int) -> ErrorInfo:
    return make_error(
        "codex_rate_limited", "codex hit a usage/rate limit.", retry_after_ms=retry_after_ms
    )


def contract_changed_error() -> ErrorInfo:
    """Shared cli_contract_changed error, reused across every failure path so a
    drift is reported identically wherever `codex` surfaces it."""
    return make_error(
        "cli_contract_changed",
        "codex rejected a flag or value this plugin sent — its CLI "
        "contract likely changed for your installed version.",
    )


def _invalid_reasoning_effort_error() -> ErrorInfo:
    """Error for a backend rejection of the reasoning_effort this run requested (#309).

    Static, value-free message: the rejected effort is caller input (the caller already
    holds it), matching the no-echo policy of invalid_arguments/ErrorDetail."""
    return make_error(
        "invalid_reasoning_effort",
        "The Codex backend rejected the requested reasoning_effort for this model/account.",
        details=ErrorDetail(field="reasoning_effort"),
    )


def _extra_args_rejected_error(matched: list[str]) -> ErrorInfo:
    """Error for a drift that codex attributes to an operator-supplied extra arg (#231).

    `matched` are the descriptors (allowlisted flag names / config keys / profile/feature
    names — never a secret `-c` VALUE) whose text appeared in codex's rejection, so the
    repair can name what to fix without echoing input.

    Each descriptor is echoed through `_safe_echo`. They are operator-supplied rather than
    codex-supplied, but they are still foreign text reaching an envelope, and they reached
    it RAW: `config._safe_token` covers only the unsupported-argument path, while a valid
    config key or profile name is recorded verbatim (#528). Both carriers are sanitized —
    the message and the repair's alternative, which quotes the same names."""
    named = ", ".join(_safe_echo(d) for d in matched) if matched else config.EXTRA_ARGS_ENV
    return make_error(
        "extra_args_rejected",
        f"codex rejected an argument from {config.EXTRA_ARGS_ENV} ({named}) — the "
        "passthrough option/config key/profile is not accepted by your installed codex.",
        repair_alternative=(
            f"Fix or remove the offending entry ({named}) in {config.EXTRA_ARGS_ENV}; "
            "this is operator config, NOT a plugin contract drift. Verify the option "
            "against `codex --help` / `codex exec --help` for your installed version."
        ),
    )


# Bound on each echoed span; a real key or path is far shorter (cf. CODEX_HOME_MAX_BYTES).
_ECHO_MAX_CHARS = 200
# Reserved INSIDE that bound, so a clipped span can never be read as a complete value
# (#531; `[8.truncation]` in the agent-friendly-mcp contract checklist). appserver's
# `_DISPLAY_TRUNC_MARKER` makes the same reservation for app-server prose.
_ECHO_TRUNC_MARKER = "…[truncated]"


def _bounded_echo(text: str) -> str:
    """Cut an already-sanitized span to `_ECHO_MAX_CHARS`, marking the cut when one happens.

    The marker's own length comes out of the budget rather than being added to it, so the
    result never exceeds the bound the caller was promised.

    The guarantee is ONE-WAY: a cut is always marked, but a marked value is not proof of a
    cut, because an under-cap span that already ends in the marker passes through untouched.
    That is why nothing branches on it — it is advisory text for a reader, not a
    machine-readable `truncated` flag, and a spoofed suffix misleads a reader and no more."""
    if len(text) <= _ECHO_MAX_CHARS:
        return text
    return text[: _ECHO_MAX_CHARS - len(_ECHO_TRUNC_MARKER)] + _ECHO_TRUNC_MARKER


def _safe_echo(text: str | None) -> str:
    """Bound an untrusted echoed span, after the shared echo sanitizer has cleaned it.

    Applied to a single-token span whose whole value is its actionable content — the
    config KEY and file path a strict-config rejection carries, or a rejected flag name.
    Codex read those off disk, so they are untrusted, but the caller cannot fix a key they
    are not told about.

    The strip-then-redact ordering is `redaction.sanitize_echo`'s and is documented there;
    this bridge does not re-derive it (#528). What stays here is bridge policy: the LENGTH
    bound, and that truncation comes LAST, so a secret straddling the cut still had the
    tail its pattern needed when the redactor saw it. The cut is MARKED (#531): a clipped
    key or flag name that reads as a complete one sends the caller after the wrong thing."""
    return _bounded_echo(redaction.sanitize_echo(text))


def version_display(version: str | None) -> str | None:
    """The bounded, sanitized DISPLAY copy of a `codex --version` string, or None.

    `codex_version()` above returns the string RAW, and it must keep doing so: it is the
    IDENTITY `config.parse_version` reads, and sanitizing at capture would let the cleanup
    decide the support verdict. Deleting the control character out of `0.<BEL>148.0` yields
    a perfectly plausible `codex-cli 0.148.0` — so a version that does not parse at all
    would be repaired into one that does. This is the identity-vs-display split #528 drew
    for extra-args descriptors, applied at the one place the string is emitted (#531).

    So the two disagree by design, and a reader must not treat this copy as the identity:
    `version_supported` is parsed from the raw probe output, and a truncated display can
    even omit the token that decided it. `StatusResult` documents that to clients.

    A value that sanitizes away to nothing comes back as None rather than "": the field is
    optional, and `codex_found` is decided from the RAW value, so a version of nothing but
    control characters still means codex ran and answered."""
    return _safe_echo(version) or None


def plugin_config_keys_for(
    *,
    sandbox: str,
    reasoning_effort: str | None,
    developer_instructions: str | None = None,
) -> frozenset[str]:
    """The `-c` config KEYS `build_exec_command` emits for a run of this shape.

    Failure attribution turns on which pinned keys THIS run actually sent (#550): the
    workspace pins ride only `workspace-write` runs and the effort key only when an
    effort was requested. Membership in PLUGIN_OWNED_CONFIG_KEYS alone proves nothing
    about a given run. This mirrors the builder's branches and a test pins the two
    together by parsing the built argv, so a drift between them fails loudly."""
    keys: set[str] = set()
    if sandbox == cli_contract.SANDBOX_WORKSPACE_WRITE:
        keys.add(cli_contract.WORKSPACE_WRITE_NETWORK_ACCESS_CONFIG_KEY)
        keys.add(cli_contract.WORKSPACE_WRITE_WRITABLE_ROOTS_CONFIG_KEY)
    if reasoning_effort is not None:
        keys.add(cli_contract.MODEL_REASONING_EFFORT_CONFIG_KEY)
    if developer_instructions is not None:
        keys.add(cli_contract.DEVELOPER_INSTRUCTIONS_CONFIG_KEY)
    return frozenset(keys)


def _user_config_rejected_error(rejection: cli_contract.StrictConfigRejection) -> ErrorInfo:
    """Error for a `--strict-config` rejection of a key in the USER's own config (#524).

    The key, file, and line are the entire actionable content, so they are echoed — but
    they are untrusted text codex read off disk, so they go through the same redaction
    and length bound as every other surfaced failure detail."""
    where = _safe_echo(rejection.source_path) or "your Codex config"
    key = _safe_echo(rejection.key)
    line = f":{rejection.line}" if rejection.line is not None else ""
    return make_error(
        "user_config_rejected",
        f"codex refused to start: your Codex config sets `{key}`, which this codex "
        f"version does not recognize ({where}{line}). No model call was made.",
    )


def _config_value_error(
    key: str,
    *,
    what_is_wrong: str,
    repair_alternative: str,
    extra: config.ExtraArgs | None,
    plugin_config_keys: frozenset[str],
) -> ErrorInfo:
    """Attribute and word a rejection of a recognized key's VALUE (#542, #550).

    Both value grammars — a RETIRED value and an INVALID one — share this: the key still
    exists and only its value is refused, so the strict unknown-KEY grammar cannot see
    it and no CONTRACT_DRIFT pattern matches. The message names no file and carries no
    `-c` marker, so ownership cannot be read off it the way `_strict_config_error` reads
    its origin; it is decided here, in order:

    1. A key THIS run pinned (`plugin_config_keys`, from `plugin_config_keys_for`) is a
       statement about our own argv: a `-c` override outranks the config file entirely
       (probed on 0.149.1: a bad file value under a good override does not fail at all),
       so codex refusing the value means OURS was refused — cli_contract_changed, the
       same security-relevant attribution the strict sibling makes first. Membership in
       PLUGIN_OWNED_CONFIG_KEYS is NOT enough (#550): the pins ride only some run
       shapes, and a user mistyping one of those keys on a read-only consult would be
       sent after a plugin update instead of their file.
    2. A key the operator's passthrough sets (or a dotted child of one) is theirs.
    3. Otherwise the user's config. `what_is_wrong` is the grammar-specific clause
       spliced after "sets `KEY`" — its caller has already bounded and sanitized every
       echoed span — and `repair_alternative` overrides the table prose, which is written
       for the unknown-KEY grammar (it calls the key unrecognized and points at a
       reported file and line, neither of which applies)."""
    if key in plugin_config_keys:
        return contract_changed_error()
    ea = config.extra_args() if extra is None else extra
    if ea.owns_config_key(key):
        return _extra_args_rejected_error([key])
    # Ownership is genuinely UNKNOWN when an operator profile is selected. `--profile NAME`
    # makes codex load $CODEX_HOME/NAME.config.toml, and a profile can reintroduce a
    # setting the extra-args denylist refuses on `-c` (the documented operator-trust
    # boundary). The strict grammar resolves this by naming the offending FILE; these
    # report no file, so the honest move is to disclose the ambiguity rather than assert
    # the user's own config and send them to fix a file that may not hold the value.
    caveat = ""
    if ea.profile_names:
        selected = ", ".join(_safe_echo(n) for n in ea.profile_names)
        caveat = (
            f" The setting may instead come from the operator profile selected by "
            f"{config.EXTRA_ARGS_ENV} ({selected}), which this plugin cannot inspect — "
            f"check there too."
        )
    return make_error(
        "user_config_rejected",
        f"codex refused to start: your Codex config sets `{_safe_echo(key)}` "
        f"{what_is_wrong} Remove or change that setting.{caveat} No model call was made.",
        repair_alternative=repair_alternative,
    )


_CONFIG_VALUE_REPAIR_TAIL = (
    "Remove or change the setting in your Codex config — checking any operator-selected "
    "profile too — then rerun. As a last resort, isolation='ignore-config' skips your "
    "config file for the run — but it drops ALL of it (model provider, MCP servers, and "
    "every other setting), so prefer fixing the setting."
)


def _retired_config_setting_error(
    setting: cli_contract.UnsupportedConfigSetting,
    extra: config.ExtraArgs | None,
    plugin_config_keys: frozenset[str],
) -> ErrorInfo:
    """Error for a config setting codex RETIRED (#542) — see `_config_value_error`.

    The retired value is a known enum literal codex named in its own message, so it is
    echoed (bounded and sanitized like every surfaced span): "no longer supported" is
    only actionable if the caller knows WHICH value."""
    return _config_value_error(
        setting.key,
        what_is_wrong=(
            f"to {_safe_echo(setting.value)}, which this codex version no longer supports."
        ),
        repair_alternative=(
            "This codex version no longer supports that config VALUE (the key itself is "
            "still recognized, and codex reports no file or line for it). "
            + _CONFIG_VALUE_REPAIR_TAIL.replace(
                "then rerun", "or upgrade/downgrade codex to a version that accepts it, then rerun"
            )
        ),
        extra=extra,
        plugin_config_keys=plugin_config_keys,
    )


def _invalid_config_value_error(
    rejection: cli_contract.InvalidConfigValue,
    extra: config.ExtraArgs | None,
    plugin_config_keys: frozenset[str],
) -> ErrorInfo:
    """Error for a recognized key whose VALUE failed serde validation (#550).

    Unlike the retired sibling, the offending value is NOT echoed — the parser never
    captured it (see cli_contract's grammar note): it is free-form text the user typed,
    plausibly a secret, and no pattern-based redactor recognizes an arbitrary one. What
    codex EXPECTED is codex's own text and is the actionable content, so it is surfaced."""
    expected = _safe_echo(rejection.expected)
    if rejection.kind == "unknown_variant":
        what = f"to a value this codex version does not accept (expected one of {expected})."
    else:
        what = f"to a value of the wrong type (expected {expected})."
    return _config_value_error(
        rejection.key,
        what_is_wrong=what,
        repair_alternative=(
            "codex refused that config VALUE: the key itself is recognized, but the value "
            "is not one it accepts (the message names what it expected), and codex reports "
            "no file or line for it. " + _CONFIG_VALUE_REPAIR_TAIL
        ),
        extra=extra,
        plugin_config_keys=plugin_config_keys,
    )


def _strict_config_error(
    rejection: cli_contract.StrictConfigRejection, extra: config.ExtraArgs | None
) -> ErrorInfo:
    """Classify a `--strict-config` unknown-key rejection by WHO owns the key (#524).

    Ownership is decided on the rejected KEY (and, for the file form, the rejected FILE),
    never on the shared `-c` descriptor: codex's own rejection text names that flag, so a
    descriptor match would let any operator `-c` entry claim a plugin pin's drift.

    An override-form rejection naming one of PLUGIN_OWNED_CONFIG_KEYS is proof that a
    guarantee-bearing pin's key drifted upstream — codex is echoing the key WE sent — so
    it is cli_contract_changed, which is the fail-loud conversion this guard exists for.
    An unattributable key stays cli_contract_changed too: fail loud rather than guess.

    The PLUGIN_OWNED_CONFIG_KEYS test is deliberately REDUNDANT today: config's
    extra-args parser already refuses all three of those keys, so `owns_config_key`
    cannot return True for one, and both branches reach the same error. It is kept so
    this security-relevant attribution does not silently depend on a denylist in another
    module; `test_operator_passthrough_can_never_own_a_plugin_pinned_key` pins that
    coupling, so narrowing the denylist fails a test instead of misattributing a drift."""
    ea = config.extra_args() if extra is None else extra
    if rejection.origin == "override":
        if rejection.key in cli_contract.PLUGIN_OWNED_CONFIG_KEYS:
            return contract_changed_error()
        if ea.owns_config_key(rejection.key):
            return _extra_args_rejected_error([rejection.key])
        return contract_changed_error()
    # File form: the user's own config, unless the file is one an operator `--profile`
    # selected (codex loads and validates $CODEX_HOME/NAME.config.toml only when selected).
    if ea.owns_profile_file(rejection.source_path):
        return _extra_args_rejected_error([rejection.key])
    return _user_config_rejected_error(rejection)


def _descriptor_in_blob(descriptor: str, blob: str) -> bool:
    """Whether `descriptor` appears in `blob` at flag/token boundaries.

    A bare substring test is too loose: a short descriptor (e.g. a one-char feature
    name "a") would match INSIDE an unrelated word ("--s**a**ndbox"), so a genuine
    plugin-flag drift would be misattributed to the operator's passthrough. clap quotes
    the offending token (`'--profile'`, `'model_provider'`), so we require the
    descriptor to be delimited by non-word / non-hyphen characters (quotes, spaces,
    line ends) on both sides — matching how codex names it, while ignoring incidental
    substring hits."""
    pattern = rf"(?<![\w-]){re.escape(descriptor)}(?![\w-])"
    return re.search(pattern, blob, re.IGNORECASE) is not None


def _extra_args_drift_match(extra: config.ExtraArgs | None, *texts: str | None) -> list[str] | None:
    """Descriptors of `extra` codex named in a rejection blob (token-bounded), or None.

    Returns None when no extra args are configured/valid — so a genuine plugin-flag
    drift (e.g. codex dropping --sandbox) stays cli_contract_changed and the fail-loud
    guarantee holds. A match means codex named one of the operator's passthrough
    entries, so the drift is attributed to CODEX_IN_CLAUDE_EXTRA_ARGS instead."""
    ea = config.extra_args() if extra is None else extra
    if not ea.configured or not ea.valid or not ea.descriptors:
        return None
    blob = "\n".join(t for t in texts if t)
    matched = [d for d in ea.descriptors if _descriptor_in_blob(d, blob)]
    return matched or None


# Prose for a run whose output capture failed (pontonier 0.8.0 `CommandRun.capture_failed`,
# #579). The flag is STREAM-NEUTRAL — either pump thread (stdout or stderr) dying sets it,
# and nothing records which — so the prose claims only what the flag proves: a capture
# thread died, output written after that (if any) was not read, and a child left writing
# into an undrained pipe can block until the deadline. Every consequence is hedged.
# It never changes a classification here (the answer channel is the last-message file,
# not stdout); it only names the fault in the two branches whose diagnosis a lost stream
# can mislead. All of it is `error.message`/`repair.alternative` prose, outside the
# discovered surface, so none of it moves FINGERPRINT.
_CAPTURE_FAILED_TIMEOUT_MESSAGE = (
    "codex exceeded the timeout, and the plugin's output capture failed mid-run (a capture "
    "thread died). Codex may have been blocked on an undrained pipe rather than slow, so this "
    "may be a bridge fault rather than a model timeout."
)
# Overrides the table's timeout alternative, which says the same sync call will likely time
# out again — true of a slow model, not of a capture fault. Machine fields (code, next_step,
# temporary) stay the table's.
_CAPTURE_FAILED_TIMEOUT_ALTERNATIVE = (
    "Retry the same call once first: if the capture failure caused this timeout, the retry "
    "may finish normally. If it times out again without this notice, treat it as an ordinary "
    "timeout — prefer the matching async tool (codex_consult_async / "
    "codex_review_changes_async / codex_delegate_async), or narrow the task or raise "
    "timeout_seconds."
)
_CAPTURE_FAILED_EXIT_NOTE = (
    " (the plugin's output capture failed mid-run, so part of codex's output may have been "
    "lost and this diagnosis may be incomplete)"
)


def classify_failure(
    run: CommandRun,
    *,
    last_message: str | None = None,
    events: str | None = None,
    extra_args: config.ExtraArgs | None = None,
    reasoning_effort: str | None = None,
    sanitize: Callable[[str], str] | None = None,
    plugin_config_keys: frozenset[str] = frozenset(),
) -> ErrorInfo:
    """Classify a non-success `codex exec` run into a recoverable ErrorInfo.

    Codex reports request/turn failures as JSONL `error`/`turn.failed` events on
    stdout, so we extract that message (when present) for both classification and
    the surfaced text — it is cleaner than the truncated raw stream.

    `extra_args` (defaulting to a fresh env read) lets a drift codex attributes to an
    operator's CODEX_IN_CLAUDE_EXTRA_ARGS entry be reported as `extra_args_rejected`
    rather than `cli_contract_changed` (#231).

    `reasoning_effort` is the effort override this run sent through the plugin's
    first-class controls, or None when none was sent. The backend rejects a bad
    effort VALUE with a message that also matches the generic drift patterns, so
    when one was sent and every backend effort marker appears in its bracketed
    `[…]` field form the failure is the caller's argument
    (`invalid_reasoning_effort`), not contract drift (#309) — unless the operator's
    own matched passthrough descriptors account for that signature, in which case
    the rejection is theirs (`extra_args_rejected`, #313).

    `sanitize`, when given, REPLACES the generic `nonzero_exit` branch's own sanitizer
    (`redaction.sanitize_echo_prose`), so it must carry the same guarantees — redaction AND
    control-character stripping, in that fixed order. `worktree.sanitize_echo_prose` is the
    one approved composition; `delegate.run_delegate` passes it so a delegate error can't
    quote a dead absolute path into the (already torn-down) throwaway worktree (#420).
    It runs on the raw `event_error or run.stderr or run.stdout` before the `[:300]`
    truncation, exactly where the default sanitizer runs. Every classification decision
    above (drift/auth/rate-limit signature matching) still reads the RAW strings — only the
    emitted text changes.

    `plugin_config_keys` is the set of `-c` KEYS this run's argv pinned — obtain it from
    `plugin_config_keys_for` with the run's sandbox and effort. A config-VALUE rejection
    naming one of them is attributed to the plugin (cli_contract_changed); the default,
    an empty set, attributes such a rejection to the user or operator instead (#550), so a
    caller that omits it can never blame the plugin for a key it did not send."""
    if run.binary_missing:
        # Not "on PATH": resolution also probes the WSL2 candidate directories and honors
        # a CODEX_IN_CLAUDE_CODEX_BIN override (#538) -- codex_status reports which failed.
        return make_error(
            "codex_not_found",
            "The `codex` CLI was not found; run codex_status for the resolution detail.",
        )
    if run.timed_out:
        if run.capture_failed:
            # One of the plugin's own capture threads died mid-run (pontonier 0.8.0
            # `capture_failed`, #579). Nothing drained that pipe afterwards, so a codex still
            # writing to it blocks until the deadline: a bridge fault wearing a timeout's
            # shape. Hedged, because the flag cannot rule out a genuine slow run alongside
            # it. Prose only (message + repair alternative): neither is part of the
            # discovered surface, so no FINGERPRINT bump.
            return make_error(
                "timeout",
                _CAPTURE_FAILED_TIMEOUT_MESSAGE,
                repair_alternative=_CAPTURE_FAILED_TIMEOUT_ALTERNATIVE,
            )
        return make_error("timeout", "codex exceeded the timeout.")
    event_error = normalize.extract_error_message(events) if events else None
    # `--strict-config` rejections are classified FIRST, from stderr alone (#524). Codex
    # parses config before it authenticates or calls a model, so this failure is never an
    # auth or rate-limit problem — but it ECHOES an untrusted key and file path, either of
    # which can carry an auth pattern ("401" in a path) or a drift phrase (a TOML quoted
    # key). Those matchers are substring tests and would win on ordering alone, so the
    # anchored strict grammar is checked ahead of them. Reading stderr alone also keeps
    # model-produced text from manufacturing this classification.
    strict = cli_contract.parse_strict_config_rejection(run.stderr)
    if strict is not None:
        return _strict_config_error(strict, extra_args)
    # A RETIRED setting is the same class of failure one step later in config parsing:
    # the key parses, its value no longer does (#542). It is checked here, beside the
    # strict grammar and ahead of the auth/drift/rate-limit substring matchers, for the
    # identical reason — it echoes untrusted text that could satisfy one of them.
    retired = cli_contract.parse_unsupported_config_setting(run.stderr)
    if retired is not None:
        return _retired_config_setting_error(retired, extra_args, plugin_config_keys)
    # An INVALID value — wrong enum variant or wrong type — is the third config-parse
    # grammar (#550): the key parses, serde refuses the value. Same position, same reason:
    # it consumes the user's free-form value, which can carry any substring below.
    invalid = cli_contract.parse_invalid_config_value(run.stderr)
    if invalid is not None:
        return _invalid_config_value_error(invalid, extra_args, plugin_config_keys)
    if cli_contract.is_auth_failure(run.stderr, run.stdout, last_message, event_error):
        return _auth_error()
    # Drift before rate-limit so a genuine contract change is never masked as a
    # transient (retryable) rate limit.
    if cli_contract.is_contract_drift(run.stderr, run.stdout, event_error):
        # Only re-attribute to the operator's passthrough when codex actually named one
        # of its descriptors; otherwise a real plugin-flag drift must stay fail-loud.
        matched = _extra_args_drift_match(extra_args, run.stderr, run.stdout, event_error)
        # When the matched operator descriptors THEMSELVES carry the full bracketed
        # marker signature (a profile/feature literally named
        # "[reasoning.effort][ReasoningEffortParam]" — the allowlist constrains flags,
        # not name characters), codex quoting that name is what satisfied the backend
        # check: the rejection is the operator's entry, not the backend's. Attribute
        # it before the backend check or the impersonation steals the classification.
        # A genuine backend rejection cannot trip this: its markers are separate
        # space-delimited fields, which never token-match a composite descriptor.
        if matched is not None and cli_contract.is_reasoning_effort_rejection(*matched):
            return _extra_args_rejected_error(matched)
        # Backend effort rejection next: when THIS run sent a first-class effort
        # override and the blob carries the backend's request-level markers
        # (reasoning.effort/ReasoningEffortParam), the failure is that argument — the
        # markers are specific, while the descriptor attribution below is a generic
        # token match that an unlucky operator name (e.g. a profile called "high",
        # which the backend's supported-values list quotes) could satisfy
        # incidentally. A rejection naming only the config key carries no marker and
        # stays fail-loud drift below.
        if reasoning_effort is not None and cli_contract.is_reasoning_effort_rejection(
            run.stderr, run.stdout, event_error
        ):
            return _invalid_reasoning_effort_error()
        # When a first-class reasoning effort was sent, the plugin ITSELF emitted a
        # bare `-c` pair, so a rejection naming only that shared flag token is
        # ambiguous between the operator's passthrough and the plugin's own tokens —
        # and the documented fail-loud `-c` guarantee must win. Operator attribution
        # requires a descriptor the plugin does not also send (a config key, profile/
        # feature name, or another flag).
        # Ownership derives from the RUN-SPECIFIC emitted key set, not the effort
        # parameter alone: a developer-instructions run and the workspace-write pins
        # also emit bare `-c` pairs (#556 review). `reasoning_effort is not None` is
        # kept for direct callers that pass no key set (its key is a member whenever
        # the builder saw it).
        plugin_owns_dash_c = reasoning_effort is not None or bool(plugin_config_keys)
        if matched is not None and not (plugin_owns_dash_c and set(matched) <= {"-c"}):
            return _extra_args_rejected_error(matched)
        return contract_changed_error()
    if cli_contract.is_rate_limited(run.stderr, run.stdout, last_message, event_error):
        retry_after = cli_contract.parse_retry_after_ms(
            run.stderr, run.stdout, last_message, event_error
        )
        # Explicit None check: a parsed "Retry-After: 0" (retry now) is a valid delay
        # and must be preserved, not coalesced to the default by a falsey check.
        if retry_after is None:
            retry_after = cli_contract.RATE_LIMIT_DEFAULT_BACKOFF_MS
        return _rate_limit_error(retry_after)
    # Sanitize the full text *before* truncating: a secret or worktree path straddling the
    # 300-char cut would otherwise lose the tail the redaction/relativization patterns need
    # to match, leaking a prefix. The default sanitizer is the shared echo one, which also
    # strips control characters ahead of redaction (#528) — this is multi-line stderr, so
    # it is the PROSE variant, which keeps line breaks wherever that is provably as safe
    # as removing them.
    raw = (event_error or run.stderr or run.stdout).strip()
    detail = (sanitize(raw) if sanitize is not None else redaction.sanitize_echo_prose(raw))[:300]
    message = f"codex exited {run.exit_code}: {detail}"
    if run.capture_failed:
        # Appended AFTER the 300-char cut so a verbose echo cannot truncate it away. Which
        # stream was lost is unknown (#579): the JSONL error event that normally carries the
        # cleanest diagnosis, or the stderr this echo may be reading — either way the text
        # above may be incomplete, and the agent should not over-read it.
        message += _CAPTURE_FAILED_EXIT_NOTE
    return make_error("nonzero_exit", message)
