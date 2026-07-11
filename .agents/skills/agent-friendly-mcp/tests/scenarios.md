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
- [ ] Input schemas use `additionalProperties: false`, disambiguated parameter names (`issue_number` not `issue`), and declared omission semantics for optional parameters — `default` only where the server applies it (§3).
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
- [ ] Flags `delete_all_messages` as needing an explicit `destructiveHint: true` and a confirmation boundary (§3 security) — without claiming omission declared it safe, since the spec default for an omitted `destructiveHint` is already `true`.
- [ ] Flags unstructured error strings as Critical or Major against §6 (Critical is defensible when credential failure modes are also collapsed).
- [ ] Flags 61 tools with no client-independent surface reduction (and no progressive-disclosure mechanism) as Major against §2 — not by asserting that `search_tools` alone would shrink what a standard preloading client loads.
- [ ] Flags missing `additionalProperties: false`, ambiguous `channel`/`msg` parameter names (§3).
- [ ] Produces the §1–§9 coverage table, with `not-checked`/`N/A` reasons for resources (§4) and prompts (§5).
- [ ] Remediations name real callable surfaces (renamed tools, parameter names), not generic advice.

**Expected baseline failures:** unstructured prose review, no severity scale, no coverage table, misses the `readOnlyHint` lie or rates it as minor, no checklist-section anchoring.

## Scenario 3: Long-running contract (application test)

**Prompt:**

> Design the agent-facing MCP contract for a `video_render` tool whose renders take 1–10 minutes and can pause waiting for the user to approve a watermark.
> Target MCP 2025-11-25.
> Produce: the capability declarations, the tool definition, the wire shapes an agent sees when creating, polling, and recovering the render, and whatever fallback you think clients without task support need.

**Assertions (with-skill run must satisfy):**

- [ ] Task support is declared at both levels: `server.capabilities.tasks.requests.tools.call` AND the tool's `execution.taskSupport` — not the per-tool flag alone (§7).
- [ ] Native task fields use spec casing (`taskId`, `status`, `statusMessage`, `createdAt`, `lastUpdatedAt`, `ttl`, `pollInterval`) and statuses are exactly `working`/`input_required`/`completed`/`failed`/`cancelled` (§7).
- [ ] The `CreateTaskResult` nests the `Task` under `result.task`, while `tasks/get` returns it directly — the shapes are not collapsed into one envelope (§7, native-wire-shapes).
- [ ] Any `progressToken` originates in the request's `_meta`, not minted by the server (§7).
- [ ] The watermark pause is handled via `input_required` with the native recovery path: the agent preemptively calls `tasks/result` and holds it open, the pending input request arrives as a separate server-to-client request while the call is pending, and the held call returns the terminal result once input is supplied (§7).
- [ ] The fallback status/cancel tools are labeled as convention, mirror the native signals (state, when to poll, result location, expiry), and do not replace `tasks/*` (§7).
- [ ] Native fields and house conventions are not mixed: convention metadata is namespaced or labeled, and native casing is never snake_cased (native-vs-convention rule).

**Expected baseline failures:** per-tool `taskSupport` without the server capability, invented snake_case task fields or statuses (`running`, `succeeded`), one collapsed envelope for all task methods, server-minted progress tokens, `input_required` handled by polling alone with no `tasks/result` channel, fallback tools presented as protocol.

## Scenario 4: Instructions and description prose (application test)

**Prompt:**

> You are writing the prose surfaces of an MCP server called `deployctl` that wraps an internal deployment service. The tool schemas already exist; your job is ONLY the prose:
>
> 1. The server-level `instructions` string (the capability summary agents see at initialization).
> 2. The `description` field text for two tools: `deployctl_deploy_service` and `deployctl_rollback_service`.
>
> Base them on the ops team's notes below (verbatim):
>
> "Deploys go through Spinnaker under the hood, which we adopted back in 2021 after the Jenkins fiasco. You generally want to run a dry-run first — it catches most config drift. Production deploys need a change ticket, that's an SRE policy thing, and honestly try to avoid deploying on Fridays. Rollbacks are pretty safe, they just repoint the release symlink, though if the previous release is more than 30 days old it's been garbage collected so rollback won't work, and you should probably check that first. The staging environment sometimes gets wedged; a redeploy usually fixes it. Also be careful with the `force` flag — it skips the pre-deploy health checks."
>
> Return exactly: the `instructions` string, then the two description strings, each clearly labeled.

**Assertions (with-skill run must satisfy):**

- [ ] **A1.** Binding rules (change-ticket requirement for production, dry-run expectation, 30-day rollback window, `force` constraint) are structurally distinguishable from background context — imperative sentences or list items, not clauses buried mid-narrative (§2/§3 prose rules).
- [ ] **A2.** Every rule's strength is explicit: mandatory ("requires `change_ticket` when `environment=production`") vs default with override ("prefer `dry_run: true` unless re-running a config already dry-run-validated") — field names in these examples are illustrative, since the prompt does not supply the schemas; score the phrasing pattern, not the identifiers; the source hedges ("generally", "try to", "probably", "honestly") are resolved to one or the other — a resolution the notes don't determine may be marked as an author decision to confirm with the ops team, but is never reproduced as a hedge.
- [ ] **A3.** Every directive is checkable against observable behavior: "be careful with `force`" is replaced by the sourced fact (it skips the pre-deploy health checks) plus either a permitted-use condition or an explicit note that the permitted-use policy is an open author decision — not a fabricated policy presented as sourced; "rollbacks are pretty safe" is replaced by the observable condition (release older than 30 days is garbage-collected and cannot be rolled back).
- [ ] **A4.** Compound notes are split into atomic obligations — the rollback sentence yields separate statements for the symlink mechanics (context) and the 30-day limit (rule).
- [ ] **A5.** Discretionary background (Spinnaker/Jenkins history, "adopted in 2021") is dropped or kept clearly apart from the rules, never interleaved with them; soft norms (Friday deploys) are either stated as an explicit default with override or dropped, not echoed as vibes.

**Expected baseline failures:** narrative paragraphs that reproduce the ops notes' structure, hedges carried through verbatim ("generally", "be careful"), rules embedded mid-sentence next to Spinnaker trivia, compound sentences bundling mechanics with constraints.

## Results

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-06-09 | 2 (audit) | baseline | 5/9 | Caught readOnlyHint lie, duplication, error strings, tool count, naming — but no severity scale (used Critical/High/Medium), no five-line format, no §-anchoring, no coverage table, no N/A entries for resources/prompts. |
| 2026-06-09 | 2 (audit) | with-skill | 9/9 | Five-line findings F1–F7 anchored to §N; coverage table with not-checked reasons; six probes run, three skipped with reasons; remediations name `chat_send_message`, `channel_id`, `search_tools`. Errors rated Critical (within loosened assertion). |
| 2026-06-09 | 1 (design) | _not yet run_ | | |
| 2026-07-01 | 3 (long-running) | _not yet run_ | | |
| 2026-07-02 | 4 (prose) | baseline (skill pre-§2/§3/§4 prose rules) | 1/5 | `force` fact and 30-day condition checkable (A3 pass), but description rules buried mid-narrative and the dry-run rule left in a workflow paragraph (A1 fail), "Recommended workflow" dry-run carries no override condition (A2 fail), one bullet bundles previous-release-only + 30-day GC + recovery action (A4 fail), rollback mechanics interleaved with eligibility rules (A5 fail). |
| 2026-07-02 | 4 (prose) | with-skill | 5/5 | Labeled `Rules:`/`Background:` sections; every rule mandatory or default-with-override; `force` carries the sourced fact plus an explicit permitted-use condition. Two earlier iterations were discarded: the first with-skill run echoed the guidance's then-deploy-domain examples (checklist examples moved to a messaging domain in response), and Codex review flagged that the prompt omitted `force` semantics so a pass could reward invented policy (sourced fact added to the notes). The scores above are the final runs against the corrected prompt. |
