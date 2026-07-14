"""Codex command building, probes, and failure classification."""

from __future__ import annotations

import tomllib

import anyio
import pytest

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


@pytest.mark.parametrize(
    ("sandbox", "isolation"),
    [
        ("read-only", "inherit"),  # consult / review tier
        ("workspace-write", "inherit"),  # delegate tier
        ("workspace-write", "ignore-rules"),  # most-isolated
    ],
)
def test_build_exec_command_disables_remote_plugin_every_tier(tmp_path, sandbox, isolation):
    # #287: connectors are disabled on EVERY model-bearing call, regardless of tier/isolation.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox=sandbox,
        isolation=isolation,
        output_last_message_path=str(tmp_path / "l"),
        flag_support=_ALL_FLAGS,
    )
    assert cmd[cmd.index("--disable") + 1] == cli_contract.REMOTE_PLUGIN_FEATURE
    # It is a plugin-owned flag (before operator extra_args), never gated away.
    assert cli_contract.DISABLE_FEATURE_FLAG in cli_contract.ALWAYS_SEND_FLAGS


def test_build_exec_command_disable_precedes_extra_args(tmp_path):
    # The plugin-owned --disable is emitted before any operator extra_args, so an operator
    # token can never displace it (and --disable wins over --enable regardless of order).
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        extra_args=("-c", "model_provider=x"),
        flag_support=_ALL_FLAGS,
    )
    assert cmd.index("--disable") < cmd.index("-c")


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


def test_build_exec_command_passes_arbitrary_model_through(tmp_path):
    # An unlisted/unknown slug is NOT validated here — codex exec is the validator.
    cmd, dropped = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        model="totally-made-up-model-9000",
        flag_support=_ALL_FLAGS,
    )
    assert cmd[cmd.index("--model") + 1] == "totally-made-up-model-9000"
    assert dropped == []


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
    assert err.temporary


def test_classify_auth():
    err = codex.classify_failure(CommandRun("", "Not logged in. Run `codex login`", 1, 1, False))
    assert err.code == "codex_auth_required"
    assert err.repair.next_step == "authenticate"


def test_classify_contract_drift():
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '--zzz' found", 2, 1, False)
    )
    assert err.code == "cli_contract_changed"


def test_classify_unknown_feature_flag_is_contract_drift():
    # #287: an upstream rename/removal of remote_plugin makes `--disable remote_plugin` print
    # "Unknown feature flag" — must fail-closed as cli_contract_changed, not generic nonzero_exit.
    err = codex.classify_failure(
        CommandRun("", "Error: Unknown feature flag: remote_plugin", 1, 1, False)
    )
    assert err.code == "cli_contract_changed"


def test_classify_nonzero_generic():
    err = codex.classify_failure(CommandRun("", "boom", 1, 1, False))
    assert err.code == "nonzero_exit"
    assert "boom" in err.message


def test_classify_failure_redacts_secret_in_detail():
    # A secret echoed by codex/git before a non-zero exit must not reach error.message.
    secret = "sk-" + "a" * 32
    err = codex.classify_failure(CommandRun("", f"auth failed token={secret}", 1, 1, False))
    assert err.code == "nonzero_exit"
    assert secret not in err.message
    assert "[redacted: secret value]" in err.message


def test_classify_failure_redacts_secret_straddling_truncation_boundary():
    # A secret that crosses the 300-char detail cut must still be fully redacted:
    # redaction runs on the whole text before truncation, so no prefix can leak.
    secret = "sk-" + "a" * 40
    stderr = "x" * 290 + secret  # begins before the 300-char cut, extends past it
    err = codex.classify_failure(CommandRun("", stderr, 1, 1, False))
    assert err.code == "nonzero_exit"
    assert "sk-aaaaaaa" not in err.message


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
    assert err.temporary
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
    assert err.temporary
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
        lambda cmd, timeout_seconds: CommandRun("codex-cli 0.144.1\n", "", 0, 1, False),
    )
    assert codex.codex_version() == "codex-cli 0.144.1"


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
    async def fake_run_async(
        cmd, *, cwd, timeout_seconds, stdin_text, on_stdout_line=None, max_output_bytes=None
    ):
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


def test_run_codex_exec_forwards_on_event(monkeypatch):
    captured = {}

    async def fake_run_async(
        cmd, *, cwd, timeout_seconds, stdin_text=None, on_stdout_line=None, max_output_bytes=None
    ):
        captured["on_stdout_line"] = on_stdout_line
        from codex_in_claude._core.runtime import CommandRun

        return CommandRun("", "", 0, 1, False)

    monkeypatch.setattr(codex.runtime, "run_async", fake_run_async)
    sentinel = lambda _l: None  # noqa: E731
    anyio.run(
        lambda: codex.run_codex_exec(
            "p",
            cwd=".",
            sandbox="read-only",
            isolation="inherit",
            timeout_seconds=10,
            on_event=sentinel,
        )
    )
    assert captured["on_stdout_line"] is sentinel


# --- CODEX_IN_CLAUDE_EXTRA_ARGS injection + reclassification (#231) ----------------

from codex_in_claude import config  # noqa: E402


def test_build_exec_command_appends_extra_args_before_sentinel(tmp_path):
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        model="gpt-5.4",
        extra_args=("-c", "model_provider=litellm", "--profile", "work"),
        flag_support=_ALL_FLAGS,
    )
    # Extra args land after --model, and immediately before the stdin sentinel.
    assert cmd[-1] == cli_contract.STDIN_PROMPT
    assert cmd[-5:-1] == ["-c", "model_provider=litellm", "--profile", "work"]
    assert cmd.index("-c") > cmd.index("--model")


def test_build_exec_command_extra_args_survive_model_gating(tmp_path):
    # Even when --model is help-gated away, extra args are never gated/dropped.
    cmd, dropped = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        model="gpt-5.4",
        extra_args=("--profile", "work"),
        flag_support=_NO_MODEL,
    )
    assert "--model" in dropped
    assert cmd[-3:] == ["--profile", "work", cli_contract.STDIN_PROMPT]


def _extra(descriptors, tokens=("-c", "x=y")):
    return config.ExtraArgs(
        tokens=tuple(tokens), descriptors=tuple(descriptors), option_count=1, configured=True
    )


def test_classify_drift_attributes_to_extra_args_when_named():
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '--profile' found", 2, 1, False),
        extra_args=_extra(["--profile", "work"]),
    )
    assert err.code == "extra_args_rejected"
    assert "CODEX_IN_CLAUDE_EXTRA_ARGS" in (err.repair.alternative or "")


def test_classify_drift_stays_contract_changed_for_plugin_flag():
    # codex rejected --sandbox (a plugin guarantee flag), NOT any extra-arg descriptor.
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '--sandbox' found", 2, 1, False),
        extra_args=_extra(["--profile", "work"]),
    )
    assert err.code == "cli_contract_changed"


def test_classify_drift_contract_changed_when_no_extra_args():
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '--zzz' found", 2, 1, False),
        extra_args=config.ExtraArgs(),  # unconfigured
    )
    assert err.code == "cli_contract_changed"


def test_extra_args_rejected_error_hides_secret_value():
    err = codex.classify_failure(
        CommandRun("", "error: invalid value for '--profile'", 2, 1, False),
        extra_args=config.ExtraArgs(
            tokens=("-c", "api_key=sk-secret", "--profile", "work"),
            descriptors=("api_key", "--profile", "work"),
            option_count=2,
            configured=True,
        ),
    )
    assert err.code == "extra_args_rejected"
    assert "sk-secret" not in err.message
    assert "sk-secret" not in (err.repair.alternative or "")


def test_classify_reads_extra_args_from_env_by_default(monkeypatch):
    # No explicit extra_args -> classify_failure reads config.extra_args() from env.
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "--profile work")
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '--profile' found", 2, 1, False)
    )
    assert err.code == "extra_args_rejected"


def test_classify_short_descriptor_does_not_misattribute_plugin_drift():
    # Regression (#231 review): a short profile/feature name must not substring-match
    # inside an unrelated plugin-flag rejection ("a" appears inside "--sandbox").
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '--sandbox' found", 2, 1, False),
        extra_args=_extra(["-p", "a"], tokens=("-p", "a")),
    )
    assert err.code == "cli_contract_changed"


def test_classify_quoted_descriptor_still_attributes_to_extra_args():
    # A genuine extra-arg rejection where codex quotes the profile name still matches.
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '--profile' found", 2, 1, False),
        extra_args=_extra(["--profile", "work"], tokens=("--profile", "work")),
    )
    assert err.code == "extra_args_rejected"


def test_classify_attributes_config_flag_token_drift_to_extra_args():
    # Copilot #237: a rejection of the `--config` FLAG token itself (not the key) is
    # still the operator's passthrough, so descriptors include the flag.
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '--config' found", 2, 1, False),
        extra_args=config.ExtraArgs(
            tokens=("--config", "model_provider=x"),
            descriptors=("--config", "model_provider"),
            option_count=1,
            configured=True,
        ),
    )
    assert err.code == "extra_args_rejected"


# --- Reasoning-effort control (#309) ----------------------------------------------
_EFFORT_KEY = cli_contract.MODEL_REASONING_EFFORT_CONFIG_KEY
# The real backend rejection captured from codex-cli 0.144.3 (2026-07-13, probe with
# `-c model_reasoning_effort=totally-bogus-effort` on a valid model).
_EFFORT_REJECTION_EVENT = (
    '{"type":"error","message":"{\\"type\\": \\"error\\", \\"error\\": {\\"type\\": '
    '\\"invalid_request_error\\", \\"message\\": \\"[ReasoningEffortParam] '
    "[reasoning.effort] [invalid_enum_value] Invalid value: 'totally-bogus-effort'. "
    "Supported values are: 'none', 'minimal', 'low', 'medium', 'high', and "
    '\'xhigh\'.\\"}, \\"status\\": 400}"}'
)


def test_build_exec_command_passes_reasoning_effort_as_config_override(tmp_path):
    cmd, dropped = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        reasoning_effort="high",
        flag_support=_ALL_FLAGS,
    )
    assert f'{_EFFORT_KEY}="high"' in cmd
    assert cmd[cmd.index(f'{_EFFORT_KEY}="high"') - 1] == "-c"
    assert dropped == []


def test_build_exec_command_omits_reasoning_effort_when_none(tmp_path):
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        reasoning_effort=None,
        flag_support=_ALL_FLAGS,
    )
    assert not any(_EFFORT_KEY in tok for tok in cmd)


def test_build_exec_command_passes_empty_reasoning_effort_through(tmp_path):
    # Whole-domain rule: an explicit "" is the caller's value, passed through for
    # codex/the backend to judge — never silently coalesced to a default or dropped.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        reasoning_effort="",
        flag_support=_ALL_FLAGS,
    )
    assert f'{_EFFORT_KEY}=""' in cmd


@pytest.mark.parametrize(
    ("value", "expected_token"),
    [
        ("true", f'{_EFFORT_KEY}="true"'),  # boolean-shaped
        ("3", f'{_EFFORT_KEY}="3"'),  # integer-shaped
        ("1.5", f'{_EFFORT_KEY}="1.5"'),  # float-shaped
        ('"high"', f'{_EFFORT_KEY}="\\"high\\""'),  # quoted — must NOT be unwrapped
        ("[low, high]", f'{_EFFORT_KEY}="[low, high]"'),  # array-shaped
        ("{effort = 1}", f'{_EFFORT_KEY}="{{effort = 1}}"'),  # table-shaped
        # Astral char: default \uXXXX escaping would emit a surrogate PAIR, which
        # TOML rejects (escapes must be scalar values) — degrading to the raw-string
        # fallback; the encoder must emit it literally (ensure_ascii=False).
        ("high\U0001f600", f'{_EFFORT_KEY}="high\U0001f600"'),
    ],
)
def test_build_exec_command_toml_string_encodes_reasoning_effort(tmp_path, value, expected_token):
    # Maintainer-review regression (#313): codex TOML-parses the `-c` right-hand side
    # and falls back to a string only when that parse fails, so a raw interpolation
    # retypes boolean/numeric/collection-shaped values (0.144.3 then rejects them
    # locally as an invalid type → misreported nonzero_exit) and silently unwraps
    # quoted ones. TOML-string-encoding every value (JSON string syntax is valid
    # TOML) makes the advertised open string round-trip exactly.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        reasoning_effort=value,
        flag_support=_ALL_FLAGS,
    )
    assert expected_token in cmd
    assert cmd[cmd.index(expected_token) - 1] == "-c"
    # The round-trip proof: the right-hand side is valid TOML that decodes back to
    # the caller's exact string (codex's fallback-to-raw-string never engages).
    encoded = expected_token.partition("=")[2]
    assert tomllib.loads(f"v = {encoded}")["v"] == value


def test_build_exec_command_reasoning_effort_survives_model_gating(tmp_path):
    # --model is help-gated and may be dropped; the effort -c pair is a config
    # override, never gated, and must survive intact (it then applies to whatever
    # model codex resolves).
    cmd, dropped = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        model="gpt-5.4",
        reasoning_effort="xhigh",
        flag_support=_NO_MODEL,
    )
    assert dropped == ["--model"]
    assert f'{_EFFORT_KEY}="xhigh"' in cmd


def test_build_exec_command_reasoning_effort_precedes_extra_args_and_sentinel(tmp_path):
    # Plugin-owned tokens come before operator extra_args and the stdin sentinel.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        reasoning_effort="low",
        extra_args=("-p", "work"),
        flag_support=_ALL_FLAGS,
    )
    assert cmd.index(f'{_EFFORT_KEY}="low"') < cmd.index("-p")
    assert cmd[-1] == cli_contract.STDIN_PROMPT


def test_classify_backend_effort_rejection_when_effort_sent():
    # The backend 400 for a bad effort VALUE contains "Invalid value", which matches
    # the drift patterns — but when this run sent a first-class effort override, it is
    # the caller's argument, not contract drift (#309).
    err = codex.classify_failure(
        CommandRun(_EFFORT_REJECTION_EVENT, "", 1, 1, False),
        events=_EFFORT_REJECTION_EVENT,
        extra_args=config.ExtraArgs(),
        reasoning_effort="totally-bogus-effort",
    )
    assert err.code == "invalid_reasoning_effort"
    assert err.temporary is False
    assert err.details is not None and err.details.field == "reasoning_effort"
    assert err.repair is not None
    assert err.repair.next_step == "correct_arguments"
    assert err.repair.tool == "codex_models"
    # The rejected value is never echoed back (it is caller input).
    assert "totally-bogus-effort" not in err.message


def test_classify_effort_marker_without_sent_effort_stays_contract_changed():
    # No first-class effort was sent, so an effort-flavored rejection cannot be the
    # caller's argument; the fail-loud drift classification stands.
    err = codex.classify_failure(
        CommandRun(_EFFORT_REJECTION_EVENT, "", 1, 1, False),
        events=_EFFORT_REJECTION_EVENT,
        extra_args=config.ExtraArgs(),
        reasoning_effort=None,
    )
    assert err.code == "cli_contract_changed"


def test_classify_key_only_rejection_stays_contract_changed():
    # A future codex rejecting the CONFIG KEY itself (drift) names the key, not the
    # backend's reasoning.effort markers — it must stay cli_contract_changed even
    # though an effort was sent.
    err = codex.classify_failure(
        CommandRun("", f"error: invalid value 'high' for '{_EFFORT_KEY}'", 2, 1, False),
        extra_args=config.ExtraArgs(),
        reasoning_effort="high",
    )
    assert err.code == "cli_contract_changed"


def test_classify_extra_args_attribution_wins_without_effort_markers():
    # A drift codex explicitly attributes to an operator passthrough entry keeps the
    # extra_args_rejected classification when an effort override was also sent but the
    # blob carries NO backend effort markers (marker-bearing rejections win instead —
    # see test_classify_effort_markers_beat_incidental_descriptor_match).
    blob = "error: unexpected argument '--profile' found"
    err = codex.classify_failure(
        CommandRun("", blob, 2, 1, False),
        extra_args=_extra(["--profile", "work"]),
        reasoning_effort="high",
    )
    assert err.code == "extra_args_rejected"


def test_classify_auth_beats_effort_rejection():
    # Auth failure classification runs before drift/effort attribution.
    err = codex.classify_failure(
        CommandRun("", f"not logged in\n{_EFFORT_REJECTION_EVENT}", 1, 1, False),
        extra_args=config.ExtraArgs(),
        reasoning_effort="high",
    )
    assert err.code == "codex_auth_required"


def test_classify_shared_dash_c_rejection_stays_contract_changed_when_effort_sent():
    # Codex-review regression (#309): the plugin itself sends a bare `-c` pair for a
    # first-class effort, so a rejection naming ONLY the shared `-c` flag must stay
    # fail-loud cli_contract_changed even when an operator passthrough also uses `-c`.
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '-c' found", 2, 1, False),
        extra_args=config.ExtraArgs(
            tokens=("-c", "model_provider=azure"),
            descriptors=("-c", "model_provider"),
            option_count=1,
            configured=True,
        ),
        reasoning_effort="high",
    )
    assert err.code == "cli_contract_changed"


def test_classify_dash_c_rejection_attributes_to_extra_args_without_effort():
    # Without a first-class effort the plugin sent no `-c` of its own, so the
    # operator's passthrough keeps the attribution (pre-#309 behavior).
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '-c' found", 2, 1, False),
        extra_args=config.ExtraArgs(
            tokens=("-c", "model_provider=azure"),
            descriptors=("-c", "model_provider"),
            option_count=1,
            configured=True,
        ),
        reasoning_effort=None,
    )
    assert err.code == "extra_args_rejected"


def test_classify_key_naming_rejection_still_attributes_to_extra_args_with_effort():
    # A rejection that names an operator-owned KEY (not just the shared flag) is
    # unambiguous and keeps the extra-args attribution even when an effort was sent.
    err = codex.classify_failure(
        CommandRun("", "error: invalid value for '-c': 'model_provider'", 2, 1, False),
        extra_args=config.ExtraArgs(
            tokens=("-c", "model_provider=azure"),
            descriptors=("-c", "model_provider"),
            option_count=1,
            configured=True,
        ),
        reasoning_effort="high",
    )
    assert err.code == "extra_args_rejected"


def test_classify_marker_named_passthrough_attributes_to_extra_args():
    # Maintainer-review regression (#313): `--enable reasoning.effort` in the
    # operator passthrough makes codex print "Unknown feature flag: reasoning.effort"
    # — a marker as a free substring, without the backend's bracketed `[…] […]`
    # signature. That failure is the operator's entry (extra_args_rejected), not a
    # backend effort rejection, even though an effort override was also sent.
    err = codex.classify_failure(
        CommandRun("", "Unknown feature flag: reasoning.effort", 2, 1, False),
        extra_args=config.ExtraArgs(
            tokens=("--enable", "reasoning.effort"),
            descriptors=("--enable", "reasoning.effort"),
            option_count=1,
            configured=True,
        ),
        reasoning_effort="high",
    )
    assert err.code == "extra_args_rejected"


def test_classify_effort_markers_beat_incidental_descriptor_match():
    # Codex re-review regression (#309): the backend's effort rejection QUOTES the
    # supported effort names, so an operator profile that happens to be named "high"
    # token-matches the blob; the marker-bearing effort classification must win over
    # that incidental descriptor hit.
    err = codex.classify_failure(
        CommandRun(_EFFORT_REJECTION_EVENT, "", 1, 1, False),
        events=_EFFORT_REJECTION_EVENT,
        extra_args=config.ExtraArgs(
            tokens=("-p", "high"),
            descriptors=("-p", "high"),
            option_count=1,
            configured=True,
        ),
        reasoning_effort="totally-bogus-effort",
    )
    assert err.code == "invalid_reasoning_effort"
