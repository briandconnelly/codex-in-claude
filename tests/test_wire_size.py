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
# Measured again 2026-08-01 (#423: `CapabilitiesResult.protocol_revision` — this schema is not on
# the `tools/list` wire path (its description is stripped from the advertised `outputSchema` by
# `_strip_schema_noise`), but the tool's `outputSchema` embeds the bare `{"type":"string"}`
# property): 84,778 bytes (+38 B) — still within budget, no further change.
# Measured again 2026-08-01 (#426: `CapabilitiesResult.annotations_reading` — same treatment as
# `protocol_revision` above, a second bare `{"type":"string"}` property on `codex_capabilities`'
# outputSchema; its description is likewise stripped from the advertised schema): 84,818 bytes
# (+40 B) — still within budget, no further change.
# Measured again 2026-08-02 (#427: the six egress tool docstrings converged onto one shared
# skills-discovery sentence pair — `cli_contract.SKILLS_DISCOVERY_FACT`/
# `SKILLS_DISCOVERY_FACT_FULL`. First pass (before tightening) grew this to 85,299 bytes, over
# budget — a
# tighter canonical pair (dropped "both"/"that workspace's"/redundant "your", kept
# "resolved") plus a minimal per-delegate addendum (the pre-existing "throwaway worktree
# seeded from tracked state" sentence earlier in each docstring already disclosed
# tracked/seeded; the only genuinely new fact needed here is that delegate's workspace IS
# the worktree, and that scrubbing it doesn't exclude $CODEX_HOME/skills/) recovered the
# rest): 84,818 -> 84,975 bytes (+157 B, closing wording gaps between sites without adding
# net new disclosure bulk) — fits under the existing budget, no raise.
# Measured again 2026-08-02 (#342: `codex_dry_run`/`codex_delegate_dry_run` both gained a
# `deadline_advisory` output field, described identically on both, plus one docstring
# sentence per tool — a genuinely new disclosure, unlike C4's wording-only tightening
# above: it tells a client BEFORE it spends that a previewed call's size or reasoning
# effort risks the synchronous deadline, and names the tool's own `_async` alternative):
# 86,418 bytes (+1,443 B, incl. an accuracy reword of the field description) — over
# budget; budget raised to the next 500 above the
# measured value.
# Measured again 2026-08-02 (#342 round-3, Codex review concerns/1 medium, verified
# valid: the generic "this tool's `_async` variant" phrasing pointed a
# codex_dry_run/codex_delegate_dry_run caller at a nonexistent codex_dry_run_async /
# codex_delegate_dry_run_async — the field, both docstrings, and REFERENCE.md now name
# the previewed PAID tool's own `_async` counterpart verbatim instead, a longer but
# genuinely more correct disclosure, not flab): 86,749 bytes (+331 B) — over budget;
# budget raised to the next 500 above the measured value.
# Measured again 2026-08-02 (#433: `Coverage` gained `redaction: RedactionSummary |
# None`, a new nested object with three properties, on both `codex_review_changes`'
# and `codex_dry_run`'s outputSchema). Field descriptions on RedactionSummary are NOT
# in `_KEPT_DESCRIPTIONS`, so they cost nothing here — the whole delta is JSON Schema
# STRUCTURE: the object is INLINED directly into `coverage.redaction` (no `$ref`/`$defs`
# on the wire — verified against the live `tools/list` payload; `$defs` exists only in
# the result-format snapshot's separate pydantic-schema view), so the anyOf-null
# wrapper plus three typed properties are duplicated in full across both tools'
# outputSchema: 87,257 bytes (+508 B) — over budget; budget raised to the next 500 above
# the measured value.
# Measured again 2026-08-02 (#433 review C4: `RedactionSummary.inline_masks` gained a
# `Field(ge=0)` constraint — a validated invariant, not description prose, so it DOES
# reach the wire as a `"minimum":0` property, duplicated across the same two tools'
# outputSchema as the row above): 87,281 bytes (+24 B) — still within budget, no
# further change.
# Measured again 2026-08-19 (#501: every egress tool description now states HOW a skill's
# body arrives — `cli_contract.SKILL_BODY_FACT` — not just that skills are discovered.
# Genuinely new disclosure on four of the six, not a reword: `codex_consult_async` and
# `codex_review_changes_async` previously named the skills roots with no mechanism at all,
# and the sync review/delegate descriptions stated the body without the metadata half that
# makes it legible. This is the security-load-bearing half of the caveat — metadata reaching
# the model is comparatively harmless, the body is the egress — so it is disclosure the
# client cannot get anywhere else before it spends): 87,794 bytes (+513 B) — over budget;
# budget raised to the next 500 above the measured value.
# Measured again 2026-08-19 (#472: the canonical skills-discovery sentence
# (`cli_contract.SKILLS_DISCOVERY_FACT`) now names all THREE `AGENTS.md` sources codex
# auto-loads, where it previously named only one. The two additions are genuinely new
# disclosure, both understated in the UNSAFE direction and both probed on 0.148.0: inside
# a repository the load walks up from the resolved workspace to the repository root — so a
# caller narrowing `workspace_root` to a subdirectory precisely to bound egress still ships
# the repo-root file — and a user-global `$CODEX_HOME/AGENTS.override.md`/`AGENTS.md` loads
# on every call from any workspace, suppressed by neither `--ignore-user-config` nor
# `project_doc_max_bytes=0`. This is what a client reads BEFORE it spends to decide what it
# is about to send to OpenAI, so the bytes buy disclosure it can get nowhere else):
# 88,682 bytes (+888 B) — over budget; budget raised to the next 500 above the measured
# value. Then +96 B on a Copilot review point, still inside that raise (88,778, no further
# change): the canonical sentence said `$CODEX_HOME/AGENTS.override.md or AGENTS.md`, whose
# bare second filename could be read as the workspace/ancestor file in a sentence that names
# both — it now repeats the path and says `, else`, which also carries the masking precedence
# `or` did not.
# Measured again 2026-08-19 (#509 — the read-scope correction. Several descriptions scoped
# Codex's reads to the "resolved working dir" or "repo files"; a reader takes that as a
# bound and it is not one. A run under this plugin's own flags read a file in $HOME on both
# sandbox tiers, while a write in the same read-only run was refused — the sandbox bounds
# writes, not reads. Every carrier now states `cli_contract.READ_SCOPE_FACT`, which replaces
# a ~70-byte scoped clause with a 208-byte accurate one across 13 carriers): 88,778 ->
# 91,195 bytes (+2,417 B) — over budget; budget raised to the next 1,000 above the measured
# value. Then +252 B on a Codex review point, still inside that raise (91,447, no further
# change): `WorkspaceRootParam`'s new clause is shared by fourteen tools, six of which never
# run Codex, so it now says "on an active call it selects where Codex works" rather than
# implying the free lifecycle and dry-run tools cause reads.
# The fact was NOT compressed to fit: the only clause short enough to drop is "no
# choice of workspace is a read boundary", which is precisely the half that retires the
# "narrow the workspace to bound egress" mitigation. Trimming the plugin's central safety
# disclosure to hold a byte line is the wrong trade; the raise is the right one.
# Measured again 2026-08-19 (#513 — the dry-run previews. Both descriptions framed the
# preview as "what a call would send", which reads as a bound on the paid call's egress. It
# is not one: a dry run never invokes Codex, so the model's own reads have not happened yet
# and cannot be listed. Both docstrings now carry `cli_contract.PREVIEW_SCOPE_FACT` AND
# `READ_SCOPE_FACT`. The read fact is carried here, unlike the six egress tools, because
# these two are NOT egress tools and had no read-scope disclosure of their own: the preview
# fact is three negatives, and without the affirmative half a reader learns the preview is
# not a bound but never what the unbounded channel reaches. This is the FREE tool a client
# reads precisely to decide whether to spend, so the bytes buy the disclosure at the moment
# the decision is made): 91,447 -> 92,289 bytes (+842 B) — over budget; budget raised to the
# next 1,000 above the measured value. Compressing was rejected for the same reason as #509:
# the only clause short enough to drop is the affirmative read half, which is the half that
# tells the reader what a clean preview failed to cover.
# Measured again 2026-08-19 (#523 — the propose-tier write scope. Both delegate
# descriptions promised "writes only inside a throwaway worktree", but codex's
# workspace-write default also grants the OS temp roots; both now carry
# `cli_contract.WORKSPACE_WRITE_SCOPE_FACT` plus the persistence clause — temp-root
# writes are neither in the diff nor cleaned up — replacing the two exclusivity
# sentences they retire): 92,289 -> 92,599 bytes (+310 B) — still within budget, no
# further change.
# Measured again 2026-08-20 (#529 — the machine-identifier control-character boundary. Six
# parameters (job_id, base, commit, model on two annotations, transcript_path) now advertise
# `config.CONTROL_CHAR_FREE_PATTERN`, and four carry a one-clause note that the value is
# REJECTED rather than stripped): 92,599 -> 94,130 bytes (+1,531 B) — over budget; budget
# raised to the next 1,000 above the measured value. Compacting was considered and rejected:
# roughly half the growth is the advertised `pattern` itself, which is the machine-readable
# half a client validates against before spending and so cannot be dropped at all. The prose
# half was already cut from four sentences to one clause; what remains states the disposition
# (rejected, not stripped), which is the fact a caller cannot infer from the regex — a regex
# says which values fail, not that the server refuses rather than silently repairs them.
TOOLS_LIST_BYTE_BUDGET = 95_000

# The measured tools/list size as of the last deliberate review above — NOT a second gate.
# The budget assertion below is the only hard failure; this exists purely so the assertion's
# failure message can show how far a later change has drifted from the last reviewed
# measurement, without requiring a diff against this file's comment history. Revisit this
# value (re-measure and update it in the same commit) whenever a DELIBERATE change to
# tools/list's measured size lands — i.e. whenever a new row is added to the budget-history
# comment above, whether or not that row also moves TOOLS_LIST_BYTE_BUDGET (most of the
# history above is "still within budget, no further change" rows that grew the measured size
# without touching the budget; the target must track every one of those too, or it silently
# goes stale between the raises).
TOOLS_LIST_BYTE_TARGET = 94_130


def _budget_failure_message(measured: int) -> str:
    return (
        f"tools/list is {measured} bytes (target {TOOLS_LIST_BYTE_TARGET}), over the "
        f"{TOOLS_LIST_BYTE_BUDGET} budget. Compact a description or schema, or raise "
        "the budget deliberately."
    )


def test_budget_failure_message_reports_measured_budget_and_target():
    """The budget assertion's failure message must surface all three figures — measured,
    BUDGET, and TARGET — so the delta from the last deliberate measurement is visible on
    every run, not just on a failure. TARGET is informational only: the budget assertion
    below stays the ONLY hard gate."""
    msg = _budget_failure_message(99_999)
    assert "99999" in msg
    assert str(TOOLS_LIST_BYTE_BUDGET) in msg
    assert str(TOOLS_LIST_BYTE_TARGET) in msg


@pytest.mark.anyio
async def test_tools_list_wire_size_budget():
    async with Client(mcp) as c:
        tools = await c.list_tools()
    payload = [t.model_dump(mode="json", exclude_none=True) for t in tools]
    size = len(json.dumps(payload, separators=(",", ":")))
    assert size <= TOOLS_LIST_BYTE_BUDGET, _budget_failure_message(size)
