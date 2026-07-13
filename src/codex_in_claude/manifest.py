"""Canonical manifest of the full agent-visible MCP surface (issue #140).

Snapshotting this manifest guards ``FINGERPRINT``: any externally observable
change to a category in ``FINGERPRINT_COVERS`` (``schemas.py``) moves the
snapshot. That tuple is the authority — do not re-list its categories here or
anywhere else, or the copy drifts as the tuple grows. This is an *acknowledgment*
guard: the snapshot test fails on any covered change so it cannot ship
unreviewed, and its message directs the author to bump ``FINGERPRINT``; it does
not mechanically force the integer bump (the snapshot and ``FINGERPRINT`` are
independently editable). The rules for both questions — bump? breaking? — live
in ``AGENTS.md`` under Versioning.
"""

from __future__ import annotations

import asyncio
import contextlib
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
# capabilities fields excluded: ``version`` and ``server_version`` are both
# release-variable (the latter added alongside ``server_version`` on every other
# result envelope; it echoes ``version`` in the live payload) and ``fingerprint``
# echoes FINGERPRINT itself (would self-reference the guard).
_CAPABILITIES_EXCLUDE = frozenset({"version", "server_version", "fingerprint"})


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


def _envelope_block(content: Any) -> dict[str, Any]:
    """Canonicalize one error-envelope content block.

    The block's ``text`` carries the envelope JSON Schema as a serialized string.
    Parse it so its embedded set-like arrays (``enum``/``required``/multi-``type``,
    e.g. the ErrorCode enum) are normalized by ``_canonicalize`` like the rest of
    the manifest, rather than left order-dependent inside an opaque string."""
    block = _canonicalize(_dump(content))
    text = block.get("text")
    if isinstance(text, str):
        with contextlib.suppress(json.JSONDecodeError):
            block["text"] = _canonicalize(json.loads(text))
    return block


async def build_manifest() -> dict[str, Any]:
    """Assemble the normalized, canonical agent-visible surface manifest."""
    async with Client(mcp) as client:
        tools = [_canonicalize(_dump(t)) for t in await client.list_tools()]
        resources = [_canonicalize(_dump(r)) for r in await client.list_resources()]
        templates = [_canonicalize(_dump(t)) for t in await client.list_resource_templates()]
        prompts = [_canonicalize(_dump(p)) for p in await client.list_prompts()]
        # Capture the whole client-visible initialize response (serverInfo name,
        # protocolVersion, advertised capabilities, instructions) — not just
        # instructions — minus the release-variable server version.
        initialize = _canonicalize(_dump(client.initialize_result))
        server_info = initialize.get("serverInfo")
        if isinstance(server_info, dict):
            server_info.pop("version", None)
        envelope = [
            _envelope_block(c) for c in await client.read_resource("codex://error-envelope")
        ]
        result_meta = [
            _envelope_block(c) for c in await client.read_resource("codex://result-meta")
        ]
        capabilities_result = [
            _envelope_block(c) for c in await client.read_resource("codex://capabilities-result")
        ]
        status_result = [
            _envelope_block(c) for c in await client.read_resource("codex://status-result")
        ]

    caps = {k: v for k, v in codex_capabilities().items() if k not in _CAPABILITIES_EXCLUDE}

    return {
        "tools": sorted(tools, key=lambda t: t["name"]),
        "resources": sorted(resources, key=lambda r: r["uri"]),
        "resource_templates": sorted(templates, key=lambda t: t["uriTemplate"]),
        "prompts": sorted(prompts, key=lambda p: p["name"]),
        "initialize": initialize,
        "error_envelope": envelope,
        "result_meta": result_meta,
        "capabilities_result": capabilities_result,
        "status_result": status_result,
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
