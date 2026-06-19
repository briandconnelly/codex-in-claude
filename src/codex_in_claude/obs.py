"""Diagnostic logging for the MCP server.

Logs go to **stderr** (and, optionally, a file) — never stdout, which is the
stdio JSON-RPC channel a stray byte would corrupt, closing the connection. The
server otherwise emits nothing, so a disconnect leaves no trail (#39); this gives
one. Generic `_core` modules just call `logging.getLogger(__name__)` and inherit
these handlers by propagation — only this parent module wires the env config.
"""

from __future__ import annotations

import contextlib
import logging
import sys

from codex_in_claude import config

# All server loggers live under this namespace; handlers are attached here and
# child loggers (e.g. codex_in_claude._core.runtime) inherit them by propagation.
ROOT_LOGGER_NAME = "codex_in_claude"

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

    logger.setLevel(config.log_level())
    # Stop propagation so records do not reach the root logger's default handler
    # (which could be wired to stdout by an embedding host).
    logger.propagate = False

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()

    formatter = logging.Formatter(_LOG_FORMAT)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    path = config.log_file()
    if path:
        try:
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            logger.warning(
                "could not open CODEX_IN_CLAUDE_LOG_FILE %r; logging to stderr only", path
            )

    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the server namespace, configuring handlers first."""
    configure()
    return logging.getLogger(name)
