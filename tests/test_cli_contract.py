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
