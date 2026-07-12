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

## Scoring dimensions

Each assertion is one of two kinds; scenarios label which.

- **Scored (protocol / outcome).**
  Defect detection, severity / priority judgment, schema validity, and — for scenarios that supply a task and a tool catalog — first-call correctness, first-repair correctness, and the token / tool-call budgets.
  These are what a run passes or fails on.
- **Non-scored conformance.**
  Report layout (the five-line finding format), the exact `Critical / Major / Minor / Nit` words, and coverage-table cosmetics.
  Recorded for consistency, never the reason a run fails.

Outcome-metric mapping is deliberately partial.
First-call success, first-repair success, schema validity, tool-selection accuracy, and token usage are measured only where a scenario runs an executable or replayed task with a supplied catalog (see `tests/fixtures/` and `references/design-workflow.md` Step 8).
A static design/audit assertion — "the contract describes a symbolic error code", "the schema sets `additionalProperties: false`" — is a **leading indicator** of those outcomes, not a measurement of them, and is labeled as such rather than force-mapped onto a metric it does not observe.

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
- [ ] Tool definitions publish an `outputSchema`, and success results are described as `structuredContent` conforming to it, with `content` kept as a textual fallback (§3 output contract).
- [ ] **(Scored.)** The error path is contract-correct and distinct from the success shape: `isError: true` results carry the §6 error envelope in `structuredContent` (with a `content` textual fallback), and `outputSchema` is scoped to **success** results — stated as this skill's reading of an unsettled spec point, not as settled MCP law (`contract-checklist.md:190`). The design does one of: documents a success-only `outputSchema` with the error envelope validated separately, or unions success and error branches into `outputSchema`. A text-only error carrier counts only when disclosed as a degraded mode (`contract-checklist.md:471`).

**Expected baseline failures:** endpoint-mirroring (one tool per endpoint), prose-only error descriptions, no negative scope, no pagination contract, missing or dishonest annotations, no output schema / free-text results, and error results that reuse the success `structuredContent` shape or omit the structured error envelope entirely.

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

- [ ] **(Scored — defect detection.)** Surfaces the load-bearing defects: the `readOnlyHint` lie on `send`, the `send`/`post_message` overlap, the unstructured error strings, the 61-tool no-reduction gap, and the missing `additionalProperties: false` — independent of how they are worded or labeled.
- [ ] **(Scored — priority judgment, predeclared bands.)** Ranks by impact against `review-workflow.md:9–19`: the false `readOnlyHint` is highest-priority and merge-blocking; the `send`/`post_message` overlap and the unstructured error strings are at least must-fix / major-equivalent. Score the impact judgment, not the label word.
- [ ] **(Non-scored conformance.)** May use the five-line finding layout and the exact `Critical / Major / Minor / Nit` vocabulary from `review-workflow.md:79–86`; recorded, never the reason a run fails.
- [ ] **(Covered by the priority-band bullet above.)** The `readOnlyHint: true` claim on the mutating `send` tool is ranked highest-priority / merge-blocking (§3 annotation honesty); the exact word "Critical" is non-scored.
- [ ] Flags `send` vs `post_message` overlap as a wrong-tool-selection finding (§3).
- [ ] Flags `delete_all_messages` as needing an explicit `destructiveHint: true` and a confirmation boundary (§3 security) — without claiming omission declared it safe, since the spec default for an omitted `destructiveHint` is already `true`.
- [ ] Flags unstructured error strings as Critical or Major against §6 (Critical is defensible when credential failure modes are also collapsed).
- [ ] Flags 61 tools with no client-independent surface reduction (and no progressive-disclosure mechanism) as Major against §2 — not by asserting that `search_tools` alone would shrink what a standard preloading client loads.
- [ ] Flags missing `additionalProperties: false`, ambiguous `channel`/`msg` parameter names (§3).
- [ ] **(Scored — coverage completeness.)** Accounts for every checklist section §1–§9, giving resources (§4) and prompts (§5) an explicit `not-checked` / `N/A` reason rather than silently skipping them.
- [ ] **(Non-scored conformance.)** The specific coverage-table layout is recorded for consistency, not scored.
- [ ] Remediations name real callable surfaces (renamed tools, parameter names), not generic advice.

**Expected baseline failures:** unstructured prose review, misranks the readOnlyHint lie below merge-blocking, no coverage table, misses the `readOnlyHint` lie or rates it as minor, no checklist-section anchoring.

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

> You are writing the prose surfaces of an MCP server called `deployctl` that wraps an internal deployment service.
> The tool schemas already exist; your job is ONLY the prose:
>
> 1. The server-level `instructions` string (the capability summary agents see at initialization).
> 2. The `description` field text for two tools: `deployctl_deploy_service` and `deployctl_rollback_service`.
>
> Base them on the ops team's notes below (verbatim):
>
> "Deploys go through Spinnaker under the hood, which we adopted back in 2021 after the Jenkins fiasco.
> You generally want to run a dry-run first — it catches most config drift.
> Production deploys need a change ticket, that's an SRE policy thing, and honestly try to avoid deploying on Fridays.
> Rollbacks are pretty safe, they just repoint the release symlink, though if the previous release is more than 30 days old it's been garbage collected so rollback won't work, and you should probably check that first.
> The staging environment sometimes gets wedged; a redeploy usually fixes it.
> Also be careful with the `force` flag — it skips the pre-deploy health checks."
>
> Return exactly: the `instructions` string, then the two description strings, each clearly labeled.

**Assertions (with-skill run must satisfy):**

- [ ] **A1.** Binding rules (change-ticket requirement for production, dry-run expectation, 30-day rollback window, `force` constraint) are structurally distinguishable from background context — imperative sentences or list items, not clauses buried mid-narrative (§2/§3 prose rules).
- [ ] **A2.** Every rule's strength is explicit: mandatory ("requires `change_ticket` when `environment=production`") vs default with override ("prefer `dry_run: true` unless re-running a config already dry-run-validated") — field names in these examples are illustrative, since the prompt does not supply the schemas; score the phrasing pattern, not the identifiers; the source hedges ("generally", "try to", "probably", "honestly") are resolved to one or the other — a resolution the notes don't determine may be marked as an author decision to confirm with the ops team, but is never reproduced as a hedge.
- [ ] **A3.** Every directive is checkable against observable behavior: "be careful with `force`" is replaced by the sourced fact (it skips the pre-deploy health checks) plus either a permitted-use condition or an explicit note that the permitted-use policy is an open author decision — not a fabricated policy presented as sourced; "rollbacks are pretty safe" is replaced by the observable condition (release older than 30 days is garbage-collected and cannot be rolled back).
- [ ] **A4.** Compound notes are split into atomic obligations — the rollback sentence yields separate statements for the symlink mechanics (context) and the 30-day limit (rule).
- [ ] **A5.** Discretionary background (Spinnaker/Jenkins history, "adopted in 2021") is dropped or kept clearly apart from the rules, never interleaved with them; soft norms (Friday deploys) are either stated as an explicit default with override or dropped, not echoed as vibes.

**Expected baseline failures:** narrative paragraphs that reproduce the ops notes' structure, hedges carried through verbatim ("generally", "be careful"), rules embedded mid-sentence next to Spinnaker trivia, compound sentences bundling mechanics with constraints.

## Scenario 5: Resources (application test)

**Prompt:**

> Design the agent-facing MCP resource surface for a server that exposes a large internal documentation wiki to an agent.
> The wiki has thousands of pages organized in spaces (e.g. `eng`, `hr`); pages can be large (some over 100 KB); page content changes during the day; and agents need to look up a specific page by space + slug as well as browse.
> Produce: the resource URI scheme, what a resource index/list entry looks like versus a full body, how large pages are delivered, how an agent discovers parameterized lookups, how an agent is notified when a page it cares about changes, and any fallback for clients that do not handle resources well.

**Assertions (with-skill run must satisfy):**

- [ ] Resource URIs are stable, hierarchical, and use domain nouns (e.g. `wiki://spaces/{space}/pages/{slug}`), not internal numeric ids that change between deployments (§4).
- [ ] Index/list entries carry native triage metadata — `title`, `description`, `mimeType`, `size`, `annotations.lastModified` — and do NOT inline the page body; the summary is short (at most three sentences) (§4).
- [ ] Large page bodies are chunkable, and every chunk has its own callable URI published as a resource template (`resources/templates/list` with `uriTemplate`) — because native `resources/read` takes only a URI — or a labeled tool fallback that accepts a chunk id (§4).
- [ ] Chunk identifiers are stable across reads of the same page version, and a version change is observable via the resource's modification metadata (§4).
- [ ] Custom/index metadata with no native field rides under a namespaced `_meta` key, never as a new top-level field on the `Resource` record (§4, native-vs-convention).
- [ ] Mutable pages support subscriptions: the server advertises `resources.subscribe`, accepts `resources/subscribe`, and emits `notifications/resources/updated` for subscribed URIs — distinguished from `notifications/resources/list_changed`, which signals catalog membership changes, not body edits (§4).
- [ ] Parameterized lookups are exposed via resource templates, with completion for `{space}`/`{slug}` where clients negotiate `completions`, so an agent can construct a page URI without enumerating every page (§4).
- [ ] A tool fallback reaches the same indexed content and is self-sufficient from `tools/list` alone, for clients that do not expose resources well (§4).

**Expected baseline failures:** unstable or numeric-id URIs, index entries that inline full bodies or omit `size`/`lastModified`, whole-document delivery with no chunking or chunk URIs, custom fields added at the top level of the resource record, `list_changed` conflated with per-resource update notifications (or no subscription mechanism at all), no resource-template or completion discovery, no tool fallback.

## Scenario 6: Prompts (application test)

**Prompt:**

> Design a reusable MCP prompt named `open_incident` for a server that wraps an incident-management service.
> Its tools already exist: `incident_create`, `incident_add_responder`, `incident_post_update`, plus a resource `incidents://active`.
> The prompt should help an agent run the "declare a new incident" workflow.
> Produce: the prompt definition (name, arguments, and any orchestration text), and a short note on what belongs in the prompt versus what belongs in the tool and resource schemas.

**Assertions (with-skill run must satisfy):**

- [ ] The prompt references its follow-on tools and resource by canonical name (`incident_create`, `incident_add_responder`, `incident_post_update`, `incidents://active`) and does NOT redefine their argument shapes or contracts (§5).
- [ ] Prerequisites are declared — which tools, which resources, and which permission or context assumptions the prompt relies on (§5).
- [ ] No behavior is load-bearing in the prompt text: apply the "remove every prompt — is any tool now unsafe or uncallable?" audit; argument shapes, side effects, and error handling live in the tool schemas, not the prompt prose (§5).
- [ ] Only native `PromptArgument` fields appear on each argument (`name`, `title`, `description`, `required`); value-shape guidance (types, enums, defaults, arrays) is carried in each argument's `description`, not as non-native keys such as `type`/`items`/`default` (§5, native-vs-convention).
- [ ] Any convention metadata (when-to-use, prerequisites, expected-followups) rides under a namespaced `_meta` key, not as top-level `Prompt` fields (§5, native-vs-convention).
- [ ] Completion is offered for arguments with dynamic value sets (e.g. a service or team the agent should not guess) where `completions` is negotiated (§5).
- [ ] The design states explicitly that the prompt is optional orchestration scaffolding — a client that never invokes it (most code-execution clients) can still call every tool correctly from the schemas alone (§5).

**Expected baseline failures:** prompt redefines tool argument shapes inline, encodes required behavior (side effects or error handling) in prose so a schema-only client would call wrong, uses non-native argument keys (`type`/`items`/`default`) or top-level convention fields, no declared prerequisites, no completion, treats the prompt as required rather than optional.

## Scenario 7: Triggering (activation test)

Unlike Scenarios 1–6, this scenario tests whether the skill's `description` fires at all, so baseline/treatment does not apply — loading the skill is the dependent variable.

**Method.**

1. Present the skill alongside a realistic catalog of sibling skills, in randomized order, with the prompt blind to the expected answer.
2. Prefer the host's real selector and verify an observable skill-load/read event before task execution.
   Where the harness exposes no such signal, fall back to a labeled description-classifier proxy: ask a dispatcher "which of these skills, if any, applies?" — recorded as a **proxy**, not proof the host loads the skill.
3. Run each case several times with a pinned model/runtime and report a **trigger rate**, not a single pass/fail.

**Cases — close-boundary minimal pairs (grounded in `SKILL.md:56–69`):**

| # | Prompt (abbreviated) | Expected |
| --- | --- | --- |
| T1 | "Design the MCP server contract for our billing API so agents can call it." | fire |
| T2 | "Review the internal Python of our MCP server for dead code and style." | quiet (non-agent-facing internals, `SKILL.md:64`) |
| T3 | "Harden the input schemas on our `deploy` and `rollback` MCP tools — agents keep passing ambiguous args." | fire |
| T4 | "Add one optional `note` field to a tool on our already agent-friendly MCP server." | quiet (trivial addition, `SKILL.md:66`) |
| T5 | "Design the recovery + progress contract for a 5-minute MCP render task." | fire |
| T6 | "Design the operator dashboard that shows our MCP server's request volume." | quiet (operator dashboard, `SKILL.md:67`) |
| T7 | "Write the client code that calls the `incidents://active` resource on someone else's MCP server." | quiet (consuming, not designing) |
| T8 | "Our agents keep picking the wrong tool out of the 60 we expose — help." | fire (symptom, no literal "MCP server") |

**Assertions:**

- [ ] **(Scored — trigger recall.)** T1, T3, T5, T8 fire at a high rate; report the rate per case.
- [ ] **(Scored — trigger precision.)** T2, T4, T6, T7 stay quiet at a high rate; a fire on any of these is a precision finding against the `description`, not against the agent.
- [ ] **(Recorded.)** Whether the run used the host's real selector or the labeled classifier proxy, and the model/runtime and trial count.

**Expected description weaknesses (findings against the skill if seen):** T8 (symptom without "MCP server") fails to fire — the description leans on the literal term; T2 or T7 fire — the description over-matches on the keyword "MCP" regardless of design-vs-consume or agent-facing-vs-internal.

## Results

| Date | Scenario | Run | Assertions passed | Notes |
| --- | --- | --- | --- | --- |
| 2026-06-09 | 2 (audit) | baseline | 5/9 | Caught readOnlyHint lie, duplication, error strings, tool count, naming — but no severity scale (used Critical/High/Medium), no five-line format, no §-anchoring, no coverage table, no N/A entries for resources/prompts. |
| 2026-06-09 | 2 (audit) | with-skill | 9/9 | Five-line findings F1–F7 anchored to §N; coverage table with not-checked reasons; six probes run, three skipped with reasons; remediations name `chat_send_message`, `channel_id`, `search_tools`. Errors rated Critical (within loosened assertion). |
| 2026-07-11 | 1 (design) | baseline | 4/9 | Tree `d586ce3`. Passed A3/A4/A5/A8; failed granularity (11 endpoint-mirroring tools), naming (no service prefix, noun_verb), negative scope, pagination provenance/detail-toggle, and shown `outputSchema` (claimed in prose only). [evidence](runs/2026-07-11-scenario1-baseline.md) |
| 2026-07-11 | 1 (design) | with-skill | 9/9 | Tree `d586ce3`. 11 endpoints → 7 task-completing `github_*` tools with justification table; explicit `does_not`; house pagination labeled vs native `nextCursor`; `outputSchema` on the flagship read tool; one-envelope-two-carriers errors. [evidence](runs/2026-07-11-scenario1-with-skill.md) |
| 2026-07-11 | 3 (long-running) | baseline | 4/7 | Tree `d586ce3`. Passed A1/A2/A3/A7; failed A4 (invented `notifications/tasks/status` instead of request-`progressToken`), A5 (polled instead of preemptive-hold `tasks/result`), A6 (fallback not labeled convention, no expiry). Caveat: A1 used `requests: ["tools/call"]` array vs spec nested shape. [evidence](runs/2026-07-11-scenario3-baseline.md) |
| 2026-07-11 | 3 (long-running) | with-skill | 7/7 | Tree `d586ce3`. Nested `tasks.requests.tools.call`; request-originated `progressToken`; preemptive-hold `input_required` recovery via `elicitation/create`; labeled-convention fallback mirroring expiry/result-location; `tasks/list` withheld for isolation. [evidence](runs/2026-07-11-scenario3-with-skill.md) |
| 2026-07-11 | 5 (resources) | baseline | 5/8 | Tree `d586ce3`. Passed URIs/chunk-templates/version-pinning/subscriptions/tool-fallback; failed native triage field names (used `summary`/`sizeBytes`/`updatedAt`), non-namespaced `_meta`, and no completion on templates. [evidence](runs/2026-07-11-scenario5-baseline.md) |
| 2026-07-11 | 5 (resources) | with-skill | 8/8 | Tree `d586ce3`. Native `size`/`annotations.lastModified` triage fields; namespaced `_meta` (`com.acme-wiki/*`); `completion/complete` for `{space}`/`{slug}`; bounded `resources/list`; one-envelope-two-carriers resource failures. [evidence](runs/2026-07-11-scenario5-with-skill.md) |
| 2026-07-11 | 6 (prompts) | baseline | 5/7 | Tree `d586ce3`. Passed canonical-name refs, native `PromptArgument` fields, workflow-vs-schema split, no top-level convention fields; failed declared prerequisites and completion on dynamic arguments. [evidence](runs/2026-07-11-scenario6-baseline.md) |
| 2026-07-11 | 6 (prompts) | with-skill | 7/7 | Tree `d586ce3`. Prerequisites block + convention metadata under namespaced `_meta` (`com.incident-mcp/*`); completion for `severity`/`responders`/`commander`; explicit "delete the prompt — does anything break?" §5 audit; contract kept in tool/resource schemas. [evidence](runs/2026-07-11-scenario6-with-skill.md) |
| 2026-07-02 | 4 (prose) | baseline (skill pre-§2/§3/§4 prose rules) | 1/5 | `force` fact and 30-day condition checkable (A3 pass), but description rules buried mid-narrative and the dry-run rule left in a workflow paragraph (A1 fail), "Recommended workflow" dry-run carries no override condition (A2 fail), one bullet bundles previous-release-only + 30-day GC + recovery action (A4 fail), rollback mechanics interleaved with eligibility rules (A5 fail). |
| 2026-07-02 | 4 (prose) | with-skill | 5/5 | Labeled `Rules:`/`Background:` sections; every rule mandatory or default-with-override; `force` carries the sourced fact plus an explicit permitted-use condition. Two earlier iterations were discarded: the first with-skill run echoed the guidance's then-deploy-domain examples (checklist examples moved to a messaging domain in response), and Codex review flagged that the prompt omitted `force` semantics so a pass could reward invented policy (sourced fact added to the notes). The scores above are the final runs against the corrected prompt. |
| 2026-07-11 | 2 (audit) | baseline | 2/9 | Tree `a3cd37f`. Rescored against the rewritten scoring (defect detection + priority bands; layout/vocabulary non-scored). No predeclared severity bands or §-anchoring, missed `additionalProperties: false`, generic remediations. [evidence](runs/2026-07-11-scenario2-baseline.md) |
| 2026-07-11 | 2 (audit) | with-skill | 9/9 | Tree `a3cd37f`. Rescored against the rewritten scoring. Impact-ranked findings (readOnlyHint lie merge-blocking), §-anchored, full §1–§9 coverage table with N/A reasons, concrete renamed-surface remediations. [evidence](runs/2026-07-11-scenario2-with-skill.md) |
| 2026-07-11 | 4 (prose) | baseline | 1/5 | Tree `a3cd37f`. Rerun on the pinned tree. Binding rules not separated from narrative (A1), Friday soft-norm + dry-run hedge unresolved (A2/A5), rollback mechanics compound-bundled with the 30-day rule (A4); A3 pass. [evidence](runs/2026-07-11-scenario4-baseline.md) |
| 2026-07-11 | 4 (prose) | with-skill | 5/5 | Tree `a3cd37f`. Rerun on the pinned tree. Labeled Rules/Background/When-to-use blocks, mandatory-vs-default-with-override phrasing, hedges removed. [evidence](runs/2026-07-11-scenario4-with-skill.md) |
| 2026-07-11 | 1 (design, outputSchema-on-error) | with-skill | PASS | Tree `a3cd37f`. Focused error-path probe of the new §3/§6 assertion: every error result sets `isError: true` with the §6 envelope in `structuredContent` + `content` fallback; `outputSchema` scoped to success (stated as the skill's reading of an unsettled point). Baseline expectation (documented): error results reuse the success shape or omit the envelope. [evidence](runs/2026-07-11-scenario1-outputschema-error.md) |
| 2026-07-11 | 7 (trigger) | proxy | recall 4/4, precision 4/4 | Tree `a3cd37f`. Labeled classifier **proxy** (Haiku 4.5), 3 trials/case, randomized 7-skill catalog. T1/T3/T5/T8 fire 12/12; T2/T4/T6/T7 quiet 12/12. Anticipated weaknesses (T8 symptom miss, T2/T7 keyword over-match) did not appear. Not the host's real selector. [evidence](runs/2026-07-11-scenario7-trigger.md) |
