"""Targeted tests for branches not exercised by the primary suites."""

from __future__ import annotations

import subprocess

import pytest

from codex_in_claude import config, orchestration, prompts, server
from codex_in_claude._core import runtime


async def _run_review_direct(tmp_path, *, scope="working_tree", base=None, commit=None):
    # The sync review tool now runs in a detached worker (#169); its run-behavior
    # branches are exercised by calling the same orchestration entry point directly.
    meta = server._base_meta(
        str(tmp_path),
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=180,
        scope=scope,
        base=base,
        commit=commit,
    )
    return await orchestration.run_review(
        str(tmp_path),
        meta,
        scope=scope,
        base=base,
        commit=commit,
        paths=None,
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=180,
        model=None,
        git_timeout=30,
        max_bytes=config.max_input_bytes(),
    )


# --- prompts -----------------------------------------------------------------
def test_build_consult_prompt_with_context():
    out = prompts.build_consult_prompt("Q?", "some context")
    assert "## Question" in out
    assert "## Context (untrusted data)" in out
    assert "some context" in out


def test_build_consult_prompt_without_context():
    out = prompts.build_consult_prompt("Q?", "")
    assert "## Context" not in out


def test_build_delegate_prompt():
    out = prompts.build_delegate_prompt("do the thing", "ctx")
    assert "## Task" in out
    assert "do the thing" in out
    assert "## Context (untrusted data)" in out


def test_build_review_prompt_with_context():
    out = prompts.build_review_prompt("diff --git a/x b/x", "working_tree", "I verified numstat")
    assert "## Diff under review (working_tree) — untrusted data" in out
    assert "## Author-provided context (untrusted data)" in out
    assert "I verified numstat" in out
    # Author intent precedes the diff so the reviewer reads the why first.
    assert out.index("Author-provided context") < out.index("Diff under review")


def test_build_review_prompt_without_context():
    out = prompts.build_review_prompt("diff --git a/x b/x", "working_tree", "")
    assert "Author-provided context" not in out


# --- config edges ------------------------------------------------------------
def test_env_int_bad_value_falls_back(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_TIMEOUT_SECONDS", "notanint")
    assert config.defaults().timeout_seconds == config.DEFAULT_TIMEOUT_SECONDS


def test_worktree_base_override(clean_env, tmp_path):
    clean_env.setenv("CODEX_IN_CLAUDE_WORKTREE_BASE", str(tmp_path))
    assert config.worktree_base() == tmp_path


def test_worktree_base_default(clean_env):
    assert config.worktree_base() is None


def test_supported_versions_partial_token(clean_env):
    # A token without a minor is skipped; falls back to built-in set.
    clean_env.setenv("CODEX_IN_CLAUDE_SUPPORTED_VERSIONS", "5")
    assert config.version_supported("codex-cli 0.142.0") is True


# --- runtime property --------------------------------------------------------
def test_command_run_binary_missing_property():
    run = runtime.CommandRun("", runtime.BINARY_NOT_FOUND, 127, 1, False)
    assert run.binary_missing is True
    assert runtime.CommandRun("x", "", 0, 1, False).binary_missing is False


def test_kill_process_tree_already_exited():
    proc = subprocess.Popen(["true"])
    proc.wait()
    # No raise when the process already exited.
    runtime.kill_process_tree(proc)


# --- server: roots from ctx --------------------------------------------------
class _FakeRoot:
    def __init__(self, uri):
        self.uri = uri


class _FakeCtx:
    def __init__(self, uris, raise_exc=False):
        self._uris = uris
        self._raise = raise_exc

    async def list_roots(self):
        if self._raise:
            raise RuntimeError("client has no roots")
        return [_FakeRoot(u) for u in self._uris]


async def test_roots_from_ctx_file_uris():
    ctx = _FakeCtx(["file:///Users/me/repo", "https://not-a-file/x"])
    paths = await server._roots_from_ctx(ctx)
    assert paths == ["/Users/me/repo"]


async def test_roots_from_ctx_none():
    assert await server._roots_from_ctx(None) == []


async def test_roots_from_ctx_unsupported_degrades():
    ctx = _FakeCtx([], raise_exc=True)
    assert await server._roots_from_ctx(ctx) == []


async def test_consult_uses_roots(monkeypatch, clean_env, tmp_path):
    async def fake(*args, **kwargs):
        return server.codex.CodexExecResult(
            run=runtime.CommandRun("", "", 0, 1, False), last_message="answer", events=""
        )

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    # The sync consult now dispatches to the detached worker; assert the tool resolved
    # the MCP root into the job spec's workspace (source == "roots") at the start seam.
    captured = {}

    def capture_start(meta, cwd, *, kind, spec, deadline):
        captured["source"] = spec["workspace_source"]
        captured["cwd"] = cwd
        return server.serialize_error(
            server.ErrorResult(error=server.make_error("internal_error", "stop"), meta=meta)
        )

    monkeypatch.setattr(server, "_start_job", capture_start)
    ctx = _FakeCtx([f"file://{tmp_path}"])
    res = await server.codex_consult("q", ctx=ctx)
    assert res["ok"] is False  # short-circuited by the capture stub
    assert captured["source"] == "roots"
    assert captured["cwd"] == str(tmp_path)


# --- server status: could-not-determine auth --------------------------------
def test_status_auth_unknown(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.142.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (None, None))
    res = server.codex_status()
    assert res["ready"] is False
    assert "Could not determine" in res["readiness_detail"]


def test_status_version_unsupported_warning(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.99.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "ok"))
    res = server.codex_status()
    assert res["version_supported"] is False
    assert res["version_warning"]


def test_status_flags_warning(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.142.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "ok"))
    monkeypatch.setattr(server.preflight, "missing_expected_flags", lambda fs: ["--sandbox"])
    res = server.codex_status()
    assert res["flags_warning"]
    assert "--sandbox" in res["flags_warning"]


# --- review: extra branches --------------------------------------------------
async def test_review_git_unavailable(monkeypatch, clean_env, tmp_path):
    from codex_in_claude._core import gitdiff

    def boom(*a, **k):
        raise gitdiff.GitUnavailableError("git not found")

    monkeypatch.setattr(gitdiff, "gather_diff", boom)
    res = await _run_review_direct(tmp_path, scope="working_tree")
    assert res["ok"] is False
    assert res["error"]["code"] == "git_unavailable"


async def test_review_generic_git_runtime_error(monkeypatch, clean_env, tmp_path):
    from codex_in_claude._core import gitdiff

    def boom(*a, **k):
        raise RuntimeError("git diff timed out after 60s")

    monkeypatch.setattr(gitdiff, "gather_diff", boom)
    res = await _run_review_direct(tmp_path, scope="working_tree")
    assert res["ok"] is False
    assert res["error"]["code"] == "git_unavailable"


async def test_review_commit_scope_label(monkeypatch, clean_env, tmp_path):
    from codex_in_claude._core import gitdiff

    monkeypatch.setattr(
        gitdiff,
        "gather_diff",
        lambda *a, **k: gitdiff.DiffResult(
            text="diff --git a/x b/x\n+y", summary=gitdiff.DiffSummary(1, 1, 0)
        ),
    )
    seen = {}

    async def fake(prompt, **k):
        seen["prompt"] = prompt
        return server.codex.CodexExecResult(
            run=server.codex.runtime.CommandRun("", "", 0, 1, False),
            last_message='{"summary":"ok","verdict":"pass","confidence":"high","findings":[],'
            '"questions":[],"assumptions":[],"next_steps":[]}',
            events="",
        )

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await _run_review_direct(tmp_path, scope="commit", commit="abc123")
    assert res["ok"] is True
    assert "commit abc123" in seen["prompt"]
    assert res["meta"]["commit"] == "abc123"


async def test_review_invalid_workspace(clean_env):
    res = await server.codex_review_changes(scope="working_tree", workspace_root="relative/x")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


async def test_review_placeholder_env(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    res = await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "unexpanded_env_placeholder"


def test_main_runs(monkeypatch):
    called = {"n": 0}
    # _patch_main() stubs _install_signal_logging() / obs.configure() so main() does
    # not mutate the test runner's process-wide SIGINT/SIGTERM disposition (#76).
    _patch_main(monkeypatch, lambda *a, **k: called.__setitem__("n", 1))
    server.main()
    assert called["n"] == 1


# --- main() top-level transport-loop guard (#76) -----------------------------
import signal as _signal  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


def _patch_main(monkeypatch, run):
    """Run main() with a mock logger and a stubbed mcp.run; return the logger."""
    log = MagicMock()
    monkeypatch.setattr(server.obs, "configure", lambda *a, **k: log)
    monkeypatch.setattr(server, "_install_signal_logging", lambda *_a: None)
    monkeypatch.setattr(server.mcp, "run", run)
    return log


def _raise(exc):
    def run(*_a, **_k):
        raise exc

    return run


def test_main_crash_logs_breadcrumb_and_exits_nonzero(monkeypatch):
    log = _patch_main(monkeypatch, _raise(RuntimeError("boom")))
    with pytest.raises(SystemExit) as ei:
        server.main()
    assert ei.value.code == 1
    log.exception.assert_called_once()
    # The breadcrumb names the reconnect path so a dead server is recoverable.
    assert "/mcp" in log.exception.call_args[0][0]


def test_main_keyboard_interrupt_is_clean_shutdown(monkeypatch):
    log = _patch_main(monkeypatch, _raise(KeyboardInterrupt()))
    server.main()  # must not raise
    log.exception.assert_not_called()
    assert any("clean shutdown" in c.args[0] for c in log.info.call_args_list)


def test_main_broken_pipe_is_clean_shutdown(monkeypatch):
    log = _patch_main(monkeypatch, _raise(BrokenPipeError()))
    server.main()
    log.exception.assert_not_called()
    assert any("clean shutdown" in c.args[0] for c in log.info.call_args_list)


def test_main_normal_return_logs_transport_closed(monkeypatch):
    log = _patch_main(monkeypatch, lambda *a, **k: None)
    server.main()
    log.exception.assert_not_called()
    assert any("transport closed" in c.args[0] for c in log.info.call_args_list)


def test_main_reraises_system_exit_code(monkeypatch):
    log = _patch_main(monkeypatch, _raise(SystemExit(3)))
    with pytest.raises(SystemExit) as ei:
        server.main()
    assert ei.value.code == 3
    log.exception.assert_not_called()


def test_signal_logging_handler_logs_and_chains_to_default(monkeypatch):
    log = MagicMock()
    recorded = {}
    killed = {}

    def record_signal(num, h):
        recorded[num] = h

    # Capture handlers instead of mutating the real process disposition; pretend the
    # prior disposition was the OS default.
    monkeypatch.setattr(server.signal, "getsignal", lambda num: _signal.SIG_DFL)
    monkeypatch.setattr(server.signal, "signal", record_signal)
    monkeypatch.setattr(server.os, "kill", lambda pid, num: killed.__setitem__("call", (pid, num)))
    server._install_signal_logging(log)
    handler = recorded[_signal.SIGTERM]
    # When the prior disposition is the default, the handler logs then re-raises the
    # signal at OS default (so it does not swallow shutdown).
    handler(_signal.SIGTERM, None)
    assert log.info.called
    assert killed["call"][1] == _signal.SIGTERM


def test_signal_logging_skips_ignored_disposition(monkeypatch):
    # A signal inherited as ignored must stay truly ignored: no handler installed, so
    # no Python code runs (and no logging) on delivery.
    log = MagicMock()
    recorded = {}

    def record_signal(num, h):
        recorded[num] = h

    monkeypatch.setattr(server.signal, "getsignal", lambda num: _signal.SIG_IGN)
    monkeypatch.setattr(server.signal, "signal", record_signal)
    server._install_signal_logging(log)
    assert recorded == {}  # nothing installed for either signal


def test_signal_logging_handler_chains_to_prior_handler(monkeypatch):
    log = MagicMock()
    prior = MagicMock()
    recorded = {}

    def record_signal(num, h):
        recorded[num] = h

    monkeypatch.setattr(server.signal, "getsignal", lambda num: prior)
    monkeypatch.setattr(server.signal, "signal", record_signal)
    server._install_signal_logging(log)
    recorded[_signal.SIGINT](_signal.SIGINT, None)
    assert log.info.called
    prior.assert_called_once_with(_signal.SIGINT, None)
