"""Import-light orchestration for the read-only tiers (consult, review).

Both the synchronous tools in ``server.py`` and the detached ``_worker.py`` call
these, so this module must NOT import the FastMCP app (``server``) — like
``delegate.run_delegate`` for the propose tier. It builds the prompt, runs
``codex exec``, and finalizes the structured result envelope. For review it also
gathers and validates the diff *before* any model call, so an async review job that
hits a bad scope/base/commit spends nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast, get_args

from codex_in_claude import codex, normalize, prompts, rate_limit
from codex_in_claude._core import gitdiff, redaction
from codex_in_claude.errors import make_error, serialize_error
from codex_in_claude.schemas import (
    CONSULT_OUTPUT_SCHEMA,
    FINDINGS_OUTPUT_SCHEMA,
    ConsultResult,
    ContextSummary,
    Coverage,
    CoverageOmissionReason,
    ErrorCode,
    ErrorDetail,
    ErrorResult,
    Meta,
    RawResponse,
    ReviewResult,
    ReviewScope,
    dump_success,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from codex_in_claude._core.gitdiff import DiffResult


def build_coverage(*, scope: str, diff: DiffResult) -> Coverage:
    """Derive the agent-visible Coverage from a gathered diff and its scope (#319).

    `complete` is a strict claim — it holds only when nothing in scope was left
    unreviewed. Untracked omission, byte-cap truncation, and secret redaction each make
    coverage `partial`, since each hides changed content from the model. Untracked counts
    are scoped to the review's pathspec (`diff.untracked_detected`) and are N/A (None) for
    non-working_tree scopes, where untracked files are irrelevant. Reasons are emitted in a
    fixed order for deterministic output."""
    reasons: list[CoverageOmissionReason] = []
    if scope == "working_tree":
        detected = diff.untracked_detected or 0
        included = diff.untracked_included
        omitted = max(0, detected - included)
        det: int | None = detected
        inc: int | None = included
        omt: int | None = omitted
        if omitted > 0:
            reasons.append("untracked_omitted")
    else:
        det = inc = omt = None
    if diff.truncated:
        reasons.append("truncated")
    if diff.redacted_paths:
        reasons.append("redacted")
    return Coverage(
        status="partial" if reasons else "complete",
        untracked_files_detected=det,
        untracked_files_included=inc,
        untracked_files_omitted=omt,
        omission_reasons=reasons,
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
    codex.reconcile_dropped_model(result, meta)
    usage, session_id = normalize.parse_event_metadata(result.events)
    meta.usage = usage
    meta.session_id = session_id
    meta.rate_limit = rate_limit.capture(result.events)
    if result.run.exit_code != 0 or result.run.binary_missing or result.run.timed_out:
        err = codex.classify_failure(
            result.run,
            last_message=result.last_message,
            events=result.events,
            # meta carries the effort this run sent through the first-class controls,
            # so a backend effort rejection is attributed to the caller's argument
            # (invalid_reasoning_effort), not misread as contract drift (#309).
            reasoning_effort=meta.reasoning_effort,
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
        return dump_success(
            ConsultResult(
                summary=_summary_of(structured),
                findings=normalize.coerce_findings(structured.get("findings")),
                questions=_str_list(structured.get("questions")),
                assumptions=_str_list(structured.get("assumptions")),
                next_steps=_str_list(structured.get("next_steps")),
                raw_response=raw,
                meta=meta,
            )
        )
    # Deliberate prose-passthrough exception (#159): consult is Q&A, so a plain-language
    # answer is itself a valid result. Unlike review (whose value is the structured
    # verdict/findings), there is nothing to mislead here — the prose maps onto `summary`
    # — so exit-0 non-JSON is surfaced as the answer rather than the
    # invalid_json/schema_violation error the strict review path now raises.
    return dump_success(
        ConsultResult(
            summary=(raw.text or "").strip() or "(codex returned no message)",
            raw_response=raw,
            meta=meta,
        )
    )


def _review_invalid_response_error(code: str, last_message: str | None, meta: Meta) -> dict:
    """Build the explicit error for an exit-0 review whose output ignored the schema
    (#159). Unlike consult's prose-passthrough, review's value is the structured
    verdict/findings, so a missing/non-object payload is surfaced rather than silently
    downgraded to verdict="unknown". The raw text is preserved as a bounded, secret-
    redacted preview for debugging (ErrorResult carries no raw_response field)."""
    preview = (redaction.redact_text(last_message) or "").strip()[:300]
    tail = f" Raw output preview: {preview}" if preview else ""
    message = (
        "codex exited 0 but did not return a schema-valid JSON object for the review "
        f"(--output-schema appears to have been ignored).{tail}"
    )
    return serialize_error(
        ErrorResult(error=make_error(cast("ErrorCode", code), message), meta=meta)
    )


def _apply_coverage(
    verdict: str, confidence: str, summary: str, coverage: Coverage
) -> tuple[str, str, str]:
    """Fold coverage into the *overall* conclusion (#319). A model `pass` over partly
    reviewed code is surfaced as `unknown`/`low` with a caveat prefixed to the summary,
    so the model's all-clear prose never stands unqualified beside `verdict: unknown`.
    Concrete `fail`/`concerns` are left untouched — partial coverage cannot invalidate a
    demonstrated defect."""
    if coverage.status == "partial" and verdict == "pass":
        reasons = ", ".join(coverage.omission_reasons) or "unreviewed content"
        return (
            "unknown",
            "low",
            f"Overall verdict is unknown because coverage is partial ({reasons}); the "
            f"model reported no blocking concerns in the reviewed portion. {summary}",
        )
    return verdict, confidence, summary


def finalize_review(result: codex.CodexExecResult, *, meta: Meta, coverage: Coverage) -> dict:
    """Build a ReviewResult/ErrorResult dict — the only verdict-bearing result.

    Strict on exit-0 unparseable output (#159): the structured verdict/findings *are*
    the product here, so a successful run whose last message is not a JSON object is an
    explicit invalid_json/schema_violation error rather than a prose downgrade. (consult
    deliberately keeps the prose-passthrough — see ``finalize_consult``.)

    `coverage` describes what the model was actually shown; it can downgrade a `pass`
    (see ``_apply_coverage``) but never touches the retained findings."""
    err = _stamp_meta(result, meta)
    if err is not None:
        return err
    status, parsed = normalize.classify_structured(result.last_message)
    if status != "ok":
        return _review_invalid_response_error(status, result.last_message, meta)
    structured = cast("dict[str, Any]", redaction.redact_tree(cast("dict", parsed)))
    raw = RawResponse(
        text=redaction.redact_text(result.last_message),
        session_id=meta.session_id,
        model=meta.model,
    )
    verdict, confidence, summary = _apply_coverage(
        _enum(structured.get("verdict"), ("pass", "concerns", "fail", "unknown"), "unknown"),
        _enum(structured.get("confidence"), ("low", "medium", "high"), "medium"),
        _summary_of(structured),
        coverage,
    )
    return dump_success(
        ReviewResult(
            summary=summary,
            verdict=cast("Any", verdict),
            confidence=cast("Any", confidence),
            review_status="completed",
            coverage=coverage,
            findings=normalize.coerce_findings(structured.get("findings")),
            questions=_str_list(structured.get("questions")),
            assumptions=_str_list(structured.get("assumptions")),
            next_steps=_str_list(structured.get("next_steps")),
            raw_response=raw,
            meta=meta,
        )
    )


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
            error=make_error(
                cast("ErrorCode", code),
                (redaction.redact_text(str(exc)) or "")[:300],
                details=details,
            ),
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
    reasoning_effort: str | None = None,
    extra_context: str = "",
    on_event: Callable[[str], None] | None = None,
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
        reasoning_effort=reasoning_effort,
        output_schema=CONSULT_OUTPUT_SCHEMA,
        # consult is read-only Q&A; repo membership is irrelevant, so never let a
        # non-repo workspace block the run.
        skip_git_repo_check=True,
        on_event=on_event,
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
    untracked: str = "explicit_only",
    sandbox: str,
    isolation: str,
    timeout_seconds: int,
    model: str | None,
    reasoning_effort: str | None = None,
    git_timeout: int,
    max_bytes: int,
    extra_context: str = "",
    on_event: Callable[[str], None] | None = None,
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
            untracked=untracked,
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
    coverage = build_coverage(scope=scope, diff=diff)

    if diff.summary.files_changed == 0 and not diff.text.strip():
        # Nothing reviewable was gathered, so the model is NOT called. This is a
        # `not_run`/`unknown` result — never a `pass` — and coverage discloses whether
        # anything (untracked files) was omitted rather than genuinely absent (#319).
        omitted = coverage.untracked_files_omitted or 0
        if omitted > 0:
            summary = (
                f"No reviewable changes were gathered for scope={scope}, but {omitted} "
                "untracked file(s) were detected and omitted (see coverage). Re-run with "
                'untracked="include", or name them in paths, to review them.'
            )
        else:
            summary = f"No changes to review for scope={scope}."
        return dump_success(
            ReviewResult(
                summary=summary,
                verdict="unknown",
                confidence="low",
                review_status="not_run",
                coverage=coverage,
                meta=meta,
            )
        )

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
        reasoning_effort=reasoning_effort,
        output_schema=FINDINGS_OUTPUT_SCHEMA,
        on_event=on_event,
    )
    return finalize_review(result, meta=meta, coverage=coverage)
