# MCP Server Design Workflow

Use this workflow when designing a new MCP server or redesigning an existing one.
The output is a server whose tools, resources, and prompts pass `contract-checklist.md` end to end — that is, a contract an agent can plan against from the schema and structured responses alone, never undocumented prose outside the schema.

The steps are deliberately re-entrant: a later step often surfaces a flaw in an earlier decision (a granularity choice that breaks the schema, a discovery design that breaks under eval, an error taxonomy that exposes a missing primitive).
Re-entering Step N from Step N+3 is normal — treat earlier outputs as drafts until the eval at Step 8 holds.
The checkpoint links at each step point at the relevant `contract-checklist.md` sections; consult them as you work, not only at the end.

**On this page:**

- [Step 0: Fix the protocol revision target](#step-0-fix-the-protocol-revision-target)
- [Step 1: Enumerate user/agent tasks](#step-1-enumerate-useragent-tasks)
- [Step 2: Choose the right primitive per task](#step-2-choose-the-right-primitive-per-task)
- [Step 3: Decide tool granularity](#step-3-decide-tool-granularity)
- [Step 4: Write input schemas](#step-4-write-input-schemas)
- [Step 5: Design the discovery surface](#step-5-design-the-discovery-surface)
- [Step 6: Design failure recovery](#step-6-design-failure-recovery)
- [Step 7: Design long-running behavior](#step-7-design-long-running-behavior)
- [Step 8: Build evaluations grounded in real tasks](#step-8-build-evaluations-grounded-in-real-tasks)
- [Step 9: Iterate against the eval, including transcript review](#step-9-iterate-against-the-eval-including-transcript-review)

## Step 0: Fix the protocol revision target

Decide which protocol revision(s) and which extensions the target clients speak, and record the decision per `[1.spec-revision]`, which owns the declaration's required content.
Every later step designs against that target.

Output: a one-line revision-and-extensions target that the capability summary (Step 5) will carry.

## Step 1: Enumerate user/agent tasks

List the real tasks an agent should be able to complete with this server.

- Write tasks, not endpoints. "Send a message to a Slack channel" is a task; `POST /chat.postMessage` is not.
- Pull tasks from real workflows — what a user or agent already does end-to-end without this server.
- Group near-duplicates; collapse 60 endpoint variations into the 6–10 tasks they actually serve.
- Note prerequisites per task (auth scope, workspace/project context, prior calls, implicit state, handles, cursors, jobs, or sessions that affect behavior) — these surface again in Step 5.
- Note optional MCP capabilities each task benefits from or requires: roots, completions, resource subscriptions, elicitation, list-change notifications, and tasks.
  Record the weaker-client fallback at the same time.

Output: a written task list, each task expressed as a verb phrase with the user/agent goal.
Checkpoint: §1, §3 granularity rule.

## Step 2: Choose the right primitive per task

For each task, pick tool, resource, or prompt scaffold.

- State-changing or operational?
  Tool.
  Annotate with `destructiveHint` / `idempotentHint` honestly.
- Read-only and browseable, with stable URIs the agent will quote back?
  Resource.
- Multi-step orchestration scaffolding the agent invokes by name?
  Prompt — but never load-bearing for correctness.
- A "write resource" is a tool.
  A "prompt that defines argument shapes" is a tool schema in disguise; reclassify it.

Output: per-task primitive assignment (tool / resource / prompt) with one-line justification each.
Checkpoint: §3, §4, §5.

## Step 3: Decide tool granularity

For each tool, choose task-completing vs composable primitive, and record the reason.

- Default to task-completing.
  Hide internal step granularity unless the steps are themselves separately useful tasks.
- Split into composable primitives only when a named exception applies or an eval (Step 8) shows the split helps a code-execution client — not on speculation.
- Named split exceptions: intermediate state inspection, branching on intermediate results, streaming large results, step reuse across workflows, and human-approval boundaries.
- Endpoint-shaped tools are an anti-pattern: collapse endpoint chains into the task they serve.
- If two tools' descriptions need long prose to disambiguate, the granularity is wrong.

Output: per-tool granularity decision with explicit reasoning (task-completing or composable, and why).
Checkpoint: §3 granularity rule.

## Step 4: Write input schemas

Treat the schema as the authoritative contract.
Write it before behavior.

- Required vs optional discipline: required parameters are necessary; optional ones declare their omission semantics in the schema description, with `default` only where the server actually applies that value (§3).
- Strict types: enums for fixed value sets; formats (`date-time`, `uri`, `email`); `integer` vs `number` chosen deliberately.
- Schema dialect: declare it where supported, and close object schemas with `additionalProperties: false` unless extension fields are intentional.
- Outputs: satisfy `[3.output-schema]`, which owns the per-tool scope of that obligation, its carve-out, and the `content` fallback.
- Rich results: for large or binary outputs, plan `resource_link` or embedded `resource` content plus a concise `structuredContent` summary instead of inlining bulk data.
- Disambiguating names: `user_id` not `user`, `started_after` not `since`, `channel_id` not `channel`.
- Descriptions cover when to use, edge cases, and an example invocation.

Output: complete tool input schemas, resource URI patterns, and prompt argument schemas — including descriptions and examples.
Checkpoint: §3, §4. See `examples.md` §1 for a worked tool schema and §5 for a prompt scaffold.

## Step 5: Design the discovery surface

Decide how an agent finds the right primitive at the lowest cost its clients allow (§2 defines the client-dependence rules).

- Write the server capability summary: what it does, what it does NOT do, and any prerequisites that affect whether or how an agent should use it.
- Expose the summary through a resource, discovery tool, or instructions field, whichever the client honors.
  Treat `instructions` as supplemental because some clients do not surface it to the model.
- Make compact definitions your baseline (§2), then, if you need progressive disclosure, pick a mechanism by cost axis: host-managed context disclosure, server-managed catalog disclosure, or client-independent surface reduction — only the last helps a client that preloads and never lazy-loads.
- Make discovery selective, but through a discovery tool, resource catalog, or authorization-scoped catalog — native `tools/list` takes only a pagination cursor and has no filter parameters.
  A flat list of 80 tools is undiscoverable.
- Index resources; do not inline bodies.
  Catalog entries carry triage metadata only.
- Publish `resources/templates/list` for URI-shaped resources that cannot or should not be fully enumerated.
- Implement `completion/complete` for prompt arguments and resource-template variables with dynamic value sets when the server advertises the `completions` capability.
  Document that completion does not cover arbitrary tool arguments.
- For workspace-scoped servers, take workspace scope as ordinary tool arguments; for clients that still declare the deprecated `roots` capability, obtain roots via MRTR and declare how changes are handled (`[1.roots]`).
- If resource discoverability matters, provide a tool fallback for clients that do not expose resources well — self-sufficient from `tools/list` alone (§4).
- Set a serialized-size budget for `tools/list` — the wire response as a client receives it — and enforce it in CI (Step 8).

Output: server capability summary, discovery primitives implemented, resource catalog shape.
Checkpoint: §1, §2, §4. See `examples.md` §7 for a server capability summary and §8 for a `search_tools` response.

## Step 6: Design failure recovery

Design the error surface as deliberately as the success surface.

- Define stable, symbolic error codes (`not_found`, `rate_limited`, `invalid_field`) and document per-tool which codes can occur.
- Field-level validation feedback: which field, why invalid, allowed values; include the offending value per `[6.offending-value]`.
- Retryability and rate-limit signals: `retry_after_ms`, `temporary`, `rate_limit_remaining` where applicable.
- Tool semantic errors return as tool result errors with `isError: true`.
- Resource failures return JSON-RPC errors with structured `error.data` repair fields; allocate their numeric `error.code` per `[6.jsonrpc-code-allocation]`.
- Repair hints reference real callable surfaces — tool names, parameter names, valid enum values — not free-form prose.
- Capability failures use the native error (`MissingRequiredClientCapability`, `-32021`, with `data.requiredCapabilities`) and name the fallback path in the same `error.data` (`[6.capability-missing]`).
- If the server can use elicitation for missing input or sensitive external flows, define both the elicitation path and the non-elicitation fallback error.
- Draft a worked JSON payload for each top failure mode — not just a field inventory.
  Concrete payloads expose contradictions a field list hides.

Output: error taxonomy with example payloads for the top failure modes per tool, including correlation context (`request_id`, offending parameter).
Checkpoint: §6. See `examples.md` §6 for an actionable error payload.

## Step 7: Design long-running behavior

For each operation that may outlive a normal request/response turn, decide how the agent monitors and recovers it.

- Choose blocking `tools/call`, progress notifications, or a task returned under the negotiated tasks extension.
- Declare expected duration, timeout behavior, whether partial progress is observable — and, because task creation is server-directed, when a tool may answer with `resultType: "task"` (`[7.declare-duration]`).
- Support `progressToken` for request-bound progress, and recover continuing work through native task operations — poll `tasks/get` (respect `pollIntervalMs`; terminal payloads arrive inline), supply input with `tasks/update`, cancel with `tasks/cancel` — using the extension's task fields and statuses (`working`, `input_required`, `completed`, `failed`, `cancelled`).
- For `input_required`, design around the native path — `tasks/get` carries the outstanding `inputRequests`, the client answers via `tasks/update` `inputResponses` — and say whether the input mechanism is form elicitation, URL-mode elicitation, or a domain-specific status/repair tool for clients without the capability (§7).
- Gate tasks on the extension at both ends: the client's per-request `clientCapabilities.extensions` declaration of `io.modelcontextprotocol/tasks` and the server's `server/discover` advertisement (`[7.task-support]`).
  Add a domain-specific status/cancel fallback as a labeled stand-in for clients that never declare the extension.
- Remember `completed` is a delivery statement: a tool error still lands as a `completed` task whose `result.isError` is true (`[7.failed-task]`).

Output: long-running behavior contract for each affected tool, including progress, cancellation, retrieval, and terminal-state semantics.
Checkpoint: §7.

## Step 8: Build evaluations grounded in real tasks

Build an eval suite from the Step 1 task list before iterating further.

- Use real workflows from Step 1 as the eval tasks; synthetic prompts hide discovery and selection failures.
- Measure first-call correctness: did the agent pick and call the right primitive on the first attempt?
- Measure first-repair correctness: given a structured error, did the agent's next call succeed?
- Measure token consumption and tool-call count per completed task — both are first-class quality signals.
- Include fixture types for cold-start/tool discovery, wrong-tool selection, invalid-argument, auth-failure, pagination, upgrade/version-change, and long-running progress plus cancel/recover.
- Add a `forced_error` fixture per tool asserting `isError: true`, carrier location, and envelope shape on the serialized wire result — unit-level error objects can pass while framework serialization is broken.
- Add a `discovery_size_budget` fixture asserting the exact serialized `tools/list` response against a deterministic byte budget, runnable in CI; pin the tokenizer if the budget is stated in tokens.
- Add a `host_capture` fixture per target client: record the serialized `tools/list` payload as that client receives it and real `tools/call` arguments as observed at the server, and treat what hosts actually do — stringify containers, truncate descriptions, hide annotations, cache stale schemas, expose resources poorly — as compatibility constraints on the design, not protocol facts.
  Captures gate the §3 stringified-argument shim and the §2 retrieval-phrasing note: neither applies without observed evidence.

A worked fixture for one task makes the metrics runnable rather than aspirational.
Each fixture pairs a prompt with an assertion the harness can check against the transcript:

```json
{
  "task": "announce that api@v2.4.1 shipped to the #deploys channel",
  "expect_first_call": {"tool": "slack_send_message", "args_match": {"text": {"contains": "v2.4.1"}}},
  "metrics": {"max_tool_calls": 2, "max_tokens": 4000},
  "fixtures": [
    {
      "type": "cold_start",
      "given": "fresh connection, no prior discovery",
      "assert": "parameterized by host mode: on a preloading host, the serialized definitions the host injects stay within the token budget (the agent cannot avoid the load); on a lazy-loading host, the agent reads the capability summary or search_tools before loading full definitions"
    },
    {
      "type": "first_repair",
      "given": "first call passes channel_id='deploys' (a name, not a C… id)",
      "inject_error": {"code": "channel_not_found", "repair": {"tool": "slack_lookup_channel"}},
      "assert": "next call is slack_lookup_channel(name='deploys'), then slack_send_message with the resolved id — repair succeeds in one hop"
    },
    {
      "type": "discovery_cost",
      "assert": "tokens spent before the first useful call stay under the max_tokens budget; record the count, do not estimate"
    }
  ]
}
```

Score each run for first-call correctness (did `expect_first_call` match?), first-repair correctness (did the injected error resolve in one hop?), and the two budgets.
A fixture that no current transcript can satisfy is a finding against the schema, not a flaky test.

`expect_first_call`, `inject_error`, `expect_repair`, and `metrics` are **harness declarations, deliberately abbreviated** — they tell the runner what to do, and `inject_error.repair` names only the fields the assertion turns on.
Wire conformance is judged on the serialized payloads a fixture records, not on these declarations; keep a fixture's wire section for anything that must satisfy the §3 output contract or the §6 envelope.

Output: at least one eval task suite covering the high-value tasks from Step 1, with metrics for first-call correctness, first-repair correctness, token consumption, and tool-call count.
Checkpoint: §2, §3, §6, §7, §8, §9 — the eval should exercise discovery, tool selection, error repair, long-running behavior, token consumption, and upgrade behavior.

## Step 9: Iterate against the eval, including transcript review

Run the eval with an agent and read the transcripts; do not trust aggregate scores alone.

- Look for contradictions between description prose and schema; the schema wins, so fix the prose or the schema.
- Look for ambiguity in tool selection — two tools the agent confuses, or descriptions that overlap.
- Look for ineffective discovery: agents loading every definition, agents missing a tool that exists, agents picking the wrong namespace.
- Each iteration should produce a measurable improvement in first-call correctness, first-repair correctness, or token consumption, without regressing first-call or first-repair correctness — if it doesn't, the change wasn't grounded.

**When the eval cannot be run.** Designing a contract does not always come with somewhere to run it.
Record the step as `not-run` when an execution prerequisite is genuinely unavailable — no runnable host or no eval harness — and name which one is missing.
A `not-run` record claims no improvement and does not complete the step; it states what would have to exist before the step could run.

`not-run` is available only when the prerequisite is actually absent.
An eval that exists and could be run is not `not-run` because running it was inconvenient or because the author declared it outside the current scope.
A missing *baseline* is not a qualifying prerequisite when the host and harness exist: run the suite and record that run as the baseline, because a baseline nobody creates would otherwise excuse the gate forever.
Step 8 is unaffected: a suite is fixtures plus assertions, and authoring one needs no host.

Output: revised schemas, descriptions, and discovery surface, with eval-measured improvement against the prior baseline — or a `not-run` record naming the missing prerequisite.
Checkpoint: §1–§9 — walk the full checklist; iteration is the moment to catch contradictions any earlier checkpoint missed.
