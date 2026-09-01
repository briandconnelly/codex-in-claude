"""Fail-closed contract for scripts/capture_wire_tools.py.

The script lives under scripts/ (not the package), so coverage doesn't track it; these
tests pin the property that actually matters for a probe whose whole job is to report an
ABSENCE: it must never let a failed capture read as "codex sent no tools". It is loaded by
path, and `capture` — its only codex-spawning function — is monkeypatched, so nothing here
runs the CLI."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "capture_wire_tools.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("capture_wire_tools", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script():
    return _load_script()


def _run(script, monkeypatch, capsys, body, argv=("capture_wire_tools.py",)):
    monkeypatch.setattr(script, "capture", lambda *a, **k: body)
    monkeypatch.setattr(script.sys, "argv", list(argv))
    code = script.main()
    return code, capsys.readouterr()


def test_a_failed_capture_exits_nonzero_and_prints_no_tool_names(script, monkeypatch, capsys):
    """The whole point: a probe that captured NOTHING must not look like an empty tool list.

    A caller greps this output for a tool name. If a failed run printed nothing and exited 0,
    `grep -c clock` would report 0 and a broken probe would be indistinguishable from a genuine
    absence -- the exact confusion the positive control exists to prevent."""
    code, out = _run(script, monkeypatch, capsys, None)
    assert code == 1
    assert out.out == ""


def test_a_request_without_a_tools_array_is_a_failure(script, monkeypatch, capsys):
    """A captured request that carries no `tools` key means the shape moved, not that the
    catalog is empty. Reporting it as empty would silently retract every finding built on it."""
    code, out = _run(script, monkeypatch, capsys, {"model": "gpt-5.5"})
    assert code == 1
    assert out.out == ""


@pytest.mark.parametrize(
    "tools,expected",
    [
        ([{"type": "function", "name": "exec_command"}], ["exec_command"]),
        # A namespace tool carries `name`; a bare builtin may carry only `type`.
        ([{"type": "namespace", "name": "clock"}], ["clock"]),
        ([{"type": "web_search"}], ["web_search"]),
        (
            [{"type": "function", "name": "apply_patch"}, {"type": "tool_search"}],
            ["apply_patch", "tool_search"],
        ),
        ([], []),
    ],
)
def test_tool_names_come_from_name_then_type(script, monkeypatch, capsys, tools, expected):
    """Both shapes appear in a real 0.152.0 capture, so reading only one would silently drop
    tools from the diff the upgrade procedure runs."""
    code, out = _run(script, monkeypatch, capsys, {"tools": tools})
    assert code == 0
    assert out.out.split() == expected


def test_json_mode_emits_the_full_definitions(script, monkeypatch, capsys):
    """`--json` is what exposes a tool's PARAMETERS -- the way the 12-hour `duration_ms` bound on
    `clock.sleep` was read (COMPATIBILITY.md -> "Sleep tool")."""
    import json

    tools = [{"type": "namespace", "name": "clock", "tools": [{"name": "sleep"}]}]
    code, out = _run(
        script,
        monkeypatch,
        capsys,
        {"tools": tools},
        argv=("capture_wire_tools.py", "--json"),
    )
    assert code == 0
    assert json.loads(out.out) == tools
