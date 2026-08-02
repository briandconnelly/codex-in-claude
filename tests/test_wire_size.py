"""Wire-size budget for the discovery surface (audit 2, F2).

`tools/list` is what every client pays before its first useful call, and the least-capable
realistic client preloads all of it. This bounds the serialized size with a ceiling — growth
within the remaining headroom passes, and exceeding it forces a future description or schema
addition to be a deliberate, reviewed budget change rather than silent drift."""

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
# above the measured value. Measured again 2026-07-26 (F8 follow-on: `roots_source` was
# added to the flat `codex_dry_run`/`codex_delegate_dry_run` output schemas, ~222 bytes
# undocumented at the time): 82,759 bytes. Measured once more the same day (final-review fix
# wave: the three PAID-block descriptions changed "spends Codex quota on every call" to
# "...every new call", resolving a contradiction with idempotency_key's "no new spend" replay
# wording): 82,771 bytes — still within budget, no further change. Measured once more the same
# day (Copilot review of #383, merged forward onto this branch: normalized every cost marker
# to the literal canonical token — `codex_transfer`'s `FREE —` and `codex_capabilities`'s
# line-wrapped `Free —` corrected, and the three async active tools gained their own `PAID —`
# block naming the right preview tool): 83,281 bytes — budget raised to the next 500 above the
# measured value. Measured once more after merging forward a Copilot review fix on #382
# clarifying that async_lifecycle appears only on the `*_async` tools, in both
# CapabilitiesDetailParam's description and the codex_capabilities docstring: 83,354 bytes —
# still within budget, no further change. Measured again 2026-07-27 (#396: codex_job_list's
# `limit` became nullable so a no-arg call returns every retained job — the integer-or-null
# schema costs ~30 bytes, the rest is the reworded param description, docstring, and the
# now-explicit "running jobs are never evicted" ceiling caveat): 83,895 bytes — budget raised
# to the next 500 above the measured value. Spent deliberately: the wording it buys is what
# tells a client the default is complete and that `truncated` is a cap with no cursor.
# Measured again 2026-07-29 (#411: `question`/`task` must be non-blank, so the three param
# descriptions — QuestionParam, TaskParam, TaskDryRunParam, across five tools — state the rule
# and when it is enforced): 84,292 bytes — budget raised to the next 500 above the measured
# value. Spent deliberately, and the cheapest wording that carries the rule was chosen (the
# first draft cost ~145 bytes more): the constraint is enforced at runtime rather than by a
# schema `minLength`, so these descriptions are the ONLY place a client can discover it before
# spending. It buys back a whole class of wasted paid calls — the reported repro burned 23,120
# tokens on a whitespace-only question.
# Measured again 2026-08-01 (#414: the three sync tools' Progress & recovery paragraphs now name
# `codex_job_status` as the recovery chain's polling step and state that some MCP clients
# background a long call before the server's own deadline, so `timeout_seconds` bounds the run,
# not necessarily the client's inline wait — the job record covers that case too): 84,740 bytes —
# budget raised to the next 500 above the measured value.
TOOLS_LIST_BYTE_BUDGET = 85_000


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
