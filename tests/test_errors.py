import typing

from codex_in_claude.errors import (
    _REPAIR_BY_CODE,
    make_error,
    serialize_error,
    serialize_error_info,
)
from codex_in_claude.schemas import ErrorCode, ErrorResult, Meta


def _meta():
    return Meta(
        cwd="/x",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=180,
        elapsed_ms=1,
    )


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
    e = make_error("invalid_arguments", "bad arg")
    assert e.temporary is False and e.retry_after_ms is None
    assert e.repair.next_step == "correct_arguments"


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
    e = make_error("invalid_arguments", "bad arg", repair_tool="codex_consult")
    assert e.repair.tool == "codex_consult"
    # The rejected argument values are never echoed, so repair.arguments stays absent
    # and the combination can't read as "call the same tool again as-is".
    assert e.repair.arguments is None


def test_serialize_error_strips_nulls_but_keeps_retry_after_ms():
    env = ErrorResult(error=make_error("invalid_arguments", "bad"), meta=_meta())
    d = serialize_error(env)
    assert d["error"]["retry_after_ms"] is None  # kept (§6)
    assert "details" not in d["error"]  # null stripped
    assert "limit_bytes" not in d["error"]  # null stripped
    assert d["ok"] is False


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
