"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import pytest

from codex_in_claude import preflight
from codex_in_claude._core.runtime import CommandRun


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
