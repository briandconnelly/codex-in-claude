"""Guard: the delivered success-envelope shape is pinned (issue #334).

An acknowledgment guard in the manifest/result-format mould, covering the gap both of
those leave: a wire-only serializer change moves neither of them, so without this fixture
the set of keys a caller receives could drift with no reviewable evidence. A change here
is a change to what every client sees on every call — review the diff, then decide the
FINGERPRINT bump.
"""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from codex_in_claude import wire_shape_snapshot
from codex_in_claude.schemas import RESULT_META_SCHEMA

_FIXTURE = Path(__file__).parent / "fixtures" / "wire_shape_snapshot.json"

_REGEN = (
    "the DELIVERED envelope shape changed — review the snapshot diff to see exactly "
    "which keys a client gains or loses, then in the SAME commit bump FINGERPRINT "
    "(schemas.py) if the agent-visible surface moved, and regenerate the fixture "
    "(`uv run python -m codex_in_claude.wire_shape_snapshot > "
    "tests/fixtures/wire_shape_snapshot.json`). RESULT_FORMAT is a SEPARATE decision: "
    "bump it only if the PERSISTED result.json shape moved (see result_format_snapshot)."
)


def test_wire_shape_snapshot_matches_golden():
    assert wire_shape_snapshot.render() == _FIXTURE.read_text(encoding="utf-8"), _REGEN


def test_render_is_deterministic():
    assert wire_shape_snapshot.render() == wire_shape_snapshot.render()
    assert wire_shape_snapshot.render().endswith("\n")


def test_delivered_meta_carries_no_nulls():
    delivered = wire_shape_snapshot.build_snapshot()["delivered"]
    for detail, envelopes in delivered.items():
        for name, env in envelopes.items():
            nulls = [k for k, v in env["meta"].items() if v is None]
            assert nulls == [], f"{detail}/{name} still delivers null meta keys: {nulls}"


def test_something_was_actually_omitted():
    # Guards the guard: if slimming silently stopped running, every other assertion here
    # would still pass while the fixture quietly recorded the unslimmed shape.
    omitted = wire_shape_snapshot.build_snapshot()["omitted_meta_keys"]
    assert omitted and all(keys for keys in omitted.values())
    assert "session_id" in omitted["consult"]


def test_payload_keys_survive_delivery():
    delivered = wire_shape_snapshot.build_snapshot()["delivered"]
    for detail in ("summary", "full"):
        # A no-changes delegate's `diff` is null, and its key must still reach the caller.
        assert "diff" in delivered[detail]["delegate_no_changes"]
        for env in delivered[detail].values():
            assert set(env["raw_response"]) == {"text", "session_id", "model"}


def test_summary_still_nulls_raw_text_and_full_keeps_it():
    # apply_detail's guarantee, pinned against a future broadening of the omission rule:
    # raw_response.text is nulled by detail, NOT dropped by slimming.
    delivered = wire_shape_snapshot.build_snapshot()["delivered"]
    for name in delivered["summary"]:
        assert delivered["summary"][name]["raw_response"]["text"] is None
        assert delivered["full"][name]["raw_response"]["text"] == "RAW MODEL TEXT"


def test_delivered_meta_still_validates_against_published_contract():
    # Absence must be legal under the schema clients actually fetch (codex://result-meta),
    # not merely under the Pydantic model.
    validator = Draft202012Validator(RESULT_META_SCHEMA)
    delivered = wire_shape_snapshot.build_snapshot()["delivered"]
    for envelopes in delivered.values():
        for name, env in envelopes.items():
            errors = [e.message for e in validator.iter_errors(env["meta"])]
            assert errors == [], f"{name}: {errors}"


def test_published_contract_validator_is_not_blind():
    # The negative above is only evidence if the same validator rejects a known-bad meta.
    validator = Draft202012Validator(RESULT_META_SCHEMA)
    meta = dict(wire_shape_snapshot.build_snapshot()["delivered"]["summary"]["consult"]["meta"])
    assert validator.is_valid(meta)
    meta.pop("cwd")
    assert not validator.is_valid(meta), "required member removal must fail validation"


def test_snapshot_is_sensitive_to_key_loss():
    # The fixture comparison must be able to fail: dropping a delivered key must change it.
    snap = wire_shape_snapshot.build_snapshot()
    mutated = json.loads(json.dumps(snap))
    assert "summary" in mutated["delivered"]["summary"]["consult"]
    del mutated["delivered"]["summary"]["consult"]["summary"]
    assert mutated != snap
