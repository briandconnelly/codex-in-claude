"""The codex CLI contract: drift/auth signatures and flag-class invariants."""

from __future__ import annotations

import pytest

from codex_in_claude import cli_contract


def test_always_send_and_help_gated_are_disjoint():
    assert cli_contract.ALWAYS_SEND_FLAGS.isdisjoint(cli_contract.HELP_GATED_FLAGS)


def test_core_sandbox_values():
    assert cli_contract.SANDBOX_READ_ONLY in cli_contract.VALID_SANDBOXES
    assert cli_contract.SANDBOX_WORKSPACE_WRITE in cli_contract.VALID_SANDBOXES
    assert cli_contract.SANDBOX_DANGER_FULL in cli_contract.VALID_SANDBOXES


@pytest.mark.parametrize(
    "text",
    [
        "error: unexpected argument '--nope' found",
        "error: invalid value 'wat' for '--sandbox'",
        "unrecognized subcommand 'frobnicate'",
        "no such subcommand",
    ],
)
def test_is_contract_drift_true(text):
    assert cli_contract.is_contract_drift(text)


def test_is_contract_drift_false_for_normal_output():
    assert not cli_contract.is_contract_drift("done", "applied patch", None)


@pytest.mark.parametrize(
    "text",
    ["Not logged in", "please run `codex login`", "401 Unauthorized", "not authenticated"],
)
def test_is_auth_failure_true(text):
    assert cli_contract.is_auth_failure(text)


def test_is_auth_failure_false():
    assert not cli_contract.is_auth_failure("wrote 3 files", None)


@pytest.mark.parametrize(
    "text",
    [
        "Error: 429 Too Many Requests",
        "you have hit your usage limit",
        "rate limit exceeded",
        "quota exceeded for this account",
        "Retry-After: 30",
    ],
)
def test_is_rate_limited_true(text):
    assert cli_contract.is_rate_limited(text)


@pytest.mark.parametrize(
    "text",
    [
        "wrote 3 files",
        "see file429.py for the handler",  # 429 without word boundaries
        "error code 4290 from the linter",  # 4290 is not a bare 429
    ],
)
def test_is_rate_limited_false(text):
    assert not cli_contract.is_rate_limited(text, None)


@pytest.mark.parametrize(
    ("text", "expected_ms"),
    [
        ("Retry-After: 30", 30_000),
        ("retry after 5s", 5_000),
        ("please try again in 12 seconds", 12_000),
        ("429 too many requests", None),  # no parseable delay
        ("retry after 5 minutes", None),  # non-second unit: don't misread as seconds
        ("retry after a 5-minute cooldown", None),  # hyphenated non-second unit
        ("try again in 2-hour window", None),  # hyphenated non-second unit
        ("Retry-After: Wed, 18 Jun 2026 12:00:00 GMT", None),  # HTTP-date, not seconds
    ],
)
def test_parse_retry_after_ms(text, expected_ms):
    assert cli_contract.parse_retry_after_ms(text) == expected_ms
