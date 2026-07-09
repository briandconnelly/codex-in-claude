"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import os
import subprocess

import pytest

from codex_in_claude import preflight
from codex_in_claude._core.runtime import CommandRun

# Git environment variables that redirect where git reads/writes its object store,
# index, and repo config. If pytest is invoked with any of these exported (e.g. by a
# pre-push hook running from a linked worktree, where GIT_DIR is set), the fixtures'
# `git add`/`commit`/`config` calls would operate on the *invoking* repo with a temp
# dir as the working tree -- staging every real file as deleted and rewriting the real
# repo's config. Scrub them so every git subprocess a test spawns is anchored purely by
# `cwd`. See #229.
GIT_ISOLATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
)


def scrubbed_git_env() -> dict[str, str]:
    """A copy of the current environment with the git-location vars removed."""
    return {k: v for k, v in os.environ.items() if k not in GIT_ISOLATION_VARS}


def run_git(cwd, *args, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command anchored to ``cwd`` alone, never an inherited GIT_DIR."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(),
    )


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch):
    """Blanket protection: strip inherited git-location vars from every test's
    environment so even ad-hoc ``subprocess.run(["git", ...])`` calls stay anchored to
    their ``cwd``. Complements the per-call scrubbing in `run_git`. See #229."""
    for var in GIT_ISOLATION_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_preflight_cache():
    """Each test starts with a clean flag-support probe cache."""
    preflight.reset_cache()
    yield
    preflight.reset_cache()


@pytest.fixture(autouse=True)
def _scrub_inherited_git_env(monkeypatch):
    """Scrub inherited git environment variables so tests that spawn git in
    temp repos don't operate on (or corrupt) the invoking repository.

    git exports ``GIT_DIR`` (and friends) to hooks. From a linked worktree it
    is an absolute path into the main checkout's ``.git/worktrees/<name>``, so
    a test subprocess running ``git`` with ``cwd=<tmp repo>`` resolves the wrong
    repository — corrupting the invoking repo's index and config (#229).
    Removing these vars lets each temp repo use its own ``.git``.
    """
    for var in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def clean_env(monkeypatch):
    """Strip CODEX_IN_CLAUDE_* env so tests see built-in defaults."""
    import os

    for key in list(os.environ):
        if key.startswith("CODEX_IN_CLAUDE_"):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


def make_run(
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    elapsed_ms: int = 5,
    timed_out: bool = False,
) -> CommandRun:
    return CommandRun(stdout, stderr, exit_code, elapsed_ms, timed_out)
