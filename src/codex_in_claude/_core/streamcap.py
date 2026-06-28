"""Bounded streaming primitives shared by the subprocess runtime and diff gather.

CLI-agnostic; no parent-package imports. Two tools:

- ``iter_bounded_lines`` reads a text stream in fixed chunks and yields complete
  lines, capping any single logical line so a pathological producer cannot buffer
  an unbounded line into memory before a newline arrives. Lines are kept whole up
  to ``max_line_bytes``; a single logical line that exceeds the cap is truncated
  mid-line with a ``…[line truncated]`` marker (so a pathologically long JSONL
  line may not parse, but normal-sized lines are preserved intact).
- ``BoundedCapture`` accumulates lines under a byte budget, keeping a head window
  plus a bounded tail so the newest lines (where codex emits usage/rate-limit
  metadata) survive truncation. Complete lines only.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
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


def iter_bounded_lines(
    stream: TextIO, max_line_bytes: int, chunk_size: int = 65536
) -> Iterator[str]:
    """Yield complete lines from ``stream`` (each ending in ``\\n`` except possibly
    the last). Reads ``chunk_size`` chars at a time so a line with no newline cannot
    grow without bound: once the pending line exceeds ``max_line_bytes`` it is
    flushed truncated and the rest is discarded up to the next newline."""
    pending: list[str] = []
    pending_bytes = 0
    overflowing = False
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
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


class BoundedCapture:
    """Accumulate text lines under ``max_bytes`` keeping a head window and a bounded
    tail.  ``result()`` returns ``head + tail`` when nothing was dropped, or
    ``head + marker + tail`` when at least one line was evicted because the total
    exceeded ``max_bytes``.  Truncation (and the marker) occur only when output
    actually exceeds the cap and a line is dropped; retained bytes never exceed
    ``max_bytes`` plus the marker.  Complete lines only."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._head_budget = max(1, max_bytes // 2)
        self._head: list[str] = []
        self._head_bytes = 0
        self._tail: deque[tuple[str, int]] = deque()
        self._tail_bytes = 0
        self._truncated = False

    def add(self, line: str) -> None:
        n = _nbytes(line)
        # Fill the head window first.  Once any line has gone to the tail OR a line
        # has been evicted (``_truncated``), all subsequent lines follow into the
        # tail so ordering is preserved (head=earliest, tail=most-recent).  The
        # ``not self._truncated`` guard matters because eviction can empty the tail
        # again: without it a later line would slip back into the head and end up
        # before the truncation marker, ahead of output it actually followed.
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
        return self._truncated

    def result(self) -> str:
        head = "".join(self._head)
        tail = "".join(line for line, _ in self._tail)
        if not self._truncated:
            return head + tail
        return head + _OUTPUT_TRUNC_MARKER + tail
