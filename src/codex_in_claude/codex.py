"""Build and run the `codex` CLI invocation; probe version/auth; classify failures."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from codex_in_claude import cli_contract, config, normalize, preflight
from codex_in_claude._core import redaction, runtime
from codex_in_claude.config import isolation_flags
from codex_in_claude.errors import make_error

if TYPE_CHECKING:
    from collections.abc import Callable

    from codex_in_claude._core.runtime import CommandRun
    from codex_in_claude.preflight import FlagSupport
    from codex_in_claude.schemas import ErrorInfo


@dataclass
class CodexExecResult:
    """Outcome of a `codex exec` run: the raw process result plus the cleanly
    extracted final agent message and the JSONL event text (for tolerant metadata
    parsing)."""

    run: CommandRun
    last_message: str | None
    events: str = ""
    dropped_flags: list[str] = field(default_factory=list)


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


def build_exec_command(
    *,
    cwd: str,
    sandbox: str,
    isolation: str,
    output_last_message_path: str,
    model: str | None = None,
    output_schema_path: str | None = None,
    add_dirs: tuple[str, ...] = (),
    skip_git_repo_check: bool = False,
    ephemeral: bool = True,
    flag_support: FlagSupport | None = None,
) -> tuple[list[str], list[str]]:
    """Build the `codex exec` invocation. Returns (cmd, dropped_optional_flags).

    The prompt is supplied over stdin (the trailing ``-`` sentinel) by the runner,
    keeping gathered context/diffs out of argv and local process listings.
    Guarantee-bearing flags are sent unconditionally; HELP_GATED (depth) flags are
    dropped when the installed CLI does not list them."""
    fs = flag_support if flag_support is not None else preflight.flag_support()
    tokens = [cli_contract.CODEX_BIN, *cli_contract.EXEC_SUBCOMMAND]
    tokens += ["--json"]
    tokens += ["--sandbox", sandbox]
    tokens += ["--cd", cwd]
    tokens += ["--output-last-message", output_last_message_path]
    if ephemeral:
        tokens += ["--ephemeral"]
    tokens += isolation_flags(isolation)
    if skip_git_repo_check:
        tokens += ["--skip-git-repo-check"]
    for d in add_dirs:
        tokens += ["--add-dir", d]
    if output_schema_path:
        tokens += ["--output-schema", output_schema_path]
    if model:
        tokens += ["--model", model]
    cmd, dropped = _gate_optional(tokens, fs)
    # Prompt comes from stdin; the trailing sentinel tells codex exec to read it.
    cmd += [cli_contract.STDIN_PROMPT]
    return cmd, dropped


async def run_codex_exec(
    prompt: str,
    *,
    cwd: str,
    sandbox: str,
    isolation: str,
    timeout_seconds: int,
    model: str | None = None,
    output_schema: dict | None = None,
    add_dirs: tuple[str, ...] = (),
    skip_git_repo_check: bool = False,
    ephemeral: bool = True,
    flag_support: FlagSupport | None = None,
    on_event: Callable[[str], None] | None = None,
) -> CodexExecResult:
    """Run `codex exec` for the sync path, managing the temp output files.

    Writes an optional JSON Schema to a temp file, runs codex with the prompt over
    stdin, then reads the final agent message from --output-last-message. The temp
    dir (and the schema/last-message files) are removed on exit."""
    with tempfile.TemporaryDirectory(prefix="codex-in-claude-") as tmp:
        last_msg_path = str(Path(tmp) / "last-message.txt")
        schema_path: str | None = None
        if output_schema is not None:
            schema_path = str(Path(tmp) / "schema.json")
            Path(schema_path).write_text(json.dumps(output_schema), encoding="utf-8")
        cmd, dropped = build_exec_command(
            cwd=cwd,
            sandbox=sandbox,
            isolation=isolation,
            output_last_message_path=last_msg_path,
            model=model,
            output_schema_path=schema_path,
            add_dirs=add_dirs,
            skip_git_repo_check=skip_git_repo_check,
            ephemeral=ephemeral,
            flag_support=flag_support,
        )
        run = await runtime.run_async(
            cmd,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            stdin_text=prompt,
            on_stdout_line=on_event,
            max_output_bytes=config.max_output_bytes(),
        )
        last_message = _read_last_message(last_msg_path)
    return CodexExecResult(
        run=run, last_message=last_message, events=run.stdout, dropped_flags=dropped
    )


def _read_last_message(path: str) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def codex_version(timeout_seconds: int = 10) -> str | None:
    """Probe `codex --version`. Returns the trimmed version string, or None."""
    run = runtime.run_sync_capture(
        [cli_contract.CODEX_BIN, *cli_contract.VERSION_ARGS], timeout_seconds=timeout_seconds
    )
    if run.binary_missing or run.exit_code != 0:
        return None
    return run.stdout.strip() or None


def login_status(timeout_seconds: int = 10) -> tuple[bool | None, str | None]:
    """Probe `codex login status` without a model call.

    Returns (logged_in, detail). logged_in is None when the probe could not run
    (codex missing/timeout). detail is a NON-identifying phrase derived from the
    exit code and method keyword — never the raw output, which may name an account.
    """
    run = runtime.run_sync_capture(
        [cli_contract.CODEX_BIN, *cli_contract.LOGIN_STATUS_ARGS], timeout_seconds=timeout_seconds
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


def classify_failure(
    run: CommandRun, *, last_message: str | None = None, events: str | None = None
) -> ErrorInfo:
    """Classify a non-success `codex exec` run into a recoverable ErrorInfo.

    Codex reports request/turn failures as JSONL `error`/`turn.failed` events on
    stdout, so we extract that message (when present) for both classification and
    the surfaced text — it is cleaner than the truncated raw stream."""
    if run.binary_missing:
        return make_error("codex_not_found", "The `codex` CLI was not found on PATH.")
    if run.timed_out:
        return make_error("timeout", "codex exceeded the timeout.")
    event_error = normalize.extract_error_message(events) if events else None
    if cli_contract.is_auth_failure(run.stderr, run.stdout, last_message, event_error):
        return _auth_error()
    # Drift before rate-limit so a genuine contract change is never masked as a
    # transient (retryable) rate limit.
    if cli_contract.is_contract_drift(run.stderr, run.stdout, event_error):
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
    # Redact the full text *before* truncating: a secret straddling the 300-char cut
    # would otherwise lose the tail the redaction patterns need to match, leaking a prefix.
    detail = (redaction.redact_text((event_error or run.stderr or run.stdout).strip()) or "")[:300]
    return make_error("nonzero_exit", f"codex exited {run.exit_code}: {detail}")
