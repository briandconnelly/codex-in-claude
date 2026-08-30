"""Codex command building, probes, and failure classification."""

from __future__ import annotations

import os
import tomllib

import anyio
import pytest
from pontonier.core import redaction, worktree
from pontonier.core.runtime import CommandRun

from codex_in_claude import binpath, cli_contract, codex, config
from codex_in_claude.preflight import FlagSupport

_ALL_FLAGS = FlagSupport(
    supported=frozenset(cli_contract.ALWAYS_SEND_FLAGS | set(cli_contract.HELP_GATED_FLAGS)),
    help_parsed=True,
)
_NO_MODEL = FlagSupport(supported=frozenset(cli_contract.ALWAYS_SEND_FLAGS), help_parsed=True)


def test_build_exec_command_core(tmp_path, monkeypatch):
    # This test is about the argv build, not binary resolution (that behavior is
    # covered by tests/test_binresolve.py and tests/test_binpath.py) -- pin the
    # resolved binary to a distinctive sentinel so the assertion below is both
    # independent of whether this machine happens to have a real `codex` install
    # AND actually verifies position 0 carries the *resolved* value (not a
    # tautology against the bare literal).
    resolved_bin = "/fake/resolved/codex"
    monkeypatch.setattr(binpath, "codex_bin", lambda: resolved_bin)
    out = str(tmp_path / "last.txt")
    cmd, dropped = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=out,
        model="gpt-5.4",
        flag_support=_ALL_FLAGS,
    )
    assert cmd[0] == resolved_bin
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


@pytest.mark.parametrize("isolation", config.VALID_ISOLATIONS)
@pytest.mark.parametrize("sandbox", cli_contract.VALID_SANDBOXES)
def test_build_exec_command_pins_network_access_exactly_on_workspace_write(
    tmp_path, sandbox, isolation
):
    # #518: at the default isolation (inherit) codex reads $CODEX_HOME/config.toml, where
    # `[sandbox_workspace_write] network_access = true` would silently void the advertised
    # no-network-egress guarantee. The pin closes that channel (and --profile — the `-c`
    # override outranks both, verified live on 0.148.0 and re-verified on 0.149.1 and 0.151.0) for
    # every workspace-write run.
    # The expected token is a LITERAL here on purpose: deriving it from the constant the
    # code reads would make this test unable to catch a wrong constant.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox=sandbox,
        isolation=isolation,
        output_last_message_path=str(tmp_path / "l"),
        flag_support=_ALL_FLAGS,
    )
    pin = "sandbox_workspace_write.network_access=false"
    pairs = [i for i in range(len(cmd) - 1) if cmd[i] == "-c" and cmd[i + 1] == pin]
    if sandbox == "workspace-write":
        # Exactly one adjacent `-c <pin>` pair: absent breaks the guarantee, duplicated or
        # mis-paired tokens would corrupt the argv.
        assert len(pairs) == 1
    else:
        assert pin not in cmd


def test_build_exec_command_network_pin_ordering_with_other_config_tokens(tmp_path):
    # #518: the pin is plugin-owned — emitted before operator extra_args — and must not
    # displace or mis-pair the reasoning-effort `-c` token that shares the same flag.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="workspace-write",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        reasoning_effort="high",
        extra_args=("-c", "model_provider=x"),
        flag_support=_ALL_FLAGS,
    )
    pin = "sandbox_workspace_write.network_access=false"
    assert cmd[cmd.index(pin) - 1] == "-c"
    effort = 'model_reasoning_effort="high"'
    assert cmd[cmd.index(effort) - 1] == "-c"
    assert cmd.index(pin) < cmd.index("model_provider=x")
    assert cmd[-1] == cli_contract.STDIN_PROMPT


def test_network_pin_key_constant_matches_codex_config_key():
    # The cli_contract constant is the single source the builder reads; pin its VALUE
    # here so a typo'd constant fails loudly instead of drifting into a silent no-op
    # (codex ignores unknown -c keys).
    assert (
        cli_contract.WORKSPACE_WRITE_NETWORK_ACCESS_CONFIG_KEY
        == "sandbox_workspace_write.network_access"
    )


@pytest.mark.parametrize("isolation", config.VALID_ISOLATIONS)
@pytest.mark.parametrize("sandbox", cli_contract.VALID_SANDBOXES)
def test_build_exec_command_pins_writable_roots_exactly_on_workspace_write(
    tmp_path, sandbox, isolation
):
    # #520: the filesystem sibling of the #518 network pin. At the default isolation
    # (inherit) codex reads $CODEX_HOME/config.toml, where `[sandbox_workspace_write]
    # writable_roots = [...]` would silently widen the delegate sandbox to write outside
    # the workspace, voiding the advertised writes-stay-in-the-workspace boundary. The
    # pin restores codex's own default ([]) and closes the config-file and --profile
    # channels (the `-c` override outranks both, verified live on 0.148.0 and re-verified
    # on 0.149.1 and 0.151.0) for every workspace-write run. The expected token is a LITERAL here on
    # purpose: deriving it
    # from the constant the code reads would make this test unable to catch a wrong
    # constant.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox=sandbox,
        isolation=isolation,
        output_last_message_path=str(tmp_path / "l"),
        flag_support=_ALL_FLAGS,
    )
    pin = "sandbox_workspace_write.writable_roots=[]"
    pairs = [i for i in range(len(cmd) - 1) if cmd[i] == "-c" and cmd[i + 1] == pin]
    if sandbox == "workspace-write":
        # Exactly one adjacent `-c <pin>` pair: absent breaks the guarantee, duplicated or
        # mis-paired tokens would corrupt the argv.
        assert len(pairs) == 1
    else:
        assert pin not in cmd


def test_build_exec_command_writable_roots_pin_ordering_with_other_config_tokens(tmp_path):
    # #520: the pin is plugin-owned — emitted before operator extra_args — and must not
    # displace or mis-pair the network pin or the reasoning-effort `-c` token that share
    # the same flag.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="workspace-write",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        reasoning_effort="high",
        extra_args=("-c", "model_provider=x"),
        flag_support=_ALL_FLAGS,
    )
    pin = "sandbox_workspace_write.writable_roots=[]"
    assert cmd[cmd.index(pin) - 1] == "-c"
    net_pin = "sandbox_workspace_write.network_access=false"
    assert cmd[cmd.index(net_pin) - 1] == "-c"
    effort = 'model_reasoning_effort="high"'
    assert cmd[cmd.index(effort) - 1] == "-c"
    assert cmd.index(pin) < cmd.index("model_provider=x")
    assert cmd[-1] == cli_contract.STDIN_PROMPT


def test_writable_roots_pin_key_constant_matches_codex_config_key():
    # The cli_contract constant is the single source the builder reads; pin its VALUE
    # here so a typo'd constant fails loudly instead of drifting into a silent no-op
    # (codex ignores unknown -c keys).
    assert (
        cli_contract.WORKSPACE_WRITE_WRITABLE_ROOTS_CONFIG_KEY
        == "sandbox_workspace_write.writable_roots"
    )


def test_build_exec_command_add_dir_composes_with_writable_roots_pin(tmp_path):
    # #520: --add-dir grants ride the FLAG layer, which outranks the `-c` config-layer
    # pin (verified live on 0.148.0 and re-verified on 0.149.1 and 0.151.0) — so a future add_dirs
    # caller widens the sandbox
    # DESPITE the pin. Both tokens coexisting in the argv is the documented behavior;
    # adopting add_dirs on a model-bearing path is a contract change needing its own
    # surface review (see the builder's add_dirs note).
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="workspace-write",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        add_dirs=("/extra",),
        flag_support=_ALL_FLAGS,
    )
    assert cmd[cmd.index("--add-dir") + 1] == "/extra"
    assert "sandbox_workspace_write.writable_roots=[]" in cmd


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


# --- classify_failure(sanitize=...): worktree-path-safe delegate errors (#420) ------
#
# `sanitize`, when given, REPLACES the `nonzero_exit` branch's `redact_text` call (the
# sanitizer already includes redaction). Omitted, behavior must be byte-identical to
# before this parameter existed.


def test_classify_failure_sanitize_none_is_byte_identical():
    """Pin: `sanitize=None` (the default, and omitting the kwarg entirely) must match
    today's redact_text-only behavior exactly."""
    secret = "sk-" + "a" * 32
    run = CommandRun("", f"auth failed token={secret}", 1, 1, False)
    omitted = codex.classify_failure(run)
    explicit_none = codex.classify_failure(run, sanitize=None)
    assert omitted.message == explicit_none.message
    assert "[redacted: secret value]" in explicit_none.message


def test_classify_failure_sanitize_none_leaks_worktree_path_positive_control(tmp_path):
    """Positive control: without a `sanitize` callable, a worktree path in the raw
    diagnostic DOES leak into the message — proves the assertions below are not vacuous."""
    wt = str(tmp_path / "cic-worktree-p" / "tree")
    run = CommandRun("", f"error at {wt}/f.py", 1, 1, False)
    err = codex.classify_failure(run)
    assert wt in err.message


def test_classify_failure_sanitize_relativizes_and_redacts(tmp_path):
    """The classify_failure path test (#420): a delegate-error stderr embedding the
    worktree path AND its file:// alias comes back relativized, with no absolute path,
    when a sanitize callable is supplied. RED before the `sanitize` parameter exists."""
    wt = str(tmp_path / "cic-worktree-x" / "tree")
    aliases = worktree.path_aliases(wt)
    stderr = f"error at {wt}/f.py (also file://{wt}/f.py)"
    run = CommandRun("", stderr, 1, 1, False)
    err = codex.classify_failure(run, sanitize=lambda t: worktree.sanitize_prose(t, aliases) or "")
    assert wt not in err.message
    assert os.path.realpath(wt) not in err.message
    assert "./f.py" in err.message


def test_classify_failure_sanitize_survives_partial_alias_consumption(tmp_path):
    """Ordering attack A: naive redact-then-relativize lets the redactor consume PART of
    the file:// alias, leaving an un-relativizable dead-path remainder. sanitize_prose
    stages the alias first, so it never fragments."""
    wt = str(tmp_path / "cic-worktree-c" / "tree")
    aliases = worktree.path_aliases(wt)
    stderr = f"api_key={'A' * 16}=file://{wt}/abcdefgh"
    run = CommandRun("", stderr, 1, 1, False)
    err = codex.classify_failure(run, sanitize=lambda t: worktree.sanitize_prose(t, aliases) or "")
    assert "abcdefgh" not in err.message
    assert wt not in err.message
    assert "cic-worktree-" not in err.message


def test_classify_failure_sanitize_redacts_short_path_bearing_secret(tmp_path):
    """Ordering attack B: naive relativize-then-redact shortens
    `api_key=<root>/abcdefgh` below the redactor's 16-char floor, letting the secret
    escape. sanitize_prose defeats this by redacting the staged (still-long) value."""
    wt = str(tmp_path / "cic-worktree-s" / "tree")
    aliases = worktree.path_aliases(wt)
    stderr = f"api_key={wt}/abcdefgh"
    run = CommandRun("", stderr, 1, 1, False)
    err = codex.classify_failure(run, sanitize=lambda t: worktree.sanitize_prose(t, aliases) or "")
    assert "abcdefgh" not in err.message
    assert "[redacted: secret value]" in err.message


def test_classify_failure_sanitize_replaces_sentence_final_root_with_marker(tmp_path):
    """#420 review round 3: a raw diagnostic ending in a bare worktree root plus a period
    (`fatal: failed in <wt>.`, a common git-stderr shape) must not leak the absolute path
    through classify_failure's sanitize path — this was `sanitize_prose`'s own
    ambiguous-period carve-out leaking, not something specific to classify_failure."""
    wt = str(tmp_path / "cic-worktree-m" / "tree")
    aliases = worktree.path_aliases(wt)
    stderr = f"fatal: failed in {wt}."
    run = CommandRun("", stderr, 1, 1, False)
    err = codex.classify_failure(run, sanitize=lambda t: worktree.sanitize_prose(t, aliases) or "")
    assert wt not in err.message
    assert "cic-worktree-" not in err.message


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
        lambda cmd, timeout_seconds: CommandRun("codex-cli 0.151.0\n", "", 0, 1, False),
    )
    assert codex.codex_version() == "codex-cli 0.151.0"


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


def test_codex_version_returns_none_when_override_binary_missing(clean_env, tmp_path):
    """A bad `CODEX_IN_CLAUDE_CODEX_BIN` override must not escape past this
    function's documented "returns None on failure" contract as
    `binpath.BinaryNotFoundError` -- the same bug already fixed for
    `preflight._probe_help()` / `appserver.transfer_session()` /
    `appserver.read_rate_limits()` in round 1 (commit 53c3b27)."""
    missing = tmp_path / "does-not-exist"
    clean_env.setenv(binpath.ENV_VAR, str(missing))
    try:
        result = codex.codex_version()
    except binpath.BinaryNotFoundError as exc:
        pytest.fail(f"codex_version() must return None, not raise {exc!r}")
    assert result is None


def test_login_status_returns_none_none_when_override_binary_missing(clean_env, tmp_path):
    """Same escaping bug as `codex_version()` above, for `login_status()`'s
    documented `(None, None)` failure return."""
    missing = tmp_path / "does-not-exist"
    clean_env.setenv(binpath.ENV_VAR, str(missing))
    try:
        result = codex.login_status()
    except binpath.BinaryNotFoundError as exc:
        pytest.fail(f"login_status() must return (None, None), not raise {exc!r}")
    assert result == (None, None)


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
        cmd,
        *,
        cwd,
        timeout_seconds,
        stdin_text,
        env=None,
        on_stdout_line=None,
        max_output_bytes=None,
    ):
        # Emulate codex writing the final message to --output-last-message.
        out_path = cmd[cmd.index("--output-last-message") + 1]
        from pathlib import Path

        Path(out_path).write_text(
            '{"summary":"hi","verdict":"pass","confidence":"high","findings":[]}'
        )
        return CommandRun('{"type":"token_count","usage":{"input_tokens":3}}\n', "", 0, 7, False)

    monkeypatch.setattr(codex.runtime, "run_async", fake_run_async)
    monkeypatch.setattr(codex.preflight, "flag_support", lambda force=False: _ALL_FLAGS)
    result = await codex.run_codex_exec(
        "q",
        kind="review_changes",
        cwd=str(tmp_path),
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=30,
        output_schema={"type": "object"},
    )
    assert result.run.exit_code == 0
    assert "summary" in (result.last_message or "")


def test_run_codex_exec_forwards_on_event(monkeypatch):
    captured = {}

    async def fake_run_async(
        cmd,
        *,
        cwd,
        timeout_seconds,
        stdin_text=None,
        env=None,
        on_stdout_line=None,
        max_output_bytes=None,
    ):
        captured["on_stdout_line"] = on_stdout_line
        from pontonier.core.runtime import CommandRun

        return CommandRun("", "", 0, 1, False)

    monkeypatch.setattr(codex.runtime, "run_async", fake_run_async)
    monkeypatch.setattr(codex.preflight, "flag_support", lambda force=False: _ALL_FLAGS)
    sentinel = lambda _l: None  # noqa: E731
    anyio.run(
        lambda: codex.run_codex_exec(
            "p",
            kind="consult",
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


def test_classify_composite_marker_descriptor_attributes_to_extra_args(monkeypatch):
    # Maintainer-review regression (#313): a passthrough descriptor that ITSELF
    # carries the full bracketed marker signature (a profile literally named
    # "[reasoning.effort][ReasoningEffortParam]" — the allowlist constrains flags,
    # not name characters) would otherwise impersonate the backend rejection: codex
    # quotes the name in its error, and that quoted text alone satisfies
    # is_reasoning_effort_rejection. When the matched descriptors account for the
    # signature, the failure is the operator's entry, even with an effort sent.
    name = "[reasoning.effort][ReasoningEffortParam]"
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", f"--profile '{name}'")
    ea = config.extra_args()
    assert ea.valid, ea.error  # the composite name passes the passthrough allowlist
    err = codex.classify_failure(
        CommandRun("", f"error: invalid value '{name}' for '--profile'", 2, 1, False),
        extra_args=ea,
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


async def test_run_codex_exec_surfaces_help_gate_drops_from_the_adapter(monkeypatch, tmp_path):
    """The dropped-flags channel now rides PreparedRun.dropped_flags. A wiring bug
    (e.g. always-empty) would silently kill compat warnings and model-provenance
    reconciliation, so a real gated drop is pinned end to end."""

    async def fake_run_async(
        cmd,
        *,
        cwd,
        timeout_seconds,
        stdin_text=None,
        env=None,
        on_stdout_line=None,
        max_output_bytes=None,
    ):
        assert "--model" not in cmd  # the gate really dropped it from argv
        return CommandRun("", "", 0, 1, False)

    monkeypatch.setattr(codex.runtime, "run_async", fake_run_async)
    monkeypatch.setattr(codex.preflight, "flag_support", lambda force=False: _NO_MODEL)
    result = await codex.run_codex_exec(
        "q",
        kind="consult",
        cwd=str(tmp_path),
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=10,
        model="gpt-5.6-sol",
    )
    assert result.dropped_flags == ["--model"]


# --- strict-config emission (#524) -------------------------------------------------
def _strict_count(cmd: list[str]) -> int:
    return cmd.count(cli_contract.STRICT_CONFIG_FLAG)


def _dash_c_keys(cmd: list[str]) -> list[str]:
    """The KEY half of every `-c KEY=VALUE` pair in a built argv."""
    return [
        cmd[i + 1].split("=", 1)[0] for i in range(len(cmd) - 1) if cmd[i] in ("-c", "--config")
    ]


@pytest.mark.parametrize("isolation", config.VALID_ISOLATIONS)
@pytest.mark.parametrize("sandbox", cli_contract.VALID_SANDBOXES)
@pytest.mark.parametrize("effort", [None, "high"])
@pytest.mark.parametrize("extra", [(), ("-c", "model_provider=x"), ("--profile", "p")])
def test_strict_config_emitted_exactly_when_a_config_override_rides(
    tmp_path, sandbox, isolation, effort, extra
):
    # #524: the flag guards the plugin's guarantee-bearing `-c` KEY pins, so it rides
    # exactly the runs that CARRY a `-c` override — and never an override-free run, where
    # it would only expose the user's own config to a hard failure for no guarantee.
    # The expectation is derived from the BUILT argv (does any `-c` pair exist?), not from
    # a restatement of the emission condition, so a builder that emits the wrong set fails.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox=sandbox,
        isolation=isolation,
        output_last_message_path=str(tmp_path / "l"),
        reasoning_effort=effort,
        extra_args=extra,
        flag_support=_ALL_FLAGS,
    )
    assert _strict_count(cmd) == (1 if _dash_c_keys(cmd) else 0)


def test_strict_config_rides_the_workspace_write_pins(tmp_path):
    # The delegate tier always carries the two #518/#520 pins, so it always gets the guard.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="workspace-write",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        flag_support=_ALL_FLAGS,
    )
    assert _strict_count(cmd) == 1
    assert set(_dash_c_keys(cmd)) == {
        "sandbox_workspace_write.network_access",
        "sandbox_workspace_write.writable_roots",
    }


def test_strict_config_absent_on_the_default_consult_run(tmp_path):
    # The common read-only consult carries no `-c` at all: no pin to guard, so no new
    # failure mode for an unrelated unknown key in the user's config.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        flag_support=_ALL_FLAGS,
    )
    assert _dash_c_keys(cmd) == []
    assert cli_contract.STRICT_CONFIG_FLAG not in cmd


def test_strict_config_rides_an_operator_only_config_override(tmp_path):
    # An operator `-c` is the only override on this run; the key is still validated, so a
    # typo'd operator key fails loudly instead of being silently ignored.
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="ignore-config",
        output_last_message_path=str(tmp_path / "l"),
        extra_args=("--config", "model_provider=x"),
        flag_support=_ALL_FLAGS,
    )
    assert _strict_count(cmd) == 1


def test_strict_config_is_plugin_owned_and_precedes_extra_args(tmp_path):
    # Plugin-owned tokens come before operator passthrough, and the flag is never gated
    # away by the --help parse (ALWAYS_SEND).
    cmd, dropped = codex.build_exec_command(
        cwd="/repo",
        sandbox="workspace-write",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        extra_args=("-c", "model_provider=x"),
        flag_support=_NO_MODEL,
    )
    assert cmd.index(cli_contract.STRICT_CONFIG_FLAG) < cmd.index("model_provider=x")
    assert cli_contract.STRICT_CONFIG_FLAG not in dropped
    assert cmd[-1] == cli_contract.STDIN_PROMPT


# --- strict-config failure classification (#524) -----------------------------------
def _override_stderr(key: str) -> str:
    return (
        f"Error loading config.toml: unknown configuration field `{key}` in -c/--config override\n"
    )


def _file_stderr(key: str, path: str = "/home/u/.codex/config.toml", line: int = 1) -> str:
    return (
        "Error loading config.toml:\n"
        f"{path}:{line}:1: unknown configuration field `{key}`\n"
        "  |\n"
        f"{line} | {key} = true\n"
    )


def _run(stderr: str = "", stdout: str = "", exit_code: int = 1) -> CommandRun:
    return CommandRun(stdout, stderr, exit_code, 1, False)


@pytest.mark.parametrize("key", sorted(cli_contract.PLUGIN_OWNED_CONFIG_KEYS))
def test_strict_rejection_of_a_plugin_pin_is_contract_drift(key, monkeypatch):
    # THE point of #524: a silent upstream rename of a guarantee-bearing pin key becomes a
    # loud, zero-spend cli_contract_changed instead of a quietly reopened guarantee.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    err = codex.classify_failure(_run(stderr=_override_stderr(key)))
    assert err.code == "cli_contract_changed"


@pytest.mark.parametrize("key", sorted(cli_contract.PLUGIN_OWNED_CONFIG_KEYS))
def test_plugin_pin_drift_is_not_stolen_by_an_operator_config_passthrough(key, monkeypatch):
    # The misattribution hazard: every operator `-c` entry records the SHARED `-c`
    # descriptor, which appears in codex's own rejection text ("in -c/--config override").
    # Attribution keys on the rejected KEY, so an unrelated operator entry cannot claim a
    # plugin pin's drift and send the user to fix the wrong configuration.
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "-c model_provider=x")
    err = codex.classify_failure(_run(stderr=_override_stderr(key)))
    assert err.code == "cli_contract_changed"


def test_strict_rejection_of_an_operator_key_is_attributed_to_the_operator(monkeypatch):
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "-c model_provider=x")
    err = codex.classify_failure(_run(stderr=_override_stderr("model_provider")))
    assert err.code == "extra_args_rejected"
    assert "model_provider" in (err.message or "")


def test_strict_rejection_of_an_unattributable_override_stays_fail_loud(monkeypatch):
    # Neither a known plugin pin nor an operator key: report drift rather than guess.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    err = codex.classify_failure(_run(stderr=_override_stderr("some.mystery.key")))
    assert err.code == "cli_contract_changed"


def test_strict_rejection_in_the_user_config_file_is_user_config_rejected(monkeypatch):
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    err = codex.classify_failure(_run(stderr=_file_stderr("junk_key", line=7)))
    assert err.code == "user_config_rejected"
    assert err.temporary is False
    assert err.repair is not None
    assert err.repair.next_step == "correct_config"
    # The message must locate the offending key — that is the whole actionable content.
    assert "junk_key" in (err.message or "")
    assert "/home/u/.codex/config.toml" in (err.message or "")
    assert ":7" in (err.message or "")


def test_user_config_rejection_names_a_profile_table_key(monkeypatch):
    # Verified live: an unknown key in an UNSELECTED [profiles.X] table fails the run too,
    # and is reported with its full dotted path.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    err = codex.classify_failure(_run(stderr=_file_stderr("profiles.myprof.junk_key", line=2)))
    assert err.code == "user_config_rejected"
    assert "profiles.myprof.junk_key" in (err.message or "")


def test_strict_rejection_in_a_selected_operator_profile_file_is_the_operators(monkeypatch):
    # `--profile NAME` (an operator passthrough) makes codex load and validate
    # $CODEX_HOME/NAME.config.toml, so a rejection there is operator config, not the
    # user's own file.
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "--profile myprof")
    err = codex.classify_failure(
        _run(stderr=_file_stderr("junk_key", path="/home/u/.codex/myprof.config.toml"))
    )
    assert err.code == "extra_args_rejected"


def test_strict_rejection_beats_the_auth_check(monkeypatch):
    # Config parsing precedes auth, so this failure is never an auth problem — but the
    # echoed key and path can carry AUTH_FAILURE_PATTERNS substrings. Ordering protects it.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    stderr = _file_stderr("unauthorized_key", path="/home/401/.codex/config.toml")
    assert cli_contract.is_auth_failure(stderr) is True  # positive control
    err = codex.classify_failure(_run(stderr=stderr))
    assert err.code == "user_config_rejected"


def test_strict_rejection_beats_a_drift_phrase_inside_the_rejected_key(monkeypatch):
    # A TOML quoted key may contain a CONTRACT_DRIFT_STDERR_PATTERNS phrase; the anchored
    # strict grammar classifies it as the user's config, not as a plugin-flag drift.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    stderr = _file_stderr("invalid value")
    assert cli_contract.is_contract_drift(stderr) is True  # positive control
    err = codex.classify_failure(_run(stderr=stderr))
    assert err.code == "user_config_rejected"


def test_strict_rejection_is_read_from_stderr_only(monkeypatch):
    # The failure is stderr-only and pre-model (verified: stdout is empty). Restricting
    # recognition to stderr keeps model-produced text — a last message or an event blob
    # quoting this grammar — from manufacturing the classification.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    text = _file_stderr("junk_key")
    assert codex.classify_failure(_run(stdout=text)).code != "user_config_rejected"
    assert codex.classify_failure(_run(), last_message=text).code != "user_config_rejected"
    # Positive control: the identical text on STDERR does classify, so the negatives above
    # measure the stream restriction rather than a recognizer that matches nothing.
    assert codex.classify_failure(_run(stderr=text)).code == "user_config_rejected"


def test_user_config_rejection_redacts_a_secret_in_the_echoed_path(monkeypatch):
    # The path is untrusted echoed text that reaches an envelope; it goes through the same
    # redaction every other surfaced failure text does.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    secret = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    stderr = _file_stderr("junk_key", path=f"/tmp/{secret}/c.toml")
    err = codex.classify_failure(_run(stderr=stderr))
    assert err.code == "user_config_rejected"
    assert secret not in (err.message or "")


def test_operator_passthrough_can_never_own_a_plugin_pinned_key(monkeypatch):
    """The coupling that makes override-form attribution safe (#524).

    `_strict_config_error` checks the rejected key against PLUGIN_OWNED_CONFIG_KEYS
    before asking whether the operator owns it. That check is currently REDUNDANT — the
    extra-args parser already refuses all three keys (`sandbox` root denial, reserved
    meta keys), so `owns_config_key` cannot return True for any of them — and a mutation
    removing it passes every other test here. The redundancy is the point: this test pins
    the invariant the redundancy rests on, in the module that depends on it, so narrowing
    that denylist fails HERE instead of silently misattributing a guarantee-bearing pin's
    drift to the operator.
    """
    for key in sorted(cli_contract.PLUGIN_OWNED_CONFIG_KEYS):
        monkeypatch.setenv(config.EXTRA_ARGS_ENV, f"-c {key}=x")
        ea = config.extra_args()
        assert ea.valid is False, f"{key} must be refused by the extra-args denylist"
        assert ea.owns_config_key(key) is False
    # Positive control: an ordinary key IS accepted and owned, so the assertions above
    # measure the denial rather than a parser that rejects everything.
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "-c model_provider=x")
    control = config.extra_args()
    assert control.valid is True
    assert control.owns_config_key("model_provider") is True


def test_strict_config_not_emitted_for_a_flag_shaped_option_value(tmp_path):
    """A VALUE that looks like `-c` must not trigger the guard (#524).

    `model` accepts any string, so `model="-c"` puts that token in the argv as the
    --model VALUE while no config override rides. A membership test over the token list
    cannot tell a value from an option, and would arm strict validation — exposing the
    run to an unrelated user-config rejection the documented scope promises it is free of.
    """
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        model="-c",
        flag_support=_ALL_FLAGS,
    )
    assert cmd[cmd.index("--model") + 1] == "-c"  # the value really is in the argv
    # No real override rides: none of the pinned keys was appended. (Deliberately not
    # `_dash_c_keys`, which is the same naive membership scan this test exists to reject —
    # it reads the --model VALUE as a flag and reports a phantom key.)
    assert not [
        t for t in cmd if any(t.startswith(f"{k}=") for k in cli_contract.PLUGIN_OWNED_CONFIG_KEYS)
    ]
    assert cli_contract.STRICT_CONFIG_FLAG not in cmd
    # The same for --config-shaped and for the extra-args side.
    cmd2, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        model="--config",
        extra_args=("--profile", "myprof"),
        flag_support=_ALL_FLAGS,
    )
    assert cli_contract.STRICT_CONFIG_FLAG not in cmd2


def test_user_config_rejection_strips_control_characters(monkeypatch):
    """Echoed key/path text is untrusted and must not carry control sequences (#524).

    codex read both off disk — a TOML quoted key and a `$CODEX_HOME` path can hold
    arbitrary bytes — and the message travels to an agent and a terminal, where an escape
    sequence can corrupt or spoof rendering. Secret redaction is best-effort and does not
    address control characters at all, so they are stripped independently.
    """
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    stderr = (
        "Error loading config.toml:\n"
        "/home/u/\x1b[31mBOOM\x1b[0m/config.toml:4:1: unknown configuration field `k\x07ey`\n"
    )
    err = codex.classify_failure(_run(stderr=stderr))
    assert err.code == "user_config_rejected"
    message = err.message or ""
    assert not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in message), repr(message)
    # The actionable content survives the stripping: the line number and the printable
    # remainder of the key are still there.
    assert ":4" in message
    assert "key" in message


@pytest.mark.parametrize(
    "attack",
    [
        "sk-\x01ant-api03-" + "A" * 40,  # control char breaks the redactor's prefix anchor
        "s\x01k-ant-api03-" + "A" * 40,
        "sk-ant\x7f-api03-" + "A" * 40,  # DEL, not just C0
    ],
)
def test_safe_echo_strips_control_characters_before_redacting(attack):
    """Control-char stripping must run BEFORE redaction, never after (#524).

    A secret with an embedded control character does not match the redactor's pattern,
    so redacting first leaves it intact — and stripping afterwards then REASSEMBLES the
    contiguous secret in the outgoing message. Stripping first joins the fragments while
    the redactor can still see them; it cannot split anything, so this order has no
    mirror-image failure. Verified against the real redactor, which reconstituted the
    full key in the old order.
    """
    secret = "sk-ant-api03-" + "A" * 40
    out = codex._safe_echo(attack)
    assert secret not in out, out
    assert not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in out)
    # Positive control: the redactor really does catch this secret once contiguous, so
    # the assertion above measures the ordering rather than a matcher that never fires.
    assert secret not in (redaction.redact_text(secret) or "")


def test_user_config_rejection_does_not_leak_a_control_split_secret(monkeypatch):
    """The same ordering defect, end to end through the classifier."""
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    secret = "sk-ant-api03-" + "A" * 40
    stderr = _file_stderr("junk_key", path=f"/tmp/sk-\x01ant-api03-{'A' * 40}/config.toml")
    err = codex.classify_failure(_run(stderr=stderr))
    assert err.code == "user_config_rejected"
    assert secret not in (err.message or "")


# --- #528: every echoed span goes through the shared echo sanitizer ------------------
#
# #524/#527 applied strip-then-redact to the two spans a --strict-config rejection
# carries. Every OTHER path that echoes foreign text into an envelope had the same gap:
# escape sequences reached the agent (and often a terminal, where they can recolor,
# reposition, or erase), and a control character wedged into a secret defeated redaction
# so the value rode out as plaintext. The ordering now lives upstream in
# pontonier.core.redaction, so no call site re-derives it.

_ECHO_ATTACKS = ["\x1b[31mRED\x1b[0m", "bell\x07", "wipe\x1b[2K", "del\x7f", "c1\x85"]


def _has_control(text: str) -> bool:
    return any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in text)


@pytest.mark.parametrize("attack", _ECHO_ATTACKS)
def test_nonzero_exit_strips_control_characters_from_echoed_stderr(attack, monkeypatch):
    """The generic branch echoed codex's stderr with no control-character stripping."""
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    # Embedded mid-string, not trailing: `str.strip()` already eats a TRAILING NEL, so a
    # trailing attack would pass without any of this code running.
    err = codex.classify_failure(_run(stderr=f"boom {attack} and more"))
    assert err.code == "nonzero_exit"
    assert not _has_control(err.message or ""), repr(err.message)


def test_nonzero_exit_redacts_a_control_split_secret(monkeypatch):
    """The interaction half: a secret split by a control character matches no pattern, so
    it survived redaction as plaintext. Stripping first rejoins it where the matcher can
    still see it."""
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    secret = "sk-ant-api03-" + "A" * 40
    err = codex.classify_failure(_run(stderr="sk-\x01ant-api03-" + "A" * 40))
    message = err.message or ""
    # Assert the VALUE is gone, not that the contiguous spelling is absent — the attacked
    # text never contains that spelling, so `secret not in message` passes vacuously.
    assert "A" * 40 not in message, repr(message)
    assert secret not in message
    # Positive control: the redactor really does catch this once contiguous.
    assert secret not in (redaction.redact_text(secret) or "")


@pytest.mark.parametrize("attack", _ECHO_ATTACKS)
def test_extra_args_rejected_strips_control_characters_from_descriptors(attack, monkeypatch):
    """Descriptors reach BOTH error.message and repair.alternative, and neither was
    sanitized: `_safe_token` covers only the unsupported-argument path, while a VALID
    config key or profile name is recorded raw (#528)."""
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, f"--profile ev{attack}il")
    err = codex.classify_failure(
        _run(stderr=f"error: unexpected argument '--profile ev{attack}il' found")
    )
    assert err.code == "extra_args_rejected"
    assert not _has_control(err.message or ""), repr(err.message)
    assert not _has_control((err.repair.alternative if err.repair else "") or "")


@pytest.mark.parametrize("name", ["ev\x07il", "p" * 80])
def test_a_control_bearing_or_long_descriptor_is_still_attributed_to_the_operator(
    name, monkeypatch
):
    """Regression: sanitizing descriptors at CONSTRUCTION broke attribution.

    A descriptor is matched against codex's rejection text, which quotes the operator's
    name with its raw spelling. Stripping it (or bounding it to 60 chars) made the match
    fail, so the operator's own bad passthrough came back as `cli_contract_changed` — the
    fail-loud plugin-drift path, with repair guidance pointing at the wrong thing.

    The rejection here names ONLY the descriptor, with no `--profile` token, so a match
    can only come from the name itself; including the static flag would let this pass
    without proving anything about the controlled name.
    """
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, f"--profile {name}")
    err = codex.classify_failure(_run(stderr=f"error: unexpected argument '{name}' found"))
    assert err.code == "extra_args_rejected"
    assert not _has_control(err.message or ""), repr(err.message)


# --- version_display: bounded, sanitized DISPLAY copy of `codex --version` (#531) ------
# `codex --version` stdout was echoed into StatusResult.codex_version with no bound and no
# sanitation. The identity/display split is the point: `codex_version()` keeps returning the
# RAW string (config.parse_version reads it), and only the emitted copy is sanitized.


def test_version_display_passes_an_ordinary_version_through():
    assert codex.version_display("codex-cli 0.151.0") == "codex-cli 0.151.0"


@pytest.mark.parametrize("value", [None, ""])
def test_version_display_absent_input_stays_none(value):
    assert codex.version_display(value) is None


def test_version_display_deletes_control_characters():
    """RED before #531: the raw ESC sequences rode into the envelope verbatim."""
    out = codex.version_display("codex-cli 9.9.9\x1b[2K\x1b[31m rogue")
    assert out is not None
    assert not _has_control(out), repr(out)
    assert "\x1b" not in out


def test_version_display_of_nothing_but_control_characters_is_none():
    """An all-Cc version sanitizes to "", which must read as ABSENT rather than as the
    empty string — `codex_found` is decided from the raw value, not from this copy."""
    assert codex.version_display("\x1b\x07\x00") is None


def test_version_display_bounds_a_long_version_with_an_explicit_marker():
    """RED before #531: a 5010-char version came back at full length.

    The marker is reserved INSIDE the cap, so a clipped value can never be mistaken for a
    complete one ([8.truncation] in the agent-friendly-mcp contract checklist)."""
    out = codex.version_display("codex-cli " + "A" * 5000)
    assert out is not None
    assert len(out) == codex._ECHO_MAX_CHARS
    assert out.endswith(codex._ECHO_TRUNC_MARKER)


@pytest.mark.parametrize(
    ("length", "clipped"),
    [
        (codex._ECHO_MAX_CHARS - 1, False),
        (codex._ECHO_MAX_CHARS, False),
        (codex._ECHO_MAX_CHARS + 1, True),
    ],
)
def test_version_display_boundary_is_at_the_cap_not_around_it(length, clipped):
    """The cap's own boundary: at or below it nothing is touched; one character past it
    truncates. A silent off-by-one here would clip a complete value or emit an over-cap one."""
    out = codex.version_display("v" * length)
    assert out is not None
    assert (out.endswith(codex._ECHO_TRUNC_MARKER)) is clipped
    assert len(out) == (codex._ECHO_MAX_CHARS if clipped else length)


# The retained-text cut, once the marker's own length is reserved out of the budget. The
# secret below is positioned against THIS number, not against the budget: a fixture that
# starts the secret past the cut is dropped whole by a truncate-first implementation, so the
# assertion passes against the very ordering it exists to forbid (caught in review).
_RETAINED = codex._ECHO_MAX_CHARS - len(codex._ECHO_TRUNC_MARKER)
# Short enough that the redactor does NOT match it on its own (it needs ~27 characters of
# this value), so the fragment a truncate-first cut would retain rides out in plaintext.
_STRADDLE = 20


def test_version_display_redacts_a_secret_straddling_the_truncation_cut():
    """Sanitation runs BEFORE the bound, so a secret whose pattern needs its tail is
    redacted while the redactor can still see it.

    The secret STRADDLES the cut: its labelled head sits inside the retained text and its
    value runs past it. Truncating first keeps that head as an unredactable fragment and
    publishes `sk-ant-api03-...` verbatim — verified by mutating `_safe_echo` to truncate
    first and watching this assertion fail."""
    secret = "sk-ant-api03-" + "x" * 95
    filler = "p" * (_RETAINED - _STRADDLE)
    out = codex.version_display(filler + secret)
    assert out is not None
    assert len(out) == codex._ECHO_MAX_CHARS  # the input is over-cap, so the cut happened
    assert "sk-ant-api03-" not in out


def test_a_literal_truncation_marker_in_the_input_is_not_a_truncation_claim():
    """The marker's guarantee is ONE-WAY, and this pins that reading (review finding).

    Real truncation is always marked; marked text does not prove truncation, because an
    under-cap value that literally ends in the marker is passed through untouched. Nothing
    in this package branches on the marker — it is text for a reader, never a
    machine-readable `truncated` flag — so a spoofed suffix misleads a reader and nothing
    more. `StatusResult` describes it as advisory for that reason."""
    spoofed = "codex-cli 0.151.0" + codex._ECHO_TRUNC_MARKER
    assert len(spoofed) <= codex._ECHO_MAX_CHARS
    assert codex.version_display(spoofed) == spoofed


def test_version_display_never_repairs_a_control_split_version_for_the_verdict():
    """The display copy is LOSSY and is not the identity.

    Deleting the control character out of `0.<BEL>151.0` yields a plausible
    `codex-cli 0.151.0` — but `version_supported` must keep parsing the RAW probe output,
    which does not parse at all. Pinning both halves keeps a future refactor from routing
    the verdict through this copy."""
    raw = "codex-cli 0.\x07151.0"
    assert codex.version_display(raw) == "codex-cli 0.151.0"
    assert config.parse_version(raw) is None
    assert config.version_supported(raw) is None


def test_safe_echo_bounds_an_over_cap_span_with_the_explicit_marker():
    """#531 applies the same rule to every echoed span: a clipped config key or flag name
    must not read as a complete one either."""
    out = codex._safe_echo("k" * 5000)
    assert len(out) == codex._ECHO_MAX_CHARS
    assert out.endswith(codex._ECHO_TRUNC_MARKER)


# --- retired config SETTING classification (codex 0.149, #542) --------------------
# Captured verbatim from codex-cli 0.149.1 (2026-08-25); the same wording re-observed on
# 0.151.0 (2026-08-29). 0.148.0 accepted the same
# config, so a user upgrading hits this on their FIRST run at the default isolation.
_RETIRED_SETTING_STDERR = (
    'Error: approval_policy = "untrusted" is no longer supported; remove this setting\n'
)


def test_retired_config_setting_is_user_config_rejected(monkeypatch):
    # Before #542 this matched no signature and fell through to a generic nonzero_exit,
    # losing the diagnosis. It is the user's own config, so it repairs like one.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    err = codex.classify_failure(_run(stderr=_RETIRED_SETTING_STDERR))
    assert err.code == "user_config_rejected"
    assert err.temporary is False
    assert err.repair is not None
    assert err.repair.next_step == "correct_config"
    # The key and the retired value are the entire actionable content.
    assert "approval_policy" in (err.message or "")
    assert "untrusted" in (err.message or "")


def test_retired_config_setting_is_read_from_stderr_only(monkeypatch):
    # Same reason as the strict grammar: recognition must not be manufacturable by
    # model-produced text on stdout, in a last message, or in an event blob.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    text = _RETIRED_SETTING_STDERR
    # Positive control: the identical text on STDERR *is* recognized, so each negative
    # below is about the channel and not about the grammar failing to match.
    assert codex.classify_failure(_run(stderr=text)).code == "user_config_rejected"
    assert codex.classify_failure(_run(stdout=text)).code != "user_config_rejected"
    assert codex.classify_failure(_run(), last_message=text).code != "user_config_rejected"


def test_retired_config_setting_owned_by_an_operator_passthrough_is_theirs(monkeypatch):
    # The message names no file and no `-c` marker, so ownership cannot be read off it.
    # When the operator's own passthrough sets the retired key, the rejection is theirs.
    # `model_provider` stands in for a retired key an operator CAN own: `approval_policy`
    # itself never can (the test below pins why), so using it here would assert nothing.
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "-c model_provider=acme")
    stderr = 'Error: model_provider = "acme" is no longer supported; remove this setting\n'
    err = codex.classify_failure(_run(stderr=stderr))
    assert err.code == "extra_args_rejected"
    assert "model_provider" in (err.message or "")


def test_a_retired_guarantee_key_can_never_be_operator_owned(monkeypatch):
    # `approval_policy` is on the extra-args denylist precisely because it could weaken an
    # advertised guarantee, so the ownership branch above can never fire for it and the
    # rejection stays the USER's config. This pins that coupling: if the denylist ever
    # stopped refusing the key, an operator passthrough could claim this rejection.
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "-c approval_policy=untrusted")
    assert config.extra_args().owns_config_key("approval_policy") is False
    err = codex.classify_failure(_run(stderr=_RETIRED_SETTING_STDERR))
    assert err.code == "user_config_rejected"


def test_retired_config_setting_does_not_disturb_unrelated_failures(monkeypatch):
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    assert codex.classify_failure(_run(stderr="some unrelated boom")).code == "nonzero_exit"


def test_retired_config_setting_repair_prose_fits_this_grammar(monkeypatch):
    """The shared `user_config_rejected` repair prose is written for the OTHER grammar.

    It says the key is one "this codex version does not recognize" and points at "the
    reported file and line". For a retired setting both are wrong: the key IS recognized
    (only its value is refused), and this message reports no file and no line. An agent
    following that guidance looks for location data that was never sent."""
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    err = codex.classify_failure(_run(stderr=_RETIRED_SETTING_STDERR))
    assert err.repair is not None
    alt = err.repair.alternative or ""
    assert "does not recognize" not in alt
    assert "reported file and line" not in alt
    # It must still carry the actionable instruction and the lossy fallback.
    assert "no longer supports" in alt or "no longer supported" in alt
    assert "ignore-config" in alt


def test_retired_config_setting_discloses_a_selected_operator_profile(monkeypatch):
    """Unlike the strict grammar, this message names no file — so ownership is UNKNOWN.

    `--profile NAME` makes codex load `$CODEX_HOME/NAME.config.toml`, and a profile can
    reintroduce a setting the extra-args denylist refuses on `-c`. When one is selected we
    cannot tell whether the retired value came from the user's own config or from the
    operator's profile, so the message must not assert the user's config definitively."""
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "--profile myprof")
    err = codex.classify_failure(_run(stderr=_RETIRED_SETTING_STDERR))
    assert "myprof" in (err.message or "")
    # Positive control: with NO profile selected, the message is unqualified.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    plain = codex.classify_failure(_run(stderr=_RETIRED_SETTING_STDERR))
    assert "myprof" not in (plain.message or "")


@pytest.mark.parametrize("key", sorted(cli_contract.PLUGIN_OWNED_CONFIG_KEYS))
def test_retired_value_on_a_plugin_pinned_key_is_drift_only_when_this_run_sent_it(key, monkeypatch):
    """A retired VALUE on a key the plugin pins is drift — IF this run sent the key.

    The strict sibling has this branch and its docstring calls it security-relevant. This
    path needs it for the same reason: when the plugin sends one of these keys, codex
    refusing its value is a statement about OUR argv, not about the user's `config.toml`
    (a `-c` override outranks the file entirely, so the file cannot be what failed).
    Blaming the user would send them to edit a file that does not contain the setting.

    But membership in PLUGIN_OWNED_CONFIG_KEYS is not proof the key was SENT: the
    workspace pins ride only `workspace-write` runs and the effort key only when an
    effort was requested (#550). On any other run the retired value can only be the
    user's own, and calling it drift sends them after a plugin update instead of their
    file. So attribution turns on the keys THIS run emitted, defaulting to none."""
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    stderr = f"Error: {key} = false is no longer supported; remove this setting\n"
    # Positive control: the grammar really does match, so the assertions below are about
    # attribution and not about the line being unparseable.
    assert cli_contract.parse_unsupported_config_setting(stderr) is not None
    assert codex.classify_failure(_run(stderr=stderr)).code == "user_config_rejected"
    sent = codex.classify_failure(_run(stderr=stderr), plugin_config_keys=frozenset({key}))
    assert sent.code == "cli_contract_changed"


@pytest.mark.parametrize(
    "signature",
    ["please run codex login", "usage limit reached", "unexpected argument"],
)
def test_retired_config_setting_is_classified_before_the_substring_matchers(signature):
    """Pins the ORDER, which is behavior and not commentary.

    The echoed value is untrusted text codex read off disk, and a real retired setting can
    legitimately hold a value carrying an auth, rate-limit, or drift signature. Those
    matchers are substring tests, so whichever runs first wins. Moving this check below
    them silently reclassifies these runs — and the whole suite still passed when that
    mutation was applied, which is why this test exists."""
    stderr = f'Error: k = "{signature}" is no longer supported; remove this setting\n'
    # Positive control: a downstream matcher really does claim this text, so the assertion
    # is about ordering rather than about an inert string.
    assert (
        cli_contract.is_auth_failure(stderr)
        or cli_contract.is_rate_limited(stderr)
        or cli_contract.is_contract_drift(stderr)
    )
    assert codex.classify_failure(_run(stderr=stderr)).code == "user_config_rejected"


def test_retired_config_setting_profile_disclosure_is_a_readable_sentence(monkeypatch):
    # The disclosure is spliced into the middle of the sentence, so it has to leave a
    # grammatical one behind — "cannot inspect sets `k`" is not.
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "--profile myprof")
    msg = codex.classify_failure(_run(stderr=_RETIRED_SETTING_STDERR)).message or ""
    assert "inspect sets" not in msg
    assert "myprof" in msg


# --- invalid config VALUE classification (codex 0.149, #550) -----------------------
# Captured verbatim from codex-cli 0.149.1 (2026-08-25); the same wording re-observed on
# 0.151.0 (2026-08-29). The third config-parse grammar:
# a recognized key whose value fails serde validation — a typo, plausibly more common
# than the retired setting above, and hit at the default isolation with no pin.
_INVALID_VARIANT_STDERR = (
    "Error loading config.toml: unknown variant `bogus`, expected one of `untrusted`, "
    "`on-failure`, `on-request`, `granular`, `never`\n"
    "in `approval_policy`\n\n"
)
_INVALID_TYPE_STDERR = (
    'Error loading config.toml: invalid type: string "yes", expected a boolean\n'
    "in `sandbox_workspace_write.network_access`\n\n"
)


def _invalid_type_stderr(key: str) -> str:
    return (
        f'Error loading config.toml: invalid type: string "yes", expected a boolean\nin `{key}`\n\n'
    )


def test_plugin_config_keys_for_mirrors_build_exec_command():
    """The emitted-key helper must agree with the argv builder for every run shape.

    Ownership attribution (#550) turns on which `-c` KEYS THIS run sent, so the helper
    is checked against the keys actually parsed out of `build_exec_command`'s tokens —
    a hand-maintained mirror that drifted would misattribute silently."""
    for sandbox in cli_contract.VALID_SANDBOXES:
        for effort in (None, "high", ""):
            for di in (None, "focus"):
                cmd, _ = codex.build_exec_command(
                    cwd="/w",
                    sandbox=sandbox,
                    isolation="inherit",
                    output_last_message_path="/x",
                    reasoning_effort=effort,
                    developer_instructions=di,
                    flag_support=_ALL_FLAGS,
                )
                emitted = {
                    cmd[i + 1].split("=", 1)[0] for i, tok in enumerate(cmd[:-1]) if tok == "-c"
                }
                assert (
                    codex.plugin_config_keys_for(
                        sandbox=sandbox, reasoning_effort=effort, developer_instructions=di
                    )
                    == emitted
                ), (sandbox, effort, di)
    # Every emitted key is a plugin-owned one, and each plugin-owned key is emitted by
    # SOME run shape — otherwise the set below and the pins have drifted apart.
    union: set[str] = set()
    for sandbox in cli_contract.VALID_SANDBOXES:
        for effort in (None, "high"):
            for di in (None, "focus"):
                keys = codex.plugin_config_keys_for(
                    sandbox=sandbox, reasoning_effort=effort, developer_instructions=di
                )
                assert keys <= cli_contract.PLUGIN_OWNED_CONFIG_KEYS
                union |= keys
    assert union == cli_contract.PLUGIN_OWNED_CONFIG_KEYS


def test_invalid_config_value_is_user_config_rejected(monkeypatch):
    # Before #550 this matched no signature and fell through to a generic nonzero_exit.
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    err = codex.classify_failure(_run(stderr=_INVALID_VARIANT_STDERR))
    assert err.code == "user_config_rejected"
    assert err.temporary is False
    assert err.repair is not None
    assert err.repair.next_step == "correct_config"
    assert "approval_policy" in (err.message or "")
    # What codex would ACCEPT is the actionable content and is surfaced.
    assert "`on-failure`" in (err.message or "")


def test_invalid_config_value_never_echoes_the_offending_value(monkeypatch):
    """The rejected value is user free text — plausibly a secret pasted into the wrong
    key — and no pattern-based redactor can recognize an arbitrary one, so it must not
    reach the envelope at all (the key and what codex expected are enough to fix it)."""
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    # A short value, an over-cap one (a parse failure would fall through to the generic
    # branch, which quotes the head of stderr — the value), and a delimiter-laden one.
    for secret in (
        "hunter2-not-a-known-token-shape",
        "S" * (cli_contract.STRICT_CONFIG_KEY_MAX_CHARS + 10),
        "sec`ret, expected a comma",
    ):
        for stderr in (
            _INVALID_VARIANT_STDERR.replace("`bogus`", f"`{secret}`"),
            _INVALID_TYPE_STDERR.replace('"yes"', f'"{secret}"'),
        ):
            err = codex.classify_failure(_run(stderr=stderr))
            # Positive control: the grammar matched, so the absence below is deliberate.
            assert err.code == "user_config_rejected"
            assert secret not in (err.message or "")
            assert secret[:200] not in (err.message or "")
            assert secret not in (err.repair.alternative if err.repair else "")


def test_invalid_config_value_wrong_type_message_names_the_expected_type(monkeypatch):
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    # On a READ-ONLY run the plugin does not send this key, so it is the user's file.
    err = codex.classify_failure(_run(stderr=_INVALID_TYPE_STDERR))
    assert err.code == "user_config_rejected"
    assert "sandbox_workspace_write.network_access" in (err.message or "")
    assert "a boolean" in (err.message or "")


def test_invalid_config_value_is_read_from_stderr_only(monkeypatch):
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    text = _INVALID_VARIANT_STDERR
    assert codex.classify_failure(_run(stderr=text)).code == "user_config_rejected"
    assert codex.classify_failure(_run(stdout=text)).code != "user_config_rejected"
    assert codex.classify_failure(_run(), last_message=text).code != "user_config_rejected"


def test_invalid_config_value_owned_by_an_operator_passthrough_is_theirs(monkeypatch):
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "-c model_provider=acme")
    stderr = (
        "Error loading config.toml: unknown variant `acme`, expected one of `openai`, `x`\n"
        "in `model_provider`\n"
    )
    err = codex.classify_failure(_run(stderr=stderr))
    assert err.code == "extra_args_rejected"
    assert "model_provider" in (err.message or "")


def test_invalid_config_value_under_an_operator_parent_table_is_theirs(monkeypatch):
    """A `-c t={k=v}` parent-table assignment echoes the dotted CHILD path (probed live
    on 0.149.1), so exact key matching would hand the operator's own mistake to the user."""
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "-c model_providers.acme={base_url=3}")
    err = codex.classify_failure(_run(stderr=_invalid_type_stderr("model_providers.acme.base_url")))
    assert err.code == "extra_args_rejected"


@pytest.mark.parametrize("key", sorted(cli_contract.PLUGIN_OWNED_CONFIG_KEYS))
def test_invalid_value_on_a_pinned_key_is_drift_only_when_this_run_sent_it(key, monkeypatch):
    """Membership in PLUGIN_OWNED_CONFIG_KEYS is not proof the plugin sent the key.

    The workspace pins ride only `workspace-write` runs and the effort key only when an
    effort was requested. A user whose own config mistypes one of those keys on a
    read-only consult would otherwise be told the PLUGIN drifted — and sent after an
    update instead of their file. When the plugin DID send the key, a `-c` override
    outranks the file entirely (probed live: a bad file value under a good override
    does not fail at all), so this grammar naming it can only mean OUR value was
    refused: genuine cli_contract_changed."""
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    stderr = _invalid_type_stderr(key)
    assert cli_contract.parse_invalid_config_value(stderr) is not None
    # Default context — no keys emitted — is the user's config.
    assert codex.classify_failure(_run(stderr=stderr)).code == "user_config_rejected"
    not_sent = codex.classify_failure(_run(stderr=stderr), plugin_config_keys=frozenset())
    assert not_sent.code == "user_config_rejected"
    sent = codex.classify_failure(_run(stderr=stderr), plugin_config_keys=frozenset({key}))
    assert sent.code == "cli_contract_changed"


def test_invalid_config_value_repair_prose_fits_this_grammar(monkeypatch):
    # The shared table prose is for the unknown-KEY grammar (see the #542 sibling).
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    err = codex.classify_failure(_run(stderr=_INVALID_VARIANT_STDERR))
    assert err.repair is not None
    alt = err.repair.alternative or ""
    assert "does not recognize" not in alt
    assert "reported file and line" not in alt
    assert "ignore-config" in alt


def test_invalid_config_value_discloses_a_selected_operator_profile(monkeypatch):
    monkeypatch.setenv(config.EXTRA_ARGS_ENV, "--profile myprof")
    err = codex.classify_failure(_run(stderr=_INVALID_VARIANT_STDERR))
    assert "myprof" in (err.message or "")
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    plain = codex.classify_failure(_run(stderr=_INVALID_VARIANT_STDERR))
    assert "myprof" not in (plain.message or "")


@pytest.mark.parametrize(
    "signature",
    ["please run codex login", "usage limit reached", "unexpected argument"],
)
def test_invalid_config_value_is_classified_before_the_substring_matchers(signature):
    """Pins the ORDER. The allowed-variant list is codex's, but the offending value is
    the user's and is consumed by the grammar — it can carry any substring."""
    stderr = _INVALID_VARIANT_STDERR.replace("`bogus`", f"`{signature}`")
    assert (
        cli_contract.is_auth_failure(stderr)
        or cli_contract.is_rate_limited(stderr)
        or cli_contract.is_contract_drift(stderr)
    )
    assert codex.classify_failure(_run(stderr=stderr)).code == "user_config_rejected"


def test_invalid_config_value_does_not_disturb_unrelated_failures(monkeypatch):
    monkeypatch.delenv(config.EXTRA_ARGS_ENV, raising=False)
    assert codex.classify_failure(_run(stderr="some unrelated boom")).code == "nonzero_exit"


async def test_run_codex_exec_maps_a_bad_override_to_binary_missing(clean_env, tmp_path):
    """`build_exec_command()` runs inside `CodexBackend.prepare()`, so a bad
    `CODEX_IN_CLAUDE_CODEX_BIN` override raised `BinaryNotFoundError` out of the run
    instead of surfacing as the run-level fact it is. It now comes back as the same
    `binary_missing` CommandRun `runtime.run_async` returns for a spawn that finds no
    binary, so `classify_failure` yields `codex_not_found` (Copilot, PR #539). The
    override fails before any spawn, so nothing is mocked."""
    clean_env.setenv(binpath.ENV_VAR, str(tmp_path / "does-not-exist"))
    result = await codex.run_codex_exec(
        "q",
        kind="consult",
        cwd=str(tmp_path),
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=30,
    )
    assert result.run.binary_missing
    assert result.run.exit_code == 127
    assert result.last_message is None
    err = codex.classify_failure(result.run, last_message=None, events="")
    assert err.code == "codex_not_found"
    assert "does-not-exist" not in err.message


# --- #556: caller developer instructions ride one composed -c override ----------------

_DI_KEY = cli_contract.DEVELOPER_INSTRUCTIONS_CONFIG_KEY


def _build(tmp_path, **kw):
    return codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        flag_support=_ALL_FLAGS,
        **kw,
    )


def test_build_exec_command_composes_developer_instructions_once(tmp_path):
    from codex_in_claude import prompts

    cmd, dropped = _build(tmp_path, developer_instructions="Focus on locking.")
    tokens = [t for t in cmd if t.startswith(f"{_DI_KEY}=")]
    assert len(tokens) == 1
    assert cmd[cmd.index(tokens[0]) - 1] == "-c"
    # The right-hand side is valid TOML decoding to the composed value: framing first,
    # the caller's exact text between the markers (encode once, compose once).
    decoded = tomllib.loads(f"v = {tokens[0].partition('=')[2]}")["v"]
    assert decoded == prompts.compose_developer_instructions("Focus on locking.")
    assert decoded.startswith(prompts.DEVELOPER_INSTRUCTIONS_FRAMING)
    assert "Focus on locking." in decoded
    # A plugin `-c` rode, so the strict-config guard must arm (silent upstream rename
    # of the key would otherwise drop the caller's instructions without a trace).
    assert cli_contract.STRICT_CONFIG_FLAG in cmd
    assert dropped == []


@pytest.mark.parametrize(
    "text",
    [
        'line1\nline2 with "quotes" and back\\slash',
        "astral 😀 and café",
    ],
)
def test_build_exec_command_developer_instructions_toml_round_trip(tmp_path, text):
    from codex_in_claude import prompts

    cmd, _ = _build(tmp_path, developer_instructions=text)
    token = next(t for t in cmd if t.startswith(f"{_DI_KEY}="))
    decoded = tomllib.loads(f"v = {token.partition('=')[2]}")["v"]
    assert decoded == prompts.compose_developer_instructions(text)


def test_build_exec_command_omits_developer_instructions_when_none(tmp_path):
    # The common run sends NO developer override: no framing-only turn, no `-c`, no
    # strict-config arming — byte-identical to the pre-#556 argv.
    cmd, _ = _build(tmp_path, developer_instructions=None)
    assert not any(_DI_KEY in tok for tok in cmd)
    assert cli_contract.STRICT_CONFIG_FLAG not in cmd


def test_build_exec_command_developer_instructions_ordering(tmp_path, monkeypatch):
    # After the effort pair, before the operator passthrough and the stdin sentinel.
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c model_provider=azure")
    cmd, _ = codex.build_exec_command(
        cwd="/repo",
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        reasoning_effort="high",
        developer_instructions="x",
        extra_args=config.extra_args().tokens,
        flag_support=_ALL_FLAGS,
    )
    effort_at = cmd.index(f'{_EFFORT_KEY}="high"')
    di_at = next(i for i, t in enumerate(cmd) if t.startswith(f"{_DI_KEY}="))
    operator_at = cmd.index("model_provider=azure")
    assert effort_at < di_at < operator_at < len(cmd) - 1
    assert cmd[-1] == "-"


def test_plugin_config_keys_for_includes_developer_instructions_iff_sent():
    with_di = codex.plugin_config_keys_for(
        sandbox="read-only", reasoning_effort=None, developer_instructions="x"
    )
    without = codex.plugin_config_keys_for(
        sandbox="read-only", reasoning_effort=None, developer_instructions=None
    )
    assert cli_contract.DEVELOPER_INSTRUCTIONS_CONFIG_KEY in with_di
    assert cli_contract.DEVELOPER_INSTRUCTIONS_CONFIG_KEY not in without


def test_classify_shared_dash_c_stays_contract_changed_for_any_plugin_owned_key():
    # Codex-review regression (#556 plan): ownership of the shared `-c` token derives
    # from the RUN-SPECIFIC emitted key set, not from the effort parameter alone — a
    # developer-instructions run (no effort) also emits a bare `-c` pair, so a
    # rejection naming only that shared token must stay fail-loud.
    err = codex.classify_failure(
        CommandRun("", "error: unexpected argument '-c' found", 2, 1, False),
        extra_args=config.ExtraArgs(
            tokens=("-c", "model_provider=azure"),
            descriptors=("-c", "model_provider"),
            option_count=1,
            configured=True,
        ),
        reasoning_effort=None,
        plugin_config_keys=frozenset({cli_contract.DEVELOPER_INSTRUCTIONS_CONFIG_KEY}),
    )
    assert err.code == "cli_contract_changed"
