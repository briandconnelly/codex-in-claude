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
"""

from __future__ import annotations

import json

from codex_in_claude.schemas import (
    RESULT_FORMAT,
    ConsultResult,
    Coverage,
    DelegateResult,
    Meta,
    RawResponse,
    ReviewResult,
    dump_success,
)
from codex_in_claude.server import _finished_job_envelope

_FINGERPRINT_SENTINEL = "<fingerprint>"
_VERSION_SENTINEL = "0.0.0"
_REQUEST_ID_SENTINEL = "0" * 32


def _representative_meta() -> Meta:
    meta = Meta(
        cwd="/repo",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=1,
        elapsed_ms=1,
    )
    meta.fingerprint = _FINGERPRINT_SENTINEL
    meta.server_version = _VERSION_SENTINEL
    meta.request_id = _REQUEST_ID_SENTINEL
    return meta


def _raw() -> RawResponse:
    return RawResponse(text="RAW MODEL TEXT", session_id="sess-1", model="a-model")


def _stored_envelopes() -> dict[str, dict]:
    """The persisted envelopes, exactly as a worker writes them (nulls retained)."""
    return {
        "consult": dump_success(
            ConsultResult(summary="s", raw_response=_raw(), meta=_representative_meta())
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
                meta=_representative_meta(),
            )
        ),
        # diff=None on purpose: a no-changes delegate. Its `diff` key must survive
        # delivery — it is the field the result's own next_steps tells callers to read.
        "delegate_no_changes": dump_success(
            DelegateResult(summary="s", diff=None, raw_response=_raw(), meta=_representative_meta())
        ),
    }


_JOB_ID_SENTINEL = "0" * 32
_KIND_BY_NAME = {
    "consult": "codex_consult",
    "review": "codex_review_changes",
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
        _representative_meta(),
        detail,
        None,
    )
    if not delivered:
        raise AssertionError(f"{name}: the chokepoint refused to deliver the payload")
    # job_id/fingerprint are stamped by the chokepoint from live values; pin them so the
    # fixture does not churn on every release or FINGERPRINT bump.
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
