"""Guard: the manifest snapshot covers the full agent-visible surface (issue #140)."""

import re
from pathlib import Path

from fastmcp import Client

from codex_in_claude import manifest, server

_FIXTURE = Path(__file__).parent / "fixtures" / "manifest_snapshot.json"

# sha256 of the canonical manifest JSON; regenerate per the test failure message.
EXPECTED_MANIFEST_HASH = "23e64d6ea75bae0174f584c906c01303d58200af2dbb7fd1146a767b496d2963"


def test_canonicalize_strips_only_fastmcp_meta():
    # An app-owned _meta key survives; the fastmcp sub-key is removed.
    assert manifest._canonicalize({"_meta": {"fastmcp": {"tags": []}, "app": {"k": 1}}}) == {
        "_meta": {"app": {"k": 1}}
    }
    # A _meta that is only fastmcp noise is dropped entirely.
    assert manifest._canonicalize({"_meta": {"fastmcp": {"tags": []}}}) == {}


def test_canonicalize_sorts_setlike_arrays():
    canon = manifest._canonicalize(
        {"enum": ["c", "a", "b"], "required": ["z", "a"], "type": ["string", "null"]}
    )
    assert canon["enum"] == ["a", "b", "c"]
    assert canon["required"] == ["a", "z"]
    assert canon["type"] == ["null", "string"]


def test_canonicalize_preserves_order_sensitive_arrays():
    # anyOf is order-sensitive in JSON Schema and must NOT be reordered.
    src = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert manifest._canonicalize(src)["anyOf"] == [{"type": "string"}, {"type": "null"}]


async def test_build_manifest_covers_full_surface():
    m = await manifest.build_manifest()
    caps = server.codex_capabilities()
    expected_tools = set(caps["active_tools"]) | set(caps["free_tools"])
    assert {t["name"] for t in m["tools"]} == expected_tools
    # All manifest sections must be present as keys.
    assert set(m) >= {
        "tools",
        "resources",
        "resource_templates",
        "prompts",
        "initialize",
        "error_envelope",
        "result_meta",
        "capabilities_result",
        "status_result",
        "capabilities",
    }
    for section in (
        "resources",
        "initialize",
        "error_envelope",
        "result_meta",
        "capabilities_result",
        "status_result",
        "capabilities",
    ):
        assert m[section], f"manifest section {section} is empty"


async def test_fingerprint_covers_accounts_for_every_section():
    """`FINGERPRINT_COVERS` is advertised as an authoritative disclosure of what the
    fingerprint guards (#178, audit F6), so it must stay complete relative to the actual
    guard — the manifest surface. Every manifest section maps to at least one coverage
    token, and the tokens are exactly that union: a newly guarded section (or a token
    with no section) fails here until the disclosure is reconciled."""
    from codex_in_claude.schemas import FINGERPRINT_COVERS

    # Each canonical manifest section → the coverage token(s) that disclose it.
    section_tokens = {
        "tools": {
            "tool_names",
            "tool_input_schemas",
            "tool_output_schemas",
            "tool_descriptions",
            "tool_annotations",
            "error_codes",
            "value_enums",
        },
        "resources": {"resource_metadata"},
        "resource_templates": {"resource_templates"},
        "prompts": {"prompts"},
        "initialize": {"initialize_response"},
        "error_envelope": {"error_envelope_schema"},
        "result_meta": {"result_meta_schema"},
        "capabilities_result": {"capabilities_result_schema"},
        "status_result": {"status_result_schema"},
        "params": {"parameter_contracts"},
        "capabilities": {"capabilities_payload", "capability_guarantees"},
    }
    m = await manifest.build_manifest()
    # A new manifest section must gain a mapping entry (and thus a coverage token).
    assert set(section_tokens) == set(m), (
        "manifest sections changed; update FINGERPRINT_COVERS and this mapping in lockstep"
    )
    # The advertised tokens are exactly the union of the section tokens — no token that
    # discloses nothing guarded, no guarded section left undisclosed.
    assert set().union(*section_tokens.values()) == set(FINGERPRINT_COVERS)


def _iter_enums(obj):
    """Yield every JSON-Schema ``enum`` array found anywhere in ``obj``."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "enum" and isinstance(value, list):
                yield value
            yield from _iter_enums(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_enums(item)


async def test_build_manifest_excludes_dynamic_fields():
    m = await manifest.build_manifest()
    # Release-variable / self-referential capability fields are excluded.
    assert "version" not in m["capabilities"]
    assert "fingerprint" not in m["capabilities"]
    # server_version echoes `version` (both == __version__) in the live capabilities
    # payload; excluded for the identical release-variable reason, or every release
    # would move the golden snapshot with no contract change (see manifest.py).
    assert "server_version" not in m["capabilities"]
    # Resource METADATA for codex://models is present; its dynamic CONTENT is not read.
    uris = {r["uri"] for r in m["resources"]}
    assert "codex://models" in uris


async def test_manifest_drops_exactly_the_declared_capability_fields():
    """The membership assertions above prove the declared exclusions ARE excluded; they
    cannot catch the opposite failure — an exclusion set silently WIDENED, which would
    drop real contract surface out of the guard without moving the snapshot's field list.
    Asserted as set EQUALITY against the live payload, so any added `.pop()`/filter in
    build_manifest's capabilities handling fails here (#337)."""
    m = await manifest.build_manifest()
    dropped = set(server.codex_capabilities()) - set(m["capabilities"])
    assert dropped == manifest._RELEASE_VARIABLE_EXCLUDE | manifest._SELF_REFERENTIAL_EXCLUDE
    # Positive control: the live payload really does carry the excluded keys, so the
    # difference above is a genuine drop and not an empty-set comparison.
    assert set(server.codex_capabilities()) >= manifest._RELEASE_VARIABLE_EXCLUDE


async def test_manifest_drops_exactly_the_declared_server_info_fields():
    """Same widening guard for the initialize response: `serverInfo.version` is popped
    inline, so only equality against the live serverInfo catches a second pop (#337)."""
    m = await manifest.build_manifest()
    async with Client(server.mcp) as client:
        live_info = manifest._dump(client.initialize_result).get("serverInfo", {})
    dropped = set(live_info) - set(m["initialize"]["serverInfo"])
    assert dropped == {"version"}


def test_release_variable_exclusions_are_disclosed_in_the_coverage_description():
    """The manifest's release-variable exclusions and the agent-visible carve-out must
    stay in lockstep: widening one without disclosing the other re-opens the #337 gap.
    `serverInfo.version` is popped inline rather than declared, so it is named directly."""
    from codex_in_claude.schemas import _FINGERPRINT_COVERS_DESC

    assert manifest._RELEASE_VARIABLE_EXCLUDE  # positive control: non-empty
    # Parse the disclosed field list rather than searching the whole sentence: a loose
    # search would let a future excluded field named e.g. `release` be "disclosed" by the
    # prose phrase "Release identity", passing while saying nothing about that field.
    clause = re.search(
        r"Release identity is excluded: (.+?) change every release", _FINGERPRINT_COVERS_DESC
    )
    assert clause, "the coverage description no longer carries a parseable exclusion clause"
    disclosed = {name.strip() for name in clause.group(1).split(",")}
    # `serverInfo.version` lives in the initialize response, not this payload, and is popped
    # inline rather than declared in a constant — so it is expected by name.
    assert disclosed == manifest._RELEASE_VARIABLE_EXCLUDE | {"serverInfo.version"}
    # The self-referential `fingerprint` removal is deliberately NOT disclosed as a
    # carve-out: its value changes precisely BECAUSE something covered changed.
    assert {"fingerprint"} == manifest._SELF_REFERENTIAL_EXCLUDE


async def test_build_manifest_captures_error_envelope_schema():
    """The error-envelope schema (where ErrorCode lives) is captured AND parsed,
    so its embedded code enum is normalized rather than left as an opaque string.
    Asserted structurally — not against a specific ErrorCode literal — so a
    legitimate ErrorCode change is flagged by the golden snapshot, not here."""
    m = await manifest.build_manifest()
    assert m["error_envelope"], "error_envelope section is empty"
    # C2: each block's content was parsed from its `text` string into JSON, so
    # _canonicalize reaches the embedded set-like arrays.
    parsed = [b["text"] for b in m["error_envelope"] if isinstance(b.get("text"), dict)]
    assert parsed, "error-envelope content was not parsed into JSON"
    # The schema carries at least one non-empty enum (the ErrorCode set among them).
    assert any(enum for block in parsed for enum in _iter_enums(block))


async def test_build_manifest_captures_result_meta_schema():
    """The result-meta schema (the full Meta contract the opaque wire stub hides) is
    captured AND parsed, so a change to it moves the snapshot and is flagged for the
    FINGERPRINT bump — the guard is not weakened by opaquing meta on the wire (F1/#173)."""
    m = await manifest.build_manifest()
    assert m["result_meta"], "result_meta section is empty"
    parsed = [b["text"] for b in m["result_meta"] if isinstance(b.get("text"), dict)]
    assert parsed, "result-meta content was not parsed into JSON"
    # The full Meta shape carries the fields the wire stub elides.
    assert any("tier" in block.get("properties", {}) for block in parsed)


async def test_build_manifest_captures_capabilities_result_schema():
    m = await manifest.build_manifest()
    assert m["capabilities_result"], "capabilities_result section is empty"
    parsed = [b["text"] for b in m["capabilities_result"] if isinstance(b.get("text"), dict)]
    assert parsed, "capabilities-result content was not parsed into JSON"
    assert any("ToolCapability" in block.get("$defs", {}) for block in parsed)


async def test_build_manifest_captures_status_result_schema():
    m = await manifest.build_manifest()
    assert m["status_result"], "status_result section is empty"
    parsed = [b["text"] for b in m["status_result"] if isinstance(b.get("text"), dict)]
    assert parsed, "status-result content was not parsed into JSON"
    assert any("RateLimit" in block.get("$defs", {}) for block in parsed)


async def test_build_manifest_captures_params_contracts():
    """The codex://params body (compressed param summaries + full text) is captured
    AND parsed (#333), so a weakened summary or altered full contract moves the
    snapshot and is flagged for the FINGERPRINT bump."""
    m = await manifest.build_manifest()
    assert m["params"], "params section is empty"
    parsed = [b["text"] for b in m["params"] if isinstance(b.get("text"), dict)]
    assert parsed, "params content was not parsed into JSON"
    # The idempotency_key contract (the largest moved-out detail) is present with both
    # its inline summary and its full text.
    assert any("idempotency_key" in block.get("params", {}) for block in parsed)


async def test_build_manifest_captures_initialize_without_version():
    """The full initialize response is guarded (serverInfo, protocolVersion,
    advertised capabilities), minus only the release-variable server version."""
    m = await manifest.build_manifest()
    init = m["initialize"]
    assert init.get("serverInfo", {}).get("name") == "codex-in-claude"
    assert "version" not in init.get("serverInfo", {})
    assert init.get("protocolVersion")
    assert "capabilities" in init


async def test_build_manifest_strips_fastmcp_meta_from_tools():
    m = await manifest.build_manifest()
    for tool in m["tools"]:
        assert "fastmcp" not in tool.get("_meta", {})


async def test_manifest_json_is_deterministic():
    a = manifest.manifest_json(await manifest.build_manifest())
    b = manifest.manifest_json(await manifest.build_manifest())
    assert a == b
    assert a.endswith("\n")


async def test_manifest_hash_returns_sha256_hex():
    h = await manifest.manifest_hash()
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_render_returns_canonical_json():
    result = manifest.render()
    assert result.endswith("\n")
    assert result.startswith("{")


async def test_manifest_matches_golden():
    current = manifest.manifest_json(await manifest.build_manifest())
    assert current == _FIXTURE.read_text(encoding="utf-8"), (
        "agent-visible surface changed — review the snapshot diff, then in the SAME "
        "commit: bump FINGERPRINT (schema-N) in schemas.py, regenerate the fixture "
        "(`uv run python -m codex_in_claude.manifest > tests/fixtures/manifest_snapshot.json`), "
        "and add a CHANGELOG entry under [Unreleased]."
    )


async def test_manifest_hash_is_pinned():
    assert await manifest.manifest_hash() == EXPECTED_MANIFEST_HASH
