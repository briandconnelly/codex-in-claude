# Scenario 2 (Audit) — Baseline scoring

Date: 2026-07-11
Tree: `a3cd37f`
Run: baseline (produced WITHOUT the skill)
Artifact scored: inlined verbatim below (produced in the run harness; not a repo file).

Scored against `agent-friendly-mcp/tests/scenarios.md` § "Scenario 2: Audit (retrieval test)" — bullets labeled **(Scored — ...)** plus the unlabeled assertions in that section. "(Non-scored conformance.)" bullets and the bullet explicitly marked "(Covered by the priority-band bullet above.)" are excluded from the pass count (the latter folded into the priority-judgment row).

| # | Assertion | Pass/Fail | Evidence pointer |
|---|---|---|---|
| 1 | **(Scored — defect detection.)** Surfaces readOnlyHint lie, send/post_message overlap, unstructured error strings, 61-tool no-reduction gap, AND missing `additionalProperties: false` | FAIL | Covers readOnlyHint (line 7-10), overlap (line 18-22), error string (line 45-49), 61-tool/pagination gap (line 53-55) — but never mentions `additionalProperties` anywhere in the document (grep confirms zero hits). Conjunctive assertion requires all five; missing one fails it. |
| 2 | **(Scored — priority judgment, predeclared bands.)** readOnlyHint ranked highest/merge-blocking; overlap and error strings ranked at least major-equivalent, distinguished from minor items | FAIL | Doc uses only one ad hoc tier, "## Critical Issues" (lines 5-22), which does correctly include readOnlyHint (line 7, called "the most dangerous finding") and the overlap (line 18) — good. But the error-string finding sits in its own untiered "## Error Handling" section (line 43-49), given no Major/Minor label and organizationally indistinguishable in weight from the minor schema nits in "## Schema / Description Quality Issues" (lines 24-41, e.g. "No enums or format constraints", line 40). No predeclared Critical/Major/Minor/Nit bands are used anywhere, so error strings are not demonstrably ranked above minor nits. |
| 3 | send vs post_message overlap flagged as wrong-tool-selection finding | PASS | Lines 18-22: "Duplicate/overlapping tools... An agent has no principled way to choose between them," listed under "## Critical Issues." |
| 4 | delete_all_messages flagged needing explicit destructiveHint:true + confirmation boundary, without claiming omission = safe | PASS | Lines 12-16: "No `destructiveHint` / `idempotentHint` annotations at all... No confirmation parameter... should almost certainly require explicit confirmation semantics and a strong `destructiveHint: true`." Never claims the omission implies safety. |
| 5 | Unstructured error strings flagged Critical or Major against §6 | FAIL | Lines 45-49 call it a "severe... problem" in prose but the finding lives outside the "## Critical Issues" section and receives no Major/Minor tier; no §6 (or any checklist-section) citation anywhere in the document. |
| 6 | 61 tools flagged as Major, no-reduction gap against §2 (not via a bare "search_tools would fix it" claim) | FAIL | Lines 53-55 raise pagination and namespacing concerns ("no visible namespacing... makes tool selection harder") but never as an explicitly Major-ranked finding, and no §2/checklist anchoring; framed mainly as a pagination gap rather than a surface-reduction/progressive-disclosure gap. |
| 7 | Missing `additionalProperties: false` + ambiguous channel/msg parameter names flagged | FAIL | `additionalProperties` never appears (grep confirms). Parameter-name inconsistency is noted only as `msg` vs `text` mismatch across tools (line 21), not as a general ambiguity/`additionalProperties` gap. |
| 8 | **(Scored — coverage completeness.)** Accounts for every checklist section §1–§9, with explicit not-checked/N/A reasons for §4/§5 | FAIL | Document never cites checklist section numbers (§1–§9) anywhere; no coverage table. Resources/prompts are covered as a substantive finding (lines 57-59, "No resources or prompts exposed") rather than an explicit not-checked/N/A entry against §4/§5. |
| 9 | Remediations name real callable surfaces (renamed tools, parameter names), not generic advice | FAIL | "Summary of Recommendations" (lines 63-69) references some real annotation fields (`destructiveHint`, `tools/list`) but proposes no concrete renamed tool or parameter identifiers (no e.g. `channel_id`, no new tool name) — stays at generic "add X" / "consolidate" level. |

**Total: 2/9**

## Scored artifact (verbatim)

# MCP Server Surface Audit (Baseline)

Scope: 3 tools shown (`send`, `post_message`, `delete_all_messages`) plus notes indicating 58 additional hidden tools, generic string errors, and no resources/prompts/pagination.

## Critical Issues

1. **`send` has a false `readOnlyHint: true` annotation.**
   Sending a message is a mutating, side-effecting action (it writes a message to a channel), not a read-only operation.
   A misleading `readOnlyHint` can cause an agent or client to treat this tool as safe to call speculatively, in parallel, or without confirmation — when in fact it has real-world side effects (notifying users, posting content).
   This is the most dangerous finding: incorrect annotations are worse than no annotations, because they actively mislead trust/safety logic in clients.

2. **`delete_all_messages` is a highly destructive, irreversible tool with no safety signaling.**
   - No `destructiveHint` / `idempotentHint` annotations at all (in fact none of the three tools show annotations except the incorrect one on `send`).
   - No confirmation parameter (e.g. a required `confirm: true` field, a dry-run mode, or a count/preview return).
   - The name "Clear a channel" undersells the blast radius — "delete_all_messages" is bulk, irreversible data loss. This should almost certainly require explicit confirmation semantics and a strong `destructiveHint: true`.
   - There's no way for an agent to know how many messages will be deleted before calling it, and no undo path implied.

3. **Duplicate/overlapping tools with inconsistent schemas: `send` vs `post_message`.**
   Both appear to do the same thing ("send a message" / "send a message to a channel") but use different parameter names (`msg` vs `text`) and different descriptions. This is a classic agent-friendliness failure:
   - An agent has no principled way to choose between them.
   - Inconsistent field naming across semantically identical tools increases the chance of the wrong schema being used (e.g., calling `send` with `text` instead of `msg`).
   - If they are *not* actually identical (e.g., one is broadcast, one is DM, or one triggers notifications and one doesn't), the descriptions fail to convey that distinction — one-line descriptions like "Send a message." are too thin to disambiguate.

## Schema / Description Quality Issues

4. **No `required` fields declared in any `inputSchema`.**
   All three tools show `properties` but no `required` array. If `channel` and `msg`/`text` are actually mandatory (which they almost certainly are), omitting `required` means:
   - Agents may omit them and get a late runtime failure instead of an upfront schema validation error.
   - Clients that do schema-based prompting/UI generation won't mark the fields as mandatory.

5. **No parameter-level descriptions.**
   `channel`, `msg`, `text` all have only a bare `"type": "string"` with no `description`. An agent has no way to know:
   - What format `channel` expects (ID? name? `#`-prefixed?).
   - Whether `msg`/`text` supports markdown, length limits, mentions, etc.
   This is a major usability gap — schema types alone are not enough documentation for correct tool use.

6. **Tool descriptions are too short to support disambiguation or correct usage.**
   "Send a message.", "Send a message to a channel.", "Clear a channel." are all single, generic sentences with no mention of side effects, scope, reversibility, or constraints. Good MCP tool descriptions should tell the model *when* to use the tool, what happens as a result, and any caveats (e.g., "irreversible", "visible to all channel members", "rate-limited").

7. **No enums or format constraints on `channel`.**
   If channels are a bounded/discoverable set, `channel` should either use an enum, a reference to a resource listing valid channels, or at least a description of the expected format. As-is, an agent must guess or hallucinate valid channel identifiers.

## Error Handling

8. **Errors collapse to a single opaque string: "something went wrong."**
   This is a severe agent-friendliness problem:
   - No error codes, no structured error object, no distinction between validation errors, permission errors, rate limits, not-found channels, etc.
   - An agent cannot self-correct (e.g., retry vs. fix parameters vs. give up) because it has no signal about *why* the call failed.
   - This violates MCP/JSON-RPC error-object best practice (structured `code`/`message`/`data`), and generally violates the principle that tool errors should be actionable by the caller.

## Discoverability / Scale Issues

9. **61 total tools (3 shown + 58 hidden) with no pagination mentioned.**
   - MCP's `tools/list` supports cursor-based pagination; the notes explicitly say "no pagination," which for a 61-tool surface risks a single oversized listing response, higher token cost on every session, and harder context management for the agent.
   - A flat namespace of 61 tools with no apparent grouping/prefixing convention (based on the visible names) makes tool selection harder — no visible namespacing (e.g., `channel.send`, `channel.delete_all`) to help an agent narrow down relevant tools contextually.

10. **No resources or prompts exposed.**
    - No resources means the agent has no lightweight, read-side way to enumerate valid channels, browse message history, etc., without invoking a "tool" (which implies action/cost). Everything must go through the tools interface, even pure reads.
    - No prompts means no server-provided guided workflows/templates for common multi-step tasks (e.g., "archive and notify"), leaving all orchestration logic to the calling agent.

## Summary of Recommendations

- Fix or remove the incorrect `readOnlyHint` on `send`; add correct `destructiveHint`/`idempotentHint`/`openWorldHint` annotations across all tools, especially `delete_all_messages`.
- Consolidate `send`/`post_message` into a single tool with one consistent parameter naming scheme, or clearly differentiate them in name and description if they are genuinely different operations.
- Add `required` arrays and per-parameter `description` fields to every schema.
- Add confirmation/dry-run semantics (or at minimum a strong destructive annotation and richer description) to `delete_all_messages`.
- Replace the single generic error string with structured errors (code + message + relevant context) so agents can react appropriately.
- Implement `tools/list` pagination and consider namespacing/grouping the 61 tools to aid discovery.
- Consider exposing read-only data (e.g., channel lists, message history) as MCP resources rather than tools.
