"""Run an agent's writes inside a throwaway git worktree, then capture the diff.

This is the engine of the `propose` tier: the agent edits files in an isolated
worktree (never the live tree), and we return the resulting patch for review. The
worktree mirrors the live tree's *tracked* state (HEAD + uncommitted tracked
changes as a baseline commit) so the agent builds on current code; the returned
diff is exactly the agent's changes on top of that baseline. CLI-agnostic.

Repo-config isolation: these porcelain git ops run in the *server process*, not in
Codex's sandbox, so every invocation is prefixed with ``_hardening_flags`` (see
there), which disables repo-configured hooks, fsmonitor, and every gitattributes
``clean``/``smudge``/``process`` filter driver; the baseline commit also passes
``--no-gpg-sign``. That closes the repo-controlled code-execution surface across the
worktree lifecycle (checkout, staging, and working-tree diffs)."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from codex_in_claude._core import gitdiff, gitproc
from codex_in_claude._core.redaction import redact_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

# mkdtemp prefix for the throwaway worktree's parent dir. Exposed so a job runner
# can constrain its cleanup to this temp area (see jobs.JobStore cleanup_prefix).
WORKTREE_PREFIX = "cic-worktree-"

# Per-line cap for plan()'s streamed inventory counts (ls-tree / diff --numstat). Each
# line's counted/summed fields precede the pathname, so even a pathologically long path
# truncated at this cap still parses correctly; the cap only bounds peak memory (#326).
_PLAN_LINE_CAP = 1024 * 1024


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


@functools.lru_cache(maxsize=1)
def _empty_hooks_dir() -> str:
    """An empty directory used as ``core.hooksPath`` so no repo-configured git hook
    (``post-checkout`` on ``worktree add``, ``post-commit`` on the baseline commit,
    etc.) executes during worktree operations. Created once per process and left for
    the OS to reap — it holds nothing sensitive — and deliberately placed *outside*
    any worktree so the sandboxed agent cannot drop a hook file into it."""
    return tempfile.mkdtemp(prefix="cic-nohooks-")


# A configured filter driver's name (the ``<name>`` in ``[filter "<name>"]``) is
# neutralized by emitting ``-c`` overrides for it. Those overrides are ``key=value``
# argv tokens split on the FIRST ``=``, so a name containing ``=`` (or a control char
# that cannot round-trip) would corrupt the override and leave the driver ACTIVE. We
# refuse to run in that case rather than silently fail to neutralize (fail closed). The
# rejected set is ``=`` plus every ASCII control character (C0 ``0x00-0x1f`` and DEL
# ``0x7f``).
_UNNEUTRALIZABLE_DRIVER_CHARS = re.compile(r"[=\x00-\x1f\x7f]")

# ``git config --name-only --get-regexp ^filter\.`` emits one key per line; the driver
# name is everything between the ``filter.`` prefix and the trailing ``.<var>``. The
# name may itself contain dots (a multi-level subsection), so match greedily; it may also
# be EMPTY -- ``[filter ""]`` enumerates as ``filter..smudge`` and is selectable from a
# committed ``.gitattributes`` via ``path filter=`` -- so use ``*`` not ``+`` (a ``+``
# would skip that key and leave the driver ACTIVE). ``*`` still does not match the
# non-driver key ``filter.smudge`` (a single dot), which has no ``.<var>`` suffix.
_FILTER_KEY_RE = re.compile(r"^filter\.(?P<name>.*)\.(?:smudge|clean|process|required)$")


def _base_hardening_flags() -> list[str]:
    """The repo-config-independent ``-c`` overrides: ``core.hooksPath`` -> an empty dir
    (disables every repo hook, including ``post-checkout`` on ``worktree add`` and
    ``post-commit`` on the baseline commit, which ``--no-verify`` does not suppress) and
    ``core.fsmonitor=false`` (no fsmonitor program)."""
    return ["-c", f"core.hooksPath={_empty_hooks_dir()}", "-c", "core.fsmonitor=false"]


def _configured_filter_drivers(
    repo: str, timeout: int, *, aliases: Iterable[str] = ()
) -> list[str]:
    """Every gitattributes filter driver name configured for ``repo`` -- from system and
    repo-local config, but NOT the user's global ``~/.gitconfig``. This runs under
    ``_base_env()``, which every other git call here also uses; because that is a
    *complete replacement* environment with no ``HOME``, git cannot locate the global
    config file, so global drivers are read by neither the enumeration nor the ops it
    protects. What we enumerate is therefore exactly the driver set those ops would run.

    Read with a raw subprocess carrying only ``_base_hardening_flags`` -- NOT ``_git``,
    which would recurse back through ``_hardening_flags`` -> here. Raises WorktreeError
    if enumeration fails, or if a driver name cannot be safely expressed as a ``-c``
    override (fail closed; see ``_UNNEUTRALIZABLE_DRIVER_CHARS``).

    ``aliases`` (default ``()``, byte-identical to before) sanitizes the enumeration-failure
    message through ``sanitize_prose`` when ``repo`` is a throwaway worktree -- this is NOT
    pre-worktree-only: `_seed_uncommitted` and `capture_diff` both call ``_hardening_flags``
    (hence here) against ``wt`` (#420)."""
    proc = subprocess.run(
        ["git", *_base_hardening_flags(), "config", "--name-only", "--get-regexp", r"^filter\."],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_base_env(),
    )
    # returncode 1 is git's "no matching keys" (no filters configured), not an error.
    if proc.returncode not in (0, 1):
        aliases = tuple(aliases)
        if aliases:
            raw = f"enumerating filter drivers failed: {proc.stderr.strip()}"
            message = (sanitize_prose(raw, aliases) or "")[:200]
        else:
            message = (
                f"enumerating filter drivers failed: "
                f"{(redact_text(proc.stderr.strip()) or '')[:200]}"
            )
        raise WorktreeError(message)
    names: list[str] = []
    seen: set[str] = set()
    for line in proc.stdout.splitlines():
        match = _FILTER_KEY_RE.match(line.strip())
        if match is None:
            continue
        name = match.group("name")
        if name in seen:
            continue
        seen.add(name)
        if _UNNEUTRALIZABLE_DRIVER_CHARS.search(name):
            # Cap the echoed name (like the [:200] git-stderr truncation elsewhere) so a
            # pathologically long driver name can't bloat the client-visible envelope.
            raise WorktreeError(
                f"refusing to run: gitattributes filter driver {name[:100]!r} cannot be safely "
                "neutralized (its name contains '=' or a control character)"
            )
        names.append(name)
    return names


def _filter_neutralization_flags(
    repo: str, timeout: int, *, aliases: Iterable[str] = ()
) -> list[str]:
    """``-c`` overrides that disable every configured gitattributes filter driver so no
    ``clean``/``smudge``/``process`` command executes. For each driver we blank the three
    command hooks (an empty command is a no-op, leaving git to use the raw blob bytes)
    and force ``required=false`` so a now-disabled ``required`` filter is non-fatal
    instead of aborting checkout. ``process`` must be blanked explicitly: it takes
    precedence over ``smudge``/``clean``, so overriding only those would still run it.

    ``aliases`` is forwarded to ``_configured_filter_drivers`` -- see there (#420)."""
    flags: list[str] = []
    for name in _configured_filter_drivers(repo, timeout, aliases=aliases):
        flags += [
            "-c",
            f"filter.{name}.process=",
            "-c",
            f"filter.{name}.smudge=",
            "-c",
            f"filter.{name}.clean=",
            "-c",
            f"filter.{name}.required=false",
        ]
    return flags


def _hardening_flags(repo: str, timeout: int, *, aliases: Iterable[str] = ()) -> list[str]:
    """``git -c`` overrides prepended to every git call here, to neutralize
    repo-configured code execution in the *server process* (these git ops run here, not
    in Codex's sandbox): repo hooks and fsmonitor (``_base_hardening_flags``) plus every
    configured gitattributes filter driver (``_filter_neutralization_flags``), which git
    would otherwise run during checkout (``worktree add``), staging (``git add -A`` in
    seeding/capture), and working-tree diffs (``git diff HEAD``). The baseline commit
    additionally passes ``--no-gpg-sign`` to keep a configured signing program from
    running.

    Delivered as command-line ``-c`` (not ``GIT_CONFIG_*`` env, which git honors only
    since 2.31 and would fail *open* on an older binary) at the highest config
    precedence, so it overrides the repo's own local config and reaches even the
    standalone ``git apply``. The filter set is enumerated fresh per call (uncached) so a
    driver added between operations in a long-lived server process is never missed.

    ``aliases`` is forwarded to ``_filter_neutralization_flags`` -- pass ``path_aliases(wt)``
    whenever ``repo`` IS the throwaway worktree, so a filter-enumeration failure it triggers
    is sanitized rather than naming a path that is dead by the time the caller reads it
    (#420). Leave it default when ``repo`` is the source repo (structurally worktree-path-free)."""
    return [
        *_base_hardening_flags(),
        *_filter_neutralization_flags(repo, timeout, aliases=aliases),
    ]


def _base_env() -> dict[str, str]:
    """Locale/PATH pinning shared by every git subprocess (deterministic output, no
    inherited locale surprises). Delegates to :func:`gitdiff._base_git_env` so the
    stripped-env construction lives in exactly one place across `_core` (#330)."""
    return gitdiff._base_git_env()


def _git(
    repo: str, args: list[str], timeout: int, *, aliases: Iterable[str] = ()
) -> subprocess.CompletedProcess:
    """``aliases`` is forwarded to ``_hardening_flags`` -- pass ``path_aliases(wt)`` when
    ``repo`` is the throwaway worktree (#420); see there."""
    return subprocess.run(
        ["git", *_hardening_flags(repo, timeout, aliases=aliases), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_base_env(),
    )


def _git_ok(repo: str, args: list[str], timeout: int, *, aliases: Iterable[str] = ()) -> str:
    """Run a git command in ``repo``, raising WorktreeError with the (redacted) stderr on
    failure. The message interpolates argv verbatim, so a worktree path can appear there
    even with empty stderr; ``aliases`` (default ``()``, byte-identical to before) sanitizes
    the WHOLE raw message -- argv and stderr together -- through ``sanitize_prose`` when the
    caller knows ``args`` can name a worktree (#420). ``_git``'s own internal hardening-flags
    call is NOT threaded with these aliases: that call operates on ``repo``, decoupled from
    whatever path the caller's ``args`` happen to reference."""
    proc = _git(repo, args, timeout)
    if proc.returncode != 0:
        aliases = tuple(aliases)
        if aliases:
            raw = f"git {' '.join(args)} failed: {proc.stderr.strip()}"
            message = (sanitize_prose(raw, aliases) or "")[:200]
        else:
            detail = (redact_text(proc.stderr.strip()) or "")[:200]
            message = f"git {' '.join(args)} failed: {detail}"
        raise WorktreeError(message)
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
        # argv names `wt` (the destination) even though this call runs against `repo` (the
        # source), so the failure message needs `wt`'s aliases -- not `_hardening_flags`'s
        # own repo-scoped enumeration, which stays default (#420). `path_aliases(wt)` here
        # runs BEFORE `wt` itself exists (only `parent`, from `mkdtemp` above, does) --
        # safe despite `path_aliases`'s own "capture while it still exists" docstring
        # caveat, because that caveat is about symlinked ANCESTORS (macOS's /tmp ->
        # /private/tmp): `os.path.realpath` resolves those against `parent`, which already
        # exists, and the leaf `wt` adds is a plain directory `git worktree add` creates
        # fresh -- never itself a symlink -- so its absence at alias-computation time
        # changes nothing `realpath` needs to resolve.
        _git_ok(
            repo,
            ["worktree", "add", "--detach", "--quiet", wt, "HEAD"],
            timeout,
            aliases=path_aliases(wt),
        )
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


def _count_uncommitted(repo: str, timeout: int) -> int:
    """Count tracked files changed vs HEAD (staged + unstaged) — the changes
    `_seed_uncommitted` would replay — by streaming `git diff --numstat HEAD` so a repo
    with a pathological number of changed files is counted in bounded memory (#326),
    matching the untracked count. Each non-empty line is one changed file.

    Carries the `--no-ext-diff`/`--no-textconv` hardening the rest of this module uses: a
    free preview must never run a repo-configured diff/textconv helper. (`--numstat` does
    not invoke those helpers today, but the flags keep this defensive and uniform.)

    Fail-soft on a non-zero git exit — returns 0, since a transient git hiccup must not
    break a free preview (the pre-#326 `_count_nonempty_lines` behavior). A timeout or
    missing git binary is an infrastructure fault surfaced as WorktreeError, preserving
    plan()'s contract."""
    cmd = [
        "git",
        *_hardening_flags(repo, timeout),
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--numstat",
        "HEAD",
    ]
    try:
        return gitproc.run_lines(
            cmd,
            cwd=repo,
            env=_base_env(),
            timeout=timeout,
            max_line_bytes=_PLAN_LINE_CAP,
            consume=lambda lines: sum(1 for line in lines if line.strip()),
        )
    except gitproc.GitStreamFailed:
        return 0
    except (gitproc.GitStreamTimeout, gitproc.GitBinaryNotFound) as exc:
        raise WorktreeError(
            f"counting uncommitted files failed: {(redact_text(str(exc).strip()) or '')[:200]}"
        ) from exc


def _count_untracked(repo: str, timeout: int) -> int:
    """Count untracked, non-ignored files for the advisory plan preview via the shared
    inventory primitive both dry-run tools use (``gitdiff.count_untracked``): one
    NUL-delimited, memory-bounded, fsmonitor-hardened implementation, so
    ``codex_delegate_dry_run`` and ``codex_review_changes``/``codex_dry_run`` can't drift
    (#323).

    ``count_untracked`` is fail-loud — it raises ``RuntimeError`` (and its
    ``GitUnavailableError``/``NotAGitRepoError`` subclasses) on a git failure or timeout.
    By the time ``plan()`` reaches the untracked count the repo has already passed
    ``_ensure_repo_with_head`` and several git calls, so any failure here is an
    infrastructure fault; translate it to ``WorktreeError`` to preserve ``plan()``'s
    documented contract (git missing / a subprocess timeout -> ``WorktreeError``, never a
    crash and never a falsely-authoritative ``0``)."""
    try:
        return gitdiff.count_untracked(repo, None, timeout)
    except RuntimeError as exc:
        raise WorktreeError(
            f"counting untracked files failed: {(redact_text(str(exc).strip()) or '')[:200]}"
        ) from exc


def _parse_tracked(lines: Iterable[str]) -> tuple[int, int]:
    """Count entries and sum blob sizes over `git ls-tree -r --long` output lines. Each
    entry is `<mode> <type> <sha> <size>\\t<path>`; size is `-` for non-blob entries (e.g.
    submodule gitlinks), which are counted as files but contribute no bytes. A line with
    no tab (or fewer than four leading fields) is skipped. The counted/summed fields all
    precede the tab, so a pathname truncated by the streaming line cap still parses."""
    files = total = 0
    for line in lines:
        meta, sep, _path = line.partition("\t")
        fields = meta.split()
        if not sep or len(fields) < 4:  # a real entry always has a tab before its path
            continue
        files += 1
        size = fields[3]
        if size.isdigit():
            total += int(size)
    return files, total


def _tracked_files_and_bytes(repo: str, timeout: int) -> tuple[int, int]:
    """Count entries in the HEAD tree and sum blob sizes (approximate baseline size) by
    streaming `git ls-tree -r --long HEAD` so a repo with a pathological number of tracked
    entries is counted in bounded memory (#326), matching the untracked count. See
    `_parse_tracked` for the line format. A git failure (non-zero exit, timeout, or a
    missing binary) surfaces as WorktreeError, preserving plan()'s contract."""
    cmd = ["git", *_hardening_flags(repo, timeout), "ls-tree", "-r", "--long", "HEAD"]
    try:
        return gitproc.run_lines(
            cmd,
            cwd=repo,
            env=_base_env(),
            timeout=timeout,
            max_line_bytes=_PLAN_LINE_CAP,
            consume=_parse_tracked,
        )
    except (gitproc.GitStreamFailed, gitproc.GitStreamTimeout, gitproc.GitBinaryNotFound) as exc:
        raise WorktreeError(
            f"counting tracked files failed: {(redact_text(str(exc).strip()) or '')[:200]}"
        ) from exc


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
        uncommitted = _count_uncommitted(repo, timeout)
        untracked = _count_untracked(repo, timeout)
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
    baseline raises ``WorktreeError`` (the caller maps it to a zero-spend error). Every
    git call against ``wt`` below carries ``wt``'s aliases, so any WorktreeError it raises
    (a staging/commit failure, or a filter-enumeration failure surfaced through
    ``_hardening_flags`` -- round-3 review finding, #420) is sanitized rather than naming
    the worktree, which is dead by the time the caller reads the result."""
    aliases = path_aliases(wt)
    diff = _git(repo, ["diff", "--no-ext-diff", "--no-textconv", "HEAD"], timeout)
    if diff.returncode != 0:
        return "could not read live uncommitted changes; worktree based on HEAD only"
    if not diff.stdout.strip():
        return None  # clean tree; HEAD is already the live state
    apply = subprocess.run(
        [
            "git",
            *_hardening_flags(wt, timeout, aliases=aliases),
            "apply",
            "--whitespace=nowarn",
            "-",
        ],
        cwd=wt,
        input=diff.stdout,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_base_env(),
    )
    if apply.returncode != 0:
        return "uncommitted changes could not be replayed; worktree based on HEAD only"
    add = _git(wt, ["add", "-A"], timeout, aliases=aliases)
    if add.returncode != 0:
        raw = f"staging the baseline failed: {add.stderr.strip()}"
        raise WorktreeError((sanitize_prose(raw, aliases) or "")[:200])
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
            "--no-gpg-sign",
            "-m",
            "baseline: live uncommitted state",
        ],
        timeout,
        aliases=aliases,
    )
    if commit.returncode != 0:
        raw = f"committing the baseline failed: {commit.stderr.strip()}"
        raise WorktreeError((sanitize_prose(raw, aliases) or "")[:200])
    # The baseline commit must leave the worktree clean; any residue means the live
    # changes were not fully captured and would leak into the agent's diff.
    status = _git(
        wt, ["status", "--porcelain=v1", "--untracked-files=all"], timeout, aliases=aliases
    )
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
    holds only meaningful source changes. Every git call carries ``wt``'s aliases, so a
    staging/diff failure -- or a filter-enumeration failure surfaced through
    ``_hardening_flags`` -- round-3 review finding, #420 -- is sanitized rather than naming
    the worktree, which is dead by the time the caller reads the result."""
    aliases = path_aliases(wt)
    pathspec = [".", *_ARTIFACT_EXCLUDES]
    add = _git(wt, ["add", "-A", "--", *pathspec], timeout, aliases=aliases)
    if add.returncode != 0:
        raw = f"staging the worktree diff failed: {add.stderr.strip()}"
        raise WorktreeError((sanitize_prose(raw, aliases) or "")[:200])
    proc = _git(
        wt,
        ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--", *pathspec],
        timeout,
        aliases=aliases,
    )
    if proc.returncode != 0:
        raw = f"capturing the worktree diff failed: {proc.stderr.strip()}"
        raise WorktreeError((sanitize_prose(raw, aliases) or "")[:200])
    return proc.stdout


# An alias is rewritten only where prose punctuation (or a string edge) brackets it, so it
# is the WHOLE leading portion of a path and never a fragment of a longer name. This is an
# ALLOWLIST of delimiters, deliberately not a denylist of "path characters": a POSIX
# component may contain nearly any byte, so `<root>+suffix`, `<root>@v2` and `/pré<root>`
# name DIFFERENT paths, and a denylist that forgot `+`/`@`/`%`/non-ASCII silently rewrote
# them into the wrong file. Erring toward a missed rewrite is safe; erring toward a wrong
# one points the caller at the wrong content.
#
# `/` is asymmetric on purpose. On the RIGHT it is what separates the root from the path we
# want to keep (`<root>/src/f.py`), so it must be allowed — an earlier draft forbade it on
# both sides and was a silent no-op on every real case. On the LEFT it would mean the alias
# is the tail of a longer path (`/other<root>/f.py`), a different file, so it is excluded.
#
# `.` is absent from the right-hand set deliberately: allowing it would rewrite a
# sentence-final `<root>.` to `..`, the parent directory — more misleading than the dead
# path it replaced. See `relativize`'s documented limitation.
_LEFT_DELIMS = r"\s(\[{`\"'<=,;:|"
_RIGHT_DELIMS = r"/\s)\]}`\"'<>,;:!?*|"


def path_aliases(path: str) -> tuple[str, ...]:
    """Every textual spelling of ``path`` an agent's prose might use, longest first.

    Covers the symlinked-ancestor case (macOS resolves ``/tmp`` -> ``/private/tmp`` and
    ``/var/folders`` -> ``/private/var/folders``, so the path ``mkdtemp`` returned and the
    one the agent reports can differ) and the ``file://`` URI spelling of each. Sorted
    longest-first so a containing alias is always tried before an alias it contains.

    Capture these while the worktree still EXISTS. ``realpath`` happens to resolve a
    deleted path correctly today — only the surviving ancestors carry the symlinks — but
    relying on that would make a rename of this call site silently degrade the alias set.

    Raises ``ValueError`` unless ``path`` is absolute, free of surrounding whitespace, and
    below the filesystem root. Each rejection is a programming error a ``_core`` caller
    would rather hear loudly than have silently reinterpreted:

    - blank resolves to the process CWD and yields a bare ``file://`` alias that would
      rewrite any unrelated URI (``file:///etc/passwd`` -> ``./etc/passwd``);
    - surrounding whitespace cannot be trimmed away, because trimming would ACCEPT the
      relative ``"\\n/tmp/tree"`` and would silently retarget the legal absolute
      ``"/tmp/tree\\n"`` (a different file) onto ``/tmp/tree``;
    - ``/`` is never a worktree, and its aliases cannot rewrite anything anyway (nothing
      follows the root to supply the required delimiter)."""
    if path != path.strip() or not path:
        raise ValueError(f"path_aliases needs a path without surrounding whitespace, got {path!r}")
    root = path.rstrip("/")
    if not root or not Path(root).is_absolute():
        raise ValueError(f"path_aliases needs an absolute path below the root, got {path!r}")
    forms = {root, os.path.realpath(root)}
    # Both file-URI spellings: the raw concatenation an agent typically writes, and the
    # canonical percent-encoded one `Path.as_uri()` produces. They differ as soon as an
    # ancestor holds a space or `%` (`/tmp/a%b c` -> `file:///tmp/a%25b%20c`), so covering
    # only the raw form would leave a valid URI pointing into the deleted worktree.
    aliases = forms | {f"file://{form}" for form in forms}
    for form in forms:
        with contextlib.suppress(ValueError):
            aliases.add(Path(form).as_uri())
    return tuple(sorted(aliases, key=len, reverse=True))


def relativize(text: str | None, aliases: Iterable[str]) -> str | None:
    """Rewrite absolute paths under a throwaway worktree to repo-relative form.

    The worktree is torn down before the caller reads a delegate result, so every
    absolute path the agent wrote into its prose is dead on arrival (#412). Each alias is
    replaced by a single ``.``, which leaves the rest of the path to follow on its own:
    ``<root>/src/f.py`` -> ``./src/f.py``, ``file://<root>/f.py`` -> ``./f.py``, and a
    bare ``<root>`` -> ``.``.

    The paths stay RELATIVE rather than being re-rooted at the live repo: the diff is not
    applied, so a live absolute path would be equally dead for a new file and, worse,
    would point at a real file whose content differs from what the agent described.

    The ``./`` prefix is load-bearing, not cosmetic: bare-relative output would turn
    ``[x](<root>/javascript:a)`` into a link target with a live URI scheme, and stripping
    only the root from a ``file://`` URI would leave ``file://./f.py``, where ``.`` parses
    as the HOST. Prefixing sidesteps both without teaching this function to parse Markdown.

    A match needs prose punctuation (or a string edge) on both sides — see ``_LEFT_DELIMS``
    / ``_RIGHT_DELIMS`` for why that is an allowlist rather than a denylist of path
    characters, and why ``/`` is allowed on only one side.

    Known limitation: a sentence-final bare root (``... in <root>.``) is left alone,
    because rewriting it would emit ``..`` — the parent directory, which is more
    misleading than the dead path it replaced.

    Aliases are sorted longest-first HERE rather than trusting the caller's order: with a
    containing alias tried second, a shorter one it contains would match first and name
    the wrong file. Blank entries are dropped — an empty alias matches everywhere.

    ``None`` passes through unchanged, mirroring ``redaction.redact_text``.

    Prefer ``sanitize_prose`` for text that is also being secret-redacted — the two
    operations interact, and it is the only combination that is safe for both."""
    return _replace_aliases(text, aliases, ".")


def _replace_aliases(text: str | None, aliases: Iterable[str], replacement: str) -> str | None:
    if not text:
        return text
    usable = sorted({alias for alias in aliases if alias.strip()}, key=len, reverse=True)
    if not usable:
        return text
    alternation = "|".join(re.escape(alias) for alias in usable)
    pattern = re.compile(rf"(?<![^{_LEFT_DELIMS}])(?:{alternation})(?=[{_RIGHT_DELIMS}]|$)")
    return pattern.sub(replacement, text)


# The stand-in an alias wears while the secret redactor runs. Three properties are required,
# and the third is why this is derived per input rather than being a module constant:
#
# 1. EVERY character is alphanumeric, hence inside the redactor's inline-value character
#    class (`redaction.SECRET_VALUE_PATTERNS`), so the redactor can only swallow the token
#    WHOLE or not at all. Its value match is anchored at a label and runs greedily until a
#    character outside that class; there is no such character inside the token, so it can
#    never stop part-way through and leave a fragment.
# 2. It is comfortably longer than the labelled pattern's 16-character minimum, so a
#    labelled path still reads as a long value and stays redacted no matter how short the
#    real root is (erring toward redaction).
# 3. It is ABSENT from the text being sanitized, verified rather than assumed. A fixed
#    sentinel let model output smuggle a covered secret straight through: the final
#    token -> `.` replacement also hit sentinels ALREADY in the text, and `.` is structural
#    in secret shapes. `eyJ<8 chars><sentinel><8 chars><sentinel><8 chars>` carries no dots
#    while the redactor inspects it, so the JWT pattern misses — and the replacement then
#    reconstructs a valid JWT in the output. Deriving the token from the text and checking
#    membership closes that: nothing pre-existing can be turned into `.`.
_PLACEHOLDER_PREFIX = "cicwt0alias0"


def _placeholder_seed(text: str) -> str:
    """The hex tail that makes a staged placeholder text-specific. A seam: overriding it is
    the only way a test can force the collision that ``_staged_placeholder``'s loop exists
    to handle, since deriving a digest that appears inside its own input is not
    constructible."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:24]


def _staged_placeholder(text: str) -> str:
    """An alphanumeric token, longer than the redactor's length floor, guaranteed absent
    from ``text``. Derived from the text's own digest so it is deterministic, then extended
    until it does not occur — absence is CHECKED, which is the property that matters;
    unpredictability is not relied upon. The loop terminates because each pass lengthens the
    token while ``text`` is finite."""
    token = _PLACEHOLDER_PREFIX + _placeholder_seed(text)
    while token in text:
        token += "0"
    return token


def sanitize_prose(text: str | None, aliases: Iterable[str]) -> str | None:
    """Relativize worktree paths AND redact secrets — the one order safe for both (#412).

    Doing these in sequence is not safe in either direction, which is why they are one
    function rather than two calls a caller has to order correctly:

    - **Relativize, then redact** shortens a labelled worktree-prefixed value below the
      redactor's 16-character floor, so ``api_key=<root>/abcdefgh`` becomes
      ``api_key=./abcdefgh`` and the secret escapes. Codex sees the worktree path, so an
      injected task can aim for that shape deliberately.
    - **Redact, then relativize** lets the redactor consume PART of an alias. Its value
      class covers ``=`` but stops at ``:``, so a crafted
      ``api_key=<16 chars>=file://<root>/abcdefgh`` has ``...=file`` eaten, leaving
      ``://<root>/abcdefgh`` — whose bare root is now preceded by ``/`` and so fails the
      left-hand delimiter check. Both the dead path and the secret survive.

    So each alias is first staged behind a token from ``_staged_placeholder`` — which the
    redactor can neither partially consume nor confuse with pre-existing text (see there for
    all three required properties); redaction runs against that; then any token that survived
    — i.e. was not part of a redacted secret — becomes ``.``.

    Redaction remains best-effort by contract (see ``redaction``): an adversarial model can
    always emit an unlabelled secret that no pattern matches. This closes the interaction
    between the two passes, not that broader gap."""
    if not text:
        return text
    placeholder = _staged_placeholder(text)
    staged = _replace_aliases(text, aliases, placeholder)
    redacted = redact_text(staged)
    if not redacted:
        return redacted
    return redacted.replace(placeholder, ".")


def remove(repo: str, worktree: Worktree, *, timeout: int) -> None:
    """Tear down the worktree and its temp parent. Best-effort; never raises.

    Also suppress ``WorktreeError``: the git calls route through ``_hardening_flags`` ->
    filter enumeration, which fails closed on an un-neutralizable driver name. Teardown
    must never let that (or any git failure) prevent ``shutil.rmtree`` or escape."""
    with contextlib.suppress(WorktreeError, subprocess.SubprocessError, OSError):
        _git(repo, ["worktree", "remove", "--force", worktree.path], timeout)
    shutil.rmtree(worktree.parent, ignore_errors=True)
    with contextlib.suppress(WorktreeError, subprocess.SubprocessError, OSError):
        _git(repo, ["worktree", "prune"], timeout)
