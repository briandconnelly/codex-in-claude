"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import os
import subprocess

import pytest

from codex_in_claude import preflight
from codex_in_claude._core import gitdiff
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
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
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
def _isolate_git_env(monkeypatch, tmp_path_factory):
    """Blanket protection: strip inherited git-location vars from every test's
    environment so even ad-hoc ``subprocess.run(["git", ...])`` calls stay anchored to
    their ``cwd``. Complements the per-call scrubbing in `run_git`. See #229.

    Also fully isolate the git-excludes layers the #330 resolver consults, so the suite
    never depends on the developer's or CI host's real config:

    * GLOBAL: point ``GIT_CONFIG_GLOBAL`` at an empty file and ``XDG_CONFIG_HOME`` at an
      empty dir (a non-empty ``XDG_CONFIG_HOME`` also suppresses the ``$HOME`` default
      excludes location in git's own resolution).
    * SYSTEM: production reads git's compiled-in system config (the resolver and the
      enumeration child do so identically — that parity is the point of #330). Tests must
      not depend on the host's ``/etc/gitconfig``, so patch ``gitdiff._base_git_env`` — the
      env BOTH share — to add ``GIT_CONFIG_NOSYSTEM=1``. That keeps resolver/child parity
      (both skip system config) while making the suite host-independent. Production
      ``_base_git_env`` is unchanged.

    Tests exercising the fix opt back in by overriding these explicitly."""
    for var in GIT_ISOLATION_VARS:
        monkeypatch.delenv(var, raising=False)
    empty_xdg = tmp_path_factory.mktemp("git-excludes-isolation")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(empty_xdg))

    _real_base_git_env = gitdiff._base_git_env

    def _base_git_env_no_system() -> dict[str, str]:
        env = _real_base_git_env()
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        return env

    monkeypatch.setattr(gitdiff, "_base_git_env", _base_git_env_no_system)


@pytest.fixture(autouse=True)
def _reset_preflight_cache():
    """Each test starts with a clean flag-support probe cache."""
    preflight.reset_cache()
    yield
    preflight.reset_cache()


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
