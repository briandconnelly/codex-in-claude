"""Central construction and serialization of the error envelope.

One place owns the §6 symbolic-repair mapping and the null-stripping policy so every
error path emits the identical, machine-actionable shape. Nothing in `_core` imports
this module (it depends on the parent package's `schemas`)."""

from __future__ import annotations

from typing import Any

from codex_in_claude.schemas import (
    ErrorCode,
    ErrorDetail,
    ErrorInfo,
    ErrorResult,
    InvalidArgument,
    Repair,
    RepairStep,
)

# code -> (next_step, repair_tool, temporary, default alternative prose)
_REPAIR_BY_CODE: dict[str, tuple[RepairStep, str | None, bool, str]] = {
    "codex_not_found": (
        "install_codex",
        None,
        False,
        "Install the codex CLI (https://developers.openai.com/codex/cli), then rerun codex_status.",
    ),
    "codex_auth_required": (
        "authenticate",
        None,
        False,
        "Run `codex login` (ChatGPT or API key), then rerun codex_status.",
    ),
    "unexpanded_env_placeholder": (
        "update_plugin",
        None,
        False,
        "Set the referenced environment variable, or fix the plugin config.",
    ),
    "unsupported_tier": (
        "use_allowed_value",
        None,
        False,
        "Pass one of the tier's allowed_values.",
    ),
    "unsupported_sandbox": (
        "use_allowed_value",
        None,
        False,
        "Pass one of the sandbox's allowed_values.",
    ),
    "unsupported_isolation": (
        "use_allowed_value",
        None,
        False,
        "Pass one of isolation's allowed_values.",
    ),
    "unsupported_detail": (
        "use_allowed_value",
        None,
        False,
        "Pass one of detail's allowed_values.",
    ),
    "invalid_scope": ("correct_arguments", None, False, "Correct the scope argument."),
    "invalid_base": ("correct_arguments", None, False, "Correct the base argument."),
    "invalid_commit": ("correct_arguments", None, False, "Correct the commit argument."),
    "invalid_paths": ("correct_arguments", None, False, "Correct the paths argument."),
    "invalid_arguments": (
        "correct_arguments",
        None,
        False,
        "Check each tool's inputSchema (tools/list) or codex_capabilities, then retry.",
    ),
    "invalid_workspace_root": (
        "correct_arguments",
        None,
        False,
        "Pass an absolute path to an existing repository root.",
    ),
    "workspace_outside_roots": (
        "use_workspace_in_roots",
        None,
        False,
        "Pass a workspace_root inside one of candidate_roots.",
    ),
    "input_too_large": (
        "reduce_input",
        None,
        False,
        "Trim the input below limit_bytes, or raise the configured byte limit.",
    ),
    "not_a_git_repo": (
        "init_git_repo",
        None,
        False,
        "Point workspace_root at a git repository (propose needs one).",
    ),
    "git_unavailable": (
        "install_git",
        None,
        False,
        "Install git and ensure it is on PATH.",
    ),
    "worktree_error": (
        "inspect_and_retry",
        None,
        False,
        "Inspect the repository state; retry only after correcting it.",
    ),
    "context_too_large": (
        "reduce_input",
        None,
        False,
        "Narrow paths/scope so the gathered context fits.",
    ),
    "timeout": (
        "inspect_and_retry",
        # tool stays None: `timeout` is emitted from a shared classifier (codex.classify_failure)
        # serving consult, review AND delegate, so a single repair.tool naming one async variant
        # would misdirect the other callers. The prose names all three instead (#195).
        None,
        True,
        "Retrying the same synchronous call (codex_consult / codex_review_changes / "
        "codex_delegate) will likely time out again. Prefer re-running it via the matching "
        "async tool (codex_consult_async / codex_review_changes_async / codex_delegate_async), "
        "then poll codex_job_status and fetch codex_job_result — async jobs run to the "
        "separately configured background-job deadline (default 1800s) rather than the 10-600s "
        "sync clamp. Otherwise narrow the task or raise timeout_seconds, then retry.",
    ),
    "nonzero_exit": (
        "inspect_and_retry",
        None,
        False,
        "Inspect the error; retry with a smaller or corrected task.",
    ),
    "resource_not_found": (
        "list_resources",
        # repair.tool names a server TOOL to call; the authoritative resource-discovery
        # path is the MCP `resources/list` method (not a tool), so tool stays None and
        # the alternative prose names both it and codex_capabilities (#181/F9).
        None,
        False,
        "List the available resource URIs via the MCP resources/list method (or "
        "codex_capabilities), then retry with an exact URI.",
    ),
    "invalid_json": ("retry_then_report", None, True, "Retry; if it persists, report a bug."),
    "schema_violation": (
        "retry_then_report",
        None,
        True,
        "Retry; if it persists, report a bug.",
    ),
    "internal_error": (
        "retry_then_report",
        None,
        True,
        "Retry; if it persists, run codex_status and inspect the repo.",
    ),
    "cli_contract_changed": (
        "update_plugin",
        None,
        False,
        "Update codex-in-claude (the installed codex CLI changed its contract);"
        " or pin codex to a supported version, and run codex_status to check the version.",
    ),
    "codex_rate_limited": (
        "retry_after_delay",
        None,
        True,
        "Wait retry_after_ms before retrying; reduce concurrent codex calls.",
    ),
    "job_not_found": (
        "list_jobs",
        "codex_job_list",
        False,
        "Call codex_job_list to recover known job_ids in this workspace.",
    ),
    "job_running": (
        "poll_job_status",
        "codex_job_status",
        True,
        "Poll codex_job_status until result_available, honoring poll_after_ms.",
    ),
    "job_cancelled": ("start_new_job", None, False, "Start a new job."),
    "job_timeout": ("start_new_job", None, False, "Start a new job."),
    "job_failed": (
        "inspect_and_retry",
        None,
        False,
        "Inspect the failure detail; start a new job.",
    ),
    "idempotency_conflict": (
        "use_new_idempotency_key",
        None,
        False,
        "This idempotency_key was already used with different arguments; resend the"
        " original arguments to replay, or pass a new idempotency_key to run again.",
    ),
    "idempotency_result_unavailable": (
        "use_new_idempotency_key",
        None,
        False,
        "The prior run for this idempotency_key already completed and its result is no"
        " longer available; pass a new idempotency_key to run again.",
    ),
    "idempotency_in_progress": (
        "retry_after_delay",
        None,
        True,
        "A run for this idempotency_key is still starting; retry the same call after"
        " retry_after_ms.",
    ),
}


def make_error(
    code: ErrorCode,
    message: str,
    *,
    retry_after_ms: int | None = None,
    temporary: bool | None = None,
    repair_tool: str | None = None,
    repair_arguments: dict[str, Any] | None = None,
    repair_alternative: str | None = None,
    details: ErrorDetail | None = None,
    invalid_arguments: list[InvalidArgument] | None = None,
    limit_bytes: int | None = None,
    actual_bytes: int | None = None,
    candidate_roots: list[str] | None = None,
) -> ErrorInfo:
    """Build the §6 error envelope for `code`, deriving the symbolic repair from the
    per-code table. `temporary` defaults to the table's value; pass it to override.
    `retry_after_ms` is honored only when the result is temporary. `repair_tool` overrides
    the table's repair tool when the failing tool is known at the call site but not fixed
    per code (e.g. invalid_arguments names the tool that was called — #184/N3)."""
    next_step, tool, temp_default, alt_default = _REPAIR_BY_CODE[code]
    if repair_tool is not None:
        tool = repair_tool
    is_temp = temp_default if temporary is None else temporary
    backoff = retry_after_ms if is_temp else None
    repair = Repair(
        next_step=next_step,
        tool=tool,
        arguments=repair_arguments,
        alternative=repair_alternative or alt_default,
    )
    return ErrorInfo(
        code=code,
        message=message,
        temporary=is_temp,
        retry_after_ms=backoff,
        repair=repair,
        details=details,
        invalid_arguments=invalid_arguments,
        limit_bytes=limit_bytes,
        actual_bytes=actual_bytes,
        candidate_roots=candidate_roots,
    )


def serialize_error_info(error: ErrorInfo) -> dict:
    """Serialize a bare `ErrorInfo`, stripping absent optionals (§8) but ALWAYS retaining
    `retry_after_ms` (§6 wants the key present even when null).

    This is the §6 shape carried in a resource-read failure's JSON-RPC `error.data`
    (audit F9, #181): no `ok`/`meta` wrapper, since a resources/read has no Codex run to
    describe — inventing a tier/sandbox for it would be dishonest. `serialize_error`
    reuses this so the null-retention policy lives in exactly one place."""
    payload = error.model_dump(mode="json", exclude_none=True)
    payload.setdefault("retry_after_ms", None)
    return payload


def serialize_error(result: ErrorResult) -> dict:  # type: ignore[type-arg]
    """Serialize an ErrorResult, stripping absent optionals (§8) but ALWAYS retaining
    `error.retry_after_ms` (§6 wants the key present even when null)."""
    payload = result.model_dump(mode="json", exclude_none=True)
    payload["error"] = serialize_error_info(result.error)
    return payload
