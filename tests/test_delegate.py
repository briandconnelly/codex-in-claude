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
    *,
    events: str = "",
    exit_code: int = 0,
    last_message: str = "ok",
    dropped_flags: list[str] | None = None,
) -> codex.CodexExecResult:
    return codex.CodexExecResult(
        run=CommandRun(events, "", exit_code, 12, exit_code == -9),
        last_message=last_message,
        events=events,
        dropped_flags=dropped_flags or [],
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


def test_apply_run_meta_clears_model_when_model_flag_dropped(monkeypatch):
    """When --model is dropped by help-gating, meta.model is reconciled to None so
    the delegate result's provenance matches the default model used (#158)."""
    from codex_in_claude import delegate

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    meta.model = "gpt-5.5"
    result = _make_exec_result(exit_code=0, dropped_flags=["--model"])
    delegate._apply_run_meta(meta, result)
    assert meta.model is None
    assert "--model" in meta.compat_warnings


def test_apply_run_meta_preserves_model_when_not_dropped(monkeypatch):
    """A requested model survives when --model was not dropped (#158)."""
    from codex_in_claude import delegate

    monkeypatch.setattr(rate_limit, "save", lambda *a, **k: None)
    meta = _make_meta()
    meta.model = "gpt-5.5"
    result = _make_exec_result(exit_code=0)
    delegate._apply_run_meta(meta, result)
    assert meta.model == "gpt-5.5"


def test_run_delegate_forwards_on_event(monkeypatch):
    from types import SimpleNamespace

    import anyio

    from codex_in_claude import delegate
    from codex_in_claude._core import worktree

    captured: dict = {}

    def fake_create(*a, **k):
        return SimpleNamespace(path="/tmp/wt", baseline_warning=None)

    async def fake_exec(prompt, **kwargs):
        captured["on_event"] = kwargs.get("on_event")
        return codex.CodexExecResult(run=CommandRun("", "", 0, 1, False), last_message=None)

    monkeypatch.setattr(worktree, "create", fake_create)
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: "")
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(delegate.codex, "run_codex_exec", fake_exec)
    sentinel = lambda _l: None  # noqa: E731
    meta = Meta(
        cwd="/tmp",
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        timeout_seconds=10,
        elapsed_ms=0,
    )
    anyio.run(
        lambda: delegate.run_delegate(
            "task",
            "/tmp",
            meta,
            sandbox="workspace-write",
            isolation="inherit",
            timeout_seconds=10,
            model=None,
            git_timeout=30,
            on_event=sentinel,
        )
    )
    assert captured["on_event"] is sentinel


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
