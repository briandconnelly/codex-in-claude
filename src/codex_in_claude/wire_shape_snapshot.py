"""Canonical snapshot of the DELIVERED (wire) success-envelope surface (issue #334).

The sibling ``result_format_snapshot`` pins what a worker writes to ``result.json``;
this pins what a caller actually receives, which since #334 is a different shape: the
delivery chokepoint drops ``meta``'s null-valued keys. Neither existing guard can see
that change — the manifest snapshot captures schemas (unmoved by a serializer change)
and the result-format snapshot renders only through ``dump_success``/``serialize_error``
(deliberately untouched here, so ``RESULT_FORMAT`` must NOT move). Without this fixture
the omission would ship with no reviewable evidence of exactly which keys disappeared.

Both detail levels are rendered because they are separate wire shapes: ``summary`` nulls
``raw_response.text`` before slimming runs, and that null is retained (only ``meta``
sheds keys), so the fixture also pins ``apply_detail``'s guarantee against a future
broadening of the omission rule.

Rendered by driving the REAL chokepoint, ``server._finished_job_envelope``, rather than
by re-applying its steps here. Re-implementing them looked equivalent and was not: the
chokepoint stamps ``meta.job_id`` before trimming, so a hand-rolled pipeline reported
``job_id`` as an omitted key when delivery never omits it, and — worse — a snapshot that
calls the shaping helpers itself stays green when the *wiring* is removed, leaving the
fixture blind to the one regression it exists to catch (Codex review of #334).

The representative metas POPULATE their applicable optional fields (#400). They once left
every optional null, which made the fixture blind in a way that reads as coverage: a null
optional is already absent from a delivered envelope, so a regression deleting a
*populated* optional key rendered a byte-identical fixture. That blindness was repo-wide,
not local to this module — ``slim_meta`` was measurably free to destroy ``meta.model`` and
``meta.session_id`` on every delivered envelope with the whole suite green. Which fields
each envelope populates, and why three of them are populated nowhere, is documented on the
builders below.
"""

from __future__ import annotations

import json
from typing import Any

from codex_in_claude.schemas import (
    FINGERPRINT,
    RESULT_FORMAT,
    ConsultResult,
    ContextSummary,
    Coverage,
    DelegateResult,
    DeveloperInstructions,
    Meta,
    RawResponse,
    ReviewResult,
    Usage,
    dump_success,
    workspace_warning_for,
)
from codex_in_claude.server import _finished_job_envelope

_FINGERPRINT_SENTINEL = "<fingerprint>"
_VERSION_SENTINEL = "0.0.0"
_REQUEST_ID_SENTINEL = "0" * 32
# A FIXED literal like every other sentinel here: the real value comes from a live
# `codex --version` probe, so rendering the true one would churn the fixture on every
# codex upgrade (#519).
_CODEX_VERSION_SENTINEL = "codex-cli 0.0.0"


def _meta(**optional: Any) -> Meta:
    """A representative meta: the six required fields, pinned sentinels, and whatever
    optional fields the caller populates.

    Every value here must be a FIXED literal. Nothing may come from the clock, the
    environment, or a random source, or the fixture churns on every render — which is
    also why `rate_limit` is populated nowhere below (`RateLimit.as_of` and its windows'
    `resets_at` are timestamps). See `_consult_meta` for the other reason it stays out.
    """
    meta = Meta(
        cwd="/repo",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=1,
        elapsed_ms=1,
        **optional,
    )
    meta.fingerprint = _FINGERPRINT_SENTINEL
    meta.server_version = _VERSION_SENTINEL
    meta.request_id = _REQUEST_ID_SENTINEL
    return meta


def _consult_meta() -> Meta:
    """A consult's meta with its applicable optionals POPULATED (#400).

    Populated on purpose: an optional field left null is slimmed away at delivery, so a
    regression that unconditionally deletes it leaves this fixture byte-identical. Every
    field below is one whose loss the snapshot can now see.

    What is deliberately NOT here, because a real worker could never persist it into a
    stored consult success — putting it in would document an impossible producer state:
      * `job_kind`         — set only when a codex_job_* call resolved an existing
                             record, and null "for every non-lifecycle call".
      * `idempotency_replayed` — patched onto an outgoing replay response and "never
                             persisted into a job's result.json".
      * `rate_limit`       — null on codex 0.144+; quota no longer rides the exec stream.
                             (It is also the one nested model carrying timestamps.)
    Their survival is pinned at the unit level instead, by
    `TestSlimMetaPopulatedOptionals` in tests/test_schemas.py.
    """
    return _meta(
        workspace_source="param",
        roots_source="client",
        model="a-model",
        reasoning_effort="high",
        # A run prepared with the #556 parameter: the fingerprint (never the text)
        # must survive slimming and replay (#400: populate every producible optional).
        developer_instructions=DeveloperInstructions.of("focus on locking"),
        # The codex build observed next to the run (#519). Populated on every builder a
        # real run backs, per #400: left null it would slim away at delivery and a
        # regression deleting it would render this fixture byte-identical.
        codex_version=_CODEX_VERSION_SENTINEL,
        # 0 on purpose: a POPULATED FALSY optional. `slim_meta` keys on `is None`, never
        # falsiness, and this extends that guarantee from the unit test to the real
        # chokepoint. Do not "fix" it to a non-zero exit code.
        command_exit_code=0,
        session_id="sess-1",
        usage=Usage(input_tokens=1, output_tokens=2, cached_input_tokens=3, total_tokens=6),
    )


def _review_meta() -> Meta:
    """A review's meta: everything a consult reports, plus the review-scope fields.

    `scope="branch"` carries `base` and `paths` but NOT `commit` — the two are
    alternatives, and a meta reporting both describes a run that cannot happen.
    """
    return _meta(
        workspace_source="param",
        roots_source="client",
        model="a-model",
        reasoning_effort="high",
        # Producible on review too (#556); left null here it would list among
        # review's omitted keys and hide a review-specific delivery regression
        # (Copilot, #559; the #400 convention).
        developer_instructions=DeveloperInstructions.of("focus"),
        codex_version=_CODEX_VERSION_SENTINEL,
        command_exit_code=0,
        session_id="sess-1",
        usage=Usage(input_tokens=1, output_tokens=2, cached_input_tokens=3, total_tokens=6),
        scope="branch",
        base="main",
        paths=["src/a.py"],
        context_summary=ContextSummary(files_changed=1, lines_added=2, lines_removed=3),
    )


def _review_commit_truncated_meta() -> Meta:
    """The optional states the other builders CANNOT reach, in one producible run.

    Three fields stayed null everywhere after the first pass at #400, leaving the fixture
    blind to their deletion even though real producers set all three — `_worker` stamps
    `commit` and `workspace_warning`, `orchestration` stamps `truncation_hint`. Each needs
    a run state the consult/branch-review builders exclude:
      * `commit`            — scope="commit"; the alternative to branch's base/paths.
      * `truncation_hint`   — a diff that hit the byte cap (so `truncated` is True here,
                              and the coverage below is `partial` for that reason).
      * `workspace_warning` — a workspace resolved from the server's own cwd, so
                              `workspace_source` is "cwd" rather than "param".
    None of those conflict, so one envelope covers all three: a commit-scope review, of a
    repo resolved by fallback, whose diff was truncated. The warning text is built by the
    production helper rather than hand-copied, so it cannot drift from what callers see.
    """
    return _meta(
        workspace_source="cwd",
        workspace_warning=workspace_warning_for("cwd", "/repo"),
        roots_source="not_negotiated",
        model="a-model",
        reasoning_effort="high",
        developer_instructions=DeveloperInstructions.of("focus"),
        codex_version=_CODEX_VERSION_SENTINEL,
        command_exit_code=0,
        session_id="sess-1",
        usage=Usage(input_tokens=1, output_tokens=2, cached_input_tokens=3, total_tokens=6),
        scope="commit",
        commit="0" * 40,
        context_summary=ContextSummary(files_changed=1, lines_added=2, lines_removed=3),
        truncated=True,
        truncation_hint="diff truncated at the byte cap; narrow the scope with paths",
    )


def _sparse_meta() -> Meta:
    """A meta with EVERY optional left null — the omission case, kept deliberately.

    If every representative envelope were fully populated, `omitted_meta_keys` would go
    empty everywhere and the fixture would stop showing that delivery drops anything at
    all. One sparse envelope keeps the omission itself under review.
    """
    return _meta()


def _fallback_meta() -> Meta:
    """The meta the chokepoint falls back to for a GENERATED envelope (corruption, a
    lifecycle error) rather than a delivered one.

    Separate from the success builders above by design: it describes the failing
    lifecycle call, not the run that produced the payload, so the two must be free to
    diverge. Nothing in this module's happy path reads it — `_deliver` treats a
    non-delivered envelope as an error — but sharing one builder invited the assumption
    that the success examples and the error context must stay in step.
    """
    return _meta()


def _raw() -> RawResponse:
    return RawResponse(text="RAW MODEL TEXT", session_id="sess-1", model="a-model")


def _stored_envelopes() -> dict[str, dict]:
    """The persisted envelopes, exactly as a worker writes them (nulls retained).

    The three metas differ on purpose (#400). consult and review populate their
    applicable optionals, so deleting one of those keys anywhere in the delivery path
    moves this fixture; the no-changes delegate keeps a sparse meta, so the omission that
    delivery performs stays visible in `omitted_meta_keys`. Populating all three would
    have closed the deletion blind spot and opened an omission one.
    """
    return {
        "consult": dump_success(
            ConsultResult(summary="s", raw_response=_raw(), meta=_consult_meta())
        ),
        "review": dump_success(
            ReviewResult(
                summary="s",
                review_status="completed",
                coverage=Coverage(
                    status="complete",
                    untracked_files_detected=0,
                    untracked_files_included=0,
                    untracked_files_omitted=0,
                ),
                raw_response=_raw(),
                meta=_review_meta(),
            )
        ),
        # The states `review` above cannot hold at once — commit scope, a truncated
        # diff, a cwd-resolved workspace — so every optional a producer can set is
        # populated SOMEWHERE across this matrix. `test_every_producible_optional_is_
        # populated_somewhere` in tests/test_wire_shape.py enforces exactly that.
        "review_commit_truncated": dump_success(
            ReviewResult(
                summary="s",
                review_status="completed",
                coverage=Coverage(status="partial", omission_reasons=["truncated"]),
                raw_response=_raw(),
                meta=_review_commit_truncated_meta(),
            )
        ),
        # diff=None on purpose: a no-changes delegate. Its `diff` key must survive
        # delivery — it is the field the result's own next_steps tells callers to read.
        "delegate_no_changes": dump_success(
            DelegateResult(summary="s", diff=None, raw_response=_raw(), meta=_sparse_meta())
        ),
    }


_JOB_ID_SENTINEL = "0" * 32
_KIND_BY_NAME = {
    "consult": "codex_consult",
    "review": "codex_review_changes",
    "review_commit_truncated": "codex_review_changes",
    "delegate_no_changes": "codex_delegate",
}


def _deliver(stored: dict, name: str, detail: str) -> dict:
    """Render one envelope through the PRODUCTION delivery path."""
    # The terminal record the chokepoint reads: `status` selects the stored-payload
    # branch and `extra.result_format` must match RESULT_FORMAT, or the payload is
    # classified as a cross-release incompatibility instead of being delivered.
    rec = {"status": "done", "extra": {"result_format": RESULT_FORMAT}}
    envelope, delivered = _finished_job_envelope(
        rec,
        json.loads(json.dumps(stored)),  # a fresh dict, as a real disk read produces
        _JOB_ID_SENTINEL,
        _KIND_BY_NAME[name],
        _fallback_meta(),
        detail,
        None,
    )
    if not delivered:
        raise AssertionError(f"{name}: the chokepoint refused to deliver the payload")
    # The chokepoint stamps meta.fingerprint from the live FINGERPRINT, which would churn
    # the fixture on every bump — so pin it to a sentinel. ASSERT FIRST: overwriting
    # unconditionally would render an identical fixture whether or not the chokepoint
    # still stamps at all, silently un-pinning the very step this module claims to cover.
    # (meta.job_id needs no such check: it is pinned by the sentinel passed IN, so dropping
    # the stamping leaves the key absent and moves the fixture.)
    if envelope["meta"].get("fingerprint") != FINGERPRINT:
        raise AssertionError(f"{name}: the chokepoint did not stamp meta.fingerprint")
    envelope["meta"]["fingerprint"] = _FINGERPRINT_SENTINEL
    return envelope


def build_snapshot() -> dict:
    """The deterministic snapshot dict the guard test compares to the fixture."""
    stored = _stored_envelopes()
    return {
        "delivered": {
            detail: {name: _deliver(env, name, detail) for name, env in stored.items()}
            for detail in ("summary", "full")
        },
        # The key names delivery removed, per envelope — the reviewable "what changed".
        # Computed against the delivered envelope, so a key the chokepoint STAMPS (job_id)
        # correctly never appears here.
        "omitted_meta_keys": {
            name: sorted(set(env["meta"]) - set(_deliver(env, name, "summary")["meta"]))
            for name, env in stored.items()
        },
    }


def render() -> str:
    """Canonical JSON for the committed fixture (sorted keys, trailing newline)."""
    return json.dumps(build_snapshot(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    import sys

    sys.stdout.write(render())
