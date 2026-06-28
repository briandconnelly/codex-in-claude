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
    assert len(out[0].encode("utf-8")) <= 100 + len("…[line truncated]\n".encode())
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
