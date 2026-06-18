"""Run an agent's writes inside a throwaway git worktree, then capture the diff.

This is the engine of the `propose` tier: the agent edits files in an isolated
worktree (never the live tree), and we return the resulting patch for review. The
worktree mirrors the live tree's *tracked* state (HEAD + uncommitted tracked
changes as a baseline commit) so the agent builds on current code; the returned
diff is exactly the agent's changes on top of that baseline. CLI-agnostic."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    """Creating, seeding, or removing the worktree failed."""


class NotAGitRepoError(RuntimeError):
    """The workspace is not a git repository (propose requires one)."""


class NoCommitsError(RuntimeError):
    """The repository has no commits to base a worktree on."""


@dataclass
class Worktree:
    path: str  # where the agent runs (the worktree working dir)
    parent: str  # temp dir holding it (removed on teardown)
    baseline_warning: str | None = None  # set when uncommitted changes could not be seeded


def _git(repo: str, args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={"LC_ALL": "C", "LANG": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )


def _git_ok(repo: str, args: list[str], timeout: int) -> str:
    proc = _git(repo, args, timeout)
    if proc.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def _ensure_repo_with_head(repo: str, timeout: int) -> None:
    inside = _git(repo, ["rev-parse", "--is-inside-work-tree"], timeout)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise NotAGitRepoError("workspace is not a git repository")
    head = _git(repo, ["rev-parse", "--verify", "--quiet", "HEAD"], timeout)
    if head.returncode != 0:
        raise NoCommitsError("repository has no commits to base a worktree on")


def ensure_repo_with_head(repo: str, *, timeout: int) -> None:
    """Public guard: raise NotAGitRepoError / NoCommitsError / WorktreeError if
    ``repo`` is not a git repo with at least one commit. Used to fail an async
    delegate fast, before a background job is started."""
    _ensure_repo_with_head(repo, timeout)


def create(repo: str, *, timeout: int) -> Worktree:
    """Create a worktree mirroring the live tree's tracked state.

    Raises NotAGitRepoError / NoCommitsError / WorktreeError. On success the
    worktree's HEAD equals the live tree's current tracked content (a baseline
    commit), so a later diff isolates only the agent's edits."""
    _ensure_repo_with_head(repo, timeout)
    parent = tempfile.mkdtemp(prefix="cic-worktree-")
    wt = str(Path(parent) / "tree")
    try:
        _git_ok(repo, ["worktree", "add", "--detach", "--quiet", wt, "HEAD"], timeout)
    except WorktreeError:
        shutil.rmtree(parent, ignore_errors=True)
        raise

    warning = _seed_uncommitted(repo, wt, timeout)
    return Worktree(path=wt, parent=parent, baseline_warning=warning)


def _seed_uncommitted(repo: str, wt: str, timeout: int) -> str | None:
    """Replay the live tree's uncommitted *tracked* changes into the worktree and
    commit them as a baseline. Best-effort: if the patch will not apply, leave the
    worktree at HEAD and return a warning instead of failing the whole run.
    Untracked files are intentionally not copied."""
    diff = _git(repo, ["diff", "--no-ext-diff", "--no-textconv", "HEAD"], timeout)
    if diff.returncode != 0:
        return "could not read live uncommitted changes; worktree based on HEAD only"
    if not diff.stdout.strip():
        return None  # clean tree; HEAD is already the live state
    apply = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=wt,
        input=diff.stdout,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={"LC_ALL": "C", "LANG": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    if apply.returncode != 0:
        return "uncommitted changes could not be replayed; worktree based on HEAD only"
    _git(wt, ["add", "-A"], timeout)
    _git(
        wt,
        [
            "-c",
            "user.email=codex-in-claude@local",
            "-c",
            "user.name=codex-in-claude",
            "commit",
            "--quiet",
            "--no-verify",
            "-m",
            "baseline: live uncommitted state",
        ],
        timeout,
    )
    return None


# Build/cache artifacts an agent may create by running code — excluded from the
# captured diff so the proposed patch is just the meaningful source changes.
_ARTIFACT_EXCLUDES = (
    ":(exclude,glob)**/__pycache__/**",
    ":(exclude,glob)**/*.py[co]",
    ":(exclude,glob)**/.pytest_cache/**",
    ":(exclude,glob)**/.ruff_cache/**",
    ":(exclude,glob)**/.mypy_cache/**",
    ":(exclude,glob)**/.DS_Store",
    ":(exclude,glob)**/node_modules/**",
    ":(exclude,glob)**/*.egg-info/**",
)


def capture_diff(wt: str, *, timeout: int) -> str:
    """Stage the agent's changes and return the patch vs the baseline.

    Staging first means new and deleted files appear in the diff, so the returned
    patch is a complete, git-appliable representation of the agent's work. Common
    build artifacts (``__pycache__``, ``.pyc``, caches) are excluded so the patch
    holds only meaningful source changes."""
    pathspec = [".", *_ARTIFACT_EXCLUDES]
    _git(wt, ["add", "-A", "--", *pathspec], timeout)
    proc = _git(
        wt, ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--", *pathspec], timeout
    )
    if proc.returncode != 0:
        raise WorktreeError(f"capturing the worktree diff failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def remove(repo: str, worktree: Worktree, *, timeout: int) -> None:
    """Tear down the worktree and its temp parent. Best-effort; never raises."""
    with contextlib.suppress(subprocess.SubprocessError, OSError):
        _git(repo, ["worktree", "remove", "--force", worktree.path], timeout)
    shutil.rmtree(worktree.parent, ignore_errors=True)
    with contextlib.suppress(subprocess.SubprocessError, OSError):
        _git(repo, ["worktree", "prune"], timeout)
