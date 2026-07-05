"""Server tool behavior: status, capabilities, consult (mocked codex)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import get_args

import pytest
from pydantic import ValidationError

from codex_in_claude import codex, delegate, orchestration, server
from codex_in_claude._core.runtime import CommandRun
from codex_in_claude.schemas import (
    FINGERPRINT,
    JOB_POLL_AFTER_MS,
    ErrorCode,
    Isolation,
    ReviewScope,
    apply_detail,
)


def _fake_result(last_message, *, exit_code=0, stderr="", events=""):
    return codex.CodexExecResult(
        run=CommandRun(events, stderr, exit_code, 12, exit_code == -9),
        last_message=last_message,
        events=events,
    )


# The sync consult/review/delegate tools now run the orchestration in a detached
# worker subprocess (#169), so a monkeypatched `run_codex_exec`/`gather_diff`/worktree
# seam can no longer be observed *through* the sync tool. These helpers call the same
# orchestration/delegate entry points the worker calls, so the run-behavior tests
# (parsing, redaction, truncation, error mapping) keep their assertions at the unit
# level where that behavior now lives. Tool-level wiring is covered by the F3 tests.
async def _run_consult_direct(tmp_path, question="q", **kw):
    meta = server._base_meta(
        str(tmp_path),
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=180,
    )
    return await orchestration.run_consult(
        question,
        str(tmp_path),
        meta,
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=180,
        model=None,
        **kw,
    )


async def _run_review_direct(
    tmp_path, *, scope="working_tree", base=None, commit=None, paths=None, **kw
):
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
        paths=paths,
    )
    return await orchestration.run_review(
        str(tmp_path),
        meta,
        scope=scope,
        base=base,
        commit=commit,
        paths=paths,
        sandbox="read-only",
        isolation="inherit",
        timeout_seconds=180,
        model=None,
        git_timeout=30,
        max_bytes=server.config.max_input_bytes(),
        **kw,
    )


async def _run_delegate_direct(tmp_path, *, task="do work", **kw):
    meta = server._base_meta(
        str(tmp_path),
        "param",
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        model=None,
        timeout_seconds=180,
    )
    return await delegate.run_delegate(
        task,
        str(tmp_path),
        meta,
        sandbox="workspace-write",
        isolation="inherit",
        timeout_seconds=180,
        model=None,
        git_timeout=30,
        **kw,
    )


# --- status / capabilities ---------------------------------------------------
def test_status_ready(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.142.0")
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
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.142.0")
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
        "codex_models",  # advisory model-slug discovery (tool + codex://models resource)
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


def test_capabilities_names_tool_error_carrier():
    # F3: agents must learn WHERE a tool failure travels before the first failure.
    res = server.codex_capabilities()
    carrier = res["tool_error_carrier"]
    assert "structuredContent" in carrier
    assert "isError" in carrier


def test_instructions_name_the_error_carrier():
    # F3: the capability summary (served as MCP instructions) names the carrier for
    # tool failures, so a discovery-only client need not infer it from the outputSchema.
    summary = server.CAPABILITY_SUMMARY
    assert "isError" in summary
    assert "structuredContent" in summary


def test_workspace_write_no_egress_is_documented():
    """The propose-tier no-network constraint of workspace-write is discoverable (issue #24).

    Delegate runs under workspace-write, which blocks network egress; agents must
    not assume write access implies internet access."""
    for doc in (server.codex_delegate.__doc__, server.codex_delegate_async.__doc__):
        assert doc is not None
        assert "network" in doc.lower()
    negative_scope = server.codex_capabilities()["negative_scope"]
    assert any("network" in entry.lower() for entry in negative_scope)


# Active tools that send caller content to OpenAI via the codex CLI (issue #114).
# Derived from the capabilities source of truth so the disclosure contract tracks
# the active-tool set automatically as tools are added/removed/renamed.
_ACTIVE_EGRESS_TOOLS = tuple(server.codex_capabilities()["active_tools"])


@pytest.mark.parametrize("name", _ACTIVE_EGRESS_TOOLS)
def test_egress_disclosed_in_active_tool_docstrings(name):
    """Every active tool's description states it sends content to OpenAI (issue #114).

    An agent must be able to determine, without making a call, that the tool
    transmits repo content off the machine."""
    doc = getattr(server, name).__doc__
    assert doc is not None
    assert "OpenAI" in doc, name


@pytest.mark.parametrize("name", _ACTIVE_EGRESS_TOOLS)
def test_egress_disclosed_in_capabilities(name):
    """codex_capabilities alone discloses OpenAI egress per active tool (issue #114).

    AC1: capabilities OR the tool descriptions must suffice; this asserts the
    capabilities path independently of the docstrings."""
    by_name = {t["name"]: t for t in server.codex_capabilities()["tool_details"]}
    assert name in by_name, f"capabilities omitted active tool {name}"
    detail = by_name[name]
    assert "OpenAI" in (detail["use_when"] + detail["returns"]), name


def test_redaction_limits_disclosed_in_capabilities():
    """negative_scope states redaction is best-effort and what it does not cover (issue #114)."""
    negative_scope = server.codex_capabilities()["negative_scope"]
    blob = " ".join(negative_scope).lower()
    assert "redact" in blob
    assert "best-effort" in blob
    # It must be clear that user-supplied inputs are not redacted.
    assert "input" in blob


def test_delegate_no_network_not_misread_as_no_egress():
    """The delegate no-network line cannot be read as 'nothing leaves the machine' (issue #114).

    Some negative_scope entry must tie the network-sandbox claim to the fact that
    the model call still sends task/repo context to OpenAI."""
    negative_scope = server.codex_capabilities()["negative_scope"]
    assert any("network" in entry.lower() and "openai" in entry.lower() for entry in negative_scope)


def test_status_caveat_names_review_and_delegate(monkeypatch, clean_env):
    """The status caveat discloses egress for review and delegate, not just consult (issue #114)."""
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.142.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "auth (ChatGPT)."))
    caveat = server.codex_status()["caveat"].lower()
    assert "review" in caveat
    assert "delegate" in caveat


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
    res = await _run_consult_direct(tmp_path, "is this ok?")
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
    res = await _run_consult_direct(tmp_path, "question")
    assert res["ok"] is True
    assert "plain answer" in res["summary"]
    assert "verdict" not in res  # consult carries no verdict (#31)


# --- consult: error paths ----------------------------------------------------
async def test_consult_codex_error(monkeypatch, clean_env, tmp_path):
    async def fake(*args, **kwargs):
        return _fake_result(None, exit_code=1, stderr="not logged in")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await _run_consult_direct(tmp_path, "q")
    assert res["ok"] is False
    assert res["error"]["code"] == "codex_auth_required"


async def test_consult_bad_isolation(clean_env, tmp_path):
    res = await server.codex_consult("q", workspace_root=str(tmp_path), isolation="bogus")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_isolation"
    assert res["error"]["details"]["field"] == "isolation"


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


async def test_consult_combined_too_large_names_both_fields(monkeypatch, clean_env, tmp_path):
    # F2: the combined-size limit is on question + extra_context together, so when both
    # contribute the envelope names both via details.fields (not a single misleading field).
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    res = await server.codex_consult(
        "x" * 600, workspace_root=str(tmp_path), extra_context="y" * 600
    )
    assert res["error"]["code"] == "input_too_large"
    assert res["error"]["details"]["fields"] == ["question", "extra_context"]
    assert "field" not in res["error"]["details"]  # exactly one of field/fields


async def test_consult_question_only_too_large_names_question(monkeypatch, clean_env, tmp_path):
    # F2: when only `question` is oversized (no extra_context), report field="question"
    # rather than blaming extra_context, which contributed nothing.
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    res = await server.codex_consult("x" * 2000, workspace_root=str(tmp_path))
    assert res["error"]["code"] == "input_too_large"
    assert res["error"]["details"]["field"] == "question"
    assert "fields" not in res["error"]["details"]


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
    res = await _run_review_direct(tmp_path, scope="working_tree")
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
    res = await _run_review_direct(
        tmp_path,
        scope="working_tree",
        extra_context="I verified git diff --numstat does not invoke textconv.",
    )
    assert res["ok"] is True
    assert "Author-provided context (untrusted data)" in captured["prompt"]
    assert "does not invoke textconv" in captured["prompt"]


async def test_review_extra_context_too_large(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())
    res = await _run_review_direct(tmp_path, scope="working_tree", extra_context="x" * 2000)
    assert res["ok"] is False
    assert res["error"]["code"] == "input_too_large"
    assert res["error"]["details"]["field"] == "extra_context"
    # The review path (run_review) also carries the structured size fields (#95).
    assert res["error"]["limit_bytes"] == 1000
    assert res["error"]["actual_bytes"] == 2000


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
    assert res["error"]["details"]["field"] == "extra_context"
    assert res["error"]["limit_bytes"] == 1000
    assert res["error"]["actual_bytes"] == 2000


async def test_dry_run_advertises_returnable_error_codes():
    # codex_dry_run can return these via its pre-flight checks; capabilities must
    # advertise each (input_too_large from extra_context, the placeholder guard). It
    # must NOT advertise unsupported_isolation — `isolation` is Literal-typed, so a bad
    # value is rejected by MCP validation before the handler (#92).
    caps = server.codex_capabilities()
    dry = next(t for t in caps["tool_details"] if t["name"] == "codex_dry_run")
    assert "input_too_large" in dry["error_codes"]
    assert "unexpanded_env_placeholder" in dry["error_codes"]
    assert "unsupported_isolation" not in dry["error_codes"]


def test_isolation_accepting_tools_do_not_advertise_unsupported_isolation():
    # `isolation` is a Literal param, so an out-of-enum value is rejected by FastMCP
    # input validation before the handler's _resolve_isolation guard runs — the
    # unsupported_isolation envelope is MCP-unreachable and must not be advertised (#92).
    # The param is still advertised; only the unreachable error code is dropped.
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
        assert "isolation" in by_name[name]["key_optional_params"], name
        assert "unsupported_isolation" not in by_name[name]["error_codes"], name


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
    res = await _run_review_direct(tmp_path, scope="working_tree")
    assert res["ok"] is True
    assert res["verdict"] == "pass"
    assert called["n"] == 0  # no model call for an empty diff


async def test_review_exit0_non_json_returns_invalid_json_error(monkeypatch, clean_env, tmp_path):
    # When Codex exits 0 but returns a non-JSON message, review no longer silently
    # downgrades to prose with verdict="unknown" — it surfaces an explicit error because
    # the structured verdict/findings are the review's product, not a prose answer (#159).
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())

    async def fake(*a, **k):
        return _fake_result("plain prose, not JSON")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await _run_review_direct(tmp_path, scope="working_tree")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_json"
    # raw output preserved (bounded, redacted) in the message for debugging
    assert "plain prose" in res["error"]["message"]


async def test_review_codex_error(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())

    async def fake(*a, **k):
        return _fake_result(None, exit_code=1, stderr="not logged in")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await _run_review_direct(tmp_path, scope="working_tree")
    assert res["ok"] is False
    assert res["error"]["code"] == "codex_auth_required"


async def test_review_not_a_git_repo(monkeypatch, clean_env, tmp_path):
    def raise_not_repo(*a, **k):
        raise gitdiff.NotAGitRepoError("not a git repository")

    monkeypatch.setattr(gitdiff, "gather_diff", raise_not_repo)
    res = await _run_review_direct(tmp_path, scope="working_tree")
    assert res["ok"] is False
    assert res["error"]["code"] == "not_a_git_repo"


async def test_review_invalid_base(monkeypatch, clean_env, tmp_path):
    def raise_base(*a, **k):
        raise gitdiff.InvalidBaseError("bad base")

    monkeypatch.setattr(gitdiff, "gather_diff", raise_base)
    res = await _run_review_direct(tmp_path, scope="branch", base="-bad")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_base"
    assert res["error"]["details"]["field"] == "base"


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
    res = await _run_delegate_direct(tmp_path, task="add a feature")
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
    return await _run_delegate_direct(tmp_path)


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
    res = await _run_delegate_direct(tmp_path, task="do it")
    assert res["ok"] is False
    assert res["error"]["code"] == "codex_auth_required"
    assert removed["n"] == 1  # cleanup still happened


async def test_run_delegate_reports_worktree_parent(monkeypatch, clean_env, tmp_path):
    # run_delegate forwards the on_worktree_parent hook to worktree.create so the
    # background worker can record the temp dir for cleanup before codex runs.
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
    res = await _run_delegate_direct(tmp_path, task="inspect config")
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
    res = await _run_consult_direct(tmp_path, "any secrets?")
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
    res = await _run_review_direct(tmp_path, scope="working_tree")
    assert res["ok"] is True
    assert _SECRET not in res["summary"]
    assert "AKIAIOSFODNN7EXAMPLE" not in res["summary"]
    assert _SECRET not in (res["raw_response"]["text"] or "")
    assert res["verdict"] == "concerns"


async def test_delegate_not_a_git_repo(monkeypatch, clean_env, tmp_path):
    # The sync tool now fails fast on the synchronous ensure_repo_with_head preflight
    # (zero spend, no job record) — same as codex_delegate_async.
    def boom(*a, **k):
        raise server.worktree.NotAGitRepoError("not a git repo")

    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", boom)
    res = await server.codex_delegate("x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "not_a_git_repo"


async def test_delegate_no_commits(monkeypatch, clean_env, tmp_path):
    def boom(*a, **k):
        raise server.worktree.NoCommitsError("no commits")

    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", boom)
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

    res = await _run_delegate_direct(tmp_path, task="do something")
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
    res = await _run_delegate_direct(tmp_path, task="x")
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
    assert res["error"]["details"]["field"] == "isolation"


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
    assert res["error"]["limit_bytes"] == 1000
    assert res["error"]["actual_bytes"] == 2000


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
    res = await _run_delegate_direct(tmp_path, task="x")
    assert res["ok"] is False
    assert res["error"]["code"] == "worktree_error"
    assert removed["n"] == 1


# --- async delegate + job lifecycle ------------------------------------------
class _FakeStore:
    """In-memory stand-in for JobStore used by the async/lifecycle tool tests."""

    def __init__(
        self, *, status_dict="__unset__", record=None, result_json=None, status_sequence=None
    ):
        self._status = status_dict
        self._record = record
        self._result_json = result_json
        # A list of status dicts returned one-per-call (e.g. increasing events_seen);
        # the last entry repeats once the sequence is exhausted. Takes priority over
        # status_dict/record when set.
        self._status_sequence = status_sequence
        self._status_sequence_idx = 0
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
        if self._status_sequence is not None:
            idx = min(self._status_sequence_idx, len(self._status_sequence) - 1)
            self._status_sequence_idx += 1
            return self._status_sequence[idx]
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
    assert res["error"]["limit_bytes"] == 1000
    assert res["error"]["actual_bytes"] == 2000


async def test_job_status_done(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(status_dict=_ok_record("done"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_status("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["job_id"] == "job-abc"
    assert res["status"] == "done"
    assert res["result_available"] is True


async def test_job_status_includes_workspace(monkeypatch, clean_env, tmp_path):
    # #54: a successful status response carries the resolved workspace context so an
    # agent can tell which repo it polled (recovering after context compaction).
    store = _FakeStore(status_dict=_ok_record("running"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_status("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    ws = res["workspace"]
    assert ws["workspace_source"] == "param"
    assert ws["cwd"]
    assert ws["workspace_warning"] is None


async def test_job_status_cwd_fallback_warning(monkeypatch, clean_env, tmp_path):
    # #54: with no workspace_root and no MCP roots the server resolves from its own
    # cwd; the success response must surface workspace_warning so wrong-workspace
    # polling is diagnosable rather than silently returning job_not_found.
    store = _FakeStore(status_dict=_ok_record("running"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    monkeypatch.setattr(server.workspace, "server_cwd", lambda: str(tmp_path))
    res = await server.codex_job_status("job-abc")
    assert res["ok"] is True
    assert res["workspace"]["workspace_source"] == "cwd"
    assert res["workspace"]["workspace_warning"] is not None


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
    assert res["error"]["temporary"] is True


async def test_job_result_timeout_maps_error(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("timeout"), result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_timeout"


# --- F5: lifecycle envelopes report the OPERATION's posture, not the job's -----
# The lifecycle tools never call Codex and never write the caller's workspace, so
# their generated error envelopes must report meta.tier/sandbox = consult/read-only
# (consistent with readOnlyHint), and carry the inspected job's kind in meta.job_kind
# rather than overloading tier/sandbox with the job's posture (audit F5, #177).
async def test_job_status_not_found_meta_reports_read_only_operation(
    monkeypatch, clean_env, tmp_path
):
    store = _FakeStore(status_dict=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_status("nope", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_not_found"
    # No job resolved: the LOOKUP is read-only, and there is no worst-case propose.
    assert res["meta"]["tier"] == "consult"
    assert res["meta"]["sandbox"] == "read-only"
    # No record was found, so there is no job posture to report (None → omitted).
    assert "job_kind" not in res["meta"]


async def test_job_result_running_meta_read_only_with_job_kind(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("running"), result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_running"
    # The lookup itself is read-only — it did not run the delegate.
    assert res["meta"]["tier"] == "consult"
    assert res["meta"]["sandbox"] == "read-only"
    # The inspected job's posture is preserved via its kind (a propose-tier delegate).
    assert res["meta"]["job_kind"] == "codex_delegate"


async def test_job_result_done_preserves_originating_meta(monkeypatch, clean_env, tmp_path):
    # A retrieved success envelope carries the ORIGINATING run's meta (a delegate ran
    # propose/workspace-write) — the lifecycle read-only posture must not overwrite it.
    store = _FakeStore(record=_ok_record("done"), result_json=_done_envelope())
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["meta"]["tier"] == "propose"
    assert res["meta"]["sandbox"] == "workspace-write"


async def test_job_tool_invalid_arguments_meta_read_only(clean_env):
    # A malformed-argument call to a read-only lifecycle tool must not claim the
    # propose/workspace-write posture on its invalid_arguments envelope.
    res = await server.mcp.call_tool("codex_job_status", {"definitely_not_a_param": 1})
    sc = res.structured_content
    assert sc["error"]["code"] == "invalid_arguments"
    assert sc["meta"]["tier"] == "consult"
    assert sc["meta"]["sandbox"] == "read-only"


async def test_invalid_arguments_repair_names_tool_and_leads_with_correction(clean_env):
    # N3: the failing tool name is known and non-sensitive, so repair.tool surfaces it.
    res = await server.mcp.call_tool("codex_consult", {"definitely_not_a_param": 1})
    repair = res.structured_content["error"]["repair"]
    assert repair["next_step"] == "correct_arguments"
    assert repair["tool"] == "codex_consult"
    # Values are never echoed, so repair.arguments must stay absent, and the alternative
    # must lead with correcting the args so (tool set, no arguments) can't read as a
    # blind re-call of the same tool.
    assert "arguments" not in repair
    assert repair["alternative"].startswith("Correct the argument(s) first")


async def test_invalid_arguments_repair_untyped_failure_is_not_self_referential(clean_env):
    # When no type-specific hint applies (e.g. a wrong-type value), the alternative must
    # not render the self-referential "… first — correct the argument(s)." (Copilot review).
    res = await server.mcp.call_tool("codex_consult", {"question": [1, 2]})
    alt = res.structured_content["error"]["repair"]["alternative"]
    assert alt.startswith("Correct the argument(s) first. ")
    assert " — " not in alt


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
    # cancel reuses JobStatus, so it carries the resolved workspace too (#54).
    assert res["workspace"]["workspace_source"] == "param"
    assert res["workspace"]["workspace_warning"] is None


async def test_job_cancel_cwd_fallback_warning(monkeypatch, clean_env, tmp_path):
    # #54: the cwd-fallback warning propagates to codex_job_cancel's success response.
    store = _FakeStore(record=_ok_record("cancelled"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    monkeypatch.setattr(server.workspace, "server_cwd", lambda: str(tmp_path))
    res = await server.codex_job_cancel("job-abc")
    assert res["ok"] is True
    assert res["workspace"]["workspace_source"] == "cwd"
    assert res["workspace"]["workspace_warning"] is not None


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


async def test_job_list_includes_workspace(monkeypatch, clean_env, tmp_path):
    # #54: codex_job_list success carries the resolved workspace context too.
    store = _FakeStore(record=_ok_record("done"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_list(workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["workspace"]["workspace_source"] == "param"
    assert res["workspace"]["cwd"]
    assert res["workspace"]["workspace_warning"] is None


async def test_job_list_cwd_fallback_warning(monkeypatch, clean_env, tmp_path):
    # #54: cwd-fallback warning propagates to the list success response.
    store = _FakeStore(record=_ok_record("done"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    monkeypatch.setattr(server.workspace, "server_cwd", lambda: str(tmp_path))
    res = await server.codex_job_list()
    assert res["ok"] is True
    assert res["workspace"]["workspace_source"] == "cwd"
    assert res["workspace"]["workspace_warning"] is not None


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


def test_fingerprint_is_schema_28():
    assert FINGERPRINT == "codex-in-claude/0.1/schema-28"


def test_capabilities_payload_discloses_fingerprint_covers():
    """The capabilities payload advertises what the fingerprint covers so a client can
    reason about cache invalidation programmatically instead of reading source (#178, F6)."""
    from codex_in_claude.schemas import FINGERPRINT_COVERS

    caps = server.codex_capabilities()
    assert caps["fingerprint_covers"] == list(FINGERPRINT_COVERS)


def test_capabilities_mark_m4_surface_experimental():
    """The newer async + background-job lifecycle tools advertise stability=experimental;
    the sync core inherits the server-wide alpha (field omitted via exclude_none) (#71)."""
    caps = server.codex_capabilities()
    by_name = {t["name"]: t for t in caps["tool_details"]}
    experimental = {
        "codex_consult_async",
        "codex_review_changes_async",
        "codex_delegate_async",
        "codex_job_status",
        "codex_job_result",
        "codex_job_consume_result",
        "codex_job_cancel",
        "codex_job_list",
    }
    for name in experimental:
        assert by_name[name]["stability"] == "experimental", name
    # Sync core tools omit the field entirely (inherit server-wide stability).
    for name in ("codex_consult", "codex_review_changes", "codex_delegate", "codex_status"):
        assert "stability" not in by_name[name], name


def test_server_advertises_tools_list_changed():
    """The server declares the tools `listChanged` capability so clients know the
    contract even though the static tool list never changes mid-session (#71)."""
    opts = server.mcp._mcp_server.create_initialization_options()
    assert opts.capabilities.tools.listChanged is True


async def test_sync_active_tools_document_progress_and_job_recovery():
    """The blocking active tools tell agents they stream coarse progress when
    requested, that the run is recorded as a recoverable job under meta.job_id, and
    point to the async variant + codex_job_status for fire-and-forget from the start
    (#72, #169)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name, async_name in (
        ("codex_consult", "codex_consult_async"),
        ("codex_review_changes", "codex_review_changes_async"),
        ("codex_delegate", "codex_delegate_async"),
    ):
        desc = tools[name].description or ""
        assert "notifications/progress" in desc, name
        assert "meta.job_id" in desc, name
        assert "codex_job_result" in desc, name
        assert async_name in desc, name
        assert "codex_job_status" in desc, name


# --- detail levels (#56) -----------------------------------------------------
_CONSULT_PAYLOAD = {"summary": "Looks fine", "findings": [], "questions": ["q1"]}


async def test_consult_default_detail_omits_raw_text(monkeypatch, clean_env, tmp_path):
    # #56: the orchestration (worker) envelope carries the full raw model text; the
    # tool applies detail via _finished_job_envelope — summary omits raw_response.text
    # while keeping the authoritative structured fields (tool-level path covered by the
    # F3 tests; here we assert the seam directly on the orchestration envelope).
    async def fake(*a, **k):
        return _fake_result(json.dumps(_CONSULT_PAYLOAD))

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await _run_consult_direct(tmp_path, "ok?")
    assert res["ok"] is True
    assert res["summary"] == "Looks fine"
    assert res["questions"] == ["q1"]
    assert res["raw_response"]["text"] == json.dumps(_CONSULT_PAYLOAD)  # populated by orchestration
    assert apply_detail(res, "summary")["raw_response"]["text"] is None  # omitted by default


async def test_consult_full_detail_includes_raw_text(monkeypatch, clean_env, tmp_path):
    async def fake(*a, **k):
        return _fake_result(json.dumps(_CONSULT_PAYLOAD))

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await _run_consult_direct(tmp_path, "ok?")
    assert apply_detail(res, "full")["raw_response"]["text"] == json.dumps(_CONSULT_PAYLOAD)


async def test_consult_bad_detail(clean_env, tmp_path):
    res = await server.codex_consult("q", workspace_root=str(tmp_path), detail="bogus")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_detail"
    assert res["error"]["details"]["allowed_values"] == ["summary", "full"]


async def test_review_bad_detail(clean_env, tmp_path):
    res = await server.codex_review_changes(
        scope="working_tree", workspace_root=str(tmp_path), detail="bogus"
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_detail"


async def test_delegate_bad_detail(clean_env, tmp_path):
    res = await server.codex_delegate("x", workspace_root=str(tmp_path), detail="bogus")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_detail"


async def test_job_result_bad_detail(monkeypatch, clean_env, tmp_path):
    store = _FakeStore(record=_ok_record("done"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path), detail="bogus")
    assert res["ok"] is False
    assert res["error"]["code"] == "unsupported_detail"


async def test_review_default_detail_omits_raw_text(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(gitdiff, "gather_diff", lambda *a, **k: _diff())
    payload = {"summary": "ok", "verdict": "pass", "confidence": "high"}

    async def fake(*a, **k):
        return _fake_result(json.dumps(payload))

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await _run_review_direct(tmp_path, scope="working_tree")
    assert res["ok"] is True
    assert res["verdict"] == "pass"
    assert res["raw_response"]["text"] == json.dumps(payload)  # populated by orchestration
    assert apply_detail(res, "full")["raw_response"]["text"] == json.dumps(payload)
    assert apply_detail(res, "summary")["raw_response"]["text"] is None


async def test_delegate_default_detail_omits_raw_text(monkeypatch, clean_env, tmp_path):
    wt = _fake_worktree(tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *a, **k: wt)
    monkeypatch.setattr(worktree, "remove", lambda *a, **k: None)
    monkeypatch.setattr(worktree, "capture_diff", lambda *a, **k: "diff --git a/x b/x\n+y\n")

    async def fake(*a, **k):
        return _fake_result("Implemented the change.")

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    res = await _run_delegate_direct(tmp_path, task="do x")
    assert res["ok"] is True
    assert res["summary"] == "Implemented the change."
    assert res["raw_response"]["text"] == "Implemented the change."  # populated by orchestration
    assert apply_detail(res, "full")["raw_response"]["text"] == "Implemented the change."
    assert apply_detail(res, "summary")["raw_response"]["text"] is None


async def test_job_result_detail_controls_raw_text(monkeypatch, clean_env, tmp_path):
    # #56: async result retrieval applies detail too — the worker stores the full
    # envelope, and codex_job_result trims raw_response.text unless detail="full".
    import copy

    def _stored():
        meta = server._base_meta(
            "/repo",
            "param",
            tier="propose",
            sandbox="workspace-write",
            isolation="inherit",
            model=None,
            timeout_seconds=1800,
        ).model_dump(mode="json")
        return {
            "ok": True,
            "tool": "codex_delegate",
            "summary": "did it",
            "diff": "d",
            "raw_response": {"text": "RAW MODEL OUTPUT", "session_id": "s1", "model": "m"},
            "meta": meta,
        }

    store = _FakeStore(record=_ok_record("done"), result_json=copy.deepcopy(_stored()))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["raw_response"]["text"] is None  # summary default

    store2 = _FakeStore(record=_ok_record("done"), result_json=copy.deepcopy(_stored()))
    monkeypatch.setattr(server.config, "job_store", lambda: store2)
    full = await server.codex_job_result("job-abc", workspace_root=str(tmp_path), detail="full")
    assert full["raw_response"]["text"] == "RAW MODEL OUTPUT"

    # codex_job_consume_result shares the same trimming path (consume=True); assert it
    # honors detail too so a regression there can't slip through (Copilot review).
    store3 = _FakeStore(record=_ok_record("done"), result_json=copy.deepcopy(_stored()))
    monkeypatch.setattr(server.config, "job_store", lambda: store3)
    consumed = await server.codex_job_consume_result("job-abc", workspace_root=str(tmp_path))
    assert consumed["ok"] is True
    assert consumed["raw_response"]["text"] is None  # summary default on consume
    assert store3.consumed == ["job-abc"]  # the record was actually consumed

    store4 = _FakeStore(record=_ok_record("done"), result_json=copy.deepcopy(_stored()))
    monkeypatch.setattr(server.config, "job_store", lambda: store4)
    consumed_full = await server.codex_job_consume_result(
        "job-abc", workspace_root=str(tmp_path), detail="full"
    )
    assert consumed_full["raw_response"]["text"] == "RAW MODEL OUTPUT"


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


async def test_consult_async_combined_too_large_names_both_fields(monkeypatch, clean_env, tmp_path):
    # F2: the async consult path shares the combined-size guard, so it names both fields too.
    monkeypatch.setenv("CODEX_IN_CLAUDE_MAX_INPUT_BYTES", "1000")
    res = await server.codex_consult_async(
        "x" * 600, workspace_root=str(tmp_path), extra_context="y" * 600
    )
    assert res["error"]["code"] == "input_too_large"
    assert res["error"]["details"]["fields"] == ["question", "extra_context"]


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
    # Both inputs contributed to the combined-size limit, so both are named (#174/F2).
    assert res["error"]["details"]["fields"] == ["question", "extra_context"]
    assert res["error"]["limit_bytes"] == 1000
    # actual_bytes covers question + extra_context: len("q") + 2000.
    assert res["error"]["actual_bytes"] == 2001


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


def test_review_tools_advertise_isolation_param_not_unreachable_error():
    # Both review tools accept `isolation`, so the param is advertised — but
    # unsupported_isolation is MCP-unreachable (Literal param) and must not be (#92).
    caps = server.codex_capabilities()
    by_name = {t["name"]: t for t in caps["tool_details"]}
    for name in ("codex_review_changes", "codex_review_changes_async"):
        assert "isolation" in by_name[name]["key_optional_params"], name
        assert "unsupported_isolation" not in by_name[name]["error_codes"], name


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


async def test_isolation_param_description_does_not_hardcode_default():
    """IsolationParam must not label 'inherit' the unconditional default: the
    default is env-configurable (CODEX_IN_CLAUDE_ISOLATION), so an agent omitting
    the param on a configured server can get behavior the schema didn't promise.
    The description instead points to the server's configured default and to
    codex_status for the resolved value (issue #183, audit N2)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    desc = tools["codex_consult"].parameters["properties"]["isolation"]["description"]
    assert "'inherit' (default)" not in desc
    assert "configured" in desc
    assert "codex_status" in desc


async def test_all_tool_input_schemas_are_closed_and_declare_dialect():
    """Every tool input schema rejects unknown keys and declares its JSON Schema
    dialect, so a misspelled/extra param can't be silently dropped (issue #70)."""
    tools = await server.mcp.list_tools()
    assert tools
    for tool in tools:
        schema = tool.parameters
        assert schema.get("additionalProperties") is False, f"{tool.name} schema not closed"
        assert schema.get("$schema") == server.INPUT_SCHEMA_DIALECT, (
            f"{tool.name} schema declares no dialect"
        )


async def test_dialect_middleware_overwrites_existing_schema():
    """The middleware stamps our dialect even when a tool already carries a
    ``$schema`` (a different draft, or None) — the guarantee is that the
    advertised dialect matches the one we validate against, not that we defer
    to whatever upstream emitted (Copilot review, PR #80)."""

    class _FakeTool:
        def __init__(self, params):
            self.parameters = params

    tools = [
        _FakeTool({"$schema": "https://json-schema.org/draft-07/schema#"}),
        _FakeTool({"$schema": None}),
        _FakeTool({}),
        _FakeTool(None),
    ]

    async def call_next(_context):
        return tools

    middleware = server._InputSchemaDialectMiddleware()
    result = await middleware.on_list_tools(object(), call_next)

    assert result[0].parameters["$schema"] == server.INPUT_SCHEMA_DIALECT
    assert result[1].parameters["$schema"] == server.INPUT_SCHEMA_DIALECT
    assert result[2].parameters["$schema"] == server.INPUT_SCHEMA_DIALECT
    assert result[3].parameters is None


def test_input_dialect_is_the_shared_constant():
    """The input dialect is sourced from the one shared constant, so it can't drift from
    the output-schema dialect (audit N4, #185)."""
    from codex_in_claude import schemas

    assert server.INPUT_SCHEMA_DIALECT == schemas.JSON_SCHEMA_DIALECT


async def test_all_tool_output_schemas_declare_dialect():
    """Every advertised outputSchema declares its JSON Schema dialect, symmetric with the
    input schemas, so a client knows which draft to validate a tool result against
    (audit N4, #185)."""
    tools = await server.mcp.list_tools()
    assert tools
    for tool in tools:
        schema = tool.output_schema
        assert schema is not None, f"{tool.name} advertises no output schema"
        assert schema.get("$schema") == server.JSON_SCHEMA_DIALECT, (
            f"{tool.name} output schema declares no dialect"
        )


async def test_resources_declare_explicit_name_and_title():
    """Resources advertise an explicit agent-facing name + title rather than the
    function-derived name, so a resource-browsing agent sees intent, not internals
    (audit N1, #182)."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        resources = {str(r.uri): r for r in await client.list_resources()}
    expected = {
        "codex://models": ("codex-models", "Codex model catalog"),
        "codex://error-envelope": ("codex-error-envelope", "Codex error envelope schema"),
        "codex://result-meta": ("codex-result-meta", "Codex result metadata schema"),
    }
    for uri, (name, title) in expected.items():
        r = resources[uri]
        assert r.name == name, f"{uri}: name {r.name!r} != {name!r}"
        assert r.title == title, f"{uri}: title {r.title!r} != {title!r}"


async def test_unknown_tool_argument_is_rejected():
    """An unknown argument fails validation rather than being silently ignored.

    This pins the raw Tool-level boundary: `Tool.run` still raises a Pydantic
    `ValidationError`. The MCP call-tool boundary re-emits that as the structured
    `invalid_arguments` envelope — see the tests below (#136)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    with pytest.raises(ValidationError):
        await tools["codex_status"].run({"definitely_not_a_param": 1})


# --- invalid-argument envelope at the MCP call-tool boundary (#136) -----------
async def test_unknown_argument_returns_structured_envelope():
    """An unknown argument at the call_tool boundary becomes the documented error
    envelope (isError + invalid_arguments code) instead of raw Pydantic prose."""
    res = await server.mcp.call_tool("codex_status", {"definitely_not_a_param": 1})
    assert res.is_error is True
    sc = res.structured_content
    assert sc["ok"] is False
    err = sc["error"]
    assert err["code"] == "invalid_arguments"
    assert err["details"]["field"] == "definitely_not_a_param"
    assert err["temporary"] is False
    # the symbolic, machine-actionable detail list is present
    items = err["invalid_arguments"]
    assert items[0]["field"] == "definitely_not_a_param"
    assert items[0]["reason"]
    assert err["details"]["reason"] == items[0]["reason"]
    # envelope carries the contract identifiers that raw Pydantic errors lack
    assert sc["meta"]["fingerprint"] == FINGERPRINT
    assert sc["meta"]["request_id"]


async def test_bad_enum_argument_lists_allowed_values_at_boundary():
    """An out-of-enum Literal value surfaces invalid_arguments with the enum's
    allowed_values derived from the tool's input schema (not parsed prose)."""
    res = await server.mcp.call_tool("codex_review_changes", {"scope": "nope"})
    assert res.is_error is True
    err = res.structured_content["error"]
    assert err["code"] == "invalid_arguments"
    assert err["details"]["field"] == "scope"
    assert err["details"]["allowed_values"] == list(get_args(ReviewScope))
    assert err["invalid_arguments"][0]["allowed_values"] == list(get_args(ReviewScope))


async def test_bad_isolation_enum_lists_allowed_values_from_anyof():
    """allowed_values are extracted even when the enum lives under an anyOf branch
    (Optional Literal params like `isolation`)."""
    res = await server.mcp.call_tool("codex_consult", {"question": "hi", "isolation": "bogus"})
    err = res.structured_content["error"]
    assert err["code"] == "invalid_arguments"
    assert err["details"]["allowed_values"] == list(server.config.VALID_ISOLATIONS)


async def test_rejected_argument_value_is_never_echoed():
    """No rejected input value is copied into the result — not for an unknown key, a
    wrong-typed free-form param, or even an out-of-enum value (a Literal param accepts
    arbitrary input that could be a secret the pattern redactor can't catch) (#136).
    The detail carries field/reason/allowed_values only, never `value`."""
    leak = "correct horse battery staple"
    for tool, args in (
        ("codex_status", {"token": leak}),  # unknown key
        ("codex_consult", {"question": "q", "paths": leak}),  # known param, wrong type
        ("codex_review_changes", {"scope": leak}),  # out-of-enum Literal value
    ):
        res = await server.mcp.call_tool(tool, args)
        assert res.structured_content["error"]["code"] == "invalid_arguments"
        for item in res.structured_content["error"]["invalid_arguments"]:
            assert "value" not in item
        assert leak not in json.dumps(res.structured_content)


def test_format_loc_nested_index_has_no_stray_dot():
    """A nested list location renders as a valid accessor (paths[0]), not paths.[0]."""
    assert server._format_loc(("paths", 0)) == "paths[0]"
    assert server._format_loc(("a", "b")) == "a.b"
    assert server._format_loc(()) == "<arguments>"


def test_format_loc_bounds_oversized_field_name():
    """A caller-controlled (oversized) location is length-bounded so it can't amplify
    the envelope or copy a long key verbatim (#136)."""
    out = server._format_loc(("x" * 100_000,))
    assert len(out) <= server._MAX_ARG_FIELD_LEN + 1  # +1 for the ellipsis marker


async def test_invalid_arguments_meta_reports_called_tool_posture():
    """meta.tier/sandbox describe the CALLED tool, not the server defaults — a malformed
    propose-tier call reports propose/workspace-write, a consult call reports
    consult/read-only (#136)."""
    res = await server.mcp.call_tool("codex_delegate", {"nope": 1})
    meta = res.structured_content["meta"]
    assert (meta["tier"], meta["sandbox"]) == ("propose", "workspace-write")
    res = await server.mcp.call_tool("codex_consult", {"nope": 1})
    meta = res.structured_content["meta"]
    assert (meta["tier"], meta["sandbox"]) == ("consult", "read-only")


async def test_oversized_unknown_argument_does_not_amplify_response():
    """An oversized unknown key produces a bounded envelope, not a megabyte response."""
    res = await server.mcp.call_tool("codex_status", {"k" * 100_000: 1})
    err = res.structured_content["error"]
    assert err["code"] == "invalid_arguments"
    assert len(err["details"]["field"]) <= server._MAX_ARG_FIELD_LEN + 1
    assert len(json.dumps(res.structured_content)) < 5_000


async def test_invalid_arguments_count_is_capped():
    """Many bad arguments are reported but capped, with the total noted, so a
    request cannot amplify into an unbounded response."""
    args = {f"bogus_{i}": i for i in range(60)}
    res = await server.mcp.call_tool("codex_status", args)
    err = res.structured_content["error"]
    assert err["code"] == "invalid_arguments"
    assert len(err["invalid_arguments"]) <= 25
    assert "60" in err["message"]  # the true total is surfaced


async def test_missing_required_argument_returns_envelope():
    """A missing required argument also maps to invalid_arguments."""
    res = await server.mcp.call_tool("codex_consult", {})
    err = res.structured_content["error"]
    assert err["code"] == "invalid_arguments"
    assert err["details"]["field"] == "question"
    assert err["details"]["reason"]  # mirrored from first invalid_arguments entry
    assert err["details"]["reason"] == err["invalid_arguments"][0]["reason"]
    assert err["invalid_arguments"][0]["field"] == "question"


async def test_invalid_arguments_on_success_only_tool_conforms_to_schema():
    """codex_capabilities/status/models advertised success-only output schemas;
    they now advertise a success|error union so the invalid_arguments envelope
    they can return conforms to the declared output schema. The assertion checks that
    exactly one opaque error branch (ok:false) is present — so it fails if that branch
    is ever dropped (Copilot review, PR #145)."""
    for schema in (server.STATUS_SCHEMA, server.CAPABILITIES_SCHEMA, server.MODEL_CATALOG_SCHEMA):
        branches = schema.get("anyOf", [])
        err_branches = [
            b for b in branches if b.get("properties", {}).get("ok", {}).get("const") is False
        ]
        assert len(err_branches) == 1, f"expected 1 opaque error branch, got {err_branches}"
    res = await server.mcp.call_tool("codex_capabilities", {"nope": 1})
    assert res.is_error is True
    assert res.structured_content["error"]["code"] == "invalid_arguments"


async def test_invalid_arguments_advertised_for_every_tool():
    """invalid_arguments is now reachable for all tools, so capabilities lists it."""
    caps = server.codex_capabilities()
    for cap in caps["tool_details"]:
        assert "invalid_arguments" in cap["error_codes"], cap["name"]


def test_unrelated_validation_error_is_not_misclassified():
    """A ValidationError whose locations are not request arguments (e.g. an
    output-validation failure) must NOT be mapped to invalid_arguments — the
    helper returns None so the middleware re-raises it as a real error."""
    out = server._invalid_arguments_envelope(
        "codex_status",
        param_names=set(),
        property_schemas={},
        errors=[{"type": "model_type", "loc": ("error", "code"), "msg": "x", "input": None}],
    )
    assert out is None


class _FakeCallCtx:
    def __init__(self, name):
        self.message = type("Msg", (), {"name": name})()


async def test_middleware_reraises_non_argument_validation_error():
    """A ValidationError whose locations are not request arguments propagates
    unchanged (the middleware does not mask it as invalid_arguments) (#136)."""
    mw = server._ArgumentValidationMiddleware()
    err = ValidationError.from_exception_data(
        "X", [{"type": "missing", "loc": ("error", "code"), "input": {}}]
    )

    async def call_next(_ctx):
        raise err

    with pytest.raises(ValidationError):
        await mw.on_call_tool(_FakeCallCtx("codex_status"), call_next)


async def test_middleware_reraises_when_tool_introspection_fails(monkeypatch):
    """If the tool's schema cannot be introspected, the original ValidationError is
    preserved rather than guessed at (#136)."""
    mw = server._ArgumentValidationMiddleware()
    err = ValidationError.from_exception_data(
        "X", [{"type": "missing", "loc": ("bogus",), "input": {}}]
    )

    async def call_next(_ctx):
        raise err

    async def boom(_name):
        raise RuntimeError("cannot introspect")

    monkeypatch.setattr(server.mcp, "get_tool", boom)
    with pytest.raises(ValidationError):
        await mw.on_call_tool(_FakeCallCtx("codex_status"), call_next)


async def test_isolation_error_lists_allowed_values(clean_env, tmp_path):
    """unsupported_isolation surfaces the valid set as machine-readable allowed_values."""
    res = await server.codex_consult("q", workspace_root=str(tmp_path), isolation="bogus")
    assert res["error"]["code"] == "unsupported_isolation"
    assert res["error"]["details"]["allowed_values"] == list(get_args(Isolation))


async def test_scope_error_lists_allowed_values(monkeypatch, clean_env, tmp_path):
    """invalid_scope surfaces the valid review scopes as allowed_values."""

    def raise_scope(*a, **k):
        raise gitdiff.InvalidScopeError("bad scope")

    monkeypatch.setattr(gitdiff, "gather_diff", raise_scope)
    res = await _run_review_direct(tmp_path, scope="nope")
    assert res["error"]["code"] == "invalid_scope"
    assert res["error"]["details"]["field"] == "scope"
    assert res["error"]["details"]["allowed_values"] == list(get_args(ReviewScope))


async def test_job_running_error_is_actionable(monkeypatch, clean_env, tmp_path):
    """job_running points at the recovery tool with concrete params and a backoff."""
    store = _FakeStore(record=_ok_record("running"), result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    err = res["error"]
    assert err["code"] == "job_running"
    assert err["temporary"] is True
    assert err["repair"]["next_step"] == "poll_job_status"
    assert err["repair"]["tool"] == "codex_job_status"
    # repair params carry both the job_id AND the caller's workspace_root, so the
    # poll targets the same workspace rather than risking a wrong-workspace miss.
    assert err["repair"]["arguments"] == {"job_id": "job-abc", "workspace_root": str(tmp_path)}
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
    assert res["error"]["repair"]["arguments"] == {"job_id": "job-abc"}


async def test_job_not_found_points_at_list(monkeypatch, clean_env, tmp_path):
    """job_not_found names codex_job_list as the way to recover known job_ids."""
    store = _FakeStore(record=None, result_json=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("missing", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_not_found"
    assert res["error"]["details"]["field"] == "job_id"
    assert res["error"]["repair"]["next_step"] == "list_jobs"
    assert res["error"]["repair"]["tool"] == "codex_job_list"
    # codex_job_list takes only workspace_root (not job_id) — echo it so the
    # recovery lists jobs in the same workspace the lookup used.
    assert res["error"]["repair"]["arguments"] == {"workspace_root": str(tmp_path)}


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
    # Reachable codes are advertised; schema-gated (Literal-param) codes are not (#92).
    assert "invalid_workspace_root" in details["codex_consult"]["error_codes"]
    assert "invalid_base" in details["codex_review_changes"]["error_codes"]
    assert "invalid_scope" not in details["codex_review_changes"]["error_codes"]
    assert "job_running" in details["codex_job_result"]["error_codes"]


@pytest.mark.parametrize(
    ("tool_name", "read_only", "idempotent"),
    [
        ("codex_job_status", True, None),
        ("codex_job_result", True, None),
        ("codex_job_list", True, None),
        ("codex_job_consume_result", False, False),
        ("codex_job_cancel", False, True),
    ],
)
async def test_job_lifecycle_annotations_split_read_from_mutation(tool_name, read_only, idempotent):
    """Read/inspect job tools are read-only; consume/cancel mutate state (issue #9).

    cancel mutates (not read-only) but is idempotent: terminal jobs are returned
    unchanged, so a retry after a lost response has no additional effect (#141).
    consume stays non-idempotent — a repeat consume returns not-found, a different
    response, since the first call deleted the record. Read-only tools omit
    idempotentHint/destructiveHint entirely — those hints have MCP-spec meaning only
    when readOnlyHint is false (audit F4)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    ann = tools[tool_name].annotations
    assert ann.readOnlyHint is read_only
    assert ann.idempotentHint is idempotent
    # Every job tool is local (closed-world), so it's non-destructive; read-only
    # tools omit destructiveHint (audit F4), mutating tools state it explicitly.
    assert ann.openWorldHint is False
    assert ann.destructiveHint is (False if not read_only else None)


async def test_job_cancel_is_idempotent_but_not_read_only():
    """codex_job_cancel mutates job state (not read-only) yet is idempotent: a
    terminal job is returned unchanged and cancellation re-validates concurrent
    completion, so a retry after a lost response is safe and has no additional
    effect. The earlier idempotentHint:false deterred that safe retry (#141)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    ann = tools["codex_job_cancel"].annotations
    assert ann.readOnlyHint is False
    assert ann.idempotentHint is True
    assert ann.openWorldHint is False
    assert ann.destructiveHint is False


@pytest.mark.parametrize(
    "tool_name",
    ["codex_consult_async", "codex_review_changes_async", "codex_delegate_async"],
)
async def test_async_launchers_are_not_read_only(tool_name):
    """Every *_async launcher creates an observable, mutable, spend-committing job
    record that outlives the response, so none may advertise readOnlyHint — even
    consult/review whose underlying run is read-only (issue #138)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    ann = tools[tool_name].annotations
    assert ann.readOnlyHint is False
    assert ann.idempotentHint is False
    assert ann.openWorldHint is True
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
    model = server._job_status_model(data, server._job_workspace("/repo", "param"))
    assert model.cleanup_warnings == ["could not remove temporary path: /tmp/cic-worktree-x"]
    assert model.workspace.cwd == "/repo"


def test_job_status_model_maps_activity_fields():
    from codex_in_claude.schemas import Workspace

    data = {
        "job_id": "j",
        "kind": "codex_consult",
        "status": "running",
        "started_at": "t",
        "elapsed_ms": 5,
        "deadline_seconds": 60,
        "poll_after_ms": 1000,
        "ttl_seconds": 60,
        "expires_at": None,
        "result_available": False,
        "cleanup_warnings": [],
        "events_seen": 3,
        "last_event_at": "2026-06-27T00:00:00+00:00",
        "event_age_ms": 250,
    }
    model = server._job_status_model(data, Workspace(cwd="/x", workspace_source="param"))
    assert model.events_seen == 3
    assert model.last_event_at == "2026-06-27T00:00:00+00:00"
    assert model.event_age_ms == 250


# --- boundary: unexpected exceptions become a structured internal_error (#39) ---
async def test_consult_unexpected_exception_returns_internal_error(
    monkeypatch, clean_env, tmp_path
):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    # Inject an unexpected exception into the handler body via the job-start seam
    # (the sync tool now dispatches through the detached worker, #169).
    monkeypatch.setattr(server, "_start_job", boom)
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert res["error"]["temporary"] is True
    # The documented envelope still holds: meta is present and tier reflects the tool.
    assert res["meta"]["tier"] == "consult"
    assert res["meta"]["sandbox"] == "read-only"


async def test_review_unexpected_exception_returns_internal_error(monkeypatch, clean_env, tmp_path):
    # An unexpected exception escaping the review dispatch must be caught by the tool
    # boundary and become a structured internal_error (not an opaque error).
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(server, "_start_job", boom)
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

    import time

    def slow_boom(*a, **k):
        time.sleep(0.02)
        raise RuntimeError("late failure")

    monkeypatch.setattr(server, "_start_job", slow_boom)
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    # A late failure records its elapsed time, not a misleading 0.
    assert res["meta"]["elapsed_ms"] > 0


# --- exception-derived client-visible text is redacted before return (#186/F10) ---
def _meta_for(tmp_path):
    return server._base_meta(
        str(tmp_path),
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=180,
    )


def test_internal_error_result_redacts_secret_in_exception_text(clean_env, tmp_path):
    # F10: an unexpected exception whose message carries a secret-looking value must not
    # reach the client raw — redact it like orchestration.gitdiff_error does.
    exc = RuntimeError("upstream blew up with token AKIAIOSFODNN7EXAMPLE")
    res = server._internal_error_result("codex_consult", exc, tier="consult", sandbox="read-only")
    msg = res["error"]["message"]
    assert "AKIAIOSFODNN7EXAMPLE" not in msg
    assert "[redacted: secret value]" in msg
    # The safe exception class name is still preserved for debugging.
    assert "RuntimeError" in msg


def test_spawn_failure_envelope_redacts_secret_in_exception_text(clean_env, tmp_path):
    # F10: the spawn-failure internal_error is a second exception-text sink.
    exc = OSError("cannot exec /home/AKIAIOSFODNN7EXAMPLE/worker")
    res = server._spawn_failure_envelope(exc, _meta_for(tmp_path))
    msg = res["error"]["message"]
    assert "AKIAIOSFODNN7EXAMPLE" not in msg
    assert "[redacted: secret value]" in msg
    # The safe exception class name is preserved, consistent with the other sinks.
    assert "OSError" in msg


def test_job_result_corrupt_redacts_secret_in_detail(clean_env, tmp_path):
    # F10: the corrupt-stored-result internal_error interpolates ValidationError text,
    # which can echo stored payload fragments — redact its detail at the sink.
    res = server._job_result_corrupt(
        "stored codex_consult result did not match its schema: AKIAIOSFODNN7EXAMPLE",
        _meta_for(tmp_path),
    )
    msg = res["error"]["message"]
    assert "AKIAIOSFODNN7EXAMPLE" not in msg
    assert "[redacted: secret value]" in msg


async def test_job_result_malformed_error_payload_redacts_secret(monkeypatch, clean_env, tmp_path):
    # End-to-end: a stored ok:false payload whose malformed `error` value carries a secret
    # leaks it via the Pydantic ValidationError's input_value echo — the returned message
    # must redact it (regression through the real _finished_job_envelope path, not a mock).
    rec = _ok_record("done")
    store = _FakeStore(record=rec, result_json={"ok": False, "error": "AKIAIOSFODNN7EXAMPLE"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert "AKIAIOSFODNN7EXAMPLE" not in res["error"]["message"]
    assert "[redacted: secret value]" in res["error"]["message"]


async def test_job_result_valid_stored_error_message_redacted(monkeypatch, clean_env, tmp_path):
    # F10 boundary redact: a SCHEMA-VALID stored ErrorResult (e.g. written by a pre-fix
    # worker still within its TTL) whose message carries a secret is returned via
    # serialize_error(validated) — the return boundary must redact it too.
    from codex_in_claude.errors import make_error as _make_error
    from codex_in_claude.errors import serialize_error as _serialize_error
    from codex_in_claude.schemas import ErrorResult as _ErrorResult

    meta = _meta_for(tmp_path).model_dump(mode="json")
    stored = _serialize_error(
        _ErrorResult(
            error=_make_error("internal_error", "prior crash leaked AKIAIOSFODNN7EXAMPLE"),
            meta=_meta_for(tmp_path),
        )
    )
    stored["meta"] = meta
    rec = _ok_record("done")
    store = _FakeStore(record=rec, result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert "AKIAIOSFODNN7EXAMPLE" not in res["error"]["message"]
    assert "[redacted: secret value]" in res["error"]["message"]


async def test_job_result_valid_stored_error_message_preserved_when_clean(
    monkeypatch, clean_env, tmp_path
):
    # The boundary redact must not alter legitimate, non-secret stored error text — and it
    # only touches internal_error (domain errors are already redacted at write time).
    from codex_in_claude.errors import make_error as _make_error
    from codex_in_claude.errors import serialize_error as _serialize_error
    from codex_in_claude.schemas import ErrorResult as _ErrorResult

    meta = _meta_for(tmp_path).model_dump(mode="json")
    # A domain error whose message merely resembles a token must pass through verbatim.
    stored = _serialize_error(
        _ErrorResult(
            error=_make_error("git_unavailable", "git failed near ref AKIAIOSFODNN7EXAMPLE"),
            meta=_meta_for(tmp_path),
        )
    )
    stored["meta"] = meta
    store = _FakeStore(record=_ok_record("done"), result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "git_unavailable"
    # Non-internal_error stored messages are returned untouched (no boundary redaction).
    assert res["error"]["message"] == "git failed near ref AKIAIOSFODNN7EXAMPLE"


async def test_boundary_propagates_cancellation(monkeypatch, clean_env, tmp_path):

    def cancel(*a, **k):
        raise asyncio.CancelledError

    monkeypatch.setattr(server, "_start_job", cancel)
    with pytest.raises(asyncio.CancelledError):
        await server.codex_consult("q", workspace_root=str(tmp_path))


# --- structured repair fields for size/workspace errors (#95) ----------------
async def test_input_too_large_carries_size_fields_consult(monkeypatch, clean_env, tmp_path):
    """input_too_large exposes the byte limit and the offending input's actual size in
    machine-readable fields, while keeping the prose repair (#95)."""
    monkeypatch.setattr(server.config, "max_input_bytes", lambda: 10)
    res = await server.codex_consult("x" * 50, workspace_root=str(tmp_path))
    assert res["ok"] is False
    err = res["error"]
    assert err["code"] == "input_too_large"
    assert err["limit_bytes"] == 10
    assert err["actual_bytes"] == 50
    assert "10" in err["message"] and err["repair"]  # prose retained


async def test_input_too_large_carries_size_fields_delegate(monkeypatch, clean_env, tmp_path):
    """The task-input path (delegate) also carries limit_bytes/actual_bytes (#95)."""
    monkeypatch.setattr(server.config, "max_input_bytes", lambda: 10)
    res = await server.codex_delegate("x" * 50, workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "input_too_large"
    assert res["error"]["details"]["field"] == "task"
    assert res["error"]["limit_bytes"] == 10
    assert res["error"]["actual_bytes"] == 50


async def test_workspace_outside_roots_carries_candidate_roots(monkeypatch, clean_env, tmp_path):
    """workspace_outside_roots attaches the client-supplied MCP roots as candidate_roots
    so an agent can pick a valid workspace_root without parsing prose (#95)."""
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()

    async def fake_roots(ctx):
        return [str(root)]

    monkeypatch.setattr(server, "_roots_from_ctx", fake_roots)
    res = await server.codex_consult("q", workspace_root=str(outside))
    assert res["ok"] is False
    assert res["error"]["code"] == "workspace_outside_roots"
    assert res["error"]["candidate_roots"] == [str(root)]


async def test_invalid_workspace_root_omits_candidate_roots(monkeypatch, clean_env, tmp_path):
    """candidate_roots is scoped to the outside-roots error only — an invalid (relative)
    workspace_root leaves it null even when client roots are present (#95)."""
    root = tmp_path / "repo"
    root.mkdir()

    async def fake_roots(ctx):
        return [str(root)]

    monkeypatch.setattr(server, "_roots_from_ctx", fake_roots)
    res = await server.codex_consult("q", workspace_root="relative/not/abs")
    assert res["error"]["code"] == "invalid_workspace_root"
    assert res["error"].get("candidate_roots") is None


async def test_roots_from_ctx_filters_non_absolute_and_non_file(tmp_path):
    """_roots_from_ctx returns only non-empty absolute file:// paths, so candidate_roots
    never advertises a malformed (empty/relative) or non-file root (#95, Copilot review)."""

    class _Root:
        def __init__(self, uri):
            self.uri = uri

    class _Ctx:
        async def list_roots(self):
            return [
                _Root(f"file://{tmp_path}"),  # valid absolute (empty authority) -> kept
                _Root(f"file://localhost{tmp_path}"),  # localhost authority -> kept
                _Root("file:relative/path"),  # relative -> dropped
                _Root("file://"),  # empty path -> dropped
                _Root("file://example.com/tmp/repo"),  # remote host -> dropped
                _Root("file://C:/repo"),  # drive-letter authority -> dropped
                _Root("https://example.com"),  # non-file scheme -> dropped
            ]

    paths = await server._roots_from_ctx(_Ctx())
    assert paths == [str(tmp_path), str(tmp_path)]


# --- async job-lifecycle capability metadata (#94) ---------------------------
def test_async_tools_advertise_job_lifecycle_metadata():
    """Each *_async tool structurally declares no native task/progress support and the
    custom codex_job_* lifecycle; the referenced tools and JobStatus fields are real, so
    the metadata stays consistent with the registered surface (#94)."""
    from codex_in_claude.schemas import JobStatus

    caps = server.codex_capabilities()
    by_name = {t["name"]: t for t in caps["tool_details"]}
    all_tools = set(caps["active_tools"]) | set(caps["free_tools"])
    async_tools = {"codex_consult_async", "codex_review_changes_async", "codex_delegate_async"}
    status_fields = set(JobStatus.model_fields)
    for name in async_tools:
        meta = by_name[name].get("async_lifecycle")
        assert meta is not None, name
        assert meta["native_task_support"] is False
        assert meta["progress_support"] == "none"
        assert meta["lifecycle"] == "codex_job_*"
        # Every referenced lifecycle tool is a real, registered tool.
        for key in ("poll_tool", "result_tool", "consume_tool", "cancel_tool", "list_tool"):
            assert meta[key] in all_tools, (name, key, meta[key])
        # Every referenced JobStatus field actually exists on the model.
        for key in ("status_field", "result_ready_field", "poll_after_field"):
            assert meta[key] in status_fields, (name, key, meta[key])


def test_non_async_tools_omit_lifecycle_metadata():
    """async_lifecycle is omitted (exclude_none) for sync and job-lifecycle tools — only
    the *_async tools carry it (#94)."""
    caps = server.codex_capabilities()
    async_tools = {"codex_consult_async", "codex_review_changes_async", "codex_delegate_async"}
    for cap in caps["tool_details"]:
        if cap["name"] not in async_tools:
            assert "async_lifecycle" not in cap, cap["name"]


# --- MCP boundary: protocol isError flag (#91) -------------------------------
# These go through the real MCP boundary via an in-memory Client, so they assert
# the protocol-level `is_error` flag a conformant client keys off — not just the
# `ok` field inside our envelope, which the direct-call tests above cover.
async def test_mcp_success_path_reports_is_error_false(clean_env):
    from fastmcp import Client

    async with Client(server.mcp) as client:
        result = await client.call_tool("codex_capabilities", {}, raise_on_error=False)
    assert result.is_error is False
    assert result.structured_content["ok"] is True


async def test_mcp_semantic_failure_reports_is_error_true(clean_env):
    """A handler-level failure (`ok: false`) must map to MCP `isError: true` while
    leaving the ErrorInfo envelope intact in structured_content (#91)."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "codex_consult",
            {"question": "q", "workspace_root": "relative/not/abs"},
            raise_on_error=False,
        )
    assert result.is_error is True
    # The envelope still carries the structured error for clients that parse it.
    assert result.structured_content["ok"] is False
    assert result.structured_content["error"]["code"] == "invalid_workspace_root"


# NOTE: the run-failure MCP-boundary is_error assertion for the now-worker-routed sync
# path lives in test_sync_run_failure_reports_is_error_true (a job-produced error
# envelope flows through the boundary with is_error=True), since the sync tool no
# longer runs Codex in-process.


# --- initialize: no empty prompts capability (F5, audit) ---------------------
async def test_initialize_does_not_advertise_prompts(clean_env):
    """The server registers no MCP prompts; advertising the capability over an empty,
    static catalog misleads clients (audit F5)."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        caps = client.initialize_result.capabilities
    assert caps.prompts is None
    assert caps.tools is not None  # the override must not clobber siblings
    assert caps.resources is not None
    # `caps.prompts is None` alone can't distinguish an omitted wire key from an
    # explicit `"prompts": null` — both parse back to None. The mcp SDK serializes
    # InitializeResult via `model_dump(exclude_none=True)`, which recurses into nested
    # models, so re-run that exact seam and assert the key is actually absent.
    wire = caps.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert "prompts" not in wire


# --- advertised error codes must be MCP-reachable (#92) -----------------------
# A code whose only production path is an out-of-enum value on a Literal-typed param
# is rejected by FastMCP validation before the handler runs, so a real MCP caller can
# never receive its envelope. These must not be advertised per-tool.
_ENUM_PARAM_TO_GATED_CODE = {
    "isolation": "unsupported_isolation",
    "detail": "unsupported_detail",
    "scope": "invalid_scope",
}


def _is_enum_param(spec: object) -> bool:
    """True if a JSON-Schema property is enum-constrained, including an Optional param
    whose enum lives inside an `anyOf` branch (e.g. `isolation: Isolation | None`).
    Delegates enum extraction to `_param_enum` so the two stay in lockstep."""
    return isinstance(spec, dict) and _param_enum(spec) is not None


async def test_advertised_error_codes_exclude_schema_gated(clean_env):
    """No tool advertises an error code that is unreachable over MCP because its only
    trigger is an out-of-enum value on a Literal-typed param (#92). Inspects the real
    advertised input schemas via the MCP boundary, so it guards against future drift."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    caps = {t["name"]: t for t in server.codex_capabilities()["tool_details"]}
    covered: set[str] = set()
    for tool in tools:
        props = (tool.inputSchema or {}).get("properties", {})
        advertised = set(caps.get(tool.name, {}).get("error_codes", []))
        for param, gated_code in _ENUM_PARAM_TO_GATED_CODE.items():
            if _is_enum_param(props.get(param)):
                covered.add(gated_code)
                assert gated_code not in advertised, (tool.name, param, gated_code)
    # Guard against a vacuous pass: each gated code must actually be reached by at least
    # one enum-constrained param somewhere, or the assertions above prove nothing.
    assert covered == set(_ENUM_PARAM_TO_GATED_CODE.values())


def _is_our_error_envelope(structured_content: object) -> bool:
    """True if a call_tool result carries *our* ErrorResult envelope — i.e. the handler
    ran and produced a structured error. The MCP-unreachability invariant is that a bad
    enum value never produces this (FastMCP rejects it during input validation first).
    Asserting "not our envelope" rather than `structured_content is None` keeps the test
    robust if a future FastMCP (the repo pins no upper bound) attaches its own structured
    validation details. Matches the full `ErrorResult` shape (`ok: false` + nested
    `error.code`), not a bare `ok: false`, so unrelated structured details that merely
    carry an `ok` field are not mistaken for our envelope."""
    return (
        isinstance(structured_content, dict)
        and structured_content.get("ok") is False
        and isinstance(structured_content.get("error"), dict)
        and "code" in structured_content["error"]
    )


async def test_mcp_bad_enum_value_returns_invalid_arguments(clean_env, tmp_path):
    """A bad Literal value is rejected by MCP input validation, and that rejection is
    re-emitted as OUR `invalid_arguments` envelope at the call boundary (#136) — NOT
    the per-param unsupported_*/invalid_scope codes, which stay unreachable/unadvertised
    by their own symbolic code (#92)."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        for args, param in (
            ({"question": "q", "workspace_root": str(tmp_path), "isolation": "bogus"}, "isolation"),
            ({"question": "q", "workspace_root": str(tmp_path), "detail": "verbose"}, "detail"),
        ):
            res = await client.call_tool("codex_consult", args, raise_on_error=False)
            assert res.is_error is True
            assert _is_our_error_envelope(res.structured_content)
            assert res.structured_content["error"]["code"] == "invalid_arguments"
            assert res.structured_content["error"]["details"]["field"] == param
        res = await client.call_tool(
            "codex_review_changes",
            {"scope": "everything", "workspace_root": str(tmp_path)},
            raise_on_error=False,
        )
        assert res.is_error is True
        assert res.structured_content["error"]["code"] == "invalid_arguments"
        # the legacy per-param code is never the one returned over MCP
        assert res.structured_content["error"]["code"] != "invalid_scope"


# --- input schemas describe ambiguous params (#93) ---------------------------
# Each param maps to a lowercase substring its advertised description must contain, so
# the test pins meaning (not mere presence) and guards against drift.
_DESCRIBED_PARAMS = {
    "workspace_root": "absolute",
    "base": "branch",
    "commit": "commit",
    "paths": "repo-relative",
    "model": "model",
    "timeout_seconds": "clamp",
    "question": "codex",
    "task": "implement",
    "extra_context": "context",
    "job_id": "job",
    "scope": "review",
    "detail": "verbosity",
    "isolation": "isolation",
}


async def test_input_schemas_describe_ambiguous_params(clean_env):
    """Ambiguous params carry a meaningful `description` in the advertised input schema,
    so an agent need not parse docstring prose to use them correctly (#93). Inspects the
    real schemas via the MCP boundary."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    seen: set[str] = set()
    for tool in tools:
        props = (tool.inputSchema or {}).get("properties", {})
        for param, must_contain in _DESCRIBED_PARAMS.items():
            if param in props:
                seen.add(param)
                desc = props[param].get("description", "")
                assert desc, (tool.name, param)
                assert must_contain in desc.lower(), (tool.name, param, desc)
    # Non-vacuous: every named param actually appears on at least one tool.
    assert seen == set(_DESCRIBED_PARAMS)


async def test_timeout_seconds_description_matches_clamp_behavior(clean_env):
    """The timeout_seconds description states the 10..600 clamp (and that out-of-range
    is coerced, not rejected), so the schema agrees with clamp_timeout() runtime (#93)."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    consult = next(t for t in tools if t.name == "codex_consult")
    spec = consult.inputSchema["properties"]["timeout_seconds"]
    desc = spec["description"]
    assert "10" in desc and "600" in desc
    # No numeric schema constraint — behavior is clamp, not reject.
    assert "minimum" not in spec and "maximum" not in spec


async def test_delegate_dry_run_param_descriptions_do_not_claim_a_run(clean_env):
    """codex_delegate_dry_run reuses task/model but never calls Codex or returns a diff,
    so its descriptions must not imply an active run (#93, Codex review)."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    dry = next(t for t in tools if t.name == "codex_delegate_dry_run")
    props = dry.inputSchema["properties"]
    task_desc = props["task"]["description"].lower()
    assert "does not call codex" in task_desc and "return a diff" in task_desc
    assert "does not call codex" in props["model"]["description"].lower()


# --- codex_models tool + codex://models resource -----------------------------


def test_codex_models_tool_returns_advisory_catalog():
    res = server.codex_models()
    assert res["ok"] is True
    assert res["source"] in {"cache", "static", "none"}
    assert res["advisory"]
    assert res["fingerprint"] == server.FINGERPRINT


def test_codex_models_listed_as_free_tool_and_detailed():
    caps = server.codex_capabilities()
    assert "codex_models" in caps["free_tools"]
    by_name = {t["name"]: t for t in caps["tool_details"]}
    assert "codex_models" in by_name
    assert by_name["codex_models"]["cost"] == "free"


async def test_codex_models_resource_matches_tool_payload():
    # FastMCP 3.x returns a ResourceResult with .contents list;
    # each ResourceContent has a .content str (serialized JSON).
    result = await server.mcp.read_resource("codex://models")
    payload = json.loads(result.contents[0].content)
    assert payload == server.codex_models()


# --- rate_limit field on codex_status ----------------------------------------


def test_codex_status_includes_rate_limit_unknown_without_cache(monkeypatch):
    from codex_in_claude import rate_limit, server

    monkeypatch.setattr(rate_limit, "_load_raw", lambda path=None: None)
    result = server.codex_status()
    assert result["rate_limit"]["status"] == "unknown"
    assert result["rate_limit"]["note"]


def test_codex_status_reports_cached_snapshot(monkeypatch):
    from codex_in_claude import rate_limit, server
    from codex_in_claude.config import codex_home as _codex_home

    monkeypatch.setattr(
        rate_limit,
        "_load_raw",
        lambda path=None: {
            "version": rate_limit.CACHE_VERSION,
            "captured_at": 1,
            "codex_home": str(_codex_home()),
            "snapshot": {
                "plan_type": "plus",
                "primary": {
                    "used_percent": 10.0,
                    "window_minutes": 300,
                    "resets_at": 9999999999,
                },
                "secondary": {
                    "used_percent": 5.0,
                    "window_minutes": 10080,
                    "resets_at": 9999999999,
                },
            },
        },
    )
    result = server.codex_status()
    assert result["rate_limit"]["status"] == "available"
    assert result["rate_limit"]["plan_type"] == "plus"
    assert result["rate_limit"]["source"] == "plugin_cache"
    assert result["rate_limit"]["home_unverified"] is False


def test_codex_status_tolerates_corrupt_cache_envelope(monkeypatch):
    from codex_in_claude import rate_limit, server

    # captured_at as a string would crash arithmetic if not validated -> must degrade.
    monkeypatch.setattr(
        rate_limit,
        "_load_raw",
        lambda path=None: {
            "version": rate_limit.CACHE_VERSION,
            "captured_at": "not-a-number",
            "codex_home": ["bad"],
            "snapshot": {"primary": {"used_percent": 10.0, "resets_at": 9999999999}},
        },
    )
    result = server.codex_status()
    # captured_at invalid -> as_of/age drop out, but interpretation must not raise.
    assert result["rate_limit"]["as_of"] is None
    assert result["rate_limit"]["status"] in {"unknown", "available", "limited", "exhausted"}


# --------------------------------------------------------------------------- #
# codex://error-envelope resource and capabilities pointer (Task 7)
# --------------------------------------------------------------------------- #


def test_error_envelope_resource_returns_full_schema():
    from codex_in_claude.server import error_envelope_resource

    schema = error_envelope_resource()
    assert schema["$defs"], "schema must carry $defs"
    assert "ErrorInfo" in schema["$defs"], "full ErrorInfo shape must live in $defs"


def test_capabilities_advertises_error_envelope_pointer():
    from codex_in_claude.server import codex_capabilities

    caps = codex_capabilities()
    assert caps["error_envelope_resource"] == "codex://error-envelope"


# --------------------------------------------------------------------------- #
# Resource-read failures carry the §6 envelope in JSON-RPC error.data (F9, #181)
# --------------------------------------------------------------------------- #


async def test_unknown_resource_read_carries_error_envelope(clean_env):
    """A resources/read of an unknown URI must no longer return error.data: null — it
    carries the §6 ErrorInfo envelope (code/message/temporary/retry_after_ms/repair) so
    the resource surface matches the unified contract every tool already honors."""
    from fastmcp import Client
    from mcp import McpError

    with pytest.raises(McpError) as excinfo:
        async with Client(server.mcp) as client:
            await client.read_resource("codex://does-not-exist")

    err = excinfo.value.error
    assert err.code == -32002  # MCP numeric "resource not found"
    # The URI/exception text is NOT echoed into the client-visible message (redaction
    # posture, #189) — it is a bounded generic string.
    assert err.message == "Resource not found."
    env = err.data
    assert isinstance(env, dict)
    assert env["code"] == "resource_not_found"
    assert env["temporary"] is False
    assert env["retry_after_ms"] is None  # §6: key present even when null
    assert env["repair"]["next_step"] == "list_resources"
    assert "resources/list" in env["repair"]["alternative"]
    # The bare ErrorInfo shape — no ok/meta wrapper (no Codex run to describe).
    assert "ok" not in env and "meta" not in env


async def test_known_resource_read_is_unaffected(clean_env):
    """The interception must not perturb a successful read."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        contents = await client.read_resource("codex://models")
    assert contents and contents[0].text  # payload still delivered


async def test_resource_error_middleware_maps_read_failure_to_internal_error():
    """A ResourceError (a resource function raised; FastMCP's core wraps arbitrary
    handler exceptions into it) maps to internal_error with MCP numeric -32603 — the
    branch our static resources can't exercise end-to-end, driven directly here."""
    from fastmcp.exceptions import ResourceError
    from mcp import McpError

    mw = server._ResourceErrorMiddleware()

    async def call_next(_ctx):
        raise ResourceError("boom (would leak internal detail)")

    with pytest.raises(McpError) as excinfo:
        await mw.on_read_resource(object(), call_next)
    err = excinfo.value.error
    assert err.code == -32603
    assert err.message == "Resource read failed."  # generic; no exception text echoed
    assert err.data["code"] == "internal_error"


async def test_resource_error_middleware_does_not_reclassify_mcp_error():
    """An McpError raised by an inner layer keeps its own code/data — the middleware
    only wraps FastMCP's NotFoundError/DisabledError/ResourceError, never a protocol
    error that already carries the contract."""
    from mcp import McpError
    from mcp.types import ErrorData

    mw = server._ResourceErrorMiddleware()
    original = McpError(ErrorData(code=-32000, message="deliberate", data={"x": 1}))

    async def call_next(_ctx):
        raise original

    with pytest.raises(McpError) as excinfo:
        await mw.on_read_resource(object(), call_next)
    assert excinfo.value.error.code == -32000
    assert excinfo.value.error.data == {"x": 1}


def test_capabilities_advertises_resource_error_carrier(clean_env):
    """The resource error carrier is stated up front so a client need not infer the
    error.data shape from a first failure (F9, #181)."""
    from codex_in_claude.server import codex_capabilities

    carrier = codex_capabilities()["resource_error_carrier"]
    assert "error.data" in carrier
    assert "-32002" in carrier


# --------------------------------------------------------------------------- #
# codex://result-meta resource + capabilities pointer + opt-in fallback (F1/#179)
# --------------------------------------------------------------------------- #


def test_result_meta_resource_returns_full_schema():
    from codex_in_claude.server import result_meta_resource

    schema = result_meta_resource()
    # The full Meta contract the opaque wire stub hides.
    props = schema["properties"]
    for field in ("cwd", "tier", "sandbox", "usage", "rate_limit", "fingerprint"):
        assert field in props, f"result-meta missing {field}"


def test_capabilities_advertises_result_meta_pointer():
    from codex_in_claude.server import codex_capabilities

    assert codex_capabilities()["result_meta_resource"] == "codex://result-meta"


def test_capabilities_omits_schemas_by_default():
    from codex_in_claude.server import codex_capabilities

    # The opt-in fallback must not bloat the default payload (#179 caveat).
    assert "schemas" not in codex_capabilities()


def test_capabilities_include_schemas_embeds_requested_contracts():
    from codex_in_claude.server import codex_capabilities

    caps = codex_capabilities(include_schemas=["error-envelope", "result-meta"])
    assert set(caps["schemas"]) == {"error-envelope", "result-meta"}
    # The embedded schemas are the real, full contracts.
    assert "ErrorInfo" in caps["schemas"]["error-envelope"]["$defs"]
    assert "tier" in caps["schemas"]["result-meta"]["properties"]


def test_capabilities_include_schemas_single_and_deduped():
    from codex_in_claude.server import codex_capabilities

    caps = codex_capabilities(include_schemas=["result-meta", "result-meta"])
    assert list(caps["schemas"]) == ["result-meta"]


# --------------------------------------------------------------------------- #
# destructive/idempotent hints only have MCP-spec meaning when readOnlyHint is
# false (audit F4) — read-only tools must omit them, not assert them.
# --------------------------------------------------------------------------- #


async def test_read_only_tools_omit_meaningless_hints(clean_env):
    """destructiveHint/idempotentHint have spec meaning only when readOnlyHint is
    false (audit F4) — read-only tools must omit them, not assert them."""
    from fastmcp import Client

    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    for tool in tools:
        ann = tool.annotations
        if ann is not None and ann.readOnlyHint is True:
            assert ann.destructiveHint is None, tool.name
            assert ann.idempotentHint is None, tool.name


# --- F3: sync active calls run through the detached worker (#169) -------------
# The sync consult/review/delegate tools now build the same worker spec their async
# twins build, start a detached job, and await its result in-handler. These tests use
# a fake `_worker_cmd` that writes a canned envelope (real JobStore, real await loop)
# so a dropped connection leaves the result recoverable while explicit cancellation
# still stops spend. A sentinel on `run_codex_exec` proves the sync handler never runs
# Codex in-process (the worker subprocess does) — and keeps these tests spend-free.
from fastmcp import Client  # noqa: E402


def _fake_worker_cmd(envelope: dict):
    """A `_worker_cmd` replacement whose worker writes `result.json` (atomically)
    then exits — mirroring the real worker's terminal write (the JobStore derives
    `done` from result.json presence + process exit; no meta mutation needed)."""
    payload = json.dumps(envelope)

    def factory(job_dir: object) -> list[str]:
        code = (
            "import os,sys,pathlib;"
            "d=pathlib.Path(sys.argv[1]);"
            "t=d/'result.json.tmp';"
            "t.write_text(sys.argv[2]);"
            "os.replace(str(t), str(d/'result.json'))"
        )
        return [sys.executable, "-c", code, str(job_dir), payload]

    return factory


def _sleeping_worker_cmd(seconds: float = 60.0):
    """A worker that sleeps without ever writing a result — so the job stays running
    until it is cancelled/timed out."""

    def factory(job_dir: object) -> list[str]:
        snippet = "import time,sys; time.sleep(float(sys.argv[1]))"
        return [sys.executable, "-c", snippet, str(seconds)]

    return factory


def _no_codex_sentinel(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("sync tool must not run Codex in-process; the worker does")

    monkeypatch.setattr(server.codex, "run_codex_exec", _boom)


def _consult_success_envelope(
    cwd: str, *, raw_text: str | None = None, summary: str = "Looks fine"
):
    meta = server._base_meta(
        cwd,
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=180,
    ).model_dump(mode="json")
    return {
        "ok": True,
        "tool": "codex_consult",
        "summary": summary,
        "findings": [],
        "questions": [],
        "raw_response": {"text": raw_text, "session_id": None, "model": None},
        "meta": meta,
    }


def _timeout_error_envelope(cwd: str):
    meta = server._base_meta(
        cwd,
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=180,
    )
    return server.serialize_error(
        server.ErrorResult(
            error=server.make_error("timeout", "codex run exceeded its timeout."), meta=meta
        )
    )


async def test_sync_consult_runs_through_job_store_and_sets_job_id(
    clean_env, tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    envelope = _consult_success_envelope(str(tmp_path))
    monkeypatch.setattr(server, "_worker_cmd", _fake_worker_cmd(envelope))
    async with Client(server.mcp) as client:
        res = await client.call_tool(
            "codex_consult", {"question": "q", "workspace_root": str(tmp_path)}
        )
    body = res.structured_content
    assert body["ok"] is True
    job_id = body["meta"]["job_id"]
    assert job_id
    # The record survives for recovery after a (hypothetical) dropped connection.
    async with Client(server.mcp) as client:
        again = await client.call_tool(
            "codex_job_result",
            {"job_id": job_id, "workspace_root": str(tmp_path), "detail": "full"},
        )
    assert again.structured_content["ok"] is True


async def test_sync_summary_response_but_full_recoverable(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    envelope = _consult_success_envelope(str(tmp_path), raw_text="RAW MODEL TEXT")
    monkeypatch.setattr(server, "_worker_cmd", _fake_worker_cmd(envelope))
    async with Client(server.mcp) as client:
        res = await client.call_tool(
            "codex_consult", {"question": "q", "workspace_root": str(tmp_path)}
        )
        assert res.structured_content["raw_response"]["text"] is None  # detail=summary default
        job_id = res.structured_content["meta"]["job_id"]
        full = await client.call_tool(
            "codex_job_result",
            {"job_id": job_id, "workspace_root": str(tmp_path), "detail": "full"},
        )
        assert full.structured_content["raw_response"]["text"] == "RAW MODEL TEXT"


async def test_sync_full_detail_keeps_raw_text(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    envelope = _consult_success_envelope(str(tmp_path), raw_text="RAW MODEL TEXT")
    monkeypatch.setattr(server, "_worker_cmd", _fake_worker_cmd(envelope))
    res = await server.codex_consult("q", workspace_root=str(tmp_path), detail="full")
    assert res["ok"] is True
    assert res["raw_response"]["text"] == "RAW MODEL TEXT"
    assert res["meta"]["job_id"]


async def test_sync_error_envelope_is_recorded_done(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    envelope = _timeout_error_envelope(str(tmp_path))
    monkeypatch.setattr(server, "_worker_cmd", _fake_worker_cmd(envelope))
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "timeout"


async def test_sync_review_runs_through_job_store(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    meta = server._base_meta(
        str(tmp_path),
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=180,
        scope="working_tree",
    ).model_dump(mode="json")
    envelope = {
        "ok": True,
        "tool": "codex_review_changes",
        "summary": "reviewed",
        "verdict": "pass",
        "confidence": "high",
        "findings": [],
        "raw_response": {"text": None, "session_id": None, "model": None},
        "meta": meta,
    }
    monkeypatch.setattr(server, "_worker_cmd", _fake_worker_cmd(envelope))
    res = await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["tool"] == "codex_review_changes"
    assert res["verdict"] == "pass"
    assert res["meta"]["job_id"]


async def test_sync_delegate_runs_through_job_store(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    _init_repo(tmp_path)  # delegate has a synchronous ensure_repo_with_head preflight
    meta = server._base_meta(
        str(tmp_path),
        "param",
        tier="propose",
        sandbox="workspace-write",
        isolation="inherit",
        model=None,
        timeout_seconds=180,
    ).model_dump(mode="json")
    envelope = {
        "ok": True,
        "tool": "codex_delegate",
        "summary": "did it",
        "diff": "diff --git a/x b/x\n+y",
        "findings": [],
        "raw_response": {"text": None, "session_id": None, "model": None},
        "meta": meta,
    }
    monkeypatch.setattr(server, "_worker_cmd", _fake_worker_cmd(envelope))
    res = await server.codex_delegate("do x", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["tool"] == "codex_delegate"
    assert res["meta"]["job_id"]


async def test_sync_delegate_preflight_not_a_git_repo_records_nothing(
    clean_env, tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)

    def must_not_spawn(_jd):
        raise AssertionError("preflight failure must not start a worker")

    monkeypatch.setattr(server, "_worker_cmd", must_not_spawn)
    res = await server.codex_delegate("x", workspace_root=str(tmp_path))  # tmp_path is no repo
    assert res["ok"] is False
    assert res["error"]["code"] == "not_a_git_repo"
    store = server.config.job_store()
    assert store.list_jobs(str(tmp_path)) == []


def test_sync_preflight_failure_records_nothing(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))

    async def go():
        return await server.codex_consult("q", workspace_root="relative/not/absolute")

    res = asyncio.run(go())
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_workspace_root"
    # No job dirs created anywhere under the state root.
    state = tmp_path / "state"
    assert not state.exists() or not any(state.rglob("meta.json"))


async def test_sync_spawn_failure_is_internal_error_no_record(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    monkeypatch.setattr(server, "_worker_cmd", lambda jd: ["/nonexistent-binary-xyz"])
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    store = server.config.job_store()
    assert store.list_jobs(str(tmp_path)) == []


async def test_sync_review_spawn_failure_is_internal_error(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    monkeypatch.setattr(server, "_worker_cmd", lambda jd: ["/nonexistent-binary-xyz"])
    res = await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert server.config.job_store().list_jobs(str(tmp_path)) == []


async def test_sync_delegate_spawn_failure_is_internal_error(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    _init_repo(tmp_path)  # pass the synchronous ensure_repo_with_head preflight
    monkeypatch.setattr(server, "_worker_cmd", lambda jd: ["/nonexistent-binary-xyz"])
    res = await server.codex_delegate("do x", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert server.config.job_store().list_jobs(str(tmp_path)) == []


async def test_sync_cancellation_cancels_job(clean_env, tmp_path, monkeypatch):
    # The in-process Client does not reliably propagate task cancellation into the
    # handler coroutine, so we test _await_job_result's CancelledError path directly:
    # cancelling the awaiting task must cancel the job (spend stops).
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(server, "_worker_cmd", _sleeping_worker_cmd())
    cwd = str(tmp_path)
    meta = server._base_meta(
        cwd,
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=60,
    )
    handle = server._start_job(
        meta, cwd, kind="codex_consult", spec={"kind": "codex_consult", "cwd": cwd}, deadline=60
    )
    job_id = handle["job_id"]
    task = asyncio.create_task(
        server._await_job_result(cwd, job_id, "codex_consult", meta, "summary", 60, None)
    )
    await asyncio.sleep(0.5)  # let the await loop poll at least once (worker running)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    store = server.config.job_store()
    rec = store.status(cwd, job_id)
    assert rec is not None
    assert rec["status"] == "cancelled"  # spend stopped


def _await_job_result_meta(cwd: str, timeout_seconds: int = 180):
    return server._base_meta(
        cwd,
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=timeout_seconds,
    )


async def test_await_job_result_grace_exhausted_cancels_and_times_out(
    clean_env, tmp_path, monkeypatch
):
    # A job stuck "running" past timeout + grace must be actively cancelled (spend
    # stops) and reported as a timeout, not silently hung or swallowed.
    monkeypatch.setattr(server, "_SYNC_AWAIT_GRACE_S", 0.05)
    monkeypatch.setattr(server, "_SYNC_POLL_INTERVAL_S", 0.01)
    store = _FakeStore(status_dict=_ok_record("running"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    cwd = str(tmp_path)
    meta = _await_job_result_meta(cwd, timeout_seconds=1)
    res = await server._await_job_result(cwd, "job-abc", "codex_consult", meta, "summary", 1, None)
    assert store.cancelled == ["job-abc"]
    assert res["ok"] is False
    assert res["error"]["code"] == "timeout"


async def test_await_job_result_status_disappears_is_internal_error(
    clean_env, tmp_path, monkeypatch
):
    # store.status() returning None mid-await (record evicted/expired) must not
    # crash or hang the awaiting handler; it is an internal_error, not a timeout.
    store = _FakeStore(status_dict=None)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    cwd = str(tmp_path)
    meta = _await_job_result_meta(cwd)
    res = await server._await_job_result(
        cwd, "job-abc", "codex_consult", meta, "summary", 180, None
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


async def test_await_job_result_missing_result_payload_is_internal_error(
    clean_env, tmp_path, monkeypatch
):
    # The job reports done, but result_payload() comes back (None, None) — a
    # corrupt/expired record discovered right after the loop exits. Must surface
    # as internal_error, not a success envelope or a crash.
    store = _FakeStore(status_dict=_ok_record("done"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    cwd = str(tmp_path)
    meta = _await_job_result_meta(cwd)
    res = await server._await_job_result(
        cwd, "job-abc", "codex_consult", meta, "summary", 180, None
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


async def test_sync_call_returns_envelope_even_under_eviction(clean_env, tmp_path, monkeypatch):
    # With a tiny count cap, each new sync call can evict an older terminal record —
    # but every sync call must still return its own envelope successfully.
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CODEX_IN_CLAUDE_JOB_MAX_COUNT", "2")
    _no_codex_sentinel(monkeypatch)
    envelope = _consult_success_envelope(str(tmp_path))
    monkeypatch.setattr(server, "_worker_cmd", _fake_worker_cmd(envelope))
    job_ids = []
    for _ in range(4):
        res = await server.codex_consult("q", workspace_root=str(tmp_path))
        assert res["ok"] is True  # envelope returned every time
        job_ids.append(res["meta"]["job_id"])
    # The count cap held: the earliest records were evicted, yet those calls still
    # returned their envelopes (the payload is read in-hand before any eviction).
    store = server.config.job_store()
    live = {j["job_id"] for j in store.list_jobs(str(tmp_path))}
    assert len(live) <= 2
    assert job_ids[0] not in live  # earliest evicted


async def test_sync_run_failure_reports_is_error_true(clean_env, tmp_path, monkeypatch):
    # A failure surfaced from the (worker-produced) codex run flips the MCP protocol
    # is_error flag through the boundary, same as before the reroute (#91).
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    meta = server._base_meta(
        str(tmp_path),
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=180,
    )
    envelope = server.serialize_error(
        server.ErrorResult(
            error=server.make_error("codex_auth_required", "not logged in"), meta=meta
        )
    )
    monkeypatch.setattr(server, "_worker_cmd", _fake_worker_cmd(envelope))
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "codex_consult",
            {"question": "q", "workspace_root": str(tmp_path)},
            raise_on_error=False,
        )
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "codex_auth_required"


# --- F2: throttled progress notifications while awaiting (#169) --------------
# _await_job_result is driven directly (as test_sync_cancellation_cancels_job and the
# _await_job_result_* tests above already do) with a stub ctx recording
# report_progress calls. This is preferred over routing through fastmcp.Client: the
# in-process Client's default progress plumbing requires the caller to send a
# progressToken and wire a progress_handler through call_tool, which only exercises
# the MCP-protocol relay (already FastMCP's responsibility) rather than the
# throttle/dedupe logic that is actually new in this task. Driving the coroutine
# directly isolates that logic with a fast, deterministic stub.
class _StubProgressCtx:
    def __init__(self, *, raise_error=False):
        self.calls: list[tuple] = []
        self._raise = raise_error

    async def report_progress(self, progress, total=None, message=None):
        if self._raise:
            raise RuntimeError("boom")
        self.calls.append((progress, total, message, time.monotonic()))


def _running_record_with_events(events_seen: int):
    return _ok_record("running") | {"events_seen": events_seen}


async def test_await_job_result_reports_throttled_progress(clean_env, tmp_path, monkeypatch):
    # Many events_seen changes spread across several throttle windows: at most one
    # notification fires per window (message-only, no fake total), and consecutive
    # notifications are separated by at least one throttle interval.
    monkeypatch.setattr(server, "_SYNC_POLL_INTERVAL_S", 0.02)
    monkeypatch.setattr(server, "_SYNC_PROGRESS_THROTTLE_S", 0.05)
    sequence = [_running_record_with_events(n) for n in range(1, 31)]
    sequence.append(_ok_record("done") | {"events_seen": 30})
    store = _FakeStore(
        status_sequence=sequence,
        record=_ok_record("done"),
        result_json=_consult_success_envelope(str(tmp_path)),
    )
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    ctx = _StubProgressCtx()
    cwd = str(tmp_path)
    meta = _await_job_result_meta(cwd)
    res = await server._await_job_result(cwd, "job-abc", "codex_consult", meta, "summary", 180, ctx)
    assert res["ok"] is True
    assert ctx.calls, "no progress reported"
    assert all(total is None for _, total, _, _ in ctx.calls)
    assert all(m.startswith("codex events:") for _, _, m, _ in ctx.calls)
    assert len(ctx.calls) > 1  # spans multiple throttle windows
    gaps = [b[3] - a[3] for a, b in zip(ctx.calls, ctx.calls[1:], strict=False)]
    assert all(g >= 0.05 * 0.9 for g in gaps)  # small tolerance for scheduling jitter


async def test_await_job_result_progress_skipped_when_events_unchanged(
    clean_env, tmp_path, monkeypatch
):
    # events_seen staying flat across many polls (spanning several throttle windows)
    # must not re-notify: only the initial transition from "no events yet" fires.
    monkeypatch.setattr(server, "_SYNC_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(server, "_SYNC_PROGRESS_THROTTLE_S", 0.02)
    sequence = [_running_record_with_events(2) for _ in range(10)]
    sequence.append(_ok_record("done") | {"events_seen": 2})
    store = _FakeStore(
        status_sequence=sequence,
        record=_ok_record("done"),
        result_json=_consult_success_envelope(str(tmp_path)),
    )
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    ctx = _StubProgressCtx()
    cwd = str(tmp_path)
    meta = _await_job_result_meta(cwd)
    res = await server._await_job_result(cwd, "job-abc", "codex_consult", meta, "summary", 180, ctx)
    assert res["ok"] is True
    assert len(ctx.calls) == 1
    assert ctx.calls[0][2] == "codex events: 2"


async def test_await_job_result_no_ctx_no_progress_calls(clean_env, tmp_path, monkeypatch):
    # No ctx (e.g. a transport that doesn't support progress) -> silently no calls,
    # and the awaited result is unaffected.
    monkeypatch.setattr(server, "_SYNC_POLL_INTERVAL_S", 0.01)
    sequence = [_running_record_with_events(n) for n in range(1, 4)]
    sequence.append(_ok_record("done") | {"events_seen": 3})
    store = _FakeStore(
        status_sequence=sequence,
        record=_ok_record("done"),
        result_json=_consult_success_envelope(str(tmp_path)),
    )
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    cwd = str(tmp_path)
    meta = _await_job_result_meta(cwd)
    res = await server._await_job_result(
        cwd, "job-abc", "codex_consult", meta, "summary", 180, None
    )
    assert res["ok"] is True


async def test_progress_failure_does_not_fail_call(clean_env, tmp_path, monkeypatch):
    # report_progress raising must never surface: the awaited call still succeeds.
    monkeypatch.setattr(server, "_SYNC_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(server, "_SYNC_PROGRESS_THROTTLE_S", 0.02)
    sequence = [_running_record_with_events(n) for n in range(1, 4)]
    sequence.append(_ok_record("done") | {"events_seen": 3})
    store = _FakeStore(
        status_sequence=sequence,
        record=_ok_record("done"),
        result_json=_consult_success_envelope(str(tmp_path)),
    )
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    ctx = _StubProgressCtx(raise_error=True)
    cwd = str(tmp_path)
    meta = _await_job_result_meta(cwd)
    res = await server._await_job_result(cwd, "job-abc", "codex_consult", meta, "summary", 180, ctx)
    assert res["ok"] is True
    assert ctx.calls == []  # the raise happens before the call is recorded


# --- idempotency_key wiring on the spend-committing tools (F4) ----------------
class _FakeIdemStore(_FakeStore):
    """Fake store whose start_idempotent returns a canned outcome, for exercising the
    server's mapping of each idempotency outcome onto the wire envelope."""

    def __init__(self, outcome, *, snapshot=None, outcomes=None, **kw):
        super().__init__(**kw)
        self._outcome = outcome
        # A sequence returned one-per-call (last repeats), for the in-progress->resolve loop.
        self._outcomes = list(outcomes) if outcomes is not None else None
        self._snapshot = snapshot
        self.idem_calls = []

    def start_idempotent(self, cmd_factory, cwd, *, kind, tool, key, arg_hash, write_spec=None):
        self.idem_calls.append({"tool": tool, "key": key, "arg_hash": arg_hash, "kind": kind})
        if self._outcomes is not None:
            idx = min(len(self.idem_calls) - 1, len(self._outcomes) - 1)
            return self._outcomes[idx]
        return self._outcome

    def status(self, cwd, job_id):
        return self._snapshot if self._snapshot is not None else super().status(cwd, job_id)


async def test_consult_async_conflict_maps_to_error(monkeypatch, clean_env, tmp_path):
    store = _FakeIdemStore({"kind": "conflict"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult_async("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is False
    assert res["error"]["code"] == "idempotency_conflict"
    assert res["error"]["temporary"] is False
    # the exact public tool name is the namespace, not the normalized kind
    assert store.idem_calls[0]["tool"] == "codex_consult_async"


async def test_consult_async_result_unavailable_maps_to_error(monkeypatch, clean_env, tmp_path):
    store = _FakeIdemStore({"kind": "unavailable"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult_async("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is False
    assert res["error"]["code"] == "idempotency_result_unavailable"
    assert res["error"]["temporary"] is False


async def test_consult_async_in_progress_is_temporary(monkeypatch, clean_env, tmp_path):
    store = _FakeIdemStore({"kind": "in_progress"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult_async("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is False
    assert res["error"]["code"] == "idempotency_in_progress"
    assert res["error"]["temporary"] is True
    assert res["error"]["retry_after_ms"] == server._IDEM_IN_PROGRESS_RETRY_MS


async def test_delegate_async_replay_returns_real_handle(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", lambda *a, **k: None)
    snap = _ok_record("done")  # a replayed job may already be terminal
    store = _FakeIdemStore({"kind": "replay", "job_id": "job-abc"}, snapshot=snap)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_delegate_async(
        "do x", workspace_root=str(tmp_path), idempotency_key="k1"
    )
    assert res["ok"] is True
    assert res["job_id"] == "job-abc"
    assert res["status"] == "done"  # the job's REAL status, not a synthetic "running"
    assert res["meta"]["idempotency_replayed"] is True


async def test_consult_async_created_has_no_replayed_flag(monkeypatch, clean_env, tmp_path):
    store = _FakeIdemStore({"kind": "created", "job_id": "job-abc", "started_at": "t"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult_async("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is True and res["status"] == "running"
    assert res["meta"].get("idempotency_replayed") is None  # only set on a replay


async def test_consult_sync_replay_marks_replayed(monkeypatch, clean_env, tmp_path):
    done = _ok_record("done")
    done["kind"] = "codex_consult"
    env = {
        "ok": True,
        "tool": "codex_consult",
        "summary": "answer",
        "meta": server._base_meta(
            str(tmp_path),
            "param",
            tier="consult",
            sandbox="read-only",
            isolation="inherit",
            model=None,
            timeout_seconds=180,
        ).model_dump(mode="json"),
    }
    store = _FakeIdemStore(
        {"kind": "replay", "job_id": "job-abc"}, snapshot=done, record=done, result_json=env
    )
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is True
    assert res["meta"]["idempotency_replayed"] is True
    assert store.cancelled == []  # a replay waiter never cancels the shared job


async def test_empty_idempotency_key_rejected_at_boundary():
    """An empty idempotency_key violates min_length -> the invalid_arguments envelope."""
    res = await server.mcp.call_tool("codex_consult", {"question": "q", "idempotency_key": ""})
    assert res.is_error is True
    err = res.structured_content["error"]
    assert err["code"] == "invalid_arguments"
    assert err["details"]["field"] == "idempotency_key"


def _consult_meta(cwd):
    return server._base_meta(
        cwd,
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        timeout_seconds=1,
    )


async def test_keyed_await_timeout_leaves_shared_job_running(monkeypatch, clean_env, tmp_path):
    """A keyed sync waiter that hits its local grace must NOT cancel the job — another
    idempotent caller may be awaiting the same run; it stays recoverable via job_id."""
    monkeypatch.setattr(server, "_SYNC_AWAIT_GRACE_S", 0)
    store = _FakeStore(status_dict=_ok_record("running"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server._await_job_result(
        str(tmp_path),
        "job-abc",
        "codex_consult",
        _consult_meta(str(tmp_path)),
        "summary",
        0,
        None,
        keyed=True,
    )
    assert res["error"]["code"] == "timeout"
    assert store.cancelled == []  # not cancelled
    assert "continues in the background" in res["error"]["message"]


async def test_unkeyed_await_timeout_cancels_job(monkeypatch, clean_env, tmp_path):
    """The prior (no-key) behavior is preserved: a timed-out unkeyed waiter cancels."""
    monkeypatch.setattr(server, "_SYNC_AWAIT_GRACE_S", 0)
    store = _FakeStore(status_dict=_ok_record("running"))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server._await_job_result(
        str(tmp_path),
        "job-abc",
        "codex_consult",
        _consult_meta(str(tmp_path)),
        "summary",
        0,
        None,
    )
    assert res["error"]["code"] == "timeout"
    assert store.cancelled == ["job-abc"]


async def test_consult_sync_conflict_maps_to_error(monkeypatch, clean_env, tmp_path):
    store = _FakeIdemStore({"kind": "conflict"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is False and res["error"]["code"] == "idempotency_conflict"
    assert store.idem_calls[0]["tool"] == "codex_consult"  # sync tool namespace


async def test_consult_sync_in_progress_after_wait(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(server, "_IDEM_SYNC_INPROGRESS_WAIT_S", 0.0)  # don't actually block
    store = _FakeIdemStore({"kind": "in_progress"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is False and res["error"]["code"] == "idempotency_in_progress"
    assert res["error"]["temporary"] is True


async def test_consult_sync_in_progress_then_created_loops(monkeypatch, clean_env, tmp_path):
    """A reservation that is still publishing resolves on a retry within the wait window."""
    monkeypatch.setattr(server, "_IDEM_SYNC_INPROGRESS_POLL_S", 0.0)
    done = _ok_record("done")
    done["kind"] = "codex_consult"
    env = {
        "ok": True,
        "tool": "codex_consult",
        "summary": "a",
        "meta": _consult_meta(str(tmp_path)).model_dump(mode="json"),
    }
    store = _FakeIdemStore(
        None,
        outcomes=[
            {"kind": "in_progress"},
            {"kind": "created", "job_id": "job-abc", "started_at": "t"},
        ],
        record=done,
        result_json=env,
    )
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is True
    assert len(store.idem_calls) == 2  # looped once past the in-progress reservation


async def test_keyed_sync_created_sets_meta_job_id_before_await(monkeypatch, clean_env, tmp_path):
    """A keyed sync call must set meta.job_id before awaiting so a timeout/terminal-error
    envelope (built from this meta) names the durable job it tells the caller to fetch."""
    store = _FakeIdemStore({"kind": "created", "job_id": "job-xyz", "started_at": "t"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    captured = {}

    async def fake_await(cwd, job_id, kind, meta, detail_v, timeout, ctx, *, keyed=False):
        captured["meta_job_id"] = meta.job_id
        captured["keyed"] = keyed
        return {"ok": True, "meta": {"job_id": meta.job_id}}

    monkeypatch.setattr(server, "_await_job_result", fake_await)
    await server.codex_consult("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert captured["meta_job_id"] == "job-xyz"
    assert captured["keyed"] is True  # keyed => shared job never auto-cancelled


def test_capabilities_advertise_idempotency_on_spend_committing_tools(clean_env):
    by_name = {t["name"]: t for t in server.codex_capabilities()["tool_details"]}
    for name in (
        "codex_consult",
        "codex_review_changes",
        "codex_delegate",
        "codex_consult_async",
        "codex_review_changes_async",
        "codex_delegate_async",
    ):
        assert "idempotency_key" in by_name[name]["key_optional_params"], name
        assert "idempotency_conflict" in by_name[name]["error_codes"], name
