"""Server diagnostic logging: stderr by default, optional file, never stdout."""

from __future__ import annotations

import logging
import sys

from codex_in_claude import config, obs


def _reset():
    obs._configured = False
    root = logging.getLogger(obs.ROOT_LOGGER_NAME)
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()  # release file descriptors (a leaked FileHandler can lock tmp files)


def test_configure_uses_stderr_never_stdout(clean_env):
    _reset()
    logger = obs.configure(force=True)
    streams = [getattr(h, "stream", None) for h in logger.handlers]
    assert sys.stderr in streams
    assert sys.stdout not in streams
    # The JSON-RPC channel (stdout) must never receive a log byte.
    assert all(s is not sys.stdout for s in streams)
    assert logger.propagate is False


def test_configure_respects_log_level(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_LOG_LEVEL", "DEBUG")
    _reset()
    logger = obs.configure(force=True)
    assert logger.level == logging.DEBUG


def test_invalid_log_level_falls_back(clean_env):
    clean_env.setenv("CODEX_IN_CLAUDE_LOG_LEVEL", "bogus")
    assert config.log_level() == "WARNING"


def test_log_file_handler_writes(clean_env, tmp_path):
    path = tmp_path / "server.log"
    clean_env.setenv("CODEX_IN_CLAUDE_LOG_FILE", str(path))
    clean_env.setenv("CODEX_IN_CLAUDE_LOG_LEVEL", "INFO")
    _reset()
    logger = obs.configure(force=True)
    obs.get_logger("codex_in_claude.test").info("hello-disk")
    for h in logger.handlers:
        h.flush()
    assert path.exists()
    assert "hello-disk" in path.read_text(encoding="utf-8")


def test_configure_is_idempotent(clean_env):
    _reset()
    first = obs.configure()
    n = len(first.handlers)
    again = obs.configure()
    assert again is first
    assert len(again.handlers) == n  # no duplicate handlers piled on


def test_force_reconfigure_replaces_handlers(clean_env):
    _reset()
    first = obs.configure(force=True)
    handlers_before = list(first.handlers)
    again = obs.configure(force=True)  # exercises the remove-existing-handlers path
    assert len(again.handlers) == len(handlers_before)
    assert all(h not in handlers_before for h in again.handlers)


def test_unopenable_log_file_falls_back_to_stderr(clean_env, tmp_path):
    # A directory path cannot be opened as a file → OSError; we keep stderr only.
    clean_env.setenv("CODEX_IN_CLAUDE_LOG_FILE", str(tmp_path))
    _reset()
    logger = obs.configure(force=True)
    assert all(not isinstance(h, logging.FileHandler) for h in logger.handlers)
    assert any(getattr(h, "stream", None) is sys.stderr for h in logger.handlers)


def test_get_logger_namespaced(clean_env):
    _reset()
    log = obs.get_logger("codex_in_claude.server")
    assert log.name == "codex_in_claude.server"
    # inherits the configured root handlers via propagation
    assert logging.getLogger(obs.ROOT_LOGGER_NAME).handlers
