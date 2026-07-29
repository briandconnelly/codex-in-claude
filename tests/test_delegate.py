"""Unit tests for delegate._apply_run_meta."""

from __future__ import annotations

import os

from codex_in_claude import codex
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


def test_apply_run_meta_leaves_rate_limit_none_even_with_legacy_events(monkeypatch):
    # #321: the exec stream no longer carries quota on codex 0.144, and we no longer scrape
    # it — meta.rate_limit stays None even with a legacy rate_limits block in the events.
    from codex_in_claude import delegate

    meta = _make_meta()
    result = _make_exec_result(events=_RATE_LIMIT_EVENTS, exit_code=0, last_message="done")
    delegate._apply_run_meta(meta, result)
    assert meta.rate_limit is None


def test_apply_run_meta_no_rate_limits_block_leaves_none(monkeypatch):
    from codex_in_claude import delegate

    meta = _make_meta()
    result = _make_exec_result(events="", exit_code=0, last_message="done")
    delegate._apply_run_meta(meta, result)
    assert meta.rate_limit is None


def test_apply_run_meta_clears_model_when_model_flag_dropped(monkeypatch):
    """When --model is dropped by help-gating, meta.model is reconciled to None so
    the delegate result's provenance matches the default model used (#158)."""
    from codex_in_claude import delegate

    meta = _make_meta()
    meta.model = "gpt-5.5"
    result = _make_exec_result(exit_code=0, dropped_flags=["--model"])
    delegate._apply_run_meta(meta, result)
    assert meta.model is None
    assert "--model" in meta.compat_warnings


def test_apply_run_meta_preserves_model_when_not_dropped(monkeypatch):
    """A requested model survives when --model was not dropped (#158)."""
    from codex_in_claude import delegate

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


# --- Reasoning-effort threading (#309) ---------------------------------------------
def test_run_delegate_forwards_reasoning_effort(monkeypatch):
    from types import SimpleNamespace

    import anyio

    from codex_in_claude import delegate
    from codex_in_claude._core import worktree

    captured: dict = {}

    def fake_create(*a, **k):
        return SimpleNamespace(path="/tmp/wt", baseline_warning=None)

    async def fake_exec(prompt, **kwargs):
        captured["reasoning_effort"] = kwargs.get("reasoning_effort")
        return codex.CodexExecResult(run=CommandRun("", "", 0, 1, False), last_message=None)

    monkeypatch.setattr(worktree, "create", fake_create)
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: "")
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(delegate.codex, "run_codex_exec", fake_exec)
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
            reasoning_effort="low",
            git_timeout=30,
        )
    )
    assert captured["reasoning_effort"] == "low"


def test_run_delegate_classifies_effort_rejection(monkeypatch):
    from types import SimpleNamespace

    import anyio

    from codex_in_claude import delegate
    from codex_in_claude._core import worktree

    rejection = (
        '{"type":"error","message":"[ReasoningEffortParam] [reasoning.effort] '
        "[invalid_enum_value] Invalid value: 'bogus'.\"}"
    )

    def fake_create(*a, **k):
        return SimpleNamespace(path="/tmp/wt", baseline_warning=None)

    async def fake_exec(prompt, **kwargs):
        return codex.CodexExecResult(
            run=CommandRun(rejection, "", 1, 1, False), last_message=None, events=rejection
        )

    monkeypatch.setattr(worktree, "create", fake_create)
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: "")
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(delegate.codex, "run_codex_exec", fake_exec)
    meta = Meta(
        cwd="/tmp",
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        timeout_seconds=10,
        elapsed_ms=0,
        reasoning_effort="bogus",
    )
    out = anyio.run(
        lambda: delegate.run_delegate(
            "task",
            "/tmp",
            meta,
            sandbox="workspace-write",
            isolation="inherit",
            timeout_seconds=10,
            model=None,
            reasoning_effort="bogus",
            git_timeout=30,
        )
    )
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_reasoning_effort"


# --- Worktree paths in returned prose (#412) ----------------------------------------
def _run_delegate_with_message(monkeypatch, message: str, *, wt_path: str, diff: str = ""):
    """Drive run_delegate with a canned last_message and worktree path; return the result."""
    from types import SimpleNamespace

    import anyio

    from codex_in_claude import delegate
    from codex_in_claude._core import worktree

    removed: list = []

    async def fake_exec(prompt, **kwargs):
        return codex.CodexExecResult(run=CommandRun("", "", 0, 1, False), last_message=message)

    monkeypatch.setattr(
        worktree, "create", lambda *a, **k: SimpleNamespace(path=wt_path, baseline_warning=None)
    )
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: diff)
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: removed.append(True))
    monkeypatch.setattr(delegate.codex, "run_codex_exec", fake_exec)

    result = anyio.run(
        lambda: delegate.run_delegate(
            "task",
            "/repo",
            _make_meta(),
            sandbox="workspace-write",
            isolation="inherit",
            timeout_seconds=10,
            model=None,
            git_timeout=30,
        )
    )
    return result, removed


def test_run_delegate_relativizes_worktree_paths_in_summary_and_raw(monkeypatch, tmp_path):
    """The worktree is torn down before the caller reads the result, so an absolute path
    into it is dead on arrival (#412). Assert the CONTENT changed — asserting only that
    summary and raw_response.text agree would pass against the bug, since both derive from
    the same last_message."""
    wt = str(tmp_path / "cic-worktree-x" / "tree")
    message = f"Created [f.md]({wt}/f.md).\n\nFull path: `{wt}/f.md`."

    result, removed = _run_delegate_with_message(
        monkeypatch, message, wt_path=wt, diff="diff --git a/f.md b/f.md\n+x\n"
    )

    real = os.path.realpath(wt)
    for field in (result["summary"], result["raw_response"]["text"]):
        assert "./f.md" in field
        assert wt not in field
        assert real not in field
    assert result["summary"] == "Created [f.md](./f.md).\n\nFull path: `./f.md`."
    assert removed, "the worktree must still be torn down"


def test_run_delegate_relativizes_on_the_empty_diff_branch(monkeypatch, tmp_path):
    """The `Codex made no changes.` branch builds its own summary string; the rewrite must
    already have been applied to the text it wraps."""
    wt = str(tmp_path / "cic-worktree-y" / "tree")
    result, _ = _run_delegate_with_message(
        monkeypatch, f"I looked at {wt}/a.py and changed nothing.", wt_path=wt, diff=""
    )
    assert result["summary"] == "Codex made no changes. I looked at ./a.py and changed nothing."
    assert wt not in result["summary"]


def test_run_delegate_preserves_none_last_message(monkeypatch, tmp_path):
    """A successful run with no final message keeps raw_response.text null — the rewrite
    must not coerce None into a string."""
    wt = str(tmp_path / "cic-worktree-z" / "tree")
    result, _ = _run_delegate_with_message(monkeypatch, None, wt_path=wt)
    assert result["raw_response"]["text"] is None
    assert result["summary"] == "Codex made no changes. (codex returned no summary)"
