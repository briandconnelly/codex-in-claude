"""Sync/async tool-pair parity (#204).

Each active tool has a synchronous and an ``_async`` variant that share the same
input preparation (isolation/detail resolution, workspace resolution, meta,
placeholder + input-size pre-flight, and the run ``spec``). Before extracting that
shared preparation into per-pair helpers, these tests pin the invariant the
extraction must preserve:

* the ``spec`` each variant builds is identical except ``timeout_seconds`` (sync
  clamps the per-call timeout; async uses the background-job deadline);
* the pre-flight error envelopes (``input_too_large``, workspace-resolve errors)
  carry an identical ``error`` block across the pair;
* competing pre-flight errors resolve in the same order they do today; and
* the idempotency argument hash of a pair is invariant once ``timeout_seconds`` is
  held equal, so the refactor cannot silently invalidate live dedup entries.

They pass against the pre-refactor code and must keep passing after it.
"""

from __future__ import annotations

import pytest

from codex_in_claude import server


@pytest.fixture
def capture_tail(monkeypatch):
    """Intercept the pair's tail calls, recording the ``spec`` (and meta/cwd) each
    variant builds instead of starting a real run."""
    calls: dict[str, dict] = {}

    async def fake_run_sync(meta, cwd, **kw):
        calls["sync"] = {"meta": meta, "cwd": cwd, **kw}
        return {"ok": True, "_captured": "sync"}

    async def fake_start_async(meta, cwd, **kw):
        calls["async"] = {"meta": meta, "cwd": cwd, **kw}
        return {"ok": True, "_captured": "async"}

    monkeypatch.setattr(server, "_run_sync", fake_run_sync)
    monkeypatch.setattr(server, "_start_async", fake_start_async)
    return calls


def _no_git_preflight(monkeypatch):
    """Let the delegate pair build its spec without a real repo on disk."""
    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", lambda *a, **k: None)


def _specs_equal_modulo_timeout(sync_spec: dict, async_spec: dict) -> None:
    a = {k: v for k, v in sync_spec.items() if k != "timeout_seconds"}
    b = {k: v for k, v in async_spec.items() if k != "timeout_seconds"}
    assert a == b
    # The only hash-affecting difference must be timeout_seconds: with it held equal
    # the idempotency arg hash is identical, so the refactor cannot drift the dedup
    # identity of a pair (beyond the deliberate sync-timeout vs async-deadline gap).
    aligned = dict(async_spec)
    aligned["timeout_seconds"] = sync_spec["timeout_seconds"]
    assert server._arg_hash_for_spec(sync_spec) == server._arg_hash_for_spec(aligned)


# --- spec parity -------------------------------------------------------------


async def test_consult_pair_spec_parity(clean_env, tmp_path, capture_tail):
    kw = dict(workspace_root=str(tmp_path), extra_context="ctx", isolation="inherit")
    await server.codex_consult("q", **kw)
    await server.codex_consult_async("q", **kw)
    _specs_equal_modulo_timeout(capture_tail["sync"]["spec"], capture_tail["async"]["spec"])


async def test_review_pair_spec_parity(clean_env, tmp_path, capture_tail):
    kw = dict(
        scope="branch",
        base="main",
        paths=["a.py"],
        workspace_root=str(tmp_path),
        extra_context="ctx",
        isolation="inherit",
    )
    await server.codex_review_changes(**kw)
    await server.codex_review_changes_async(**kw)
    _specs_equal_modulo_timeout(capture_tail["sync"]["spec"], capture_tail["async"]["spec"])


async def test_delegate_pair_spec_parity(clean_env, tmp_path, monkeypatch, capture_tail):
    _no_git_preflight(monkeypatch)
    kw = dict(workspace_root=str(tmp_path), isolation="inherit")
    await server.codex_delegate("do work", **kw)
    await server.codex_delegate_async("do work", **kw)
    _specs_equal_modulo_timeout(capture_tail["sync"]["spec"], capture_tail["async"]["spec"])


# --- pre-flight error-envelope parity ----------------------------------------


async def test_consult_pair_input_too_large_parity(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    kw = dict(workspace_root=str(tmp_path), extra_context="y" * 2000)
    sync = await server.codex_consult("q", **kw)
    asyncr = await server.codex_consult_async("q", **kw)
    assert sync["error"]["code"] == "input_too_large"
    assert sync["error"] == asyncr["error"]


async def test_delegate_pair_input_too_large_parity(clean_env, tmp_path, monkeypatch):
    _no_git_preflight(monkeypatch)
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    kw = dict(workspace_root=str(tmp_path))
    sync = await server.codex_delegate("t" * 2000, **kw)
    asyncr = await server.codex_delegate_async("t" * 2000, **kw)
    assert sync["error"]["code"] == "input_too_large"
    assert sync["error"] == asyncr["error"]


@pytest.mark.parametrize(
    ("sync_tool", "async_tool", "args"),
    [
        ("codex_consult", "codex_consult_async", ("q",)),
        ("codex_review_changes", "codex_review_changes_async", ()),
        ("codex_delegate", "codex_delegate_async", ("do work",)),
    ],
)
async def test_pair_workspace_error_parity(clean_env, sync_tool, async_tool, args):
    # A relative workspace_root fails resolution before any spend; both variants must
    # report the identical error block.
    sync = await getattr(server, sync_tool)(*args, workspace_root="relative/not/abs")
    asyncr = await getattr(server, async_tool)(*args, workspace_root="relative/not/abs")
    assert sync["error"]["code"] == "invalid_workspace_root"
    assert sync["error"] == asyncr["error"]


# --- competing pre-flight precedence (Codex review of the plan) --------------


async def test_consult_workspace_error_beats_input_too_large(clean_env, monkeypatch):
    # Workspace resolution runs before the input-size check: a bad workspace wins even
    # when the input is also oversized. Pinned for both variants.
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    big = "y" * 2000
    for tool in ("codex_consult", "codex_consult_async"):
        res = await getattr(server, tool)("q", workspace_root="relative", extra_context=big)
        assert res["error"]["code"] == "invalid_workspace_root"


async def test_delegate_placeholder_beats_input_too_large(clean_env, tmp_path, monkeypatch):
    # The env-placeholder guard runs before the task-size check.
    _no_git_preflight(monkeypatch)
    monkeypatch.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    for tool in ("codex_delegate", "codex_delegate_async"):
        res = await getattr(server, tool)("t" * 2000, workspace_root=str(tmp_path))
        assert res["error"]["code"] == "unexpanded_env_placeholder"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("codex_consult", ("q",)),
        ("codex_review_changes", ()),
        ("codex_delegate", ("do work",)),
    ],
)
async def test_sync_tool_uses_one_defaults_snapshot(
    clean_env, tmp_path, monkeypatch, capture_tail, tool, args
):
    # A sync invocation resolves config.defaults() once and threads that single snapshot
    # through preparation, so a request cannot mix a timeout from one snapshot with
    # model/isolation from another (Codex review of #204).
    _no_git_preflight(monkeypatch)
    calls = {"n": 0}
    real_defaults = server.config.defaults

    def counting_defaults():
        calls["n"] += 1
        return real_defaults()

    monkeypatch.setattr(server.config, "defaults", counting_defaults)
    await getattr(server, tool)(*args, workspace_root=str(tmp_path), isolation="inherit")
    assert calls["n"] == 1


async def test_delegate_input_too_large_beats_git_preflight(clean_env, tmp_path, monkeypatch):
    # The task-size check runs before the git preflight: an oversized task is rejected
    # without ever probing the repo (both variants).
    def boom(*a, **k):
        raise AssertionError("git preflight must not run when the task is already too large")

    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", boom)
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    for tool in ("codex_delegate", "codex_delegate_async"):
        res = await getattr(server, tool)("t" * 2000, workspace_root=str(tmp_path))
        assert res["error"]["code"] == "input_too_large"
