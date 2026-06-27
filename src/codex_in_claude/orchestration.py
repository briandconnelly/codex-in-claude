"""Import-light orchestration for the read-only tiers (consult, review).

Both the synchronous tools in ``server.py`` and the detached ``_worker.py`` call
these, so this module must NOT import the FastMCP app (``server``) — like
``delegate.run_delegate`` for the propose tier. It builds the prompt, runs
``codex exec``, and finalizes the structured result envelope. For review it also
gathers and validates the diff *before* any model call, so an async review job that
hits a bad scope/base/commit spends nothing.
"""

from __future__ import annotations

from typing import Any, cast, get_args

from codex_in_claude import codex, normalize, prompts, rate_limit
from codex_in_claude._core import gitdiff, redaction
from codex_in_claude.errors import make_error, serialize_error
from codex_in_claude.schemas import (
    CONSULT_OUTPUT_SCHEMA,
    FINDINGS_OUTPUT_SCHEMA,
    ConsultResult,
    ContextSummary,
    ErrorCode,
    ErrorDetail,
    ErrorResult,
    Meta,
    RawResponse,
    ReviewResult,
    ReviewScope,
)

# --------------------------------------------------------------------------- #
# Shared finalization (process metadata -> structured envelope)
# --------------------------------------------------------------------------- #


def _stamp_meta(result: codex.CodexExecResult, meta: Meta) -> dict | None:
    """Stamp a finished run's process metadata onto meta. Return an ErrorResult dict
    if the run failed, else None (caller builds the tool-specific success result)."""
    meta.elapsed_ms = result.run.elapsed_ms
    meta.command_exit_code = result.run.exit_code
    meta.compat_warnings = result.dropped_flags
    usage, session_id = normalize.parse_event_metadata(result.events)
    meta.usage = usage
    meta.session_id = session_id
    meta.rate_limit = rate_limit.capture(result.events)
    if result.run.exit_code != 0 or result.run.binary_missing or result.run.timed_out:
        err = codex.classify_failure(
            result.run, last_message=result.last_message, events=result.events
        )
        return serialize_error(ErrorResult(error=err, meta=meta))
    return None


def _success_common(result: codex.CodexExecResult, meta: Meta) -> tuple[dict | None, RawResponse]:
    """Parse the structured payload (or None for a plain message) and build the shared
    RawResponse. Returns (structured_or_None, raw).

    Inline secret-looking values are redacted from every free-text surface before it
    leaves this process (#58): the parsed structured payload (summary/findings/etc.)
    via redact_tree, and raw_response.text via redact_text. Best-effort defense-in-
    depth, consistent with the diff redaction the review path already applies."""
    structured = normalize.parse_structured(result.last_message)
    if structured is not None:
        structured = cast("dict[str, Any]", redaction.redact_tree(structured))
    raw = RawResponse(
        text=redaction.redact_text(result.last_message),
        session_id=meta.session_id,
        model=meta.model,
    )
    return structured, raw


def _summary_of(structured: dict) -> str:
    return str(structured.get("summary") or "").strip() or "(no summary)"


def _enum(value: object, allowed: tuple[str, ...], default: str) -> Any:
    return value if isinstance(value, str) and value in allowed else default


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, (str, int, float))]


def finalize_consult(result: codex.CodexExecResult, *, meta: Meta) -> dict:
    """Build a ConsultResult/ErrorResult dict — Q&A, so no verdict/confidence (#31)."""
    err = _stamp_meta(result, meta)
    if err is not None:
        return err
    structured, raw = _success_common(result, meta)
    if structured is not None:
        return ConsultResult(
            summary=_summary_of(structured),
            findings=normalize.coerce_findings(structured.get("findings")),
            questions=_str_list(structured.get("questions")),
            assumptions=_str_list(structured.get("assumptions")),
            next_steps=_str_list(structured.get("next_steps")),
            raw_response=raw,
            meta=meta,
        ).model_dump(mode="json")
    return ConsultResult(
        summary=(raw.text or "").strip() or "(codex returned no message)",
        raw_response=raw,
        meta=meta,
    ).model_dump(mode="json")


def finalize_review(result: codex.CodexExecResult, *, meta: Meta) -> dict:
    """Build a ReviewResult/ErrorResult dict — the only verdict-bearing result."""
    err = _stamp_meta(result, meta)
    if err is not None:
        return err
    structured, raw = _success_common(result, meta)
    if structured is not None:
        return ReviewResult(
            summary=_summary_of(structured),
            verdict=_enum(
                structured.get("verdict"), ("pass", "concerns", "fail", "unknown"), "unknown"
            ),
            confidence=_enum(structured.get("confidence"), ("low", "medium", "high"), "medium"),
            findings=normalize.coerce_findings(structured.get("findings")),
            questions=_str_list(structured.get("questions")),
            assumptions=_str_list(structured.get("assumptions")),
            next_steps=_str_list(structured.get("next_steps")),
            raw_response=raw,
            meta=meta,
        ).model_dump(mode="json")
    return ReviewResult(
        summary=(raw.text or "").strip() or "(codex returned no message)",
        raw_response=raw,
        meta=meta,
    ).model_dump(mode="json")


# --------------------------------------------------------------------------- #
# gitdiff exception -> structured error envelope
# --------------------------------------------------------------------------- #
_GITDIFF_ERRORS: dict[type, tuple[str, str | None]] = {
    gitdiff.InvalidScopeError: ("invalid_scope", "scope"),
    gitdiff.InvalidBaseError: ("invalid_base", "base"),
    gitdiff.InvalidCommitError: ("invalid_commit", "commit"),
    gitdiff.InvalidPathsError: ("invalid_paths", "paths"),
    gitdiff.NotAGitRepoError: ("not_a_git_repo", "workspace_root"),
    gitdiff.GitUnavailableError: ("git_unavailable", None),
}

# The gitdiff exceptions run_review/dry_run catch and map to error envelopes.
GITDIFF_EXCEPTIONS = (
    gitdiff.InvalidScopeError,
    gitdiff.InvalidBaseError,
    gitdiff.InvalidCommitError,
    gitdiff.InvalidPathsError,
    gitdiff.NotAGitRepoError,
    gitdiff.GitUnavailableError,
    RuntimeError,
)


def gitdiff_error(exc: Exception, meta: Meta) -> dict:
    code, offending = _GITDIFF_ERRORS.get(type(exc), ("git_unavailable", None))
    # Only invalid_scope is enum-like; the rest take free-form refs/paths.
    allowed = list(get_args(ReviewScope)) if code == "invalid_scope" else None
    details = (
        ErrorDetail(field=offending, allowed_values=allowed) if (offending or allowed) else None
    )
    return serialize_error(
        ErrorResult(
            error=make_error(cast("ErrorCode", code), str(exc)[:300], details=details),
            meta=meta,
        )
    )


# --------------------------------------------------------------------------- #
# Read-only run orchestration
# --------------------------------------------------------------------------- #
async def run_consult(
    question: str,
    cwd: str,
    meta: Meta,
    *,
    sandbox: str,
    isolation: str,
    timeout_seconds: int,
    model: str | None,
    extra_context: str = "",
) -> dict:
    """Run a read-only consult and return the ConsultResult/ErrorResult envelope."""
    prompt = prompts.build_consult_prompt(question, extra_context or "")
    result = await codex.run_codex_exec(
        prompt,
        cwd=cwd,
        sandbox=sandbox,
        isolation=isolation,
        timeout_seconds=timeout_seconds,
        model=model,
        output_schema=CONSULT_OUTPUT_SCHEMA,
        # consult is read-only Q&A; repo membership is irrelevant, so never let a
        # non-repo workspace block the run.
        skip_git_repo_check=True,
    )
    return finalize_consult(result, meta=meta)


def review_label(scope: str, base: str | None, commit: str | None) -> str:
    if scope == "commit":
        return f"commit {commit}"
    if scope == "branch":
        return f"branch {base}...HEAD"
    return scope


async def run_review(
    cwd: str,
    meta: Meta,
    *,
    scope: str,
    base: str | None,
    commit: str | None,
    paths: list[str] | None,
    sandbox: str,
    isolation: str,
    timeout_seconds: int,
    model: str | None,
    git_timeout: int,
    max_bytes: int,
    extra_context: str = "",
) -> dict:
    """Gather + validate the diff, then run a read-only review. The diff is gathered
    BEFORE any model call, so a bad scope/base/commit returns a structured error with
    zero spend (the same guarantee whether called sync or from a background job).

    `extra_context` (optional author intent) is bounded by the same `max_bytes` limit
    as the diff and appended to the prompt as untrusted data."""
    extra_context_bytes = len(extra_context.encode("utf-8"))
    if extra_context_bytes > max_bytes:
        return serialize_error(
            ErrorResult(
                error=make_error(
                    "input_too_large",
                    f"extra_context exceeds {max_bytes} bytes.",
                    limit_bytes=max_bytes,
                    actual_bytes=extra_context_bytes,
                    details=ErrorDetail(field="extra_context"),
                    repair_alternative=(
                        "Trim extra_context or raise CODEX_IN_CLAUDE_MAX_INPUT_BYTES."
                    ),
                ),
                meta=meta,
            )
        )
    try:
        diff = gitdiff.gather_diff(
            cwd,
            scope,
            base=base,
            commit=commit,
            paths=paths,
            timeout=git_timeout,
            max_bytes=max_bytes,
        )
    except GITDIFF_EXCEPTIONS as exc:
        return gitdiff_error(exc, meta)

    meta.context_summary = ContextSummary(
        files_changed=diff.summary.files_changed,
        lines_added=diff.summary.lines_added,
        lines_removed=diff.summary.lines_removed,
    )
    meta.redacted_paths = diff.redacted_paths
    meta.truncated = diff.truncated
    meta.truncation_hint = diff.truncation_hint

    if diff.summary.files_changed == 0 and not diff.text.strip():
        return ReviewResult(
            summary=f"No changes to review for scope={scope}.",
            verdict="pass",
            confidence="high",
            meta=meta,
        ).model_dump(mode="json")

    prompt = prompts.build_review_prompt(
        diff.text, review_label(scope, base, commit), extra_context or ""
    )
    result = await codex.run_codex_exec(
        prompt,
        cwd=cwd,
        sandbox=sandbox,
        isolation=isolation,
        timeout_seconds=timeout_seconds,
        model=model,
        output_schema=FINDINGS_OUTPUT_SCHEMA,
    )
    return finalize_review(result, meta=meta)
