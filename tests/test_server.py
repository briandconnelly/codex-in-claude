"""Server tool behavior: status, capabilities, consult (mocked codex)."""

from __future__ import annotations

import json
from typing import get_args

import pytest

from codex_in_claude import codex, server
from codex_in_claude._core.runtime import CommandRun
from codex_in_claude.schemas import (
    FINGERPRINT,
    JOB_POLL_AFTER_MS,
    ErrorCode,
    Isolation,
    ReviewScope,
)


def _fake_result(last_message, *, exit_code=0, stderr="", events=""):
    return codex.CodexExecResult(
        run=CommandRun(events, stderr, exit_code, 12, exit_code == -9),
        last_message=last_message,
        events=events,
    )


# --- status / capabilities ---------------------------------------------------
def test_status_ready(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.140.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "auth (ChatGPT)."))
    res = server.codex_status()
    assert res["ok"] is True
    assert res["ready"] is True
    assert res["codex_found"] is True
    assert res["version_supported"] is True


def test_status_not_found(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: None)
    res = server.codex_status()
    assert res["codex_found"] is False
    assert res["ready"] is False


def test_status_not_authenticated(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.140.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (False, "run codex login"))
    res = server.codex_status()
    assert res["ready"] is False
    assert "authenticated" in res["readiness_detail"]


def test_capability_summary_covers_all_task_families():
    """First-read instructions name every task family + prereqs + negative scope (issue #7)."""
    summary = server.CAPABILITY_SUMMARY
    # The server advertises this string to clients as FastMCP `instructions`.
    assert server.mcp.instructions == summary
    for tool in (
        "codex_consult",
        "codex_review_changes",
        "codex_delegate",
        "codex_delegate_async",
        "codex_job_status",  # the codex_job_* lifecycle family (first entry, full name)
        "codex_status",
    ):
        assert tool in summary, tool
    # The job shorthand must use real tool suffixes — `consume_result`, not `consume`
    # (there is no `codex_job_consume`), so an agent never derives a nonexistent name.
    assert "consume_result" in summary
    assert "/consume/" not in summary  # the wrong shorthand
    # Prerequisite + negative scope are stated, not just the tool list.
    low = summary.lower()
    assert "codex_status" in summary and "first" in low  # run codex_status first
    assert "verify" in low  # treat findings as claims to verify
    assert "working tree" in low or "working_tree" in low  # delegate doesn't edit it
    assert "sandbox" in low  # negative scope: no sandbox bypass
    assert "approval" in low  # negative scope: no approval bypass


def test_capabilities_shape():
    res = server.codex_capabilities()
    assert res["ok"] is True
    assert res["name"] == "codex-in-claude"
    assert "codex_consult" in res["active_tools"]
    assert res["fingerprint"] == FINGERPRINT


def test_workspace_write_no_egress_is_documented():
    """The propose-tier no-network constraint of workspace-write is discoverable (issue #24).

    Delegate runs under workspace-write, which blocks network egress; agents must
    not assume write access implies internet access."""
    for doc in (server.codex_delegate.__doc__, server.codex_delegate_async.__doc__):
        assert doc is not None
        assert "network" in doc.lower()
    negative_scope = server.codex_capabilities()["negative_scope"]
    assert any("network" in entry.lower() for entry in negative_scope)


# --- consult: success paths --------------------------------------------------
async def test_consult_structured_success(monkeypatch, clean_env, tmp_path):
    payload = {
        "summary": "Looks fine",
        "verdict": "pass",
        "confidence": "high",
        "findings": [
            {
                "severity": "low",
                "title": "nit",
                "evidence": "x",
                "risk": "minor",
                "recommendation": "tidy",
            }
        ],
        "questions": ["q1"],
    }

    async def fake(*args, **kwargs):
        return _fake_result(
            json.dumps(payload), events='{"type":"token_count","usage":{"input_tokens":4}}'
        )

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_consult("is this ok?", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["tool"] == "codex_consult"
    # Consult is Q&A: a verdict/confidence is meaningless and must not appear (#31).
    assert "verdict" not in res
    assert "confidence" not in res
    assert len(res["findings"]) == 1
    assert res["questions"] == ["q1"]
    assert res["meta"]["tier"] == "consult"
    assert res["meta"]["sandbox"] == "read-only"
    assert res["meta"]["usage"]["input_tokens"] == 4


async def test_consult_plain_text_success(monkeypatch, clean_env, tmp_path):
    async def fake(*args, **kwargs):
        return _fake_result("Just a plain answer, no JSON.")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_consult("question", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert "plain answer" in res["summary"]
    assert "verdict" not in res  # consult carries no verdict (#31)


# --- consult: error paths ----------------------------------------------------
async def test_consult_codex_error(monkeypatch, clean_env, tmp_path):
    async def fake(*args, **kwargs):
        return _fake_result(None, exit_code=1, stderr="not logged in")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "codex_auth_required"


async def test_consult_bad_isolation(clean_env, tmp_path):
    res = await server.codex_consult("q", workspace_root=str(tmp_path), isolation="bogus")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_isolation"
    assert res["error"]["offending_param"] == "isolation"


async def test_consult_invalid_workspace(clean_env):
    res = await server.codex_consult("q", workspace_root="relative/not/abs")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


async def test_consult_input_too_large(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    big = "x" * 2000
    res = await server.codex_consult("q", workspace_root=str(tmp_path), extra_context=big)
    assert res["ok"] is False
    assert res["error"]["code"] == "input_too_large"


async def test_consult_placeholder_env(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "unexpanded_env_placeholder"


# --- review ------------------------------------------------------------------
from codex_in_claude._core import gitdiff  # noqa: E402


def _diff(text="diff --git a/x b/x\n+y", files=1, added=1, removed=0):
    return gitdiff.DiffResult(
        text=text,
        summary=gitdiff.DiffSummary(files_changed=files, lines_added=added, lines_removed=removed),
    )


async def test_review_success(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())

    payload = {
        "summary": "one real bug",
        "verdict": "concerns",
        "confidence": "medium",
        "findings": [
            {
                "severity": "high",
                "title": "off-by-one",
                "file": "x",
                "line": 1,
                "line_end": None,
                "evidence": "loop",
                "risk": "crash",
                "recommendation": "fix bound",
            }
        ],
    }

    async def fake(*a, **k):
        return _fake_result(json.dumps(payload))

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["verdict"] == "concerns"
    assert res["tool"] == "codex_review_changes"
    assert res["meta"]["scope"] == "working_tree"
    assert res["meta"]["context_summary"]["files_changed"] == 1


async def test_review_extra_context_reaches_prompt(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())
    captured = {}

    async def fake(prompt, *a, **k):
        captured["prompt"] = prompt
        return _fake_result(json.dumps({"summary": "ok", "verdict": "pass", "confidence": "high"}))

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_review_changes(
        scope="working_tree",
        workspace_root=str(tmp_path),
        extra_context="I verified git diff --numstat does not invoke textconv.",
    )
    assert res["ok"] is True
    assert "Author-provided context (untrusted data)" in captured["prompt"]
    assert "does not invoke textconv" in captured["prompt"]


async def test_review_extra_context_too_large(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())
    res = await server.codex_review_changes(
        scope="working_tree", workspace_root=str(tmp_path), extra_context="x" * 2000
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "input_too_large"
    assert res["error"]["offending_param"] == "extra_context"


async def test_dry_run_extra_context_grows_prompt_bytes(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())
    base = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    with_ctx = await server.codex_dry_run(
        scope="working_tree", workspace_root=str(tmp_path), extra_context="author intent here"
    )
    assert with_ctx["ok"] is True
    assert with_ctx["prompt_bytes"] > base["prompt_bytes"]


async def test_dry_run_extra_context_too_large(monkeypatch, clean_env, tmp_path):
    # The preview must reject what the real review would reject (issue #6).
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())
    res = await server.codex_dry_run(
        scope="working_tree", workspace_root=str(tmp_path), extra_context="x" * 2000
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "input_too_large"
    assert res["error"]["offending_param"] == "extra_context"


async def test_dry_run_advertises_returnable_error_codes():
    # codex_dry_run can return all of these via its pre-flight checks; capabilities
    # must advertise each (input_too_large from extra_context, the placeholder guard,
    # and the isolation validation).
    caps = server.codex_capabilities()
    dry = next(t for t in caps["tool_details"] if t["name"] == "codex_dry_run")
    assert "input_too_large" in dry["error_codes"]
    assert "unsupported_isolation" in dry["error_codes"]
    assert "unexpanded_env_placeholder" in dry["error_codes"]


def test_isolation_accepting_tools_advertise_unsupported_isolation():
    # Every tool that accepts an `isolation` arg can return unsupported_isolation
    # via _resolve_isolation; capabilities must advertise it for all of them so an
    # agent's recovery branches are complete.
    caps = server.codex_capabilities()
    by_name = {t["name"]: t for t in caps["tool_details"]}
    for name in (
        "codex_consult",
        "codex_review_changes",
        "codex_delegate",
        "codex_delegate_async",
        "codex_dry_run",
        "codex_delegate_dry_run",
    ):
        assert "unsupported_isolation" in by_name[name]["error_codes"], name
        # ...and the param that drives that error is listed, so the summary is
        # internally consistent (param exposed alongside its error).
        assert "isolation" in by_name[name]["key_optional_params"], name


async def test_review_extra_context_advertised_in_capabilities():
    caps = server.codex_capabilities()
    review = next(t for t in caps["tool_details"] if t["name"] == "codex_review_changes")
    assert "extra_context" in review["key_optional_params"]


async def test_review_empty_diff_short_circuits(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff(text="", files=0))
    called = {"n": 0}

    async def fake(*a, **k):
        called["n"] += 1
        return _fake_result("should not run")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["verdict"] == "pass"
    assert called["n"] == 0  # no model call for an empty diff


async def test_review_plain_text_defaults_to_unknown_verdict(monkeypatch, clean_env, tmp_path):
    # When Codex returns a non-JSON message, review still carries verdict/confidence
    # (defaults) — verdict is the review tool's contract, unlike consult (#31).
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())

    async def fake(*a, **k):
        return _fake_result("plain prose, not JSON")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["tool"] == "codex_review_changes"
    assert res["verdict"] == "unknown"
    assert res["confidence"] == "medium"
    assert "plain prose" in res["summary"]


async def test_review_codex_error(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())

    async def fake(*a, **k):
        return _fake_result(None, exit_code=1, stderr="not logged in")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "codex_auth_required"


async def test_review_not_a_git_repo(monkeypatch, clean_env, tmp_path):
    def raise_not_repo(*a, **k):
        raise gitdiff.NotAGitRepoError("not a git repository")

    monkeypatch.setattr(gitdiff, "gather_diff", raise_not_repo)
    res = await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "not_a_git_repo"


async def test_review_invalid_base(monkeypatch, clean_env, tmp_path):
    def raise_base(*a, **k):
        raise gitdiff.InvalidBaseError("bad base")

    monkeypatch.setattr(gitdiff, "gather_diff", raise_base)
    res = await server.codex_review_changes(
        scope="branch", base="-bad", workspace_root=str(tmp_path)
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_base"
    assert res["error"]["offending_param"] == "base"


async def test_review_bad_isolation(clean_env, tmp_path):
    res = await server.codex_review_changes(
        scope="working_tree", workspace_root=str(tmp_path), isolation="nope"
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_isolation"


# --- delegate (propose tier) -------------------------------------------------
from codex_in_claude._core import worktree  # noqa: E402


def _fake_worktree(tmp_path):
    return worktree.Worktree(path=str(tmp_path / "wt"), parent=str(tmp_path / "parent"))


async def test_delegate_success(monkeypatch, clean_env, tmp_path):
    wt = _fake_worktree(tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *a, **k: wt)
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(
        worktree, "capture_diff", lambda *a, **k: "diff --git a/x b/x\n+added line\n"
    )

    removed = {"n": 0}
    monkeypatch.setattr(
        worktree, "remove", lambda *a, **k: removed.__setitem__("n", removed["n"] + 1)
    )

    async def fake(*a, **k):
        return _fake_result("Implemented the change.")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_delegate("add a feature", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["tool"] == "codex_delegate"
    # Delegate returns a diff, not a review judgment: no meaningless verdict (#31).
    assert "verdict" not in res
    assert "confidence" not in res
    assert res["meta"]["tier"] == "propose"
    assert res["meta"]["sandbox"] == "workspace-write"
    assert "added line" in res["diff"]
    assert res["meta"]["context_summary"]["lines_added"] >= 1
    assert removed["n"] == 1  # worktree always cleaned up


async def _delegate_with_diff(monkeypatch, tmp_path, diff):
    """Run codex_delegate with worktree mocked to return `diff`; return the result."""
    wt = _fake_worktree(tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *a, **k: wt)
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: diff)

    async def fake(*a, **k):
        return _fake_result("Implemented the change.")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    return await server.codex_delegate("do work", workspace_root=str(tmp_path))


async def test_delegate_small_diff_not_truncated(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "1000")
    diff = "diff --git a/x b/x\n+small\n"
    res = await _delegate_with_diff(monkeypatch, tmp_path, diff)
    assert res["ok"] is True
    # Returned intact and untruncated. Redaction normalizes the trailing newline
    # (same as the review path), so compare against the rstripped form.
    assert res["diff"] == diff.rstrip("\n")
    assert res["meta"]["truncated"] is False
    assert res["meta"]["truncation_hint"] is None


async def test_delegate_large_diff_truncated(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "1000")
    # Many changed files so the diffstat would be large if computed post-truncation.
    diff = "".join(f"diff --git a/f{i} b/f{i}\n+line {i}\n" for i in range(500))
    res = await _delegate_with_diff(monkeypatch, tmp_path, diff)
    assert res["ok"] is True
    assert res["meta"]["truncated"] is True
    assert res["meta"]["truncation_hint"]
    assert len(res["diff"].encode("utf-8")) <= 1000
    # Diffstat is computed from the FULL diff, not the truncated text.
    assert res["meta"]["context_summary"]["files_changed"] == 500
    assert res["meta"]["context_summary"]["lines_added"] == 500


async def test_delegate_diff_truncation_handles_multibyte(monkeypatch, clean_env, tmp_path):
    # A multibyte character straddling the byte cap must not raise or exceed the cap.
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "1000")
    diff = "diff --git a/x b/x\n+" + ("€" * 1000) + "\n"
    res = await _delegate_with_diff(monkeypatch, tmp_path, diff)
    assert res["ok"] is True
    assert res["meta"]["truncated"] is True
    assert len(res["diff"].encode("utf-8")) <= 1000


async def test_delegate_empty_diff_not_truncated(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "1000")
    res = await _delegate_with_diff(monkeypatch, tmp_path, "")
    assert res["ok"] is True
    assert "diff" not in res or res["diff"] is None
    assert res["meta"]["truncated"] is False
    assert res["summary"].startswith("Codex made no changes.")


_SECRET = "supersecretvalue1234567890"
_SECRET_DIFF = (
    "diff --git a/.env b/.env\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/.env\n"
    f"+API_TOKEN={_SECRET}\n"
    "diff --git a/id_rsa b/id_rsa\n"
    "--- /dev/null\n"
    "+++ b/id_rsa\n"
    "+-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    f'+password = "{_SECRET}"\n'
    "+normal_line = 1\n"
)


async def test_delegate_redacts_secret_files_and_inline_values(monkeypatch, clean_env, tmp_path):
    # Regression for #57: codex_delegate must apply the same secret redaction as the
    # review path before returning the worktree diff to the caller.
    res = await _delegate_with_diff(monkeypatch, tmp_path, _SECRET_DIFF)
    assert res["ok"] is True
    out = res["diff"]
    # No secret-file hunk or inline secret literal survives anywhere in the result.
    assert _SECRET not in out
    assert "BEGIN OPENSSH PRIVATE KEY" not in out
    # Secret-looking files are dropped (headers kept); inline values are replaced.
    assert "[redacted: secret-looking file not sent]" in out
    assert "[redacted: secret value]" in out
    # Non-secret content is preserved.
    assert "normal_line = 1" in out
    # meta lists every redacted path.
    rp = res["meta"]["redacted_paths"]
    assert ".env" in rp and "id_rsa" in rp and "src/app.py" in rp
    # Diffstat reflects the FULL pre-redaction diff (mirrors the review path): all
    # three files are counted even though two were redacted away.
    assert res["meta"]["context_summary"]["files_changed"] == 3


async def test_run_delegate_envelope_redacts_secrets(monkeypatch, clean_env, tmp_path):
    # The background worker serializes exactly run_delegate's returned dict, so this
    # validates the async result envelope (#57) without spawning a subprocess.
    from codex_in_claude import delegate
    from codex_in_claude.schemas import Meta

    wt = _fake_worktree(tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *a, **k: wt)
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: _SECRET_DIFF)

    async def fake(*a, **k):
        return _fake_result("done")

    monkeypatch.setattr(delegate.codex, "run_codex_exec", fake)
    meta = Meta(
        cwd=str(tmp_path),
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        model=None,
        timeout_seconds=60,
        elapsed_ms=0,
    )
    res = await delegate.run_delegate(
        "do x",
        str(tmp_path),
        meta,
        sandbox="workspace-write",
        isolation="inherit",
        timeout_seconds=60,
        model=None,
        git_timeout=30,
    )
    assert res["ok"] is True
    assert _SECRET not in res["diff"]
    assert "BEGIN OPENSSH PRIVATE KEY" not in res["diff"]
    assert {".env", "id_rsa", "src/app.py"} <= set(res["meta"]["redacted_paths"])


async def test_delegate_cleans_up_on_codex_error(monkeypatch, clean_env, tmp_path):
    wt = _fake_worktree(tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *a, **k: wt)
    removed = {"n": 0}
    monkeypatch.setattr(
        worktree, "remove", lambda *a, **k: removed.__setitem__("n", removed["n"] + 1)
    )

    async def fake(*a, **k):
        return _fake_result(None, exit_code=1, stderr="not logged in")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_delegate("do it", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "codex_auth_required"
    assert removed["n"] == 1  # cleanup still happened


async def test_run_delegate_reports_worktree_parent(monkeypatch, clean_env, tmp_path):
    # run_delegate forwards the on_worktree_parent hook to worktree.create so the
    # background worker can record the temp dir for cleanup before codex runs.
    from codex_in_claude import delegate
    from codex_in_claude.schemas import Meta

    wt = _fake_worktree(tmp_path)

    def fake_create(repo, *, timeout, on_parent=None):
        if on_parent is not None:
            on_parent(wt.parent)
        return wt

    monkeypatch.setattr(worktree, "create", fake_create)
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: "")

    async def fake(*a, **k):
        return _fake_result("done")

    monkeypatch.setattr(delegate.codex, "run_codex_exec", fake)

    seen: list[str] = []
    meta = Meta(
        cwd=str(tmp_path),
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        model=None,
        timeout_seconds=60,
        elapsed_ms=0,
    )
    await delegate.run_delegate(
        "do x",
        str(tmp_path),
        meta,
        sandbox="workspace-write",
        isolation="inherit",
        timeout_seconds=60,
        model=None,
        git_timeout=30,
        on_worktree_parent=seen.append,
    )
    assert seen == [wt.parent]


@pytest.mark.parametrize("bad_cap", [0, -5, "nope", 12.5])
async def test_run_delegate_invalid_cap_falls_back_to_default(
    monkeypatch, clean_env, tmp_path, bad_cap
):
    # A corrupt/legacy job spec could carry a non-positive or non-int cap. run_delegate
    # must ignore it and use the configured (floored) default rather than slicing with a
    # bad bound (negative slice / TypeError).
    from codex_in_claude import delegate
    from codex_in_claude.schemas import Meta

    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_DELEGATE_DIFF_BYTES", "1000")
    wt = _fake_worktree(tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *a, **k: wt)
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    diff = "".join(f"diff --git a/f{i} b/f{i}\n+line {i}\n" for i in range(500))
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: diff)

    async def fake(*a, **k):
        return _fake_result("done")

    monkeypatch.setattr(delegate.codex, "run_codex_exec", fake)
    meta = Meta(
        cwd=str(tmp_path),
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        model=None,
        timeout_seconds=60,
        elapsed_ms=0,
    )
    res = await delegate.run_delegate(
        "do x",
        str(tmp_path),
        meta,
        sandbox="workspace-write",
        isolation="inherit",
        timeout_seconds=60,
        model=None,
        git_timeout=30,
        max_diff_bytes=bad_cap,
    )
    assert res["ok"] is True
    # Fell back to the configured 1000-byte default: bounded, signaled, no crash.
    assert res["meta"]["truncated"] is True
    assert len(res["diff"].encode("utf-8")) <= 1000


async def test_delegate_redacts_secret_in_free_text(monkeypatch, clean_env, tmp_path):
    # #58: a secret Codex echoes in its prose summary / raw_response must be redacted
    # even when it never appears in a diff (delegate returns plain prose).
    wt = _fake_worktree(tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *a, **k: wt)
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: "")

    async def fake(*a, **k):
        return _fake_result(f'I read config and found password = "{_SECRET}" there.')

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_delegate("inspect config", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert _SECRET not in res["summary"]
    assert _SECRET not in (res["raw_response"]["text"] or "")
    assert "[redacted: secret value]" in res["summary"]


async def test_consult_redacts_secret_in_free_text(monkeypatch, clean_env, tmp_path):
    # #58: structured free-text (summary, finding evidence) is redacted before return.
    payload = {
        "summary": f"The token is ghp_{'a' * 36}.",
        "findings": [
            {
                "severity": "low",
                "title": "leak",
                "evidence": f'password = "{_SECRET}"',
                "risk": "exposure",
                "recommendation": "rotate",
            }
        ],
    }

    async def fake(*a, **k):
        return _fake_result(json.dumps(payload))

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_consult("any secrets?", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert "ghp_" + "a" * 36 not in res["summary"]
    assert _SECRET not in res["findings"][0]["evidence"]
    assert "[redacted: secret value]" in res["findings"][0]["evidence"]
    # raw_response.text is the unparsed JSON (escaped quotes) — also an acceptance surface.
    assert _SECRET not in (res["raw_response"]["text"] or "")
    assert "ghp_" + "a" * 36 not in (res["raw_response"]["text"] or "")


async def test_review_redacts_secret_in_free_text(monkeypatch, clean_env, tmp_path):
    # #58: review summary free-text is redacted before return.
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())
    payload = {
        "summary": f'Found AKIAIOSFODNN7EXAMPLE and password = "{_SECRET}" in the diff.',
        "verdict": "concerns",
        "confidence": "high",
    }

    async def fake(*a, **k):
        return _fake_result(json.dumps(payload))

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert _SECRET not in res["summary"]
    assert "AKIAIOSFODNN7EXAMPLE" not in res["summary"]
    assert _SECRET not in (res["raw_response"]["text"] or "")
    assert res["verdict"] == "concerns"


async def test_delegate_not_a_git_repo(monkeypatch, clean_env, tmp_path):
    def boom(*a, **k):
        raise worktree.NotAGitRepoError("not a git repo")

    monkeypatch.setattr(worktree, "create", boom)
    res = await server.codex_delegate("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "not_a_git_repo"


async def test_delegate_no_commits(monkeypatch, clean_env, tmp_path):
    def boom(*a, **k):
        raise worktree.NoCommitsError("no commits")

    monkeypatch.setattr(worktree, "create", boom)
    res = await server.codex_delegate("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "worktree_error"


async def test_delegate_bad_isolation(clean_env, tmp_path):
    res = await server.codex_delegate("x", workspace_root=str(tmp_path), isolation="nope")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_isolation"


async def test_delegate_input_too_large(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    res = await server.codex_delegate("z" * 2000, workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "input_too_large"


async def test_delegate_baseline_commit_failure_no_spend(monkeypatch, clean_env, tmp_path):
    # Regression for issue #4: if the baseline commit fails after the live patch
    # applies, delegate must fail with worktree_error BEFORE calling Codex, so the
    # caller's pre-existing changes are never returned as Codex's diff.
    import subprocess

    def g(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

    g("init", "-q")
    g("config", "user.email", "t@t.co")
    g("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n")
    g("add", "-A")
    g("commit", "-qm", "init")
    (tmp_path / "a.py").write_text("x = 999  # pre-existing live edit\n")

    real_git = worktree._git

    def fake_git(repo, args, timeout):
        if "commit" in args:
            return subprocess.CompletedProcess(["git", *args], 1, "", "simulated commit failure")
        return real_git(repo, args, timeout)

    monkeypatch.setattr(worktree, "_git", fake_git)

    called = {"codex": False}

    async def must_not_run(*a, **k):
        called["codex"] = True
        return _fake_result("should not happen")

    monkeypatch.setattr(server.codex, "run_codex_exec", must_not_run)

    res = await server.codex_delegate("do something", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "worktree_error"
    assert "diff" not in res  # no diff returned at all → nothing to misattribute
    assert called["codex"] is False  # failed before spending


async def test_delegate_baseline_warning_surfaced(monkeypatch, clean_env, tmp_path):
    wt = worktree.Worktree(
        path=str(tmp_path / "wt"), parent=str(tmp_path / "p"), baseline_warning="seed failed"
    )
    monkeypatch.setattr(worktree, "create", lambda *a, **k: wt)
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: "")

    async def fake(*a, **k):
        return _fake_result("done")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_delegate("x", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert "seed failed" in res["meta"]["security_warnings"]
    assert res["summary"].startswith("Codex made no changes")


# --- dry_run -----------------------------------------------------------------
async def test_dry_run_preview(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(
        gitdiff,
        "gather_diff",
        lambda *a, **k: gitdiff.DiffResult(
            text="diff --git a/x b/x\n+y",
            summary=gitdiff.DiffSummary(1, 1, 0),
            redacted_paths=[".env"],
        ),
    )
    res = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["tool"] == "codex_dry_run"
    assert res["context_summary"]["files_changed"] == 1
    assert res["prompt_bytes"] > 0
    assert res["redacted_paths_count"] == 1


async def test_dry_run_git_error(monkeypatch, clean_env, tmp_path):
    def boom(*a, **k):
        raise gitdiff.NotAGitRepoError("nope")

    monkeypatch.setattr(gitdiff, "gather_diff", boom)
    res = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "not_a_git_repo"


async def test_dry_run_invalid_workspace(clean_env):
    res = await server.codex_dry_run(scope="working_tree", workspace_root="relative")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


async def test_dry_run_bad_isolation(clean_env, tmp_path):
    """Invalid isolation errors like the active tools, not a silent normalize (issue #6)."""
    res = await server.codex_dry_run(workspace_root=str(tmp_path), isolation="nope")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_isolation"
    assert res["error"]["offending_param"] == "isolation"


async def test_dry_run_placeholder_env(monkeypatch, clean_env, tmp_path):
    """A dry run must surface the same unexpanded_env_placeholder a review would
    hit before gathering the diff (issue #46), not green-light it."""
    monkeypatch.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    res = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "unexpanded_env_placeholder"


async def test_dry_run_placeholder_error_meta_carries_paths(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    res = await server.codex_dry_run(
        scope="working_tree", workspace_root=str(tmp_path), paths=["a/b.py"]
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "unexpanded_env_placeholder"
    assert res["meta"]["paths"] == ["a/b.py"]


# --- delegate_dry_run --------------------------------------------------------
def _init_repo(tmp_path):
    import subprocess

    def g(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

    g("init", "-q")
    g("config", "user.email", "t@t.co")
    g("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n")
    g("add", "-A")
    g("commit", "-qm", "init")
    return tmp_path


async def test_delegate_dry_run_preview(monkeypatch, clean_env, tmp_path):
    _init_repo(tmp_path)

    def no_create(*a, **k):  # a dry run must never create a worktree or spend
        raise AssertionError("delegate dry run must not create a worktree")

    monkeypatch.setattr(worktree, "create", no_create)
    res = await server.codex_delegate_dry_run("add a feature", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["tool"] == "codex_delegate_dry_run"
    assert res["tier"] == "propose"
    assert res["sandbox"] == "workspace-write"
    assert res["prompt_bytes"] > 0
    plan = res["worktree_plan"]
    assert plan["tracked_files"] == 1
    assert plan["uncommitted_tracked_files"] == 0
    assert plan["untracked_files"] == 0
    assert plan["head_subject"] == "init"
    assert plan["note"]  # caveat is always present


async def test_delegate_dry_run_not_a_git_repo(clean_env, tmp_path):
    res = await server.codex_delegate_dry_run("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "not_a_git_repo"


async def test_delegate_dry_run_no_commits(clean_env, tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    res = await server.codex_delegate_dry_run("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "worktree_error"


async def test_delegate_dry_run_bad_isolation(clean_env, tmp_path):
    res = await server.codex_delegate_dry_run("x", workspace_root=str(tmp_path), isolation="nope")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_isolation"


async def test_delegate_dry_run_invalid_workspace(clean_env):
    res = await server.codex_delegate_dry_run("x", workspace_root="relative/path")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


async def test_delegate_dry_run_input_too_large(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    res = await server.codex_delegate_dry_run("z" * 2000, workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "input_too_large"


async def test_delegate_dry_run_placeholder_env(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    res = await server.codex_delegate_dry_run("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "unexpanded_env_placeholder"


def test_diffstat_counts():
    diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n+added\n-removed\n unchanged\n"
    summary = server._diffstat(diff)
    assert summary.files_changed == 1
    assert summary.lines_added == 1
    assert summary.lines_removed == 1


async def test_delegate_invalid_workspace(clean_env):
    res = await server.codex_delegate("x", workspace_root="relative/path")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


async def test_delegate_placeholder_env(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    res = await server.codex_delegate("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "unexpanded_env_placeholder"


async def test_delegate_capture_diff_error(monkeypatch, clean_env, tmp_path):
    wt = _fake_worktree(tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *a, **k: wt)
    removed = {"n": 0}
    monkeypatch.setattr(
        worktree, "remove", lambda *a, **k: removed.__setitem__("n", removed["n"] + 1)
    )

    def boom(*a, **k):
        raise worktree.WorktreeError("capture failed")

    monkeypatch.setattr(worktree, "capture_diff", boom)

    async def fake(*a, **k):
        return _fake_result("done")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await server.codex_delegate("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "worktree_error"
    assert removed["n"] == 1


# --- async delegate + job lifecycle ------------------------------------------
class _FakeStore:
    """In-memory stand-in for JobStore used by the async/lifecycle tool tests."""

    def __init__(self, *, status_dict="__unset__", record=None, result_json=None):
        self._status = status_dict
        self._record = record
        self._result_json = result_json
        self.poll_after_ms = JOB_POLL_AFTER_MS  # base for the job_running backoff hint
        self.started = []
        self.cancelled = []
        self.consumed = []

    def start(self, cmd_factory, cwd, *, kind, extra=None, write_spec=None):
        import pathlib

        cmd = cmd_factory(pathlib.Path(cwd) / "job")
        self.started.append({"cmd": cmd, "cwd": cwd, "kind": kind, "spec": write_spec})
        return "job-abc", "2026-06-17T00:00:00+00:00"

    def status(self, cwd, job_id):
        if self._status == "__unset__":
            return self._record
        return self._status

    def result_payload(self, cwd, job_id, *, consume):
        if consume:
            self.consumed.append(job_id)
        return self._record, self._result_json

    def cancel(self, cwd, job_id):
        self.cancelled.append(job_id)
        return self._record

    def list_jobs(self, cwd):
        return [self._record] if self._record else []


def _ok_record(status="done"):
    return {
        "job_id": "job-abc",
        "kind": "codex_delegate",
        "status": status,
        "started_at": "2026-06-17T00:00:00+00:00",
        "started_epoch": 1.0,
        "elapsed_ms": 5,
        "deadline_seconds": 1800,
        "completed_epoch": 2.0,
        "expires_at": "2026-06-18T00:00:00+00:00",
        "result_available": status == "done",
        "poll_after_ms": 1000,
        "ttl_seconds": 86400,
        "extra": {},
    }


def _done_envelope():
    meta = server._base_meta(
        "/repo",
        "param",
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        model=None,
        timeout_seconds=1800,
    ).model_dump(mode="json")
    return {"ok": True, "tool": "codex_delegate", "summary": "did it", "diff": "d", "meta": meta}


async def test_delegate_async_returns_job_id(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", lambda *a, **k: None)
    store = _FakeStore()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_delegate_async("do x", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["job_id"] == "job-abc"
    assert res["kind"] == "codex_delegate"
    assert res["status"] == "running"
    # the spawned command targets the worker module
    assert "codex_in_claude._worker" in store.started[0]["cmd"]
    assert store.started[0]["spec"]["task"] == "do x"
    # The diff cap is snapshotted into the spec so the worker bounds its diff too.
    assert store.started[0]["spec"]["max_diff_bytes"] == server.config.max_delegate_diff_bytes()


async def test_delegate_async_not_a_git_repo(monkeypatch, clean_env, tmp_path):
    def boom(*a, **k):
        raise server.worktree.NotAGitRepoError("nope")

    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", boom)
    res = await server.codex_delegate_async("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "not_a_git_repo"


async def test_delegate_async_no_commits(monkeypatch, clean_env, tmp_path):
    def boom(*a, **k):
        raise server.worktree.NoCommitsError("no commits")

    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", boom)
    res = await server.codex_delegate_async("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "worktree_error"


async def test_delegate_async_bad_isolation(clean_env, tmp_path):
    res = await server.codex_delegate_async("x", workspace_root=str(tmp_path), isolation="nope")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_isolation"


async def test_delegate_async_invalid_workspace(clean_env):
    res = await server.codex_delegate_async("x", workspace_root="relative/path")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


async def test_delegate_async_input_too_large(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", lambda *a, **k: None)
    res = await server.codex_delegate_async("z" * 2000, workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "input_too_large"


async def test_job_status_done(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(status_dict=_ok_record("done"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_status("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["job_id"] == "job-abc"
    assert res["status"] == "done"
    assert res["result_available"] is True


async def test_job_status_not_found(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(status_dict=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_status("nope", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "job_not_found"


async def test_job_status_invalid_workspace(clean_env):
    res = await server.codex_job_status("x", workspace_root="relative")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


async def test_job_result_done_patches_job_id(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("done"), result_json=_done_envelope())
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["meta"]["job_id"] == "job-abc"
    assert res["summary"] == "did it"


async def test_job_result_strips_legacy_verdict_fields(monkeypatch, clean_env, tmp_path):
    # A payload written by a pre-#31 worker may still carry verdict/confidence; the
    # result tools must drop them so the returned envelope matches DelegateResult.
    legacy = _done_envelope()
    legacy["verdict"] = "unknown"
    legacy["confidence"] = "medium"
    legacy["meta"]["fingerprint"] = "codex-in-claude/0.1/schema-0"  # a pre-upgrade worker
    store = _FakeStore(record=_ok_record("done"), result_json=legacy)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert "verdict" not in res
    assert "confidence" not in res
    # The normalized payload is stamped with the current surface fingerprint.
    assert res["meta"]["fingerprint"] == FINGERPRINT


async def test_job_result_running_maps_error(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("running"), result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "job_running"
    assert res["error"]["retryable"] is True


async def test_job_result_timeout_maps_error(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("timeout"), result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_timeout"


async def test_job_result_done_but_missing_payload(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("done"), result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "job_failed"


async def test_job_result_not_found(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=None, result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("nope", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_not_found"


async def test_job_consume_result_passes_consume(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("done"), result_json=_done_envelope())
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_consume_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert store.consumed == ["job-abc"]


async def test_job_cancel(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("cancelled"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_cancel("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["status"] == "cancelled"
    assert store.cancelled == ["job-abc"]


async def test_job_cancel_not_found(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_cancel("nope", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_not_found"


async def test_job_list(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("done"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_list(workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert len(res["jobs"]) == 1
    assert res["jobs"][0]["job_id"] == "job-abc"


async def test_job_list_invalid_workspace(clean_env):
    res = await server.codex_job_list(workspace_root="relative")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


def test_capabilities_lists_m4_tools():
    caps = server.codex_capabilities()
    assert "codex_delegate_async" in caps["active_tools"]
    for t in (
        "codex_job_status",
        "codex_job_result",
        "codex_job_consume_result",
        "codex_job_cancel",
        "codex_job_list",
    ):
        assert t in caps["free_tools"]


def test_fingerprint_is_schema_1():
    assert FINGERPRINT == "codex-in-claude/0.1/schema-1"


# --- async consult / review (#41) --------------------------------------------
async def test_consult_async_returns_job_id(monkeypatch, clean_env, tmp_path):
    store = _FakeStore()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult_async(
        "why?", workspace_root=str(tmp_path), extra_context="ctx"
    )
    assert res["ok"] is True
    assert res["job_id"] == "job-abc"
    assert res["kind"] == "codex_consult"
    spec = store.started[0]["spec"]
    assert spec["kind"] == "codex_consult"
    assert spec["question"] == "why?"
    assert spec["extra_context"] == "ctx"
    assert spec["sandbox"] == "read-only"
    assert spec["tier"] == "consult"


async def test_consult_async_bad_isolation(clean_env, tmp_path):
    res = await server.codex_consult_async("q", workspace_root=str(tmp_path), isolation="nope")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_isolation"


async def test_consult_async_invalid_workspace(clean_env):
    res = await server.codex_consult_async("q", workspace_root="relative")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


async def test_consult_async_input_too_large(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    res = await server.codex_consult_async(
        "q", workspace_root=str(tmp_path), extra_context="z" * 2000
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "input_too_large"
    assert res["error"]["offending_param"] == "extra_context"


async def test_consult_async_placeholder_env(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MODEL", "${MODEL}")
    res = await server.codex_consult_async("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "unexpanded_env_placeholder"


async def test_review_async_returns_job_id(monkeypatch, clean_env, tmp_path):
    store = _FakeStore()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_review_changes_async(
        scope="branch", base="main", workspace_root=str(tmp_path)
    )
    assert res["ok"] is True
    assert res["kind"] == "codex_review_changes"
    spec = store.started[0]["spec"]
    assert spec["kind"] == "codex_review_changes"
    assert spec["scope"] == "branch"
    assert spec["base"] == "main"
    assert spec["sandbox"] == "read-only"
    # The diff is gathered in the worker, so the byte cap is snapshotted into the spec.
    assert spec["max_bytes"] == server.config.max_input_bytes()


async def test_review_async_threads_extra_context(monkeypatch, clean_env, tmp_path):
    # review_async mirrors the sync tool's extra_context, carried to the worker via spec.
    store = _FakeStore()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_review_changes_async(
        workspace_root=str(tmp_path), extra_context="author intent"
    )
    assert res["ok"] is True
    assert store.started[0]["spec"]["extra_context"] == "author intent"


async def test_review_async_bad_isolation(clean_env, tmp_path):
    res = await server.codex_review_changes_async(workspace_root=str(tmp_path), isolation="nope")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_isolation"


async def test_review_async_invalid_workspace(clean_env):
    res = await server.codex_review_changes_async(workspace_root="relative")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"


def _done_consult_envelope():
    meta = server._base_meta(
        "/repo",
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=1800,
    ).model_dump(mode="json")
    return {"ok": True, "tool": "codex_consult", "summary": "answer", "meta": meta}


def _done_review_envelope():
    meta = server._base_meta(
        "/repo",
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=1800,
    ).model_dump(mode="json")
    return {
        "ok": True,
        "tool": "codex_review_changes",
        "summary": "looks ok",
        "verdict": "pass",
        "confidence": "high",
        "meta": meta,
    }


async def test_job_result_consult_kind_returns_consult_envelope(monkeypatch, clean_env, tmp_path):
    rec = _ok_record("done")
    rec["kind"] = "codex_consult"
    store = _FakeStore(record=rec, result_json=_done_consult_envelope())
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["tool"] == "codex_consult"
    assert res["summary"] == "answer"
    assert "verdict" not in res  # consult carries none, and we must not inject it


async def test_job_result_review_kind_keeps_verdict(monkeypatch, clean_env, tmp_path):
    rec = _ok_record("done")
    rec["kind"] = "codex_review_changes"
    store = _FakeStore(record=rec, result_json=_done_review_envelope())
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["tool"] == "codex_review_changes"
    assert res["verdict"] == "pass"  # review keeps its verdict (not stripped like delegate)


async def test_job_result_unknown_kind_is_internal_error(monkeypatch, clean_env, tmp_path):
    rec = _ok_record("done")
    rec["kind"] = "codex_bogus"
    store = _FakeStore(record=rec, result_json=_done_consult_envelope())
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


async def test_job_result_schema_mismatch_is_internal_error(monkeypatch, clean_env, tmp_path):
    # A consult-kind job whose stored payload is actually a review envelope (verdict)
    # must not be passed through — ConsultResult forbids verdict, so validation fails.
    rec = _ok_record("done")
    rec["kind"] = "codex_consult"
    store = _FakeStore(record=rec, result_json=_done_review_envelope())
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


async def test_job_result_malformed_error_payload_is_internal_error(
    monkeypatch, clean_env, tmp_path
):
    # A done job whose stored ok:false payload is malformed (e.g. truncated on disk)
    # must surface as internal_error, not leak a wrong-shaped envelope.
    rec = _ok_record("done")
    store = _FakeStore(record=rec, result_json={"ok": False, "error": "not-an-object"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


async def test_job_result_running_consult_reports_consult_meta(monkeypatch, clean_env, tmp_path):
    # A running consult job's error envelope must report its real tier/sandbox, not
    # the propose default used for delegate jobs.
    rec = _ok_record("running")
    rec["kind"] = "codex_consult"
    store = _FakeStore(record=rec, result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "job_running"
    assert res["meta"]["tier"] == "consult"
    assert res["meta"]["sandbox"] == "read-only"


def test_capabilities_lists_async_readonly_tools():
    caps = server.codex_capabilities()
    assert "codex_consult_async" in caps["active_tools"]
    assert "codex_review_changes_async" in caps["active_tools"]
    names = {t["name"] for t in caps["tool_details"]}
    assert {"codex_consult_async", "codex_review_changes_async"} <= names


def test_review_tools_advertise_isolation_param_and_error():
    # Both review tools accept `isolation` and can return unsupported_isolation, so
    # capabilities must advertise the param and the code for each.
    caps = server.codex_capabilities()
    by_name = {t["name"]: t for t in caps["tool_details"]}
    for name in ("codex_review_changes", "codex_review_changes_async"):
        assert "isolation" in by_name[name]["key_optional_params"], name
        assert "unsupported_isolation" in by_name[name]["error_codes"], name


def test_capabilities_lists_delegate_dry_run():
    caps = server.codex_capabilities()
    assert "codex_delegate_dry_run" in caps["free_tools"]
    details = {t["name"]: t for t in caps["tool_details"]}
    assert details["codex_delegate_dry_run"]["cost"] == "free"


def _param_enum(param_schema: dict) -> list | None:
    """Pull the enum out of a tool param schema, tolerating the nullable anyOf form."""
    if "enum" in param_schema:
        return param_schema["enum"]
    for branch in param_schema.get("anyOf", []):
        if "enum" in branch:
            return branch["enum"]
    return None


@pytest.mark.parametrize(
    ("tool_name", "param", "expected"),
    [
        ("codex_review_changes", "scope", list(get_args(ReviewScope))),
        ("codex_review_changes", "isolation", list(get_args(Isolation))),
        ("codex_dry_run", "scope", list(get_args(ReviewScope))),
        ("codex_dry_run", "isolation", list(get_args(Isolation))),
        ("codex_delegate", "isolation", list(get_args(Isolation))),
        ("codex_delegate_async", "isolation", list(get_args(Isolation))),
        ("codex_consult", "isolation", list(get_args(Isolation))),
    ],
)
async def test_fixed_value_params_advertise_enum(tool_name, param, expected):
    """Fixed-value params surface their allowed values as schema enums (issue #5)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    props = tools[tool_name].parameters["properties"]
    enum = _param_enum(props[param])
    # `enum` is a set semantically; assert membership, not order (which isn't
    # part of the MCP contract and may vary across Pydantic/FastMCP versions).
    assert enum is not None, f"{tool_name}.{param} schema exposes no enum"
    assert set(enum) == set(expected)


async def test_isolation_error_lists_allowed_values(clean_env, tmp_path):
    """unsupported_isolation surfaces the valid set as machine-readable allowed_values."""
    res = await server.codex_consult("q", workspace_root=str(tmp_path), isolation="bogus")
    assert res["error"]["code"] == "unsupported_isolation"
    assert res["error"]["allowed_values"] == list(get_args(Isolation))


async def test_scope_error_lists_allowed_values(monkeypatch, clean_env, tmp_path):
    """invalid_scope surfaces the valid review scopes as allowed_values."""

    def raise_scope(*a, **k):
        raise gitdiff.InvalidScopeError("bad scope")

    monkeypatch.setattr(gitdiff, "gather_diff", raise_scope)
    res = await server.codex_review_changes(scope="nope", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "invalid_scope"
    assert res["error"]["offending_param"] == "scope"
    assert res["error"]["allowed_values"] == list(get_args(ReviewScope))


async def test_job_running_error_is_actionable(monkeypatch, clean_env, tmp_path):
    """job_running points at the recovery tool with concrete params and a backoff."""
    store = _FakeStore(record=_ok_record("running"), result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    err = res["error"]
    assert err["code"] == "job_running"
    assert err["retryable"] is True
    assert err["repair_tool"] == "codex_job_status"
    # repair params carry both the job_id AND the caller's workspace_root, so the
    # poll targets the same workspace rather than risking a wrong-workspace miss.
    assert err["repair_tool_params"] == {"job_id": "job-abc", "workspace_root": str(tmp_path)}
    assert err["retry_after_ms"] == JOB_POLL_AFTER_MS


async def test_job_running_retry_after_echoes_record_poll_hint(monkeypatch, clean_env, tmp_path):
    # job_result on a running job suggests the same backed-off retry the status record
    # already computed (the growing poll hint), not a separately recomputed value.
    rec = _ok_record("running")
    rec["poll_after_ms"] = 6000  # the store's grown backoff for a long-running job
    store = _FakeStore(record=rec, result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_running"
    assert res["error"]["retry_after_ms"] == 6000  # echoed from the record's poll_after_ms


async def test_job_running_repair_omits_workspace_when_not_given(monkeypatch, clean_env, tmp_path):
    """With no explicit workspace_root, the repair params don't fabricate one."""
    store = _FakeStore(record=_ok_record("running"), result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    monkeypatch.setattr(server.workspace, "server_cwd", lambda: str(tmp_path))
    res = await server.codex_job_result("job-abc")
    assert res["error"]["repair_tool_params"] == {"job_id": "job-abc"}


async def test_job_not_found_points_at_list(monkeypatch, clean_env, tmp_path):
    """job_not_found names codex_job_list as the way to recover known job_ids."""
    store = _FakeStore(record=None, result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("missing", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_not_found"
    assert res["error"]["offending_param"] == "job_id"
    assert res["error"]["repair_tool"] == "codex_job_list"
    # codex_job_list takes only workspace_root (not job_id) — echo it so the
    # recovery lists jobs in the same workspace the lookup used.
    assert res["error"]["repair_tool_params"] == {"workspace_root": str(tmp_path)}


def test_job_poll_interval_has_single_source():
    """The agent-visible JOB_POLL_AFTER_MS is the _core default, so a live job
    record's poll_after_ms and the job_running retry_after_ms can't drift."""
    from codex_in_claude._core import jobs

    assert JOB_POLL_AFTER_MS == jobs.DEFAULT_POLL_AFTER_MS
    assert jobs.JobStore.__dataclass_fields__["poll_after_ms"].default == JOB_POLL_AFTER_MS


async def test_capabilities_list_error_codes_per_tool():
    """Each tool capability declares the (advisory) error codes it may return."""
    caps = server.codex_capabilities()
    details = {t["name"]: t for t in caps["tool_details"]}
    # error_codes is injected only into tool_details, so every advertised tool must
    # have a detail row or its codes never reach the output.
    assert set(details) == set(caps["active_tools"]) | set(caps["free_tools"])
    valid_codes = set(get_args(ErrorCode))
    for tool in details.values():
        assert "error_codes" in tool
        assert set(tool["error_codes"]) <= valid_codes, tool["name"]
    assert "unsupported_isolation" in details["codex_consult"]["error_codes"]
    assert "invalid_scope" in details["codex_review_changes"]["error_codes"]
    assert "job_running" in details["codex_job_result"]["error_codes"]


@pytest.mark.parametrize(
    ("tool_name", "read_only", "idempotent"),
    [
        ("codex_job_status", True, True),
        ("codex_job_result", True, True),
        ("codex_job_list", True, True),
        ("codex_job_consume_result", False, False),
        ("codex_job_cancel", False, False),
    ],
)
async def test_job_lifecycle_annotations_split_read_from_mutation(tool_name, read_only, idempotent):
    """Read/inspect job tools are read-only+idempotent; consume/cancel are mutating (issue #9)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    ann = tools[tool_name].annotations
    assert ann.readOnlyHint is read_only
    assert ann.idempotentHint is idempotent
    # Every job tool is local (closed-world) and touches only this server's job
    # state, never the user's files/repo, so it's non-destructive.
    assert ann.openWorldHint is False
    assert ann.destructiveHint is False


def test_job_status_model_surfaces_cleanup_warnings():
    data = {
        "job_id": "abc",
        "kind": "codex_delegate",
        "status": "cancelled",
        "started_at": "2026-01-01T00:00:00+00:00",
        "started_epoch": 0.0,
        "elapsed_ms": 5,
        "deadline_seconds": 60,
        "completed_epoch": 1.0,
        "expires_at": None,
        "result_available": False,
        "poll_after_ms": 1000,
        "ttl_seconds": 3600,
        "cleanup_warnings": ["could not remove temporary path: /tmp/cic-worktree-x"],
        "extra": {},
    }
    model = server._job_status_model(data)
    assert model.cleanup_warnings == ["could not remove temporary path: /tmp/cic-worktree-x"]


# --- boundary: unexpected exceptions become a structured internal_error (#39) ---
async def test_consult_unexpected_exception_returns_internal_error(
    monkeypatch, clean_env, tmp_path
):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(server.codex, "run_codex_exec", boom)
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert res["error"]["retryable"] is True
    # The documented envelope still holds: meta is present and tier reflects the tool.
    assert res["meta"]["tier"] == "consult"
    assert res["meta"]["sandbox"] == "read-only"


async def test_review_unexpected_exception_returns_internal_error(monkeypatch, clean_env, tmp_path):
    # An unexpected exception escaping the review orchestration must be caught by the
    # tool boundary and become a structured internal_error (not an opaque error).
    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(server.orchestration, "run_review", boom)
    res = await server.codex_review_changes(workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


async def test_delegate_unexpected_exception_uses_propose_meta(monkeypatch, clean_env, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(server.workspace, "resolve_workspace", boom)
    res = await server.codex_delegate("do a thing", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert res["meta"]["tier"] == "propose"
    assert res["meta"]["sandbox"] == "workspace-write"


async def test_boundary_internal_error_stamps_elapsed_ms(monkeypatch, clean_env, tmp_path):
    import asyncio

    async def slow_boom(*a, **k):
        await asyncio.sleep(0.02)
        raise RuntimeError("late failure")

    monkeypatch.setattr(server.codex, "run_codex_exec", slow_boom)
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    # A late failure records its elapsed time, not a misleading 0.
    assert res["meta"]["elapsed_ms"] > 0


async def test_boundary_propagates_cancellation(monkeypatch, clean_env, tmp_path):
    import asyncio

    def cancel(*a, **k):
        raise asyncio.CancelledError

    monkeypatch.setattr(server.codex, "run_codex_exec", cancel)
    with pytest.raises(asyncio.CancelledError):
        await server.codex_consult("q", workspace_root=str(tmp_path))
