"""Coverage computation for codex_review_changes / codex_dry_run (#319).

`build_coverage` turns a gathered DiffResult + scope into the agent-visible Coverage
object: what was reviewable, what was omitted, and why. `complete` must mean "nothing
was left unreviewed", so redaction and truncation — not just omitted untracked files —
make coverage `partial`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codex_in_claude import orchestration as o
from codex_in_claude._core.gitdiff import DiffResult, DiffSummary
from codex_in_claude.schemas import Coverage


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
    # saw its content — coverage is partial even though the diff was non-empty.
    cov = o.build_coverage(
        scope="working_tree",
        diff=_diff(untracked_detected=0, untracked_included=0, redacted_paths=[".env"]),
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


def test_coverage_accepts_consistent_complete():
    cov = Coverage(status="complete")  # all-None counts, no reasons — valid
    assert cov.status == "complete"
