"""Minimal one-shot `codex app-server` client for session transfer.

Codex-specific (NOT under ``_core``): it encodes the experimental
``externalAgentConfig/import`` protocol used by ``codex_transfer`` to hand a Claude
Code session transcript to a resumable Codex thread. It spawns ``codex app-server``,
performs the ``initialize``/``initialized`` handshake, sends exactly ONE import
request, waits for the matching ``.../import/completed`` notification, then
terminates the child. Deliberately single-request — no broker, no session reuse
(upstream's long-lived broker is the source of most of its transfer bug reports).

Every wire assumption (method/notification names, field names, the ledger filename,
the ``-32601`` sentinel) lives in :mod:`codex_in_claude.cli_contract`. This module
maps the run to a plain :class:`TransferOutcome`; the server layer turns that into the
result envelope. It may import from ``_core`` (one-way dependency) but nothing in
``_core`` imports it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codex_in_claude import __version__, cli_contract
from codex_in_claude._core.runtime import BINARY_NOT_FOUND

if TYPE_CHECKING:
    from collections.abc import Callable

# Claude Code writes session transcripts under ~/.claude/projects/<cwd-slug>/. We only
# transfer files from there (mirrors upstream's containment check) — a defense against
# being pointed at an arbitrary file to import.
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Our clientInfo name in the handshake (non-identifying; shows up in the app-server log).
_CLIENT_NAME = "codex-in-claude"

# Guard against a runaway app-server flooding a single JSONL line (protocol drift).
_MAX_LINE_BYTES = 8 * 1024 * 1024
# Bounded stderr tail retained for diagnostics on an error path.
_MAX_STDERR_BYTES = 64 * 1024
# Max blocking-read slice: caps how long the loop waits before re-checking the deadline
# and the cooperative-cancellation stop flag, so a cancelled call tears down promptly.
_POLL_SECONDS = 0.25


def _terminate(proc: subprocess.Popen) -> None:
    """Close stdin, SIGKILL the whole process group, then reap the leader.

    Mirrors ``runtime._kill_group``: signals by ``proc.pid`` (== pgid, since the child
    is spawned with ``start_new_session=True``) and does NOT early-return when the direct
    child has already exited — a descendant that inherited a pipe must still be killed
    after the leader becomes a zombie (``kill_process_tree`` would skip it)."""
    with contextlib.suppress(OSError):
        if proc.stdin is not None:
            proc.stdin.close()
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


class TransferStatus(StrEnum):
    """Discriminant for :class:`TransferOutcome`."""

    OK = "ok"
    UNSUPPORTED = "unsupported"  # app-server lacks the import method (old codex, -32601)
    ITEM_FAILURE = "item_failure"  # import ran but the SESSIONS item failed
    INCOMPLETE = "incomplete"  # completed with no fresh target and no ledger record
    PROTOCOL_ERROR = "protocol_error"  # drift: bad handshake / unexpected exit / bad line
    TIMED_OUT = "timed_out"  # never received the completed notification in time
    SPAWN_FAILED = "spawn_failed"  # `codex` not on PATH


class ThreadIdSource(StrEnum):
    IMPORT_NOTIFICATION = "import_notification"  # fresh import: success `target`
    LEDGER = "ledger"  # dedup re-import: recovered from the ledger


@dataclass
class TransferOutcome:
    status: TransferStatus
    thread_id: str | None = None
    thread_id_source: ThreadIdSource | None = None
    import_id: str | None = None
    codex_home: str | None = None
    ledger_path: str | None = None  # set on INCOMPLETE so the error can name it
    message: str | None = None  # upstream failure message / diagnostic detail
    stderr_tail: str | None = None


@dataclass
class PathValidation:
    realpath: str | None
    reason: str | None


def validate_transcript_path(path: str) -> PathValidation:
    """Validate a Claude session transcript path BEFORE spawning anything.

    Requires a ``.jsonl`` file that exists, is non-empty, and whose realpath resolves
    under ``~/.claude/projects``. Returns the resolved realpath on success, or a
    human-readable reason on failure (the server maps it to ``invalid_arguments``)."""
    if not isinstance(path, str) or not path.strip():
        return PathValidation(None, "transcript_path must be a non-empty string.")
    if not path.endswith(".jsonl"):
        return PathValidation(None, "transcript_path must be a .jsonl session transcript.")
    real = Path(path).resolve()
    if not real.is_file():
        return PathValidation(None, "transcript_path does not exist or is not a file.")
    try:
        if real.stat().st_size == 0:
            return PathValidation(None, "transcript_path is empty.")
    except OSError as exc:  # pragma: no cover
        return PathValidation(None, f"transcript_path could not be read: {exc}.")
    try:
        real.relative_to(CLAUDE_PROJECTS_DIR.resolve())
    except ValueError:
        return PathValidation(
            None,
            f"transcript_path must be a Claude session under {CLAUDE_PROJECTS_DIR}.",
        )
    return PathValidation(str(real), None)


def _session_migration_params(transcript_realpath: str, cwd: str) -> dict[str, Any]:
    """Build the ``externalAgentConfig/import`` params for one whole-session transfer."""
    basename = Path(transcript_realpath).name
    return {
        "source": _CLIENT_NAME,
        "migrationItems": [
            {
                cli_contract.IMPORT_ITEM_TYPE_KEY: cli_contract.IMPORT_SESSION_ITEM_TYPE,
                "description": f"Transfer Claude session {basename}",
                "cwd": None,
                "details": {
                    "plugins": [],
                    "sessions": [{"path": transcript_realpath, "cwd": cwd, "title": None}],
                    "mcpServers": [],
                    "hooks": [],
                    "subagents": [],
                    "commands": [],
                },
            }
        ],
    }


def _sha256_file(path: str) -> str | None:
    # Stream in chunks rather than slurping the whole transcript into memory — a
    # session .jsonl can be large, and this runs on the ledger-lookup path.
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:  # pragma: no cover - the caller already stat'd the file
        return None
    return digest.hexdigest()


def _lookup_ledger(codex_home: str, transcript_realpath: str) -> str | None:
    """Recover an imported thread id from the undocumented import ledger.

    Match on ``source_path == realpath(transcript)`` AND ``content_sha256 ==
    sha256(bytes)``, taking the LAST match (mirrors upstream). Read tolerantly and
    bounded; any malformed/missing/oversized state returns None (never raises)."""
    ledger = Path(codex_home) / cli_contract.IMPORT_LEDGER_FILENAME
    try:
        if ledger.stat().st_size > cli_contract.IMPORT_LEDGER_MAX_BYTES:
            return None
        data = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    records = data.get(cli_contract.IMPORT_LEDGER_RECORDS_KEY)
    if not isinstance(records, list):
        return None
    content_sha = _sha256_file(transcript_realpath)
    if content_sha is None:  # pragma: no cover
        return None
    match: str | None = None
    for record in records[: cli_contract.IMPORT_LEDGER_MAX_RECORDS]:
        if not isinstance(record, dict):
            continue
        if record.get(cli_contract.IMPORT_LEDGER_SOURCE_PATH_KEY) != transcript_realpath:
            continue
        if record.get(cli_contract.IMPORT_LEDGER_CONTENT_SHA_KEY) != content_sha:
            continue
        thread_id = record.get(cli_contract.IMPORT_LEDGER_THREAD_ID_KEY)
        if isinstance(thread_id, str) and thread_id:
            match = thread_id  # last match wins
    return match


def _session_item_result(completed_params: dict[str, Any]) -> dict[str, Any]:
    """The SESSIONS entry of a completed notification's ``itemTypeResults``, or {}."""
    results = completed_params.get(cli_contract.IMPORT_ITEM_RESULTS_KEY)
    if isinstance(results, list):
        for entry in results:
            if (
                isinstance(entry, dict)
                and entry.get(cli_contract.IMPORT_ITEM_TYPE_KEY)
                == cli_contract.IMPORT_SESSION_ITEM_TYPE
            ):
                return entry
    return {}


def _target_from_successes(item: dict[str, Any], transcript_realpath: str) -> str | None:
    """The imported thread id from a fresh import's success entry, if present."""
    successes = item.get(cli_contract.IMPORT_SUCCESSES_KEY)
    if not isinstance(successes, list):
        return None
    for success in successes:
        if not isinstance(success, dict):
            continue
        # We submit exactly one session item, so an unlabeled success is ours; when
        # `source` is present it must match (defensive cross-check).
        src = success.get(cli_contract.IMPORT_SOURCE_KEY)
        if src is not None and src != transcript_realpath:
            continue
        target = success.get(cli_contract.IMPORT_TARGET_KEY)
        if isinstance(target, str) and target:
            return target
    return None


def _failure_message(item: dict[str, Any]) -> str | None:
    """A joined message from the completed notification's failure entries, if any."""
    failures = item.get(cli_contract.IMPORT_FAILURES_KEY)
    if not isinstance(failures, list) or not failures:
        return None
    messages = [
        str(f.get(cli_contract.IMPORT_MESSAGE_KEY))
        for f in failures
        if isinstance(f, dict) and f.get(cli_contract.IMPORT_MESSAGE_KEY)
    ]
    return "; ".join(messages) or "Codex reported an import failure."


def _resolve_completed(
    completed_params: dict[str, Any],
    *,
    transcript_realpath: str,
    codex_home: str,
    import_id: str | None,
    stderr_tail: str,
) -> TransferOutcome:
    """Map a completed notification to an outcome: notification `target` first, then
    the ledger fallback for a byte-identical (deduped) re-import."""
    tail = stderr_tail or None
    item = _session_item_result(completed_params)
    target = _target_from_successes(item, transcript_realpath)
    if target is not None:
        return TransferOutcome(
            status=TransferStatus.OK,
            thread_id=target,
            thread_id_source=ThreadIdSource.IMPORT_NOTIFICATION,
            import_id=import_id,
            codex_home=codex_home,
            stderr_tail=tail,
        )
    failure = _failure_message(item)
    if failure is not None:
        return TransferOutcome(
            status=TransferStatus.ITEM_FAILURE,
            import_id=import_id,
            codex_home=codex_home,
            message=failure,
            stderr_tail=tail,
        )
    # Empty successes AND failures: either a byte-identical re-import (deduped) or a
    # transcript Codex could not import. The ledger disambiguates.
    ledger_thread = _lookup_ledger(codex_home, transcript_realpath)
    if ledger_thread is not None:
        return TransferOutcome(
            status=TransferStatus.OK,
            thread_id=ledger_thread,
            thread_id_source=ThreadIdSource.LEDGER,
            import_id=import_id,
            codex_home=codex_home,
            stderr_tail=tail,
        )
    return TransferOutcome(
        status=TransferStatus.INCOMPLETE,
        import_id=import_id,
        codex_home=codex_home,
        ledger_path=str(Path(codex_home) / cli_contract.IMPORT_LEDGER_FILENAME),
        stderr_tail=tail,
    )


# Sentinels the stdout reader queues alongside parsed JSON messages.
_EOF = object()
_BAD_LINE = object()


def _spawn_reader(stdout: Any) -> queue.Queue[Any]:
    """Start a daemon thread that parses newline-delimited JSON from ``stdout`` and
    queues each message (or a ``_BAD_LINE``/``_EOF`` sentinel)."""
    q: queue.Queue[Any] = queue.Queue()

    def _run() -> None:
        try:
            for line in stdout:
                stripped = line.strip()
                if not stripped:
                    continue
                if len(stripped) > _MAX_LINE_BYTES:
                    q.put(_BAD_LINE)
                    return
                try:
                    q.put(json.loads(stripped))
                except ValueError:
                    q.put(_BAD_LINE)
                    return
        finally:
            q.put(_EOF)

    threading.Thread(target=_run, daemon=True).start()
    return q


def _spawn_stderr_drain(stderr: Any) -> Callable[[], str]:
    """Start a daemon thread draining ``stderr`` into a bounded buffer; return a
    callable that yields the retained tail."""
    chunks: list[str] = []
    total = [0]

    def _run() -> None:
        if stderr is None:  # pragma: no cover
            return
        for line in stderr:
            if total[0] < _MAX_STDERR_BYTES:
                chunks.append(line)
                total[0] += len(line)

    threading.Thread(target=_run, daemon=True).start()
    return lambda: "".join(chunks).strip()[-_MAX_STDERR_BYTES:]


def classify_import_error(code: Any) -> TransferStatus:
    """Map an import-response JSON-RPC ``error.code`` to the transfer status it implies.

    The split decides *who is at fault*, so it decides which repair the agent is handed:

    * A non-integer code (absent, ``null``, a string, a JSON float, or a ``bool``) is a
      malformed response — the app-server is not speaking the protocol we encode, which is
      drift. ``PROTOCOL_ERROR`` -> ``cli_contract_changed``.
    * ``-32601`` (method not found) means the installed codex predates the import method.
      ``UNSUPPORTED`` -> ``transfer_unsupported``. Checked before the reserved range, which
      it sits inside.
    * Any other reserved-range code (``-32768..-32000``: invalid params/request, parse and
      internal errors, plus the server-defined ``-32000..-32099`` band) means *our request*
      is at fault. ``PROTOCOL_ERROR`` -> ``cli_contract_changed``.
    * An application-range code is Codex rejecting *this transcript*.
      ``ITEM_FAILURE`` -> ``transfer_failed``.

    ``type(code) is int`` rather than ``isinstance``: Python's ``bool`` is a subclass of
    ``int`` (a JSON ``true`` would satisfy ``isinstance`` and be read as application-range),
    and a JSON number may decode to ``float``, where ``-32601.0 == -32601`` would otherwise
    match the method-not-found branch and wrongly tell the caller to update codex."""
    if type(code) is not int:
        return TransferStatus.PROTOCOL_ERROR
    if code == cli_contract.JSONRPC_METHOD_NOT_FOUND:
        return TransferStatus.UNSUPPORTED
    if cli_contract.JSONRPC_RESERVED_ERROR_MIN <= code <= cli_contract.JSONRPC_RESERVED_ERROR_MAX:
        return TransferStatus.PROTOCOL_ERROR
    return TransferStatus.ITEM_FAILURE


def transfer_session(  # noqa: PLR0915 - a linear JSON-RPC state machine; splitting obscures the flow
    *,
    transcript_realpath: str,
    cwd: str,
    command: list[str] | None = None,
    timeout_seconds: float,
    stop_event: threading.Event | None = None,
) -> TransferOutcome:
    """Import a (pre-validated) Claude transcript into a Codex thread via app-server.

    ``command`` is the argv to spawn (defaults to ``codex app-server``); it is
    injectable so tests can drive a scripted fake app-server. ``stop_event`` lets a
    caller request cooperative cancellation: when set, the loop stops within
    ``_POLL_SECONDS`` and the ``finally`` kills the process group. Never raises for a
    subprocess failure — every path returns a :class:`TransferOutcome`."""
    argv = command or [cli_contract.CODEX_BIN, *cli_contract.APP_SERVER_SUBCOMMAND]
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            start_new_session=True,
        )
    except OSError:
        return TransferOutcome(status=TransferStatus.SPAWN_FAILED, stderr_tail=BINARY_NOT_FOUND)

    stderr_tail = _spawn_stderr_drain(proc.stderr)
    messages = _spawn_reader(proc.stdout)

    def _send(obj: dict[str, Any]) -> None:
        if proc.stdin is None:  # pragma: no cover
            return
        # A child that already exited leaves a broken pipe; swallow it and let the
        # reader's EOF drive the outcome instead of raising out of the loop.
        with contextlib.suppress(OSError):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

    codex_home: str | None = None
    import_id: str | None = None
    import_sent = False
    deadline = time.monotonic() + timeout_seconds
    try:
        _send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": cli_contract.APP_SERVER_INITIALIZE_METHOD,
                "params": {
                    "clientInfo": {"name": _CLIENT_NAME, "version": __version__},
                    "capabilities": {cli_contract.APP_SERVER_EXPERIMENTAL_CAPABILITY: True},
                },
            }
        )
        while True:
            if stop_event is not None and stop_event.is_set():
                # Cooperative cancellation: the caller abandoned this run. The value is
                # discarded (the caller re-raises), but the finally still kills the child.
                return TransferOutcome(
                    status=TransferStatus.TIMED_OUT,
                    import_id=import_id,
                    codex_home=codex_home,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return TransferOutcome(
                    status=TransferStatus.TIMED_OUT,
                    import_id=import_id,
                    codex_home=codex_home,
                    stderr_tail=stderr_tail() or None,
                )
            try:
                # Cap each wait so the deadline and the stop flag are re-checked promptly.
                msg = messages.get(timeout=min(remaining, _POLL_SECONDS))
            except queue.Empty:
                continue
            if msg is _EOF:
                return TransferOutcome(
                    status=TransferStatus.PROTOCOL_ERROR,
                    import_id=import_id,
                    codex_home=codex_home,
                    message="codex app-server exited before the import completed.",
                    stderr_tail=stderr_tail() or None,
                )
            if msg is _BAD_LINE:
                return TransferOutcome(
                    status=TransferStatus.PROTOCOL_ERROR,
                    codex_home=codex_home,
                    message="codex app-server emitted a non-JSON or oversized line.",
                    stderr_tail=stderr_tail() or None,
                )
            if not isinstance(msg, dict):
                continue
            # initialize error → the handshake itself failed (protocol drift).
            if msg.get("id") == 1 and "error" in msg:
                error = msg.get("error")
                detail = error.get("message") if isinstance(error, dict) else None
                return TransferOutcome(
                    status=TransferStatus.PROTOCOL_ERROR,
                    message=f"codex app-server initialize failed: {detail}"
                    if detail
                    else "codex app-server rejected initialize.",
                    stderr_tail=stderr_tail() or None,
                )
            # initialize response → capture codexHome, then send initialized + import.
            if msg.get("id") == 1 and "result" in msg and not import_sent:
                result = msg.get("result")
                home = (
                    result.get(cli_contract.APP_SERVER_CODEX_HOME_KEY)
                    if isinstance(result, dict)
                    else None
                )
                if not isinstance(home, str) or not home:
                    # No codexHome means we can't locate the ledger nor trust the
                    # handshake — fail fast instead of importing into the dark.
                    return TransferOutcome(
                        status=TransferStatus.PROTOCOL_ERROR,
                        message="codex app-server initialize response omitted codexHome.",
                        stderr_tail=stderr_tail() or None,
                    )
                codex_home = home
                _send(
                    {"jsonrpc": "2.0", "method": cli_contract.APP_SERVER_INITIALIZED_NOTIFICATION}
                )
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": cli_contract.APP_SERVER_IMPORT_METHOD,
                        "params": _session_migration_params(transcript_realpath, cwd),
                    }
                )
                import_sent = True
                continue
            # import response error → classify by JSON-RPC code.
            if msg.get("id") == 2 and "error" in msg:
                error = msg.get("error")
                error = error if isinstance(error, dict) else {}
                code = error.get("code")
                detail = error.get("message")
                status = classify_import_error(code)
                if status is TransferStatus.UNSUPPORTED:
                    return TransferOutcome(
                        status=TransferStatus.UNSUPPORTED,
                        codex_home=codex_home,
                        stderr_tail=stderr_tail() or None,
                    )
                if status is TransferStatus.PROTOCOL_ERROR:
                    return TransferOutcome(
                        status=TransferStatus.PROTOCOL_ERROR,
                        codex_home=codex_home,
                        message=f"codex app-server rejected the import request: {detail}"
                        if detail
                        else "codex app-server rejected the import request.",
                        stderr_tail=stderr_tail() or None,
                    )
                return TransferOutcome(
                    status=TransferStatus.ITEM_FAILURE,
                    codex_home=codex_home,
                    message=str(detail) if detail else "codex app-server rejected the import.",
                    stderr_tail=stderr_tail() or None,
                )
            if msg.get("id") == 2 and "result" in msg:
                result = msg.get("result")
                if isinstance(result, dict) and isinstance(
                    result.get(cli_contract.IMPORT_ID_KEY), str
                ):
                    import_id = result[cli_contract.IMPORT_ID_KEY]
                continue
            # The terminal signal: the import/completed notification. Single in-flight
            # import ⇒ any completed notification is ours (handles completion-before-
            # response and interleaved progress notifications).
            if (
                msg.get("method") == cli_contract.APP_SERVER_IMPORT_COMPLETED_NOTIFICATION
                and codex_home is not None
                and isinstance(msg.get("params"), dict)
            ):
                return _resolve_completed(
                    msg["params"],
                    transcript_realpath=transcript_realpath,
                    codex_home=codex_home,
                    import_id=import_id,
                    stderr_tail=stderr_tail(),
                )
            # Everything else (progress notifications, unknown methods, extra fields):
            # ignore and keep reading (tolerant decoding).
    finally:
        _terminate(proc)
