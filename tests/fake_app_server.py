"""A scripted stand-in for `codex app-server`, driven by a scenario argv.

Tests spawn ``[sys.executable, fake_app_server.py, <scenario>, <codex_home>]`` as the
``command`` for :func:`codex_in_claude.appserver.transfer_session`, so the real
subprocess/JSONL I/O path is exercised hermetically (no live codex). Each scenario
replays a canned sequence of newline-delimited JSON-RPC messages in response to the
client's ``initialize`` / ``externalAgentConfig/import`` requests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FIXED_TARGET = "thread-fresh-0001"

# A secret-shaped value the redactor must catch (matches the unlabeled `sk-` vendor-key
# pattern), and a diagnostic that carries it while running well past the display cap.
# The secret sits near the front so the redaction marker survives truncation, and the
# padding is what pushes the whole string over the cap.
SECRET = "sk-" + "b" * 32
LEAKY_MESSAGE = f"import failed near {SECRET} while converting " + "z" * 400

# An absolute codexHome that is VALID (<= CODEX_HOME_MAX_BYTES) but well past the 300-char
# display cap, so the INCOMPLETE message still exercises _display_text bounding. Kept under
# the 4096-byte identifier bound so it survives handshake validation (see #279).
LONG_CODEX_HOME = "/" + "h" * 400

# An absolute codexHome PAST the 4096-byte bound: valid handshake shape, invalid identifier.
OVERSIZED_CODEX_HOME = "/" + "h" * 5000


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _init_response(codex_home: str) -> dict:
    return {
        "id": 1,
        "result": {
            "userAgent": "fake/0.0.0",
            "codexHome": codex_home,
            "platformFamily": "unix",
            "platformOs": "macos",
        },
    }


def _import_response(import_id: str = "imp-1") -> dict:
    return {"id": 2, "result": {"importId": import_id}}


def _notification(method: str, successes: list[dict], failures: list[dict]) -> dict:
    return {
        "method": method,
        "params": {
            "importId": "imp-1",
            "itemTypeResults": [
                {"itemType": "SESSIONS", "successes": successes, "failures": failures}
            ],
        },
    }


def _progress() -> dict:
    return _notification("externalAgentConfig/import/progress", [], [])


def _completed(successes: list[dict], failures: list[dict]) -> dict:
    return _notification("externalAgentConfig/import/completed", successes, failures)


def _session_source(import_params: dict) -> str:
    return import_params["migrationItems"][0]["details"]["sessions"][0]["path"]


def _handle_initialize(scenario: str, codex_home: str) -> bool:
    """Respond to `initialize`. Returns True when the fake should exit afterwards."""
    if scenario == "protocol_drift":
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
        return True
    if scenario == "flood_line":
        # A single *valid JSON* line far past the reader's cap. It is well-formed, so it
        # parses cleanly unless the reader truncated it — which is what makes this
        # discriminating rather than merely non-JSON-in, non-JSON-out.
        pad = "x" * (9 * 1024 * 1024)
        sys.stdout.write(json.dumps({"id": 1, "result": {"pad": pad}}) + "\n")
        sys.stdout.flush()
        return True
    if scenario in ("stderr_flood", "stderr_flood_unicode"):
        # Flood stderr past _MAX_STDERR_BYTES between two sentinels, then exit without
        # answering. The client sees stdout EOF; the diagnostic that matters is the LAST
        # stderr line, which a prefix-retaining drain throws away.
        filler = "é" * 40 if scenario == "stderr_flood_unicode" else "x" * 80
        sys.stderr.write("EARLY-SENTINEL\n")
        for _ in range(4000):  # ~320KB, far past the 64KB cap
            sys.stderr.write(filler + "\n")
        sys.stderr.write("FINAL-SENTINEL\n")
        sys.stderr.flush()
        return True
    if scenario == "init_error":
        _emit({"id": 1, "error": {"code": -32000, "message": "bad init"}})
        return True
    if scenario == "init_error_leaky":
        _emit({"id": 1, "error": {"code": -32000, "message": LEAKY_MESSAGE}})
        return True
    if scenario == "init_error_falsey":
        _emit({"id": 1, "error": {"code": -32000, "message": 0}})
        return True
    if scenario == "long_codex_home":
        # A valid handshake reporting an absurd codexHome. The client keeps the raw value
        # for the ledger lookup but must bound it before it reaches an error message.
        _emit(_init_response(LONG_CODEX_HOME))
        return False
    if scenario == "init_no_home":
        _emit({"id": 1, "result": {"userAgent": "fake/0.0.0", "platformOs": "macos"}})
        return True
    if scenario == "relative_home":
        _emit(_init_response("relative/dir"))
        return False
    if scenario == "control_home":
        _emit(_init_response("/home/\x00u"))
        return False
    if scenario == "surrogate_home":
        _emit(_init_response("/home/\ud800"))
        return False
    if scenario == "oversized_home":
        _emit(_init_response(OVERSIZED_CODEX_HOME))
        return False
    _emit(_init_response(codex_home))
    return scenario == "eof_after_init"


# Scenarios that answer the import request with a JSON-RPC error. The client classifies
# by `code`: -32601 → UNSUPPORTED, the rest of the reserved range → PROTOCOL_ERROR, an
# application-range code → ITEM_FAILURE, a non-integer code → PROTOCOL_ERROR.
_IMPORT_ERRORS: dict[str, dict] = {
    "unsupported": {"code": -32601, "message": "method not found"},
    # Application-range code → a genuine import rejection (transfer_failed).
    "import_error": {"code": 42, "message": "boom"},
    "import_error_leaky": {"code": 42, "message": LEAKY_MESSAGE},
    # Reserved-range code → request/protocol drift (cli_contract_changed).
    "invalid_params": {"code": -32602, "message": "invalid params"},
    "invalid_params_leaky": {"code": -32602, "message": LEAKY_MESSAGE},
    # Error object with no integer code → treated as protocol drift.
    "malformed_error": {"message": "weird"},
    # -32601.0 is `== -32601` in Python but is NOT an integer code. A non-integer code is
    # malformed → protocol drift, never UNSUPPORTED.
    "float_method_not_found": {"code": -32601.0, "message": "floaty"},
    # JSON `true` decodes to bool, a subclass of int → still malformed.
    "bool_code": {"code": True, "message": "booly"},
    # Falsey `message` values: no diagnostic text, so the client emits its generic sentence
    # rather than coercing them into noise like "rejected the import: {}".
    "import_error_falsey": {"code": 42, "message": 0},
    "invalid_params_falsey": {"code": -32602, "message": {}},
}


def _handle_import(scenario: str, import_params: dict) -> None:
    """Respond to `externalAgentConfig/import`, then return so the fake exits."""
    source = _session_source(import_params)
    if scenario in _IMPORT_ERRORS:
        _emit({"id": 2, "error": _IMPORT_ERRORS[scenario]})
        return
    if scenario in ("dedup", "long_codex_home"):
        # Empty successes AND failures. `dedup` is a byte-identical re-import resolved via
        # the ledger; `long_codex_home` has no ledger record, so it lands as INCOMPLETE and
        # its message names the ledger path built from the absurd codexHome.
        _emit(_import_response())
        _emit(_completed([], []))
        return
    if scenario == "item_failure_leaky":
        _emit(_import_response())
        _emit(_completed([], [{"itemType": "SESSIONS", "message": LEAKY_MESSAGE}]))
        return
    _handle_import_success(scenario, source)


def _handle_import_success(scenario: str, source: str) -> None:
    success = {"itemType": "SESSIONS", "cwd": None, "source": source, "target": FIXED_TARGET}
    if scenario == "fresh":
        _emit(_import_response())
        _emit(_progress())  # interleaved progress (ignored by the client)
        _emit(_completed([success], []))
        return
    if scenario == "completed_before_response":
        # Terminal notification arrives BEFORE the import response.
        _emit(_completed([success], []))
        _emit(_import_response())
        return
    if scenario == "item_failure":
        _emit(_import_response())
        _emit(
            _completed(
                [],
                [
                    {
                        "itemType": "SESSIONS",
                        "failureStage": "convert",
                        "message": "could not parse session",
                        "errorType": "ParseError",
                    }
                ],
            )
        )
        return
    if scenario in ("oversized_target", "control_target", "null_target"):
        bad = {
            "oversized_target": "t" * 5000,
            "control_target": "thread-\x00-bad",
            "null_target": None,
        }[scenario]
        _emit(_import_response())
        _emit(_completed([{"itemType": "SESSIONS", "source": source, "target": bad}], []))
        return
    if scenario == "invalid_target_with_ledger":
        # A present-but-invalid live target AND a valid ledger record for the same transcript.
        # The live drift must win (PROTOCOL_ERROR), not silently recover the ledger id.
        _emit(_import_response())
        _emit(_completed([{"itemType": "SESSIONS", "source": source, "target": "t" * 5000}], []))
        return
    if scenario == "target_key_absent":
        # A success entry that carries NO target key at all → genuinely absent → ledger fallback.
        _emit(_import_response())
        _emit(_completed([{"itemType": "SESSIONS", "source": source}], []))
        return
    if scenario == "timeout":
        # Accept the import but never send completed; keep the process alive so the client
        # hits its deadline. The client kills us on teardown.
        _emit(_import_response())
        for _ in sys.stdin:  # block forever
            pass


def main() -> None:
    scenario = sys.argv[1]
    codex_home = sys.argv[2] if len(sys.argv) > 2 else "/tmp/fake-codex-home"
    method_log = sys.argv[3] if len(sys.argv) > 3 else None

    for raw in sys.stdin:
        stripped = raw.strip()
        if not stripped:
            continue
        msg = json.loads(stripped)
        method = msg.get("method")
        if method_log and method:
            with Path(method_log).open("a", encoding="utf-8") as fh:
                fh.write(f"{method}\n")

        if method == "initialize":
            if _handle_initialize(scenario, codex_home):
                return
            continue

        if method == "initialized":
            continue

        if method == "externalAgentConfig/import":
            _handle_import(scenario, msg["params"])
            return
    # stdin closed without an import request
    return


if __name__ == "__main__":
    main()
