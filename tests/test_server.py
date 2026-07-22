"""Server tool behavior: status, capabilities, consult (mocked codex)."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from typing import get_args

import pytest
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from pydantic import ValidationError

from codex_in_claude import __version__, codex, delegate, orchestration, server
from codex_in_claude._core.jobs import DiscardOutcome
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


# ----------------------------------------------------- platform startup guard
def test_posix_platform_guard_refuses_native_windows(monkeypatch, capsys):
    """On os.name == 'nt' with no escape hatch, the server refuses to start (#232)."""
    monkeypatch.delenv("CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM", raising=False)
    with pytest.raises(SystemExit) as exc:
        server._enforce_posix_platform(os_name="nt")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "requires a POSIX platform" in err
    assert "WSL2" in err


def test_posix_platform_guard_escape_hatch_warns(monkeypatch, capsys):
    """The escape hatch downgrades the hard exit to a stderr warning (#232)."""
    monkeypatch.setenv("CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM", "1")
    server._enforce_posix_platform(os_name="nt")  # must not raise
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM" in err


def test_posix_platform_guard_refuses_other_non_posix(monkeypatch, capsys):
    """The platform contract is POSIX-only, not just native-Windows-only (#232)."""
    monkeypatch.delenv("CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM", raising=False)
    with pytest.raises(SystemExit) as exc:
        server._enforce_posix_platform(os_name="java")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "requires a POSIX platform" in err
    assert "os.name=java" in err
    # The WSL2 hint is Windows-specific and must not appear for other non-POSIX runtimes.
    assert "WSL2" not in err


def test_posix_platform_guard_escape_hatch_reports_actual_os_name(monkeypatch, capsys):
    """Unsupported-platform warnings name the exact runtime os.name (#232)."""
    monkeypatch.setenv("CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM", "1")
    server._enforce_posix_platform(os_name="java")  # must not raise
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "os.name=java" in err


def test_posix_platform_guard_noop_on_posix(monkeypatch, capsys):
    """On a POSIX platform the guard is a no-op (#232)."""
    monkeypatch.delenv("CODEX_IN_CLAUDE_ALLOW_UNSUPPORTED_PLATFORM", raising=False)
    server._enforce_posix_platform(os_name="posix")  # must not raise
    assert capsys.readouterr().err == ""


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
        reasoning_effort=None,
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
        reasoning_effort=None,
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
        reasoning_effort=None,
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
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "auth (ChatGPT)."))
    res = server.codex_status()
    assert res["ok"] is True
    assert res["ready"] is True
    assert res["codex_found"] is True
    assert res["version_supported"] is True


def test_status_reports_raised_default_timeout(monkeypatch, clean_env):
    # #341 acceptance: codex_status surfaces the raised built-in sync deadline (300)
    # in both raw_defaults and resolved_defaults. These are readiness-independent, so
    # force not-ready (codex absent) to keep the test hermetic — a ready status would
    # call rate_limit.live_read and spawn the real app-server.
    monkeypatch.setattr(server.codex, "codex_version", lambda: None)
    res = server.codex_status()
    assert res["ready"] is False
    assert res["raw_defaults"]["timeout_seconds"] == 300
    assert res["resolved_defaults"]["timeout_seconds"] == 300
    assert res["resolved_defaults"]["timeout_bounds"] == [10, 600]


def test_status_not_found(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: None)
    res = server.codex_status()
    assert res["codex_found"] is False
    assert res["ready"] is False


def test_status_not_authenticated(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (False, "run codex login"))
    res = server.codex_status()
    assert res["ready"] is False
    assert "authenticated" in res["readiness_detail"]


def test_status_auth_indeterminate(monkeypatch, clean_env):
    """A probe that could not run (None) is not-ready, and says so without claiming
    the user is logged out (#252)."""
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (None, None))
    res = server.codex_status()
    assert res["ready"] is False
    assert res["readiness_detail"] == "Could not determine codex auth status."


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


def test_capability_summary_routing_tiebreaker_is_stated():
    """#209 (pins #198): consult and review both self-brand as a "second opinion", so
    the summary must give an agent a concrete tiebreaker — consult for a diff pasted
    inline, review for changes already in git. Pin the full clauses so the tiebreaker
    can't silently collapse back to two undifferentiated blurbs while a fragment survives
    (server.py CAPABILITY_SUMMARY routing)."""
    summary = server.CAPABILITY_SUMMARY
    # consult side: routes a diff pasted inline.
    assert "read-only second opinion or Q&A — including on a diff you paste inline." in summary
    # review side: routes changes already in git, and states the precedence over consult.
    assert "prefer it over codex_consult whenever the changes already live in git." in summary


def test_capability_summary_splits_inventory_from_model_discovery():
    """#209 (pins #198): the capabilities inventory rule and the "discover valid model
    slugs before overriding model" prerequisite have different triggers, so they must read
    as two separate consecutive sentences, not one bundled clause. Pin them as one
    contiguous substring so the split itself is what's asserted, not just the two fragments
    coexisting somewhere (server.py CAPABILITY_SUMMARY discovery rules)."""
    summary = server.CAPABILITY_SUMMARY
    assert (
        "Use codex_capabilities for the full inventory. Before overriding the model or "
        "reasoning_effort, use codex_models (or the codex://models resource) to discover "
        "valid model slugs and each model's advertised reasoning-effort set" in summary
    )


async def test_codex_status_defers_with_prefer_not_a_bare_fact():
    """#209 (pins #198): the rate-limit posture must state the default as a recommendation
    an agent can act on — *prefer* to defer non-urgent calls when limited/exhausted, urgent
    ones may proceed — not the bare fact that limited/exhausted "are reasons to defer",
    which left the strength ambiguous (codex_status docstring)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    # The docstring wraps across lines; normalize whitespace before matching the clause.
    desc = " ".join((tools["codex_status"].description or "").split())
    assert "prefer to defer non-urgent Codex calls (urgent ones may still proceed)" in desc
    # Regression guard: the complete pre-#198 bare-fact sentence must not return.
    assert "are reasons to defer non-urgent Codex calls" not in desc


async def test_codex_dry_run_frames_redaction_as_best_effort_not_confirmation():
    """#209 (pins #198): codex_dry_run previews scope and reported redactions, but
    redaction is best-effort everywhere else, so its description must frame the preview as a
    scope check, not proof no secret remains — the pre-#198 wording over-promised that it
    confirms secrets are redacted (codex_dry_run docstring)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    desc = " ".join((tools["codex_dry_run"].description or "").split())
    assert (
        "redaction is best-effort, so treat the preview as a check on scope, "
        "not as confirmation that no secret remains." in desc
    )
    # Regression guard: the complete pre-#198 over-promise must not return.
    assert "confirm the scope and that secrets are redacted" not in desc


# --- #338: selection-time steer from the sync tools to their _async variants ------
# A sync active call blocks to a resolved deadline (built-in default 300s) whose expiry
# SIGKILLs the run and loses its partial paid work; the recovery repair only fires AFTER
# that spend. These pin a pre-spend, shape-based steer at every selection home an agent
# reads — the sync/async tool descriptions, the capabilities use_when, and the
# first-read instructions — so the two members of each sync/async pair no longer both
# claim the same long-running workload.
# (sync tool, async tool, distinctive request-shape token). The shape token is the
# per-pair discriminator — its presence proves the steer names *this* pair's workload,
# not just generic deadline prose that could be copy-pasted across all three (the F2
# regression a generic-only assertion would miss).
_STEER = [
    ("codex_consult", "codex_consult_async", "repo-grounded"),
    ("codex_review_changes", "codex_review_changes_async", "whole-branch"),
    ("codex_delegate", "codex_delegate_async", "substantial"),
]


@pytest.mark.parametrize("sync_name,async_name,shape", _STEER)
async def test_sync_active_tool_steers_to_async_on_deadline(sync_name, async_name, shape):
    """#338: each sync active tool's description states the deadline consequence and steers
    THIS pair's distinctive long-running shape to its _async variant at selection time —
    while keeping progress streaming conditional on the client requesting it."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    desc = " ".join((tools[sync_name].description or "").split())
    # The agent-visible consequence of expiry — not billing/clamp language.
    assert (
        "If that deadline expires the run is terminated and its partial output is not "
        "recoverable or resumable" in desc
    ), sync_name
    # The pre-spend steer names the matching async tool AND this pair's own shape.
    assert f"prefer `{async_name}`" in desc, sync_name
    assert shape in desc, sync_name
    # Regression: progress streaming stays conditional on the client requesting it.
    assert "when your client requests it" in desc, sync_name
    # Regression: termination is deadline expiry, never input coercion ("clamp hit").
    assert "clamp hit" not in desc, sync_name


@pytest.mark.parametrize("async_name,shape", [(a, s) for _, a, s in _STEER])
async def test_async_active_tool_primary_description_names_selection_shape(async_name, shape):
    """#338: each async tool's primary tools/list description names THIS pair's shape that
    makes it the right pre-spend choice (it can exceed the synchronous deadline), replacing
    the vague "may run long" — agents commonly select from tools/list without first calling
    codex_capabilities."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    desc = " ".join((tools[async_name].description or "").split())
    assert "can exceed the synchronous deadline" in desc, async_name
    assert shape in desc, async_name
    # Regression: the vague pre-#338 selection phrase is gone.
    assert "may run long" not in desc, async_name


def test_capability_summary_steers_long_work_to_async():
    """#338: the first-read instructions (served as MCP `instructions`) steer work that can
    exceed the synchronous deadline to the matching _async variant, naming every pair's
    shape, so cold-start routing does not send every workload to the sync tools."""
    summary = server.CAPABILITY_SUMMARY
    assert "Prefer the matching _async variant" in summary
    assert "can exceed the synchronous deadline" in summary
    for _, async_name, shape in _STEER:
        assert async_name in summary, async_name
        assert shape in summary, shape


@pytest.mark.parametrize("sync_name,async_name,shape", _STEER)
def test_sync_use_when_points_at_async_for_its_shape(sync_name, async_name, shape):
    """#338: the codex_capabilities use_when for each SYNC tool carries the steer too —
    naming its async variant and this pair's shape — so a capabilities-driven agent reading
    the sync entry is not left with an unqualified 'always use sync' recommendation."""
    by_name = {t["name"]: t for t in server.codex_capabilities()["tool_details"]}
    use_when = by_name[sync_name]["use_when"]
    assert async_name in use_when, sync_name
    assert shape in use_when, sync_name


@pytest.mark.parametrize("async_name,shape", [(a, s) for _, a, s in _STEER])
def test_async_use_when_names_shape_not_may_run_long(async_name, shape):
    """#338: the codex_capabilities use_when for each async tool names THIS pair's selection
    shape rather than a vague duration hint, so a capabilities-driven client gets the same
    pre-spend steer as a tools/list-driven one."""
    by_name = {t["name"]: t for t in server.codex_capabilities()["tool_details"]}
    use_when = by_name[async_name]["use_when"]
    assert "can exceed the synchronous deadline" in use_when, async_name
    assert shape in use_when, async_name
    assert "may run long" not in use_when, async_name


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


# --- egress/security guarantee freeze matrix (issue #333) --------------------
# Compressing the tools/list catalog (#333) shrinks these docstrings. A dropped
# egress/security guarantee is a BREAKING change (AGENTS.md § Versioning), yet the
# `"OpenAI" in doc` check above is far too coarse to catch it: it would still pass
# after the raw-input, files-read, auto-loaded-AGENTS.md, isolation, or best-effort-
# redaction guarantees were silently deleted. This matrix locks each guarantee a
# tool's description states TODAY so compression can only make it shorter, never
# softer.
#
# Each matcher is a semantic probe over the lowercased docstring: it survives a
# reword but fails on an omission. The required set per tool is the empirically
# captured current baseline, NOT an idealized maximum — the `_async` twins
# deliberately carry a compressed subset (they reference their sync sibling), so
# requiring the full set on them would be a spurious failure. Adjust a tool's row
# only when deliberately, reviewably changing what that tool guarantees inline.
#
# The files-read disclosure: a genuine `read`/`reads`/`reading` verb adjacent (either
# order, within one sentence) to a file token, over the lowercased docstring. The `read`
# in "read-only" is excluded — that is the decoupled token that let the old, coarser
# "read" + "file"/"repo" co-occurrence pass on stray "read-only sandbox" + "repo-grounded"
# text even after the real disclosure was deleted (#345). Adjacency (not mere
# co-occurrence) is what keeps "codex never edits files" and "repo-grounded question" from
# standing in for "codex reads … files … and sends their content".
_FILES_READ_DISCLOSED = re.compile(
    r"read(?:s|ing)?(?!-only)\b[^.]{0,45}\b(?:file|tracked|repo)"
    r"|\b(?:file|tracked|repo)[a-z]*\b[^.]{0,45}read(?:s|ing)?(?!-only)\b"
)
# Naming the canonical path is not the guarantee — the guarantee is that those skills
# reach the model (#358). So the sentence naming it must also carry an affirmative
# discovery/loading verb and must not negate it: "$CODEX_HOME/skills is never loaded"
# names the path and a verb, yet states the opposite of what this freeze protects.
_GLOBAL_SKILLS_PATH = "$codex_home/skills"
_GLOBAL_SKILLS_VERB = re.compile(r"\b(?:discover|auto-?load|load|expose|reach|send|read)[a-z]*\b")
_GLOBAL_SKILLS_NEGATION = re.compile(r"\b(?:not|never|no|without|excludes?|suppress(?:es|ed)?)\b")
# Negations that REINFORCE the disclosure rather than deny it, and so must not veto a
# sentence: the caveats legitimately say the isolation flags do NOT suppress this, that the
# skills are NEITHER tracked NOR seeded, and that content is sent even if your prompt NEVER
# mentions it. Stripped before the negation check so only a genuine denial vetoes.
_GLOBAL_SKILLS_BENIGN_NEGATION = re.compile(
    r"do(?:es)?\s+not\s+suppress|not\s+suppress|never\s+mentions?"
    r"|neither\s+tracked|not\s+tracked|nor\s+seeded"
    r"|do(?:es)?\s+not\s+exclude|not\s+exclude|no\s+\S+\s+choice\s+excludes?"
)


def _sentences_naming_global_skills(text):
    return [s for s in re.split(r"(?<=[.;])\s+", text) if _GLOBAL_SKILLS_PATH in s]


def _global_skills_disclosed(text):
    """True when a sentence names the path and affirmatively asserts the behavior.

    Naming the path is not enough — the freeze protects the claim that those skills reach
    the model, so a sentence that names the path while denying it must read as a failure.
    """
    for sentence in _sentences_naming_global_skills(text):
        if not _GLOBAL_SKILLS_VERB.search(sentence):
            continue
        if _GLOBAL_SKILLS_NEGATION.search(_GLOBAL_SKILLS_BENIGN_NEGATION.sub("", sentence)):
            continue
        return True
    return False


_GUARANTEE_MATCHERS = {
    # Caller content is sent to OpenAI.
    "openai": lambda d: "openai" in d,
    # The caller's supplied input is sent raw / unredacted.
    "raw_input": lambda d: "unredacted" in d or "raw" in d,
    # Codex may read (and send) other files in the resolved workspace/worktree. Keyed on a
    # genuine read-verb adjacent to a file token, excluding "read-only" — see
    # _FILES_READ_DISCLOSED for why the old coarse check missed the omission (#345).
    "files_read": lambda d: bool(_FILES_READ_DISCLOSED.search(d)),
    # The workspace AGENTS.md auto-loads (its content can be sent).
    "autoload_agents": lambda d: "agents.md" in d,
    # The workspace .agents/skills/ skills auto-load.
    "autoload_skills": lambda d: ".agents/skills" in d,
    # User-global skills under $CODEX_HOME/skills/ are discovered too — outside the
    # workspace, and not suppressed by the config-isolation flags (#358). Keyed on the
    # exact canonical path (a looser "skills" check would be satisfied by the
    # .agents/skills disclosure alone) AND on an affirmative discovery/loading verb near
    # it, so prose that merely NAMES the path while denying the behavior does not pass.
    # See test_global_skills_matcher_rejects_project_only_prose.
    "autoload_global_skills": _global_skills_disclosed,
    # The isolation flags do NOT suppress that auto-loaded context.
    "isolation_suppress": lambda d: "isolation" in d and "suppress" in d,
    # Secret redaction is best-effort, not a guarantee.
    "redaction_best_effort": lambda d: "redact" in d and "best-effort" in d,
    # delegate: workspace-write blocks network egress for commands Codex RUNS *in the
    # sandbox* — the sandbox-scope qualifier is load-bearing (without it the claim reads
    # as "nothing leaves the machine", which openai/raw_input contradict), so require it.
    "no_network": lambda d: "network" in d and "block" in d and "sandbox" in d,
    # review: the gathered diff is secret-redacted before it is sent.
    "diff_redacted": lambda d: "redact" in d and "diff" in d,
}
_COMMON_EGRESS = {
    "openai",
    "raw_input",
    "files_read",
    "autoload_agents",
    "autoload_skills",
    "autoload_global_skills",
}
_REQUIRED_GUARANTEES = {
    "codex_consult": _COMMON_EGRESS | {"isolation_suppress", "redaction_best_effort"},
    "codex_consult_async": _COMMON_EGRESS,
    "codex_review_changes": _COMMON_EGRESS
    | {"isolation_suppress", "redaction_best_effort", "diff_redacted"},
    "codex_review_changes_async": _COMMON_EGRESS | {"redaction_best_effort", "diff_redacted"},
    "codex_delegate": _COMMON_EGRESS
    | {"isolation_suppress", "redaction_best_effort", "no_network"},
    "codex_delegate_async": _COMMON_EGRESS | {"redaction_best_effort", "no_network"},
}


def test_guarantee_matrix_covers_every_active_tool():
    """The freeze matrix tracks the active-tool set (mirrors _ACTIVE_EGRESS_TOOLS).

    A new active tool must get an explicit guarantee row rather than silently
    escaping the freeze."""
    assert set(_REQUIRED_GUARANTEES) == set(_ACTIVE_EGRESS_TOOLS)


def test_guarantee_matchers_are_discriminating():
    """Confirm the instrument can register a negative: no matcher is vacuously true.

    A matcher that always returned True would make the freeze below pass even after
    the guarantee was deleted (a broken instrument and a clean result look alike)."""
    for key, matcher in _GUARANTEE_MATCHERS.items():
        assert matcher("") is False, key


def test_files_read_matcher_rejects_decoupled_tokens():
    """The `files_read` matcher must not pass on stray read-only/file/repo tokens.

    Its predecessor did: a docstring that dropped the real "Codex reads … files … and
    sends their content" disclosure but still said "read-only sandbox" and "repo-grounded
    question" kept a "read" and a "repo"/"file" token, so the coarse co-occurrence check
    stayed green over the exact omission it was meant to catch (#345). This pins that a
    guarantee-free docstring reads as a failure."""
    decoupled = (
        "runs in a read-only sandbox — codex never edits files. a static review, not a "
        "verify mode. pass workspace_root for a repo-grounded question."
    )
    assert _GUARANTEE_MATCHERS["files_read"](decoupled) is False
    # …while a minimal genuine disclosure, in either token order, still registers.
    assert _GUARANTEE_MATCHERS["files_read"]("codex may read other repo files and send them")
    assert _GUARANTEE_MATCHERS["files_read"]("files codex reads are sent to openai")


def test_global_skills_matcher_rejects_project_only_prose():
    """The `autoload_global_skills` matcher must fail on the pre-#358 wording.

    That wording is the exact defect this guard exists to catch: it discloses the
    project's `AGENTS.md` and `.agents/skills/` but not the user-global skills under
    `$CODEX_HOME/skills/`, which are discovered from outside the workspace. A matcher
    keyed on a bare "skills" token would be satisfied by this string and so would stay
    green over the omission — the broken-instrument failure mode #345 hit."""
    project_only = (
        "codex also auto-loads context from that workspace — the project's agents.md and "
        "any skills under .agents/skills/ — so their content can be sent even if your "
        "prompt never mentions them; the isolation flags do not suppress this."
    )
    assert _GUARANTEE_MATCHERS["autoload_global_skills"](project_only) is False
    # …while a genuine affirmative disclosure registers.
    assert _GUARANTEE_MATCHERS["autoload_global_skills"](
        "skills under $codex_home/skills/ are discovered too"
    )


def test_global_skills_matcher_rejects_negated_prose():
    """Naming the path is not the guarantee — asserting the behavior is.

    A matcher keyed on the bare path would pass text that names `$CODEX_HOME/skills` while
    denying it reaches the model, i.e. stay green over a disclosure that had been inverted
    into a false claim. Pin that the negation reads as a failure."""
    for negated in (
        "skills under $codex_home/skills/ are never loaded by this plugin.",
        "the isolation flags suppress $codex_home/skills/ discovery.",
        "$codex_home/skills/ is not read and its content is not sent.",
    ):
        assert _GUARANTEE_MATCHERS["autoload_global_skills"](negated) is False, negated
    # A negated sentence elsewhere must not mask a genuine disclosure in another sentence.
    mixed = (
        "$codex_home/skills/ skills are discovered from outside the workspace. "
        "Note that $codex_home/config.toml is not loaded."
    )
    assert _GUARANTEE_MATCHERS["autoload_global_skills"](mixed)


# The four runtime caveat sites below are NOT function docstrings, so
# test_active_tool_docstring_preserves_guarantee cannot see them; and the
# codex_status caveat is absent from the manifest snapshot, so that guard cannot
# see it either. Without these, the #358 disclosure could regress green (#358).
def test_capability_summary_discloses_global_skills():
    """The server instructions block names the user-global skills directory."""
    assert _GUARANTEE_MATCHERS["autoload_global_skills"](server.CAPABILITY_SUMMARY.lower())


def test_status_caveat_discloses_global_skills(monkeypatch, clean_env):
    """The codex_status caveat names it too — it has no manifest-snapshot guard."""
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "auth (ChatGPT)."))
    caveat = server.codex_status()["caveat"].lower()
    assert _GUARANTEE_MATCHERS["autoload_global_skills"](caveat)


def test_negative_scope_discloses_global_skills():
    """codex_capabilities' negative_scope is an independent safety inventory (#358)."""
    blob = " ".join(server.codex_capabilities()["negative_scope"]).lower()
    assert _GUARANTEE_MATCHERS["autoload_global_skills"](blob)


@pytest.mark.parametrize("name", _ACTIVE_EGRESS_TOOLS)
def test_capability_returns_disclose_global_skills(name):
    """Each active tool's capability `returns` discloses it — asserted per entry.

    A joined blob would pass while five of six entries dropped the disclosure."""
    by_name = {t["name"]: t for t in server.codex_capabilities()["tool_details"]}
    returns = by_name[name]["returns"].lower()
    assert _GUARANTEE_MATCHERS["autoload_global_skills"](returns), name


@pytest.mark.parametrize(
    ("name", "guarantee"),
    [(name, g) for name, gs in _REQUIRED_GUARANTEES.items() for g in sorted(gs)],
)
def test_active_tool_docstring_preserves_guarantee(name, guarantee):
    """Every guarantee a tool's description states today must survive compression (#333)."""
    doc = (getattr(server, name).__doc__ or "").lower()
    assert _GUARANTEE_MATCHERS[guarantee](doc), f"{name} dropped egress guarantee: {guarantee}"


def _param_description(alias_name):
    return getattr(server, alias_name).__metadata__[0].description.lower()


def test_extra_context_param_preserves_guarantees():
    """extra_context's description carries two guarantees compression must keep (#333):
    it is treated as UNTRUSTED data (best-effort injection mitigation, not a guarantee),
    and secret redaction does NOT cover this field."""
    d = _param_description("ExtraContextParam")
    assert "untrusted" in d, "extra_context dropped the untrusted-data framing"
    # Polarity matters: 'redaction does not cover this field' is the guarantee; a matcher
    # that accepted a bare 'cover' would also pass the inverted 'redaction covers this field'.
    assert "redact" in d and ("does not cover" in d or "not cover" in d), (
        "extra_context dropped the redaction-doesn't-cover-it guarantee"
    )


def test_untracked_param_preserves_egress_guarantee():
    """untracked='include' opt-in egress must stay disclosed inline (#333): choosing it
    SENDS untracked-file contents to OpenAI, and that is not derivable from the enum name."""
    d = _param_description("UntrackedParam")
    assert "openai" in d and ("egress" in d or "send" in d), (
        "untracked dropped the 'include sends contents to OpenAI' egress disclosure"
    )


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
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
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


async def test_transfer_advertises_both_auth_error_codes():
    # codex_transfer's readiness gate can return EITHER auth code — codex_auth_required
    # for a known-absent session, codex_auth_indeterminate for a probe that could not
    # run. Capabilities must advertise both, or an agent branching on the discovered
    # surface never learns the second one exists (#252).
    caps = server.codex_capabilities()
    transfer = next(t for t in caps["tool_details"] if t["name"] == "codex_transfer")
    assert "codex_auth_required" in transfer["error_codes"]
    assert "codex_auth_indeterminate" in transfer["error_codes"]


def test_job_result_incompatible_advertised_on_exactly_the_emitters():
    # Only the tools that validate a FINISHED stored envelope can produce it: the three
    # sync tools (their keyed/unkeyed await path reattaches via _finished_job_envelope)
    # and the two job-result fetch tools. Async starters and status/list/cancel never
    # validate a finished envelope, so advertising it there would be a false contract.
    caps = server.codex_capabilities()
    by_name = {t["name"]: t for t in caps["tool_details"]}
    emitters = {
        "codex_consult",
        "codex_review_changes",
        "codex_delegate",
        "codex_job_result",
        "codex_job_consume_result",
    }
    for name, tool in by_name.items():
        if name in emitters:
            assert "job_result_incompatible" in tool["error_codes"], name
        else:
            assert "job_result_incompatible" not in tool["error_codes"], name


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
    # An empty diff makes no model call, so it is not_run/unknown — never a false pass (#319).
    assert res["verdict"] == "unknown"
    assert res["review_status"] == "not_run"
    assert res["coverage"]["status"] == "complete"
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
        reasoning_effort=None,
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
        reasoning_effort=None,
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
        reasoning_effort=None,
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


async def test_dry_run_empty_diff_reports_no_model_call_and_zero_bytes(
    monkeypatch, clean_env, tmp_path
):
    # The paid call short-circuits on an empty diff and sends 0 bytes; the preview must
    # match — a non-zero prompt_bytes here is the #320 bug.
    monkeypatch.setattr(
        gitdiff,
        "gather_diff",
        lambda *a, **k: gitdiff.DiffResult(
            text="",
            summary=gitdiff.DiffSummary(0, 0, 0),
            untracked_detected=0,
            untracked_included=0,
        ),
    )
    res = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res["would_call_model"] is False
    assert res["prompt_bytes"] == 0
    assert res["coverage"]["status"] == "complete"


async def test_dry_run_nonempty_diff_would_call_model(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(
        gitdiff,
        "gather_diff",
        lambda *a, **k: gitdiff.DiffResult(
            text="diff --git a/x b/x\n+y",
            summary=gitdiff.DiffSummary(1, 1, 0),
            untracked_detected=0,
            untracked_included=0,
        ),
    )
    res = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res["would_call_model"] is True
    assert res["prompt_bytes"] > 0


async def test_dry_run_untracked_only_reports_coverage(monkeypatch, clean_env, tmp_path):
    # Mirrors codex_delegate_dry_run's worktree_plan.untracked_files disclosure (#320).
    monkeypatch.setattr(
        gitdiff,
        "gather_diff",
        lambda *a, **k: gitdiff.DiffResult(
            text="",
            summary=gitdiff.DiffSummary(0, 0, 0),
            untracked_detected=3,
            untracked_included=0,
        ),
    )
    res = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res["would_call_model"] is False
    assert res["prompt_bytes"] == 0
    assert res["coverage"]["status"] == "partial"
    assert res["coverage"]["untracked_files_omitted"] == 3
    assert "untracked_omitted" in res["coverage"]["omission_reasons"]


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

    def result_payload(self, cwd, job_id):
        return self._record, self._result_json

    def discard(self, cwd, job_id):
        self.consumed.append(job_id)
        return DiscardOutcome.REMOVED

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
        "result_ok": True if status == "done" else None,
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
        reasoning_effort=None,
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
    # JobStarted's only wire path — unreachable from the free-tool walk (#304).
    assert res["server_version"] == __version__
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
    # JobStatus is freshly built, so it reports the RESPONDING server — the one wire path
    # for this model, which the free-tool walk can't reach (#304).
    assert res["server_version"] == __version__


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


def test_job_status_model_requires_result_ok_from_store():
    # #335: the boundary maps result_ok strictly (data["result_ok"], not .get) so a
    # store record that omits it fails loud instead of silently emitting null — the
    # whole reason the field is required-nullable. Tested on the pure mapper because
    # _guard would otherwise turn the KeyError into an internal_error envelope.
    rec = _ok_record("done")
    del rec["result_ok"]
    with pytest.raises(KeyError):
        server._job_status_model(rec, {"cwd": "/x", "workspace_source": "param"})


def test_fingerprint_is_pinned():
    assert FINGERPRINT == "codex-in-claude/0.1/schema-55"


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
            reasoning_effort=None,
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
        reasoning_effort=None,
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
        reasoning_effort=None,
        timeout_seconds=1800,
    ).model_dump(mode="json")
    return {
        "ok": True,
        "tool": "codex_review_changes",
        "summary": "looks ok",
        "verdict": "pass",
        "confidence": "high",
        "review_status": "completed",
        "coverage": {
            "status": "complete",
            "untracked_files_detected": 0,
            "untracked_files_included": 0,
            "untracked_files_omitted": 0,
            "omission_reasons": [],
        },
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
        "codex://capabilities-result": (
            "codex-capabilities-result",
            "Codex capabilities result schema",
        ),
        "codex://status-result": ("codex-status-result", "Codex status result schema"),
    }
    for uri, (name, title) in expected.items():
        r = resources[uri]
        assert r.name == name, f"{uri}: name {r.name!r} != {name!r}"
        assert r.title == title, f"{uri}: title {r.title!r} != {title!r}"


async def test_unknown_tool_argument_is_rejected():
    """An unknown argument fails validation rather than being silently ignored.

    This pins the raw Tool-level boundary, where the shape depends on the installed fastmcp:
    below 3.4.3 `Tool.run` raises Pydantic's `ValidationError` directly; from 3.4.3 it raises
    fastmcp's own, which is not a Pydantic subclass and carries only `str(e)`, chaining the
    structured Pydantic error as `__cause__`. Accept either — `requires-python`-style, the
    project supports `fastmcp>=3.4` — but pin what `_ArgumentValidationMiddleware` actually
    depends on: reachable Pydantic `.errors()`. If a future fastmcp drops the chaining, the
    envelope would silently degrade to raw prose, so fail here instead (#136)."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    with pytest.raises((ValidationError, FastMCPValidationError)) as excinfo:
        await tools["codex_status"].run({"definitely_not_a_param": 1})
    exc = excinfo.value
    cause = exc if isinstance(exc, ValidationError) else exc.__cause__
    assert isinstance(cause, ValidationError)
    assert cause.errors()[0]["type"] == "unexpected_keyword_argument"


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


async def test_middleware_reraises_fastmcp_validation_error_without_pydantic_cause():
    """fastmcp raises its own ValidationError for argument failures, chaining the Pydantic
    error as __cause__. Without that cause there are no structured errors to classify, so
    the failure propagates rather than being guessed at as invalid_arguments."""
    mw = server._ArgumentValidationMiddleware()

    async def call_next(_ctx):
        raise FastMCPValidationError("no structured cause")

    with pytest.raises(FastMCPValidationError):
        await mw.on_call_tool(_FakeCallCtx("codex_status"), call_next)


async def test_middleware_maps_fastmcp_validation_error_via_pydantic_cause():
    """A fastmcp ValidationError chaining a Pydantic argument error still becomes the
    documented invalid_arguments envelope (fastmcp >= 3.4.3 wrapping)."""
    mw = server._ArgumentValidationMiddleware()
    pydantic_err = ValidationError.from_exception_data(
        "X", [{"type": "unexpected_keyword_argument", "loc": ("bogus_arg",), "input": 1}]
    )

    async def call_next(_ctx):
        raise FastMCPValidationError(str(pydantic_err)) from pydantic_err

    res = await mw.on_call_tool(_FakeCallCtx("codex_status"), call_next)
    assert res.is_error is True
    assert res.structured_content["error"]["code"] == "invalid_arguments"


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
        "result_ok": None,
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
        "result_ok": None,
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
        reasoning_effort=None,
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


def test_internal_error_result_omits_empty_exception_detail(clean_env, tmp_path):
    exc = RuntimeError()
    res = server._internal_error_result("codex_consult", exc, tier="consult", sandbox="read-only")
    msg = res["error"]["message"]
    assert msg == "codex_consult failed unexpectedly: RuntimeError"
    assert not msg.endswith(": ")


def test_spawn_failure_envelope_redacts_secret_in_exception_text(clean_env, tmp_path):
    # F10: the spawn-failure internal_error is a second exception-text sink.
    exc = OSError("cannot exec /home/AKIAIOSFODNN7EXAMPLE/worker")
    res = server._spawn_failure_envelope(exc, _meta_for(tmp_path))
    msg = res["error"]["message"]
    assert "AKIAIOSFODNN7EXAMPLE" not in msg
    assert "[redacted: secret value]" in msg
    # The safe exception class name is preserved, consistent with the other sinks.
    assert "OSError" in msg


def test_spawn_failure_envelope_omits_empty_exception_detail(clean_env, tmp_path):
    exc = OSError()
    res = server._spawn_failure_envelope(exc, _meta_for(tmp_path))
    msg = res["error"]["message"]
    assert msg == "failed to start background job: OSError"
    assert not msg.endswith(": ")


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


# --- replay preserves the originating run's server_version, never normalizes it ---
# server_version is PROVENANCE about the run that produced a stored payload, unlike
# `fingerprint` (contract identity), which _finished_job_envelope deliberately overwrites
# with the current surface. These four cases pin that asymmetry.


async def test_replayed_error_preserves_the_originating_version(monkeypatch, clean_env, tmp_path):
    from codex_in_claude.errors import make_error as _make_error
    from codex_in_claude.errors import serialize_error as _serialize_error
    from codex_in_claude.schemas import ErrorResult as _ErrorResult

    stored = _serialize_error(
        _ErrorResult(error=_make_error("job_failed", "x"), meta=_meta_for(tmp_path))
    )
    stored["meta"]["server_version"] = "0.1.0"  # an older run's stamped release
    store = _FakeStore(record=_ok_record("done"), result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["meta"]["server_version"] == "0.1.0"  # NOT __version__
    assert res["meta"]["server_version"] != __version__


async def test_replayed_error_without_a_version_stays_unattributed(
    monkeypatch, clean_env, tmp_path
):
    """THE regression test. A payload written before this field existed must replay with
    NO version — an honest unknown — not with the replaying server's current version."""
    from codex_in_claude.errors import make_error as _make_error
    from codex_in_claude.errors import serialize_error as _serialize_error
    from codex_in_claude.schemas import ErrorResult as _ErrorResult

    stored = _serialize_error(
        _ErrorResult(error=_make_error("job_failed", "x"), meta=_meta_for(tmp_path))
    )
    del stored["meta"]["server_version"]  # simulate a pre-upgrade worker's payload
    store = _FakeStore(record=_ok_record("done"), result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert "server_version" not in res["meta"]


async def test_replayed_success_preserves_and_omits_the_same_way(monkeypatch, clean_env, tmp_path):
    stored_with = _done_envelope()
    stored_with["meta"]["server_version"] = "0.1.0"
    store = _FakeStore(record=_ok_record("done"), result_json=stored_with)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["meta"]["server_version"] == "0.1.0"

    stored_without = _done_envelope()
    del stored_without["meta"]["server_version"]
    store2 = _FakeStore(record=_ok_record("done"), result_json=stored_without)
    monkeypatch.setattr(server.config, "job_store", lambda: store2)
    res2 = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res2["ok"] is True
    assert "server_version" not in res2["meta"]


async def test_replayed_success_corrupt_fingerprint_type_is_not_healed(
    monkeypatch, clean_env, tmp_path
):
    # Strict validation must see the STORED bytes: patching job_id/fingerprint before
    # validating would silently heal a corrupt known field (#305).
    stored = _done_envelope()
    stored["meta"]["fingerprint"] = 3  # wrong type: corruption, not a pre-upgrade string
    store = _FakeStore(record=_ok_record("done"), result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


async def test_replayed_success_corrupt_job_id_type_is_not_healed(monkeypatch, clean_env, tmp_path):
    stored = _done_envelope()
    stored["meta"]["job_id"] = 42
    store = _FakeStore(record=_ok_record("done"), result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


async def test_replayed_error_corrupt_fingerprint_type_is_not_healed(
    monkeypatch, clean_env, tmp_path
):
    from codex_in_claude.errors import make_error as _make_error
    from codex_in_claude.errors import serialize_error as _serialize_error
    from codex_in_claude.schemas import ErrorResult as _ErrorResult

    stored = _serialize_error(
        _ErrorResult(error=_make_error("job_failed", "x"), meta=_meta_for(tmp_path))
    )
    stored["meta"]["fingerprint"] = 3
    store = _FakeStore(record=_ok_record("done"), result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


# --- #305: a payload written under a DIFFERENT persisted format is incompatibility, ---
# --- not corruption — and never advertised as retryable. --------------------------


def _stored_error_envelope(tmp_path, code="job_failed", message="x"):
    from codex_in_claude.errors import make_error as _make_error
    from codex_in_claude.errors import serialize_error as _serialize_error
    from codex_in_claude.schemas import ErrorResult as _ErrorResult

    return _serialize_error(
        _ErrorResult(error=_make_error(code, message), meta=_meta_for(tmp_path))
    )


def _record_with_format(fmt):
    rec = _ok_record("done")
    rec["extra"] = {"result_format": fmt}
    return rec


async def _replay(monkeypatch, tmp_path, rec, stored):
    store = _FakeStore(record=rec, result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    return await server.codex_job_result("job-abc", workspace_root=str(tmp_path))


async def test_error_payload_from_different_format_is_incompatible(
    monkeypatch, clean_env, tmp_path
):
    from codex_in_claude.schemas import RESULT_FORMAT

    stored = _stored_error_envelope(tmp_path)
    stored["meta"]["field_from_the_future"] = "x"  # a newer release's Meta addition
    res = await _replay(monkeypatch, tmp_path, _record_with_format(RESULT_FORMAT + 1), stored)
    assert res["ok"] is False
    assert res["error"]["code"] == "job_result_incompatible"
    assert res["error"]["temporary"] is False
    assert res["error"]["retry_after_ms"] is None
    assert res["error"]["repair"]["next_step"] == "start_new_job"
    assert res["meta"]["job_id"] == "job-abc"  # callers keep the job correlation


async def test_success_payload_from_different_format_is_incompatible(
    monkeypatch, clean_env, tmp_path
):
    from codex_in_claude.schemas import RESULT_FORMAT

    stored = _done_envelope()
    stored["field_from_the_future"] = "x"
    res = await _replay(monkeypatch, tmp_path, _record_with_format(RESULT_FORMAT + 1), stored)
    assert res["ok"] is False
    assert res["error"]["code"] == "job_result_incompatible"
    assert res["error"]["temporary"] is False


async def test_new_literal_value_from_different_format_is_incompatible(
    monkeypatch, clean_env, tmp_path
):
    # A newer release can also add ErrorCode/RepairStep Literal values; that fails
    # validation as literal_error, not extra_forbidden — classification must not
    # depend on the error type (#305).
    from codex_in_claude.schemas import RESULT_FORMAT

    stored = _stored_error_envelope(tmp_path)
    stored["error"]["code"] = "code_from_the_future"
    res = await _replay(monkeypatch, tmp_path, _record_with_format(RESULT_FORMAT + 1), stored)
    assert res["ok"] is False
    assert res["error"]["code"] == "job_result_incompatible"


async def test_unknown_key_with_matching_format_is_corruption(monkeypatch, clean_env, tmp_path):
    from codex_in_claude.schemas import RESULT_FORMAT

    stored = _stored_error_envelope(tmp_path)
    stored["meta"]["field_from_the_future"] = "x"
    res = await _replay(monkeypatch, tmp_path, _record_with_format(RESULT_FORMAT), stored)
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert res["meta"]["job_id"] == "job-abc"  # corruption errors correlate too


async def test_unknown_key_with_missing_format_is_corruption(monkeypatch, clean_env, tmp_path):
    # Pre-#305 records carry no result_format; missing evidence is not provenance.
    stored = _stored_error_envelope(tmp_path)
    stored["meta"]["field_from_the_future"] = "x"
    res = await _replay(monkeypatch, tmp_path, _ok_record("done"), stored)
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


@pytest.mark.parametrize("bad_format", [True, 2.0, "2", None, [2], {"v": 2}])
async def test_unusable_format_value_is_corruption(monkeypatch, clean_env, tmp_path, bad_format):
    # Job metadata is opaque JSON; a corrupt discriminator must not classify (note
    # True == 1 and 2.0 == 2 in Python — only an exact int is trusted).
    stored = _stored_error_envelope(tmp_path)
    stored["meta"]["field_from_the_future"] = "x"
    res = await _replay(monkeypatch, tmp_path, _record_with_format(bad_format), stored)
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"


async def test_known_field_failure_from_different_format_is_incompatible(
    monkeypatch, clean_env, tmp_path
):
    # Format-only classification: a differing persisted format makes the record
    # unreadable by THIS release whichever field tripped validation.
    from codex_in_claude.schemas import RESULT_FORMAT

    stored = _done_envelope()
    stored["summary"] = 42  # known field, wrong type
    res = await _replay(monkeypatch, tmp_path, _record_with_format(RESULT_FORMAT + 1), stored)
    assert res["ok"] is False
    assert res["error"]["code"] == "job_result_incompatible"


async def test_incompatible_message_names_provenance_and_redacts(monkeypatch, clean_env, tmp_path):
    from codex_in_claude.schemas import RESULT_FORMAT

    stored = _stored_error_envelope(tmp_path)
    stored["meta"]["server_version"] = "9.9.9"
    stored["meta"]["field_from_the_future"] = "AKIAIOSFODNN7EXAMPLE"
    res = await _replay(monkeypatch, tmp_path, _record_with_format(RESULT_FORMAT + 1), stored)
    assert res["error"]["code"] == "job_result_incompatible"
    msg = res["error"]["message"]
    assert "9.9.9" in msg  # the producing release, for diagnosis
    assert "AKIAIOSFODNN7EXAMPLE" not in msg  # stored values must stay redacted
    assert len(msg) <= 500


async def test_incompatible_repair_prose_addresses_idempotency_replay(
    monkeypatch, clean_env, tmp_path
):
    # A reused idempotency_key replays the same unreadable record, so the repair
    # must steer the caller to a fresh or omitted key.
    from codex_in_claude.schemas import RESULT_FORMAT

    stored = _stored_error_envelope(tmp_path)
    stored["meta"]["field_from_the_future"] = "x"
    res = await _replay(monkeypatch, tmp_path, _record_with_format(RESULT_FORMAT + 1), stored)
    assert "idempotency_key" in res["error"]["repair"]["alternative"]


async def test_consume_result_classifies_incompatibility_too(monkeypatch, clean_env, tmp_path):
    from codex_in_claude.schemas import RESULT_FORMAT

    stored = _stored_error_envelope(tmp_path)
    stored["meta"]["field_from_the_future"] = "x"
    store = _FakeStore(record=_record_with_format(RESULT_FORMAT + 1), result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_consume_result("job-abc", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "job_result_incompatible"
    assert store.consumed == []  # undeliverable → the record must survive (#306)


# --- #306: consume must not destroy a record it failed to deliver -----------------
# The store deletes only after server-side validation succeeded; an unreadable
# payload (corrupt or cross-release) keeps the record fetchable via codex_job_result.


def _real_store(tmp_path):
    from codex_in_claude._core.jobs import JobStore

    return JobStore(root=tmp_path / "jobstate", ttl_seconds=3600, max_seconds=60, max_count=50)


def _start_done_job(store, cwd, payload, kind="codex_delegate"):
    """Run a real job whose worker writes ``payload`` as its result.json."""
    code = "import sys; open('result.json','w').write(sys.argv[1])"
    body = json.dumps(payload)
    job_id, _ = store.start(lambda _jd: [sys.executable, "-c", code, body], cwd, kind=kind)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        st = store.status(cwd, job_id)
        assert st is not None
        if st["status"] != "running":
            assert st["status"] == "done"
            return job_id
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


async def test_consume_unreadable_result_keeps_record(monkeypatch, clean_env, tmp_path):
    # Regression (#306): consume used to rmtree the record before validation, so a
    # corrupt payload produced an error about a result that no longer existed.
    store = _real_store(tmp_path)
    cwd = str(tmp_path)
    job_id = _start_done_job(store, cwd, {"ok": True, "tool": "codex_delegate"})  # schema-invalid
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_consume_result(job_id, workspace_root=cwd)
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert store.status(cwd, job_id) is not None  # the record survived
    again = await server.codex_job_result(job_id, workspace_root=cwd)
    assert again["error"]["code"] == "internal_error"  # still fetchable, not job_not_found


async def test_consume_valid_result_deletes_exactly_once(monkeypatch, clean_env, tmp_path):
    store = _real_store(tmp_path)
    cwd = str(tmp_path)
    job_id = _start_done_job(store, cwd, _done_envelope())
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_consume_result(job_id, workspace_root=cwd)
    assert res["ok"] is True
    assert store.status(cwd, job_id) is None  # delivered → deleted
    second = await server.codex_job_consume_result(job_id, workspace_root=cwd)
    assert second["ok"] is False
    assert second["error"]["code"] == "job_not_found"


async def test_consume_valid_stored_error_still_deletes(monkeypatch, clean_env, tmp_path):
    # A stored ok:false envelope that validates IS a faithful delivery — consume
    # keeps its delete-on-success semantics for it.
    store = _real_store(tmp_path)
    cwd = str(tmp_path)
    job_id = _start_done_job(store, cwd, _stored_error_envelope(tmp_path))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_consume_result(job_id, workspace_root=cwd)
    assert res["ok"] is False
    assert res["error"]["code"] == "job_failed"  # the stored error, delivered faithfully
    assert store.status(cwd, job_id) is None  # delivered → deleted


async def test_consume_lost_race_reports_job_not_found(monkeypatch, clean_env, tmp_path):
    # Between read+validate and discard, a concurrent consume (or the TTL reaper /
    # count-cap eviction) can remove the record first. The loser reports
    # job_not_found instead of delivering a second copy — the same outcome it
    # would have seen had the winner run marginally earlier.
    store = _real_store(tmp_path)
    cwd = str(tmp_path)
    job_id = _start_done_job(store, cwd, _done_envelope())
    real_discard = store.discard

    def racing_discard(cwd_, job_id_):
        real_discard(cwd_, job_id_)  # the concurrent winner deletes first...
        return real_discard(cwd_, job_id_)  # ...so this caller's own discard loses

    store.discard = racing_discard
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_consume_result(job_id, workspace_root=cwd)
    assert res["ok"] is False
    assert res["error"]["code"] == "job_not_found"


async def test_consume_delete_failed_never_reports_not_found(monkeypatch, clean_env, tmp_path):
    # A partial deletion can leave the record unreadable (status() -> None) while
    # parts of it survive on disk — indistinguishable, via a status probe, from a
    # lost race. Only the store's own DELETE_FAILED verdict decides, so the
    # validated result in hand is still delivered, never swapped for
    # job_not_found (#314 review).
    store = _real_store(tmp_path)
    cwd = str(tmp_path)
    job_id = _start_done_job(store, cwd, _done_envelope())

    def partial_failure_discard(cwd_, job_id_):
        (store._job_dir(cwd_, job_id_) / "meta.json").unlink()  # record now unreadable
        return DiscardOutcome.DELETE_FAILED

    store.discard = partial_failure_discard
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_consume_result(job_id, workspace_root=cwd)
    assert res["ok"] is True  # the validated result, not job_not_found


async def test_consume_failed_delete_still_delivers(monkeypatch, clean_env, tmp_path):
    # Deletion stays best-effort (as the pre-split rmtree was): when removal fails
    # but the payload validated, deliver it and leave the record to the TTL reaper
    # rather than reporting an error about a result we hold in hand.
    from codex_in_claude._core.jobs import JobStore

    store = _real_store(tmp_path)
    cwd = str(tmp_path)
    job_id = _start_done_job(store, cwd, _done_envelope())
    monkeypatch.setattr(JobStore, "_rmtree", staticmethod(lambda jd: None))
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_consume_result(job_id, workspace_root=cwd)
    assert res["ok"] is True
    assert store.status(cwd, job_id) is not None  # record lingers for the reaper


async def test_consume_nondone_job_keeps_record(monkeypatch, clean_env, tmp_path):
    # Unchanged semantics: consume never deletes a job that isn't done.
    store = _real_store(tmp_path)
    cwd = str(tmp_path)
    job_id, _ = store.start(
        lambda _jd: [sys.executable, "-c", "import time; time.sleep(30)"], cwd, kind="k"
    )
    store.cancel(cwd, job_id)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_consume_result(job_id, workspace_root=cwd)
    assert res["ok"] is False
    assert res["error"]["code"] == "job_cancelled"
    assert store.status(cwd, job_id) is not None


async def test_replay_normalizes_fingerprint_but_not_version(monkeypatch, clean_env, tmp_path):
    """Pins the deliberate asymmetry so a future tidy-up cannot 'harmonize' it away.

    fingerprint = contract identity -> normalized to THIS server's surface, because a
    client caching on a stale contract id would be misled.
    server_version = provenance about a past run -> preserved, because rewriting it would
    be a lie about which build produced the error.
    """
    from codex_in_claude.errors import make_error as _make_error
    from codex_in_claude.errors import serialize_error as _serialize_error
    from codex_in_claude.schemas import ErrorResult as _ErrorResult

    stored = _serialize_error(
        _ErrorResult(error=_make_error("job_failed", "x"), meta=_meta_for(tmp_path))
    )
    stored["meta"]["server_version"] = "0.1.0"
    stored["meta"]["fingerprint"] = "codex-in-claude/0.1/schema-1"  # a pre-upgrade worker
    store = _FakeStore(record=_ok_record("done"), result_json=stored)
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_job_result("job-abc", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["meta"]["fingerprint"] == FINGERPRINT  # normalized
    assert res["meta"]["server_version"] == "0.1.0"  # preserved


async def test_boundary_propagates_cancellation(monkeypatch, clean_env, tmp_path):

    def cancel(*a, **k):
        raise asyncio.CancelledError

    monkeypatch.setattr(server, "_start_job", cancel)
    with pytest.raises(asyncio.CancelledError):
        await server.codex_consult("q", workspace_root=str(tmp_path))


# --- job starts execute off the asyncio event loop (#199) --------------------
# The blocking store calls (subprocess spawn, and for keyed starts a cross-process
# flock + index sweep) must run in a worker thread so one slow start can't stall
# every concurrent MCP request served by this process. Each test injects a store
# whose call records the executing thread and asserts it is not the main thread.
def _offloop_meta(tmp_path):
    return server._base_meta(
        str(tmp_path),
        "param",
        tier="consult",
        sandbox="read-only",
        isolation="inherit",
        model=None,
        reasoning_effort=None,
        timeout_seconds=60,
    )


async def test_unkeyed_start_runs_store_start_off_event_loop(monkeypatch, clean_env, tmp_path):
    import threading

    seen = {}

    class _Store:
        def start(self, *a, **k):
            seen["thread"] = threading.current_thread()
            return ("job-1", "t")

    store = _Store()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server._start_job(
        _offloop_meta(tmp_path),
        str(tmp_path),
        kind="codex_consult",
        spec={"kind": "codex_consult", "cwd": str(tmp_path)},
        deadline=60,
    )
    assert res["job_id"] == "job-1"
    assert seen["thread"] is not threading.main_thread()


async def test_keyed_async_start_idempotent_runs_off_event_loop(monkeypatch, clean_env, tmp_path):
    import threading

    seen = {}

    class _Store:
        def start_idempotent(self, *a, **k):
            seen["thread"] = threading.current_thread()
            return {"kind": "created", "job_id": "job-1", "started_at": "t"}

    store = _Store()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server._start_async(
        _offloop_meta(tmp_path),
        str(tmp_path),
        kind="codex_consult",
        tool="codex_consult",
        spec={"kind": "codex_consult", "cwd": str(tmp_path)},
        deadline=60,
        idempotency_key="k1",
    )
    assert res.get("ok") is not False  # a running JobStarted handle, not an error
    assert seen["thread"] is not threading.main_thread()


async def test_keyed_async_replay_status_runs_off_event_loop(monkeypatch, clean_env, tmp_path):
    import threading

    seen = {}

    class _Store:
        def start_idempotent(self, *a, **k):
            return {"kind": "replay", "job_id": "job-1"}

        def status(self, cwd, job_id):
            seen["thread"] = threading.current_thread()
            return {
                "status": "running",
                "started_at": "t",
                "deadline_seconds": 60,
                "expires_at": None,
            }

    store = _Store()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server._start_async(
        _offloop_meta(tmp_path),
        str(tmp_path),
        kind="codex_consult",
        tool="codex_consult",
        spec={"kind": "codex_consult", "cwd": str(tmp_path)},
        deadline=60,
        idempotency_key="k1",
    )
    assert res["meta"]["idempotency_replayed"] is True
    assert seen["thread"] is not threading.main_thread()


async def test_keyed_sync_start_idempotent_runs_off_event_loop(monkeypatch, clean_env, tmp_path):
    import threading

    seen = {}

    class _Store:
        def start_idempotent(self, *a, **k):
            seen["thread"] = threading.current_thread()
            return {"kind": "conflict"}  # short-circuits before any await loop

    store = _Store()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server._run_sync(
        _offloop_meta(tmp_path),
        str(tmp_path),
        kind="codex_consult",
        tool="codex_consult",
        spec={"kind": "codex_consult", "cwd": str(tmp_path)},
        timeout=60,
        detail_v="summary",
        ctx=None,
        idempotency_key="k1",
    )
    assert res["error"]["code"] == "idempotency_conflict"
    assert seen["thread"] is not threading.main_thread()


async def test_unkeyed_start_stamps_result_format(monkeypatch, clean_env, tmp_path):
    # The job record must carry the writer's persisted-format version so replay can
    # tell a cross-release payload from a corrupt one (#305).
    from codex_in_claude.schemas import RESULT_FORMAT

    seen = {}

    class _Store:
        def start(self, *a, **k):
            seen["extra"] = k.get("extra")
            return ("job-1", "t")

    store = _Store()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    await server._start_job(
        _offloop_meta(tmp_path),
        str(tmp_path),
        kind="codex_consult",
        spec={"kind": "codex_consult", "cwd": str(tmp_path)},
        deadline=60,
    )
    assert seen["extra"] == {"result_format": RESULT_FORMAT}


async def test_keyed_async_start_stamps_result_format(monkeypatch, clean_env, tmp_path):
    from codex_in_claude.schemas import RESULT_FORMAT

    seen = {}

    class _Store:
        def start_idempotent(self, *a, **k):
            seen["extra"] = k.get("extra")
            return {"kind": "created", "job_id": "job-1", "started_at": "t"}

    store = _Store()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    await server._start_async(
        _offloop_meta(tmp_path),
        str(tmp_path),
        kind="codex_consult",
        tool="codex_consult",
        spec={"kind": "codex_consult", "cwd": str(tmp_path)},
        deadline=60,
        idempotency_key="k1",
    )
    assert seen["extra"] == {"result_format": RESULT_FORMAT}


async def test_keyed_sync_start_stamps_result_format(monkeypatch, clean_env, tmp_path):
    from codex_in_claude.schemas import RESULT_FORMAT

    seen = {}

    class _Store:
        def start_idempotent(self, *a, **k):
            seen["extra"] = k.get("extra")
            return {"kind": "conflict"}  # short-circuits before any await loop

    store = _Store()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    await server._run_sync(
        _offloop_meta(tmp_path),
        str(tmp_path),
        kind="codex_consult",
        tool="codex_consult",
        spec={"kind": "codex_consult", "cwd": str(tmp_path)},
        timeout=60,
        detail_v="summary",
        ctx=None,
        idempotency_key="k1",
    )
    assert seen["extra"] == {"result_format": RESULT_FORMAT}


async def test_unkeyed_start_cancel_during_spawn_cancels_job(monkeypatch, clean_env, tmp_path):
    # #199 regression: moving the spawn off-loop made it cancellable. A cancellation that
    # lands *during* the spawn must not orphan the just-started (paid, unkeyed) job — the
    # shielded start runs to completion and its cleanup callback cancels the resulting job.
    import threading

    gate = threading.Event()
    cancels = []

    class _Store:
        def start(self, *a, **k):
            gate.wait(5.0)  # hold the spawn open so we can cancel mid-flight
            return ("job-1", "t")

        def cancel(self, cwd, job_id):
            cancels.append(job_id)

    store = _Store()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    task = asyncio.create_task(
        server._start_job(
            _offloop_meta(tmp_path),
            str(tmp_path),
            kind="codex_consult",
            spec={"kind": "codex_consult", "cwd": str(tmp_path)},
            deadline=60,
        )
    )
    await asyncio.sleep(0.1)  # let the task reach the shielded to_thread
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()  # release the spawn; the done-callback must now cancel the job
    deadline = time.monotonic() + 5.0
    while not cancels and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert cancels == ["job-1"]  # spend stopped despite cancellation mid-spawn


async def test_unkeyed_start_cancel_during_failed_spawn_cancels_nothing(
    monkeypatch, clean_env, tmp_path
):
    # If the shielded spawn *fails* after its awaiter was cancelled, there is no job to
    # stop — the cleanup callback must not call store.cancel (#199).
    import threading

    gate = threading.Event()
    cancels = []

    class _Store:
        def start(self, *a, **k):
            gate.wait(5.0)
            raise OSError("spawn failed")

        def cancel(self, cwd, job_id):
            cancels.append(job_id)  # pragma: no cover - must never run for a failed spawn

    store = _Store()
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    task = asyncio.create_task(
        server._start_job(
            _offloop_meta(tmp_path),
            str(tmp_path),
            kind="codex_consult",
            spec={"kind": "codex_consult", "cwd": str(tmp_path)},
            deadline=60,
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()  # release the failing spawn; the callback sees an exception, cancels nothing
    await asyncio.sleep(0.2)  # give the done-callback a chance to (not) fire
    assert cancels == []


async def test_swallow_future_result_is_safe_on_cancelled_and_failed():
    # The fire-and-forget cleanup callback must never re-raise: a cancelled future (loop
    # teardown) is skipped, and a failed future has its exception retrieved (no warning).
    loop = asyncio.get_running_loop()

    cancelled = loop.create_future()
    cancelled.cancel()
    await asyncio.sleep(0)  # let the cancellation settle
    server._swallow_future_result(cancelled)  # must not raise CancelledError

    failed = loop.create_future()
    failed.set_exception(RuntimeError("cancel failed"))
    server._swallow_future_result(failed)  # retrieves the exception; no raise
    assert failed.exception() is not None  # still readable after retrieval


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


def _force_not_ready(monkeypatch):
    """Make codex read as installed-but-unauthenticated so codex_status reports a static
    'unknown' quota (not_ready()) without spawning the app-server for a live read (hermetic)."""
    from codex_in_claude import server

    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (False, "run codex login"))


def test_codex_status_ready_uses_live_read(monkeypatch):
    # When codex is ready, codex_status fetches quota LIVE (no model spend) via live_read,
    # not from the cache. Monkeypatched so no real app-server is spawned.
    from codex_in_claude import rate_limit, server
    from codex_in_claude.schemas import RateLimit

    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "auth (ChatGPT)."))
    called = {}

    def _fake_live_read(*, timeout_seconds, **kw):
        called["timeout"] = timeout_seconds
        return RateLimit(status="available", source="app_server_live", plan_type="plus")

    monkeypatch.setattr(rate_limit, "live_read", _fake_live_read)
    result = server.codex_status()
    assert result["rate_limit"]["status"] == "available"
    assert result["rate_limit"]["source"] == "app_server_live"
    assert called["timeout"] == rate_limit.READ_TIMEOUT_SECONDS


def test_codex_status_not_ready_is_unknown_without_spawning(monkeypatch):
    # When codex is not ready, codex_status reports a static 'unknown' and does NOT spawn the
    # app-server (no cache exists to read, and an unauthenticated read would fail anyway).
    from codex_in_claude import rate_limit, server

    _force_not_ready(monkeypatch)

    def _boom(**kw):
        raise AssertionError("live_read must not be called when codex is not ready")

    monkeypatch.setattr(rate_limit, "live_read", _boom)
    result = server.codex_status()
    assert result["rate_limit"]["status"] == "unknown"
    assert result["rate_limit"]["source"] == "app_server_live"
    assert result["rate_limit"]["note"]


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


def test_capabilities_include_parameter_contracts_fold_in():
    """A resource-blind client can reach the full codex://params contracts from tools/list
    alone via the parameter-contracts fold-in (#333)."""
    from codex_in_claude.param_contracts import PARAMETER_CONTRACTS
    from codex_in_claude.server import codex_capabilities

    caps = codex_capabilities(include_schemas=["parameter-contracts"])
    embedded = caps["schemas"]["parameter-contracts"]
    # The embedded document is the full contract body, including the moved-out detail.
    assert set(embedded["params"]) == set(PARAMETER_CONTRACTS)
    assert "idempotency_in_progress" in embedded["params"]["idempotency_key"]["full"]


def test_capabilities_result_resource_returns_full_schema():
    from codex_in_claude.server import capabilities_result_resource

    schema = capabilities_result_resource()
    assert "tool_details" in schema["properties"]
    assert "ToolCapability" in schema["$defs"]


def test_status_result_resource_returns_full_schema():
    from codex_in_claude.server import status_result_resource

    schema = status_result_resource()
    assert "rate_limit" in schema["properties"]
    assert "RateLimit" in schema["$defs"]


def test_capabilities_include_schemas_covers_all_four_tokens():
    from codex_in_claude.server import codex_capabilities

    caps = codex_capabilities(
        include_schemas=["error-envelope", "result-meta", "capabilities-result", "status-result"]
    )
    assert set(caps["schemas"]) == {
        "error-envelope",
        "result-meta",
        "capabilities-result",
        "status-result",
    }
    assert "ToolCapability" in caps["schemas"]["capabilities-result"]["$defs"]
    assert "RateLimit" in caps["schemas"]["status-result"]["$defs"]


async def test_include_schemas_input_enum_lists_all_tokens():
    """The MCP-advertised input enum — not just the runtime dict — must carry the new
    tokens; a direct Python call bypasses FastMCP/Pydantic arg validation, so this is
    what catches a forgotten IncludeSchemasParam Literal widening."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    schema = tools["codex_capabilities"].parameters
    prop = schema["properties"]["include_schemas"]
    # Optional[list[Literal[...]]] renders as a nullable anyOf; find the array branch.
    branches = prop.get("anyOf", [prop])
    items_schema = next(branch["items"] for branch in branches if "items" in branch)
    enum = items_schema["enum"]
    assert set(enum) == {
        "error-envelope",
        "result-meta",
        "capabilities-result",
        "status-result",
        "parameter-contracts",
    }


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
        reasoning_effort=None,
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
        reasoning_effort=None,
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


async def test_job_list_and_status_flag_stored_error_result_ok_false(
    clean_env, tmp_path, monkeypatch
):
    # #335: a stored ERROR envelope lists/reports status done, result_available true —
    # result_ok=false is the only field that tells it apart from a success without a fetch.
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    envelope = _timeout_error_envelope(str(tmp_path))
    monkeypatch.setattr(server, "_worker_cmd", _fake_worker_cmd(envelope))
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    listing = await server.codex_job_list(workspace_root=str(tmp_path))
    assert len(listing["jobs"]) == 1
    entry = listing["jobs"][0]
    assert entry["status"] == "done" and entry["result_available"] is True
    assert entry["result_ok"] is False
    st = await server.codex_job_status(entry["job_id"], workspace_root=str(tmp_path))
    assert st["result_ok"] is False


async def test_job_status_flags_stored_success_result_ok_true(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _no_codex_sentinel(monkeypatch)
    monkeypatch.setattr(
        server, "_worker_cmd", _fake_worker_cmd(_consult_success_envelope(str(tmp_path)))
    )
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is True
    st = await server.codex_job_status(res["meta"]["job_id"], workspace_root=str(tmp_path))
    assert st["result_ok"] is True
    listing = await server.codex_job_list(workspace_root=str(tmp_path))
    assert listing["jobs"][0]["result_ok"] is True


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
        reasoning_effort=None,
        timeout_seconds=180,
        scope="working_tree",
    ).model_dump(mode="json")
    envelope = {
        "ok": True,
        "tool": "codex_review_changes",
        "summary": "reviewed",
        "verdict": "pass",
        "confidence": "high",
        "review_status": "completed",
        "coverage": {"status": "complete"},
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
        reasoning_effort=None,
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
        reasoning_effort=None,
        timeout_seconds=60,
    )
    handle = await server._start_job(
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
        reasoning_effort=None,
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
        reasoning_effort=None,
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

    def start_idempotent(
        self,
        cmd_factory,
        cwd,
        *,
        kind,
        tool,
        key,
        arg_hash,
        extra=None,
        write_spec=None,
        lock_timeout=None,
    ):
        self.idem_calls.append(
            {
                "tool": tool,
                "key": key,
                "arg_hash": arg_hash,
                "kind": kind,
                "lock_timeout": lock_timeout,
            }
        )
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


async def test_consult_async_io_error_is_temporary(monkeypatch, clean_env, tmp_path):
    # The agent-visible contract for a transient read failure (#202): the existing
    # internal_error code (reused, so no fingerprint bump), temporary, a 1s backoff, and
    # repair prose that steers to the SAME key — not a fresh paid run. A typo like
    # "ioerror" would fall through to the idempotency_in_progress envelope and this test
    # would fail, pinning the mapping the CHANGELOG promises.
    store = _FakeIdemStore({"kind": "io_error"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult_async("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert res["error"]["temporary"] is True
    assert res["error"]["retry_after_ms"] == server._IDEM_IO_ERROR_RETRY_MS
    assert "same idempotency_key" in res["error"]["repair"]["alternative"]


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


async def test_consult_sync_reattach_classifies_incompatibility(monkeypatch, clean_env, tmp_path):
    # The sync tools share _finished_job_envelope via the keyed reattach path, so they
    # can surface job_result_incompatible too (#305).
    from codex_in_claude.schemas import RESULT_FORMAT

    done = _ok_record("done")
    done["kind"] = "codex_consult"
    done["extra"] = {"result_format": RESULT_FORMAT + 1}
    env = {
        "ok": True,
        "tool": "codex_consult",
        "summary": "answer",
        "field_from_the_future": "x",
        "meta": server._base_meta(
            str(tmp_path),
            "param",
            tier="consult",
            sandbox="read-only",
            isolation="inherit",
            model=None,
            reasoning_effort=None,
            timeout_seconds=180,
        ).model_dump(mode="json"),
    }
    store = _FakeIdemStore(
        {"kind": "replay", "job_id": "job-abc"}, snapshot=done, record=done, result_json=env
    )
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is False
    assert res["error"]["code"] == "job_result_incompatible"


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
            reasoning_effort=None,
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
        reasoning_effort=None,
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


async def test_keyed_await_timeout_repair_points_at_polling(monkeypatch, clean_env, tmp_path):
    """The keyed-timeout repair must steer the agent to POLL the still-running shared
    job, NOT re-run via the async variant. Sync and async are different dedup
    identities, so following the table's async escape hatch would start a second paid
    run while the first completes unobserved (#201)."""
    monkeypatch.setattr(server, "_SYNC_AWAIT_GRACE_S", 0)
    rec = _ok_record("running")
    rec["poll_after_ms"] = 4000  # the store's grown backoff for a long-running job
    store = _FakeStore(status_dict=rec)
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
    err = res["error"]
    assert err["code"] == "timeout"
    repair = err["repair"]
    # Machine-actionable repair matches the still-running job_running shape: poll the
    # existing run, don't retry. Prose alone would contradict next_step=inspect_and_retry.
    assert repair["next_step"] == "poll_job_status"
    assert repair["tool"] == "codex_job_status"
    assert repair["arguments"] == {"job_id": "job-abc", "workspace_root": str(tmp_path)}
    assert err["retry_after_ms"] == 4000  # echoed from the record's poll_after_ms
    alt = repair["alternative"]
    assert "codex_job_status" in alt and "codex_job_result" in alt
    # Must NOT push toward the async variants — that double-pays for a keyed run.
    for async_tool in ("codex_consult_async", "codex_review_changes_async", "codex_delegate_async"):
        assert async_tool not in alt


async def test_unkeyed_await_timeout_cancels_job(monkeypatch, clean_env, tmp_path):
    """The prior (no-key) behavior is preserved: a timed-out unkeyed waiter cancels,
    and keeps the table repair pointing at the async escape hatch — re-running there is
    correct because the job WAS cancelled (#201)."""
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
    # Unkeyed keeps the static timeout-table repair (async re-run is the right recovery).
    assert res["error"]["repair"]["next_step"] == "inspect_and_retry"
    assert "codex_consult_async" in res["error"]["repair"]["alternative"]


async def test_idempotency_key_description_scopes_to_concrete_tool():
    """The idempotency_key contract preserves #201 across the #333 inline/resource split.

    The inline summary (what ships on the wire) keeps per-tool scoping and the sync/async
    separation, and must NOT reintroduce the misleading 'TTL window' single-horizon phrase;
    the true fail-closed horizon (max runtime + grace + TTL) and the replay marker moved to
    the codex://params full contract, where they remain discoverable."""
    from codex_in_claude.param_contracts import PARAMETER_CONTRACTS

    tools = {t.name: t for t in await server.mcp.list_tools()}
    inline = tools["codex_consult"].parameters["properties"]["idempotency_key"]["description"]
    assert inline == PARAMETER_CONTRACTS["idempotency_key"].summary  # wire == registry summary
    # First-call facts stay inline.
    assert "tool" in inline.lower() and "workspace" in inline.lower()  # per-tool+workspace scope
    assert "separate tools" in inline.lower()  # sync and async never share a key
    assert "TTL window" not in inline  # the misleading single-horizon phrase stays gone
    # The full fail-closed horizon and replay marker moved to the resource, not deleted.
    full = PARAMETER_CONTRACTS["idempotency_key"].full
    assert "termination grace" in full
    assert "idempotency_replayed=true" in full


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


async def test_consult_sync_io_error_is_temporary_after_wait(monkeypatch, clean_env, tmp_path):
    # The sync path waits briefly for a transient read failure to self-heal into a
    # replay; only past the wait deadline does it surface the io_error envelope. With the
    # wait budget zeroed, a persistent io_error surfaces the same contract as the async
    # path (#202).
    monkeypatch.setattr(server, "_IDEM_SYNC_INPROGRESS_WAIT_S", 0.0)  # don't actually block
    store = _FakeIdemStore({"kind": "io_error"})
    monkeypatch.setattr(server.config, "job_store", lambda: store)
    res = await server.codex_consult("q", workspace_root=str(tmp_path), idempotency_key="k1")
    assert res["ok"] is False
    assert res["error"]["code"] == "internal_error"
    assert res["error"]["temporary"] is True
    assert res["error"]["retry_after_ms"] == server._IDEM_IO_ERROR_RETRY_MS
    assert "same idempotency_key" in res["error"]["repair"]["alternative"]


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


# --- codex_transfer -------------------------------------------------------------


def _ready_codex(monkeypatch):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "auth (ChatGPT)."))


def _patch_validation(monkeypatch, realpath="/home/u/.claude/projects/s/x.jsonl", reason=None):
    monkeypatch.setattr(
        server.appserver,
        "validate_transcript_path",
        lambda _p: server.appserver.PathValidation(realpath, reason),
    )


def _patch_transfer(monkeypatch, outcome):
    monkeypatch.setattr(server.appserver, "transfer_session", lambda **_kw: outcome)


async def test_transfer_success_notification(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.OK,
            thread_id="t9",
            thread_id_source=server.appserver.ThreadIdSource.IMPORT_NOTIFICATION,
            import_id="imp-7",
            codex_home="/home/u/.codex",
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is True
    assert result["tool"] == "codex_transfer"
    assert result["thread_id"] == "t9"
    assert result["resume_command"] == "codex resume t9"
    assert result["source_path"] == "/home/u/.claude/projects/s/x.jsonl"
    assert result["meta"]["thread_id_source"] == "import_notification"
    assert result["meta"]["import_id"] == "imp-7"
    assert result["meta"]["codex_home"] == "/home/u/.codex"
    assert result["fingerprint"].endswith("schema-55")
    # TransferResult's only wire path — unreachable from the free-tool walk (#304).
    assert result["server_version"] == __version__


async def test_transfer_success_from_ledger(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.OK,
            thread_id="t-led",
            thread_id_source=server.appserver.ThreadIdSource.LEDGER,
            codex_home="/home/u/.codex",
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is True
    assert result["meta"]["thread_id_source"] == "ledger"
    assert result["meta"]["import_id"] is None


async def test_transfer_invalid_path_no_spawn(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(
        monkeypatch, realpath=None, reason="transcript_path must be a .jsonl session transcript."
    )
    called = []
    monkeypatch.setattr(server.appserver, "transfer_session", lambda **_kw: called.append(1))
    result = await server.codex_transfer(transcript_path="/x.txt")
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    assert result["error"]["details"]["field"] == "transcript_path"
    assert not called  # no subprocess attempted


async def test_transfer_codex_not_found(monkeypatch):
    """A missing binary is codex_not_found, and the auth probe is never even reached.

    The ordering is load-bearing, not incidental: `login_status()` returns None for a
    missing binary *and* for an unanswered probe, but codex_auth_indeterminate promises
    `temporary=True`, which is false for a missing binary. This gate absorbing the
    missing-binary cause is what makes that promise honest (#252)."""
    probed = []
    monkeypatch.setattr(server.codex, "codex_version", lambda: None)
    monkeypatch.setattr(server.codex, "login_status", lambda: probed.append(1) or (None, None))
    _patch_validation(monkeypatch)
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "codex_not_found"
    assert not probed  # codex_version() must gate ahead of login_status()


async def test_transfer_unauthenticated(monkeypatch):
    called = []
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (False, "run codex login"))
    monkeypatch.setattr(server.appserver, "transfer_session", lambda **_kw: called.append(1))
    _patch_validation(monkeypatch)
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "codex_auth_required"
    assert result["error"]["temporary"] is False
    assert not called  # no app-server spawned


async def test_transfer_auth_indeterminate(monkeypatch):
    """`codex login status` could not run: fail closed, but do not tell an
    already-authenticated user to run `codex login` (#252)."""
    called = []
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (None, None))
    monkeypatch.setattr(server.appserver, "transfer_session", lambda **_kw: called.append(1))
    _patch_validation(monkeypatch)
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "codex_auth_indeterminate"
    assert result["error"]["temporary"] is True
    assert result["error"]["repair"]["next_step"] == "inspect_and_retry"
    assert not called  # no app-server spawned, no side-effecting import


async def test_transfer_unsupported(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(status=server.appserver.TransferStatus.UNSUPPORTED),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "transfer_unsupported"
    assert result["error"]["temporary"] is False


async def test_transfer_item_failure(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.ITEM_FAILURE,
            message="could not parse session",
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "transfer_failed"
    assert "could not parse session" in result["error"]["message"]


async def test_transfer_incomplete_names_ledger(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.INCOMPLETE,
            ledger_path="/home/u/.codex/external_agent_session_imports.json",
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "transfer_incomplete"
    assert "external_agent_session_imports.json" in result["error"]["message"]


async def test_transfer_timeout(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(status=server.appserver.TransferStatus.TIMED_OUT),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "timeout"
    assert result["error"]["temporary"] is True


async def test_transfer_protocol_error(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.PROTOCOL_ERROR,
            message="codex app-server exited before the import completed.",
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "cli_contract_changed"


# --- app_server_stderr_tail: surfaced only where it is the primary diagnostic (#275) ------

_STDERR_ELIGIBLE = {
    server.appserver.TransferStatus.PROTOCOL_ERROR: "cli_contract_changed",
    server.appserver.TransferStatus.TIMED_OUT: "timeout",
    server.appserver.TransferStatus.INCOMPLETE: "transfer_incomplete",
}


async def _transfer_with_tail(monkeypatch, status, **fields):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(status=status, stderr_tail="panic: boom\nFINAL", **fields),
    )
    return await server.codex_transfer(transcript_path="/x.jsonl")


@pytest.mark.parametrize("status,code", list(_STDERR_ELIGIBLE.items()))
async def test_transfer_surfaces_stderr_tail_for_eligible_codes(monkeypatch, status, code):
    result = await _transfer_with_tail(monkeypatch, status)
    assert result["error"]["code"] == code
    # The untrusted child stderr rides a dedicated field, never error.message.
    assert result["error"]["app_server_stderr_tail"] == "panic: boom\nFINAL"
    assert "panic: boom" not in result["error"]["message"]


async def test_transfer_omits_stderr_tail_for_transfer_failed(monkeypatch):
    # ITEM_FAILURE always carries a structured message (surfaced post-#276); a second
    # arbitrary-text channel is not worth its injection/leak surface here.
    result = await _transfer_with_tail(
        monkeypatch,
        server.appserver.TransferStatus.ITEM_FAILURE,
        message="could not parse session",
    )
    assert result["error"]["code"] == "transfer_failed"
    assert "app_server_stderr_tail" not in result["error"]


async def test_transfer_omits_stderr_tail_for_unsupported(monkeypatch):
    result = await _transfer_with_tail(monkeypatch, server.appserver.TransferStatus.UNSUPPORTED)
    assert result["error"]["code"] == "transfer_unsupported"
    assert "app_server_stderr_tail" not in result["error"]


async def test_transfer_success_never_carries_stderr_tail(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.OK,
            thread_id="t1",
            thread_id_source=server.appserver.ThreadIdSource.IMPORT_NOTIFICATION,
            codex_home="/home/u/.codex",
            stderr_tail="noise on a healthy run",
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is True
    assert "app_server_stderr_tail" not in result


async def test_transfer_eligible_code_without_tail_omits_the_field(monkeypatch):
    # exclude_none: an eligible code whose outcome captured no stderr must not emit a null.
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.PROTOCOL_ERROR, stderr_tail=None
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["error"]["code"] == "cli_contract_changed"
    assert "app_server_stderr_tail" not in result["error"]


async def test_transfer_spawn_failed(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(status=server.appserver.TransferStatus.SPAWN_FAILED),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "codex_not_found"


async def test_transfer_resume_command_is_shell_quoted(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.OK,
            thread_id="id with space;rm",
            thread_id_source=server.appserver.ThreadIdSource.IMPORT_NOTIFICATION,
            codex_home="/home/u/.codex",
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    # shlex.join quotes the pathological id so the pasted command stays one safe argument.
    assert result["resume_command"] == "codex resume 'id with space;rm'"


async def test_transfer_resume_command_plain_id_unquoted(monkeypatch):
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.OK,
            thread_id="thread-fresh-0001",
            thread_id_source=server.appserver.ThreadIdSource.IMPORT_NOTIFICATION,
            codex_home="/home/u/.codex",
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["resume_command"] == "codex resume thread-fresh-0001"


async def test_transfer_protocol_error_maps_to_cli_contract_changed(monkeypatch):
    """PROTOCOL_ERROR maps to cli_contract_changed, and the appserver-supplied
    message passes through _transfer_outcome_envelope's `message = outcome.message
    or "..."` faithfully, with no further transformation.

    This does NOT test that no raw/oversized value can reach the message — that
    guarantee is constructed and enforced at the appserver layer, where
    outcome.message is built from fixed strings (see test_appserver.py, e.g.
    test_invalid_codex_home_is_protocol_error and the invalid-target tests, which
    assert an oversized value is absent from outcome.message before it ever
    reaches this mapping code).
    """
    _ready_codex(monkeypatch)
    _patch_validation(monkeypatch)
    message = "codex app-server reported an invalid codexHome (must be a bounded, absolute path)."
    _patch_transfer(
        monkeypatch,
        server.appserver.TransferOutcome(
            status=server.appserver.TransferStatus.PROTOCOL_ERROR,
            message=message,
        ),
    )
    result = await server.codex_transfer(transcript_path="/x.jsonl")
    assert result["ok"] is False
    assert result["error"]["code"] == "cli_contract_changed"
    assert result["error"]["message"] == message


# --- CODEX_IN_CLAUDE_EXTRA_ARGS: status + preflight before spend (#231) -----------


def test_status_reports_valid_extra_args(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "auth."))
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c model_provider=litellm --profile work")
    res = server.codex_status()
    assert res["extra_args_configured"] is True
    assert res["extra_args_count"] == 2
    assert res["extra_args_valid"] is True


def test_status_reports_invalid_extra_args(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "auth."))
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "--json")  # not allowlisted
    res = server.codex_status()
    assert res["extra_args_configured"] is True
    assert res["extra_args_valid"] is False


def test_status_unset_extra_args_defaults(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda: (True, "auth."))
    res = server.codex_status()
    assert res["extra_args_configured"] is False
    assert res["extra_args_valid"] is True


async def _assert_extra_args_rejected(res):
    assert res["ok"] is False
    assert res["error"]["code"] == "extra_args_rejected"
    assert res["error"]["repair"]["next_step"] == "correct_config"


async def test_consult_preflights_invalid_extra_args(monkeypatch, clean_env, tmp_path):
    called = False

    async def fake(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(server.codex, "run_codex_exec", fake)
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "--json")
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    await _assert_extra_args_rejected(res)
    assert called is False  # rejected before any spend


async def test_review_preflights_invalid_extra_args(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "bare-positional")
    res = await server.codex_review_changes(workspace_root=str(tmp_path))
    await _assert_extra_args_rejected(res)


async def test_delegate_preflights_invalid_extra_args(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "-c sandbox_mode=danger-full-access")
    res = await server.codex_delegate("do a thing", workspace_root=str(tmp_path))
    await _assert_extra_args_rejected(res)


async def test_dry_run_preflights_invalid_extra_args(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "--json")
    res = await server.codex_dry_run(workspace_root=str(tmp_path))
    await _assert_extra_args_rejected(res)


async def test_delegate_dry_run_preflights_invalid_extra_args(monkeypatch, clean_env, tmp_path):
    monkeypatch.setenv("CODEX_IN_CLAUDE_EXTRA_ARGS", "--json")
    res = await server.codex_delegate_dry_run("task", workspace_root=str(tmp_path))
    await _assert_extra_args_rejected(res)


# --- server_version reaches the wire, on every surface (#304, Task 2) --------
# server_version defaulting correctly IN THE MODEL (Task 1) proves nothing about what
# a client actually receives — an exclude_none path, a custom dump, or middleware could
# still drop it before the envelope reaches the wire. These assert on the real emitted
# payload through the in-process MCP Client boundary (mirroring the existing MCP-
# boundary tests above), success AND error, not on the Pydantic model directly.


async def test_success_envelope_carries_server_version(clean_env):
    from fastmcp import Client

    async with Client(server.mcp) as client:
        result = await client.call_tool("codex_status", {})
    payload = json.loads(result.content[0].text)
    assert payload["server_version"] == __version__


async def test_error_envelope_carries_server_version(clean_env, tmp_path, monkeypatch):
    """The error path is the one an audit reads — assert it directly on the emitted
    envelope, not via the model (#304)."""
    from fastmcp import Client

    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "codex_job_status",
            {"job_id": "does-not-exist", "workspace_root": str(tmp_path)},
            raise_on_error=False,
        )
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is False
    assert payload["meta"]["server_version"] == __version__


async def test_every_free_tool_envelope_carries_server_version(clean_env, tmp_path, monkeypatch):
    """A future SUCCESS result model added without server_version must fail here, not
    silently become an 'unknown' bucket in someone's audit (#304).

    Coverage split across the 10 models that carry a top-level `fingerprint` field
    (`VERSION_BEARING_MODELS` in tests/test_schemas.py):

    WIRE-COVERED here (a real SUCCESS envelope observed over the in-process MCP Client
    boundary): StatusResult (codex_status), CapabilitiesResult (codex_capabilities),
    JobListResult (codex_job_list), ModelCatalogResult (codex_models), DryRunResult
    (codex_dry_run), DelegateDryRunResult (codex_delegate_dry_run). Meta is covered on
    the wire too, but via the ERROR path in test_error_envelope_carries_server_version
    above (every error envelope carries a Meta), not this SUCCESS walk.

    WIRE-COVERED ELSEWHERE (not reachable from this free-tool walk, which only calls tools
    that need no job record or subprocess, but asserted on a real emitted envelope all the
    same): JobStarted in test_delegate_async_returns_job_id, the SUCCESS variant of
    JobStatus in test_job_status_done — both via the fake job store — and TransferResult in
    test_transfer_success_notification, via a mocked app-server outcome. None of the three
    needs a paid call.

    All 10 are ALSO guaranteed structurally, by field-declaration introspection in
    test_schemas.py::test_every_fingerprint_bearing_model_carries_server_version. That guard
    is real (it fails if server_version is removed from any model) but it is not sufficient
    on its own: it inspects the model, so it cannot see a construction or serialization path
    that drops or nulls the field before the wire. Hence the wire assertions above and in
    those three tests.

    Only FREE tools are called below — no paid Codex/OpenAI spend, and codex_transfer is
    deliberately omitted (see above) even though codex_capabilities lists it as free."""
    from fastmcp import Client

    monkeypatch.setenv("CODEX_IN_CLAUDE_STATE_DIR", str(tmp_path / "state"))
    _init_repo(tmp_path)  # codex_dry_run / codex_delegate_dry_run need a real git repo
    # to reach their SUCCESS branch rather than a not_a_git_repo ErrorResult.
    free_calls = [
        ("codex_status", {}),
        ("codex_capabilities", {}),
        ("codex_job_list", {"workspace_root": str(tmp_path)}),
        ("codex_models", {}),
        ("codex_dry_run", {"scope": "working_tree", "workspace_root": str(tmp_path)}),
        ("codex_delegate_dry_run", {"task": "add a feature", "workspace_root": str(tmp_path)}),
    ]
    async with Client(server.mcp) as client:
        for tool, params in free_calls:
            result = await client.call_tool(tool, params)
            payload = json.loads(result.content[0].text)
            # Guard the guard: a SUCCESS assertion on an accidental ERROR envelope would
            # silently validate the wrong model and prove nothing about the target.
            assert payload.get("ok") is True, f"{tool} did not return a SUCCESS envelope: {payload}"
            carrier = payload.get("meta", payload)  # meta-bearing or top-level
            assert carrier.get("server_version") == __version__, f"{tool} lost server_version"


# --- Reasoning-effort surface (#309) ------------------------------------------------
def _capture_run_sync(monkeypatch):
    calls: dict = {}

    async def fake_run_sync(meta, cwd, **kw):
        calls.update({"meta": meta, "cwd": cwd, **kw})
        return {"ok": True, "_captured": True}

    monkeypatch.setattr(server, "_run_sync", fake_run_sync)
    return calls


# --- untracked policy plumbing + idempotency-hash compatibility (#319) --------------
async def test_review_untracked_default_omitted_from_spec(monkeypatch, clean_env, tmp_path):
    # Whole-domain rule: the default `untracked` must NOT enter the spec, so pre-#319
    # idempotency hashes and stored worker specs (which lack the key) keep replaying.
    calls = _capture_run_sync(monkeypatch)
    await server.codex_review_changes(scope="working_tree", workspace_root=str(tmp_path))
    assert "untracked" not in calls["spec"]


async def test_review_untracked_include_written_to_spec(monkeypatch, clean_env, tmp_path):
    calls = _capture_run_sync(monkeypatch)
    await server.codex_review_changes(
        scope="working_tree", workspace_root=str(tmp_path), untracked="include"
    )
    assert calls["spec"]["untracked"] == "include"


async def test_review_untracked_exclude_written_to_spec(monkeypatch, clean_env, tmp_path):
    calls = _capture_run_sync(monkeypatch)
    await server.codex_review_changes(
        scope="working_tree", workspace_root=str(tmp_path), untracked="exclude"
    )
    assert calls["spec"]["untracked"] == "exclude"


async def test_dry_run_invalid_untracked_returns_structured_error(clean_env, tmp_path):
    # A direct Python call bypassing MCP Literal validation must get the structured
    # invalid_arguments envelope, not an unhandled InvalidUntrackedError (PR #322 review).
    _init_repo(tmp_path)
    res = await server.codex_dry_run(
        scope="working_tree", workspace_root=str(tmp_path), untracked="bogus"
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_arguments"


async def test_dry_run_untracked_include_gathers_untracked(clean_env, tmp_path):
    # End-to-end through the real gather path: `include` opts into sending untracked
    # contents, so the preview would call the model and reports complete coverage.
    _init_repo(tmp_path)
    (tmp_path / "brand_new.py").write_text("value = 1\n")
    res = await server.codex_dry_run(
        scope="working_tree", workspace_root=str(tmp_path), untracked="include"
    )
    assert res["ok"] is True
    assert res["would_call_model"] is True
    assert res["prompt_bytes"] > 0
    assert res["coverage"]["untracked_files_included"] == 1
    assert res["coverage"]["status"] == "complete"


async def test_consult_reasoning_effort_call_beats_server_default(monkeypatch, clean_env, tmp_path):
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "low")
    calls = _capture_run_sync(monkeypatch)
    await server.codex_consult("q", workspace_root=str(tmp_path), reasoning_effort="xhigh")
    assert calls["meta"].reasoning_effort == "xhigh"
    assert calls["spec"]["reasoning_effort"] == "xhigh"


async def test_consult_reasoning_effort_falls_back_to_server_default(
    monkeypatch, clean_env, tmp_path
):
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "low")
    calls = _capture_run_sync(monkeypatch)
    await server.codex_consult("q", workspace_root=str(tmp_path))
    assert calls["meta"].reasoning_effort == "low"
    assert calls["spec"]["reasoning_effort"] == "low"


async def test_consult_empty_reasoning_effort_is_passed_through(monkeypatch, clean_env, tmp_path):
    # Whole-domain rule: an explicit "" is the caller's value, not "unset" — it must
    # not silently fall back to the server default.
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "low")
    calls = _capture_run_sync(monkeypatch)
    await server.codex_consult("q", workspace_root=str(tmp_path), reasoning_effort="")
    assert calls["meta"].reasoning_effort == ""
    assert calls["spec"]["reasoning_effort"] == ""


async def test_spec_without_effort_matches_legacy_hash(monkeypatch, clean_env, tmp_path):
    # Regression (#309): a run with no effort override must build a spec byte-identical
    # to the pre-#309 shape, so live idempotency dedup entries survive the upgrade.
    calls = _capture_run_sync(monkeypatch)
    await server.codex_consult(
        "q", workspace_root=str(tmp_path), extra_context="ctx", timeout_seconds=60
    )
    spec = calls["spec"]
    assert "reasoning_effort" not in spec
    legacy_spec = {
        "kind": "codex_consult",
        "question": "q",
        "extra_context": "ctx",
        "cwd": spec["cwd"],
        "workspace_source": spec["workspace_source"],
        "tier": "consult",
        "sandbox": "read-only",
        "isolation": "inherit",
        "model": None,
        "timeout_seconds": 60,
    }
    assert spec == legacy_spec
    assert server._arg_hash_for_spec(spec) == server._arg_hash_for_spec(legacy_spec)


async def test_review_and_delegate_specs_carry_reasoning_effort(monkeypatch, clean_env, tmp_path):
    calls = _capture_run_sync(monkeypatch)
    monkeypatch.setattr(server.worktree, "ensure_repo_with_head", lambda *a, **k: None)
    await server.codex_review_changes(workspace_root=str(tmp_path), reasoning_effort="medium")
    assert calls["spec"]["reasoning_effort"] == "medium"
    await server.codex_delegate("do work", workspace_root=str(tmp_path), reasoning_effort="high")
    assert calls["spec"]["reasoning_effort"] == "high"


async def test_status_reports_reasoning_effort_defaults(monkeypatch, clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "medium")
    monkeypatch.setattr(server.codex, "codex_version", lambda *a, **k: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda *a, **k: (True, "ok"))
    res = server.codex_status()
    assert res["raw_defaults"]["reasoning_effort"] == "medium"
    assert res["resolved_defaults"]["reasoning_effort"] == "medium"


async def test_status_reasoning_effort_default_null_when_unset(monkeypatch, clean_env):
    monkeypatch.setattr(server.codex, "codex_version", lambda *a, **k: "codex-cli 0.145.0")
    monkeypatch.setattr(server.codex, "login_status", lambda *a, **k: (True, "ok"))
    res = server.codex_status()
    assert res["raw_defaults"]["reasoning_effort"] is None
    assert res["resolved_defaults"]["reasoning_effort"] is None


async def test_dry_run_echoes_model_and_reasoning_effort(monkeypatch, clean_env, tmp_path):
    monkeypatch.setattr(
        gitdiff,
        "gather_diff",
        lambda *a, **k: gitdiff.DiffResult(
            text="diff --git a/x b/x\n+y",
            summary=gitdiff.DiffSummary(1, 1, 0),
            redacted_paths=[],
        ),
    )
    res = await server.codex_dry_run(
        scope="working_tree",
        workspace_root=str(tmp_path),
        model="gpt-5.5",
        reasoning_effort="xhigh",
    )
    assert res["ok"] is True
    assert res["model"] == "gpt-5.5"
    assert res["reasoning_effort"] == "xhigh"


async def test_dry_run_effort_defaults_from_env(monkeypatch, clean_env, tmp_path):
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "low")
    monkeypatch.setattr(
        gitdiff,
        "gather_diff",
        lambda *a, **k: gitdiff.DiffResult(
            text="", summary=gitdiff.DiffSummary(0, 0, 0), redacted_paths=[]
        ),
    )
    res = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is True
    assert res["model"] is None
    assert res["reasoning_effort"] == "low"


async def test_delegate_dry_run_echoes_model_and_reasoning_effort(monkeypatch, clean_env, tmp_path):
    _init_repo(tmp_path)
    res = await server.codex_delegate_dry_run(
        "add a feature",
        workspace_root=str(tmp_path),
        model="gpt-5.5",
        reasoning_effort="max",
    )
    assert res["ok"] is True
    assert res["model"] == "gpt-5.5"
    assert res["reasoning_effort"] == "max"


async def test_capabilities_advertise_reasoning_effort(clean_env):
    res = server.codex_capabilities()
    details = {t["name"]: t for t in res["tool_details"]}
    effort_tools = (
        "codex_consult",
        "codex_consult_async",
        "codex_review_changes",
        "codex_review_changes_async",
        "codex_delegate",
        "codex_delegate_async",
        "codex_dry_run",
        "codex_delegate_dry_run",
    )
    for name in effort_tools:
        assert "reasoning_effort" in details[name]["key_optional_params"], name
    # The backend effort rejection is reachable on every Codex-running tool.
    for name in effort_tools[:6]:
        assert "invalid_reasoning_effort" in details[name]["error_codes"], name
    # An invalid resolved default is rejected before either dry run performs work,
    # so the code is reachable there too (pre-spend shape guard; no Codex involved).
    for name in effort_tools[6:]:
        assert "invalid_reasoning_effort" in details[name]["error_codes"], name


async def test_dry_run_model_echo_reconciles_help_gated_drop(monkeypatch, clean_env, tmp_path):
    # Codex-review regression (#309): on a CLI without --model the paid call DROPS the
    # flag and nulls meta.model; the preview's echo must not claim the dropped override.
    from codex_in_claude import cli_contract, preflight

    monkeypatch.setattr(
        server.preflight,
        "flag_support",
        lambda force=False: preflight.FlagSupport(
            supported=frozenset(cli_contract.ALWAYS_SEND_FLAGS), help_parsed=True
        ),
    )
    monkeypatch.setattr(
        gitdiff,
        "gather_diff",
        lambda *a, **k: gitdiff.DiffResult(
            text="", summary=gitdiff.DiffSummary(0, 0, 0), redacted_paths=[]
        ),
    )
    res = await server.codex_dry_run(
        scope="working_tree",
        workspace_root=str(tmp_path),
        model="gpt-5.5",
        reasoning_effort="high",
    )
    assert res["ok"] is True
    assert res["model"] is None  # would be help-gate-dropped by the paid call
    assert res["reasoning_effort"] == "high"  # the -c pair is never gated


@pytest.mark.parametrize(
    "bad_value",
    [
        "with\x00nul",  # would crash Popen (ValueError) before any classification
        "with\x07bell",  # control character
        "high\x85",  # NEL — a C1 control; the pattern must cover C1, not just C0+DEL
        "x" * 129,  # over the documented max length
    ],
)
async def test_reasoning_effort_shape_bounds_rejected_at_boundary(clean_env, bad_value):
    # Codex-review regression (#309): argv-hostile values are rejected as
    # invalid_arguments at the MCP boundary, never reaching the subprocess.
    res = await server.mcp.call_tool(
        "codex_consult", {"question": "q", "reasoning_effort": bad_value}
    )
    assert res.is_error is True
    sc = res.structured_content
    assert sc["error"]["code"] == "invalid_arguments"
    assert sc["error"]["details"]["field"] == "reasoning_effort"


async def test_reasoning_effort_max_length_boundary_accepted(monkeypatch, clean_env, tmp_path):
    # The documented boundary value itself is accepted and passed through.
    calls = _capture_run_sync(monkeypatch)
    value = "x" * 128
    await server.codex_consult("q", workspace_root=str(tmp_path), reasoning_effort=value)
    assert calls["spec"]["reasoning_effort"] == value


@pytest.mark.parametrize("bad_env", ["y" * 129, "with\x07bell"])
async def test_env_reasoning_effort_shape_rejected_pre_spend(
    monkeypatch, clean_env, tmp_path, bad_env
):
    # Codex re-review regression (#309): the env default never crosses the MCP
    # boundary, so the resolved value is re-checked pre-spend — no run may start.
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", bad_env)

    async def never_run(*a, **k):
        raise AssertionError("a run must not start for an invalid effort default")

    monkeypatch.setattr(server, "_run_sync", never_run)
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_reasoning_effort"
    assert res["error"]["details"]["field"] == "reasoning_effort"
    assert bad_env not in res["error"]["message"]  # value never echoed


async def test_env_reasoning_effort_shape_rejected_in_dry_runs(monkeypatch, clean_env, tmp_path):
    # The previews must fail exactly where the paid call would.
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "z" * 129)
    res = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_reasoning_effort"
    _init_repo(tmp_path)
    res2 = await server.codex_delegate_dry_run("task", workspace_root=str(tmp_path))
    assert res2["ok"] is False
    assert res2["error"]["code"] == "invalid_reasoning_effort"


async def test_env_reasoning_effort_shape_repair_points_at_config(monkeypatch, clean_env, tmp_path):
    # #332: when the hostile value is the resolved env DEFAULT (no per-call override),
    # the machine repair must steer to the operator config — next_step "correct_config"
    # with NO repair tool — not the backend-rejection repair (correct_arguments +
    # codex_models), which would send an agent to make a useless codex_models call while
    # the real fix is the CODEX_IN_CLAUDE_REASONING_EFFORT default.
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "z" * 129)

    async def never_run(*a, **k):
        raise AssertionError("no run may start for an invalid effort default")

    monkeypatch.setattr(server, "_run_sync", never_run)
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res["error"]["code"] == "invalid_reasoning_effort"
    repair = res["error"]["repair"]
    assert repair["next_step"] == "correct_config"
    assert "tool" not in repair  # codex_models does not apply to a config-shape refusal
    # Both free dry runs (which advertise this code) emit the same config repair.
    res_dry = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res_dry["error"]["repair"]["next_step"] == "correct_config"
    assert "tool" not in res_dry["error"]["repair"]
    _init_repo(tmp_path)
    res_del = await server.codex_delegate_dry_run("task", workspace_root=str(tmp_path))
    assert res_del["error"]["repair"]["next_step"] == "correct_config"
    assert "tool" not in res_del["error"]["repair"]


async def test_explicit_reasoning_effort_shape_repair_points_at_arguments(clean_env, tmp_path):
    # #332 provenance: when the hostile value is an EXPLICIT per-call argument that
    # bypassed MCP-boundary validation (only reachable via a direct in-process call —
    # over MCP such a value is invalid_arguments at the boundary and never reaches this
    # guard), the machine repair names the argument: next_step "correct_arguments" with
    # NO tool. The guard must not report it as a config problem.
    res = await server.codex_consult("q", workspace_root=str(tmp_path), reasoning_effort="y" * 129)
    assert res["error"]["code"] == "invalid_reasoning_effort"
    repair = res["error"]["repair"]
    assert repair["next_step"] == "correct_arguments"
    assert "tool" not in repair


async def test_reasoning_effort_surrogate_rejected_with_serializable_envelope(clean_env, tmp_path):
    # Maintainer-review regression (#313): a surrogate code point (category Cs) is
    # outside the Cc ranges but hostile to argv encoding and JSON serialization. A
    # direct in-process call must get the normal invalid_reasoning_effort envelope —
    # with NO raw surrogate echoed anywhere in it (meta.reasoning_effort included),
    # or serializing the envelope itself would fail the same way.
    res = await server.codex_consult(
        "q", workspace_root=str(tmp_path), reasoning_effort="high\ud800"
    )
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_reasoning_effort"
    assert res["error"]["details"]["field"] == "reasoning_effort"
    assert res["meta"].get("reasoning_effort") is None  # invalid raw value never echoed
    json.dumps(res).encode("utf-8")  # the whole envelope survives wire serialization


async def test_reasoning_effort_surrogate_rejected_over_mcp(clean_env):
    # Over MCP the advertised pattern cannot name the surrogate range (see
    # REASONING_EFFORT_VALUE_PATTERN), but pydantic's own string validation
    # (string_unicode) still refuses the value at the boundary — a structured
    # invalid_arguments envelope naming the field, never a raw serialization error.
    res = await server.mcp.call_tool(
        "codex_consult", {"question": "q", "reasoning_effort": "high\ud800"}
    )
    assert res.is_error is True
    sc = res.structured_content
    assert sc["error"]["code"] == "invalid_arguments"
    assert sc["error"]["details"]["field"] == "reasoning_effort"


async def test_env_reasoning_effort_surrogate_rejected_in_dry_run(clean_env, tmp_path):
    # A surrogateescape'd environment value (how a non-UTF-8 env byte surfaces in
    # os.environ) must produce the same envelope through the dry run, not a raw
    # FastMCP/Pydantic serialization error.
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "high\udcff")
    res = await server.codex_dry_run(scope="working_tree", workspace_root=str(tmp_path))
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_reasoning_effort"
    assert res["meta"].get("reasoning_effort") is None
    json.dumps(res).encode("utf-8")


async def test_env_reasoning_effort_shape_parity_sync_async(monkeypatch, clean_env, tmp_path):
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "w" * 129)
    res_async = await server.codex_consult_async("q", workspace_root=str(tmp_path))
    assert res_async["ok"] is False
    assert res_async["error"]["code"] == "invalid_reasoning_effort"


async def test_valid_env_reasoning_effort_still_runs(monkeypatch, clean_env, tmp_path):
    clean_env.setenv("CODEX_IN_CLAUDE_REASONING_EFFORT", "xhigh")
    calls = _capture_run_sync(monkeypatch)
    res = await server.codex_consult("q", workspace_root=str(tmp_path))
    assert res.get("_captured") is True
    assert calls["spec"]["reasoning_effort"] == "xhigh"
