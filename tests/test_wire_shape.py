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
    # `scope` is review-only, so it is null on a consult run no matter which optionals
    # that envelope populates — unlike `session_id`, which this anchor used to name and
    # which a consult now reports (#400).
    assert "scope" in omitted["consult"]


# --- populated optionals must survive delivery (#400) --------------------------------


def test_populated_optional_meta_keys_survive_delivery():
    # The gap this fixture had: every optional was null, so every optional was already
    # absent, so deleting a populated one changed nothing. These keys are populated
    # precisely so their deletion has somewhere to show up.
    delivered = wire_shape_snapshot.build_snapshot()["delivered"]
    for detail in ("summary", "full"):
        consult = delivered[detail]["consult"]["meta"]
        assert consult["model"] == "a-model"
        assert consult["session_id"] == "sess-1"
        assert consult["reasoning_effort"] == "high"
        assert consult["roots_source"] == "client"
        assert consult["workspace_source"] == "param"
        assert consult["usage"]["total_tokens"] == 6
        # A POPULATED FALSY optional: `slim_meta` keys on `is None`, so 0 must arrive.
        assert consult["command_exit_code"] == 0
        review = delivered[detail]["review"]["meta"]
        assert review["scope"] == "branch"
        assert review["base"] == "main"
        assert review["paths"] == ["src/a.py"]
        assert review["context_summary"]["files_changed"] == 1


def test_one_envelope_stays_sparse_so_omission_remains_visible():
    # The counterweight to the test above. If someone populates the delegate's meta too,
    # every `omitted_meta_keys` list shrinks toward empty and the fixture stops being
    # evidence that delivery omits anything — the failure this file exists to catch.
    snap = wire_shape_snapshot.build_snapshot()
    sparse = snap["delivered"]["summary"]["delegate_no_changes"]["meta"]
    assert not {"model", "session_id", "usage", "roots_source"} & set(sparse)
    assert len(snap["omitted_meta_keys"]["delegate_no_changes"]) > len(
        snap["omitted_meta_keys"]["review"]
    )


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
