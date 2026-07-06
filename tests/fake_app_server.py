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

FIXED_TARGET = "thread-fresh-0001"


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


def main() -> None:
    scenario = sys.argv[1]
    codex_home = sys.argv[2] if len(sys.argv) > 2 else "/tmp/fake-codex-home"

    for raw in sys.stdin:
        stripped = raw.strip()
        if not stripped:
            continue
        msg = json.loads(stripped)
        method = msg.get("method")

        if method == "initialize":
            if scenario == "protocol_drift":
                sys.stdout.write("this is not json\n")
                sys.stdout.flush()
                return
            _emit(_init_response(codex_home))
            if scenario == "eof_after_init":
                return
            continue

        if method == "initialized":
            continue

        if method == "externalAgentConfig/import":
            source = _session_source(msg["params"])
            if scenario == "unsupported":
                _emit({"id": 2, "error": {"code": -32601, "message": "method not found"}})
                return
            if scenario == "import_error":
                _emit({"id": 2, "error": {"code": -32000, "message": "boom"}})
                return
            if scenario == "fresh":
                _emit(_import_response())
                _emit(_progress())  # interleaved progress (ignored by the client)
                _emit(
                    _completed(
                        [
                            {
                                "itemType": "SESSIONS",
                                "cwd": None,
                                "source": source,
                                "target": FIXED_TARGET,
                            }
                        ],
                        [],
                    )
                )
                return
            if scenario == "completed_before_response":
                # Terminal notification arrives BEFORE the import response.
                _emit(
                    _completed(
                        [
                            {
                                "itemType": "SESSIONS",
                                "cwd": None,
                                "source": source,
                                "target": FIXED_TARGET,
                            }
                        ],
                        [],
                    )
                )
                _emit(_import_response())
                return
            if scenario == "dedup":
                # Byte-identical re-import: empty successes AND failures (ledger fallback).
                _emit(_import_response())
                _emit(_completed([], []))
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
            if scenario == "timeout":
                # Accept the import but never send completed; keep the process alive so
                # the client hits its deadline. The client kills us on teardown.
                _emit(_import_response())
                for _ in sys.stdin:  # block forever
                    pass
                return
            return
    # stdin closed without an import request
    return


if __name__ == "__main__":
    main()
