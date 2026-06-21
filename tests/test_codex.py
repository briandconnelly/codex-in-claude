"""Codex command building, probes, and failure classification."""

from __future__ import annotations

from codex_in_claude import cli_contract, codex
from codex_in_claude._core.runtime import CommandRun
from codex_in_claude.preflight import FlagSupport

_ALL_FLAGS = FlagSupport(
    supported=frozenset(cli_contract.ALWAYS_SEND_FLAGS | set(cli_contract.HELP_GATED_FLAGS)),
    help_parsed=True,
)
_NO_MODEL = FlagSupport(supported=frozenset(cli_contract.ALWAYS_SEND_FLAGS), help_parsed=True)


def test_build_exec_command_core(tmp_path):
    out = str(tmp_path / "last.txt")
    cmd, dropped = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=out,
        model="gpt-5.4",
        flag_support=_ALL_FLAGS,
    )
    assert cmd[0] == "codex"
    assert "exec" in cmd
    assert "--json" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[cmd.index("--cd") + 1] == "/repo"
    assert cmd[cmd.index("--output-last-message") + 1] == out
    assert "--ephemeral" in cmd
    assert cmd[cmd.index("--model") + 1] == "gpt-5.4"
    assert cmd[-1] == cli_contract.STDIN_PROMPT  # prompt via stdin sentinel
    assert dropped == []


def test_build_exec_command_isolation(tmp_path):
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="workspace-write",
        isolation="ignore-rules",
        output_last_message_path=str(tmp_path / "l"),
        flag_support=_ALL_FLAGS,
    )
    assert "--ignore-user-config" in cmd
    assert "--ignore-rules" in cmd


def test_build_exec_command_drops_unsupported_model(tmp_path):
    cmd, dropped = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        model="gpt-5.4",
        flag_support=_NO_MODEL,
    )
    assert "--model" not in cmd
    assert "gpt-5.4" not in cmd
    assert dropped == ["--model"]


def test_build_exec_command_schema_and_add_dir(tmp_path):
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="workspace-write",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        output_schema_path=str(tmp_path / "s.json"),
        add_dirs=("/extra",),
        skip_git_repo_check=True,
        flag_support=_ALL_FLAGS,
    )
    assert "--output-schema" in cmd
    assert cmd[cmd.index("--add-dir") + 1] == "/extra"
    assert "--skip-git-repo-check" in cmd


def test_classify_not_found():
    err = codex.classify_failure(CommandRun("", codex.runtime.BINARY_NOT_FOUND, 127, 1, False))
    assert err.code == "codex_not_found"


def test_classify_timeout():
    err = codex.classify_failure(CommandRun("", codex.runtime.TIMED_OUT, -9, 1, True))
    assert err.code == "timeout"
    assert err.retryable


def test_classify_auth():
    err = codex.classify_failure(CommandRun("", "Not logged in. Run `codex login`", 1, 1, False))
    assert err.code == "codex_auth_required"


def test_classify_contract_drift():
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '--zzz' found", 2, 1, False)
    )
    assert err.code == "cli_contract_changed"


def test_classify_nonzero_generic():
    err = codex.classify_failure(CommandRun("", "boom", 1, 1, False))
    assert err.code == "nonzero_exit"
    assert "boom" in err.message


def test_classify_uses_error_event_message():
    events = '{"type":"turn.failed","error":{"message":"model overloaded"}}'
    err = codex.classify_failure(CommandRun(events, "", 1, 1, False), events=events)
    assert err.code == "nonzero_exit"
    assert "model overloaded" in err.message


def test_classify_auth_from_error_event():
    events = '{"type":"error","message":"401 Unauthorized"}'
    err = codex.classify_failure(CommandRun(events, "", 1, 1, False), events=events)
    assert err.code == "codex_auth_required"


def test_auth_beats_drift_ordering():
    # A message with both auth + a clap-ish phrase classifies as auth, not drift.
    err = codex.classify_failure(CommandRun("", "not authenticated; invalid value", 1, 1, False))
    assert err.code == "codex_auth_required"


def test_classify_rate_limited_with_retry_after():
    err = codex.classify_failure(
        CommandRun("", "Error: 429 Too Many Requests. Retry-After: 30", 1, 1, False)
    )
    assert err.code == "codex_rate_limited"
    assert err.retryable
    assert err.retry_after_ms == 30_000


def test_classify_rate_limited_preserves_zero_retry_after():
    # An explicit "Retry-After: 0" (retry now) must be preserved, not coalesced to
    # the default backoff by a falsey check.
    err = codex.classify_failure(CommandRun("", "rate limit hit; Retry-After: 0", 1, 1, False))
    assert err.code == "codex_rate_limited"
    assert err.retry_after_ms == 0


def test_classify_rate_limited_default_backoff():
    err = codex.classify_failure(CommandRun("", "you have hit your usage limit", 1, 1, False))
    assert err.code == "codex_rate_limited"
    assert err.retryable
    assert err.retry_after_ms == cli_contract.RATE_LIMIT_DEFAULT_BACKOFF_MS


def test_classify_rate_limited_from_error_event():
    events = '{"type":"error","message":"rate limit exceeded"}'
    err = codex.classify_failure(CommandRun(events, "", 1, 1, False), events=events)
    assert err.code == "codex_rate_limited"


def test_auth_beats_rate_limit_ordering():
    # An auth message that also mentions a limit classifies as auth, not rate-limit.
    err = codex.classify_failure(CommandRun("", "401 unauthorized: usage limit", 1, 1, False))
    assert err.code == "codex_auth_required"


def test_drift_beats_rate_limit_ordering():
    # A genuine contract-drift error is never masked as a transient rate limit.
    err = codex.classify_failure(
        CommandRun("", "error: invalid value 'x'; rate limit", 2, 1, False)
    )
    assert err.code == "cli_contract_changed"


def test_codex_version(monkeypatch):
    monkeypatch.setattr(
        codex.runtime,
        "run_sync_capture",
        lambda cmd, timeout_seconds: CommandRun("codex-cli 0.141.0\n", "", 0, 1, False),
    )
    assert codex.codex_version() == "codex-cli 0.141.0"


def test_codex_version_missing(monkeypatch):
    monkeypatch.setattr(
        codex.runtime,
        "run_sync_capture",
        lambda cmd, timeout_seconds: CommandRun("", codex.runtime.BINARY_NOT_FOUND, 127, 1, False),
    )
    assert codex.codex_version() is None


def test_login_status_chatgpt(monkeypatch):
    monkeypatch.setattr(
        codex.runtime,
        "run_sync_capture",
        lambda cmd, timeout_seconds: CommandRun("Logged in using ChatGPT\n", "", 0, 1, False),
    )
    ok, detail = codex.login_status()
    assert ok is True
    assert "ChatGPT" in detail


def test_login_status_logged_out(monkeypatch):
    monkeypatch.setattr(
        codex.runtime,
        "run_sync_capture",
        lambda cmd, timeout_seconds: CommandRun("", "not logged in", 1, 1, False),
    )
    ok, detail = codex.login_status()
    assert ok is False
    assert "login" in detail


def test_login_status_unknown_when_missing(monkeypatch):
    monkeypatch.setattr(
        codex.runtime,
        "run_sync_capture",
        lambda cmd, timeout_seconds: CommandRun("", codex.runtime.BINARY_NOT_FOUND, 127, 1, False),
    )
    ok, detail = codex.login_status()
    assert ok is None
    assert detail is None


async def test_run_codex_exec_reads_last_message(monkeypatch, tmp_path):
    async def fake_run_async(cmd, *, cwd, timeout_seconds, stdin_text):
        # Emulate codex writing the final message to --output-last-message.
        out_path = cmd[cmd.index("--output-last-message") + 1]
        from pathlib import Path

        Path(out_path).write_text(
            '{"summary":"hi","verdict":"pass","confidence":"high","findings":[]}'
        )
        return CommandRun('{"type":"token_count","usage":{"input_tokens":3}}\n', "", 0, 7, False)

    monkeypatch.setattr(codex.runtime, "run_async", fake_run_async)
    result = await codex.run_codex_exec(
        "q",
        cwd=str(tmp_path),
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=30,
        output_schema={"type": "object"},
        flag_support=_ALL_FLAGS,
    )
    assert result.run.exit_code == 0
    assert "summary" in (result.last_message or "")
