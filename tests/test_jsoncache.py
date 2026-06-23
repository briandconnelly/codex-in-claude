from pathlib import Path

from codex_in_claude._core.jsoncache import read_bounded_json


def test_reads_valid_json(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    assert read_bounded_json(p, 1000) == {"a": 1}


def test_missing_file_returns_none(tmp_path: Path):
    assert read_bounded_json(tmp_path / "nope.json", 1000) is None


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
