"""CodexBackend: real-adapter validation of the provisional pontonier protocol.

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
from codex_in_claude import codex, preflight
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
