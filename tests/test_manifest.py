"""Guard: the manifest snapshot covers the full agent-visible surface (issue #140)."""

from pathlib import Path

from codex_in_claude import manifest, server

_FIXTURE = Path(__file__).parent / "fixtures" / "manifest_snapshot.json"

# sha256 of the canonical manifest JSON; regenerate per the test failure message.
EXPECTED_MANIFEST_HASH = "5bfbd5126f0732b29598ed63c5d2e021bcd2d3f1bb8cfc7940b3fa0d87d6c23a"


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
        "capabilities",
    }
    for section in ("resources", "initialize", "error_envelope", "result_meta", "capabilities"):
        assert m[section], f"manifest section {section} is empty"


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
    # Resource METADATA for codex://models is present; its dynamic CONTENT is not read.
    uris = {r["uri"] for r in m["resources"]}
    assert "codex://models" in uris


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
