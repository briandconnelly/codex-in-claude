"""Canonical snapshot of the persisted result-format surface (issue #305).

Snapshotting this guards ``RESULT_FORMAT`` (``schemas.py``) the way the manifest
snapshot guards ``FINGERPRINT``: a change to what a replaying reader must be able to
parse from ``result.json`` — the envelope model shapes, their Literal/enum values, or
the writers' serialization modes — moves the snapshot, so it cannot ship unreviewed.
The guard test's failure message directs the author to regenerate the fixture and
decide the ``RESULT_FORMAT`` bump; like the manifest guard, it is an acknowledgment
guard, not a mechanical bump.

Two views are captured:

- ``schemas``: each envelope model's JSON schema, normalized — ``description`` keys
  (wording, owned by ``FINGERPRINT``) stripped, and the release-variable
  ``Meta.fingerprint`` default pinned to a sentinel — so ordinary FINGERPRINT bumps
  and rewords do not churn the fixture.
- ``serialized``: representative envelopes rendered through the REAL writer calls
  (``dump_success`` for success results, ``serialize_error`` for errors), pinning the
  null-retention asymmetry between them — the serializer-mode drift class a JSON
  schema cannot see (#190/#304).
"""

from __future__ import annotations

import json
from typing import Any

from codex_in_claude.errors import make_error, serialize_error
from codex_in_claude.schemas import (
    FINGERPRINT,
    RESULT_FORMAT,
    ConsultResult,
    Coverage,
    DelegateResult,
    ErrorResult,
    Meta,
    ReviewResult,
    dump_success,
)

# Every envelope type a worker persists to result.json (and replay must re-read).
_ENVELOPE_MODELS = (ConsultResult, ReviewResult, DelegateResult, ErrorResult)

# Stand-ins for release/run-variable Meta defaults, so the fixture is deterministic
# and does not move on releases or FINGERPRINT bumps.
_FINGERPRINT_SENTINEL = "<fingerprint>"
_VERSION_SENTINEL = "0.0.0"
_REQUEST_ID_SENTINEL = "0" * 32


def _normalize_schema(node: Any) -> Any:
    """Strip wording and pin release-variable defaults; keep shape and values."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "description":
                continue
            if key == "default" and value == FINGERPRINT:
                out[key] = _FINGERPRINT_SENTINEL
                continue
            out[key] = _normalize_schema(value)
        return out
    if isinstance(node, list):
        return [_normalize_schema(v) for v in node]
    return node


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


def build_snapshot() -> dict:
    """The deterministic snapshot dict the guard test compares to the fixture."""
    serialized = {
        "consult_success": dump_success(ConsultResult(summary="s", meta=_representative_meta())),
        "review_success": dump_success(
            ReviewResult(
                summary="s",
                coverage=Coverage(
                    status="complete",
                    untracked_files_detected=0,
                    untracked_files_included=0,
                    untracked_files_omitted=0,
                ),
                meta=_representative_meta(),
            )
        ),
        "delegate_success": dump_success(DelegateResult(summary="s", meta=_representative_meta())),
        "error": serialize_error(
            ErrorResult(error=make_error("internal_error", "m"), meta=_representative_meta())
        ),
    }
    return {
        "result_format": RESULT_FORMAT,
        "schemas": {
            model.__name__: _normalize_schema(model.model_json_schema())
            for model in _ENVELOPE_MODELS
        },
        "serialized": serialized,
    }


def render() -> str:
    """Canonical JSON for the committed fixture (sorted keys, trailing newline)."""
    return json.dumps(build_snapshot(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    import sys

    sys.stdout.write(render())
