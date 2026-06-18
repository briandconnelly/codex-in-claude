"""Shared propose-tier orchestration.

`run_delegate` runs a coding task in an isolated git worktree (worktree create →
`codex exec` with `workspace-write` → capture diff → cleanup) and returns the
normalized result envelope WITHOUT touching the live tree. Both the synchronous
`codex_delegate` tool and the background `_worker` call this, so the worktree
logic lives in exactly one place. This module is import-light (no FastMCP app) so
the background worker can use it without constructing the server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codex_in_claude import codex, normalize, prompts
from codex_in_claude._core import worktree
from codex_in_claude.schemas import (
    ContextSummary,
    ErrorInfo,
    ErrorResult,
    Meta,
    RawResponse,
    SuccessResult,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _diffstat(diff: str) -> ContextSummary:
    """Cheap files/added/removed counts from a unified diff."""
    files = added = removed = 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return ContextSummary(files_changed=files, lines_added=added, lines_removed=removed)


def _apply_run_meta(meta: Meta, result: codex.CodexExecResult) -> tuple[Usage | None, str | None]:
    """Stamp a finished run's process metadata onto meta; return (usage, session)."""
    meta.elapsed_ms = result.run.elapsed_ms
    meta.command_exit_code = result.run.exit_code
    meta.compat_warnings = result.dropped_flags
    usage, session_id = normalize.parse_event_metadata(result.events)
    meta.usage = usage
    meta.session_id = session_id
    return usage, session_id


async def run_delegate(
    task: str,
    cwd: str,
    meta: Meta,
    *,
    sandbox: str,
    isolation: str,
    timeout_seconds: int,
    model: str | None,
    git_timeout: int,
    on_worktree_parent: Callable[[str], None] | None = None,
) -> dict:
    """Run the propose orchestration and return a SuccessResult|ErrorResult dict.

    `meta` is the pre-built envelope meta (tier=propose). The worktree is always
    cleaned up, even on failure or codex error. `on_worktree_parent`, if given, is
    called with the temp worktree parent as soon as it exists so a background
    worker can record it for hard-kill cleanup."""
    try:
        wt = worktree.create(cwd, timeout=git_timeout, on_parent=on_worktree_parent)
    except worktree.NotAGitRepoError as exc:
        return ErrorResult(
            error=ErrorInfo(
                code="not_a_git_repo",
                message=str(exc),
                repair="Point workspace_root at a git repository (propose needs one).",
                offending_param="workspace_root",
            ),
            meta=meta,
        ).model_dump(mode="json")
    except (worktree.NoCommitsError, worktree.WorktreeError) as exc:
        return ErrorResult(
            error=ErrorInfo(
                code="worktree_error",
                message=str(exc)[:300],
                repair="Ensure the repo has at least one commit and a clean git state.",
            ),
            meta=meta,
        ).model_dump(mode="json")

    if wt.baseline_warning:
        meta.security_warnings = [wt.baseline_warning]
    try:
        result = await codex.run_codex_exec(
            prompts.build_delegate_prompt(task),
            cwd=wt.path,
            sandbox=sandbox,
            isolation=isolation,
            timeout_seconds=timeout_seconds,
            model=model,
        )
        _apply_run_meta(meta, result)
        if result.run.exit_code != 0 or result.run.binary_missing or result.run.timed_out:
            err = codex.classify_failure(
                result.run, last_message=result.last_message, events=result.events
            )
            return ErrorResult(error=err, meta=meta).model_dump(mode="json")
        diff = worktree.capture_diff(wt.path, timeout=git_timeout)
    except worktree.WorktreeError as exc:
        return ErrorResult(
            error=ErrorInfo(
                code="worktree_error",
                message=str(exc)[:300],
                repair="Retry; if it persists, inspect the repository state.",
            ),
            meta=meta,
        ).model_dump(mode="json")
    finally:
        worktree.remove(cwd, wt, timeout=git_timeout)

    meta.context_summary = _diffstat(diff)
    summary = (result.last_message or "").strip() or "(codex returned no summary)"
    if not diff.strip():
        summary = f"Codex made no changes. {summary}"
    return SuccessResult(
        tool="codex_delegate",
        summary=summary,
        verdict="unknown",
        diff=diff or None,
        raw_response=RawResponse(
            text=result.last_message, session_id=meta.session_id, model=meta.model
        ),
        next_steps=["Review the returned diff; apply it to your tree only if correct."],
        meta=meta,
    ).model_dump(mode="json")
