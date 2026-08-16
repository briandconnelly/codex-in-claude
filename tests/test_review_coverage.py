"""Coverage computation for codex_review_changes / codex_dry_run (#319).

`build_coverage` turns a gathered DiffResult + scope into the agent-visible Coverage
object: what was reviewable, what was omitted, and why. `complete` must mean "nothing
was left unreviewed", so redaction and truncation — not just omitted untracked files —
make coverage `partial`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pontifex.core.gitdiff import DiffResult, DiffSummary
from pydantic import ValidationError

from codex_in_claude import orchestration as o
from codex_in_claude.schemas import Coverage, RedactionSummary, ReviewResult

_FIXTURES = Path(__file__).parent / "fixtures"


def _diff(**kw) -> DiffResult:
    base: dict = {"text": "x", "summary": DiffSummary(files_changed=1)}
    base.update(kw)
    return DiffResult(**base)


def test_coverage_complete_when_nothing_omitted():
    cov = o.build_coverage(
        scope="working_tree", diff=_diff(untracked_detected=0, untracked_included=0)
    )
    assert cov.status == "complete"
    assert cov.omission_reasons == []
    assert cov.untracked_files_detected == 0
    assert cov.untracked_files_omitted == 0


def test_coverage_partial_when_untracked_omitted():
    cov = o.build_coverage(
        scope="working_tree", diff=_diff(untracked_detected=3, untracked_included=0)
    )
    assert cov.status == "partial"
    assert cov.untracked_files_omitted == 3
    assert cov.omission_reasons == ["untracked_omitted"]


def test_coverage_complete_when_all_untracked_included():
    cov = o.build_coverage(
        scope="working_tree", diff=_diff(untracked_detected=2, untracked_included=2)
    )
    assert cov.status == "complete"
    assert cov.untracked_files_omitted == 0
    assert cov.omission_reasons == []


def test_coverage_partial_when_tree_changed_during_gather():
    # #336: a concurrent edit was detected across the working_tree gather, so the
    # summary/diff/untracked may be internally inconsistent — coverage is not `complete`.
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(untracked_detected=0, untracked_included=0, tree_changed_during_gather=True),
    )
    assert cov.status == "partial"
    assert "tree_changed_during_gather" in cov.omission_reasons


def test_coverage_tree_changed_ignored_for_commit_scope():
    # branch/commit read immutable objects; the flag is never set there, and even if a
    # DiffResult carried it, build_coverage only consults it under working_tree.
    cov = o.build_coverage(
        scope="commit",
        diff=_diff(untracked_detected=None, tree_changed_during_gather=True),
    )
    assert cov.status == "complete"
    assert cov.omission_reasons == []


def test_coverage_partial_on_truncation():
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(untracked_detected=0, untracked_included=0, truncated=True),
    )
    assert cov.status == "partial"
    assert "truncated" in cov.omission_reasons


def test_coverage_partial_on_redaction():
    # A redacted secret-looking file's hunk is dropped from the diff, so the model never
    # saw its content — coverage is partial even though the diff was non-empty. The
    # `redacted` reason is driven by the split withheld_paths/masked_paths fields (#433
    # review F1), same as `redacted_paths` (the union) would be in a real gather_diff.
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(
            untracked_detected=0,
            untracked_included=0,
            redacted_paths=[".env"],
            withheld_paths=[".env"],
        ),
    )
    assert cov.status == "partial"
    assert "redacted" in cov.omission_reasons


def test_coverage_untracked_na_for_commit_scope():
    cov = o.build_coverage(
        scope="commit", diff=_diff(untracked_detected=None, untracked_included=0)
    )
    assert cov.untracked_files_detected is None
    assert cov.untracked_files_included is None
    assert cov.untracked_files_omitted is None
    assert cov.status == "complete"


def test_coverage_commit_scope_still_partial_on_truncation():
    cov = o.build_coverage(scope="commit", diff=_diff(untracked_detected=None, truncated=True))
    assert cov.untracked_files_detected is None
    assert cov.status == "partial"
    assert cov.omission_reasons == ["truncated"]


def test_coverage_reasons_are_deterministically_ordered():
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(
            untracked_detected=1,
            untracked_included=0,
            tree_changed_during_gather=True,
            truncated=True,
            redacted_paths=[".env"],
            withheld_paths=[".env"],
        ),
    )
    assert cov.omission_reasons == [
        "untracked_omitted",
        "tree_changed_during_gather",
        "truncated",
        "redacted",
    ]


# --- F5: Coverage enforces its own advertised invariants (#322) --------------
def test_coverage_rejects_complete_status_with_omission_reasons():
    with pytest.raises(ValidationError):
        Coverage(status="complete", omission_reasons=["truncated"])


def test_coverage_rejects_partial_status_without_reasons():
    with pytest.raises(ValidationError):
        Coverage(status="partial", omission_reasons=[])


def test_coverage_rejects_broken_count_equation():
    # detected must equal included + omitted when the counts are present.
    with pytest.raises(ValidationError):
        Coverage(
            status="partial",
            untracked_files_detected=3,
            untracked_files_included=1,
            untracked_files_omitted=0,
            omission_reasons=["untracked_omitted"],
        )


def test_coverage_rejects_populated_redaction_without_redacted_reason():
    # #433 review F1: `redaction` and `omission_reasons` used to be two independent
    # facts a caller could set inconsistently — a populated RedactionSummary with NO
    # "redacted" reason describes a disclosure that never fired, exactly the class of
    # false/inconsistent Coverage `_check_invariants`'s own comment promises no
    # construction path can emit. `status="partial"`/`omission_reasons=["truncated"]`
    # alone already satisfies the OLDER status/omission_reasons invariant, isolating
    # this one.
    with pytest.raises(ValidationError):
        Coverage(
            status="partial",
            omission_reasons=["truncated"],
            redaction=RedactionSummary(withheld_paths=[".env"], masked_paths=[], inline_masks=0),
        )


# --- #433: RedactionSummary disclosure on coverage.redaction -----------------


def test_coverage_redaction_none_when_nothing_redacted():
    cov = o.build_coverage(
        scope="working_tree", diff=_diff(untracked_detected=0, untracked_included=0)
    )
    assert cov.redaction is None


def test_coverage_redacted_reason_and_redaction_field_derive_from_one_predicate():
    # #433 review F1: build_coverage used to read the `redacted` REASON from
    # diff.redacted_paths but the `redaction` FIELD from withheld_paths/masked_paths —
    # two independent sources for one fact. A DiffResult with the split fields set but
    # the flat union empty (never produced by real gather_diff, which always sets both
    # together, but not impossible for a future/synthetic caller — #322's own "no
    # construction path" promise is about exactly this class of caller) must not
    # silently disagree with itself: withheld_paths alone must be enough to trigger
    # BOTH the reason and the populated disclosure.
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(
            untracked_detected=0,
            untracked_included=0,
            redacted_paths=[],  # deliberately NOT set — the split fields are authoritative
            withheld_paths=[".env"],
        ),
    )
    assert cov.status == "partial"
    assert "redacted" in cov.omission_reasons
    assert cov.redaction == RedactionSummary(
        withheld_paths=[".env"], masked_paths=[], inline_masks=0
    )


def test_coverage_legacy_union_only_still_trips_the_redacted_reason():
    # #433 review C1: the FIX above over-corrected — it made the `redacted` reason
    # read SOLELY from the split fields, so a "legacy-style" DiffResult that only
    # populates `redacted_paths` (constructible with defaults — see
    # test_server.py's `test_dry_run_preview` fixture, which does exactly this; also
    # now REACHABLE from a real gather_diff after #433 review C2, whose byte-cap
    # gating can leave `redacted_paths` non-empty while withheld_paths/masked_paths
    # stay empty for content past the cap) silently reported `status="complete"` —
    # false-complete coverage. The REASON must fire from `redacted_paths OR
    # withheld_paths OR masked_paths`; only the FIELD stays scoped to the split
    # fields (reason-without-field is fine — the model invariant is field⇒reason,
    # never the converse).
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(
            untracked_detected=0,
            untracked_included=0,
            redacted_paths=[".env"],
            # withheld_paths/masked_paths deliberately left at their empty defaults.
        ),
    )
    assert cov.status == "partial"
    assert "redacted" in cov.omission_reasons
    assert cov.redaction is None


def test_coverage_synthetic_inline_masks_without_masked_paths_fails_loudly():
    # #433 Copilot review of #470 (comment 4): `redacted_via_split` ignored
    # `diff.inline_masks` entirely — a synthetic DiffResult with `inline_masks > 0`
    # but empty `masked_paths`/`withheld_paths`/`redacted_paths` was silently treated
    # as NOT redacted at all (status="complete", a false-complete), since neither
    # predicate noticed the nonzero count. The predicate now includes `inline_masks`,
    # so this inconsistent shape flows into `RedactionSummary` construction and fails
    # LOUDLY via its own `iff` invariant (#433 review C4) instead of being silently
    # dropped — never producible by a real `gather_diff`, but not something
    # `build_coverage` may quietly paper over either.
    with pytest.raises(ValidationError):
        o.build_coverage(
            scope="working_tree",
            diff=_diff(untracked_detected=0, untracked_included=0, inline_masks=1),
        )


def test_coverage_redaction_reports_one_withheld_file():
    # (a) a diff with one withheld file — no inline masks at all.
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(
            untracked_detected=0,
            untracked_included=0,
            redacted_paths=[".env"],
            withheld_paths=[".env"],
        ),
    )
    assert cov.status == "partial"
    assert cov.redaction == RedactionSummary(
        withheld_paths=[".env"], masked_paths=[], inline_masks=0
    )


def test_coverage_redaction_reports_two_inline_masks():
    # (b) one file with two inline masks — no withheld files.
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(
            untracked_detected=0,
            untracked_included=0,
            redacted_paths=["src/app.py"],
            masked_paths=["src/app.py"],
            inline_masks=2,
        ),
    )
    assert cov.status == "partial"
    assert cov.redaction == RedactionSummary(
        withheld_paths=[], masked_paths=["src/app.py"], inline_masks=2
    )


def test_coverage_redaction_reports_both_withheld_and_masked_in_encounter_order():
    # (c) both together, masked file encountered BEFORE the withheld one.
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(
            untracked_detected=0,
            untracked_included=0,
            redacted_paths=["app.py", ".env"],
            withheld_paths=[".env"],
            masked_paths=["app.py"],
            inline_masks=1,
        ),
    )
    assert cov.status == "partial"
    assert cov.redaction is not None
    assert cov.redaction.withheld_paths == [".env"]
    assert cov.redaction.masked_paths == ["app.py"]
    assert cov.redaction.inline_masks == 1


def test_coverage_redaction_model_validate_without_key_defaults_to_none():
    # The `= None` default is load-bearing (#433): a pre-B1 Coverage dict with no
    # `redaction` key must still validate, not be rejected as missing a required field.
    payload = {
        "status": "complete",
        "untracked_files_detected": 0,
        "untracked_files_included": 0,
        "untracked_files_omitted": 0,
        "omission_reasons": [],
    }
    assert "redaction" not in payload
    cov = Coverage.model_validate(payload)
    assert cov.redaction is None


def test_coverage_accepts_consistent_complete():
    cov = Coverage(status="complete")  # all-None counts, no reasons — valid
    assert cov.status == "complete"


# --- #433 review C4: RedactionSummary's own field/cross-field invariants -----


def test_redaction_summary_rejects_negative_inline_masks():
    with pytest.raises(ValidationError):
        RedactionSummary(inline_masks=-1)


def test_redaction_summary_rejects_masked_paths_without_enough_inline_masks():
    # Each masked path was added by a commit that incremented inline_masks by >=1
    # (DiffRedactor.commit_pending), so inline_masks can never be LESS than the
    # number of distinct masked paths.
    with pytest.raises(ValidationError):
        RedactionSummary(masked_paths=["a.py"], inline_masks=0)
    with pytest.raises(ValidationError):
        RedactionSummary(masked_paths=["a.py", "b.py"], inline_masks=1)


def test_redaction_summary_rejects_inline_masks_with_no_masked_paths():
    # #433 review C3's dominant-withholding semantic makes this an "iff", not just a
    # one-directional floor: every real inline_masks increment (DiffRedactor.
    # commit_pending's "mask" branch) also adds its path to masked_paths (deduped),
    # so a masked_paths==[] RedactionSummary can never carry a nonzero count.
    with pytest.raises(ValidationError):
        RedactionSummary(masked_paths=[], inline_masks=1)


def test_redaction_summary_rejects_a_path_in_both_withheld_and_masked():
    # #433 Copilot review of #470 (comment 2): the docstring promises withheld_paths/
    # masked_paths are mutually exclusive, but nothing validated it — RedactionSummary
    # is wire-contract constructible from arbitrary external JSON (e.g. a replayed
    # stored result), not only from DiffRedactor's own construction path.
    with pytest.raises(ValidationError):
        RedactionSummary(withheld_paths=["a.py"], masked_paths=["a.py"], inline_masks=1)


def test_redaction_summary_accepts_consistent_shapes():
    RedactionSummary()  # all-empty/zero — valid
    RedactionSummary(withheld_paths=[".env"])  # withheld-only — valid
    RedactionSummary(masked_paths=["a.py"], inline_masks=1)  # exact boundary — valid
    RedactionSummary(masked_paths=["a.py", "b.py"], inline_masks=5)  # >= floor — valid
    # Disjoint withheld + masked, distinct paths — valid.
    RedactionSummary(withheld_paths=["a.py"], masked_paths=["b.py"], inline_masks=1)


def test_replay_of_a_pre_b1_stored_review_result_still_validates():
    # tests/fixtures/pre_b1_review_result.json is the `review_success` entry of
    # tests/fixtures/result_format_snapshot.json AS COMMITTED at 609e9dd (schema-74,
    # RESULT_FORMAT 7 — the merged pre-B1 main this branch forked from), extracted
    # byte-for-byte from that commit's own fixture (not hand-typed). Its `coverage`
    # object carries no `redaction` key at all — exactly what a worker persisted
    # before this field existed. `ReviewResult.model_validate` (the same call
    # server.py's job-replay path makes on a stored result.json — see
    # `_validate_job_success`) must still accept it: this is the whole reason
    # `Coverage.redaction`'s `= None` default is load-bearing rather than merely
    # convenient (a required field would reject this exact payload).
    payload = json.loads((_FIXTURES / "pre_b1_review_result.json").read_text(encoding="utf-8"))
    assert "redaction" not in payload["coverage"]
    replayed = ReviewResult.model_validate(payload)
    assert replayed.coverage.redaction is None
    assert replayed.coverage.status == "complete"
    assert replayed.summary == "s"
