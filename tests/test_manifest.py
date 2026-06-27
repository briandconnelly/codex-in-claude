"""Guard: the manifest snapshot covers the full agent-visible surface (issue #140)."""

from pathlib import Path

from codex_in_claude import manifest, server

_FIXTURE = Path(__file__).parent / "fixtures" / "manifest_snapshot.json"

# Pinned in Step 3 below, after generating the fixture.
EXPECTED_MANIFEST_HASH = "36e769a2326814d52315273e7f7bc12daf29b9667b27378e57f260fbd4115553"


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
    for section in ("resources", "instructions", "error_envelope", "capabilities"):
        assert m[section], f"manifest section {section} is empty"


async def test_build_manifest_excludes_dynamic_fields():
    m = await manifest.build_manifest()
    # Release-variable / self-referential capability fields are excluded.
    assert "version" not in m["capabilities"]
    assert "fingerprint" not in m["capabilities"]
    # Resource METADATA for codex://models is present; its dynamic CONTENT is not read.
    uris = {r["uri"] for r in m["resources"]}
    assert "codex://models" in uris
    # The static error-envelope content (where ErrorCode lives) IS captured.
    blob = manifest.manifest_json(m)
    assert "invalid_workspace_root" in blob  # an ErrorCode literal


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
