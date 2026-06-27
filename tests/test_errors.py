import typing

from codex_in_claude.errors import _REPAIR_BY_CODE, make_error, serialize_error
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
