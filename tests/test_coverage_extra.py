"""Targeted tests for branches not exercised by the primary suites."""

from __future__ import annotations

import subprocess

from codex_in_claude import config, prompts, server
from codex_in_claude._core import runtime


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
    assert config.version_supported("codex-cli 0.140.0") is True


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
    ctx = _FakeCtx([f"file://{tmp_path}"])
    res = await server.codex_consult("q", ctx=ctx)
    assert res["ok"] is True
    assert res["meta"]["workspace_source"] == "roots"


# --- server status: could-not-determine auth --------------------------------
def test_status_auth_unknown(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.140.0")
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
