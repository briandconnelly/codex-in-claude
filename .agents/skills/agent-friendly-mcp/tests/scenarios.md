# Test Scenarios for agent-friendly-mcp

Behavioral test scenarios for this skill, following the baseline/with-skill methodology: run each scenario with a fresh subagent that does NOT have the skill loaded (baseline), then with the skill loaded (treatment), and compare against the assertions.
A baseline run that already satisfies every assertion means the scenario is too easy; tighten it.
An assertion the with-skill run misses is a finding against the skill, not against the agent.

## How to run

1. **Baseline:** dispatch a subagent with only the scenario prompt below.
   Record which assertions its output satisfies.
2. **Treatment:** dispatch a fresh subagent with the skill content available (or triggered via its description) and the same prompt.
3. **Score:** every assertion is pass/fail with a one-line evidence pointer into the transcript.
   Record results in the table at the bottom.

## Scenario 1: Design (application test)

**Prompt:**

> Design the agent-facing MCP server contract for a service wrapping the GitHub Issues REST API.
> The underlying API has these endpoints: list issues, get issue, create issue, update issue, add comment, list comments, lock issue, unlock issue, add labels, remove labels, search issues.
> Produce: the tool list with input schemas, the error shape for at least two failure modes, and whatever discovery surface you think agents need.

**Assertions (with-skill run must satisfy):**

- [ ] Tools are task-completing, not one-per-endpoint: the 11 endpoints collapse to roughly 4–7 tools (§3 granularity rule; e.g., label add/remove folded into update, lock/unlock folded or justified as a named split exception).
- [ ] Tool names are `snake_case`, service-prefixed, verb+noun (§3).
- [ ] Input schemas use `additionalProperties: false`, disambiguated parameter names (`issue_number` not `issue`), and declared defaults for optional parameters (§3).
- [ ] At least two error payloads use stable symbolic codes with field-level detail and a repair hint naming a real callable surface (§6).
- [ ] Errors return as tool result errors (`isError: true`), not JSON-RPC errors (§6).
- [ ] A capability summary exists stating what the server does NOT do (§1/§2 negative scope).
- [ ] Pagination is cursor-based and provenance-correct: a tool's own list-shaped result payload may use the `has_more` house convention, while native list methods (`tools/list`, etc.) use `nextCursor` (omission = done) — not a house convention; responses also have a concise default with a `detail` toggle (§8).
- [ ] Annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) are present and honest — e.g., create-issue is not marked read-only or idempotent (§3).

**Expected baseline failures:** endpoint-mirroring (one tool per endpoint), prose-only error descriptions, no negative scope, no pagination contract, missing or dishonest annotations.

## Scenario 2: Audit (retrieval test)

**Prompt:**

> Audit this MCP server surface for agent-friendliness and report your findings:
>
> ```json
> {
>   "tools": [
>     {"name": "send", "description": "Send a message.", "inputSchema": {"type": "object", "properties": {"channel": {"type": "string"}, "msg": {"type": "string"}}},
>      "annotations": {"readOnlyHint": true}},
>     {"name": "post_message", "description": "Send a message to a channel.", "inputSchema": {"type": "object", "properties": {"channel": {"type": "string"}, "text": {"type": "string"}}}},
>     {"name": "delete_all_messages", "description": "Clear a channel.", "inputSchema": {"type": "object", "properties": {"channel": {"type": "string"}}}}
>   ],
>   "notes": "Server exposes 58 more tools not shown. Errors are returned as the string 'something went wrong'. No resources, no prompts, no pagination."
> }
> ```

**Assertions (with-skill run must satisfy):**

- [ ] Uses the severity scale (Critical/Major/Minor/Nit) and the five-line finding format from `review-workflow.md`.
- [ ] Flags `readOnlyHint: true` on `send` (a mutating tool) as **Critical** (§3 annotation honesty).
- [ ] Flags `send` vs `post_message` overlap as a wrong-tool-selection finding (§3).
- [ ] Flags `delete_all_messages` missing `destructiveHint` and confirmation boundary (§3 security).
- [ ] Flags unstructured error strings as Critical or Major against §6 (Critical is defensible when credential failure modes are also collapsed).
- [ ] Flags 61 tools with no client-independent surface reduction (and no progressive-disclosure mechanism) as Major against §2 — not by asserting that `search_tools` alone would shrink what a standard preloading client loads.
- [ ] Flags missing `additionalProperties: false`, ambiguous `channel`/`msg` parameter names (§3).
- [ ] Produces the §1–§9 coverage table, with `not-checked`/`N/A` reasons for resources (§4) and prompts (§5).
- [ ] Remediations name real callable surfaces (renamed tools, parameter names), not generic advice.

**Expected baseline failures:** unstructured prose review, no severity scale, no coverage table, misses the `readOnlyHint` lie or rates it as minor, no checklist-section anchoring.

## Results

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-06-09 | 2 (audit) | baseline | 5/9 | Caught readOnlyHint lie, duplication, error strings, tool count, naming — but no severity scale (used Critical/High/Medium), no five-line format, no §-anchoring, no coverage table, no N/A entries for resources/prompts. |
| 2026-06-09 | 2 (audit) | with-skill | 9/9 | Five-line findings F1–F7 anchored to §N; coverage table with not-checked reasons; six probes run, three skipped with reasons; remediations name `chat_send_message`, `channel_id`, `search_tools`. Errors rated Critical (within loosened assertion). |
| 2026-06-09 | 1 (design) | _not yet run_ | | |
