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

from pontonier.core import gitdiff, redaction

from codex_in_claude import codex, normalize, prompts
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
    InvalidArgument,
    Meta,
    RawResponse,
    RedactionSummary,
    ReviewResult,
    ReviewScope,
    Untracked,
    dump_success,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pontonier.core.gitdiff import DiffResult


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
        # #336: the working tree was modified across the gather window, so the
        # summary/diff/untracked may not describe one consistent snapshot. Disclosed as a
        # consistency caveat (not a claim that specific content was omitted) so `complete`
        # cannot silently overpromise; the #319 verdict downgrade (partial + pass -> unknown)
        # then applies. Best-effort — see gitdiff's `_worktree_state_token` for what it does
        # and does not catch.
        if diff.tree_changed_during_gather:
            reasons.append("tree_changed_during_gather")
    else:
        det = inc = omt = None
    if diff.truncated:
        reasons.append("truncated")
    # The `redacted` REASON and the `redaction` FIELD read from deliberately
    # DIFFERENT, asymmetric predicates (#433 review F1 + C1):
    #   * The reason fires from `redacted_paths OR withheld_paths OR masked_paths` —
    #     any signal that redaction touched something. `redacted_paths` (the legacy
    #     union `DiffRedactor.redacted` always populates, unconditionally — see its
    #     own docstring) can be non-empty while the split fields stay empty: a
    #     "legacy-style" DiffResult that only sets the flat union (constructible with
    #     defaults — test_server.py's `test_dry_run_preview` fixture does exactly
    #     this), or — reachable from a REAL gather_diff since #433 review C2 — a
    #     byte-capped diff whose redaction happened entirely past the retained text,
    #     leaving withheld_paths/masked_paths empty while redacted_paths still
    #     reports the full stream. Either way, coverage must never silently report
    #     `complete` when SOMETHING was redacted (C1).
    #   * The `redaction` field stays scoped to the split fields ONLY: it is a
    #     structured breakdown of what a reader of the RETAINED text actually sees,
    #     so it must never fabricate withheld/masked detail for content nobody can
    #     see. A reason with no populated field is fine — `Coverage._check_invariants`
    #     enforces the field⇒reason direction only, never the converse — and this is
    #     exactly that case: the reason can fire on the legacy signal alone while the
    #     field stays `None`.
    # `inline_masks` is part of the split-field signal too (#433 Copilot review of
    # #470, comment 4): a synthetic DiffResult with a nonzero count but empty
    # withheld_paths/masked_paths must not be silently treated as unredacted — it
    # should instead flow into RedactionSummary construction below and fail loudly
    # via that model's own iff invariant, rather than this predicate quietly
    # dropping the count on the floor.
    redacted_via_split = bool(diff.withheld_paths or diff.masked_paths or diff.inline_masks)
    redacted_something = bool(diff.redacted_paths) or redacted_via_split
    redaction = (
        RedactionSummary(
            withheld_paths=diff.withheld_paths,
            masked_paths=diff.masked_paths,
            inline_masks=diff.inline_masks,
        )
        if redacted_via_split
        else None
    )
    if redacted_something:
        reasons.append("redacted")
    return Coverage(
        status="partial" if reasons else "complete",
        untracked_files_detected=det,
        untracked_files_included=inc,
        untracked_files_omitted=omt,
        omission_reasons=reasons,
        redaction=redaction,
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
    # `run.capture_failed` (pontonier 0.8.0) is deliberately NOT read here (#579): on an
    # exit-0 run the answer is the last-message file, so a dead capture thread costs at
    # most the stream-derived usage/session_id, which are documented as optional and read as
    # honestly absent below. Failing the run would discard a correct, paid-for answer.
    # classify_failure names the fault on the failure paths where it misleads.
    usage, session_id = normalize.parse_event_metadata(result.events)
    meta.usage = usage
    meta.session_id = session_id
    # meta.rate_limit stays None: codex 0.144 no longer emits quota on the exec stream (#321).
    # Quota is fetched live (no model spend) by codex_status via account/rateLimits/read, not
    # per paid run — a second app-server spawn on every call would add latency for no benefit.
    if result.run.exit_code != 0 or result.run.binary_missing or result.run.timed_out:
        err = codex.classify_failure(
            result.run,
            last_message=result.last_message,
            events=result.events,
            # meta carries the effort this run sent through the first-class controls,
            # so a backend effort rejection is attributed to the caller's argument
            # (invalid_reasoning_effort), not misread as contract drift (#309).
            reasoning_effort=meta.reasoning_effort,
            # The `-c` keys THIS run pinned, so a config-value rejection naming one is
            # the plugin's own argv and any other is the user's/operator's (#550).
            plugin_config_keys=codex.plugin_config_keys_for(
                sandbox=meta.sandbox,
                reasoning_effort=meta.reasoning_effort,
                # Presence is the signal: meta carries the fingerprint iff THIS run
                # sent the key, and the helper only tests `is not None`. Without this
                # the shipping path never learned the run pinned it, and a
                # strict-config rejection of the plugin's own key was blamed on the
                # user's config.toml (Opus review of #556).
                developer_instructions="sent" if meta.developer_instructions else None,
            ),
        )
        return serialize_error(ErrorResult(error=err, meta=meta))
    return None


# The PROSE members of a parsed structured payload — the text a client renders. Everything
# not named here is left byte-identical, which is the whole point: see `_sanitize_structured`.
_PROSE_KEYS = ("summary", "questions", "assumptions", "next_steps")
# The prose members of one finding. `severity` is a closed enum, and `file`/`line`/`line_end`
# are identifiers a reader uses to locate the code — machine fields, all of them.
_FINDING_PROSE_KEYS = ("title", "evidence", "risk", "recommendation")


def _sanitize_structured(parsed: dict) -> dict:
    """Sanitize the PROSE leaves of a parsed structured payload, by key.

    Deliberately not a blind tree walk. A walk that sanitizes every string also REPAIRS the
    machine fields, and repairing them is worse than leaving them dirty: a `verdict` of
    "pa\x07ss" is not a valid verdict and must degrade to "unknown", but stripping the
    control character turns it into an affirmative "pass" — promoting untrusted model output
    from an invalid judgment to a passing review with "high" confidence. `severity` inverts
    the same way, and `file` is an identifier a reader uses to locate code, so silently
    deleting a byte from it points them somewhere else.

    So the machine fields reach `_enum` and `coerce_findings` exactly as the model wrote
    them, and only text that is rendered as prose is cleaned. This mirrors the split the
    error envelopes already draw between `error.message` and `details.field` (#528; the
    validate-don't-mutate half for machine fields is #529).

    Sanitation still REPLACES the `redact_tree` call rather than running after it — it has
    to be strip-then-redact, and stripping after a redaction pass is the ordering that
    reassembles a control-split secret. Non-prose leaves keep the plain redaction they
    always had.
    """
    out: dict = dict(parsed)
    for key in _PROSE_KEYS:
        if key in out:
            out[key] = _sanitize_prose_value(out[key])
    findings = out.get("findings")
    findings_sanitized = isinstance(findings, list)
    if findings_sanitized:
        out["findings"] = [_sanitize_finding(f) for f in findings]
    # Every remaining leaf keeps the redaction it had before this change — including a
    # `findings` of some other shape, which the branch above does not touch. Excluding the
    # key unconditionally would silently DROP the redaction it used to get; `coerce_findings`
    # discards a non-list today, so that was latent rather than a leak, but the exclusion has
    # to track what was actually sanitized, not the key name.
    for key, value in out.items():
        if key in _PROSE_KEYS or (key == "findings" and findings_sanitized):
            continue
        out[key] = redaction.redact_tree(value)
    return out


def _sanitize_finding(finding: object) -> object:
    if not isinstance(finding, dict):
        return redaction.redact_tree(finding)
    out = dict(finding)
    for key, value in out.items():
        if key in _FINDING_PROSE_KEYS:
            out[key] = _sanitize_prose_value(value)
        else:
            out[key] = redaction.redact_tree(value)
    return out


def _sanitize_prose_value(value: object) -> object:
    """Apply the echo sanitizer to a prose leaf, or to each string in a prose list."""
    if isinstance(value, str):
        return redaction.sanitize_echo_prose(value)
    if isinstance(value, list):
        return [_sanitize_prose_value(v) for v in value]
    return redaction.redact_tree(value)


def _success_common(result: codex.CodexExecResult, meta: Meta) -> tuple[dict | None, RawResponse]:
    """Parse the structured payload (or None for a plain message) and build the shared
    RawResponse. Returns (structured_or_None, raw).

    Inline secret-looking values are redacted from every free-text surface before it
    leaves this process (#58). The two surfaces get DIFFERENT treatment, on purpose (#528):

    - The parsed structured payload (summary/findings/questions/next_steps) is a
      PRESENTATION this server composes, and it is what a client renders. It goes through
      the echo sanitizer, so a control character in model output cannot reach a terminal
      through the field most likely to be displayed.
    - ``raw_response.text`` is the closest-to-source carrier and keeps the bare redaction
      it always had. Not literally byte-identical to the model's output — ``redact_text``
      still runs, and the delegate path also relativizes worktree paths — but THIS change
      does not transform it, so its control characters survive. A caller that needs the
      model's output as it stood reads it there, which is why deleting characters from it
      would be wrong.

    Best-effort defense-in-depth, consistent with the diff redaction the review path already
    applies."""
    structured = normalize.parse_structured(result.last_message)
    if structured is not None:
        structured = cast("dict[str, Any]", _sanitize_structured(structured))
    raw = RawResponse(
        text=redaction.redact_text(result.last_message),
        session_id=meta.session_id,
        model=meta.model,
    )
    return structured, raw


def _summary_of(structured: dict) -> str:
    """The rendered summary, sanitized after stringification.

    `_sanitize_structured` already cleans a string- or list-valued `summary`, but the model
    can return one of any JSON shape, and a dict lands here as `str(dict)`. Python's repr
    happens to escape control characters inside a nested string, so that shape does not leak
    today — but relying on an incidental property of repr to hold a security guarantee is
    not a guarantee. Sanitizing the final string makes it explicit and survives a future
    change to how this is formatted."""
    return redaction.sanitize_echo_prose(str(structured.get("summary") or "")).strip() or (
        "(no summary)"
    )


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
            # The PRESENTATION copy, sanitized like every other rendered field (#528);
            # `raw_response` below keeps the exact-content product. Built from
            # `result.last_message`, not from `raw.text`: sanitizing `raw.text` would be
            # strip-AFTER-redact, the ordering that reassembles a control-split secret.
            summary=(redaction.sanitize_echo_prose(result.last_message)).strip()
            or "(codex returned no message)",
            raw_response=raw,
            meta=meta,
        )
    )


def _review_invalid_response_error(code: str, last_message: str | None, meta: Meta) -> dict:
    """Build the explicit error for an exit-0 review whose output ignored the schema
    (#159). Unlike consult's prose-passthrough, review's value is the structured
    verdict/findings, so a missing/non-object payload is surfaced rather than silently
    downgraded to verdict="unknown". The raw text is preserved as a bounded, sanitized
    preview for debugging (ErrorResult carries no raw_response field). It is MODEL text
    quoted back into an error, so it goes through the echo sanitizer — control characters
    deleted ahead of redaction (#528) — not through bare redaction."""
    preview = redaction.sanitize_echo_prose(last_message).strip()[:300]
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
    structured = cast("dict[str, Any]", _sanitize_structured(cast("dict", parsed)))
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
    gitdiff.InvalidUntrackedError: ("invalid_arguments", "untracked"),
    gitdiff.NotAGitRepoError: ("not_a_git_repo", "workspace_root"),
    gitdiff.GitUnavailableError: ("git_unavailable", None),
}

# The gitdiff exceptions run_review/dry_run catch and map to error envelopes.
GITDIFF_EXCEPTIONS = (
    gitdiff.InvalidScopeError,
    gitdiff.InvalidBaseError,
    gitdiff.InvalidCommitError,
    gitdiff.InvalidPathsError,
    gitdiff.InvalidUntrackedError,
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
    # InvalidUntrackedError is the one exception here mapped to invalid_arguments, which
    # owes the per-argument list docs/REFERENCE.md promises for that code (#416). Its
    # reason is BUILT from the known domain rather than reused from `str(exc)`: the
    # exception text embeds the rejected value (`got {untracked!r}`), and InvalidArgument
    # promises never to echo one — it may be a secret, and this path's value is
    # caller-supplied free text. The human-readable `message` below reuses that same
    # value-free reason instead of `str(exc)` (#418): a message built from `str(exc)`
    # would print exactly the value the machine fields withhold. The other seven branches
    # (invalid_base, invalid_commit, invalid_paths, invalid_scope, not_a_git_repo,
    # git_unavailable, and the RuntimeError fallback) keep echoing `str(exc)` unchanged —
    # only this branch's guarantee text promises no echo. Their messages are not uniformly
    # caller-supplied refs/paths: five are (invalid_base/invalid_commit/invalid_paths/
    # invalid_scope/not_a_git_repo), but git_unavailable and the RuntimeError fallback
    # carry bounded, best-effort-redacted git diagnostics (a missing executable, stderr, a
    # timeout) that this fix leaves alone.
    args: list[InvalidArgument] | None = None
    if code == "invalid_arguments" and offending:
        reason = (
            f"{offending} must be one of "
            + ", ".join(repr(v) for v in sorted(get_args(Untracked)))
            + "."
        )
        args = [
            InvalidArgument(
                field=offending, reason=reason, allowed_values=list(get_args(Untracked))
            )
        ]
        details = None  # derived from the entry by make_error, so the two cannot drift
        message = reason[:300]
    else:
        message = redaction.sanitize_echo_prose(str(exc))[:300]
    return serialize_error(
        ErrorResult(
            error=make_error(
                cast("ErrorCode", code),
                message,
                details=details,
                invalid_arguments=args,
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
    developer_instructions: str | None = None,
    extra_context: str = "",
    on_event: Callable[[str], None] | None = None,
) -> dict:
    """Run a read-only consult and return the ConsultResult/ErrorResult envelope."""
    prompt = prompts.build_consult_prompt(question, extra_context or "")
    result = await codex.run_codex_exec(
        prompt,
        # kind drives backend policy in CodexBackend.prepare — including the
        # consult-only repo-check skip that used to be passed inline here.
        kind="consult",
        cwd=cwd,
        sandbox=sandbox,
        isolation=isolation,
        timeout_seconds=timeout_seconds,
        model=model,
        reasoning_effort=reasoning_effort,
        developer_instructions=developer_instructions,
        output_schema=CONSULT_OUTPUT_SCHEMA,
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
    developer_instructions: str | None = None,
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
            # Repair guidance depends on the active policy: under `exclude`, naming files
            # in `paths` still won't review them, so only `include` will (#322 F4).
            remedy = (
                'Re-run with untracked="include" to review them.'
                if untracked == "exclude"
                else 'Re-run with untracked="include", or name them in paths, to review them.'
            )
            summary = (
                f"No reviewable changes were gathered for scope={scope}, but {omitted} "
                f"untracked file(s) were detected and omitted (see coverage). {remedy}"
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
        kind="review_changes",
        cwd=cwd,
        sandbox=sandbox,
        isolation=isolation,
        timeout_seconds=timeout_seconds,
        model=model,
        reasoning_effort=reasoning_effort,
        developer_instructions=developer_instructions,
        output_schema=FINDINGS_OUTPUT_SCHEMA,
        on_event=on_event,
    )
    return finalize_review(result, meta=meta, coverage=coverage)
