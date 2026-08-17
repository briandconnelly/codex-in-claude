"""Diagnostic logging for the MCP server.

Logs go to **stderr** (and, optionally, a file) — never stdout, which is the
stdio JSON-RPC channel a stray byte would corrupt, closing the connection. The
server otherwise emits nothing, so a disconnect leaves no trail (#39); this gives
one. This module wires the env config onto two namespaces — this package's own
and the shared library's — because they are siblings, not parent and child.
"""

from __future__ import annotations

import contextlib
import logging
import sys

from codex_in_claude import config

# This package's own loggers; children (codex_in_claude.server, …) inherit the
# handlers attached here by propagation.
ROOT_LOGGER_NAME = "codex_in_claude"

# The shared core lives in its own distribution, so `pontonier.core.*` loggers
# are siblings of ROOT_LOGGER_NAME, not children — they inherit nothing from it.
# Configured identically and alongside it, so library diagnostics land on the
# same stderr/file handlers instead of escaping to the stdlib root logger, which
# an embedding host may have wired to stdout (the JSON-RPC channel).
LIBRARY_LOGGER_NAME = "pontonier"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_configured = False


def configure(*, force: bool = False) -> logging.Logger:
    """Configure the `codex_in_claude` logger once (idempotent unless ``force``).

    Attaches a stderr handler plus, when ``CODEX_IN_CLAUDE_LOG_FILE`` is set, a
    file handler. Never attaches a stdout handler. Returns the configured logger.
    """
    global _configured  # noqa: PLW0603 — intentional one-time handler setup guard
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    if _configured and not force:
        return logger

    level = config.log_level()
    formatter = logging.Formatter(_LOG_FORMAT)
    path = config.log_file()

    for name in (ROOT_LOGGER_NAME, LIBRARY_LOGGER_NAME):
        target = logging.getLogger(name)
        target.setLevel(level)
        # Stop propagation so records do not reach the root logger's default handler
        # (which could be wired to stdout by an embedding host).
        target.propagate = False

        for handler in target.handlers[:]:
            target.removeHandler(handler)
            with contextlib.suppress(Exception):
                handler.close()

        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        target.addHandler(stderr_handler)

        if path:
            try:
                file_handler = logging.FileHandler(path, encoding="utf-8")
                file_handler.setFormatter(formatter)
                target.addHandler(file_handler)
            except OSError:
                target.warning(
                    "could not open CODEX_IN_CLAUDE_LOG_FILE %r; logging to stderr only", path
                )

    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the server namespace, configuring handlers first."""
    configure()
    return logging.getLogger(name)
