"""Bounded streaming primitives shared by the subprocess runtime and diff gather.

CLI-agnostic; no parent-package imports. Two tools:

- ``iter_bounded_lines`` reads a text stream in fixed chunks and yields complete
  lines, capping any single logical line so a pathological producer cannot buffer
  an unbounded line into memory before a newline arrives.
- ``BoundedCapture`` accumulates lines under a byte budget, keeping a head window
  plus a bounded tail so the newest lines (where codex emits usage/rate-limit
  metadata) survive truncation. Complete lines only — never a mid-line cut — so
  JSONL stays parseable.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import TextIO

_LINE_TRUNC_MARKER = "…[line truncated]\n"
_OUTPUT_TRUNC_MARKER = "[output truncated]\n"


def _nbytes(text: str) -> int:
    return len(text.encode("utf-8", "replace"))


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
                        # Truncate pending to max_line_bytes bytes so the
                        # emitted line (content + marker) stays bounded.
                        encoded = "".join(pending).encode("utf-8", "replace")
                        pending = [encoded[:max_line_bytes].decode("utf-8", "ignore")]
                        pending_bytes = _nbytes(pending[0])
                break
            if overflowing:
                yield "".join(pending) + _LINE_TRUNC_MARKER
                overflowing = False
            else:
                pending.append(chunk[start : nl + 1])
                yield "".join(pending)
            pending = []
            pending_bytes = 0
            start = nl + 1
    if overflowing:
        yield "".join(pending) + _LINE_TRUNC_MARKER
    elif pending:
        yield "".join(pending)


class BoundedCapture:
    """Accumulate text lines under ``max_bytes`` keeping a head window and a bounded
    tail. ``result()`` returns ``head + marker + tail`` (marker omitted when not
    truncated). Complete lines only."""

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
        if not self._truncated and self._head_bytes + n <= self._head_budget:
            self._head.append(line)
            self._head_bytes += n
            return
        self._truncated = True
        self._tail.append((line, n))
        self._tail_bytes += n
        tail_budget = self._max_bytes - self._head_bytes
        while self._tail_bytes > tail_budget and len(self._tail) > 1:
            _, dropped = self._tail.popleft()
            self._tail_bytes -= dropped

    @property
    def truncated(self) -> bool:
        return self._truncated

    def result(self) -> str:
        head = "".join(self._head)
        if not self._truncated:
            return head
        tail = "".join(line for line, _ in self._tail)
        return head + _OUTPUT_TRUNC_MARKER + tail
