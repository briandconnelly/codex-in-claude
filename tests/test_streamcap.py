"""Bounded line iteration and head+tail capture."""

from __future__ import annotations

import io

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
