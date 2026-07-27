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
"""

from __future__ import annotations

import copy
import json

from codex_in_claude.schemas import (
    ConsultResult,
    Coverage,
    DelegateResult,
    Meta,
    RawResponse,
    ReviewResult,
    apply_detail,
    dump_success,
    slim_meta,
)

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


def _deliver(stored: dict, detail: str) -> dict:
    """Reproduce the delivery chokepoint: detail trimming, then meta slimming."""
    return slim_meta(apply_detail(copy.deepcopy(stored), detail))


def build_snapshot() -> dict:
    """The deterministic snapshot dict the guard test compares to the fixture."""
    stored = _stored_envelopes()
    return {
        "delivered": {
            detail: {name: _deliver(env, detail) for name, env in stored.items()}
            for detail in ("summary", "full")
        },
        # The key names slimming removed, per envelope — the reviewable "what changed".
        "omitted_meta_keys": {
            name: sorted(set(env["meta"]) - set(_deliver(env, "summary")["meta"]))
            for name, env in stored.items()
        },
    }


def render() -> str:
    """Canonical JSON for the committed fixture (sorted keys, trailing newline)."""
    return json.dumps(build_snapshot(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    import sys

    sys.stdout.write(render())
