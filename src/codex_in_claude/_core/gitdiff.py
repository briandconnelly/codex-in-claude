"""Gather a git diff for review. We run git ourselves so Codex gets exactly the
reviewed text (redacted, bounded) rather than reaching for files itself.

CLI-agnostic: timeout and byte budget are passed in by the caller so this module
stays free of project config. Scopes: working_tree | branch | commit."""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from codex_in_claude._core import streamcap
from codex_in_claude._core.redaction import DiffRedactor

if TYPE_CHECKING:
    from typing import TextIO

_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# F1a: maximum bytes of git stderr retained in memory (keeps draining to avoid
# the >64 KB pipe-buffer deadlock while bounding how much we hold).
_STDERR_CAP = 64 * 1024

# F3: per-line memory ceiling for the diff stream reader — distinct from the
# display/store cap (max_bytes). Ensures lines up to 8 MiB (minified JS/CSS,
# etc.) are processed whole so diff_bytes stays exact and the redactor sees
# the full line before it decides what to store.
_MAX_DIFF_LINE_BYTES = 8 * 1024 * 1024


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
            # `-c core.quotepath=true` forces git's default path quoting regardless
            # of the user's config. git always C-quotes control characters (newlines,
            # tabs, etc.) no matter the quotepath setting; quotepath only governs
            # high-bit/non-ASCII bytes -- with quotepath=false git emits them raw
            # instead of octal-escaped, making the reviewed diff depend on the caller's
            # config. Forcing quotepath=true keeps path-header encoding deterministic.
            # encoding+surrogateescape so non-UTF-8 bytes git may emit or consume
            # (binary paths, symlink targets) round-trip instead of raising
            # UnicodeDecodeError/UnicodeEncodeError.
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


def _untracked_new_file_diff(
    cwd: str, norm_paths: list[str], timeout: int, acc: _BoundedDiffAccumulator
) -> tuple[int, int]:
    """Build new-file patches for the untracked files among ``norm_paths`` and feed
    them into ``acc``.

    Returns ``(files, added_lines)``. ``git ls-files --others --exclude-standard``
    enumerates untracked files under the named paths while skipping gitignored ones
    (matching `git add`'s default), so an explicitly-named new file is reviewed
    instead of silently producing an empty review (#74). Untracked files can never
    appear in ``git diff HEAD``, so there is no double-counting with the tracked diff.

    The patches are produced by ``git`` itself: each discovered path's content is
    hashed into a blob and recorded in a throwaway index (``GIT_INDEX_FILE``, never the
    repo's real index/working tree), which is then streamed through
    ``_stream_redacted_diff`` into ``acc`` — so the diff is never materialised whole
    in memory (F1b). Letting git format the patch — rather than hand-rolling it —
    gets correct handling of symlinks (``mode 120000``), binary files,
    control-character path quoting, and line counts (via ``--numstat``) for free.

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
        return 0, 0
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
        # F1b: stream through the bounded redactor instead of materialising the whole
        # patch as a string; extra_env carries GIT_INDEX_FILE / GIT_OBJECT_DIRECTORY.
        _stream_redacted_diff(cwd, diff_args, timeout, acc, extra_env=env)
        # numstat is one line per file (bounded, fine as a captured string).
        numstat = _git(cwd, [*diff_args, "--numstat"], timeout, extra_env=env)
    files = added = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        files += 1
        if parts[0].isdigit():  # "-" for binary; left out of the line tally
            added += int(parts[0])
    return files, added


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


class _BoundedDiffAccumulator:
    """Feed logical diff lines through an incremental redactor, storing only the
    first ``max_bytes`` of redacted output while counting the full redacted size so
    ``diff_bytes`` stays exact. Memory stays bounded regardless of diff size."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._redactor = DiffRedactor()
        self._head: list[str] = []
        # _stored tracks len("\n".join(self._head).encode("utf-8", "replace")) exactly,
        # including the joining newlines between lines, so text() is always <= max_bytes.
        self._stored = 0
        self._line_count = 0
        self._content_bytes = 0
        self.truncated = False

    @property
    def max_line_bytes(self) -> int:
        """Per-line byte cap passed to the stream reader.

        Two distinct caps:
        - ``_MAX_DIFF_LINE_BYTES``: a base per-line floor (8 MiB) — how much
          of a single line we buffer before processing (redacting + counting).
          Ensures a realistic long line (e.g. minified JS/CSS) is processed
          whole so ``diff_bytes`` stays exact and secrets at the boundary
          are fully seen by the redactor.
        - ``self._max_bytes``: display/store cap — how much redacted text is
          stored and returned. Lines that do not fit in ``text()`` are still
          counted in ``diff_bytes`` but dropped from the stored head.

        The effective per-line ceiling is ``max(_MAX_DIFF_LINE_BYTES, max_bytes)``
        — it SCALES UP with the operator-configured diff display budget
        (``CODEX_IN_CLAUDE_MAX_INPUT_BYTES``), not a fixed 8 MiB. This means:
        - A line up to this ceiling is processed whole (exact ``diff_bytes``,
          full redaction visibility). Transient peak allocation is bounded by
          the operator budget, not attacker-controlled input size.
        - A line exceeding the ceiling is truncated by the stream reader, making
          ``diff_bytes`` a lower bound for that line."""
        return max(_MAX_DIFF_LINE_BYTES, self._max_bytes)

    def feed(self, logical_line: str) -> None:
        for out in self._redactor.feed(logical_line):
            n = len(out.encode("utf-8", "replace"))
            self._content_bytes += n
            self._line_count += 1
            # sep accounts for the joining "\n" between stored lines.
            sep = 1 if self._head else 0
            if not self.truncated and self._stored + sep + n <= self._max_bytes:
                self._head.append(out)
                self._stored += sep + n
            else:
                self.truncated = True

    @property
    def redacted_paths(self) -> list[str]:
        return self._redactor.redacted

    @property
    def diff_bytes(self) -> int:
        # Mirrors len("\n".join(lines).encode()): content bytes + (N-1) newlines.
        return self._content_bytes + max(0, self._line_count - 1)

    def text(self) -> str:
        return "\n".join(self._head)


def _stream_redacted_diff(  # noqa: PLR0915
    cwd: str,
    args: list[str],
    timeout: int,
    acc: _BoundedDiffAccumulator,
    *,
    extra_env: dict[str, str] | None = None,
) -> None:
    """Run `git <args>` and feed its stdout, line by line, into `acc` — bounded in
    memory. Raises the same typed errors as `_git` on git failure/timeout."""
    env = {"LC_ALL": "C", "LANG": "C", "PATH": _path()}
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.Popen(
            ["git", "-c", "core.quotepath=true", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise GitUnavailableError("git executable not found") from exc
    deadline = time.monotonic() + timeout
    timed_out = threading.Event()
    stderr_buf: list[str] = []
    # Fix 2: guard kill and reap with a lock + flag so the Timer callback
    # cannot signal a reaped (potentially reused) PID.  A list is used instead
    # of nonlocal to match the surrounding style (e.g. _queued_bytes in runtime).
    _kill_lock = threading.Lock()
    _finished = [False]  # set by main thread before proc.wait(); callback no-ops after

    def _kill() -> None:
        with _kill_lock:
            if _finished[0]:
                return
            timed_out.set()
            # Fix 1: use proc.pid directly as the pgid.  Because proc was spawned
            # with start_new_session=True, it is its own process-group leader, so
            # pgid == proc.pid.  Critically, proc.pid is used instead of
            # os.getpgid(proc.pid) because on macOS getpgid raises ESRCH on a zombie,
            # whereas the process group is still live as long as any member (e.g. a
            # grandchild holding an inherited pipe) survives.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if hasattr(os, "killpg"):
                    os.killpg(proc.pid, signal.SIGKILL)
                else:  # pragma: no cover - non-POSIX fallback
                    proc.kill()

    def _drain_stderr() -> None:
        # F1a: keep draining to EOF (avoids the >64 KB pipe-buffer deadlock
        # the concurrent thread was added to prevent) while retaining at most
        # _STDERR_CAP bytes so large git diagnostics cannot OOM the server.
        if proc.stderr is not None:
            cap = streamcap.BoundedCapture(_STDERR_CAP)
            for line in streamcap.iter_bounded_lines(cast("TextIO", proc.stderr), _STDERR_CAP):
                cap.add(line)
            stderr_buf.append(cap.result())

    timer = threading.Timer(timeout, _kill)
    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    try:
        # proc.stdout is IO[Any] from Popen's generic; cast to TextIO for iter_bounded_lines.
        assert proc.stdout is not None
        timer.start()
        stderr_thread.start()
        for physical in streamcap.iter_bounded_lines(
            cast("TextIO", proc.stdout), acc.max_line_bytes
        ):
            for logical in physical.splitlines() or [""]:
                acc.feed(logical)
        # Drain finished (stdout EOF).  Disable the Timer's killer first so the
        # kill+reap below is main-thread-only (no killpg-after-reap race), then
        # bound the wait by the remaining deadline — git may have closed stdout
        # yet still be running (e.g. closed its fds but stays alive).
        with _kill_lock:
            _finished[0] = True
        timer.cancel()
        # Bound the process exit AND the stderr drain by the remaining deadline. git may
        # have closed stdout while still running, or a descendant may hold only stderr
        # open.  If either overruns, kill the group so _drain_stderr reaches EOF and the
        # timed_out flag is set for the error path below.
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        stderr_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if proc.poll() is None or stderr_thread.is_alive():
            timed_out.set()
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if hasattr(os, "killpg"):
                    os.killpg(proc.pid, signal.SIGKILL)
                else:  # pragma: no cover - non-POSIX fallback
                    proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
            stderr_thread.join(timeout=5)
    finally:
        timer.cancel()  # idempotent: cleans up on exception paths
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
    if timed_out.is_set():
        raise RuntimeError(f"git {' '.join(args)} timed out after {timeout}s")
    stderr = "".join(stderr_buf)
    if proc.returncode != 0:
        message = stderr.strip() or "git failed"
        if _is_not_git_repo_error(message):
            raise NotAGitRepoError(message)
        raise RuntimeError(message)


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
    acc = _BoundedDiffAccumulator(max_bytes)
    _stream_redacted_diff(cwd, diff_args, timeout, acc)
    if scope == "working_tree" and norm_paths:
        # `git diff HEAD` only sees tracked files; surface explicitly-named untracked
        # ones too so targeting a brand-new file doesn't yield a silent empty review (#74).
        # F1b: _untracked_new_file_diff now streams directly into acc rather than
        # returning the whole patch as a string.
        u_files, u_added = _untracked_new_file_diff(cwd, norm_paths, timeout, acc)
        summary.files_changed += u_files
        summary.lines_added += u_added
    diff_bytes = acc.diff_bytes
    truncated = acc.truncated
    hint = None
    if truncated:
        hint = (
            f"diff exceeded {max_bytes} bytes; retry with paths=[...], a closer "
            "branch base, or a single commit"
        )
    return DiffResult(
        text=acc.text(),
        summary=summary,
        truncated=truncated,
        truncation_hint=hint,
        redacted_paths=acc.redacted_paths,
        diff_bytes=diff_bytes,
    )
