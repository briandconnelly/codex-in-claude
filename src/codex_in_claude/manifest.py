"""Canonical manifest of the full agent-visible MCP surface (issue #140).

Snapshotting this manifest guards ``FINGERPRINT``: any change to the
client-visible surface — tool/resource/template/prompt wire shapes, the server
``instructions``, the ``codex://error-envelope`` content, or the
``codex_capabilities`` payload — moves the snapshot, forcing a conscious
``FINGERPRINT`` bump. See
``docs/superpowers/specs/2026-06-27-fingerprint-manifest-guard-design.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from typing import Any

from fastmcp import Client

from codex_in_claude.server import codex_capabilities, mcp

# Framework-owned noise dropped from every wire object's ``_meta`` (observed
# value ``{"fastmcp": {"tags": []}}``). Only this sub-key is removed; any
# application-owned ``_meta`` content is retained so it stays guarded.
_FASTMCP_META_KEY = "fastmcp"
# JSON-Schema arrays that are semantically sets — sorted for order-independence
# (enum order is explicitly non-contractual; see tests/test_server.py).
_SETLIKE_ARRAY_KEYS = frozenset({"enum", "required"})
# capabilities fields excluded: ``version`` is release-variable and
# ``fingerprint`` echoes FINGERPRINT itself (would self-reference the guard).
_CAPABILITIES_EXCLUDE = frozenset({"version", "fingerprint"})


def _sorted_by_json(items: list[Any]) -> list[Any]:
    return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))


def _canonicalize(obj: Any) -> Any:
    """Recursively strip ``_meta.fastmcp`` and sort set-like JSON-Schema arrays.

    Object-key ordering is handled at serialization time (``sort_keys=True``);
    order-sensitive arrays (``anyOf``/``oneOf``/``allOf``/``prefixItems``/...)
    are left untouched.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, raw_value in obj.items():
            value = raw_value
            if key == "_meta" and isinstance(raw_value, dict):
                value = {k: v for k, v in raw_value.items() if k != _FASTMCP_META_KEY}
                if not value:
                    continue
            cval = _canonicalize(value)
            if isinstance(cval, list) and (key in _SETLIKE_ARRAY_KEYS or key == "type"):
                cval = _sorted_by_json(cval)
            out[key] = cval
        return out
    if isinstance(obj, list):
        return [_canonicalize(v) for v in obj]
    return obj


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


async def build_manifest() -> dict[str, Any]:
    """Assemble the normalized, canonical agent-visible surface manifest."""
    async with Client(mcp) as client:
        tools = [_canonicalize(_dump(t)) for t in await client.list_tools()]
        resources = [_canonicalize(_dump(r)) for r in await client.list_resources()]
        templates = [_canonicalize(_dump(t)) for t in await client.list_resource_templates()]
        prompts = [_canonicalize(_dump(p)) for p in await client.list_prompts()]
        instructions = client.initialize_result.instructions
        # Envelope content is captured as an opaque serialized JSON string; its
        # internal determinism (ErrorCode enum order, field ordering) relies on
        # the ``ErrorCode = Literal[...]`` definition order and pydantic's stable
        # field ordering, not on ``_canonicalize``.
        envelope = [
            _canonicalize(_dump(c)) for c in await client.read_resource("codex://error-envelope")
        ]

    caps = {k: v for k, v in codex_capabilities().items() if k not in _CAPABILITIES_EXCLUDE}

    return {
        "tools": sorted(tools, key=lambda t: t["name"]),
        "resources": sorted(resources, key=lambda r: r["uri"]),
        "resource_templates": sorted(templates, key=lambda t: t["uriTemplate"]),
        "prompts": sorted(prompts, key=lambda p: p["name"]),
        "instructions": instructions,
        "error_envelope": envelope,
        "capabilities": _canonicalize(caps),
    }


def manifest_json(manifest: dict[str, Any]) -> str:
    """Canonical serialization used for both the golden fixture and the hash."""
    return (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    )


async def manifest_hash() -> str:
    """sha256 hex of the canonical manifest JSON."""
    payload = manifest_json(await build_manifest())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render() -> str:
    """Synchronous helper: the canonical manifest JSON (for regeneration)."""
    return manifest_json(asyncio.run(build_manifest()))


def main() -> None:  # pragma: no cover - thin CLI wrapper

    sys.stdout.write(render())


if __name__ == "__main__":  # pragma: no cover
    main()
