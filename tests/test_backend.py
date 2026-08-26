"""CodexBackend: real-adapter validation of the frozen pontonier protocol.

The load-bearing test is the argv differential: the adapter's PreparedRun must
build the SAME command the production `codex.run_codex_exec` path builds
(normalized for the per-run temp paths). If the two ever diverge, the adapter
is validating a protocol against behavior nobody runs.
"""

from __future__ import annotations

import re

import pytest
from pontonier.backend.protocol import AgentBackend, RunOutcome, RunRequest
from pontonier.conventions.preflight import FlagSupport
from pontonier.core.runtime import CommandRun
from pontonier.testing import conformance

from codex_in_claude import backend as backend_mod
from codex_in_claude import codex, config, preflight
from codex_in_claude.cli_contract import PONTONIER_CONTRACT

BACKEND = backend_mod.CodexBackend()

_FULL_SUPPORT = FlagSupport(supported=frozenset({"--model"}), help_parsed=True)


@pytest.fixture(autouse=True)
def _stable_flag_support(monkeypatch):
    """Pin the help probe so argv comparisons don't depend on an installed codex."""
    monkeypatch.setattr(preflight, "flag_support", lambda force=False: _FULL_SUPPORT)


def _normalize_temp_paths(argv: tuple[str, ...] | list[str]) -> list[str]:
    return [re.sub(r"/[^\s]*codex-in-claude-[^/]+/", "/TMPDIR/", tok) for tok in argv]


def test_backend_is_structurally_conformant():
    assert isinstance(BACKEND, AgentBackend)


def test_backend_passes_pontonier_conformance():
    assert conformance.check_contract(PONTONIER_CONTRACT) == []
    assert conformance.check_backend(PONTONIER_CONTRACT, BACKEND) == []


async def test_prepared_argv_matches_production_builder(monkeypatch, tmp_path, clean_env):
    """Differential: adapter argv == build_exec_command argv, temp paths aside."""
    request = RunRequest(
        kind="consult",
        prompt="why?",
        cwd=str(tmp_path),
        timeout_seconds=60,
        schema={"type": "object"},
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    async with BACKEND.prepare(request) as prepared:
        adapter_argv = _normalize_temp_paths(prepared.argv)
        assert prepared.stdin_text == "why?"  # prompt over stdin, never argv

    expected_cmd, dropped = codex.build_exec_command(
        cwd=str(tmp_path),
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path="/TMPDIR/last-message.txt",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        output_schema_path="/TMPDIR/schema.json",
        # Backend policy pinned by this differential: consult always skips the
        # repo check (read-only Q&A; repo membership irrelevant). Since the
        # freeze-window re-plumb the adapter IS production, so this comparison
        # pins the kind→flag mapping rather than mirroring a separate hot path.
        skip_git_repo_check=True,
        extra_args=(),
        flag_support=_FULL_SUPPORT,
    )
    assert dropped == []
    assert adapter_argv == _normalize_temp_paths(expected_cmd)


async def test_delegate_kind_maps_to_propose_sandbox(tmp_path, clean_env):
    request = RunRequest(kind="delegate", prompt="do", cwd=str(tmp_path), timeout_seconds=60)
    async with BACKEND.prepare(request) as prepared:
        i = prepared.argv.index("--sandbox")
        assert prepared.argv[i + 1] == "workspace-write"


async def test_consult_kind_maps_to_read_only_sandbox(tmp_path, clean_env):
    request = RunRequest(kind="consult", prompt="q", cwd=str(tmp_path), timeout_seconds=60)
    async with BACKEND.prepare(request) as prepared:
        i = prepared.argv.index("--sandbox")
        assert prepared.argv[i + 1] == "read-only"


async def test_artifacts_are_inside_a_run_temp_dir_and_cleaned(tmp_path, clean_env):
    request = RunRequest(
        kind="consult",
        prompt="q",
        cwd=str(tmp_path),
        timeout_seconds=60,
        schema={"type": "object"},
    )
    async with BACKEND.prepare(request) as prepared:
        from pathlib import Path

        last_message, schema = prepared.artifacts
        assert "codex-in-claude-" in last_message
        assert Path(schema).exists()
    assert not Path(schema).exists()  # context exit removed the staged temp dir


def test_validate_request_shape_guards_effort():
    bad = RunRequest(
        kind="consult", prompt="q", cwd=".", timeout_seconds=10, reasoning_effort="x" * 10_000
    )
    rejected = BACKEND.validate_request(bad)
    assert rejected is not None
    assert rejected.code == "invalid_reasoning_effort"


def test_validate_request_passes_normal_effort():
    ok = RunRequest(
        kind="consult", prompt="q", cwd=".", timeout_seconds=10, reasoning_effort="high"
    )
    assert BACKEND.validate_request(ok) is None


def _failed_outcome(stderr: str, **artifacts: str) -> RunOutcome:
    return RunOutcome(
        run=CommandRun(stdout="", stderr=stderr, exit_code=1, elapsed_ms=5, timed_out=False),
        artifact_texts=artifacts,
    )


def test_classify_failure_delegates_to_production_classifier():
    """Same evidence in, same code out as codex.classify_failure — the adapter
    adds no classification logic of its own."""
    stderr = "error: unexpected argument '--zap' found"
    request = RunRequest(kind="consult", prompt="q", cwd=".", timeout_seconds=10)
    adapter_result = BACKEND.classify_failure(_failed_outcome(stderr), request)
    direct = codex.classify_failure(
        CommandRun(stdout="", stderr=stderr, exit_code=1, elapsed_ms=5, timed_out=False)
    )
    assert adapter_result.code == direct.code == "cli_contract_changed"


def test_finalize_extracts_structured_answer_and_usage():
    request = RunRequest(
        kind="consult", prompt="q", cwd=".", timeout_seconds=10, schema={"type": "object"}
    )
    events = (
        '{"type":"token_count","usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}\n'
    )
    outcome = RunOutcome(
        run=CommandRun(stdout=events, stderr="", exit_code=0, elapsed_ms=5, timed_out=False),
        events=events,
        artifact_texts={"last-message": '{"summary": "fine"}'},
    )
    result = BACKEND.finalize(outcome, request)
    assert result.answer == '{"summary": "fine"}'
    assert result.structured == {"summary": "fine"}


def test_scrub_env_is_identity_for_codex():
    env = {"PATH": "/bin", "CODEX_HOME": "/x", "ANTHROPIC_API_KEY": "k"}
    assert BACKEND.scrub_env(dict(env), None) == env


_INVALID_PIN_VALUE_STDERR = (
    'Error loading config.toml: invalid type: string "yes", expected a boolean\n'
    "in `sandbox_workspace_write.network_access`\n\n"
)


@pytest.mark.parametrize(
    ("kind", "access", "expected_code"),
    [
        # A delegate resolves to workspace-write, which pins this key: the refused value
        # can only be the plugin's own -> drift.
        ("delegate", None, "cli_contract_changed"),
        # A consult resolves to read-only, which sends no pin: the user's own file.
        ("consult", None, "user_config_rejected"),
        # An explicit access override is what prepare() builds the argv from, so the
        # classifier must resolve the sandbox the same way.
        ("consult", "workspace-write", "cli_contract_changed"),
    ],
)
def test_classify_failure_attributes_a_pinned_key_by_the_resolved_sandbox(
    kind, access, expected_code, monkeypatch
):
    """The adapter must hand the classifier the `-c` keys THIS request's argv carried (#550).

    The direct classifier tests cannot see this seam: dropping `plugin_config_keys=` here
    leaves them green while every pontonier caller gets a workspace-write pin rejection
    reported as the user's config."""
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    request = RunRequest(kind=kind, prompt="q", cwd=".", timeout_seconds=10, access=access)
    result = BACKEND.classify_failure(_failed_outcome(_INVALID_PIN_VALUE_STDERR), request)
    assert result.code == expected_code
