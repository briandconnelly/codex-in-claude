"""Gather a git diff for review. We run git ourselves so Codex gets exactly the
reviewed text (redacted, bounded) rather than reaching for files itself.

CLI-agnostic: timeout and byte budget are passed in by the caller so this module
stays free of project config. Scopes: working_tree | branch | commit."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

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


def _git(
    cwd: str,
    args: list[str],
    timeout: int,
    extra_env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> str:
    env = {"LC_ALL": "C", "LANG": "C", "PATH": _path()}
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            # `-c core.quotepath=true` forces C-quoting of control-character paths
            # regardless of the user's config; with quotepath=false git would emit
            # raw newlines in path headers, letting a crafted filename forge a
            # second `diff --git` entry. encoding+surrogateescape so non-UTF-8 bytes
            # git may emit or consume (binary paths, symlink targets) round-trip
            # instead of raising UnicodeDecodeError/UnicodeEncodeError.
            ["git", "-c", "core.quotepath=true", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=timeout,
            check=False,
            env=env,
            input=stdin,
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
            encoding="utf-8",
            errors="surrogateescape",
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


# Git's well-known empty-tree object; diffing a temp index against it yields exactly
# the index's entries as `new file` patches.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _untracked_new_file_diff(cwd: str, norm_paths: list[str], timeout: int) -> tuple[str, int, int]:
    """Build new-file patches for the untracked files among ``norm_paths``.

    Returns ``(patch_text, files, added_lines)``. ``git ls-files --others
    --exclude-standard`` enumerates untracked files under the named paths while
    skipping gitignored ones (matching `git add`'s default), so an explicitly-named
    new file is reviewed instead of silently producing an empty review (#74).
    Untracked files can never appear in ``git diff HEAD``, so there is no
    double-counting with the tracked diff.

    The patches are produced by ``git`` itself: each discovered path's content is
    hashed into a blob and recorded in a throwaway index (``GIT_INDEX_FILE``, never the
    repo's real index/working tree), which is then diffed against the empty tree.
    Letting git format the patch — rather than hand-rolling it — gets correct handling
    of symlinks (``mode 120000``), binary files, control-character path quoting, and
    line counts (via ``--numstat``) for free.

    Blobs are created with ``hash-object --no-filters`` and entries with
    ``update-index --cacheinfo`` (not ``git add``) so configured gitattributes clean
    filters and EOL normalization never run: gathering stays side-effect-free of repo
    config and the reviewer sees the raw working-tree bytes, matching the deliberate
    ``--no-ext-diff``/``--no-textconv`` posture elsewhere here.

    Object writes are redirected to a temp object dir (``GIT_OBJECT_DIRECTORY``), with
    the repo's real objects as a read-only alternate, so the raw (pre-redaction) bytes
    of an untracked secret never persist as a blob in the repo's own ``.git/objects``.
    The temp index and objects are discarded with the tempdir, leaving no trace."""
    listing = _git(
        cwd, ["ls-files", "--others", "--exclude-standard", "-z", "--", *norm_paths], timeout
    )
    paths = [p for p in listing.split("\0") if p]
    if not paths:
        return "", 0, 0
    real_objects = _git(
        cwd, ["rev-parse", "--path-format=absolute", "--git-path", "objects"], timeout
    ).strip()
    with tempfile.TemporaryDirectory() as tmp:
        objects = Path(tmp) / "objects"
        objects.mkdir()
        env = {
            "GIT_INDEX_FILE": str(Path(tmp) / "index"),
            "GIT_OBJECT_DIRECTORY": str(objects),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": real_objects,
        }
        for path in paths:
            full = Path(cwd) / path
            if full.is_symlink():
                # Hash the link target text, not the dereferenced file, as a 120000 blob.
                mode = "120000"
                target = os.readlink(full)  # noqa: PTH115 — raw target, not a normalized Path
                obj_args = ["hash-object", "-w", "--stdin"]
                blob = _git(cwd, obj_args, timeout, extra_env=env, stdin=target)
            else:
                mode = "100755" if full.stat().st_mode & 0o111 else "100644"
                hash_args = ["hash-object", "--no-filters", "-w", "--", path]
                blob = _git(cwd, hash_args, timeout, extra_env=env)
            cacheinfo = f"{mode},{blob.strip()},{path}"
            _git(cwd, ["update-index", "--add", "--cacheinfo", cacheinfo], timeout, extra_env=env)
        diff_args = ["diff", "--no-ext-diff", "--no-textconv", "--cached", _EMPTY_TREE]
        text = _git(cwd, diff_args, timeout, extra_env=env)
        numstat = _git(cwd, [*diff_args, "--numstat"], timeout, extra_env=env)
    files = added = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        files += 1
        if parts[0].isdigit():  # "-" for binary; left out of the line tally
            added += int(parts[0])
    return text, files, added


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
    if scope == "working_tree" and norm_paths:
        # `git diff HEAD` only sees tracked files; surface explicitly-named untracked
        # ones too so targeting a brand-new file doesn't yield a silent empty review (#74).
        untracked, u_files, u_added = _untracked_new_file_diff(cwd, norm_paths, timeout)
        if untracked:
            raw = f"{raw}{untracked}" if raw else untracked
            summary.files_changed += u_files
            summary.lines_added += u_added
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
