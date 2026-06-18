"""Gather a git diff for review. We run git ourselves so Codex gets exactly the
reviewed text (redacted, bounded) rather than reaching for files itself.

CLI-agnostic: timeout and byte budget are passed in by the caller so this module
stays free of project config. Scopes: working_tree | branch | commit."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

from codex_in_claude._core.redaction import redact

_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class InvalidScopeError(ValueError):
    """Unrecognized diff scope."""


class InvalidBaseError(ValueError):
    """Malformed/unsafe/unresolvable base ref for scope=branch."""


class InvalidCommitError(ValueError):
    """Malformed/unsafe/unresolvable commit for scope=commit."""


class InvalidPathsError(ValueError):
    """Malformed/unsafe git pathspec filter."""


class GitUnavailableError(RuntimeError):
    """git executable missing or unlaunchable."""


class NotAGitRepoError(RuntimeError):
    """The selected workspace is not a git working tree."""


@dataclass
class DiffSummary:
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class DiffResult:
    text: str
    summary: DiffSummary
    truncated: bool = False
    truncation_hint: str | None = None
    redacted_paths: list[str] = field(default_factory=list)
    diff_bytes: int = 0


def _valid_ref(ref: str) -> bool:
    return bool(ref) and not ref.startswith("-") and bool(_REF_RE.match(ref))


def normalize_paths(paths: list[str] | None) -> list[str] | None:
    """Validate path filters before they reach git argv."""
    if not paths:
        return None
    normalized: list[str] = []
    for path in paths:
        if path == "":
            raise InvalidPathsError("paths entries must not be empty")
        if path.startswith("-"):
            raise InvalidPathsError(f"path must not start with '-': {path!r}")
        if path.startswith(":"):
            raise InvalidPathsError(f"git pathspec magic is not supported: {path!r}")
        if "\\" in path:
            raise InvalidPathsError(f"path must use '/' separators: {path!r}")
        if path.startswith("/") or _WINDOWS_DRIVE_RE.match(path):
            raise InvalidPathsError(f"path must be repo-relative: {path!r}")
        if any(segment == ".." for segment in path.split("/")):
            raise InvalidPathsError(f"path must not contain '..' segments: {path!r}")
        normalized.append(path)
    return normalized


def _is_not_git_repo_error(stderr: str) -> bool:
    return "not a git repository" in stderr.lower()


def _git(cwd: str, args: list[str], timeout: int) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={"LC_ALL": "C", "LANG": "C", "PATH": _path()},
        )
    except FileNotFoundError as exc:
        raise GitUnavailableError("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {' '.join(args)} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        message = proc.stderr.strip() or "git failed"
        if _is_not_git_repo_error(message):
            raise NotAGitRepoError(message)
        raise RuntimeError(message)
    return proc.stdout


def _path() -> str:
    return os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin")


def _ref_exists(cwd: str, ref: str, timeout: int) -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={"LC_ALL": "C", "LANG": "C", "PATH": _path()},
        )
    except FileNotFoundError as exc:
        raise GitUnavailableError("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git rev-parse timed out after {timeout}s") from exc
    if proc.returncode != 0 and _is_not_git_repo_error(proc.stderr):
        raise NotAGitRepoError(proc.stderr.strip() or "not a git repository")
    return proc.returncode == 0


def _diff_args(scope: str, base: str | None, commit: str | None) -> list[str]:
    # --no-ext-diff + --no-textconv prevent configured external/textconv diff
    # drivers from executing commands during our own git call.
    common = ["diff", "--no-ext-diff", "--no-textconv"]
    if scope == "working_tree":
        return [*common, "--end-of-options", "HEAD"]
    if scope == "branch":
        if not base or not _valid_ref(base):
            raise InvalidBaseError(f"invalid base ref: {base!r}")
        return [*common, "--end-of-options", f"{base}...HEAD"]
    if scope == "commit":
        if not commit or not _valid_ref(commit):
            raise InvalidCommitError(f"invalid commit: {commit!r}")
        # `git show` (not diff) gives the commit's own change set and handles root
        # commits (which have no parent for a `^!`/`^..` form to resolve against).
        return ["show", "--format=", "--no-ext-diff", "--no-textconv", commit]
    raise InvalidScopeError(f"invalid scope: {scope}")


def _summary(cwd: str, diff_args: list[str], timeout: int) -> DiffSummary:
    summary_args = list(diff_args)
    summary_args.insert(1, "--numstat")
    numstat = _git(cwd, summary_args, timeout)
    files = added = removed = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            removed += int(parts[1])
    return DiffSummary(files_changed=files, lines_added=added, lines_removed=removed)


def gather_diff(
    cwd: str,
    scope: str,
    *,
    base: str | None = None,
    commit: str | None = None,
    paths: list[str] | None = None,
    timeout: int,
    max_bytes: int,
) -> DiffResult:
    """Gather, redact, and bound a diff for the given scope. Raises the typed
    errors above for invalid scope/base/commit/paths or git problems."""
    norm_paths = normalize_paths(paths)
    diff_args = _diff_args(scope, base, commit)
    if scope == "branch" and not _ref_exists(cwd, base or "", timeout):
        raise InvalidBaseError(f"base ref does not resolve to a commit: {base!r}")
    if scope == "commit" and not _ref_exists(cwd, commit or "", timeout):
        raise InvalidCommitError(f"commit does not resolve: {commit!r}")
    if norm_paths:
        diff_args = [*diff_args, "--", *norm_paths]
    summary = _summary(cwd, diff_args, timeout)
    raw = _git(cwd, diff_args, timeout)
    text, redacted = redact(raw)
    encoded = text.encode("utf-8", "replace")
    diff_bytes = len(encoded)
    truncated = False
    hint = None
    if diff_bytes > max_bytes:
        text = encoded[:max_bytes].decode("utf-8", "ignore")
        truncated = True
        hint = (
            f"diff exceeded {max_bytes} bytes; retry with paths=[...], a closer "
            "branch base, or a single commit"
        )
    return DiffResult(
        text=text,
        summary=summary,
        truncated=truncated,
        truncation_hint=hint,
        redacted_paths=redacted,
        diff_bytes=diff_bytes,
    )
