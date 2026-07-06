"""Tests for the one-shot app-server session-transfer client.

The subprocess/JSONL path is exercised against a scripted fake app-server
(``tests/fake_app_server.py``) so behavior is hermetic — no live codex.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from codex_in_claude import appserver
from codex_in_claude.appserver import (
    ThreadIdSource,
    TransferStatus,
    transfer_session,
    validate_transcript_path,
)

FAKE = str(Path(__file__).parent / "fake_app_server.py")


def _transcript(tmp_path: Path, content: bytes = b'{"type":"user"}\n') -> Path:
    t = tmp_path / "session.jsonl"
    t.write_bytes(content)
    return t


def _command(scenario: str, codex_home: Path) -> list[str]:
    return [sys.executable, FAKE, scenario, str(codex_home)]


def _write_ledger(codex_home: Path, source: str, content_sha: str, thread_id: str) -> None:
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "external_agent_session_imports.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source_path": source,
                        "content_sha256": content_sha,
                        "imported_thread_id": thread_id,
                    }
                ]
            }
        )
    )


# --- validate_transcript_path ---------------------------------------------------


def test_validate_rejects_non_string():
    assert validate_transcript_path("").reason is not None


def test_validate_rejects_non_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(appserver, "CLAUDE_PROJECTS_DIR", tmp_path)
    p = tmp_path / "session.txt"
    p.write_text("x")
    result = validate_transcript_path(str(p))
    assert result.realpath is None
    assert "jsonl" in result.reason


def test_validate_rejects_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(appserver, "CLAUDE_PROJECTS_DIR", tmp_path)
    result = validate_transcript_path(str(tmp_path / "nope.jsonl"))
    assert result.realpath is None
    assert "does not exist" in result.reason


def test_validate_rejects_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(appserver, "CLAUDE_PROJECTS_DIR", tmp_path)
    p = tmp_path / "s.jsonl"
    p.write_text("")
    assert "empty" in validate_transcript_path(str(p)).reason


def test_validate_rejects_outside_projects(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(appserver, "CLAUDE_PROJECTS_DIR", projects)
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_text("data")
    result = validate_transcript_path(str(outside))
    assert result.realpath is None
    assert "under" in result.reason


def test_validate_accepts_under_projects(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "slug").mkdir(parents=True)
    monkeypatch.setattr(appserver, "CLAUDE_PROJECTS_DIR", projects)
    p = projects / "slug" / "s.jsonl"
    p.write_text("data")
    result = validate_transcript_path(str(p))
    assert result.reason is None
    assert result.realpath == str(p.resolve())


# --- transfer_session outcomes --------------------------------------------------


def test_fresh_import_returns_notification_target(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("fresh", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.OK
    assert outcome.thread_id == "thread-fresh-0001"
    assert outcome.thread_id_source is ThreadIdSource.IMPORT_NOTIFICATION
    assert outcome.codex_home == str(home)
    assert outcome.import_id == "imp-1"


def test_completed_before_response_still_resolves(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("completed_before_response", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.OK
    assert outcome.thread_id == "thread-fresh-0001"


def test_unsupported_method(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("unsupported", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.UNSUPPORTED


def test_generic_import_error_is_protocol_error(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("import_error", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert "boom" in outcome.message


def test_item_failure_carries_message(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("item_failure", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.ITEM_FAILURE
    assert "could not parse session" in outcome.message


def test_dedup_recovers_thread_from_ledger(tmp_path):
    home = tmp_path / "codex_home"
    content = b'{"type":"user","text":"hi"}\n'
    t = _transcript(tmp_path, content)
    source = str(t.resolve())
    sha = hashlib.sha256(content).hexdigest()
    _write_ledger(home, source, sha, "thread-from-ledger-99")
    outcome = transfer_session(
        transcript_realpath=source,
        cwd=str(tmp_path),
        command=_command("dedup", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.OK
    assert outcome.thread_id == "thread-from-ledger-99"
    assert outcome.thread_id_source is ThreadIdSource.LEDGER


def test_dedup_without_ledger_is_incomplete(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("dedup", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.INCOMPLETE
    assert outcome.ledger_path.endswith("external_agent_session_imports.json")


def test_dedup_ledger_sha_mismatch_is_incomplete(tmp_path):
    home = tmp_path / "codex_home"
    t = _transcript(tmp_path, b"real-content\n")
    _write_ledger(home, str(t.resolve()), "deadbeef", "thread-stale")
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("dedup", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.INCOMPLETE


def test_protocol_drift_bad_line(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("protocol_drift", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert "non-JSON" in outcome.message


def test_eof_before_completed(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("eof_after_init", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert "exited before" in outcome.message


def test_timeout(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("timeout", home),
        timeout_seconds=0.75,
    )
    assert outcome.status is TransferStatus.TIMED_OUT


def test_spawn_failed_missing_binary(tmp_path):
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=["/nonexistent/codex-binary-xyz", "app-server"],
        timeout_seconds=5,
    )
    assert outcome.status is TransferStatus.SPAWN_FAILED


# --- ledger reader edge cases ---------------------------------------------------


def test_ledger_missing_returns_none(tmp_path):
    assert appserver._lookup_ledger(str(tmp_path), str(tmp_path / "x.jsonl")) is None


def test_ledger_malformed_json_returns_none(tmp_path):
    (tmp_path / "external_agent_session_imports.json").write_text("{not json")
    t = _transcript(tmp_path)
    assert appserver._lookup_ledger(str(tmp_path), str(t)) is None


def test_ledger_non_dict_returns_none(tmp_path):
    (tmp_path / "external_agent_session_imports.json").write_text("[]")
    t = _transcript(tmp_path)
    assert appserver._lookup_ledger(str(tmp_path), str(t)) is None


def test_ledger_records_not_list_returns_none(tmp_path):
    (tmp_path / "external_agent_session_imports.json").write_text('{"records": {}}')
    t = _transcript(tmp_path)
    assert appserver._lookup_ledger(str(tmp_path), str(t)) is None


def test_ledger_oversized_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("codex_in_claude.cli_contract.IMPORT_LEDGER_MAX_BYTES", 2)
    (tmp_path / "external_agent_session_imports.json").write_text('{"records": []}')
    t = _transcript(tmp_path)
    assert appserver._lookup_ledger(str(tmp_path), str(t)) is None


def test_ledger_last_match_wins(tmp_path):
    content = b"pick-me\n"
    t = _transcript(tmp_path, content)
    source = str(t.resolve())
    sha = hashlib.sha256(content).hexdigest()
    (tmp_path / "external_agent_session_imports.json").write_text(
        json.dumps(
            {
                "records": [
                    {"source_path": source, "content_sha256": sha, "imported_thread_id": "first"},
                    {"source_path": source, "content_sha256": sha, "imported_thread_id": "last"},
                ]
            }
        )
    )
    assert appserver._lookup_ledger(str(tmp_path), source) == "last"


# --- completed-notification resolver unit cases ---------------------------------


def _resolve(params, *, source="/s.jsonl", home="/home"):
    return appserver._resolve_completed(
        params, transcript_realpath=source, codex_home=home, import_id="i1", stderr_tail=""
    )


def test_session_item_result_missing_returns_empty():
    assert appserver._session_item_result({"itemTypeResults": "nope"}) == {}
    assert appserver._session_item_result({"itemTypeResults": [{"itemType": "PLUGINS"}]}) == {}


def test_target_skips_source_mismatch():
    item = {
        "successes": [
            {"itemType": "SESSIONS", "source": "/other.jsonl", "target": "wrong"},
            {"itemType": "SESSIONS", "source": "/s.jsonl", "target": "right"},
        ]
    }
    assert appserver._target_from_successes(item, "/s.jsonl") == "right"


def test_target_accepts_unlabeled_success():
    item = {"successes": [{"itemType": "SESSIONS", "target": "t"}]}
    assert appserver._target_from_successes(item, "/s.jsonl") == "t"


def test_failure_message_joins_and_defaults():
    item = {"failures": [{"message": "a"}, {"message": "b"}, {"nope": 1}]}
    assert appserver._failure_message(item) == "a; b"
    assert appserver._failure_message({"failures": [{"nomsg": 1}]}) == (
        "Codex reported an import failure."
    )
    assert appserver._failure_message({"failures": []}) is None


def test_resolve_completed_failure_branch():
    params = {
        "itemTypeResults": [
            {"itemType": "SESSIONS", "successes": [], "failures": [{"message": "x"}]}
        ]
    }
    outcome = _resolve(params)
    assert outcome.status is TransferStatus.ITEM_FAILURE
    assert outcome.message == "x"


@pytest.mark.integration
def test_live_transfer_roundtrip(tmp_path):
    """Live: import a real transcript via the actual `codex app-server`, and confirm
    the reported thread id names an existing rollout session file. Requires a real
    Claude session transcript under ~/.claude/projects. Run with
    `pytest -m integration --no-cov`."""
    projects = Path.home() / ".claude" / "projects"
    candidates = sorted(projects.glob("*/*.jsonl")) if projects.exists() else []
    transcript = next(
        (c for c in candidates if c.stat().st_size > 2000 and _looks_like_session(c)),
        None,
    )
    if transcript is None:
        pytest.skip("no importable Claude session transcript found")
    outcome = transfer_session(
        transcript_realpath=str(transcript.resolve()),
        cwd=str(Path.cwd()),
        command=None,
        timeout_seconds=120,
    )
    assert outcome.status is TransferStatus.OK, outcome
    assert outcome.thread_id
    home = Path(outcome.codex_home or (Path.home() / ".codex"))
    matches = list(home.glob(f"sessions/**/*{outcome.thread_id}*.jsonl"))
    assert matches, f"no rollout file for thread {outcome.thread_id}"


def _looks_like_session(path: Path) -> bool:
    try:
        with path.open() as fh:
            for line in fh:
                obj = json.loads(line)
                if obj.get("type") in {"user", "assistant"}:
                    return True
    except (OSError, ValueError):
        return False
    return False
