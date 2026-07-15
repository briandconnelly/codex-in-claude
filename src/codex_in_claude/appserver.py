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
import secrets
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from codex_in_claude import __version__, cli_contract
from codex_in_claude._core import redaction, streamcap
from codex_in_claude.schemas import RateLimitSnapshot, RateLimitWindowSnapshot

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
# Per-line cap for the stderr reader. Deliberately ABOVE the _MAX_STDERR_BYTES capture
# ceiling (#275): a line the reader would split mid-token must stay large enough that
# BoundedCapture evicts it WHOLE, so the redactor never sees a split secret (see _StderrDrain).
# 2x is the minimal clear margin — a line between the two caps is evicted whole anyway, so a
# larger cap only grows the drain thread's transient per-line buffer for no diagnostic gain.
_STDERR_LINE_CAP = 2 * _MAX_STDERR_BYTES
# Max blocking-read slice: caps how long the loop waits before re-checking the deadline
# and the cooperative-cancellation stop flag, so a cancelled call tears down promptly.
_POLL_SECONDS = 0.25
# Aggregate cap on messages buffered ahead of the single-consumer loop. Small on purpose:
# the valid burst is initialize -> import response and/or completed, so a handful is ample
# slack. A drifting app-server that floods faster than the loop drains then blocks the
# reader on put(), restoring the OS pipe's natural backpressure instead of converting a
# bounded pipe buffer into unbounded process memory (#277).
_MAX_QUEUED_MESSAGES = 4

# Display budget for one app-server-derived fragment. 300 matches every other foreign-text
# site (`codex.py`, `orchestration.py`, `_worker.py`); unlike those, the marker below is
# reserved inside it rather than the cut being silent.
_MAX_DISPLAY_CHARS = 300
_DISPLAY_TRUNC_MARKER = "…[truncated]"


def _display_text(text: object) -> str:
    """Redact secret-looking values out of app-server-supplied text, then bound it.

    Everything the app-server sends is foreign input, and upstream documents these
    messages as "raw failure messages for the client to report". ``_MAX_LINE_BYTES``
    bounds one JSONL *line* at 8 MiB; without this there is no cap between that line and
    the agent's context window, and no redaction between it and an MCP error envelope.

    Redaction runs BEFORE truncation: cutting first can split a secret so no pattern
    matches, publishing its prefix. The result never exceeds ``_MAX_DISPLAY_CHARS`` — an
    over-cap value ends in ``_DISPLAY_TRUNC_MARKER`` so a reader can tell a clipped
    diagnostic from a complete one. Non-strings are coerced (``None`` -> ``""``), since
    every field here is ``.get()``-ed off untrusted JSON and may be any type.

    Apply this to the foreign *fragment* only — never to a composed message — so a long
    fragment can never truncate away our own static explanation. Any future app-server
    string that reaches an envelope (e.g. ``stderr_tail``, #275) must route through here.

    Callers decide *whether a diagnostic exists* by testing the RAW wire value, not this
    function's output. Every falsey JSON value (``null``, ``""``, ``0``, ``false``, ``[]``,
    ``{}``) carries no diagnostic text, but coercing one here yields a truthy string, so
    branching on the sanitized result would emit noise like ``rejected the import: {}``
    instead of a clean generic sentence. The converse cannot happen: a truthy ``detail``
    never sanitizes to ``""`` (``redact_text`` substitutes a non-empty placeholder), so no
    caller can strand a prefix with an empty fragment after it.
    """
    if text is None:
        return ""
    out = redaction.redact_text(str(text)) or ""
    if len(out) <= _MAX_DISPLAY_CHARS:
        return out
    return out[: _MAX_DISPLAY_CHARS - len(_DISPLAY_TRUNC_MARKER)] + _DISPLAY_TRUNC_MARKER


def _display_stderr_tail(raw: str | None) -> str | None:
    """Project a raw ``codex app-server`` stderr tail for an error envelope.

    Redact the FULL capture, then keep the LAST ``_MAX_DISPLAY_CHARS`` characters with the
    truncation marker at the START. This is the mirror image of :func:`_display_text`, which
    keeps the *head*: ``stderr_tail`` is a rolling tail whose signal — the terminal
    exception / panic line — is last, so head-truncation would spend the whole budget on the
    oldest, least useful output (the #275 hazard). Redaction runs before the cut so a secret
    straddling the boundary can't survive as an unredacted suffix.

    Returns ``None`` for empty / ``None`` input so a caller can branch on *whether a
    diagnostic exists* — the same falsey-collapse the ``drain.snapshot() or None`` idiom
    gave, now folded in. Applying this at every ``stderr_tail=`` construction site keeps the
    :class:`TransferOutcome` invariant uniform: no raw foreign stderr ever rests on the
    outcome for a later path to surface unredacted."""
    if not raw:
        return None
    out = redaction.redact_text(raw) or ""
    if not out:
        return None
    if len(out) <= _MAX_DISPLAY_CHARS:
        return out
    return _DISPLAY_TRUNC_MARKER + out[-(_MAX_DISPLAY_CHARS - len(_DISPLAY_TRUNC_MARKER)) :]


def _has_control_char(text: str) -> bool:
    """True if ``text`` contains any Unicode ``Cc`` control code point — exactly C0
    (U+0000-U+001F), DEL (U+007F), and C1 (U+0080-U+009F)."""
    return any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in text)


def _valid_wire_id(value: object, max_bytes: int) -> str | None:
    """Return ``value`` if it is a valid opaque app-server identifier, else ``None``.

    Valid = a non-empty ``str``, free of ``Cc`` control chars, that encodes to at most
    ``max_bytes`` UTF-8 bytes. JSON permits escaped unpaired surrogates that decode fine but
    raise ``UnicodeEncodeError`` on encode; those are treated as invalid, never raised. This
    is a protocol check on a semantic id — reject, never truncate (truncating an id corrupts
    it)."""
    if not isinstance(value, str) or not value:
        return None
    # Cheap O(1) early reject: a UTF-8 encoding is always at least one byte per code point,
    # so an over-cap character count is already over-cap in bytes. This skips the control
    # scan and the full-string encode for a hostile oversized value (bounded only by the
    # 8 MiB line cap upstream), whose encode would otherwise allocate a second large copy.
    if len(value) > max_bytes:
        return None
    if _has_control_char(value):
        return None
    try:
        if len(value.encode("utf-8")) > max_bytes:
            return None
    except UnicodeError:
        return None
    return value


def _valid_codex_home(value: object) -> str | None:
    """Return ``value`` if it is a valid ``codexHome`` (a bounded, control-free, ABSOLUTE
    path), else ``None``. Absolute-ness is the real invariant — a relative value would
    re-base the ledger lookup on the server process's cwd."""
    home = _valid_wire_id(value, cli_contract.CODEX_HOME_MAX_BYTES)
    if home is None or not Path(home).is_absolute():
        return None
    return home


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
    """The result of one transfer run, ready for the server layer to shape into an
    envelope.

    Invariant (#276, #275): every app-server-derived fragment inside ``message``,
    ``ledger_path``, and ``stderr_tail`` has already passed through the display sanitizers
    (:func:`_display_text` for the first two, :func:`_display_stderr_tail` for the tail) —
    redacted and length-bounded — so a consumer may surface them without re-sanitizing.
    ``codex_home`` is deliberately RAW: it is a filesystem base, not display text."""

    status: TransferStatus
    thread_id: str | None = None
    thread_id_source: ThreadIdSource | None = None
    import_id: str | None = None
    codex_home: str | None = None
    ledger_path: str | None = None  # set on INCOMPLETE so the error can name it; bounded
    message: str | None = None  # upstream failure message / diagnostic detail; bounded
    stderr_tail: str | None = None  # redacted + display-bounded child stderr tail (#275)


class RateLimitReadStatus(StrEnum):
    """Discriminant for :class:`RateLimitReadOutcome`.

    Keeping these distinct is the #321 guardrail: a legitimate no-quota response, a codex
    too old to expose the method, protocol drift, a timeout, and a spawn failure are
    different facts with different repairs — never collapsed into one silent ``None`` (the
    tolerant-parse blind spot that let the token_count removal go unnoticed)."""

    OK = "ok"  # read returned a snapshot with at least one quota window
    NO_QUOTA = "no_quota"  # read succeeded but the account exposes no quota windows
    UNSUPPORTED = "unsupported"  # app-server lacks the method (older codex, -32601)
    PROTOCOL_ERROR = "protocol_error"  # drift: bad handshake / exit / line / non-(-32601) error
    TIMED_OUT = "timed_out"  # never received the read response in time
    SPAWN_FAILED = "spawn_failed"  # `codex` not on PATH


@dataclass
class RateLimitReadOutcome:
    """The result of one ``account/rateLimits/read`` run, ready for the rate_limit layer.

    ``snapshot`` is set only on ``OK``. ``codex_home`` is the app-server-reported (raw,
    absolute) $CODEX_HOME — provenance only (which account produced the read); the read is
    ephemeral, so nothing is persisted against it. ``message``/``stderr_tail`` are redacted and
    display-bounded like :class:`TransferOutcome`'s."""

    status: RateLimitReadStatus
    snapshot: RateLimitSnapshot | None = None
    codex_home: str | None = None
    message: str | None = None
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
    try:
        real = Path(path).resolve()
        exists = real.is_file()
    except (OSError, ValueError, RuntimeError):
        # resolve() raises ValueError on an embedded NUL and RuntimeError on a symlink
        # loop (CPython 3.11/3.12); is_file() re-raises non-ignored OSErrors such as
        # PermissionError on 3.11-3.13. All are malformed-input rejections, not server
        # faults — surface a stable, value-free reason (mapped to invalid_arguments)
        # instead of letting them escape as a retryable internal_error (#278).
        return PathValidation(None, "transcript_path is not a valid file path.")
    if not exists:
        return PathValidation(None, "transcript_path does not exist or is not a file.")
    try:
        if real.stat().st_size == 0:
            return PathValidation(None, "transcript_path is empty.")
    except OSError:  # pragma: no cover
        # Value-free by design: the path is not echoed back (#244, #278).
        return PathValidation(None, "transcript_path could not be read.")
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
        valid = _valid_wire_id(
            record.get(cli_contract.IMPORT_LEDGER_THREAD_ID_KEY), cli_contract.TRANSFER_ID_MAX_BYTES
        )
        if valid is not None:
            match = valid  # last VALID match wins; an invalid record is skipped (best-effort)
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


@dataclass
class _TargetLookup:
    """Tri-state result of scanning a completed notification's success entries for our
    imported thread id: a valid ``target``; ``invalid`` when a matching entry carried a
    present-but-unusable target (drift → PROTOCOL_ERROR); or neither (absent → try the
    ledger). Presence is key existence, so a ``null``/``0``/``""`` target is present+invalid,
    not absent."""

    target: str | None = None
    invalid: bool = False


def _target_from_successes(item: dict[str, Any], transcript_realpath: str) -> _TargetLookup:
    """Scan a fresh import's success entries for our imported thread id (tri-state).

    We submit exactly one session item, so an unlabeled success is ours; a present ``source``
    must match (defensive cross-check). A matching entry whose ``target`` key is present but
    fails validation is drift and makes the whole lookup ``invalid`` — even if another entry
    looks valid — so a corrupt live notification can never be papered over by the ledger."""
    successes = item.get(cli_contract.IMPORT_SUCCESSES_KEY)
    if not isinstance(successes, list):
        return _TargetLookup()
    first_valid: str | None = None
    saw_invalid = False
    for success in successes:
        if not isinstance(success, dict):
            continue
        src = success.get(cli_contract.IMPORT_SOURCE_KEY)
        if src is not None and src != transcript_realpath:
            continue
        if cli_contract.IMPORT_TARGET_KEY not in success:
            continue  # this entry carries no target → not present here
        valid = _valid_wire_id(
            success.get(cli_contract.IMPORT_TARGET_KEY), cli_contract.TRANSFER_ID_MAX_BYTES
        )
        if valid is None:
            saw_invalid = True  # present but unusable → drift
        elif first_valid is None:
            first_valid = valid
    if saw_invalid:
        return _TargetLookup(invalid=True)
    return _TargetLookup(target=first_valid)


def _failure_message(item: dict[str, Any]) -> str | None:
    """A joined, redacted, bounded message from the completed notification's failures.

    The join is sanitized as a whole (not per entry) so the bound applies to what an
    agent actually reads; the empty-join fallback is ours and stays outside it."""
    failures = item.get(cli_contract.IMPORT_FAILURES_KEY)
    if not isinstance(failures, list) or not failures:
        return None
    messages = [
        str(f.get(cli_contract.IMPORT_MESSAGE_KEY))
        for f in failures
        if isinstance(f, dict) and f.get(cli_contract.IMPORT_MESSAGE_KEY)
    ]
    return _display_text("; ".join(messages)) or "Codex reported an import failure."


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
    tail = _display_stderr_tail(stderr_tail)
    item = _session_item_result(completed_params)
    lookup = _target_from_successes(item, transcript_realpath)
    if lookup.invalid:
        return TransferOutcome(
            status=TransferStatus.PROTOCOL_ERROR,
            import_id=import_id,
            codex_home=codex_home,
            message="codex app-server reported an invalid imported thread id.",
            stderr_tail=tail,
        )
    if lookup.target is not None:
        return TransferOutcome(
            status=TransferStatus.OK,
            thread_id=lookup.target,
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
        # Display-only: `codex_home` is app-server-derived, so bound it — but append the
        # ledger filename afterwards, since that part is ours and naming it is the whole
        # point of the message. `_lookup_ledger` above still reads the RAW `codex_home`;
        # bounding the value itself would silently break the dedup lookup.
        ledger_path=str(Path(_display_text(codex_home)) / cli_contract.IMPORT_LEDGER_FILENAME),
        stderr_tail=tail,
    )


# Sentinels the stdout reader queues alongside parsed JSON messages.
_EOF = object()
_BAD_LINE = object()


def _relevant_to_loop(msg: Any) -> bool:
    """Whether ``transfer_session``'s loop can ever act on ``msg``.

    Mirrors the loop's admission checks *exactly* — the handshake (``id`` 1), the import
    response/error (``id`` 2), and the terminal completed notification — so filtering here
    is behavior-preserving: everything the loop would tolerantly ignore (progress
    notifications, unknown methods, other ids, non-dicts) is dropped before it can
    accumulate in the queue (#277). ``==`` (not ``is``) preserves the loop's own equality
    semantics for unusual ids (e.g. a JSON ``true`` or ``1.0`` that compares equal to 1).
    Loop-state guards (``codex_home is not None``, ``params`` shape) stay in the loop; the
    reader admits by message *shape* alone."""
    if not isinstance(msg, dict):
        return False
    if msg.get("id") == 1 or msg.get("id") == 2:
        return True
    return msg.get("method") == cli_contract.APP_SERVER_IMPORT_COMPLETED_NOTIFICATION


def _put_or_stop(q: queue.Queue[Any], stop: threading.Event, item: Any) -> None:
    """Enqueue ``item``, blocking only in bounded slices so a stopped consumer releases the
    producer. Returns without enqueueing once ``stop`` is set — a bounded queue alone is not
    shutdown-safe (a full queue can't take a poison sentinel and won't wake a producer parked
    in ``put()``), so the reader's teardown rides on this flag, not on the queue."""
    while not stop.is_set():
        try:
            q.put(item, timeout=_POLL_SECONDS)
            return
        except queue.Full:
            continue


@dataclass
class _Reader:
    """The stdout reader's handle: its bounded message queue and the levers to stop it.

    ``stop`` is set (before killing the child) so a reader parked in ``put()`` on a full
    queue unblocks and exits instead of stranding one daemon thread per transfer; ``thread``
    lets the caller make a bounded join for tidy teardown."""

    messages: queue.Queue[Any]
    stop: threading.Event
    thread: threading.Thread


def _spawn_reader(stdout: Any, is_relevant: Callable[[Any], bool] = _relevant_to_loop) -> _Reader:
    """Start a daemon thread that parses newline-delimited JSON from ``stdout`` and queues
    the loop-relevant messages (plus a ``_BAD_LINE``/``_EOF`` sentinel). ``is_relevant`` selects
    which messages the loop can act on (defaults to the transfer loop's admission set; the
    rate-limit read passes its own so only its unpredictable request id is admitted).

    ``stdout`` is a *binary* pipe read through ``iter_bounded_lines_interactive``: this is
    a request/response protocol, so a response line must surface on its newline rather
    than when the child exits. That reader also caps a runaway line while it is still
    being buffered — an over-cap line arrives carrying the truncation marker, fails to
    parse, and becomes ``_BAD_LINE``, which is the same protocol-drift outcome a
    well-formed-but-enormous line would have produced.

    The queue is bounded (:data:`_MAX_QUEUED_MESSAGES`) and only messages the loop can act
    on are enqueued (:func:`_relevant_to_loop`); together they keep a chatty or drifting
    app-server from growing process memory without bound (#277)."""
    q: queue.Queue[Any] = queue.Queue(maxsize=_MAX_QUEUED_MESSAGES)
    stop = threading.Event()

    def _run() -> None:
        try:
            for line in streamcap.iter_bounded_lines_interactive(stdout, _MAX_LINE_BYTES):
                if stop.is_set():
                    return
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except ValueError:
                    _put_or_stop(q, stop, _BAD_LINE)
                    return
                if is_relevant(parsed):
                    _put_or_stop(q, stop, parsed)
        finally:
            _put_or_stop(q, stop, _EOF)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return _Reader(messages=q, stop=stop, thread=thread)


class _StderrDrain:
    """Drain ``stderr`` on a daemon thread into a byte-budgeted rolling tail.

    A **pure** tail (``head_bytes=0``): when the app-server dies verbosely, the line that
    killed it is the last one, and a head window would spend half the budget on startup
    noise. When output exceeds the budget the snapshot opens with the truncation marker,
    saying plainly that earlier stderr was dropped.

    ``snapshot()`` is called from the main thread on error paths *while this thread is
    still adding lines*, which is why the capture must be thread-safe. Snapshots are
    taken fresh rather than memoized: a later error path should see later stderr."""

    def __init__(self, stderr: Any) -> None:
        self._capture = streamcap.BoundedCapture(_MAX_STDERR_BYTES, head_bytes=0)
        threading.Thread(target=self._run, args=(stderr,), daemon=True).start()

    def _run(self, stderr: Any) -> None:
        if stderr is None:  # pragma: no cover
            return
        # Line-oriented, like the stdout reader: the tail is read while the child is
        # still alive, so a drain-to-EOF reader would leave it empty (see #255).
        #
        # The per-line reader cap is _STDERR_LINE_CAP, deliberately ABOVE the capture's
        # _MAX_STDERR_BYTES ceiling (#275). A line the reader truncates would be split
        # mid-token, and the redactor in `_display_stderr_tail` runs on the assembled
        # snapshot — so a secret straddling that cut would survive as an unmatchable prefix
        # (the same redact-before-truncate hazard fixed for diffs in gitdiff's F3). Keeping
        # the reader cap above the capture cap means any line long enough to be split is
        # instead evicted WHOLE by BoundedCapture (a single oversized line is a hard-ceiling
        # eviction, never a mid-line cut), so the redactor only ever sees complete lines.
        for line in streamcap.iter_bounded_lines_interactive(stderr, _STDERR_LINE_CAP):
            self._capture.add(line)

    def snapshot(self) -> str:
        return self._capture.result().strip()


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
        # Binary pipes, not text=True: the readers own the bytes-to-text boundary so a
        # bounded per-line read can never race a TextIOWrapper holding decoded characters
        # in its own buffer. Nothing else may read these pipes.
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        # No child was created, so there is no stderr. Leave stderr_tail None — never the
        # internal BINARY_NOT_FOUND sentinel, which a future envelope path could otherwise
        # surface to the agent as if it were child diagnostics (#275).
        return TransferOutcome(status=TransferStatus.SPAWN_FAILED)

    drain = _StderrDrain(proc.stderr)
    reader = _spawn_reader(proc.stdout)

    def _send(obj: dict[str, Any]) -> None:
        if proc.stdin is None:  # pragma: no cover
            return
        # A child that already exited leaves a broken pipe; swallow it and let the
        # reader's EOF drive the outcome instead of raising out of the loop.
        with contextlib.suppress(OSError):
            proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
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
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            try:
                # Cap each wait so the deadline and the stop flag are re-checked promptly.
                msg = reader.messages.get(timeout=min(remaining, _POLL_SECONDS))
            except queue.Empty:
                continue
            if msg is _EOF:
                return TransferOutcome(
                    status=TransferStatus.PROTOCOL_ERROR,
                    import_id=import_id,
                    codex_home=codex_home,
                    message="codex app-server exited before the import completed.",
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            if msg is _BAD_LINE:
                return TransferOutcome(
                    status=TransferStatus.PROTOCOL_ERROR,
                    codex_home=codex_home,
                    message="codex app-server emitted a non-JSON or oversized line.",
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            if not isinstance(msg, dict):
                continue
            # initialize error → the handshake itself failed (protocol drift).
            if msg.get("id") == 1 and "error" in msg:
                error = msg.get("error")
                detail = error.get("message") if isinstance(error, dict) else None
                return TransferOutcome(
                    status=TransferStatus.PROTOCOL_ERROR,
                    message=f"codex app-server initialize failed: {_display_text(detail)}"
                    if detail
                    else "codex app-server rejected initialize.",
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            # initialize response → capture codexHome, then send initialized + import.
            if msg.get("id") == 1 and "result" in msg and not import_sent:
                result = msg.get("result")
                raw_home = (
                    result.get(cli_contract.APP_SERVER_CODEX_HOME_KEY)
                    if isinstance(result, dict)
                    else None
                )
                home = _valid_codex_home(raw_home)
                if home is None:
                    # No valid absolute codexHome means we can't locate the ledger nor trust
                    # the handshake — fail fast instead of importing into the dark. Both
                    # messages are value-free (the rejected value never reaches the envelope)
                    # but distinguish an omitted key from a present-but-invalid value: they are
                    # different drift modes.
                    home_present = (
                        isinstance(result, dict)
                        and cli_contract.APP_SERVER_CODEX_HOME_KEY in result
                    )
                    detail = (
                        "codex app-server initialize response reported an invalid codexHome "
                        "(must be a bounded, absolute path)."
                        if home_present
                        else "codex app-server initialize response omitted codexHome."
                    )
                    return TransferOutcome(
                        status=TransferStatus.PROTOCOL_ERROR,
                        message=detail,
                        stderr_tail=_display_stderr_tail(drain.snapshot()),
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
                        stderr_tail=_display_stderr_tail(drain.snapshot()),
                    )
                if status is TransferStatus.PROTOCOL_ERROR:
                    return TransferOutcome(
                        status=TransferStatus.PROTOCOL_ERROR,
                        codex_home=codex_home,
                        message=f"codex app-server rejected the import request: "
                        f"{_display_text(detail)}"
                        if detail
                        else "codex app-server rejected the import request.",
                        stderr_tail=_display_stderr_tail(drain.snapshot()),
                    )
                return TransferOutcome(
                    status=TransferStatus.ITEM_FAILURE,
                    codex_home=codex_home,
                    message=_display_text(detail)
                    if detail
                    else "codex app-server rejected the import.",
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            if msg.get("id") == 2 and "result" in msg:
                result = msg.get("result")
                if isinstance(result, dict):
                    # Non-load-bearing metadata: an invalid importId drops to None rather than
                    # failing the run (it does not establish the resumable thread).
                    import_id = _valid_wire_id(
                        result.get(cli_contract.IMPORT_ID_KEY), cli_contract.TRANSFER_ID_MAX_BYTES
                    )
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
                    stderr_tail=drain.snapshot(),
                )
            # Everything else (progress notifications, unknown methods, extra fields):
            # ignore and keep reading (tolerant decoding).
    finally:
        # Release a reader parked in put() on a full queue BEFORE killing the child, then
        # tear down and make a bounded join so we don't strand a daemon thread per transfer.
        reader.stop.set()
        _terminate(proc)
        reader.thread.join(timeout=_POLL_SECONDS)


# --- account/rateLimits/read (0.144+): live quota, no model spend -------------------
# Same one-shot transport as transfer_session — initialize handshake, one request, kill the
# child — but the request is `account/rateLimits/read` and the terminal signal is that
# request's RESPONSE (id 2), not a notification. Recovers the quota feature #321 lost when
# codex 0.144 dropped the token_count event off the exec stream.


def _classify_read_error(code: Any) -> RateLimitReadStatus:
    """Map a rate-limit-read JSON-RPC ``error.code`` to a read status.

    ``-32601`` (method not found) means the installed codex predates the method →
    UNSUPPORTED. Any other error — a reserved-range framework error, an application-range
    code, or a malformed non-integer code — means our request was rejected or the app-server
    is not speaking the protocol we encode → PROTOCOL_ERROR (drift). ``type(code) is int``
    (not ``isinstance``) so a JSON ``true``/float that compares ``== -32601`` is treated as
    malformed, never as method-not-found (mirrors :func:`classify_import_error`)."""
    if type(code) is int and code == cli_contract.JSONRPC_METHOD_NOT_FOUND:
        return RateLimitReadStatus.UNSUPPORTED
    return RateLimitReadStatus.PROTOCOL_ERROR


def _rate_limit_window_from_wire(
    blob: object,
) -> tuple[int | None, RateLimitWindowSnapshot] | None:
    """Map one app-server window object to ``(duration_minutes, snapshot)``, or ``None`` when
    it is not a usable window. The duration rides alongside so the caller classifies windows
    by LENGTH, not by the ``primary``/``secondary`` slot they arrived in — the app-server does
    not keep that slot order stable (#321)."""
    if not isinstance(blob, dict):
        return None

    # isinstance guards narrow to the expected types so ty is satisfied; semantic validation
    # (finite, range) is delegated to RateLimitWindowSnapshot's validators (mirrors normalize).
    def _num(v: object) -> float | None:
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # type: ignore[return-value]

    def _int(v: object) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) else None  # type: ignore[return-value]

    snap = RateLimitWindowSnapshot(
        used_percent=_num(blob.get(cli_contract.RATE_LIMIT_WINDOW_USED_PERCENT_KEY)),
        window_minutes=_int(blob.get(cli_contract.RATE_LIMIT_WINDOW_DURATION_MINS_KEY)),
        resets_at=_int(blob.get(cli_contract.RATE_LIMIT_WINDOW_RESETS_AT_KEY)),
    )
    if snap.used_percent is None and snap.resets_at is None:
        return None
    return snap.window_minutes, snap


def _valid_plan_type(value: object) -> str | None:
    """A bounded, control-free ``planType`` string, or None. Untrusted wire text bound for an
    envelope must not be arbitrarily large or carry control content (#321 review F2)."""
    return _valid_wire_id(value, cli_contract.RATE_LIMIT_PLAN_TYPE_MAX_BYTES)


def _valid_reached_type(value: object) -> str | None:
    """A recognized ``rateLimitReachedType`` (normalized lower-case), or None. An unknown value
    is dropped rather than trusted as a real limit reason — an unrecognized value from a drifting
    or hostile child must never become a false `exhausted` or leak into agent-visible prose (F2)."""
    if not isinstance(value, str):
        return None
    norm = value.strip().lower()
    return norm if norm in cli_contract.RATE_LIMIT_REACHED_TYPES else None


def _assign_window_slots(
    windows: list[tuple[int | None, RateLimitWindowSnapshot, str]],
) -> tuple[RateLimitWindowSnapshot | None, RateLimitWindowSnapshot | None]:
    """Map 1-2 parsed ``(duration, snapshot, source_slot)`` windows onto (primary, secondary) BY
    DURATION — shorter to ``primary``, longer to ``secondary``. A single window is slotted by its
    own duration (short → primary, long → secondary); an unknown duration keeps its source slot.
    Two windows with known durations are sorted so ``primary`` is always the shorter horizon the
    schema promises (review F3); if either duration is unknown, source order is kept."""
    threshold = cli_contract.RATE_LIMIT_SHORT_WINDOW_MAX_MINUTES
    if len(windows) == 1:
        duration, snap, slot = windows[0]
        if duration is not None:
            is_long = duration > threshold
        else:
            is_long = slot == cli_contract.RATE_LIMIT_SECONDARY_KEY
        return (None, snap) if is_long else (snap, None)
    (d0, s0, _), (d1, s1, _) = windows[0], windows[1]
    if d0 is not None and d1 is not None:
        return (s0, s1) if d0 <= d1 else (s1, s0)  # shorter → primary
    return s0, s1  # unknown duration(s): keep source order (primary slot came first)


def _parse_rate_limits(result: object) -> tuple[RateLimitReadStatus, RateLimitSnapshot | None]:
    """Discriminate an ``account/rateLimits/read`` result into (status, snapshot):

    * ``OK`` with a snapshot when at least one quota window parses.
    * ``NO_QUOTA`` when the block is explicitly null, or present with no quota windows — a
      legitimate no-quota account.
    * ``PROTOCOL_ERROR`` when the result is not an object, the required ``rateLimits`` key is
      absent, the block is the wrong type, or a *present* window is malformed. Collapsing these
      into ``NO_QUOTA`` would re-hide upstream drift behind a plausible "no quota" — the exact
      #321 silent-degradation trap (review F1).

    Windows are re-slotted by DURATION (shorter → ``primary``, longer → ``secondary``), not by
    the app-server's slot order, which is not stable. When both windows' durations are known they
    are sorted so ``primary`` is always the shorter horizon the schema promises (review F3); an
    unknown duration keeps its source slot."""
    if not isinstance(result, dict):
        return RateLimitReadStatus.PROTOCOL_ERROR, None
    if cli_contract.RATE_LIMITS_RESULT_KEY not in result:
        return RateLimitReadStatus.PROTOCOL_ERROR, None  # required field absent → drift
    block = result.get(cli_contract.RATE_LIMITS_RESULT_KEY)
    if block is None:
        return RateLimitReadStatus.NO_QUOTA, None  # explicit no-quota
    if not isinstance(block, dict):
        return RateLimitReadStatus.PROTOCOL_ERROR, None  # wrong type → drift
    # Collect present windows in slot order (primary, then secondary).
    windows: list[tuple[int | None, RateLimitWindowSnapshot, str]] = []
    for slot in (cli_contract.RATE_LIMIT_PRIMARY_KEY, cli_contract.RATE_LIMIT_SECONDARY_KEY):
        raw = block.get(slot)
        if raw is None:
            continue  # window absent/null for this slot
        parsed = _rate_limit_window_from_wire(raw)
        if parsed is None:
            return RateLimitReadStatus.PROTOCOL_ERROR, None  # present but malformed → drift
        windows.append((parsed[0], parsed[1], slot))
    if not windows:
        return RateLimitReadStatus.NO_QUOTA, None  # block present, no windows → no quota
    primary, secondary = _assign_window_slots(windows)
    return RateLimitReadStatus.OK, RateLimitSnapshot(
        plan_type=_valid_plan_type(block.get(cli_contract.RATE_LIMIT_PLAN_TYPE_KEY)),
        rate_limit_reached_type=_valid_reached_type(
            block.get(cli_contract.RATE_LIMIT_REACHED_TYPE_KEY)
        ),
        primary=primary,
        secondary=secondary,
    )


def read_rate_limits(  # noqa: PLR0915 - a linear JSON-RPC state machine; splitting obscures the flow
    *,
    command: list[str] | None = None,
    timeout_seconds: float,
    stop_event: threading.Event | None = None,
) -> RateLimitReadOutcome:
    """Read the current account quota via ``codex app-server`` — a read-only call with NO
    model-token spend. ``command`` is injectable so tests can drive a scripted fake
    app-server; ``stop_event`` requests cooperative cancellation. Never raises for a
    subprocess failure — every path returns a :class:`RateLimitReadOutcome` (the #321
    contract: a failure is a typed fact, never a silent ``None``)."""
    argv = command or [cli_contract.CODEX_BIN, *cli_contract.APP_SERVER_SUBCOMMAND]
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        return RateLimitReadOutcome(status=RateLimitReadStatus.SPAWN_FAILED)

    drain = _StderrDrain(proc.stderr)
    # An UNPREDICTABLE request id for the read, so a drifting/hostile child cannot prequeue a
    # fabricated response before it has read the request (it can't guess the id) — the effective
    # form of the review-F6 guard. Bounded to the JS safe-integer range and != 1 (initialize).
    read_id = secrets.randbelow(2**53 - 3) + 2
    reader = _spawn_reader(
        proc.stdout,
        lambda m: isinstance(m, dict) and (m.get("id") == 1 or m.get("id") == read_id),
    )

    def _send(obj: dict[str, Any]) -> None:
        if proc.stdin is None:  # pragma: no cover
            return
        with contextlib.suppress(OSError):
            proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            proc.stdin.flush()

    codex_home: str | None = None
    read_sent = False
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
                return RateLimitReadOutcome(
                    status=RateLimitReadStatus.TIMED_OUT, codex_home=codex_home
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return RateLimitReadOutcome(
                    status=RateLimitReadStatus.TIMED_OUT,
                    codex_home=codex_home,
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            try:
                msg = reader.messages.get(timeout=min(remaining, _POLL_SECONDS))
            except queue.Empty:
                continue
            if msg is _EOF:
                return RateLimitReadOutcome(
                    status=RateLimitReadStatus.PROTOCOL_ERROR,
                    codex_home=codex_home,
                    message="codex app-server exited before the rate-limit read completed.",
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            if msg is _BAD_LINE:
                return RateLimitReadOutcome(
                    status=RateLimitReadStatus.PROTOCOL_ERROR,
                    codex_home=codex_home,
                    message="codex app-server emitted a non-JSON or oversized line.",
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == 1 and "error" in msg:
                error = msg.get("error")
                detail = error.get("message") if isinstance(error, dict) else None
                return RateLimitReadOutcome(
                    status=RateLimitReadStatus.PROTOCOL_ERROR,
                    message=f"codex app-server initialize failed: {_display_text(detail)}"
                    if detail
                    else "codex app-server rejected initialize.",
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            if msg.get("id") == 1 and "result" in msg and not read_sent:
                result = msg.get("result")
                # codex_home is provenance-only here (no ledger to locate and nothing persisted),
                # so an absent or invalid value is non-fatal: we proceed with codex_home=None.
                # transfer_session, which needs it to find the import ledger, fails instead.
                codex_home = _valid_codex_home(
                    result.get(cli_contract.APP_SERVER_CODEX_HOME_KEY)
                    if isinstance(result, dict)
                    else None
                )
                _send(
                    {"jsonrpc": "2.0", "method": cli_contract.APP_SERVER_INITIALIZED_NOTIFICATION}
                )
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": read_id,
                        "method": cli_contract.APP_SERVER_RATE_LIMITS_READ_METHOD,
                        "params": None,
                    }
                )
                read_sent = True
                continue
            # The read response is matched on the UNPREDICTABLE read_id, so an unsolicited or
            # prequeued message (which cannot guess the id) is never trusted as quota — it falls
            # through to the tolerant ignore and the run resolves on the real response, EOF, or
            # timeout (review F6). read_sent guards only the one-time id-1 handshake above.
            if msg.get("id") == read_id and "error" in msg:
                error = msg.get("error")
                error = error if isinstance(error, dict) else {}
                status = _classify_read_error(error.get("code"))
                if status is RateLimitReadStatus.UNSUPPORTED:
                    return RateLimitReadOutcome(
                        status=RateLimitReadStatus.UNSUPPORTED,
                        codex_home=codex_home,
                        stderr_tail=_display_stderr_tail(drain.snapshot()),
                    )
                detail = error.get("message")
                return RateLimitReadOutcome(
                    status=RateLimitReadStatus.PROTOCOL_ERROR,
                    codex_home=codex_home,
                    message=f"codex app-server rejected the rate-limit read: "
                    f"{_display_text(detail)}"
                    if detail
                    else "codex app-server rejected the rate-limit read.",
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            if msg.get("id") == read_id and "result" in msg:
                status, snapshot = _parse_rate_limits(msg.get("result"))
                if status is RateLimitReadStatus.PROTOCOL_ERROR:
                    return RateLimitReadOutcome(
                        status=RateLimitReadStatus.PROTOCOL_ERROR,
                        codex_home=codex_home,
                        message="codex app-server returned a malformed rate-limit result.",
                        stderr_tail=_display_stderr_tail(drain.snapshot()),
                    )
                return RateLimitReadOutcome(
                    status=status,
                    snapshot=snapshot,
                    codex_home=codex_home,
                    stderr_tail=_display_stderr_tail(drain.snapshot()),
                )
            # Everything else (interleaved notifications, unknown/unsolicited ids): tolerant ignore.
    finally:
        reader.stop.set()
        _terminate(proc)
        reader.thread.join(timeout=_POLL_SECONDS)
