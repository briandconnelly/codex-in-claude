"""Guard: the persisted result-format snapshot moves with RESULT_FORMAT (issue #305).

An acknowledgment guard in the manifest-snapshot mould: a change to the persisted
result.json surface (envelope model shapes, their Literal/enum values, or the writers'
serialization) moves the snapshot and fails CI until the author regenerates the fixture
— and decides whether RESULT_FORMAT must bump (it must whenever an older reader's
closed schema could reject the new shape). Wording-only edits (field descriptions) and
ordinary FINGERPRINT bumps are normalized out and do not move it.
"""

import json
from pathlib import Path

from codex_in_claude import result_format_snapshot
from codex_in_claude.schemas import FINGERPRINT, RESULT_FORMAT

_FIXTURE = Path(__file__).parent / "fixtures" / "result_format_snapshot.json"

_REGEN = (
    "persisted result-format surface changed — review the snapshot diff, then in the "
    "SAME commit: bump RESULT_FORMAT in schemas.py if an older reader could reject the "
    "new shape (see its comment for the rule), and regenerate the fixture (`uv run "
    "python -m codex_in_claude.result_format_snapshot > "
    "tests/fixtures/result_format_snapshot.json`)."
)


def test_result_format_snapshot_matches_golden():
    current = result_format_snapshot.render()
    assert current == _FIXTURE.read_text(encoding="utf-8"), _REGEN


def test_snapshot_embeds_result_format():
    # Bumping RESULT_FORMAT alone also moves the snapshot, so constant and fixture
    # stay acknowledged in lockstep.
    snap = result_format_snapshot.build_snapshot()
    assert snap["result_format"] == RESULT_FORMAT


def test_snapshot_normalizes_wording_and_release_variables():
    # Descriptions (wording) and the release-variable fingerprint/server_version
    # defaults must not appear: an ordinary FINGERPRINT bump or a reworded field
    # description is not a persisted-format change and must not churn the fixture.
    text = result_format_snapshot.render()
    assert FINGERPRINT not in text
    assert '"description"' not in text
    from codex_in_claude import __version__

    assert __version__ not in text


def test_snapshot_pins_null_retention_asymmetry():
    # The writers' serialization modes are part of the persisted format: success
    # envelopes RETAIN null optionals (model_dump), error envelopes DROP them
    # (serialize_error exclude_none) — the exact drift class behind #190/#304.
    snap = result_format_snapshot.build_snapshot()
    assert "verdict" not in snap["serialized"]["consult_success"]  # consult never had one
    delegate = snap["serialized"]["delegate_success"]
    assert delegate["diff"] is None  # null optional retained as a key
    error = snap["serialized"]["error"]
    assert "session_id" not in error["meta"]  # null optional dropped


def test_snapshot_covers_all_four_envelope_types():
    snap = result_format_snapshot.build_snapshot()
    assert set(snap["schemas"]) == {
        "ConsultResult",
        "ReviewResult",
        "DelegateResult",
        "ErrorResult",
    }
    assert set(snap["serialized"]) == {
        "consult_success",
        "review_success",
        "delegate_success",
        "error",
    }


def test_render_is_deterministic():
    assert result_format_snapshot.render() == result_format_snapshot.render()
    assert result_format_snapshot.render().endswith("\n")


def test_snapshot_is_sensitive_to_shape_change():
    # The guard must be able to fail: a schema with one extra property must produce a
    # different snapshot (confirm the probe applied before trusting the difference).
    snap = result_format_snapshot.build_snapshot()
    mutated = json.loads(json.dumps(snap))
    props = mutated["schemas"]["ConsultResult"]["properties"]
    assert "field_from_the_future" not in props
    props["field_from_the_future"] = {"type": "string"}
    assert (
        mutated["schemas"]["ConsultResult"]["properties"]
        != snap["schemas"]["ConsultResult"]["properties"]
    )
