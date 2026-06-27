"""Unit tests for delegate._apply_run_meta (rate-limit capture)."""

from __future__ import annotations

from codex_in_claude import codex, rate_limit
from codex_in_claude._core.runtime import CommandRun
from codex_in_claude.schemas import Meta

# events string containing a token_count event with a rate_limits block
_RATE_LIMIT_EVENTS = (
    '{"type":"event_msg","payload":{"type":"token_count",'
    '"rate_limits":{"primary":{"used_percent":10.0,"window_minutes":300,"resets_at":9999999999},'
    '"secondary":{"used_percent":5.0,"window_minutes":10080,"resets_at":9999999999},'
    '"plan_type":"plus"}}}'
)


def _make_meta() -> Meta:
    return Meta(
        cwd="/x",
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        timeout_seconds=180,
        elapsed_ms=0,
    )


def _make_exec_result(
    *, events: str = "", exit_code: int = 0, last_message: str = "ok"
) -> codex.CodexExecResult:
    return codex.CodexExecResult(
        run=CommandRun(events, "", exit_code, 12, exit_code == -9),
        last_message=last_message,
        events=events,
    )


def test_apply_run_meta_attaches_rate_limit(monkeypatch):
    from codex_in_claude import delegate

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(events=_RATE_LIMIT_EVENTS, exit_code=0, last_message="done")
    delegate._apply_run_meta(meta, result)
    assert meta.rate_limit is not None
    assert meta.rate_limit.status == "available"
    assert meta.rate_limit.plan_type == "plus"
    assert meta.rate_limit.source == "current_run"


def test_apply_run_meta_no_rate_limits_block_leaves_none(monkeypatch):
    from codex_in_claude import delegate

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    result = _make_exec_result(events="", exit_code=0, last_message="done")
    delegate._apply_run_meta(meta, result)
    assert meta.rate_limit is None


async def test_run_delegate_not_a_git_repo(tmp_path, monkeypatch):
    """not_a_git_repo error uses new envelope shape with symbolic next_step."""
    from codex_in_claude import delegate
    from codex_in_claude._core import worktree
    from codex_in_claude.schemas import Meta

    meta = Meta(
        cwd=str(tmp_path),
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        timeout_seconds=60,
        elapsed_ms=0,
    )

    def fake_create(*a, **k):
        raise worktree.NotAGitRepoError("not a git repo")

    monkeypatch.setattr(worktree, "create", fake_create)

    result = await delegate.run_delegate(
        "task",
        str(tmp_path),
        meta,
        sandbox="workspace-write",
        isolation="inherit",
        timeout_seconds=60,
        model=None,
        git_timeout=30,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "not_a_git_repo"
    assert result["error"]["repair"]["next_step"] == "init_git_repo"
    assert result["error"]["temporary"] is False
    assert result["error"]["details"]["field"] == "workspace_root"
