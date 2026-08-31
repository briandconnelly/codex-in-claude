"""The three synchronous MCP tools return envelopes, never protocol errors (#541).

`server._guard` wraps ASYNC tools. `codex_status`, `codex_capabilities` and
`codex_models` are plain `def` and were unguarded, so any exception inside them reached
the client as a raw MCP protocol error instead of the documented result envelope. That is
worst for `codex_status`, whose entire job is to answer "is codex usable right now?" --
the tool an agent reaches for BECAUSE something is already wrong was the one least able
to report it.

Every assertion here drives the tool through `server.mcp.call_tool`, not the Python
function: `_SemanticErrorMiddleware` is what turns an `ok: false` envelope into protocol
`isError`, and a direct call skips it -- so a direct-call test could pass while a real
MCP client still saw a success.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from codex_in_claude import config, schemas, server

SYNC_TOOLS = ("codex_status", "codex_capabilities", "codex_models")
_SCHEMA_FOR = {
    "codex_status": schemas.STATUS_SCHEMA,
    "codex_capabilities": schemas.CAPABILITIES_SCHEMA,
    "codex_models": schemas.MODEL_CATALOG_SCHEMA,
}
# The single internal each tool cannot complete without, used to inject a raise.
_RAISE_TARGET = {
    # NOT config.defaults: the error path builds its envelope from it too, so
    # patching that would make the guard itself raise and prove nothing.
    "codex_status": ("codex_in_claude.codex", "codex_version"),
    "codex_capabilities": ("codex_in_claude.server", "CapabilitiesResult"),
    "codex_models": ("codex_in_claude.server", "_model_catalog_payload"),
}


@pytest.mark.parametrize("tool", SYNC_TOOLS)
async def test_raise_through_returns_an_internal_error_envelope(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception inside the handler becomes a structured envelope, not a transport error."""
    module_name, attr = _RAISE_TARGET[tool]
    module = __import__(module_name, fromlist=[attr])

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("synthetic failure 541")

    monkeypatch.setattr(module, attr, _boom)

    res = await server.mcp.call_tool(tool, {})
    sc = res.structured_content
    assert sc["ok"] is False
    assert sc["error"]["code"] == "internal_error"
    # The protocol flag, not just the envelope: an MCP-conformant client keys off this.
    assert res.is_error is True


@pytest.mark.parametrize("tool", SYNC_TOOLS)
async def test_internal_error_envelope_conforms_to_the_advertised_output_schema(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error envelope must validate against the schema the tool advertises.

    These three already publish a success|error anyOf union, so no schema change was
    needed -- this pins that the guard's envelope actually lands inside the error branch.
    """
    module_name, attr = _RAISE_TARGET[tool]
    module = __import__(module_name, fromlist=[attr])
    monkeypatch.setattr(module, attr, lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))

    res = await server.mcp.call_tool(tool, {})
    Draft202012Validator(_SCHEMA_FOR[tool]).validate(res.structured_content)


@pytest.mark.parametrize("tool", SYNC_TOOLS)
async def test_internal_error_does_not_leak_the_exception_text_unbounded(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard's message is bounded, like every other error envelope."""
    module_name, attr = _RAISE_TARGET[tool]
    module = __import__(module_name, fromlist=[attr])
    monkeypatch.setattr(
        module, attr, lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("z" * 10_000))
    )

    res = await server.mcp.call_tool(tool, {})
    assert len(res.structured_content["error"]["message"]) <= 300


async def test_every_registered_tool_carries_the_guard_marker() -> None:
    """Completeness over the callables FastMCP ACTUALLY holds.

    Asserting over `_TOOL_POSTURE` would not prove this: `_guard` populates that map
    before it builds the wrapper, so a future tool whose decorators are stacked in the
    wrong order could register the UNGUARDED function while the posture map looked
    correct. Reading `FunctionTool.fn` back off the registry closes that gap.
    """
    names = [t.name for t in await server.mcp.list_tools()]
    assert names, "no tools registered — the completeness check would be vacuous"
    unguarded = []
    for name in names:
        registered = (await server.mcp.get_tool(name)).fn
        if not getattr(registered, server.GUARD_MARKER, False):
            unguarded.append(name)
    unguarded.sort()
    assert unguarded == [], (
        f"{unguarded} are registered without an exception guard; an exception inside them "
        "reaches the client as a raw protocol error instead of a result envelope (#541)."
    )


async def test_guard_marker_is_absent_from_an_unguarded_function() -> None:
    """Positive control for the marker itself.

    Without this, `test_every_registered_tool_carries_the_guard_marker` could pass because
    the marker is trivially truthy on every object rather than because the guards are
    applied.
    """

    def _bare() -> dict:
        return {}

    assert not getattr(_bare, server.GUARD_MARKER, False)
    registered = (await server.mcp.get_tool("codex_status")).fn
    assert getattr(registered, server.GUARD_MARKER, False)


@pytest.mark.parametrize("tool", SYNC_TOOLS)
async def test_posture_reported_on_invalid_arguments_ignores_operator_defaults(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """These tools report their OWN pinned posture, not the operator's defaults.

    README documents that "every shipped tool pins its own tier and sandbox and ignores"
    CODEX_IN_CLAUDE_TIER_DEFAULT/SANDBOX_DEFAULT. Before #541 the three unguarded tools
    were absent from `_TOOL_POSTURE`, so an invalid-argument envelope fell back to
    `config.defaults()` and reported whatever the operator had configured -- breaking that
    promise. Registering them with the guard fixes it as a direct consequence.
    """
    monkeypatch.setenv("CODEX_IN_CLAUDE_TIER_DEFAULT", "propose")
    monkeypatch.setenv("CODEX_IN_CLAUDE_SANDBOX_DEFAULT", "workspace-write")
    # Positive control: the defaults really are what we just set, so a pass below is the
    # tool pinning its posture and not the env var failing to take effect.
    d = config.defaults()
    assert (d.tier, d.sandbox) == ("propose", "workspace-write")

    res = await server.mcp.call_tool(tool, {"definitely_not_a_param": 1})
    meta = res.structured_content["meta"]
    assert (meta["tier"], meta["sandbox"]) == ("consult", "read-only")


@pytest.mark.parametrize("tool", SYNC_TOOLS)
async def test_capabilities_advertises_internal_error_for_the_sync_tools(tool: str) -> None:
    """A guarded tool can return internal_error, so its advertised contract must say so."""
    res = await server.mcp.call_tool("codex_capabilities", {"detail": "full"})
    detail = next(t for t in res.structured_content["tool_details"] if t["name"] == tool)
    assert "internal_error" in detail["error_codes"]


async def test_status_reports_a_readiness_fact_when_codex_is_an_executable_directory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #541 end-to-end shape, through the MCP boundary.

    An executable DIRECTORY named `codex` as the only PATH entry used to raise
    PermissionError straight out of `codex_status`. It must now be a readiness FACT.

    The message stays the generic not-found text: distinguishing EACCES from a genuinely
    absent binary means threading a spawn-failure reason up through `codex_version()`,
    whose documented contract is None on ANY failure. Filed as #568 rather than widened
    into this fix; update this assertion rather than deleting it when that lands.
    """
    from codex_in_claude import binpath, preflight

    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("CODEX_IN_CLAUDE_CODEX_BIN", raising=False)
    binpath.reset_cache()
    preflight._cache = None

    res = await server.mcp.call_tool("codex_status", {})
    sc = res.structured_content
    assert res.is_error is False
    assert sc["ok"] is True
    assert sc["codex_found"] is False
    assert sc["ready"] is False
    assert sc["readiness_detail"] == "codex CLI not found."
    Draft202012Validator(schemas.STATUS_SCHEMA).validate(sc)
    # Nothing about the failure leaks the probed path into the envelope.
    assert str(tmp_path) not in json.dumps(sc)
