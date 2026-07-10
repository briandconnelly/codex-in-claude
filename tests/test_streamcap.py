"""Bounded line iteration and head+tail capture."""

from __future__ import annotations

import io
import subprocess
import sys
import threading
import time

import pytest

from codex_in_claude._core import streamcap


def test_iter_bounded_lines_basic():
    stream = io.StringIO("a\nb\nc\n")
    assert list(streamcap.iter_bounded_lines(stream, max_line_bytes=1024)) == ["a\n", "b\n", "c\n"]


def test_iter_bounded_lines_no_trailing_newline():
    stream = io.StringIO("a\nb")
    assert list(streamcap.iter_bounded_lines(stream, max_line_bytes=1024)) == ["a\n", "b"]


def test_iter_bounded_lines_truncates_huge_line():
    stream = io.StringIO("x" * 10_000 + "\n" + "tail\n")
    out = list(streamcap.iter_bounded_lines(stream, max_line_bytes=100, chunk_size=64))
    assert out[0].endswith("[line truncated]\n")
    # The marker must fit WITHIN the budget: content + marker <= max_line_bytes.
    # (Previously asserted <= max_line_bytes + len(marker), allowing overshoot.)
    assert len(out[0].encode("utf-8")) <= 100
    assert out[-1] == "tail\n"  # recovery after the oversized line


def test_bounded_capture_under_budget_is_verbatim():
    cap = streamcap.BoundedCapture(max_bytes=1024)
    for line in ["a\n", "b\n", "c\n"]:
        cap.add(line)
    assert cap.result() == "a\nb\nc\n"
    assert not cap.truncated


def test_bounded_capture_keeps_head_and_tail():
    cap = streamcap.BoundedCapture(max_bytes=40)  # 20 head / ~20 tail
    for i in range(100):
        cap.add(f"line{i}\n")
    result = cap.result()
    assert cap.truncated
    assert "[output truncated]" in result
    assert result.startswith("line0\n")  # head preserved
    assert result.rstrip().endswith("line99")  # tail preserved (newest survives)
    assert len(result.encode("utf-8")) <= 40 + len(b"[output truncated]\n")


def test_bounded_capture_no_false_truncation_between_half_and_full():
    # Bug A: output between 50% and 100% of cap must NOT be reported truncated.
    # Single 60-byte line with cap=100: head budget is 50, so 60 > 50 spills to
    # tail — but 60 < 100 so nothing is dropped; result must be verbatim.
    cap = streamcap.BoundedCapture(max_bytes=100)
    line = "x" * 59 + "\n"  # 60 bytes
    cap.add(line)
    assert not cap.truncated, "60-byte line with cap=100 must not be truncated"
    assert "[output truncated]" not in cap.result()
    assert cap.result() == line

    # Also verify with multiple lines summing to ~90 bytes (between half and full).
    cap2 = streamcap.BoundedCapture(max_bytes=100)
    lines = ["a" * 29 + "\n"] * 3  # 3 x 30 bytes = 90 bytes total
    for ln in lines:
        cap2.add(ln)
    assert not cap2.truncated, "90 bytes with cap=100 must not be truncated"
    assert cap2.result() == "".join(lines)


def test_bounded_capture_hard_ceiling_on_oversized_tail_line():
    # Bug B: a single oversized tail line must be evicted so the cap is a hard ceiling.
    # head: 50-byte line; tail: 100-byte line -- old code kept ~169 bytes (1.5x cap).
    cap = streamcap.BoundedCapture(max_bytes=100)
    cap.add("h" * 49 + "\n")  # 50 bytes → fills head budget (50)
    cap.add("t" * 99 + "\n")  # 100 bytes → tail, exceeds remaining budget; must evict
    assert cap.truncated, "oversized tail must force truncation"
    marker = b"[output truncated]\n"
    assert len(cap.result().encode("utf-8", "replace")) <= 100 + len(marker)


def test_bounded_capture_no_head_reentry_after_eviction():
    # After an eviction empties the tail, a later line must NOT slip back into the
    # head ahead of the truncation marker — that would put it before output it
    # actually followed (chronological misrepresentation).
    cap = streamcap.BoundedCapture(max_bytes=100)
    cap.add("a" * 39 + "\n")  # 40 bytes → head (<= head budget 50)
    cap.add("b" * 99 + "\n")  # 100 bytes → tail, total 140 > 100 → evicts itself, truncated
    cap.add("c" * 9 + "\n")  # 10 bytes → must go to the tail (after the marker), not head
    assert cap.truncated
    result = cap.result()
    marker = "[output truncated]\n"
    assert marker in result
    # The later "c" line must appear AFTER the marker, not adjacent to the "a" line.
    assert result.index("a") < result.index(marker) < result.index("c")


def test_iter_bounded_lines_truncates_within_chunk_line():
    # Fix 1 regression: a line whose newline falls within the current chunk but whose
    # length exceeds max_line_bytes must be truncated, not yielded whole.
    # chunk_size=64, max_line_bytes=20: "x"*40 + "\n" is 41 bytes — fits in one chunk
    # but exceeds the cap. Before fix: yielded whole (41 bytes). After fix: truncated.
    data = "x" * 40 + "\n" + "ok\n"
    stream = io.StringIO(data)
    out = list(streamcap.iter_bounded_lines(stream, max_line_bytes=20, chunk_size=64))
    first = out[0]
    assert len(first.encode("utf-8")) <= 20, (
        f"first line exceeds max_line_bytes: {len(first.encode('utf-8'))} bytes"
    )
    assert "[line truncated]" in first, f"no truncation marker: {first!r}"
    assert out[-1] == "ok\n"


# --- iter_bounded_lines_interactive -------------------------------------------------
#
# The drain reader (`iter_bounded_lines`) is fed by `stream.read(n)`, which on a blocking
# pipe waits for n characters OR EOF. The interactive reader must instead yield a line as
# soon as its newline arrives, while the producer is still running.

# Emits one line, flushes, then stays alive. `read(n)` blocks here; `read1(n)` does not.
_ONE_LINE_THEN_SLEEP = (
    "import sys, time; sys.stdout.write('{\"id\":1}\\n'); sys.stdout.flush(); time.sleep(30)"
)


def _first_line_within(stream, timeout, **kwargs):
    """Pull one line from the interactive reader on a worker thread; None if it blocks."""
    box: dict[str, str] = {}
    lines = streamcap.iter_bounded_lines_interactive(stream, **kwargs)
    worker = threading.Thread(target=lambda: box.update(line=next(lines)), daemon=True)
    worker.start()
    worker.join(timeout)
    return box.get("line")


def test_interactive_yields_line_before_eof():
    # The regression the drain reader cannot pass: the child holds the pipe open, so the
    # line must surface on its newline, not on EOF.
    proc = subprocess.Popen([sys.executable, "-c", _ONE_LINE_THEN_SLEEP], stdout=subprocess.PIPE)
    try:
        assert _first_line_within(proc.stdout, timeout=10, max_line_bytes=1024) == '{"id":1}\n'
    finally:
        proc.kill()
        proc.wait()


def test_interactive_drain_reader_would_block_on_the_same_pipe():
    # Pins the constraint documented on iter_bounded_lines: read(n) waits for n or EOF,
    # so the drain reader never surfaces this line while the producer lives.
    proc = subprocess.Popen(
        [sys.executable, "-c", _ONE_LINE_THEN_SLEEP], stdout=subprocess.PIPE, text=True
    )
    try:
        box: dict[str, str] = {}
        lines = streamcap.iter_bounded_lines(proc.stdout, max_line_bytes=1024)
        worker = threading.Thread(target=lambda: box.update(line=next(lines)), daemon=True)
        worker.start()
        worker.join(2)
        assert worker.is_alive(), "read(n) returned early; the drain/interactive split is moot"
    finally:
        proc.kill()
        proc.wait()


def test_interactive_basic_and_partial_final_line():
    stream = io.BytesIO(b"a\nb\nc")
    out = list(streamcap.iter_bounded_lines_interactive(stream, max_line_bytes=1024))
    assert out == ["a\n", "b\n", "c"]


def test_interactive_empty_stream_yields_nothing():
    assert list(streamcap.iter_bounded_lines_interactive(io.BytesIO(b""), max_line_bytes=64)) == []


def test_interactive_decodes_multibyte_split_across_chunks():
    # chunk_size=1 forces every multibyte character to straddle a chunk boundary; a
    # non-incremental decoder mangles these into replacement characters.
    stream = io.BytesIO("héllo → wörld\n".encode())
    out = list(streamcap.iter_bounded_lines_interactive(stream, max_line_bytes=1024, chunk_size=1))
    assert out == ["héllo → wörld\n"]


def test_interactive_truncates_huge_line_and_recovers():
    stream = io.BytesIO(b"x" * 10_000 + b"\ntail\n")
    out = list(streamcap.iter_bounded_lines_interactive(stream, max_line_bytes=100, chunk_size=64))
    assert out[0].endswith("[line truncated]\n")
    assert len(out[0].encode("utf-8")) <= 100
    assert out[-1] == "tail\n"  # recovery after the oversized line


def test_interactive_bounds_memory_of_unterminated_line():
    # The bound must hold while the line is still being buffered, not after its newline
    # arrives — that is the defect in appserver's post-hoc len(stripped) > cap check.
    seen: list[int] = []

    class _Spy(io.BytesIO):
        def read1(self, size=-1):
            seen.append(size)
            return super().read1(size)

    stream = _Spy(b"y" * 500_000)  # no newline, ever
    out = list(streamcap.iter_bounded_lines_interactive(stream, max_line_bytes=100, chunk_size=64))
    assert seen, "reader must use read1(), not read()"
    assert len(out) == 1
    assert len(out[0].encode("utf-8")) <= 100


def test_interactive_preserves_carriage_return():
    # Splits on LF only — no universal-newline translation. Callers strip \r themselves.
    stream = io.BytesIO(b"a\r\n")
    assert list(streamcap.iter_bounded_lines_interactive(stream, max_line_bytes=64)) == ["a\r\n"]


# --- BoundedCapture: pure tail + thread safety --------------------------------------


def test_bounded_capture_pure_tail_drops_head():
    # head_bytes=0 turns the capture into a pure rolling tail: no head window, so the
    # marker leads and only the newest lines survive. This is what a field named
    # `stderr_tail` actually promises.
    cap = streamcap.BoundedCapture(max_bytes=40, head_bytes=0)
    for i in range(100):
        cap.add(f"line{i}\n")
    result = cap.result()
    assert cap.truncated
    assert result.startswith("[output truncated]\n"), result
    assert "line0\n" not in result  # earliest dropped, not preserved as a head window
    assert result.rstrip().endswith("line99")  # newest survives


def test_bounded_capture_pure_tail_verbatim_under_budget():
    cap = streamcap.BoundedCapture(max_bytes=1024, head_bytes=0)
    for line in ["a\n", "b\n"]:
        cap.add(line)
    assert not cap.truncated
    assert cap.result() == "a\nb\n"  # no marker when nothing was dropped


def test_bounded_capture_pure_tail_budgets_in_bytes_not_characters():
    # Each "é" is 2 UTF-8 bytes. A character-counting cap would retain ~2x the budget.
    cap = streamcap.BoundedCapture(max_bytes=100, head_bytes=0)
    for _ in range(50):
        cap.add("é" * 10 + "\n")  # 21 bytes per line
    retained = cap.result().replace("[output truncated]\n", "")
    assert len(retained.encode("utf-8")) <= 100


def test_bounded_capture_snapshot_is_safe_while_a_writer_adds():
    # Regression: result() iterates self._tail while add() appends/poplefts it. Without
    # a lock this raises `RuntimeError: deque mutated during iteration` — and it does so
    # on the very error path that is trying to report a failure.
    cap = streamcap.BoundedCapture(max_bytes=4096)
    stop = threading.Event()
    failures: list[BaseException] = []

    def writer() -> None:
        i = 0
        while not stop.is_set():
            cap.add(f"line{i}\n")
            i += 1

    def reader() -> None:
        try:
            while not stop.is_set():
                # A consistent snapshot must never exceed the budget mid-eviction.
                out = cap.result().replace("[output truncated]\n", "")
                assert len(out.encode("utf-8")) <= 4096
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=writer, daemon=True) for _ in range(2)]
    threads.append(threading.Thread(target=reader, daemon=True))
    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(5)
    assert not failures, f"snapshot raced the writer: {failures[0]!r}"


def test_bounded_capture_rejects_head_bytes_outside_the_budget():
    # A head window larger than the total budget is never evicted, so `add()` grows the
    # head past max_bytes and `truncated` stays False — the ceiling is silently violated
    # rather than enforced. Reject it at construction instead of retaining 15x the cap.
    with pytest.raises(ValueError, match="head_bytes"):
        streamcap.BoundedCapture(max_bytes=100, head_bytes=101)
    with pytest.raises(ValueError, match="head_bytes"):
        streamcap.BoundedCapture(max_bytes=100, head_bytes=-1)


def test_bounded_capture_accepts_head_bytes_at_the_boundaries():
    assert streamcap.BoundedCapture(max_bytes=100, head_bytes=0) is not None
    assert streamcap.BoundedCapture(max_bytes=100, head_bytes=100) is not None
