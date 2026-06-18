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


def _ci_matrix_minors() -> set[str]:
    """Python minor versions exercised by the CI gate matrix in ci.yml."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    m = re.search(r"python-version:\s*\[([^\]]*)\]", ci)
    assert m, "could not find python-version matrix in .github/workflows/ci.yml"
    return set(re.findall(r"\d+\.\d+", m.group(1)))


def test_python_support_matrix_matches_classifiers():
    """The advertised support set and the CI matrix can't silently diverge (issue #17)."""
    declared = _declared_py_minors()
    assert declared, "no Python minor classifiers found"
    assert declared == _ci_matrix_minors()


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


def test_delegate_async_command_present():
    cmd_dir = ROOT / "commands/codex"
    names = {p.stem for p in cmd_dir.glob("*.md")}
    assert "delegate-async" in names
