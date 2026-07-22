"""Tests for the one-shot app-server session-transfer client.

The subprocess/JSONL path is exercised against a scripted fake app-server
(``tests/fake_app_server.py``) so behavior is hermetic — no live codex.
"""

from __future__ import annotations

import hashlib
import io
import json
import queue
import sys
import threading
import time
from pathlib import Path

import pytest
from tests.fake_app_server import LEAKY_MESSAGE, LONG_CODEX_HOME, OVERSIZED_CODEX_HOME, SECRET

from codex_in_claude import appserver, cli_contract
from codex_in_claude._core import streamcap
from codex_in_claude.appserver import (
    RateLimitReadStatus,
    ThreadIdSource,
    TransferStatus,
    read_rate_limits,
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


def _command_logged(scenario: str, codex_home: Path, log_path: Path) -> list[str]:
    return [sys.executable, FAKE, scenario, str(codex_home), str(log_path)]


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


def test_validate_rejects_empty_string():
    assert validate_transcript_path("").reason is not None


def test_validate_rejects_non_string():
    # A genuinely non-string input exercises the isinstance guard (not the empty-string
    # branch). tests are not type-checked, so passing None here is intentional.
    assert validate_transcript_path(None).reason is not None  # ty: ignore


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


def test_validate_rejects_embedded_nul(tmp_path, monkeypatch):
    # An embedded NUL makes Path.resolve() raise ValueError on every supported Python
    # (#278). It must surface as a reason (-> invalid_arguments), never escape as an
    # internal_error, and must not echo the offending path back.
    monkeypatch.setattr(appserver, "CLAUDE_PROJECTS_DIR", tmp_path)
    marker = "SECRETSLUG"
    result = validate_transcript_path(f"{tmp_path}/{marker}\x00b.jsonl")
    assert result.realpath is None
    assert result.reason is not None
    assert marker not in result.reason


def test_validate_rejects_symlink_loop(tmp_path, monkeypatch):
    # A symlink loop makes Path.resolve() raise RuntimeError on CPython 3.11/3.12 and
    # resolve to a non-file on 3.13/3.14 (#278). The rejection is required on every
    # supported Python even though the underlying exception is version-dependent, so
    # this assertion is deliberately version-independent.
    monkeypatch.setattr(appserver, "CLAUDE_PROJECTS_DIR", tmp_path)
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.symlink_to(b)
    b.symlink_to(a)
    result = validate_transcript_path(str(a))
    assert result.realpath is None
    assert result.reason is not None


def test_validate_rejects_unstatable_path(tmp_path, monkeypatch):
    # is_file() re-raises non-ignored OSErrors (e.g. PermissionError/EACCES) on CPython
    # 3.11-3.13 rather than swallowing them (#278). Such an OSError must surface as a
    # reason, not escape as a retryable internal_error. Monkeypatched so the OSError
    # branch is reached deterministically on every Python (3.14 swallows a real EACCES).
    from pathlib import Path as _Path

    monkeypatch.setattr(appserver, "CLAUDE_PROJECTS_DIR", tmp_path)

    def _raise_eacces(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(_Path, "is_file", _raise_eacces)
    result = validate_transcript_path(str(tmp_path / "s.jsonl"))
    assert result.realpath is None
    assert result.reason is not None


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


@pytest.mark.parametrize("scenario", ["oversized_target", "control_target", "null_target"])
def test_invalid_notification_target_is_protocol_error(tmp_path, scenario):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command(scenario, home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert "thread id" in outcome.message
    assert "t" * 5000 not in outcome.message  # value-free


def test_invalid_live_target_beats_valid_ledger(tmp_path):
    home = tmp_path / "codex_home"
    content = b'{"type":"user","text":"hi"}\n'
    t = _transcript(tmp_path, content)
    source = str(t.resolve())
    sha = hashlib.sha256(content).hexdigest()
    _write_ledger(home, source, sha, "thread-from-ledger-OK")
    outcome = transfer_session(
        transcript_realpath=source,
        cwd=str(tmp_path),
        command=_command("invalid_target_with_ledger", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR  # NOT recovered from the ledger
    assert outcome.thread_id is None


def test_absent_target_key_falls_through_to_ledger(tmp_path):
    home = tmp_path / "codex_home"
    content = b'{"type":"user","text":"hi"}\n'
    t = _transcript(tmp_path, content)
    source = str(t.resolve())
    sha = hashlib.sha256(content).hexdigest()
    _write_ledger(home, source, sha, "thread-recovered-77")
    outcome = transfer_session(
        transcript_realpath=source,
        cwd=str(tmp_path),
        command=_command("target_key_absent", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.OK
    assert outcome.thread_id == "thread-recovered-77"
    assert outcome.thread_id_source is ThreadIdSource.LEDGER


def test_multiple_successes_any_invalid_target_fails_loud(tmp_path):
    # Two matching success entries: one with a VALID target, one with a present-but-invalid
    # (oversized) target. The invalid entry must poison the whole lookup — the valid entry
    # must NOT win — per the tri-state rule in `appserver._target_from_successes`.
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("mixed_valid_invalid_targets", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert outcome.thread_id is None
    assert "thread id" in outcome.message
    assert "t" * 5000 not in outcome.message  # value-free


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


def test_application_import_error_is_item_failure(tmp_path):
    # An application-range JSON-RPC error code on the import request is a genuine import
    # rejection → ITEM_FAILURE (server -> transfer_failed).
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("import_error", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.ITEM_FAILURE
    assert "boom" in outcome.message


def test_invalid_params_import_error_is_protocol_error(tmp_path):
    # A reserved-range code (-32602 invalid params) means our request drifted →
    # PROTOCOL_ERROR (server -> cli_contract_changed), not a transcript failure.
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("invalid_params", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert "invalid params" in outcome.message


def test_malformed_import_error_is_protocol_error(tmp_path):
    # An error object with no integer code is treated as protocol drift.
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("malformed_error", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR


@pytest.mark.parametrize("scenario", ["float_method_not_found", "bool_code"])
def test_non_integer_import_error_code_is_protocol_error(tmp_path, scenario):
    """A JSON number that decodes to float, or a JSON `true`, is not an integer code.

    Python makes both dangerous: `-32601.0 == -32601` is True (so a float would reach the
    method-not-found branch and be reported as `transfer_unsupported` — "update codex" —
    for what is really a malformed response), and `bool` is a subclass of `int` (so `True`
    would satisfy an isinstance check and fall through to `transfer_failed`, blaming the
    transcript). Both are malformed responses: protocol drift, nothing else."""
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command(scenario, home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (-32601, TransferStatus.UNSUPPORTED),  # method-not-found wins over the range
        (-32768, TransferStatus.PROTOCOL_ERROR),  # reserved, inclusive lower bound
        (-32602, TransferStatus.PROTOCOL_ERROR),  # invalid params
        (-32000, TransferStatus.PROTOCOL_ERROR),  # reserved, inclusive upper bound
        (-31999, TransferStatus.ITEM_FAILURE),  # first application-range code below
        (42, TransferStatus.ITEM_FAILURE),
        (0, TransferStatus.ITEM_FAILURE),
        (-32601.0, TransferStatus.PROTOCOL_ERROR),  # float, not an int code
        (True, TransferStatus.PROTOCOL_ERROR),  # bool is a subclass of int
        (None, TransferStatus.PROTOCOL_ERROR),  # absent code
        ("-32601", TransferStatus.PROTOCOL_ERROR),  # string, not an int code
    ],
)
def test_classify_import_error_boundaries(code, expected):
    """Pin the reserved/application split at its exact inclusive bounds (#256)."""
    assert appserver.classify_import_error(code) is expected


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


def test_oversized_import_id_drops_to_none_but_run_succeeds(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("oversized_import_id", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.OK
    assert outcome.thread_id == "thread-fresh-0001"
    assert outcome.import_id is None  # non-load-bearing → dropped, not fatal


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


def test_oversized_line_is_protocol_drift(tmp_path):
    # A line past _MAX_LINE_BYTES is bounded by the reader (truncated with a marker), so
    # it fails to parse and lands as protocol drift. Guards the memory bound: the old
    # post-hoc `len(stripped) > _MAX_LINE_BYTES` check ran only after the whole line had
    # already been buffered, so it capped nothing.
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("flood_line", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert "non-JSON" in outcome.message


# --- reader: filtering + bounded queue + shutdown safety (#277) -----------------


def test_relevant_to_loop_retains_actionable_and_drops_noise():
    # The reader's admission filter must mirror the loop's own checks EXACTLY: keep the
    # handshake (id 1), the import response/error (id 2), and the terminal completed
    # notification; drop progress notifications, unknown methods, other ids, and non-dicts
    # so they never accumulate in the queue.
    completed = cli_contract.APP_SERVER_IMPORT_COMPLETED_NOTIFICATION
    assert appserver._relevant_to_loop({"id": 1, "result": {}})
    assert appserver._relevant_to_loop({"id": 2, "error": {}})
    assert appserver._relevant_to_loop({"id": 1})  # id match alone, even sans result/error
    assert appserver._relevant_to_loop({"method": completed, "params": {}})
    # Dropped: chatty-but-ignored traffic.
    assert not appserver._relevant_to_loop({"method": "externalAgentConfig/import/progress"})
    assert not appserver._relevant_to_loop({"id": 3, "result": {}})
    assert not appserver._relevant_to_loop({"jsonrpc": "2.0"})
    assert not appserver._relevant_to_loop(["not", "a", "dict"])
    assert not appserver._relevant_to_loop("scalar")


def test_reader_filters_progress_and_surfaces_only_relevant(tmp_path):
    # A flood of progress notifications, then one completed. Only the completed (plus the
    # _EOF sentinel) should ever reach the queue — the progress traffic is filtered at the
    # reader so it cannot accumulate.
    completed = {"method": cli_contract.APP_SERVER_IMPORT_COMPLETED_NOTIFICATION, "params": {}}
    progress = {"method": "externalAgentConfig/import/progress"}
    lines = [json.dumps(progress).encode() + b"\n" for _ in range(500)]
    lines.append(json.dumps(completed).encode() + b"\n")
    reader = appserver._spawn_reader(io.BytesIO(b"".join(lines)))
    drained = []
    while True:
        msg = reader.messages.get(timeout=2)
        if msg is appserver._EOF:
            break
        drained.append(msg)
    assert drained == [completed]


def test_reader_thread_exits_when_consumer_abandons_a_full_queue():
    # With a bounded queue and no draining, the reader blocks on put(). Setting the stop
    # event must release it so it exits promptly rather than stranding a daemon thread
    # (one per transfer) forever. All messages are relevant (id 2) so filtering can't be
    # what drains the flood.
    payload = b"".join(json.dumps({"id": 2, "result": {}}).encode() + b"\n" for _ in range(1000))
    reader = appserver._spawn_reader(io.BytesIO(payload))
    # Never drain. The bounded queue fills and the reader parks in put().
    reader.stop.set()
    reader.thread.join(timeout=2)
    assert not reader.thread.is_alive()


def test_put_or_stop_returns_without_enqueue_when_already_stopped():
    # The fast-path guard: a producer must not enqueue once stop is set, even into a queue
    # with room. This is what keeps a post-return flood from re-growing the queue.
    q: queue.Queue[object] = queue.Queue(maxsize=1)
    stop = threading.Event()
    stop.set()
    appserver._put_or_stop(q, stop, "item")
    assert q.empty()


def test_progress_flood_still_resolves_ok(tmp_path):
    # End-to-end: a valid app-server that emits thousands of progress notifications before
    # completing must still land OK — filtering + the bounded queue are transparent to a
    # chatty-but-well-behaved server.
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("progress_flood", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.OK, outcome
    assert outcome.thread_id


def _flood_outcome(tmp_path, scenario):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    return transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command(scenario, home),
        timeout_seconds=15,
    )


def test_stderr_tail_retains_the_end_not_the_beginning(tmp_path):
    # #254/#275: the tail must retain the END — the line that killed the child, not the
    # startup noise. The outcome now carries the display *projection* (#275): redacted,
    # capped to _MAX_DISPLAY_CHARS, with the truncation marker at the START (not
    # _display_text's head-keeping cut).
    outcome = _flood_outcome(tmp_path, "stderr_flood")
    tail = outcome.stderr_tail or ""
    assert "FINAL-SENTINEL" in tail, "the last stderr line was dropped — still a prefix"
    assert "EARLY-SENTINEL" not in tail, "the first stderr line survived — still a prefix"
    assert tail.startswith(appserver._DISPLAY_TRUNC_MARKER), tail[:80]
    assert len(tail) <= appserver._MAX_DISPLAY_CHARS


def test_stderr_tail_display_projection_is_char_bounded_even_with_unicode(tmp_path):
    # #275: the surfaced tail is bounded by _MAX_DISPLAY_CHARS *characters* — a token
    # budget for the agent — independent of the drain's 64KB *byte* capture, and non-ASCII
    # stderr must not let it grow. (The raw drain's byte budget, #254's second defect, is
    # covered directly on BoundedCapture in test_streamcap.)
    outcome = _flood_outcome(tmp_path, "stderr_flood_unicode")
    tail = outcome.stderr_tail or ""
    assert "FINAL-SENTINEL" in tail
    assert len(tail) <= appserver._MAX_DISPLAY_CHARS


def test_stderr_tail_never_leaks_a_secret_split_by_the_per_line_cap():
    # #275 (Codex review): the reader's per-line cap must exceed the capture cap, so a line
    # long enough to be split mid-token is evicted WHOLE by BoundedCapture rather than kept
    # with a split secret the redactor can't match. Here a single secret is positioned to
    # STRADDLE the reader's 64 KiB per-line cut, so a truncate-before-redact bug retains only
    # its unmatchable `sk-bbb…` prefix (needs 20+ chars) right before the truncation marker —
    # inside the last-300-char display window. With the reader cap above the capture cap the
    # whole line is evicted instead, so no `sk-` fragment survives. (The oversized line is the
    # SOLE content: a trailing line would evict the split line and mask the leak.)
    content_limit = appserver._MAX_STDERR_BYTES - streamcap._LINE_TRUNC_MARKER_BYTES
    # SECRET straddles the reader's cut at content_limit: its sk- prefix lands just inside.
    huge = "x" * (content_limit - 10) + SECRET + "x" * 500
    drain = appserver._StderrDrain(io.BytesIO((huge + "\n").encode("utf-8")))
    prev = None
    for _ in range(500):  # wait for the daemon reader to drain the BytesIO to EOF and settle
        snap = drain.snapshot()
        if snap and snap == prev:
            break
        prev = snap
        time.sleep(0.01)
    out = appserver._display_stderr_tail(drain.snapshot()) or ""
    assert "sk-" not in out, "a secret fragment from a split oversized line leaked"
    assert SECRET not in out


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
    # #275 landmine: the spawn failed, so no child and no stderr ever existed. The tail must
    # be None — never the internal BINARY_NOT_FOUND sentinel, which a future envelope path
    # could otherwise surface to the agent as if it were child diagnostics.
    assert outcome.stderr_tail is None


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


def test_ledger_skips_invalid_id_last_valid_match_wins(tmp_path):
    # Two records match source+sha: an older VALID id, then a newer INVALID (oversized) id.
    # The invalid newest is filtered; the older valid id is recovered (last VALID match wins).
    home = tmp_path / "codex_home"
    home.mkdir(parents=True, exist_ok=True)
    content = b'{"type":"user","text":"hi"}\n'
    t = _transcript(tmp_path, content)
    source = str(t.resolve())
    sha = hashlib.sha256(content).hexdigest()
    (home / "external_agent_session_imports.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source_path": source,
                        "content_sha256": sha,
                        "imported_thread_id": "thread-older-valid",
                    },
                    {
                        "source_path": source,
                        "content_sha256": sha,
                        "imported_thread_id": "t" * 5000,
                    },
                ]
            }
        )
    )
    outcome = transfer_session(
        transcript_realpath=source,
        cwd=str(tmp_path),
        command=_command("dedup", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.OK
    assert outcome.thread_id == "thread-older-valid"
    assert outcome.thread_id_source is ThreadIdSource.LEDGER


def test_initialize_error_is_protocol_error(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("init_error", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert "initialize failed" in outcome.message


def test_initialize_without_codex_home_is_protocol_error(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("init_no_home", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert "codexHome" in outcome.message
    # An omitted key is a distinct drift mode from a present-but-invalid value.
    assert "omitted" in outcome.message


@pytest.mark.parametrize(
    "scenario", ["relative_home", "control_home", "surrogate_home", "oversized_home"]
)
def test_invalid_codex_home_is_protocol_error(tmp_path, scenario):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command(scenario, home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert "codexHome" in outcome.message
    # A present-but-invalid value is a distinct drift mode from an omitted key.
    assert "invalid" in outcome.message
    # value-free: the invalid value never reaches the message.
    assert OVERSIZED_CODEX_HOME not in outcome.message
    assert "\x00" not in outcome.message


def test_invalid_codex_home_stops_before_import_request(tmp_path):
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    log = tmp_path / "methods.log"
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command_logged("relative_home", home, log),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    received = log.read_text(encoding="utf-8").split() if log.exists() else []
    assert "initialize" in received
    assert "externalAgentConfig/import" not in received  # never imported into the dark
    assert "initialized" not in received


def test_stop_event_cancels_promptly(tmp_path):
    """A set stop_event tears the run down well before the deadline, and the child
    process is reaped (cooperative cancellation)."""

    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    stop = threading.Event()
    result: list = []

    def _run() -> None:
        result.append(
            transfer_session(
                transcript_realpath=str(t.resolve()),
                cwd=str(tmp_path),
                command=_command("timeout", home),  # hangs, never completes
                timeout_seconds=30,
                stop_event=stop,
            )
        )

    worker = threading.Thread(target=_run)
    worker.start()
    time.sleep(0.5)  # let it reach the read loop
    stop.set()
    worker.join(timeout=5)
    assert not worker.is_alive()  # returned well before the 30s deadline
    assert result and result[0].status is TransferStatus.TIMED_OUT


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
    assert appserver._target_from_successes(item, "/s.jsonl").target == "right"


def test_target_accepts_unlabeled_success():
    item = {"successes": [{"itemType": "SESSIONS", "target": "t"}]}
    assert appserver._target_from_successes(item, "/s.jsonl").target == "t"


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


# --- app-server text is redacted and bounded before display (#276) ---------------

CAP = appserver._MAX_DISPLAY_CHARS
MARKER = appserver._DISPLAY_TRUNC_MARKER


def test_display_text_passes_short_text_through_unchanged():
    assert appserver._display_text("could not parse session") == "could not parse session"


@pytest.mark.parametrize("length", [0, 1, CAP - 1, CAP])
def test_display_text_leaves_text_at_or_under_the_cap_intact(length):
    # The whole in-bounds domain, not just the lengths the call sites happen to produce:
    # `CAP` is the last length that must survive verbatim, with no marker.
    text = "y" * length
    out = appserver._display_text(text)
    assert out == text
    assert MARKER not in out


@pytest.mark.parametrize("length", [CAP + 1, CAP + 500, 9_000])
def test_display_text_bounds_over_cap_text_and_says_so(length):
    # The marker is reserved INSIDE the budget: the result never exceeds CAP, and an agent
    # can tell a clipped diagnostic from a complete one.
    out = appserver._display_text("y" * length)
    assert len(out) == CAP
    assert out.endswith(MARKER)


def test_display_text_redacts_secret_shaped_values():
    out = appserver._display_text(f"auth failed for {SECRET}")
    assert SECRET not in out
    assert "[redacted: secret value]" in out


def test_display_text_redacts_before_truncating():
    """Redaction must run first: truncating first can split a secret so no pattern
    matches, publishing its prefix.

    The secret must STRADDLE the cut point for this to discriminate — placed wholly after
    it, a truncate-then-redact implementation drops the secret and passes for the wrong
    reason. Starting 10 chars before the cut leaves `sk-bbbbbbb` in a truncate-first
    result, and nothing in a redact-first one."""
    cut = CAP - len(MARKER)
    out = appserver._display_text("y" * (cut - 10) + SECRET + "z" * 100)
    assert "sk-" not in out, "a partial secret survived — truncation ran before redaction"
    assert len(out) == CAP


def test_display_text_coerces_non_string_input():
    # Wire values are `.get()`-ed off untrusted JSON: `message` may be any JSON type.
    assert appserver._display_text(None) == ""
    assert appserver._display_text(1234) == "1234"
    assert appserver._display_text({"a": 1}) == "{'a': 1}"
    assert len(appserver._display_text(["y" * 9_000])) == CAP


def test_display_stderr_tail_none_and_empty_return_none():
    # Callers branch on 'is there a diagnostic', so no-tail collapses to None (not "").
    assert appserver._display_stderr_tail(None) is None
    assert appserver._display_stderr_tail("") is None


def test_display_stderr_tail_short_text_passes_through_unchanged():
    assert appserver._display_stderr_tail("panic: config missing") == "panic: config missing"


def test_display_stderr_tail_keeps_the_end_with_marker_at_start():
    # Opposite of _display_text: stderr_tail's signal is the terminal exception line, so
    # keep the END and drop the oldest output, marking the cut at the START.
    text = "OLDEST-STARTUP-NOISE\n" + "x" * (CAP * 2) + "\nNEWEST-CRASH-LINE"
    out = appserver._display_stderr_tail(text)
    assert out is not None
    assert out.endswith("NEWEST-CRASH-LINE"), "the terminal line must survive"
    assert "OLDEST-STARTUP-NOISE" not in out, "the oldest output must be dropped, not the newest"
    assert out.startswith(MARKER)
    assert len(out) <= CAP


def test_display_stderr_tail_redacts_secret_shaped_values():
    out = appserver._display_stderr_tail(f"auth failed for {SECRET}")
    assert out is not None
    assert SECRET not in out
    assert "[redacted: secret value]" in out


def test_display_stderr_tail_redacts_before_truncating():
    """Redaction must run on the FULL capture before the tail cut. A secret straddling the
    kept-window boundary would otherwise leave its unredacted suffix (`bbbb…`) in the tail
    under a truncate-first implementation; redact-first replaces it whole. The `sk-` prefix
    always falls in the dropped head here, so it cannot be the discriminator — the surviving
    `b`-run is."""
    keep = CAP - len(MARKER)
    text = "y" * 200 + SECRET + "z" * (keep - 15)
    out = appserver._display_stderr_tail(text) or ""
    assert "b" * 12 not in out, "a partial secret survived — truncation ran before redaction"
    assert len(out) <= CAP


# --- identifier validation helpers ---------------------------------------------


SURROGATE = "\ud800"  # JSON-legal, decodes fine, raises on .encode("utf-8")


def test_has_control_char_detects_cc_category():
    assert appserver._has_control_char("a\x00b")  # C0 NUL
    assert appserver._has_control_char("a\x7fb")  # DEL
    assert appserver._has_control_char("a\x85b")  # C1
    assert not appserver._has_control_char("normal-id_1.2")
    assert not appserver._has_control_char("café")  # non-ASCII letters are fine


def test_valid_wire_id_accepts_plain_id():
    result = appserver._valid_wire_id("thread-abc_123", cli_contract.TRANSFER_ID_MAX_BYTES)
    assert result == "thread-abc_123"


@pytest.mark.parametrize("bad", ["", 0, None, [], {}, b"bytes"])
def test_valid_wire_id_rejects_empty_and_non_str(bad):
    assert appserver._valid_wire_id(bad, cli_contract.TRANSFER_ID_MAX_BYTES) is None


def test_valid_wire_id_rejects_control_and_surrogate():
    assert appserver._valid_wire_id("a\x00b", cli_contract.TRANSFER_ID_MAX_BYTES) is None
    assert appserver._valid_wire_id(SURROGATE, cli_contract.TRANSFER_ID_MAX_BYTES) is None


def test_valid_wire_id_enforces_byte_bound_not_char_count():
    at_cap = "z" * cli_contract.TRANSFER_ID_MAX_BYTES
    assert appserver._valid_wire_id(at_cap, cli_contract.TRANSFER_ID_MAX_BYTES) == at_cap
    over_cap = "z" * (cli_contract.TRANSFER_ID_MAX_BYTES + 1)
    assert appserver._valid_wire_id(over_cap, cli_contract.TRANSFER_ID_MAX_BYTES) is None
    # A 2-byte char at the boundary is measured in BYTES, not characters.
    two_byte = "é" * ((cli_contract.TRANSFER_ID_MAX_BYTES // 2) + 1)
    assert appserver._valid_wire_id(two_byte, cli_contract.TRANSFER_ID_MAX_BYTES) is None


def test_valid_codex_home_requires_absolute():
    assert appserver._valid_codex_home("/home/u/.codex") == "/home/u/.codex"
    assert appserver._valid_codex_home("relative/dir") is None
    assert appserver._valid_codex_home("") is None
    assert appserver._valid_codex_home("/home/\x00u") is None  # control char
    assert appserver._valid_codex_home(SURROGATE) is None
    assert appserver._valid_codex_home("/" + "h" * (cli_contract.CODEX_HOME_MAX_BYTES + 1)) is None


def test_failure_message_redacts_and_bounds_the_join():
    item = {"failures": [{"message": LEAKY_MESSAGE}, {"message": f"and {SECRET}"}]}
    message = appserver._failure_message(item)
    assert SECRET not in message
    assert len(message) == CAP
    assert message.endswith(MARKER)


def test_failure_message_defaults_survive_sanitizing():
    # Sanitizing must not swallow the empty-join fallback (regression guard for wiring
    # `_display_text` around the `or` rather than inside it).
    assert appserver._failure_message({"failures": [{"nomsg": 1}]}) == (
        "Codex reported an import failure."
    )
    assert appserver._failure_message({"failures": []}) is None


@pytest.mark.parametrize(
    ("scenario", "status", "prefix"),
    [
        ("item_failure_leaky", TransferStatus.ITEM_FAILURE, ""),
        ("init_error_leaky", TransferStatus.PROTOCOL_ERROR, "codex app-server initialize failed: "),
        ("import_error_leaky", TransferStatus.ITEM_FAILURE, ""),
        (
            "invalid_params_leaky",
            TransferStatus.PROTOCOL_ERROR,
            "codex app-server rejected the import request: ",
        ),
    ],
)
def test_every_app_server_message_route_is_redacted_and_bounded(tmp_path, scenario, status, prefix):
    """#276: all four routes that carry app-server text into an error envelope.

    Each keeps its static prefix (which is ours, not the child's) and bounds only the
    foreign fragment, so the cap can never eat our own explanation."""
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command(scenario, home),
        timeout_seconds=15,
    )
    assert outcome.status is status
    assert outcome.message.startswith(prefix)
    foreign = outcome.message[len(prefix) :]
    assert "sk-" not in foreign
    assert "[redacted: secret value]" in foreign
    assert len(foreign) == CAP
    assert foreign.endswith(MARKER)


@pytest.mark.parametrize(
    ("scenario", "status", "expected"),
    [
        (
            "init_error_falsey",
            TransferStatus.PROTOCOL_ERROR,
            "codex app-server rejected initialize.",
        ),
        (
            "invalid_params_falsey",
            TransferStatus.PROTOCOL_ERROR,
            "codex app-server rejected the import request.",
        ),
        (
            "import_error_falsey",
            TransferStatus.ITEM_FAILURE,
            "codex app-server rejected the import.",
        ),
    ],
)
def test_falsey_app_server_message_yields_our_generic_sentence(
    tmp_path, scenario, status, expected
):
    """Each `if detail` gate tests the RAW wire value, deliberately.

    A falsey JSON `message` (`0`, `{}`, `""`, `false`, `[]`) carries no diagnostic text.
    `_display_text` would coerce it to a truthy string, so branching on the *sanitized*
    result — as a reviewer suggested — would publish noise like "rejected the import: {}"
    where we currently emit a clean generic sentence. Locks that decision at all three
    sites."""
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command(scenario, home),
        timeout_seconds=15,
    )
    assert outcome.status is status
    assert outcome.message == expected


def test_incomplete_ledger_path_is_bounded_but_keeps_the_ledger_filename(tmp_path):
    """The INCOMPLETE message names the ledger. `codexHome` is app-server-derived, so the
    displayed path is built from a bounded copy — but the trailing filename is ours and
    must survive, since it is the part that tells an operator what to look for."""
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("long_codex_home", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.INCOMPLETE
    assert outcome.ledger_path.endswith("/external_agent_session_imports.json")
    assert len(outcome.ledger_path) <= CAP + len("/external_agent_session_imports.json")
    assert MARKER in outcome.ledger_path
    # The RAW codexHome is retained: it is the filesystem base `_lookup_ledger` reads,
    # so bounding it at capture would silently break the dedup lookup.
    assert outcome.codex_home == LONG_CODEX_HOME


def test_bad_line_still_yields_its_constant_message(tmp_path):
    """Regression guard for the bound the fix relies on: an over-cap line is truncated by
    the reader, fails to parse, and becomes a CONSTANT message — no app-server text at
    all. This is what keeps a multi-megabyte JSONL line off the error envelope."""
    home = tmp_path / "codex_home"
    home.mkdir()
    t = _transcript(tmp_path)
    outcome = transfer_session(
        transcript_realpath=str(t.resolve()),
        cwd=str(tmp_path),
        command=_command("flood_line", home),
        timeout_seconds=15,
    )
    assert outcome.status is TransferStatus.PROTOCOL_ERROR
    assert outcome.message == "codex app-server emitted a non-JSON or oversized line."


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


# --- read_rate_limits (account/rateLimits/read, 0.144+) -------------------------


def test_rate_limits_weekly_in_primary_slot_is_reslotted_to_secondary(tmp_path):
    """The real 0.144 shape: a weekly window (10080 min) reported in the `primary` slot,
    no secondary. It must be re-slotted to our `secondary` (weekly) by duration, NOT filed
    as a 5-hour primary — the #321 topology fix."""
    home = tmp_path / "ch"
    out = read_rate_limits(command=_command("rl_ok", home), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.OK
    assert out.snapshot is not None
    assert out.snapshot.primary is None
    assert out.snapshot.secondary is not None
    assert out.snapshot.secondary.window_minutes == 10080
    assert out.snapshot.secondary.used_percent == 6.0
    assert out.snapshot.plan_type == "plus"
    assert out.codex_home == str(home)


def test_rate_limits_two_windows_classified_by_duration(tmp_path):
    home = tmp_path / "ch"
    out = read_rate_limits(command=_command("rl_two_windows", home), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.OK
    assert out.snapshot is not None
    assert out.snapshot.primary is not None and out.snapshot.primary.window_minutes == 300
    assert out.snapshot.secondary is not None and out.snapshot.secondary.window_minutes == 10080


@pytest.mark.parametrize("scenario", ["rl_no_windows", "rl_null_block"])
def test_rate_limits_no_windows_is_no_quota_not_drift(tmp_path, scenario):
    out = read_rate_limits(command=_command(scenario, tmp_path / "ch"), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.NO_QUOTA
    assert out.snapshot is None


def test_rate_limits_spend_control_round_trips_from_the_wire(tmp_path):
    # End-to-end over the real JSON-RPC path: the 0.145 field survives the handshake, the
    # reader, and the parse (#359) — not just the unit-level _parse_rate_limits call.
    out = read_rate_limits(
        command=_command("rl_spend_control", tmp_path / "ch"), timeout_seconds=10
    )
    assert out.status is RateLimitReadStatus.OK
    assert out.snapshot is not None
    assert out.snapshot.spend_control_reached is True
    assert out.snapshot.primary is not None  # the healthy window is still reported


def test_rate_limits_spend_control_without_windows_is_not_no_quota(tmp_path):
    out = read_rate_limits(
        command=_command("rl_spend_control_no_windows", tmp_path / "ch"), timeout_seconds=10
    )
    assert out.status is RateLimitReadStatus.OK
    assert out.snapshot is not None
    assert out.snapshot.spend_control_reached is True
    assert out.snapshot.primary is None and out.snapshot.secondary is None


def test_rate_limits_unsupported_method(tmp_path):
    out = read_rate_limits(command=_command("rl_unsupported", tmp_path / "ch"), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.UNSUPPORTED
    assert out.snapshot is None


def test_rate_limits_read_error_is_protocol_error(tmp_path):
    out = read_rate_limits(command=_command("rl_read_error", tmp_path / "ch"), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.PROTOCOL_ERROR


def test_rate_limits_error_message_is_redacted(tmp_path):
    out = read_rate_limits(
        command=_command("rl_read_error_leaky", tmp_path / "ch"), timeout_seconds=10
    )
    assert out.status is RateLimitReadStatus.PROTOCOL_ERROR
    assert out.message is not None
    assert SECRET not in out.message


def test_rate_limits_eof_before_response_is_protocol_error(tmp_path):
    out = read_rate_limits(command=_command("rl_eof", tmp_path / "ch"), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.PROTOCOL_ERROR


def test_rate_limits_timeout(tmp_path):
    out = read_rate_limits(command=_command("rl_timeout", tmp_path / "ch"), timeout_seconds=0.5)
    assert out.status is RateLimitReadStatus.TIMED_OUT
    assert out.snapshot is None


def test_rate_limits_missing_codex_home_is_non_fatal(tmp_path):
    """Unlike the transfer path, a handshake that omits codexHome does not fail the read —
    codex_home is provenance-only here. The read still returns OK with codex_home None."""
    out = read_rate_limits(command=_command("rl_no_home", tmp_path / "ch"), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.OK
    assert out.snapshot is not None
    assert out.codex_home is None


def test_rate_limits_prequeued_id2_is_not_trusted(tmp_path):
    # #321 review F2: an adversarial child emits a fabricated id-2 quota response WITHOUT reading
    # the request (guessing the old fixed id 2). Because the client correlates on an unpredictable
    # read_id, the injected response is ignored — the run must NOT return it as OK quota.
    out = read_rate_limits(command=_command("rl_prequeue_id2", tmp_path / "ch"), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.PROTOCOL_ERROR
    assert out.snapshot is None


def test_rate_limits_spawn_failure(tmp_path):
    out = read_rate_limits(command=[str(tmp_path / "does-not-exist")], timeout_seconds=10)
    assert out.status is RateLimitReadStatus.SPAWN_FAILED


def test_rate_limits_init_non_json_is_protocol_error(tmp_path):
    out = read_rate_limits(command=_command("protocol_drift", tmp_path / "ch"), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.PROTOCOL_ERROR


def test_rate_limits_init_error_is_protocol_error(tmp_path):
    out = read_rate_limits(command=_command("init_error", tmp_path / "ch"), timeout_seconds=10)
    assert out.status is RateLimitReadStatus.PROTOCOL_ERROR


# --- _parse_rate_limits (discriminated pure mapping) ----------------------------

_OK = appserver.RateLimitReadStatus.OK
_NO_QUOTA = appserver.RateLimitReadStatus.NO_QUOTA
_DRIFT = appserver.RateLimitReadStatus.PROTOCOL_ERROR


def _win(used, dur, resets=9999999999):
    return {"usedPercent": used, "windowDurationMins": dur, "resetsAt": resets}


def test_parse_unknown_duration_keeps_source_slot():
    result = {"rateLimits": {"primary": _win(10, None), "secondary": _win(20, None)}}
    status, snap = appserver._parse_rate_limits(result)
    assert status is _OK
    assert snap.primary is not None and snap.primary.used_percent == 10.0
    assert snap.secondary is not None and snap.secondary.used_percent == 20.0


def test_parse_two_windows_sorted_shorter_to_primary():
    # #321 review F3: when both windows target the same slot, they are sorted by duration so
    # `primary` is always the SHORTER horizon — never left inverted.
    result = {"rateLimits": {"primary": _win(10, 300), "secondary": _win(20, 60)}}
    status, snap = appserver._parse_rate_limits(result)
    assert status is _OK
    assert snap.primary is not None and snap.primary.window_minutes == 60
    assert snap.secondary is not None and snap.secondary.window_minutes == 300


def test_parse_huge_int_does_not_raise():
    # #321 review F1: a 400-digit usedPercent must not raise (float() OverflowError) — it
    # degrades to a coerced value, and the read stays a typed outcome, never an exception.
    status, snap = appserver._parse_rate_limits(
        {
            "rateLimits": {
                "primary": {"usedPercent": 10**400, "windowDurationMins": 300, "resetsAt": 9}
            }
        }
    )
    assert status is _OK
    assert snap.primary is not None
    assert snap.primary.used_percent is None  # out-of-range/huge → absent, not clamped


def test_parse_null_or_empty_block_is_no_quota():
    # An explicitly null block, or a present block with no windows, is a legitimate no-quota
    # account — NO_QUOTA, not drift.
    assert appserver._parse_rate_limits({"rateLimits": None}) == (_NO_QUOTA, None)
    assert appserver._parse_rate_limits({"rateLimits": {"primary": None, "secondary": None}}) == (
        _NO_QUOTA,
        None,
    )


def test_parse_malformed_shapes_are_protocol_error_not_no_quota():
    # #321 review F1: missing key, wrong-typed block, and malformed windows are DRIFT — they
    # must not masquerade as a legitimate no-quota account.
    assert appserver._parse_rate_limits({})[0] is _DRIFT  # required key absent
    assert appserver._parse_rate_limits("nope")[0] is _DRIFT  # result not an object
    assert appserver._parse_rate_limits({"rateLimits": []})[0] is _DRIFT  # wrong block type
    assert appserver._parse_rate_limits({"rateLimits": {"primary": "x"}})[0] is _DRIFT  # bad window
    assert appserver._parse_rate_limits({"rateLimits": {"primary": {"nope": 1}}})[0] is _DRIFT


def test_parse_boundary_duration_is_short():
    # A window exactly at the threshold is short (→ primary), one above is long (→ secondary).
    thresh = cli_contract.RATE_LIMIT_SHORT_WINDOW_MAX_MINUTES
    result = {"rateLimits": {"primary": _win(10, thresh), "secondary": _win(20, thresh + 1)}}
    status, snap = appserver._parse_rate_limits(result)
    assert status is _OK
    assert snap.primary is not None and snap.primary.window_minutes == thresh
    assert snap.secondary is not None and snap.secondary.window_minutes == thresh + 1


def test_parse_unknown_reached_type_is_dropped_not_trusted():
    # #321 review F2: an unrecognized rateLimitReachedType must be dropped (None), never trusted
    # as a real limit reason (which would later become a false 'exhausted').
    result = {"rateLimits": {"primary": _win(10, 300), "rateLimitReachedType": "made_up_value"}}
    _, snap = appserver._parse_rate_limits(result)
    assert snap.rate_limit_reached_type is None


def test_parse_known_reached_type_is_kept():
    result = {
        "rateLimits": {"primary": _win(10, 300), "rateLimitReachedType": "rate_limit_reached"}
    }
    _, snap = appserver._parse_rate_limits(result)
    assert snap.rate_limit_reached_type == "rate_limit_reached"


def test_parse_oversized_plan_type_is_dropped():
    # #321 review F2: an unbounded/hostile planType must not reach the envelope.
    result = {"rateLimits": {"primary": _win(10, 300), "planType": "p" * 5000}}
    _, snap = appserver._parse_rate_limits(result)
    assert snap.plan_type is None


# --- spendControlReached (0.145+, #359) -----------------------------------------
# Tri-state: True/False are the backend's answer; None means UNAVAILABLE (upstream's own
# wording), explicitly NOT "false" — collapsing it would invent a reassuring signal the
# backend never sent.


@pytest.mark.parametrize("wire", [True, False])
def test_parse_spend_control_keeps_both_boolean_states(wire):
    result = {"rateLimits": {"primary": _win(10, 300), "spendControlReached": wire}}
    _, snap = appserver._parse_rate_limits(result)
    assert snap.spend_control_reached is wire


def test_parse_spend_control_absent_is_none():
    # codex 0.144 omits the key entirely; that must degrade to None, never to False.
    result = {"rateLimits": {"primary": _win(10, 300)}}
    _, snap = appserver._parse_rate_limits(result)
    assert snap.spend_control_reached is None


@pytest.mark.parametrize("wire", [None, 1, 0, "true", "false", "", [], {}, [True], 1.0])
def test_parse_spend_control_non_bool_is_dropped_to_none(wire):
    # Strict: only a real bool is an answer. A truthy non-bool (1, "true") must never become a
    # false administrative block, and a falsy non-bool (0, "") must never become a real False —
    # both are "the backend did not say".
    result = {"rateLimits": {"primary": _win(10, 300), "spendControlReached": wire}}
    _, snap = appserver._parse_rate_limits(result)
    assert snap.spend_control_reached is None


def test_parse_spend_control_true_with_no_windows_is_ok_not_no_quota():
    # A windowless response carrying spendControlReached=true must NOT collapse to NO_QUOTA:
    # that would discard the exact signal (#359). The snapshot has no windows but is real.
    result = {"rateLimits": {"primary": None, "secondary": None, "spendControlReached": True}}
    status, snap = appserver._parse_rate_limits(result)
    assert status is _OK
    assert snap is not None
    assert snap.spend_control_reached is True
    assert snap.primary is None and snap.secondary is None


@pytest.mark.parametrize("wire", [False, None, "yes"])
def test_parse_no_windows_without_spend_control_stays_no_quota(wire):
    # Only an explicit True rescues a windowless block; False/None/junk remain a legitimate
    # no-quota account.
    result = {"rateLimits": {"primary": None, "secondary": None, "spendControlReached": wire}}
    assert appserver._parse_rate_limits(result) == (_NO_QUOTA, None)


def test_parse_spend_control_true_with_window_keys_absent_matches_explicit_null():
    # Upstream's RateLimitSnapshot declares NO required members, so an OMITTED window key is
    # schema-valid and means exactly what an explicit null means. Pinning the equivalence keeps
    # a future reader from "fixing" absence into drift (Codex review, declined with this
    # evidence) — and note absence was already NO_QUOTA before #359, never PROTOCOL_ERROR.
    absent = {"rateLimits": {"spendControlReached": True}}
    explicit = {"rateLimits": {"primary": None, "secondary": None, "spendControlReached": True}}
    status_a, snap_a = appserver._parse_rate_limits(absent)
    status_e, snap_e = appserver._parse_rate_limits(explicit)
    assert status_a is status_e is _OK
    assert snap_a == snap_e


def test_parse_no_windows_and_no_spend_control_keys_is_no_quota():
    # The same absent-key shape WITHOUT the spend-control block stays a legitimate no-quota
    # account — the pre-#359 behavior, unchanged.
    assert appserver._parse_rate_limits({"rateLimits": {}}) == (_NO_QUOTA, None)


def test_parse_malformed_window_still_drifts_even_with_spend_control():
    # A PRESENT but malformed window is drift regardless of spend control — the new field must
    # not turn protocol drift into a plausible OK.
    result = {"rateLimits": {"primary": {"nope": 1}, "spendControlReached": True}}
    assert appserver._parse_rate_limits(result)[0] is _DRIFT


# --- liveness guards ------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_matches_recorded_real_wire_shape():
    """Known-positive against the recorded (synthetic-valued) 0.144 wire shape — including
    rateLimitsByLimitId / credits / rateLimitResetCredits siblings. A future change that
    breaks the mapping fails HERE rather than silently degrading (the #321 trap)."""
    result = json.loads((_FIXTURES / "rate_limits_read_response.json").read_text())
    status, snap = appserver._parse_rate_limits(result)
    assert status is _OK
    assert snap.plan_type == "plus"
    # the single weekly window (10080 min) is re-slotted from `primary` to our `secondary`.
    assert snap.primary is None
    assert snap.secondary is not None
    assert snap.secondary.window_minutes == 10080
    assert snap.secondary.used_percent == 12.0


@pytest.mark.integration
def test_live_rate_limits_read_roundtrip():
    """Live: read quota from the REAL `codex app-server`. This is the liveness guard for
    #321 — if codex moves the method again, the status is UNSUPPORTED/PROTOCOL_ERROR and this
    FAILS loudly instead of silently reporting no quota. Requires an authenticated codex. Run
    with `pytest -m integration --no-cov`."""
    outcome = read_rate_limits(command=None, timeout_seconds=60)
    if outcome.status is RateLimitReadStatus.SPAWN_FAILED:
        pytest.skip("codex CLI not installed")
    assert outcome.status in (RateLimitReadStatus.OK, RateLimitReadStatus.NO_QUOTA), (
        f"account/rateLimits/read contract drifted: {outcome.status} ({outcome.message})"
    )
    if outcome.status is RateLimitReadStatus.OK:
        assert outcome.snapshot is not None
        assert outcome.snapshot.primary is not None or outcome.snapshot.secondary is not None
