"""Guards on the plugin packaging: JSON validity and cross-file version/tool parity."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from codex_in_claude import __version__, server

ROOT = Path(__file__).resolve().parents[1]


def _load_json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text())


def _declared_py_minors() -> set[str]:
    """Python minor versions advertised by the trove classifiers in pyproject.toml."""
    classifiers = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["classifiers"]
    return {
        m.group(1)
        for c in classifiers
        if (m := re.fullmatch(r"Programming Language :: Python :: (\d+\.\d+)", c))
    }


def _parse_py_matrix(workflow: str) -> set[str]:
    """Python minors from a workflow's `python-version` matrix.

    Handles both the inline-list form (`python-version: ["3.11", "3.12"]`) and the
    block-list form (`python-version:` followed by indented `- "3.11"` items), so a
    harmless YAML reformat doesn't false-fail the drift guard."""
    inline = re.search(r"python-version:\s*\[([^\]]*)\]", workflow)
    if inline:
        return set(re.findall(r"\d+\.\d+", inline.group(1)))
    # Block-list form: capture the contiguous run of `- <version>` items that
    # follows the key, stopping at the first line that isn't a list item.
    block = re.search(
        r"python-version:[ \t]*\n((?:[ \t]*-[ \t]*['\"]?\d+\.\d+['\"]?[ \t]*\n)+)", workflow
    )
    assert block, "could not find a python-version matrix in the workflow"
    return set(re.findall(r"\d+\.\d+", block.group(1)))


def _test_matrix_minors() -> set[str]:
    """Python minor versions exercised by the reusable test workflow in test.yml."""
    return _parse_py_matrix((ROOT / ".github/workflows/test.yml").read_text())


def test_python_support_matrix_matches_classifiers():
    """The advertised support set and the CI matrix can't silently diverge (issue #17)."""
    declared = _declared_py_minors()
    assert declared, "no Python minor classifiers found"
    assert declared == _test_matrix_minors()


def test_matrix_parser_handles_inline_and_block_yaml():
    """The drift guard tolerates either YAML list style for python-version."""
    inline = '      python-version: ["3.11", "3.12", "3.13"]\n'
    block = '      python-version:\n        - "3.11"\n        - "3.12"\n        - "3.13"\n'
    expected = {"3.11", "3.12", "3.13"}
    assert _parse_py_matrix(inline) == expected
    assert _parse_py_matrix(block) == expected


def test_requires_python_floor_is_lowest_declared():
    requires = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    floor = re.search(r">=\s*(\d+\.\d+)", requires)
    assert floor, f"could not parse a >= floor from requires-python: {requires!r}"
    lowest = min(_declared_py_minors(), key=lambda v: tuple(map(int, v.split("."))))
    assert floor.group(1) == lowest


def test_plugin_manifest_valid_and_versioned():
    manifest = _load_json(".claude-plugin/plugin.json")
    assert manifest["name"] == "codex-in-claude"
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert manifest["version"] == pyproject["project"]["version"]


def test_marketplace_valid():
    market = _load_json(".claude-plugin/marketplace.json")
    names = [p["name"] for p in market["plugins"]]
    assert "codex-in-claude" in names


def test_mcp_json_launches_pinned_release():
    mcp = _load_json(".mcp.json")
    args = mcp["mcpServers"]["codex-in-claude"]["args"]
    assert "codex-in-claude-mcp" in args
    # Pinned to a versioned git tag for deliberate updates.
    assert any("@v" in a for a in args)


def test_pyproject_version_matches_package():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    # __version__ resolves from installed metadata; tolerate dev/unknown in source trees.
    if not __version__.endswith("+unknown"):
        assert __version__ == pyproject["project"]["version"]


def test_skill_present_with_frontmatter():
    skill = (ROOT / "skills/collaborating-with-codex/SKILL.md").read_text()
    assert skill.startswith("---")
    assert "name: collaborating-with-codex" in skill


def test_commands_present():
    cmd_dir = ROOT / "commands/codex"
    names = {p.stem for p in cmd_dir.glob("*.md")}
    assert {"status", "consult", "review", "delegate", "dry-run"} <= names


async def test_capabilities_match_registered_tools():
    caps = server.codex_capabilities()
    advertised = set(caps["active_tools"]) | set(caps["free_tools"])
    tool_names = {t.name for t in await server.mcp.list_tools()}
    assert advertised == tool_names


def test_tool_error_codes_cover_every_tool_and_are_valid():
    """Each advertised tool has an error-code list, and every code is a real ErrorCode."""
    from typing import get_args

    from codex_in_claude.schemas import ErrorCode

    caps = server.codex_capabilities()
    advertised = set(caps["active_tools"]) | set(caps["free_tools"])
    valid_codes = set(get_args(ErrorCode))
    assert set(server._TOOL_ERROR_CODES) == advertised
    for tool, codes in server._TOOL_ERROR_CODES.items():
        assert set(codes) <= valid_codes, tool


def test_delegate_async_command_present():
    cmd_dir = ROOT / "commands/codex"
    names = {p.stem for p in cmd_dir.glob("*.md")}
    assert "delegate-async" in names
