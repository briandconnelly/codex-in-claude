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
from typing import TYPE_CHECKING

from codex_in_claude._core.redaction import redact_text

if TYPE_CHECKING:
    from collections.abc import Callable

# mkdtemp prefix for the throwaway worktree's parent dir. Exposed so a job runner
# can constrain its cleanup to this temp area (see jobs.JobStore cleanup_prefix).
WORKTREE_PREFIX = "cic-worktree-"


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


@dataclass
class WorktreePlanData:
    """Read-only preview of the baseline a `create()` run would seed from. Gathered
    without creating a worktree, so counts are advisory: uncommitted tracked changes
    are reported but replay into the worktree is not validated here."""

    head_commit: str  # the HEAD commit the worktree is detached at
    head_subject: str | None  # short subject of HEAD, if readable
    tracked_files: int  # entries in the HEAD tree (blobs + submodule gitlinks)
    tracked_bytes: int  # approximate total size (blob sizes; gitlinks count as 0)
    uncommitted_tracked_files: int  # tracked files changed vs HEAD (would be replayed)
    untracked_files: int  # untracked files (never copied into the worktree)


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
        raise WorktreeError(
            f"git {' '.join(args)} failed: {(redact_text(proc.stderr.strip()) or '')[:200]}"
        )
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


def create(repo: str, *, timeout: int, on_parent: Callable[[str], None] | None = None) -> Worktree:
    """Create a worktree mirroring the live tree's tracked state.

    Raises NotAGitRepoError / NoCommitsError / WorktreeError. On success the
    worktree's HEAD equals the live tree's current tracked content (a baseline
    commit), so a later diff isolates only the agent's edits.

    ``on_parent`` is invoked with the temp parent dir the moment it exists — before
    any slow git work — so a caller can record it for cleanup even if the process is
    hard-killed mid-create."""
    _ensure_repo_with_head(repo, timeout)
    parent = tempfile.mkdtemp(prefix=WORKTREE_PREFIX)
    if on_parent is not None:
        try:
            on_parent(parent)
        except BaseException:
            # A failing hook (e.g. disk-full writing the manifest) must not leak the
            # temp dir it was meant to register for cleanup.
            shutil.rmtree(parent, ignore_errors=True)
            raise
    wt = str(Path(parent) / "tree")
    try:
        _git_ok(repo, ["worktree", "add", "--detach", "--quiet", wt, "HEAD"], timeout)
    except BaseException:
        # A git hang (TimeoutExpired) or spawn failure (OSError) is not a WorktreeError,
        # so catch broadly and match the sibling _seed_uncommitted block: best-effort
        # teardown of any partial registration + the temp parent, then re-raise. No leak.
        remove(repo, Worktree(path=wt, parent=parent), timeout=timeout)
        raise

    try:
        warning = _seed_uncommitted(repo, wt, timeout)
    except BaseException:
        # Any failure after creating the worktree (a raised WorktreeError, or an
        # unexpected error like a git subprocess timeout) must tear it down — so a
        # partial baseline can never be mistaken for a clean one and the temp dir
        # never leaks — then re-raise.
        remove(repo, Worktree(path=wt, parent=parent), timeout=timeout)
        raise
    return Worktree(path=wt, parent=parent, baseline_warning=warning)


def _count_nonempty_lines(proc: subprocess.CompletedProcess) -> int:
    """Count non-blank stdout lines, treating a failed git call as zero (the plan is
    advisory — a transient git hiccup must not break a free preview)."""
    if proc.returncode != 0:
        return 0
    return sum(1 for line in proc.stdout.splitlines() if line.strip())


def _tracked_files_and_bytes(repo: str, timeout: int) -> tuple[int, int]:
    """Count entries in the HEAD tree and sum blob sizes (approximate baseline size).
    `git ls-tree -r --long` emits `<mode> <type> <sha> <size>\\t<path>`; size is `-`
    for non-blob entries (e.g. submodule gitlinks), which are counted as files but
    contribute no bytes."""
    out = _git_ok(repo, ["ls-tree", "-r", "--long", "HEAD"], timeout)
    files = total = 0
    for line in out.splitlines():
        meta, sep, _path = line.partition("\t")
        fields = meta.split()
        if not sep or len(fields) < 4:  # a real entry always has a tab before its path
            continue
        files += 1
        size = fields[3]
        if size.isdigit():
            total += int(size)
    return files, total


def plan(repo: str, *, timeout: int) -> WorktreePlanData:
    """Preview the baseline a `create()` run would seed from — NO worktree created,
    no spend. Raises NotAGitRepoError / NoCommitsError / WorktreeError exactly like
    `create()`, so a dry run fails the same way the real propose run would. An
    infrastructure failure (git missing, a git subprocess timing out) is mapped to
    WorktreeError so the caller returns a structured error rather than crashing."""
    try:
        _ensure_repo_with_head(repo, timeout)
        head = _git_ok(repo, ["rev-parse", "HEAD"], timeout).strip()
        subj = _git(repo, ["log", "-1", "--format=%s"], timeout)
        head_subject = subj.stdout.strip() if subj.returncode == 0 and subj.stdout.strip() else None
        tracked_files, tracked_bytes = _tracked_files_and_bytes(repo, timeout)
        # `git diff --numstat HEAD` mirrors what _seed_uncommitted replays (staged +
        # unstaged tracked changes); each non-empty line is one changed file. Carry the
        # same --no-ext-diff/--no-textconv hardening the rest of this module uses: a free
        # preview must never run a repo-configured diff/textconv helper. (--numstat does
        # not invoke those helpers today, but the flags keep this defensive and uniform.)
        uncommitted = _count_nonempty_lines(
            _git(repo, ["diff", "--no-ext-diff", "--no-textconv", "--numstat", "HEAD"], timeout)
        )
        untracked = _count_nonempty_lines(
            _git(repo, ["ls-files", "--others", "--exclude-standard"], timeout)
        )
    except (NotAGitRepoError, NoCommitsError, WorktreeError):
        raise  # domain errors pass through unchanged
    except (subprocess.SubprocessError, OSError) as exc:
        # git binary missing (FileNotFoundError) or a subprocess timeout, etc.
        raise WorktreeError(
            f"git command failed during plan: {(redact_text(str(exc)) or '')[:200]}"
        ) from exc
    return WorktreePlanData(
        head_commit=head,
        head_subject=head_subject,
        tracked_files=tracked_files,
        tracked_bytes=tracked_bytes,
        uncommitted_tracked_files=uncommitted,
        untracked_files=untracked,
    )


def _seed_uncommitted(repo: str, wt: str, timeout: int) -> str | None:
    """Replay the live tree's uncommitted *tracked* changes into the worktree and
    commit them as a baseline. Untracked files are intentionally not copied.

    If the patch will not *apply*, that is best-effort: nothing was changed, so we
    leave the worktree at HEAD and return a warning. But once the patch HAS applied,
    the baseline commit must fully succeed — otherwise ``capture_diff`` would later
    report the caller's live changes as the agent's work. Any failure finalizing the
    baseline raises ``WorktreeError`` (the caller maps it to a zero-spend error)."""
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
    add = _git(wt, ["add", "-A"], timeout)
    if add.returncode != 0:
        raise WorktreeError(
            f"staging the baseline failed: {(redact_text(add.stderr.strip()) or '')[:200]}"
        )
    commit = _git(
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
    if commit.returncode != 0:
        raise WorktreeError(
            f"committing the baseline failed: {(redact_text(commit.stderr.strip()) or '')[:200]}"
        )
    # The baseline commit must leave the worktree clean; any residue means the live
    # changes were not fully captured and would leak into the agent's diff.
    status = _git(wt, ["status", "--porcelain=v1", "--untracked-files=all"], timeout)
    if status.returncode != 0 or status.stdout.strip():
        raise WorktreeError("baseline commit left the worktree dirty; aborting before spend")
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
    add = _git(wt, ["add", "-A", "--", *pathspec], timeout)
    if add.returncode != 0:
        raise WorktreeError(
            f"staging the worktree diff failed: {(redact_text(add.stderr.strip()) or '')[:200]}"
        )
    proc = _git(
        wt, ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--", *pathspec], timeout
    )
    if proc.returncode != 0:
        raise WorktreeError(
            f"capturing the worktree diff failed: {(redact_text(proc.stderr.strip()) or '')[:200]}"
        )
    return proc.stdout


def remove(repo: str, worktree: Worktree, *, timeout: int) -> None:
    """Tear down the worktree and its temp parent. Best-effort; never raises."""
    with contextlib.suppress(subprocess.SubprocessError, OSError):
        _git(repo, ["worktree", "remove", "--force", worktree.path], timeout)
    shutil.rmtree(worktree.parent, ignore_errors=True)
    with contextlib.suppress(subprocess.SubprocessError, OSError):
        _git(repo, ["worktree", "prune"], timeout)
