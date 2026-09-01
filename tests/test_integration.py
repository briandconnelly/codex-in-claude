"""Live tests that call the real `codex` CLI. Opt in with:

    uv run pytest -m integration --no-cov

They require codex to be installed and authenticated (`codex login`). They spend
tokens, so they are excluded from the default run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pontonier.core import runtime

from codex_in_claude import cli_contract, codex, server

pytestmark = pytest.mark.integration


def _feature_state(feature: str, *flags: str) -> str | None:
    """Live `codex features list [flags]` → the effective 'true'/'false' for one feature."""
    run = runtime.run_sync_capture(
        [cli_contract.CODEX_BIN, "features", "list", *flags], timeout_seconds=30
    )
    for line in run.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == feature:
            return parts[-1]
    return None


def test_remote_plugin_disabled_by_plugin_flag_live():
    # #287: prove the mechanism against the real CLI (no model spend). The plugin's
    # guarantee is that `--disable remote_plugin` forces the feature OFF — NOT that the
    # upstream default is on (that premise is documented in cli_contract.py, and pinning it
    # here would make the test brittle if Codex ever flips the default). So assert only that
    # the feature exists and is readable, then that the plugin flag drives it to false.
    assert _feature_state(cli_contract.REMOTE_PLUGIN_FEATURE) in {"true", "false"}
    off = _feature_state(
        cli_contract.REMOTE_PLUGIN_FEATURE,
        cli_contract.DISABLE_FEATURE_FLAG,
        cli_contract.REMOTE_PLUGIN_FEATURE,
    )
    assert off == "false"
    # An operator --enable cannot win: --disable is order-independent.
    still_off = _feature_state(
        cli_contract.REMOTE_PLUGIN_FEATURE,
        "--enable",
        cli_contract.REMOTE_PLUGIN_FEATURE,
        cli_contract.DISABLE_FEATURE_FLAG,
        cli_contract.REMOTE_PLUGIN_FEATURE,
    )
    assert still_off == "false"
    # ...in EITHER order (#542): pinning only one order would miss a change that made the
    # last flag win, which is the shape an argv-precedence regression actually takes.
    reversed_order = _feature_state(
        cli_contract.REMOTE_PLUGIN_FEATURE,
        cli_contract.DISABLE_FEATURE_FLAG,
        cli_contract.REMOTE_PLUGIN_FEATURE,
        "--enable",
        cli_contract.REMOTE_PLUGIN_FEATURE,
    )
    assert reversed_order == "false"
    # ...and against the CONFIG layer too. `--disable X` is documented as exactly
    # `-c features.X=false`, so an operator `-c` setting it true is the same layer and the
    # precedence is worth pinning rather than assumed (#542).
    over_config = _feature_state(
        cli_contract.REMOTE_PLUGIN_FEATURE,
        "-c",
        f"features.{cli_contract.REMOTE_PLUGIN_FEATURE}=true",
        cli_contract.DISABLE_FEATURE_FLAG,
        cli_contract.REMOTE_PLUGIN_FEATURE,
    )
    assert over_config == "false"


def test_unknown_feature_name_fails_loud_live():
    """The fail-loud half of the remote-plugin guarantee (#287, pinned #542).

    If upstream ever renames or removes `remote_plugin`, the plugin must fail at arg-parse
    (zero spend, classified `cli_contract_changed`) instead of silently running with the
    feature back on. That conversion rests on codex rejecting an unknown feature NAME, so
    pin the rejection itself — and pin that the plugin's own feature name is NOT rejected,
    which is the positive control that keeps this from passing on a broken invocation."""
    rejected = runtime.run_sync_capture(
        [
            cli_contract.CODEX_BIN,
            "features",
            "list",
            cli_contract.DISABLE_FEATURE_FLAG,
            "definitely_not_a_feature",
        ],
        timeout_seconds=30,
    )
    assert rejected.exit_code != 0
    assert cli_contract.is_contract_drift(rejected.stderr, rejected.stdout) or (
        "Unknown feature flag" in (rejected.stderr or "")
    ), rejected.stderr
    # Positive control: the real feature name is accepted by the same parser.
    assert (
        _feature_state(
            cli_contract.REMOTE_PLUGIN_FEATURE,
            cli_contract.DISABLE_FEATURE_FLAG,
            cli_contract.REMOTE_PLUGIN_FEATURE,
        )
        == "false"
    )


def test_sleep_tool_disabled_by_plugin_flag_live():
    # #587: the second plugin-owned disable, pinned the same way as remote_plugin above —
    # the feature exists and is readable, and the plugin flag drives it to false in EITHER
    # order against `--enable` and against the `-c features.<name>=true` config layer.
    feature = cli_contract.SLEEP_TOOL_FEATURE
    assert _feature_state(feature) in {"true", "false"}
    assert _feature_state(feature, cli_contract.DISABLE_FEATURE_FLAG, feature) == "false"
    assert (
        _feature_state(feature, "--enable", feature, cli_contract.DISABLE_FEATURE_FLAG, feature)
        == "false"
    )
    assert (
        _feature_state(feature, cli_contract.DISABLE_FEATURE_FLAG, feature, "--enable", feature)
        == "false"
    )
    assert (
        _feature_state(
            feature, "-c", f"features.{feature}=true", cli_contract.DISABLE_FEATURE_FLAG, feature
        )
        == "false"
    )


_WIRE_CAPTURE = Path(__file__).resolve().parents[1] / "scripts" / "capture_wire_tools.py"
_SLEEP_ALWAYS_ON = "-c", 'features.sleep_tool.mode="always_on"'


def _wire_tool_names(*extra: str, script_opts: tuple[str, ...] = ()) -> list[str]:
    """The tool names codex sends the model under `extra` flags (zero spend, no auth).

    Drives `scripts/capture_wire_tools.py` — a local HTTP sink answers the first request
    with 400, so the run stops before any model call. `script_opts` are the script's own
    switches (before `--`); `extra` goes to codex (after it). A non-zero exit means NOTHING
    was captured and is a failure here, never an empty catalog (see the script's docstring)."""
    run = runtime.run_sync_capture(
        [sys.executable, str(_WIRE_CAPTURE), *script_opts, "--", *extra], timeout_seconds=150
    )
    assert run.exit_code == 0, f"wire capture failed (nothing captured): {run.stderr}"
    return run.stdout.split()


def test_plugin_disables_keep_clock_out_of_the_model_request_live():
    """The sleep-tool posture's real oracle is the request codex sends (#587).

    `codex features list` reports feature STATE; whether the `clock` namespace reaches the
    model is decided by the exec tool planner, gated on the feature's `mode`. So pin the
    wire: with `mode="always_on"` (which bypasses the model-metadata gate) `clock` MUST
    appear — the positive control that proves the capture observes feature gating — and
    with the plugin's own disable set added, in the argv orders an operator could produce,
    it must not."""
    disables = [
        tok
        for feature in cli_contract.MODEL_RUN_DISABLED_FEATURES
        for tok in (cli_contract.DISABLE_FEATURE_FLAG, feature)
    ]
    exposed = _wire_tool_names(*_SLEEP_ALWAYS_ON)
    assert "clock" in exposed, exposed  # positive control: an absent clock here is a blind probe
    assert "clock" not in _wire_tool_names(*disables, *_SLEEP_ALWAYS_ON)
    # The disable outranks an operator re-enable in either order, and the config layer.
    assert "clock" not in _wire_tool_names(*disables, "--enable", "sleep_tool", *_SLEEP_ALWAYS_ON)
    assert "clock" not in _wire_tool_names("--enable", "sleep_tool", *_SLEEP_ALWAYS_ON, *disables)
    assert "clock" not in _wire_tool_names(
        *disables, "-c", "features.sleep_tool=true", *_SLEEP_ALWAYS_ON
    )


def test_plugin_disable_outranks_profile_and_config_file_live(tmp_path):
    """The plugin's runtime `--disable` beats every operator config channel (#587 review).

    An opaque `--profile` and the `$CODEX_HOME/config.toml` `[features]` table (read at the
    default `inherit` isolation) can each set `sleep_tool` to `always_on`. The two channels
    live in SEPARATE scratch homes so each positive control proves its own channel: a home
    that carried both would let the profile rows pass even if `--profile` were ignored
    (Copilot review of #592). Neither channel survives the plugin's `--disable`, so the
    posture has no operator escape hatch short of editing the plugin — the docs must not
    claim one. Zero spend: the sink answers before any model call, and the scratch homes
    hold no credentials (codex needs no `config.toml` to exist)."""
    always_on = '[features]\nsleep_tool = { mode = "always_on" }\n'
    config_home = tmp_path / "config_home"
    config_home.mkdir()
    (config_home / "config.toml").write_text(always_on)
    profile_home = tmp_path / "profile_home"
    profile_home.mkdir()
    (profile_home / "sleepy.config.toml").write_text(always_on)  # no config.toml at all
    disable = (cli_contract.DISABLE_FEATURE_FLAG, cli_contract.SLEEP_TOOL_FEATURE)
    via_config = ("--codex-home", str(config_home), "--inherit-config")
    via_profile = ("--codex-home", str(profile_home), "--inherit-config")
    # The config-file channel: exposes the tool on its own, and the disable removes it.
    assert "clock" in _wire_tool_names(script_opts=via_config)
    assert "clock" not in _wire_tool_names(*disable, script_opts=via_config)
    # The profile channel, in isolation: the profile alone exposes it (so `--profile` is
    # genuinely read), and the disable removes it in either argv order.
    assert "clock" not in _wire_tool_names(script_opts=via_profile)  # nothing without --profile
    assert "clock" in _wire_tool_names("--profile", "sleepy", script_opts=via_profile)
    assert "clock" not in _wire_tool_names("--profile", "sleepy", *disable, script_opts=via_profile)
    assert "clock" not in _wire_tool_names(*disable, "--profile", "sleepy", script_opts=via_profile)


def test_status_live():
    res = server.codex_status()
    assert res["codex_found"] is True
    assert res["ready"] is True, res["readiness_detail"]


async def test_consult_live(tmp_path):
    res = await server.codex_consult(
        "Reply concisely in one sentence: what does the DRY principle mean?",
        workspace_root=str(tmp_path),
        timeout_seconds=150,
    )
    assert res["ok"] is True, res.get("error")
    assert res["summary"]
    assert res["meta"]["sandbox"] == "read-only"
    assert res["meta"]["session_id"]


def test_login_status_live():
    logged_in, _ = codex.login_status()
    assert logged_in is True


async def test_review_changes_live(tmp_path):
    import subprocess

    def g(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t.co")
    g("config", "user.name", "t")
    (tmp_path / "m.py").write_text("def f(xs):\n    return xs[0]\n")
    g("add", "-A")
    g("commit", "-qm", "init")
    # Introduce an obvious off-by-one bug.
    (tmp_path / "m.py").write_text(
        "def f(xs):\n"
        "    out = []\n"
        "    for i in range(len(xs) + 1):\n"
        "        out.append(xs[i])\n"
        "    return out\n"
    )
    res = await server.codex_review_changes(
        scope="working_tree", workspace_root=str(tmp_path), timeout_seconds=150
    )
    assert res["ok"] is True, res.get("error")
    assert res["meta"]["context_summary"]["files_changed"] == 1


async def test_delegate_live(tmp_path):
    import subprocess

    def g(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t.co")
    g("config", "user.name", "t")
    (tmp_path / "greet.py").write_text('def greet(n):\n    return "hi " + n\n')
    g("add", "-A")
    g("commit", "-qm", "init")
    before = (tmp_path / "greet.py").read_text()
    res = await server.codex_delegate(
        "Add a farewell(name) function returning 'bye ' + name to greet.py.",
        workspace_root=str(tmp_path),
        timeout_seconds=180,
    )
    assert res["ok"] is True, res.get("error")
    assert res["diff"]  # a proposed patch came back
    assert (tmp_path / "greet.py").read_text() == before  # live tree untouched
    # worktree cleaned up: only the main worktree remains
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout
    assert out.strip().count("\n") == 0


async def test_delegate_async_live(tmp_path, monkeypatch):
    import subprocess
    import time

    # keep job state out of the user's real cache dir
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "jobs"))

    def g(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t.co")
    g("config", "user.name", "t")
    (tmp_path / "greet.py").write_text('def greet(n):\n    return "hi " + n\n')
    g("add", "-A")
    g("commit", "-qm", "init")
    before = (tmp_path / "greet.py").read_text()

    started = await server.codex_delegate_async(
        "Add a farewell(name) function returning 'bye ' + name to greet.py.",
        workspace_root=str(tmp_path),
    )
    assert started["ok"] is True, started.get("error")
    job_id = started["job_id"]

    # poll to completion (bounded)
    deadline = time.monotonic() + 240
    status = None
    while time.monotonic() < deadline:
        status = await server.codex_job_status(job_id, workspace_root=str(tmp_path))
        if status["status"] != "running":
            break
        time.sleep(status.get("poll_after_ms", 1000) / 1000)
    assert status is not None and status["status"] == "done", status

    res = await server.codex_job_result(job_id, workspace_root=str(tmp_path))
    assert res["ok"] is True, res.get("error")
    assert res["diff"]
    assert res["meta"]["job_id"] == job_id
    assert (tmp_path / "greet.py").read_text() == before  # live tree untouched
    # worktree cleaned up
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout
    assert out.strip().count("\n") == 0

    # consume deletes the record
    await server.codex_job_consume_result(job_id, workspace_root=str(tmp_path))
    gone = await server.codex_job_status(job_id, workspace_root=str(tmp_path))
    assert gone["ok"] is False and gone["error"]["code"] == "job_not_found"


async def test_unknown_model_returns_envelope_not_exception(tmp_path):
    """An unknown slug surfaces a structured envelope (likely ok:false), never a crash.

    Opt-in — calls the real codex CLI and may spend. Run with:
        uv run pytest -m integration --no-cov -k unknown_model
    """
    res = await server.codex_consult(
        "ping",
        model="definitely-not-a-real-model-zzz",
        workspace_root=str(tmp_path),
    )
    assert "ok" in res  # structured envelope, not an exception


def _strict_probe(*extra: str, home: str, prompt: str = "hi") -> runtime.CommandRun:
    """A live `codex exec --strict-config` startup probe against a scratch $CODEX_HOME.

    ZERO SPEND, structurally: codex parses config before it authenticates, so a probe that
    trips the guard dies at parsing. A probe that does NOT trip it would run on to a real
    turn if it could authenticate, so the scratch `$CODEX_HOME` (no `auth.json`) is only
    half the guard — `OPENAI_API_KEY` is stripped from the merged environment too, or a
    machine that has one exported would spend here. What is left is a run that always
    stops at auth."""
    return runtime.run_sync_capture(
        [
            cli_contract.CODEX_BIN,
            *cli_contract.EXEC_SUBCOMMAND,
            cli_contract.STRICT_CONFIG_FLAG,
            *extra,
            "--skip-git-repo-check",
            prompt,
        ],
        timeout_seconds=60,
        # `env` REPLACES the environment (it goes straight to subprocess.run), so merge
        # rather than pass the override alone — a bare dict would drop PATH and the probe
        # would report codex_not_found instead of exercising anything.
        env={k: v for k, v in os.environ.items() if k not in {"OPENAI_API_KEY", "CODEX_API_KEY"}}
        | {"CODEX_HOME": home},
        stdin_text="",
    )


def test_strict_config_override_grammar_live(tmp_path):
    # #524: the classifier depends on this exact stderr grammar. If upstream rewords it,
    # a pin drift degrades from cli_contract_changed to a generic nonzero_exit — the run
    # still fails, but the diagnosis is lost. Parse the REAL output with the REAL parser.
    run = _strict_probe("-c", "bogus_key_xyz=1", home=str(tmp_path))
    rejection = cli_contract.parse_strict_config_rejection(run.stderr)
    assert rejection is not None, run.stderr
    assert rejection.origin == "override"
    assert rejection.key == "bogus_key_xyz"
    # And the whole classification path, end to end, on real output.
    assert codex.classify_failure(run).code in {"cli_contract_changed", "extra_args_rejected"}


def test_strict_config_file_grammar_live(tmp_path):
    # The second shape: an unknown key in the user's own config file, located by line.
    (tmp_path / "config.toml").write_text("some_unknown_junk_key = true\n", encoding="utf-8")
    run = _strict_probe(home=str(tmp_path))
    rejection = cli_contract.parse_strict_config_rejection(run.stderr)
    assert rejection is not None, run.stderr
    assert rejection.origin == "file"
    assert rejection.key == "some_unknown_junk_key"
    assert rejection.source_path is not None
    assert rejection.source_path.endswith("config.toml")
    assert rejection.line == 1
    assert codex.classify_failure(run).code == "user_config_rejected"


def test_strict_config_unselected_profile_table_is_validated_live(tmp_path):
    # The blast-radius fact the emission scope is chosen around: a table for a profile the
    # run never selects is validated too. If upstream narrows this, the availability
    # argument in COMPATIBILITY.md's strict-config section should be revisited.
    (tmp_path / "config.toml").write_text(
        "[profiles.myprof]\nsome_unknown_junk_key = true\n", encoding="utf-8"
    )
    run = _strict_probe(home=str(tmp_path))
    rejection = cli_contract.parse_strict_config_rejection(run.stderr)
    assert rejection is not None, run.stderr
    assert rejection.key == "profiles.myprof.some_unknown_junk_key"


def test_strict_config_is_exempted_by_ignore_user_config_live(tmp_path):
    # The documented escape hatch, and the positive control for the two tests above: the
    # SAME junk file that hard-fails at `inherit` must be exempt under --ignore-user-config,
    # proving those failures come from the file rather than from anything else in the argv.
    (tmp_path / "config.toml").write_text("some_unknown_junk_key = true\n", encoding="utf-8")
    run = _strict_probe("--ignore-user-config", home=str(tmp_path))
    assert cli_contract.parse_strict_config_rejection(run.stderr) is None, run.stderr


def test_strict_config_accepts_the_plugins_own_pinned_keys_live(tmp_path):
    # The guard must not fire on the argv the plugin actually sends: every pinned key is
    # still a key this codex recognizes. This is the check that catches an upstream rename
    # at upgrade time rather than at a user's first delegate run.
    pins: list[str] = []
    for key in sorted(cli_contract.PLUGIN_OWNED_CONFIG_KEYS):
        value = '"high"' if key == cli_contract.MODEL_REASONING_EFFORT_CONFIG_KEY else "false"
        if key == cli_contract.WORKSPACE_WRITE_WRITABLE_ROOTS_CONFIG_KEY:
            value = "[]"
        pins += ["-c", f"{key}={value}"]
    run = _strict_probe(*pins, home=str(tmp_path))
    assert cli_contract.parse_strict_config_rejection(run.stderr) is None, run.stderr


# --- #556: developer_instructions placement probes (zero-spend) -----------------------
# `codex debug prompt-input` renders the composed input without a model call, so these
# probes cost nothing and need no auth. They pin the TWO facts the feature rests on:
# where codex places the key's value, and that our composed string survives the TOML
# round-trip byte-exact.


def _prompt_input(*extra: str, home: str) -> runtime.CommandRun:
    return runtime.run_sync_capture(
        [cli_contract.CODEX_BIN, "debug", "prompt-input", *extra, "hi"],
        timeout_seconds=60,
        env={k: v for k, v in os.environ.items() if k not in {"OPENAI_API_KEY", "CODEX_API_KEY"}}
        | {"CODEX_HOME": home},
        stdin_text="",
    )


def test_developer_instructions_is_the_first_developer_item_live(tmp_path):
    import json as _json

    from codex_in_claude import codex, prompts

    composed = prompts.compose_developer_instructions('probe 😀 "q" \\ line\ntwo')
    cmd, _ = codex.build_exec_command(
        cwd=str(tmp_path),
        sandbox="read-only",
        isolation="inherit",
        output_last_message_path=str(tmp_path / "l"),
        developer_instructions='probe 😀 "q" \\ line\ntwo',
        flag_support=None,
    )
    token = next(
        t for t in cmd if t.startswith(f"{cli_contract.DEVELOPER_INSTRUCTIONS_CONFIG_KEY}=")
    )
    run = _prompt_input("-c", token, home=str(tmp_path))
    assert run.exit_code == 0, run.stderr
    messages = _json.loads(run.stdout)
    first = messages[0]
    assert first["role"] == "developer"
    # The composed value is the FIRST content item of the FIRST developer message —
    # ahead of codex's own developer content — and survives byte-exact.
    assert first["content"][0]["text"] == composed


def test_without_the_key_the_first_item_is_not_ours_live(tmp_path):
    # Negative control: the probe above is only evidence if an override-free render
    # does NOT start with our framing.
    import json as _json

    from codex_in_claude import prompts

    run = _prompt_input(home=str(tmp_path))
    assert run.exit_code == 0, run.stderr
    messages = _json.loads(run.stdout)
    texts = [c.get("text", "") for m in messages for c in m.get("content", [])]
    assert not any(t.startswith(prompts.DEVELOPER_INSTRUCTIONS_FRAMING) for t in texts)


def test_cli_override_outranks_a_config_file_value_live(tmp_path):
    # An operator's own `developer_instructions` in config.toml must not displace the
    # composed value on a run that carries the `-c` (the pin-outranks-file behavior
    # the workspace pins already rely on).
    import json as _json

    from codex_in_claude import prompts

    (tmp_path / "config.toml").write_text(
        'developer_instructions = "FILE_VALUE_MUST_LOSE"\n', encoding="utf-8"
    )
    composed = prompts.compose_developer_instructions("cli wins")
    encoded = _json.dumps(composed, ensure_ascii=False)
    run = _prompt_input(
        "-c",
        f"{cli_contract.DEVELOPER_INSTRUCTIONS_CONFIG_KEY}={encoded}",
        home=str(tmp_path),
    )
    assert run.exit_code == 0, run.stderr
    first = _json.loads(run.stdout)[0]
    assert first["role"] == "developer"
    assert first["content"][0]["text"] == composed
    assert "FILE_VALUE_MUST_LOSE" not in run.stdout
