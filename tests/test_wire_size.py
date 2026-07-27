"""Wire-size budget for the discovery surface (audit 2, F2).

`tools/list` is what every client pays before its first useful call, and the least-capable
realistic client preloads all of it. This pins the serialized size so a future description or
schema addition has to be a deliberate, reviewed budget change rather than silent drift."""

import json

import pytest
from fastmcp import Client

from codex_in_claude.server import mcp

# Measured 2026-07-26 at schema-56: 79,242 bytes. Measured at schema-57 (idempotency_key and
# extra_context compressed into the registry): 76,911 bytes. Measured again at schema-57 after
# adding codex_capabilities' `detail` parameter (F1): 77,439 bytes. Measured once more after
# giving codex_capabilities its own accurate CapabilitiesDetailParam description (F1 review
# finding #2, replacing the shared DetailParam's inaccurate "omits the raw model text" wording
# for this no-model-call tool): 77,561 bytes — ~120 bytes for a materially more useful
# description on the tool that most needs it. Measured once more at schema-57 after a Copilot
# review fix clarifying that async_lifecycle appears only on the `*_async` tools, in both
# CapabilitiesDetailParam's description and the codex_capabilities docstring: 77,634 bytes.
# Raising this number is a reviewed decision — say why in the PR body.
TOOLS_LIST_BYTE_BUDGET = 78_000


@pytest.mark.anyio
async def test_tools_list_wire_size_budget():
    async with Client(mcp) as c:
        tools = await c.list_tools()
    payload = [t.model_dump(mode="json", exclude_none=True) for t in tools]
    size = len(json.dumps(payload, separators=(",", ":")))
    assert size <= TOOLS_LIST_BYTE_BUDGET, (
        f"tools/list is {size} bytes, over the {TOOLS_LIST_BYTE_BUDGET} budget. "
        "Compact a description or schema, or raise the budget deliberately."
    )
