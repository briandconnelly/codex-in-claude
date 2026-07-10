"""Bounded streaming primitives shared by the subprocess runtime and diff gather.

CLI-agnostic; no parent-package imports. Three tools:

- ``iter_bounded_lines`` reads a text stream in fixed chunks and yields complete
  lines, capping any single logical line so a pathological producer cannot buffer
  an unbounded line into memory before a newline arrives. Lines are kept whole up
  to ``max_line_bytes``; a single logical line that exceeds the cap is truncated
  mid-line with a ``…[line truncated]`` marker (so a pathologically long JSONL
  line may not parse, but normal-sized lines are preserved intact).
  **It drains to EOF and must never back an interactive stream** — see its docstring.
- ``iter_bounded_lines_interactive`` applies the same bounding to a *binary* stream
  read via ``read1``, so a line surfaces as soon as its newline arrives rather than
  when the producer exits. Use it for request/response protocols over a live pipe.
- ``BoundedCapture`` accumulates lines under a byte budget, keeping a head window
  plus a bounded tail so the newest lines (where codex emits usage/rate-limit
  metadata) survive truncation. Complete lines only. ``head_bytes=0`` drops the head
  window for a pure rolling tail. It is thread-safe: a reader may snapshot it while a
  writer thread is still filling it.

Both readers share ``_assemble_bounded_lines``; they differ only in the chunk source,
which is the whole of the drain/interactive distinction. Keeping the truncation and
pending-line logic in one place is deliberate: it has been the site of several subtle
bugs (see the regression tests in ``tests/test_gitdiff.py``), and a second copy would
let them diverge.
"""

from __future__ import annotations

import codecs
import threading
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import io
    from collections.abc import Iterable, Iterator
    from typing import TextIO

_LINE_TRUNC_MARKER = "…[line truncated]\n"
_LINE_TRUNC_MARKER_BYTES = len(_LINE_TRUNC_MARKER.encode("utf-8"))
_OUTPUT_TRUNC_MARKER = "[output truncated]\n"


def _nbytes(text: str) -> int:
    return len(text.encode("utf-8", "replace"))


def _truncate_to_marker(text: str, max_line_bytes: int) -> str:
    """Truncate ``text`` (a logical line WITHOUT its trailing newline) so that the
    returned line — truncated content plus ``_LINE_TRUNC_MARKER`` — encodes to
    ``<= max_line_bytes`` bytes. UTF-8-safe: never splits a multibyte character."""
    content_limit = max(0, max_line_bytes - _LINE_TRUNC_MARKER_BYTES)
    encoded = text.encode("utf-8", "replace")
    return encoded[:content_limit].decode("utf-8", "ignore") + _LINE_TRUNC_MARKER


def _drain_chunks(stream: TextIO, chunk_size: int) -> Iterator[str]:
    """Chunk source for a stream being read to completion.

    ``TextIO.read(n)`` blocks until ``n`` characters or EOF, so this source only ever
    finishes when the producer exits. That is exactly right for draining and fatally
    wrong for an interactive stream."""
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            return
        yield chunk


def _available_chunks(stream: io.BufferedIOBase, chunk_size: int, encoding: str) -> Iterator[str]:
    """Chunk source for a live stream, yielding whatever bytes have arrived.

    ``read1`` returns as soon as *any* bytes are available (``b""`` only at EOF), which
    is what lets a line surface on its newline instead of on the producer's exit. The
    incremental decoder holds a partial multibyte character across a chunk boundary, so
    reading a fixed byte count cannot split a character."""
    decoder = codecs.getincrementaldecoder(encoding)("replace")
    while True:
        data = stream.read1(chunk_size)
        if not data:
            # `final=True` flushes a trailing partial character as U+FFFD rather than
            # dropping the bytes silently.
            tail = decoder.decode(b"", final=True)
            if tail:
                yield tail
            return
        # A chunk of continuation bytes alone decodes to "" — skip, don't mistake for EOF.
        text = decoder.decode(data)
        if text:
            yield text


def _assemble_bounded_lines(chunks: Iterable[str], max_line_bytes: int) -> Iterator[str]:
    """Yield complete lines from a stream of text ``chunks`` (each line ending in ``\\n``
    except possibly the last), capping any single logical line at ``max_line_bytes``.

    The bound holds *while* a line is being buffered, not after its newline arrives:
    once the pending line exceeds the cap it is flushed truncated and the remainder is
    discarded up to the next newline. Memory is therefore bounded by
    ``max_line_bytes + chunk_size`` regardless of what the producer emits."""
    pending: list[str] = []
    pending_bytes = 0
    overflowing = False
    for chunk in chunks:
        start = 0
        while True:
            nl = chunk.find("\n", start)
            if nl == -1:
                seg = chunk[start:]
                if seg and not overflowing:
                    pending.append(seg)
                    pending_bytes += _nbytes(seg)
                    if pending_bytes > max_line_bytes:
                        overflowing = True
                        # Reserve space for the marker so content + marker fits
                        # within max_line_bytes (not just content alone).
                        # max(0, ...) guards against a pathologically tiny cap.
                        pending = [_truncate_to_marker("".join(pending), max_line_bytes)]
                        pending_bytes = _nbytes(pending[0])
                break
            if overflowing:
                # pending[0] already includes the marker (set by _truncate_to_marker above).
                yield "".join(pending)
                overflowing = False
            else:
                line = "".join(pending) + chunk[start : nl + 1]
                if _nbytes(line) > max_line_bytes:
                    # strip the line's own trailing newline; the marker brings its own
                    line = _truncate_to_marker(
                        line[:-1] if line.endswith("\n") else line, max_line_bytes
                    )
                yield line
            pending = []
            pending_bytes = 0
            start = nl + 1
    if overflowing:
        # pending[0] already includes the marker (set by _truncate_to_marker above).
        yield "".join(pending)
    elif pending:
        yield "".join(pending)


def iter_bounded_lines(
    stream: TextIO, max_line_bytes: int, chunk_size: int = 65536
) -> Iterator[str]:
    """Yield complete lines from a text ``stream`` that is being **drained to EOF**,
    capping any single logical line at ``max_line_bytes``.

    Constraint: this reader **must never back an interactive stream.** It is fed by
    ``stream.read(chunk_size)``, which on a blocking pipe waits for ``chunk_size``
    characters *or EOF* — it does not return early once a newline arrives. Pointing it
    at a request/response protocol deadlocks the handshake: the response line never
    surfaces because the producer is waiting for the next request before it writes
    enough bytes to fill the chunk. Use ``iter_bounded_lines_interactive`` there.

    Correct callers run a subprocess to completion and read what it wrote."""
    return _assemble_bounded_lines(_drain_chunks(stream, chunk_size), max_line_bytes)


def iter_bounded_lines_interactive(
    stream: io.BufferedIOBase,
    max_line_bytes: int,
    chunk_size: int = 65536,
    encoding: str = "utf-8",
) -> Iterator[str]:
    """Yield complete lines from a **live** binary ``stream``, each as soon as its
    newline arrives, capping any single logical line at ``max_line_bytes``.

    For request/response protocols over a pipe whose producer is still running. Takes a
    binary stream (``Popen(..., text=False).stdout``) and owns the bytes-to-text boundary
    itself, because ``read1`` on a ``TextIOWrapper``'s underlying buffer would bypass any
    characters the wrapper had already decoded into its own buffer. Never read the same
    pipe through both a text wrapper and this reader.

    Splits on ``\\n`` only — no universal-newline translation, so a ``\\r\\n`` producer
    yields lines with a trailing ``\\r``. Undecodable bytes become U+FFFD rather than
    raising, since a diagnostic stream is not worth crashing a transfer over."""
    return _assemble_bounded_lines(_available_chunks(stream, chunk_size, encoding), max_line_bytes)


class BoundedCapture:
    """Accumulate text lines under ``max_bytes`` keeping a head window and a bounded
    tail.  ``result()`` returns ``head + tail`` when nothing was dropped, or
    ``head + marker + tail`` when at least one line was evicted because the total
    exceeded ``max_bytes``.  Truncation (and the marker) occur only when output
    actually exceeds the cap and a line is dropped; retained bytes never exceed
    ``max_bytes`` plus the marker.  Complete lines only.

    ``head_bytes`` overrides the head window (default: half of ``max_bytes``).  Pass
    ``head_bytes=0`` for a **pure rolling tail** — no head window, so the marker leads
    and only the newest lines survive.  That is the right shape when the diagnostic
    value is at the *end* of the stream (a stack trace that killed a process) rather
    than at the start.

    **Thread-safe.**  ``add()`` and ``result()`` are serialized, so a reader may snapshot
    a capture that a writer thread is still filling.  ``add()`` mutates a list, a deque
    and three counters across several statements; the GIL makes each statement atomic but
    not the sequence, so an unlocked ``result()`` can observe a pre-eviction over-budget
    state, disagree with the ``truncated`` flag, or raise ``RuntimeError: deque mutated
    during iteration`` — on the very error path trying to report a failure.  The lock is
    held only for bounded in-memory work, never across a blocking read.  Note that a
    snapshot is *consistent*, not *final*: a live stream may still grow after it."""

    def __init__(self, max_bytes: int, *, head_bytes: int | None = None) -> None:
        self._max_bytes = max_bytes
        self._head_budget = max(1, max_bytes // 2) if head_bytes is None else head_bytes
        self._head: list[str] = []
        self._head_bytes = 0
        self._tail: deque[tuple[str, int]] = deque()
        self._tail_bytes = 0
        self._truncated = False
        self._lock = threading.Lock()

    def add(self, line: str) -> None:
        n = _nbytes(line)
        with self._lock:
            # Fill the head window first.  Once any line has gone to the tail OR a line
            # has been evicted (``_truncated``), all subsequent lines follow into the
            # tail so ordering is preserved (head=earliest, tail=most-recent).  The
            # ``not self._truncated`` guard matters because eviction can empty the tail
            # again: without it a later line would slip back into the head and end up
            # before the truncation marker, ahead of output it actually followed.
            # With ``head_budget == 0`` no line ever qualifies, giving a pure tail.
            if not self._truncated and not self._tail and self._head_bytes + n <= self._head_budget:
                self._head.append(line)
                self._head_bytes += n
                return
            self._tail.append((line, n))
            self._tail_bytes += n
            # Drop oldest tail lines only when the TOTAL retained exceeds the cap.
            # Nothing is dropped (and no marker is shown) until the full cap — not
            # merely the head half — is exceeded, so any output that fits within
            # max_bytes is returned verbatim.  The len(self._tail) > 1 guard is
            # intentionally absent so even a single oversized tail line is evicted,
            # making max_bytes a hard ceiling.
            while self._head_bytes + self._tail_bytes > self._max_bytes and self._tail:
                _, dropped = self._tail.popleft()
                self._tail_bytes -= dropped
                self._truncated = True

    @property
    def truncated(self) -> bool:
        with self._lock:
            return self._truncated

    def result(self) -> str:
        with self._lock:
            head = "".join(self._head)
            tail = "".join(line for line, _ in self._tail)
            if not self._truncated:
                return head + tail
            return head + _OUTPUT_TRUNC_MARKER + tail
