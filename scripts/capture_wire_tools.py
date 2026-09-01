#!/usr/bin/env python3
"""Capture the tool catalog codex sends to the model, without spending anything.

Some upstream changes are visible ONLY in the model-facing request: codex 0.152.0 dropped
`update_plan` from the default tool set and added a gated `clock.sleep`, while every `--help`
surface stayed byte-identical and `codex features list` showed nothing resembling either. See
COMPATIBILITY.md -> "Sleep tool" and "Default tool-catalog changes are invisible to `--help`".

This points codex at a local HTTP sink instead of the real API. The sink records the first
request body and answers 400, so the run stops before any model call: **zero spend, no auth
needed**.

Usage (from the repo root):

    uv run python scripts/capture_wire_tools.py
    uv run python scripts/capture_wire_tools.py --bin /path/to/old/codex
    uv run python scripts/capture_wire_tools.py -- -c 'features.sleep_tool.mode="always_on"'
    uv run python scripts/capture_wire_tools.py --json          # full tool definitions

Exit status: 0 on a captured request, 1 on failure (nothing captured). A non-zero exit means
the probe did not run — never read it as "no tools".

**Always pair a finding with the positive control**, or an absence proves nothing about codex
and everything about a blind probe:

    uv run python scripts/capture_wire_tools.py -- --disable view_image   # view_image must vanish
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

SINK_HOST = "127.0.0.1"
RUN_TIMEOUT_SECONDS = 120


def _make_handler(captured: list[bytes]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # BaseHTTPRequestHandler's required method name
            length = int(self.headers.get("content-length", 0) or 0)
            captured.append(self.rfile.read(length))
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"sink: request captured, not forwarded"}}')

        def log_message(self, *args: object) -> None:
            pass  # keep the probe's stdout clean

    return Handler


def capture(binary: str, model: str, extra: list[str]) -> dict | None:
    """Run one codex turn against a local sink and return the parsed request body."""
    captured: list[bytes] = []
    server = HTTPServer((SINK_HOST, 0), _make_handler(captured))
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = (
        f'model_providers.sink={{name="sink",base_url="http://{SINK_HOST}:{port}/v1",'
        'wire_api="responses",env_key="OPENAI_API_KEY",requires_openai_auth=false}'
    )
    cmd = [
        binary,
        "exec",
        "--json",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "-c",
        provider,
        "-c",
        "model_provider=sink",
        "-c",
        f"model={model}",
        *extra,
        "hi",
    ]
    env = {**os.environ, "OPENAI_API_KEY": "sink-placeholder-not-a-credential"}
    try:
        subprocess.run(  # argv list, never a shell string
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=RUN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAILED to run {binary}: {exc}", file=sys.stderr)
        return None
    finally:
        server.shutdown()
        server.server_close()
    if not captured:
        print(
            "FAILED: codex sent no request to the sink. The provider override may have been "
            "rejected, or the binary exited before the first turn. Nothing was captured — do "
            "NOT read this as an empty tool list.",
            file=sys.stderr,
        )
        return None
    try:
        return json.loads(captured[0])
    except json.JSONDecodeError as exc:
        print(f"FAILED to parse the captured request: {exc}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture the model-facing tool catalog codex sends (zero spend).",
    )
    parser.add_argument("--bin", default="codex", help="codex binary to drive (default: PATH)")
    parser.add_argument(
        "--model",
        default="gpt-5.5",
        help="model slug named in the request; the sink answers before it is used",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print full tool definitions, not just names",
    )
    parser.add_argument(
        "extra",
        nargs="*",
        help="extra codex args, after `--` (e.g. -- --disable view_image)",
    )
    args = parser.parse_args()

    binary = shutil.which(args.bin) or args.bin
    body = capture(binary, args.model, list(args.extra))
    if body is None:
        return 1
    tools = body.get("tools")
    if not isinstance(tools, list):
        print("FAILED: the captured request carried no `tools` array.", file=sys.stderr)
        return 1
    if args.json:
        json.dump(tools, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for tool in tools:
            name = tool.get("name") or tool.get("type") if isinstance(tool, dict) else None
            print(name if name else json.dumps(tool))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
