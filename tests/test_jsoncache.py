import os
import sys
from pathlib import Path

import pytest

from codex_in_claude._core.jsoncache import read_bounded_json


def test_reads_valid_json(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    assert read_bounded_json(p, 1000) == {"a": 1}


def test_missing_file_returns_none(tmp_path: Path):
    assert read_bounded_json(tmp_path / "nope.json", 1000) is None


def test_deeply_nested_json_returns_none(tmp_path: Path):
    # Stays well under the byte cap but blows the recursion limit in json.loads,
    # which raises RecursionError (not a ValueError) — must still fall back to None.
    p = tmp_path / "nested.json"
    p.write_bytes(b"[" * 200_000)
    assert read_bounded_json(p, 1_000_000) is None


def test_directory_returns_none(tmp_path: Path):
    assert read_bounded_json(tmp_path, 1000) is None


def test_oversize_returns_none(tmp_path: Path):
    p = tmp_path / "big.json"
    p.write_text('{"a": "' + "x" * 1000 + '"}', encoding="utf-8")
    assert read_bounded_json(p, 100) is None


def test_invalid_json_returns_none(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_bounded_json(p, 1000) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses file perms")
def test_unreadable_file_returns_none(tmp_path: Path):
    p = tmp_path / "locked.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    p.chmod(0o000)
    try:
        assert read_bounded_json(p, 1000) is None
    finally:
        p.chmod(0o644)  # restore so tmp cleanup can remove it


def test_invalid_utf8_returns_none(tmp_path: Path):
    p = tmp_path / "binary.json"
    p.write_bytes(b"\xff\xfe not utf-8")
    assert read_bounded_json(p, 1000) is None
