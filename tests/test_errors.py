import typing

import pytest

from codex_in_claude.errors import (
    _REPAIR_BY_CODE,
    make_error,
    serialize_error,
    serialize_error_info,
)
from codex_in_claude.schemas import ErrorCode, ErrorDetail, ErrorResult, InvalidArgument, Meta


def _meta():
    return Meta(
        cwd="/x",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=180,
        elapsed_ms=1,
    )


# Every invalid_arguments envelope must carry the per-argument list (#416), so the
# tests below that only care about some OTHER facet of the envelope pass this stand-in.
_ARG = InvalidArgument(field="question", reason="bad arg")


def test_repair_map_covers_every_error_code():
    codes = set(typing.get_args(ErrorCode))
    assert codes <= set(_REPAIR_BY_CODE), codes - set(_REPAIR_BY_CODE)


def test_make_error_derives_symbolic_repair():
    e = make_error(
        "job_running", "still running", retry_after_ms=2000, repair_arguments={"job_id": "j1"}
    )
    assert e.temporary is True
    assert e.retry_after_ms == 2000
    assert e.repair.next_step == "poll_job_status"
    assert e.repair.tool == "codex_job_status"
    assert e.repair.arguments == {"job_id": "j1"}


def test_make_error_non_temporary_has_no_backoff():
    e = make_error("invalid_arguments", "bad arg", invalid_arguments=[_ARG])
    assert e.temporary is False and e.retry_after_ms is None
    assert e.repair.next_step == "correct_arguments"


# --- the invalid_arguments list invariant (#416) ------------------------------
# docs/REFERENCE.md:34-35 promises the per-argument list on EVERY envelope carrying
# this code, with `details` mirroring the first entry. Two producers had drifted from
# that promise independently, so the guarantee moves into the one constructor every
# producer goes through instead of being re-implemented per call site.


@pytest.mark.parametrize("omitted", [None, []])
def test_make_error_rejects_invalid_arguments_without_the_list(omitted):
    """The promise is unconditional, so a listless envelope is a programming error —
    loud here, where tests catch it, rather than a silently non-conformant envelope.

    Deliberately NOT an ErrorInfo model_validator: replay reconstructs stored records
    with `ErrorResult.model_validate` in the job-result reader, and a record written before
    this rule legitimately has no list. Validating there would make old jobs unreadable."""
    with pytest.raises(ValueError, match="invalid_arguments"):
        make_error("invalid_arguments", "bad arg", invalid_arguments=omitted)


def test_make_error_derives_details_from_the_first_invalid_argument():
    """`details` mirrors the first entry (docs/REFERENCE.md:34-35). Deriving it here
    means a producer cannot supply a list whose first entry disagrees with `details`."""
    e = make_error(
        "invalid_arguments",
        "bad arg",
        invalid_arguments=[
            InvalidArgument(field="scope", reason="not a valid scope", allowed_values=["branch"]),
            InvalidArgument(field="base", reason="unresolvable ref"),
        ],
    )
    assert e.details is not None
    assert e.details.field == "scope"
    assert e.details.reason == "not a valid scope"
    assert e.details.allowed_values == ["branch"]


def test_make_error_rejects_an_explicit_details_for_invalid_arguments():
    """Derivation is UNCONDITIONAL, which is what makes the mirror structural: a call site
    cannot pass a `details` that disagrees with the first entry because it cannot pass one
    at all. Rejected loudly rather than silently ignored — a silent no-op would let a call
    site believe it had set something it had not.

    Nothing legitimately needs the exception: the one shape no single entry can mirror,
    `fields=[...]` for a combined-size failure, belongs to `input_too_large`
    (`_combined_input_detail`'s only caller, the consult combined-size guard)."""
    with pytest.raises(ValueError, match="derived from invalid_arguments"):
        make_error(
            "invalid_arguments",
            "too big together",
            details=ErrorDetail(fields=["question", "extra_context"]),
            invalid_arguments=[InvalidArgument(field="question", reason="combined size")],
        )


def test_make_error_mirrors_details_for_every_invalid_arguments_producer():
    """The mirror holds for a multi-entry list too: `details` tracks the FIRST entry,
    including its allowed_values, and never a later one."""
    e = make_error(
        "invalid_arguments",
        "two bad args",
        invalid_arguments=[
            InvalidArgument(field="scope", reason="bad scope", allowed_values=["branch"]),
            InvalidArgument(field="base", reason="bad base", allowed_values=["main"]),
        ],
    )
    assert e.details == ErrorDetail(field="scope", reason="bad scope", allowed_values=["branch"])


def test_resource_not_found_repair_lists_resources():
    """The resource-read carrier (F9, #181): resource_not_found is permanent, has no
    backoff, and steers the agent to list resources. repair.tool stays None because the
    authoritative discovery path (resources/list) is an MCP method, not a server tool."""
    e = make_error("resource_not_found", "Resource not found.")
    assert e.temporary is False and e.retry_after_ms is None
    assert e.repair.next_step == "list_resources"
    assert e.repair.tool is None
    assert "resources/list" in e.repair.alternative


def test_timeout_repair_points_at_async_escape_hatch():
    """#195: a timed-out sync call retried as-is will just time out again. The repair
    prose leads with the real escape hatch — the matching *_async tool, which runs to the
    background-job deadline rather than the 10-600s sync clamp — while keeping the sync
    fallbacks. repair.tool stays None: `timeout` is emitted from a shared classifier
    serving consult/review/delegate, so naming one async tool would misinform the others."""
    e = make_error("timeout", "codex exceeded the timeout.")
    assert e.temporary is True
    assert e.repair.next_step == "inspect_and_retry"
    assert e.repair.tool is None
    alt = e.repair.alternative
    # Names all three async tools, the poll/fetch lifecycle, and keeps the sync fallbacks.
    assert "codex_consult_async" in alt
    assert "codex_review_changes_async" in alt
    assert "codex_delegate_async" in alt
    assert "codex_job_status" in alt and "codex_job_result" in alt
    assert "timeout_seconds" in alt
    # Does not overstate the deadline as unconditionally "longer" (it is configurable).
    assert "longer deadline" not in alt


def test_serialize_error_info_retains_null_retry_after_ms():
    """The bare-ErrorInfo serializer used for resource errors keeps retry_after_ms present
    even when null (§6), and carries no ok/meta wrapper."""
    d = serialize_error_info(make_error("resource_not_found", "Resource not found."))
    assert d["code"] == "resource_not_found"
    assert d["retry_after_ms"] is None  # present though null
    assert "ok" not in d and "meta" not in d
    assert d["repair"]["next_step"] == "list_resources"


def test_make_error_repair_tool_override_names_the_failing_tool():
    # N3: invalid_arguments has no table-derived repair tool, but the failing
    # tool name is known and non-sensitive — the override surfaces it.
    e = make_error(
        "invalid_arguments", "bad arg", repair_tool="codex_consult", invalid_arguments=[_ARG]
    )
    assert e.repair.tool == "codex_consult"
    # The rejected argument values are never echoed, so repair.arguments stays absent
    # and the combination can't read as "call the same tool again as-is".
    assert e.repair.arguments is None


def test_make_error_repair_tool_omitted_keeps_table_tool():
    # The default (repair_tool omitted) leaves the per-code table's tool in place.
    # invalid_reasoning_effort's table repair points at codex_models (the backend path).
    e = make_error("invalid_reasoning_effort", "bad effort")
    assert e.repair.tool == "codex_models"


def test_make_error_repair_tool_none_clears_table_tool():
    # Explicit None now CLEARS the table tool (distinct from omitting it), so a call site
    # whose failure the table's tool does not fit can emit a repair with no tool. On the
    # wire (exclude_none) the tool key is omitted entirely, not serialized as null.
    e = make_error("invalid_reasoning_effort", "bad effort", repair_tool=None)
    assert e.repair.tool is None
    env = ErrorResult(error=e, meta=_meta())
    d = serialize_error(env)
    assert "tool" not in d["error"]["repair"]


def test_serialize_error_strips_nulls_but_keeps_retry_after_ms():
    # A code with no details of its own: invalid_arguments would now DERIVE `details`
    # from its mandatory list (#416), which would defeat the null-stripping assertion.
    env = ErrorResult(error=make_error("internal_error", "bad"), meta=_meta())
    d = serialize_error(env)
    assert d["error"]["retry_after_ms"] is None  # kept (§6)
    assert "details" not in d["error"]  # null stripped
    assert "limit_bytes" not in d["error"]  # null stripped
    assert d["ok"] is False


def test_serialize_error_omits_absent_allowed_values_on_an_invalid_argument():
    """A non-enum field carries no `allowed_values`, and §8 strips absent optionals — so
    the key is ABSENT from the wire dict, not present-and-null. Asserted on the serialized
    payload, since the model attribute is None either way."""
    env = ErrorResult(
        error=make_error(
            "invalid_arguments",
            "bad path",
            invalid_arguments=[InvalidArgument(field="transcript_path", reason="not a .jsonl")],
        ),
        meta=_meta(),
    )
    d = serialize_error(env)
    assert d["error"]["invalid_arguments"] == [
        {"field": "transcript_path", "reason": "not a .jsonl"}
    ]
    assert "allowed_values" not in d["error"]["invalid_arguments"][0]
    assert "allowed_values" not in d["error"]["details"]


def test_serialize_error_keeps_populated_fields():
    env = ErrorResult(
        error=make_error("codex_rate_limited", "limited", retry_after_ms=60000), meta=_meta()
    )
    d = serialize_error(env)
    assert d["error"]["retry_after_ms"] == 60000
    assert d["error"]["temporary"] is True


def test_idempotency_conflict_repair_is_new_key_and_permanent():
    e = make_error("idempotency_conflict", "reused key with different args")
    assert e.repair.next_step == "use_new_idempotency_key"
    assert e.temporary is False and e.retry_after_ms is None


def test_idempotency_result_unavailable_is_permanent_new_key():
    e = make_error("idempotency_result_unavailable", "result gone")
    assert e.repair.next_step == "use_new_idempotency_key"
    assert e.temporary is False


def test_idempotency_in_progress_is_temporary_with_backoff():
    e = make_error("idempotency_in_progress", "still starting", retry_after_ms=250)
    assert e.repair.next_step == "retry_after_delay"
    assert e.temporary is True and e.retry_after_ms == 250
