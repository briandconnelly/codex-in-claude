import pytest
from pydantic import ValidationError

from codex_in_claude.schemas import ErrorDetail, ErrorInfo, Repair


def test_repair_next_step_is_symbolic_and_optional_fields_default_none():
    r = Repair(next_step="poll_job_status")
    assert r.next_step == "poll_job_status"
    assert r.tool is None and r.arguments is None and r.alternative is None


def test_errorinfo_requires_temporary_and_retry_after_ms_in_schema():
    schema = ErrorInfo.model_json_schema()
    assert "temporary" in schema["required"]
    assert "retry_after_ms" in schema["required"]


def test_errorinfo_invariant_non_temporary_forbids_retry_after_ms():
    with pytest.raises(ValidationError):
        ErrorInfo(code="internal_error", message="x", temporary=False, retry_after_ms=5)


def test_errorinfo_retry_after_ms_must_be_non_negative():
    with pytest.raises(ValidationError):
        ErrorInfo(code="codex_rate_limited", message="x", temporary=True, retry_after_ms=-1)


def test_errorinfo_temporary_with_backoff_ok():
    e = ErrorInfo(code="codex_rate_limited", message="x", temporary=True, retry_after_ms=60000)
    assert e.temporary is True and e.retry_after_ms == 60000


def test_errordetail_has_no_value_field():
    assert "value" not in ErrorDetail.model_fields
