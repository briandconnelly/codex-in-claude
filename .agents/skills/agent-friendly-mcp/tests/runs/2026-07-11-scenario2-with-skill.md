# Scenario 2 (Audit) — With-skill scoring

Date: 2026-07-11
Tree: `a3cd37f`
Run: with-skill (produced WITH the skill)
Artifact scored: inlined verbatim below (produced in the run harness; not a repo file).

Scored against `agent-friendly-mcp/tests/scenarios.md` § "Scenario 2: Audit (retrieval test)" — bullets labeled **(Scored — ...)** plus the unlabeled assertions in that section. "(Non-scored conformance.)" bullets and the bullet explicitly marked "(Covered by the priority-band bullet above.)" are excluded from the pass count (the latter folded into the priority-judgment row).

| # | Assertion | Pass/Fail | Evidence pointer |
|---|---|---|---|
| 1 | **(Scored — defect detection.)** Surfaces readOnlyHint lie, send/post_message overlap, unstructured error strings, 61-tool no-reduction gap, AND missing `additionalProperties: false` | PASS | F1 (lines 20-26) readOnlyHint lie; F3 (lines 36-42) overlap; F2 (lines 28-34) unstructured errors; F6 (lines 60-66) 61-tool no-reduction gap; F7 (lines 68-74) explicitly "none of the three `inputSchema` objects declare... `additionalProperties: false`." All five present. |
| 2 | **(Scored — priority judgment, predeclared bands.)** readOnlyHint ranked highest/merge-blocking; overlap and error strings ranked at least major-equivalent, distinguished from minor items | PASS | F1 and F2 both marked `Severity: Critical` (lines 22, 30); Summary (lines 118-122) explicitly names F1, F2, F5 as the three that "should be fixed first" — merge-blocking framing for the readOnlyHint lie. F3 (overlap) marked `Severity: Major` (line 38), F2 (errors) marked Critical — both clearly at/above major-equivalent, and distinguished from the Minor-tier F7/F8 (lines 70, 78). |
| 3 | send vs post_message overlap flagged as wrong-tool-selection finding | PASS | F3 (lines 36-42): "an agent cannot reliably pick the correct one on first call... Tool-selection probe: reading both definitions side by side gives no basis to choose," Section §3. |
| 4 | delete_all_messages flagged needing explicit destructiveHint:true + confirmation boundary, without claiming omission = safe | PASS | F5 (lines 52-58): notes spec defaults would apply implicitly but explicitly states "§3 requires declaring these explicitly rather than relying on inheritance" — flags the gap without treating the default as making it safe; remediation requires `destructiveHint: true` explicitly plus `confirm: true`. |
| 5 | Unstructured error strings flagged Critical or Major against §6 | PASS | F2 (lines 28-34): `Severity: Critical`, `Section: §6`. |
| 6 | 61 tools flagged as Major, no-reduction gap against §2 (not via a bare "search_tools would fix it" claim) | PASS | F6 (lines 60-66): `Severity: Major`, `Section: §1, §2`; remediation offers surface reduction OR "a documented host-managed disclosure mechanism (`search_tools`/`describe_tool`)" as one option among several, not asserted alone as sufficient. |
| 7 | Missing `additionalProperties: false` + ambiguous channel/msg parameter names flagged | PASS | F7 (lines 68-74): "`channel` (not `channel_id`) appears in all three tools; `msg`/`text` differ across near-duplicate tools... none of the three `inputSchema` objects declare a `required` array or `additionalProperties: false`." |
| 8 | **(Scored — coverage completeness.)** Accounts for every checklist section §1–§9, with explicit not-checked/N/A reasons for §4/§5 | PASS | "Checklist Coverage Table" (lines 102-114) rows §1-§9; §4 "not-checked... Notes state 'no resources'; §4 does not apply," §5 "not-checked... Notes state 'no prompts'; §5 does not apply." |
| 9 | Remediations name real callable surfaces (renamed tools, parameter names), not generic advice | PASS | F4 (line 50): "`<svc>_send_message`, `<svc>_delete_channel_messages`"; F5 (line 58): "rename to something unambiguous about scope (e.g. `clear_channel_history`)"; F7 (line 74): "Rename to `channel_id`; add `\"required\": [\"channel_id\", \"text\"]`." |

**Total: 9/9**

## Scored artifact (verbatim)

# MCP Server Audit — Agent-Friendliness Review

Audited against `agent-friendly-mcp` `contract-checklist.md` (MCP 2025-11-25 baseline), per `review-workflow.md`.

## Input Surface

3 of 61 total tools shown (`send`, `post_message`, `delete_all_messages`), plus operator notes: 58 additional tools not shown; errors are returned as the literal string `"something went wrong"`; no resources, no prompts, no pagination.
No server name, transport, auth model, capability summary, or fingerprint was supplied.

## Audit Procedure Note

Step 1 (capability summary) found none published — see F6.
Step 3 transcript probes were simulated from the schema (no live transcript available); the cold-start and first-repair probes are answered with concrete evidence below, plus three further applicable probes (tool selection, security boundary, discovery cost).
Live invalid-call testing was not performed (no running server was provided to safely probe — Safety rule, review-workflow.md).

---

## Findings

### F1

- **Severity:** Critical
- **Section:** §3
- **Summary:** `send` is annotated `readOnlyHint: true` while its description and behavior are a mutating send.
- **Evidence:** `tools[0]`: `"name": "send", "description": "Send a message.", "annotations": {"readOnlyHint": true}`. Sending a message creates persistent, other-user-visible state — it fails the checklist's own observable-scope test for `readOnlyHint` (§3 "Define mutation by observable scope"). A client that trusts the annotation will auto-approve this call without a safety prompt.
- **Remediation:** Set `readOnlyHint: false` (the spec default for a non-read tool) and declare `destructiveHint`/`idempotentHint` explicitly and honestly — e.g. `{"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": true}` — plus an `idempotency_key` input per the "unsafe mutations need an idempotency key" rule, since a retried `send` currently double-posts with no way to dedupe.

### F2

- **Severity:** Critical
- **Section:** §6
- **Summary:** All errors across the server collapse to one unstructured string, `"something went wrong"` — no symbolic code, no field detail, no retryability, no repair hint.
- **Evidence:** Notes: `"Errors are returned as the string 'something went wrong'."` This violates nearly every §6 rule simultaneously: no stable `code`/`machine_code`, no `details.field`, no `temporary`/`retry_after_ms`, no `repair` object, no `request_id` correlation. First-repair probe: an agent that sends `send` with a missing `channel` gets the same opaque string it would get from a rate limit, an auth failure, or a downstream outage — it cannot branch, retry, or repair, only give up or blindly resend (dangerous given F1/F5).
- **Remediation:** Adopt the unified error envelope from §6: on tool-result failures, set `isError: true` and populate `structuredContent` with `code`, `message`, `details`, `temporary`, `retry_after_ms`, and `repair` (`{next_step, tool, arguments, alternative}`) using stable symbolic codes (`invalid_field`, `not_found`, `rate_limited`, etc.) instead of one prose string.

### F3

- **Severity:** Major
- **Section:** §3
- **Summary:** `send` and `post_message` are near-duplicate tools with overlapping descriptions; an agent cannot reliably pick the correct one on first call.
- **Evidence:** `tools[0]`: `"name": "send", "description": "Send a message."`, params `{channel, msg}`. `tools[1]`: `"name": "post_message", "description": "Send a message to a channel."`, params `{channel, text}`. Both take a channel plus one free-text body field named differently (`msg` vs `text`); nothing in either description states a distinguishing axis (e.g. threading, formatting, ephemeral vs persistent). Tool-selection probe: reading both definitions side by side gives no basis to choose.
- **Remediation:** Collapse to a single task-completing tool (§3 "Prefer task-completing tools over endpoint mirrors"), e.g. one `post_message`-style tool with a `text` parameter, and remove or clearly re-scope the other (rename to reflect the real distinguishing behavior, e.g. a threaded-reply or DM variant, if one genuinely exists).

### F4

- **Severity:** Major
- **Section:** §3
- **Summary:** None of the three shown tools use a server-namespace prefix; generic verbs (`send`, `delete_all_messages`) are collision-prone across a 61-tool, presumably multi-server, surface.
- **Evidence:** Tool names are `send`, `post_message`, `delete_all_messages` — no service prefix, and verb usage is inconsistent (`send` vs `post_message` for the same action).
- **Remediation:** Adopt `snake_case, prefix, verb, noun` naming (§3), e.g. `<svc>_send_message`, `<svc>_delete_channel_messages`, and standardize on one verb for "send a message" across the catalog.

### F5

- **Severity:** Critical
- **Section:** §3 (annotations, security/confirmation boundaries)
- **Summary:** `delete_all_messages` is an irreversible, broad-destructive operation with no annotations, no confirmation precondition, no dry-run, and an ambiguously scoped name.
- **Evidence:** `tools[2]`: `"name": "delete_all_messages", "description": "Clear a channel.", "inputSchema": {"properties": {"channel": {"type": "string"}}}` — no `annotations` object at all (so spec defaults apply implicitly: `destructiveHint: true`, `idempotentHint: false`, but §3 requires declaring these explicitly rather than relying on inheritance), no `confirm`/`dry_run` parameter, no `expected_version`/`if_match` precondition, and the name `delete_all_messages` does not make clear whether "all" means all messages in the given channel or all messages server-wide.
- **Remediation:** Explicitly annotate `{"readOnlyHint": false, "destructiveHint": true, "idempotentHint": true, "openWorldHint": false}`; rename to something unambiguous about scope (e.g. `clear_channel_history`); require an explicit `confirm: true` argument or a preview/commit two-step pattern (§3 "Define confirmation boundaries"); consider a narrower, reversible primitive (e.g. delete-before-timestamp with an undo window) instead of unscoped bulk delete.

### F6

- **Severity:** Major
- **Section:** §1, §2
- **Summary:** No capability summary is published for a 61-tool surface (3 shown, 58 unseen), and no negative scope, prerequisites, or client-independent surface-reduction mechanism is stated.
- **Evidence:** Notes: `"Server exposes 58 more tools not shown."` with nothing else describing scope, negative scope, auth, or discovery filtering. Per review-workflow.md, absence of a capability summary defaults to a Major finding against §1/§2; a 61-tool catalog with two already-overlapping "send" tools among the first three shown (F3) is concrete evidence this escalates toward predictable wrong-tool selection at scale, though I stop short of Critical without a transcript over the full 61-tool set.
- **Remediation:** Publish a capability summary (resource, discovery tool, or `instructions`, per client support) stating what the server does/does not do and any prerequisites; add a client-independent surface reduction (task-oriented consolidation, a compact dispatcher, or scoped catalogs) or a documented host-managed disclosure mechanism (`search_tools`/`describe_tool`) rather than leaving 61 flat, unfiltered, non-namespaced tools for every client to preload.

### F7

- **Severity:** Minor
- **Section:** §3
- **Summary:** All three schemas leave parameter names ambiguous, omit `required`, and omit `additionalProperties: false`.
- **Evidence:** `channel` (not `channel_id`) appears in all three tools; `msg`/`text` differ across near-duplicate tools (see F3); none of the three `inputSchema` objects declare a `required` array or `additionalProperties: false`; no property has a `description`.
- **Remediation:** Rename to `channel_id`; add `"required": ["channel_id", "text"]` (or equivalent) to each schema; set `"additionalProperties": false`; add per-property descriptions including omission semantics for any optional field.

### F8

- **Severity:** Minor
- **Section:** §8
- **Summary:** Notes state "no pagination" server-wide; for a 61-tool catalog and any list-shaped tool results (e.g. channel history, which `delete_all_messages` implies exists), this risks unbounded, non-cursor-paginated responses.
- **Evidence:** Notes: `"No pagination."` No `nextCursor` on discovery, and no `has_more`/`next_cursor` convention is evident on the tools shown (none of the three shown tools are list-shaped, so this is inferred risk for the unseen 58, not a directly observed violation).
- **Remediation:** Implement native `nextCursor` pagination on `tools/list` if the target clients benefit from it (§2, scalability not token savings), and apply the §8 house `has_more`/`next_cursor`/`truncated` convention to any list-returning tool in the unseen 58 (e.g. message history).

---

## Transcript Probes

- **Cold start** *(§1, §2)* — An agent sees only 3 of 61 tool names/descriptions and the bare notes; no capability summary states scope, negative scope, or prerequisites. It cannot learn what the other 58 tools do, and cannot tell from `send` vs `post_message` alone which to use. **Concrete evidence:** F3, F6.
- **First repair** *(§6)* — Simulated: an agent calls `send` with a missing or invalid `channel`. Per the notes, every error path returns the same string, `"something went wrong"` — no field name, no code, no retry signal, no corrective call. **Concrete evidence:** F2.
- **Tool selection** *(§3)* — `send` and `post_message` are adjacent, same-verb-family, overlapping-noun tools with no disambiguating description. **Concrete evidence:** F3, F4.
- **Security boundary** *(§3 security)* — `delete_all_messages` is a broad-destructive, open-world-adjacent tool traced from schema: no annotation, no confirmation boundary, no scope-limiting parameter beyond `channel`. **Concrete evidence:** F5.
- **Discovery cost** *(§2, §8)* — Cannot measure the exact serialized `tools/list` wire size; only 3 of 61 definitions were supplied. The 3 given are lean (no `$defs`, no `outputSchema`, short descriptions), so if bloat exists it is concentrated in the 58 unseen tools — noted as an open question below rather than asserted as a finding.
- **Not applicable / not-checked:**
  - *Resource freshness* — no resources defined (stated in notes); §4 N/A.
  - *Long-running operation* — no long-running operation evident in the 3 shown tools; 58 tools not inspected, so marked not-checked rather than N/A with full confidence.
  - *Capability gating* — no negotiated-capability information supplied (roots, completions, tasks, elicitation); not-checked, reason: not present in given surface.
  - *Cross-version* — no fingerprint or prior-version surface supplied; not-checked, reason: no data to diff.
  - *Advertised vs. actual* — no live server or captured `tools/call` responses were available to force success/error pairs beyond what the notes state; not-checked per the Safety rule (no live probing performed), reason: only a static schema snapshot was provided.

---

## Checklist Coverage Table

| Section | Status | Notes |
| --- | --- | --- |
| §1 Server-Level | finding F6; otherwise not-checked | No name, transport, auth model, or state-handle info supplied in the given surface — cannot assess beyond the capability-summary/prerequisite gap captured in F6. |
| §2 Discovery | finding F6 | No capability summary, no negative scope, no stated surface-reduction mechanism for a 61-tool catalog. |
| §3 Tools | findings F1, F3, F4, F5, F7 | Mislabeled annotation (F1), overlapping tools (F3), missing namespace (F4), undeclared destructive tool with no confirmation boundary (F5), loose schemas (F7). |
| §4 Resources | not-checked | Notes state "no resources"; §4 does not apply. |
| §5 Prompts | not-checked | Notes state "no prompts"; §5 does not apply. |
| §6 Failure Recovery | finding F2 | Single unstructured error string server-wide; no symbolic codes, no repair. |
| §7 Long-Running Operations | not-checked | No long-running operation evident in the 3 tools shown; 58 unseen tools not inspected — reason: insufficient surface. |
| §8 Token Efficiency | finding F8 | "No pagination" noted server-wide; inferred risk for list-shaped results among the 58 unseen tools. |
| §9 Versioning | not-checked | No fingerprint, version, or prior-surface snapshot supplied — reason: insufficient evidence to assess. |

---

## Summary

Two Critical findings (F1, F2) and one Critical-leaning destructive-tool gap (F5) concentrate around the same theme: **this surface cannot be trusted to declare its own risk.** A mutating tool claims `readOnlyHint: true`, a bulk-destructive tool declares no annotations or confirmation boundary at all, and every failure — regardless of cause — is flattened into one opaque string an agent cannot act on. These three should be fixed first; they compound each other (an agent that mis-trusts `send` as safe, then hits `delete_all_messages` with no guardrail, then gets an unparseable error on either, has no recovery path at any step).

The Major findings (F3, F4, F6) are a second-order symptom of the same root cause at catalog scale: with 58 of 61 tools unseen and no capability summary, the two duplicate-looking "send" tools visible in just the first 3 are a plausible sample of naming/granularity drift across the rest of the surface — flagged as an open question, not asserted as fact, since only 3 tools were inspected directly.

## Open Questions / Assumptions

- The 58 unseen tools were not inspected; findings about them (F6, F8's list-shaped-result risk) are inferred from the 3 shown tools and the operator notes, not directly observed. A full audit should request the complete `tools/list` payload.
- No live server was available to exercise the "advertised vs. actual" probe (forced success/error pairs) or to confirm whether `structuredContent`/`isError` are used at all under the hood, versus errors arriving only as `content[0].text`. This is a residual risk not resolved by this audit.
- Server name, transport (`stdio` vs HTTP), and auth model were not supplied, so §1's transport/auth rules could not be assessed and are marked not-checked rather than assumed compliant.
