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
# description on the tool that most needs it. Raising this number is a reviewed decision — say
# why in the PR body. Measured at schema-58 (N1: a PAID/spend or Free — no model call. marker
# added to every tool description, closing the 7-tool gap a keyword sweep found): 78,072 bytes —
# 511 bytes for a consistent, single-token cost signal readable from tools/list alone, budget
# raised accordingly. Measured once more, still at schema-58, after correcting the PAID block's
# dry-run pointer (codex_delegate now names codex_delegate_dry_run instead of codex_dry_run;
# codex_consult's now states no preview tool exists instead of naming the wrong one): 78,111
# bytes — still within budget, no further change. Measured 2026-07-26 (F3 + F9: every tool
# gained a `title` and a namespaced `_meta` stability tier, so tools/list alone shows which
# tools are experimental): 79,723 bytes — ~1,600 bytes for 17 titles plus 17 `_meta` blocks;
# budget raised to the next 500 above the measured value. Measured again 2026-07-26 (F7:
# JobStarted gained a required `follow_up: Repair` field, so the three `*_async` tools'
# outputSchema each grew the nested Repair shape): 81,739 bytes — budget raised to the next
# 500 above the measured value. Measured again 2026-07-26 (F5: codex_job_list gained
# `limit`/`status` input params plus `truncated`/`truncation_hint` output fields, so its
# outputSchema and parameter list both grew): 82,537 bytes — budget raised to the next 500
# above the measured value.
TOOLS_LIST_BYTE_BUDGET = 83_000


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
