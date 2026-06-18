# MCP Server Contract Checklist

This is the normative standard for the skill, used by both `design-workflow.md` and `review-workflow.md`. The section order serves audit walks: discovery first (what an agent sees first), then the primitives that get invoked, then the cross-cutting concerns that span all primitives, then versioning. Walk it top to bottom when designing or reviewing a server.

---

## 1. Server-Level

*Worked shapes: `examples.md` §7 (server capability summary), §8a (roots-aware workspace behavior).*

- **Name with a service prefix.** Use a descriptive, agent-facing name with no version numbers, not a host-language convention. The name shows up in tool selection across multi-server contexts and is part of the discovery surface.

- **Avoid generic service names.** Names like `api`, `data`, or `tools` collide silently in multiplexed clients. Pick a name a human could disambiguate at a glance.

- **Choose transport explicitly.** Use `stdio` for local single-client; use streamable HTTP for shared or remote. Document the choice in the capability summary.

- **`stdio` servers MUST NOT log to stdout.** Stdout is the JSON-RPC channel; mixing log output corrupts the protocol stream. Send logs to stderr or a file.

- **Declare the auth model when it affects agent behavior.** State required scopes and permission boundaries that change capability availability, result shape, or repair. Keep credential setup mechanics out of the first-read summary unless the agent can act on them.

- **Distinguish credential failure modes.** "Missing credential," "wrong credential," and "insufficient scope" are three different repair paths. Collapsing them forces the agent to guess.

- **Declare agent-actionable implicit state.** List workspace/project context, default resources, caches, session data, or configuration only when it affects tool choice, results, permissions, or repair.
  Hidden deployment details belong in operator docs, not the first-read surface.

- **Declare state-handle discipline.** Handles for jobs, cursors, sessions, or server-side state are opaque IDs with readable labels where useful, declared lifetime, expiry behavior, auth checked on every use, and bounded retention.

- **Surface observability in responses, not dashboards.** Rate limits, timeouts, retry hints, deprecation notices, and the capability fingerprint belong in the response payload an agent reads. Operator dashboards are out of scope here.

- **Treat server metadata as contract.** Name, version, fingerprint, and summary are part of the discovery surface.
  Changes to them are discoverable changes (see §9).
  See `examples.md` §7 for a capability summary that carries server identity, negative scope, and actionable prerequisites.

- **Declare display metadata where humans choose capabilities.** Use native `title` and `icons` on tools, resources, resource templates, prompts, and implementation info when a client has a human-facing picker.
  These fields are not a substitute for precise names and descriptions, but they reduce wrong selection in mixed human/agent workflows.

- **Record negotiated capabilities as part of the contract.** During initialization, client and server exchange protocol versions and capabilities; optional features are usable only if negotiated.
  A design or audit must say which fully qualified paths it depends on (for example, `server.capabilities.completions`, `server.capabilities.resources.subscribe`, `client.capabilities.roots`, `client.capabilities.elicitation`, or `server.capabilities.tasks.requests.tools.call`) and what fallback weaker clients receive.
  *Forward-compat (see `SKILL.md` Spec Baseline):* the 2026-07-28 RC is expected to make negotiation per-request rather than init-time and to express optional features as a reverse-DNS `extensions` map. The contract obligation — declare what you depend on and what weaker clients get — is stable; the carrier mechanism is a likely migration point.

- **Handle roots deliberately for workspace-scoped servers.** If a server reads or writes local project content, request `roots/list` from clients that advertise `roots`, stay within those declared roots unless the tool contract explicitly says otherwise, and handle `notifications/roots/list_changed`.
  Roots guide server behavior and reduce path ambiguity; they are not access control, so still enforce filesystem permissions independently.
  *Forward-compat:* roots is slated for deprecation in the 2026-07-28 RC on a long retention window, with explicit tool-minted/model-returned handles taking over scope declaration. Keep workspace scope expressible as ordinary tool arguments so it survives roots' removal.

- **Expose auth mechanics that affect repair.** For HTTP authorization, document the canonical server URI and resource indicator used for token audience binding, never pass through tokens issued for a different resource, and surface incremental or step-up scope challenges as structured repair (`required_scopes`, `resource`, `authorization_url` or elicitation URL where appropriate).
  For stdio, document where credentials come from only when the agent can act on it.

Audit prompt: Can an agent learn what this server does, what it doesn't, and which prerequisites affect use, in a single read?

---

## 2. Discovery Primitives

*Worked shapes: `examples.md` §7 (server capability summary), §8 (`search_tools` response shape).*

- **Provide a server capability summary.** A concise overview of what the server does, what it does not do, and any prerequisites that affect whether or how an agent should use it.
  Expose it via a resource, discovery tool, or instructions field, whichever the client honors.

- **State negative scope explicitly.** What the server does NOT do is as important as what it does. Negative scope prevents wasted exploration and wrong-tool selection.

- **Provide at least one progressive-disclosure mechanism.** `search_tools` / `describe_tool` and resource-catalog endpoints are valid patterns. The requirement is that clients can load definitions on demand rather than receiving everything upfront.

- **Make discovery selective.** Clients can filter by name, namespace, or topic. A server with 80 tools and no filter is functionally undiscoverable.

- **Use native completion for hard-to-guess prompt and resource-template arguments.** If the server has prompt arguments or resource-template variables with large, dynamic, or enum-like value sets, advertise `server.capabilities.completions` and implement `completion/complete`.
  Use it proactively to reduce invalid IDs, paths, project keys, channel names, and URI components; keep tool-argument repair in normal tool errors because MCP completion does not complete arbitrary tool arguments.

- **Discovery obeys token-efficiency rules.** Definitions paginate, support filtering, and return concise summaries by default with an opt-in detailed mode (see §8).

- **Design for client variance.** Some clients preload tools, paginate discovery, ignore annotations, or expose resources poorly; keep discovery usable for the least-capable realistic client.

- **Index resources; do not inline bodies.** Catalog entries carry metadata sufficient to triage; bodies are fetched on demand (see §4).

- **The discovery surface itself is contract.** Renaming a tool, changing its namespace, or removing it from a catalog is a discoverable change to clients; treat it as a versioned event (see §9).

- **Resource catalogs are part of discovery.** A catalog that omits new resources or returns inconsistent metadata silently breaks agent planning. Treat the catalog as authoritative.

- **Include the capability fingerprint in discovery responses.** Clients can short-circuit a re-walk if nothing has changed (see §9).

- **Discovery may vary by authorization context, but never by hidden side effects.** The tool and resource lists an agent sees may legitimately differ across auth scopes — an unauthorized scope simply does not see a capability. They MUST NOT drift as a side effect of unrelated calls within a connection: the same authorized client gets the same surface in the same order (see §9), so a cached client can trust it. Make differences auth-scoped, declared, and stable, not per-request surprises.

Audit prompt: Can an agent find the right tool or resource for a task without loading every definition the server exposes?

---

## 3. Tools

*Worked shapes: `examples.md` §1 (namespaced tool schema), §2 (structured tool response), §10 (worked task: API mirroring vs. task completion), §12 (response-delivery artifact), §13 (tool result with resource link).*

- **Name with `snake_case`, prefix, verb, noun.** `slack_send_message`, not `send_message`. Generic verbs collide across servers in multi-server contexts.

- **Reuse verbs consistently.** `list`, `get`, `create`, `update`, `delete`, `send`, `search` should mean the same thing across tools. Inconsistent verbs make the agent second-guess otherwise-obvious calls.

- **Write descriptions that are narrow and unambiguous.** Cover when to use, edge cases, and an example invocation. Descriptions are the primary input to tool selection.

- **Disambiguate parameter names.** Use `user_id`, not `user`; `channel_id`, not `channel`; `started_after`, not `since`. Ambiguous names cause wrong-shape arguments on the first call.

- **Apply required-vs-optional discipline strictly.** Required parameters must be necessary; optional parameters must have meaningful defaults declared in schema.

- **Use strict types.** Enums where the value set is fixed; formats (`date-time`, `uri`, `email`) where the shape is conventional; `integer` vs `number` chosen deliberately.

- **Declare schema dialect where supported.** A schema without a dialect forces clients and validators to infer semantics.

- **Close object schemas.** Use `additionalProperties: false` on all object schemas unless unknown extension fields are an intentional, documented contract.

- **Publish an `outputSchema` and return `structuredContent` when targeting MCP versions that support them.** This is the normative target, not an optional nicety: when a tool declares an `outputSchema`, servers MUST return conforming `structuredContent` on success results, and clients SHOULD validate against it (the `isError: true` carve-out is the next item).
  Keep parser-compatible JSON in `content` as a fallback for older or weaker clients; support varies across MCP versions, so the fallback stays useful — but it is the fallback, not the contract.

- **Scope `outputSchema` to success results — a deliberate reading of an unsettled point.** The spec does not say whether `outputSchema` binds `isError: true` results.
  This skill takes the position that it governs success results only: an `isError: true` result carries the documented error envelope in `structuredContent` (see §6) instead of the success shape, and is not validated against `outputSchema`.
  Document the error envelope per tool — or union it into `outputSchema` — so validators never have to guess which shape applies; do not leave the two contracts in silent conflict.

- **Default to structured output.** Structured data is authoritative; text or markdown is supplemental rendering for human-facing clients.
  Token-efficiency rules for responses live in §8.

- **Use rich content types deliberately.** Tool results may include `text`, `image`, `audio`, `resource_link`, embedded `resource`, and `structuredContent`.
  Put machine-contract fields in `structuredContent`; use `content` for human rendering, linked artifacts, and compatibility fallbacks.

- **Prefer resource links over inline bulk or binary payloads.** For large documents, generated charts, exports, or files, return a concise `structuredContent` summary plus a `resource_link` with `uri`, `name`, `description`, `mimeType`, `size` where known, and `annotations.lastModified` where useful.
  Resource links returned by tools may not appear in `resources/list`, so include enough metadata for the agent to fetch, subscribe, cite, or discard them.

- **Use content annotations to steer attention, not correctness.** `annotations.audience`, `priority`, and `lastModified` help capable clients decide what to show the user vs. model and when to re-read linked content.
  Never make safety or parsing depend on annotations being visible.

- **Declare side effects, idempotency, and rate limits as first-class contract.** Use tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) and structured response fields.

- **Set annotations honestly.** `readOnlyHint: true` on a tool that mutates is worse than no annotation, because clients will skip safety prompts.

- **Define mutation by observable scope, not by I/O — a deliberate reading of an ambiguous hint.** The MCP spec glosses `readOnlyHint` only as "the tool does not modify its environment," which is broad enough to read either way for a local write.
  This skill takes the position that `readOnlyHint` should track whether the call changes state that outlives the response contract: shared systems, persistent records, other users' data, or persistent state in the caller's environment that other calls or tools can observe.
  Under that reading it is not about whether the tool performs any I/O at all, and a write to the caller's filesystem does not by itself count as mutation.
  A transient artifact written purely as response delivery — for example, a CSV or Parquet result file with a declared TTL, scoped to this call, and no shared visibility — is treated as part of the response, not a side effect, so the tool stays `readOnlyHint: true`.
  This is a judgment call, not settled spec: a reviewer who reads "environment" literally may disagree, so document the choice rather than asserting it.
  Prefer returning large results as resources or resource links with TTL metadata where you can, and reserve the response-delivery-artifact pattern for cases where an inline or linked resource does not fit.
  Disclose the artifact through a structured response field (e.g., `result_artifact: {path, ttl_hours, mimeType}`) and the tool description, not by flipping the annotation.
  See `examples.md` §12 for a worked response-delivery artifact.

- **Annotations are hints, not security.** Declare them so agents can plan; do not rely on them for access control.
  Enforcement lives in the implementation.

- **Do not rely on annotation visibility.** Some clients do not surface annotations to the model, so annotations are advisory and cannot be the only safety prompt.

- **Prefer task-completing tools over endpoint mirrors.** Tool granularity follows user/agent tasks, not the underlying API's resource map.
  Valid split exceptions are recorded in `design-workflow.md` Step 3.

- **Hide internal step granularity.** When a task requires multiple steps internally, expose the task — not the steps — unless the steps are themselves separately useful tasks.

- **Failure paths are part of the contract.** Repair signals belong in tool result errors (see §6), not in description prose.

- **Declare every parameter the tool reads.** Hidden parameters that affect behavior belong in the agent-actionable implicit state declaration (see §1), not implicit in tool behavior.

- **Design tool surfaces to survive code-execution clients.** When an agent imports the server as a code API instead of calling tools through a chat UI (see [mcp-vs-cli.md](mcp-vs-cli.md)), tool and parameter names become identifiers in source and errors cross a language boundary. Keep names import-friendly (`snake_case`, no collisions when flattened into one module namespace), keep `structuredContent` the authoritative return so it maps to a native value, and choose symbolic error `code`s that translate cleanly into language-native exceptions. A task-completing tool composes in code far better than an endpoint chain the agent must re-orchestrate by hand.

### Anti-patterns

- **Wrapping every API endpoint as a tool.** More tools dilute discovery, increase token cost on every definition load, and make selection harder. A 60-tool server where every tool maps to a REST endpoint typically expresses 6–10 actual user tasks. Collapse endpoint chains into the task they serve.

- **Burying side effects, idempotency, or rate limits in description prose.** Agents do not reliably read prose for safety-relevant signals. If a tool mutates state, say so via `destructiveHint`; if it can be retried safely, say so via `idempotentHint`; if it has a per-minute call limit, surface that in the response, not the description.

- **Flipping `readOnlyHint` to `false` because the tool writes a transient response artifact.** Equally misleading as the inverse. Clients use `readOnlyHint: false` to gate auto-approval and surface confirmation prompts; if the call is semantically read-only (no shared-state mutation), `false` creates unnecessary friction without protecting against any real risk. Disclose response artifacts through the structured response and the tool description, not through the annotation.

### Security

- **Treat external content as untrusted.** Tool output must not smuggle instructions that override the user's task or the server contract.

- **Sanitize output before returning it.** Strip or escape credentials, control sequences, and content that would corrupt the client's rendering or parser.

- **Minimize data exfiltration paths.** Open-world tools need explicit data-flow review because they can send local or retrieved data to external services.

- **Use least-privilege scopes.** Request only the scopes needed for the exposed tasks, and surface insufficient-scope repair separately from missing credentials.

- **Define confirmation boundaries.** Destructive, paid, external-send, or broad-read operations need clear preconditions before execution.

- **Keep state handles opaque.** Handles must not embed credentials or internal record IDs; they are references to server-side state, not encoded payloads.

- **Keep this subsection bounded.** These are agent-facing contract rules only; full threat modeling (trust boundaries, attack trees, data-flow analysis) is out of scope here — route it to a dedicated security review.

Audit prompt: For each tool, can an agent decide to use it, call it correctly, and recover from a failure — using only the schema and structured response, never the prose?

---

## 4. Resources

*Worked shapes: `examples.md` §3 (resource index entry), §4 (resource body with chunking), §5a (resource template with completion), §5b (resource subscription).*

- **Use stable, hierarchical, predictable URI patterns.** Resource URIs are identifiers agents quote back to the server and may cache; instability breaks repeat calls.

- **URI segments use stable domain nouns.** Not internal identifiers that change between deployments. The URI is part of the contract.

- **Distinguish lightweight indexes from bodies.** Resource lists return summaries with metadata sufficient for the agent to decide whether to fetch the body.

- **Surface a summary before the body for large resources.** Force the agent to opt in to bulk content rather than handing it whole documents by default.

- **Make body content chunkable with stable identifiers.** Agents need to fetch chunk N+1 without re-fetching N, and to cite a chunk back to a tool.

- **Chunk identifiers are stable across reads of the same resource version.** If the resource changes, identifiers may change — but the change is observable via the resource's modification metadata.

- **Include the metadata fields agents use to triage.** Use native `Resource` fields: `title`, `description`, `mimeType`, `size`, and `annotations.lastModified`. Missing metadata pushes the agent into fetching bodies blindly; custom triage fields with no native home go under a namespaced `_meta` key, not a new top-level field.

- **Expose URI-shaped resources through resource templates.** When resources are parameterized by a URI pattern, publish `resourceTemplates` via `resources/templates/list` (with `uriTemplate`, `name`, `title`, `description`, `mimeType`) so agents can discover the shape without enumerating every instance.
  Templates are a native discovery primitive distinct from the resource list itself.

- **Pair resource templates with completion where values are dynamic.** For template variables such as `{project}`, `{channel_id}`, `{schema}`, or `{path}`, implement `completion/complete` when clients negotiate `completions`.
  This gives agents a proactive way to build valid URIs instead of learning the value space through failed reads.

- **Keep summaries short.** Typically 1–3 sentences, not paragraphs. Resource summaries appear in lists of dozens; long summaries defeat the index.

- **Support resource subscriptions for mutable resources.** If a resource can change during a long-lived agent session and stale reads matter, advertise `resources.subscribe`, accept `resources/subscribe`, and emit `notifications/resources/updated` for subscribed URIs.
  Use `notifications/resources/list_changed` for catalog membership changes; use per-resource updates for body changes.

- **Resources share the failure-recovery contract.** Missing-credential, not-found, gone, and rate-limit signals must be machine-readable (see §6).

- **Resource failures use JSON-RPC errors.** Put the repair contract in structured `error.data` fields such as `human_message`, `machine_code`, `repair_hints`, and `recoverable`.

- **Some clients do not expose resources well.** If discoverability matters for a read-oriented capability, provide a tool fallback that reaches the same indexed content.

- **Pagination, filtering, and truncation rules live in §8.** Reference them; do not duplicate.

- **A write-surface "resource" is a tool.** Resources are read-oriented; if mutation is the point, model it as a tool with the appropriate annotations (see §3).

Audit prompt: Can an agent decide whether to fetch a resource — and which chunk of it — without first reading the full body?

---

## 5. Prompts

- **State when to use the prompt explicitly.** The agent needs to recognize the matching task, not infer it from the title.

- **List prerequisites.** Which tools, which resources, and which permission or context assumptions the prompt relies on. Missing prerequisites surface as confusing failures partway through execution.

- **Offer completion for prompt arguments with dynamic value sets.** If a prompt argument asks for a project, workspace, repository, environment, channel, or similar value the agent should not guess, support `completion/complete` when `server.capabilities.completions` is negotiated.

- **Reference expected follow-on tools and resources by name.** A prompt that doesn't tell the agent what to invoke next is half a scaffold.

- **Keep prompts as orchestration scaffolding, not contract.** Essential behavior — argument shapes, side effects, error shapes — belongs in tool and resource schemas. See `examples.md` §5 for a prompt scaffold that references tools and resources without redefining them.

- **Prompts may reference; they may not redefine.** A prompt may name a tool by canonical name, but it must not redefine the tool's contract or override its arguments. The schema is authoritative.

- **Prompts are optional.** A server with no prompts is fine; a server whose prompts are load-bearing for correctness is broken.

### Anti-patterns

- **Prompts as contract container.** Encoding required behavior in prompt text rather than schema means the agent must read prose to call correctly, and any client that bypasses prompts (most code-execution clients do) will call incorrectly. Orchestration scaffolding is not a substitute for schema. If behavior is essential, encode it in tool/resource schemas.

Audit prompt: If every prompt on this server were removed, would any tool or resource become incorrect or unsafe to call?

---

## 6. Failure Recovery

*(Cross-cutting; co-equal with first-call success.)*

- **Use stable, machine-readable error codes.** Codes are symbolic strings (e.g., `not_found`, `rate_limited`, `invalid_field`), not numeric exit codes. The symbolic code is the authoritative branch key for agents.

- **Document every error code per tool.** An undocumented code is an undiscoverable code; agents cannot branch on it reliably. List the codes a tool can return in its schema.

- **Code semantic changes are breaking.** Introducing a new additive code is safe; changing or renaming an existing code's meaning is a breaking change.
  Both are recorded in the fingerprint (see §9).

- **Provide field-level validation feedback.** Which field, why it's invalid, and which values are allowed. "Invalid input" without a field name forces the agent to guess.

- **Include the offending value when safe.** The agent's repair attempt depends on knowing what it sent. Redact when sensitive; never omit silently.

- **Signal retryability and rate limits explicitly.** Use `retry_after_ms`, `temporary: true|false`, and `rate_limit_remaining` where applicable. Agents need to distinguish "wait and retry" from "stop and reconsider."

- **Include "what to do next" repair hints.** The corrective call, parameter, or filter. The first repair attempt is as important as the first call.

- **Repair hints reference real, callable surfaces.** Tool names, parameter names, valid enum values — not free-form prose.

- **Surface capability-missing failures explicitly.** If a path needs an optional feature the client did not negotiate, return a structured error such as `capability_not_negotiated` with `required_capability`, the affected operation, and the fallback tool/resource/prompt when one exists.

- **Use elicitation only behind negotiated support and clear fallback.** When a server needs missing user input, user confirmation, or sensitive external interaction during a call, prefer MCP elicitation for clients that advertise it.
  URL-mode elicitation is the safe path for passwords, API keys, payment credentials, OAuth, or other sensitive exchanges.
  For clients without elicitation, return an actionable error or task `input_required` status that names the next callable surface or external action.

- **Tool semantic errors return as tool result errors.** Set `isError: true` on the tool result.
  JSON-RPC errors are reserved for transport, protocol, and non-tool RPC methods (such as `resources/read` and `resources/list`); raising a JSON-RPC error from `tools/call` strips the structured-response contract from the failure path.
  See `examples.md` §6 for an actionable tool-result error payload.

- **Resource semantic errors return as JSON-RPC errors.** `resources/read` and `resources/list` are non-tool RPC methods, so failures surface through the JSON-RPC envelope; carry the repair fields agents need (`human_message`, `machine_code`, `repair_hints`, and `recoverable`) in structured `error.data`.

- **Errors include correlation context.** A `request_id`, the offending parameter, and (where applicable) the resource URI. Agents need to correlate failures with the requests that caused them.

Audit prompt: For each failure mode, does the agent receive enough structured signal to either retry, repair, or escalate — without parsing the message field?

---

## 7. Long-Running Operations

*(Cross-cutting; rules apply when an operation may outlive a normal request/response turn.)*

- **Choose the execution mode deliberately.** Use blocking `tools/call` for short operations, progress notifications for bounded multi-step work, and task-augmented requests when clients need later status or result recovery.

- **Declare long-running behavior in the tool contract.** Tool descriptions or schemas include expected duration, timeout behavior, and whether partial progress is observable.

- **Support `progressToken` where progress exists.** Send rate-limited `notifications/progress` updates that identify current phase, completed work, and remaining work when knowable.

- **Support cancellation where work can continue after the call starts.** Honor `notifications/cancelled` for request-bound work and `tasks/cancel` for task-capable tools.

- **Declare task support at both levels.** Native task augmentation requires the server to advertise the `tasks` capability (`server.capabilities.tasks.requests.tools.call`) AND the tool to declare `execution.taskSupport` as `optional`, `required`, or `forbidden`.
  The per-tool flag alone is insufficient — without the server capability, clients must not attempt task augmentation.

- **Use native task operations for status and result retrieval.** Poll with `tasks/get` (respecting the returned `pollInterval`), retrieve the result with `tasks/result`, and cancel with `tasks/cancel`.
  Task objects use the spec's fields and casing — `taskId`, `status`, `createdAt`, `lastUpdatedAt`, `ttl`, `pollInterval` — and `status` is one of `working`, `input_required`, `completed`, `failed`, `cancelled`.
  Carry `io.modelcontextprotocol/related-task` in `_meta` on task-associated messages whose payload does not already name the task: `tasks/result` responses MUST include it, while `tasks/get`, `tasks/list`, and `tasks/cancel` SHOULD NOT, because the `taskId` already travels in the message itself.

- **Treat `notifications/tasks/status` as optional push, not contract.** Receivers MAY emit it on status changes with the full task state; requestors MUST NOT rely on receiving it.
  Keep polling `tasks/get` as the authoritative status path, and use the notification only to poll sooner.

- **Define `input_required` recovery.** If a task can pause for user input or authorization, say whether the recovery path is native elicitation (`elicitation/create`), URL-mode elicitation, or a domain-specific fallback.
  The task status alone is not enough; the agent needs the next operation and the capabilities required to perform it.

- **Tasks are experimental; degrade deliberately.** Tasks were introduced in MCP 2025-11-25 and are still experimental, so a server MAY also expose a domain-specific status/cancel tool as a labeled fallback for clients without task support — but it should mirror the native signals (current status, when to poll again, result location, expiry), not replace `tasks/*`.
  *Forward-compat (see `SKILL.md` Spec Baseline):* the 2026-07-28 RC is expected to move tasks out of core into a negotiated extension — server-directed task creation, `tasks/update` added, `tasks/list` removed. The labeled domain-specific fallback this checklist already requires is the hedge that survives that move; keep status/result/cancel expressible as ordinary tools so recovery does not depend on the task surface staying core.

- **Make recovery auditable.** A 2-minute operation should provide useful progress, and a client should be able to cancel or recover the result later.

Audit prompt: Can an agent monitor, cancel, and recover a long-running operation without guessing at server state?

---

## 8. Token Efficiency

*(Cross-cutting; rules apply to both tools and resources.)*

- **Default to a concise response.** Offer richer variants via an explicit detail toggle (e.g., `detail: "summary" | "full"`), not a free `response_format` parameter. See `examples.md` §2 for the concise-vs-detailed response pattern.

- **Detail is orthogonal to format.** Changing detail level changes the density of fields included, not the schema's shape. The same parser handles both modes.

- **Use cursor-based pagination by default.** Offset-based pagination is acceptable only when ordering is stable and the result set is small enough that pages don't shift between calls.

- **Pagination responses include `has_more`.** If `has_more` is true, include a navigation token (`next_cursor`) and, where available, `estimated_total`.

- **Provide filters that meaningfully reduce response size.** `since=`, `query=`, `category=`, `field=`. Filters that don't change wire size are noise.

- **Truncate explicitly with a repair hint.** `"truncated": true, "truncation_hint": "hit cap of 200; narrow with since= or query="`. Silent truncation breaks agent planning.

- **Choose identifiers by role.** Domain IDs with natural meaning can stay readable; state handles use opaque stable IDs with labels or summaries; security-sensitive references are opaque and never leak structure.

- **Support per-capability detail levels.** Progressive disclosure applies to both definitions (in discovery, see §2) and responses. The agent should be able to ask for "summary" before "full" at every level.

- **Strip null and default-valued fields from concise responses.** Where they add no information, they cost tokens and clutter parsing. Detail mode may include them for completeness.

- **Use locale-independent wire values.** Timestamps are RFC3339 UTC, currency uses ISO-4217 plus minor units, sort keys are stable, and display localization stays out of machine fields.

### Anti-patterns

- **Free `response_format` toggles** (markdown vs. json vs. xml as parallel contracts on the same tool).
  Format proliferation creates ambiguous contracts: which format is authoritative?
  Which one carries error signals?
  Which gets versioned when the schema changes?
  Prefer one structured default with optional supplemental text or markdown rendering.

Audit prompt: Could an agent complete a typical task on this server in a single context window, including discovery, calls, and one round of repair?

---

## 9. Versioning and Compatibility

- **Publish a capability fingerprint.** A versioned identity for the server's surface. Clients use it to detect breaking changes cheaply, without re-walking the discovery surface. See `examples.md` §9 for fingerprint evolution across deprecation and removal.

- **Advertise and emit protocol-native list-changed notifications.** Declare the `listChanged` capability where supported, and emit `notifications/tools/list_changed`, `notifications/resources/list_changed`, and `notifications/prompts/list_changed` when the corresponding list changes.

- **Distinguish list changes from resource updates.** `listChanged` means the catalog changed; `notifications/resources/updated` means a subscribed resource's contents changed.
  Emit both only when both facts are true.

- **Keep list ordering deterministic.** `tools/list` and resource catalogs use stable ordering so clients can diff and cache predictably.

- **Treat fingerprints as additive signals.** A capability fingerprint helps clients short-circuit discovery, but it does not replace native change notifications or stable list ordering.

- **The fingerprint covers the full agent-visible surface.** Tool definitions, resource catalogs, resource templates, prompt scaffolds, completion support, subscription behavior, negotiated-capability expectations, error codes, and the server capability summary.
  Anything an agent can plan against is part of the fingerprint input.

- **Define deprecation semantics.** How a tool, resource, or prompt is marked deprecated, how long it remains available, and what replaces it. Deprecation is a contract, not a sticky note.

- **Deprecated capabilities remain discoverable.** They continue to appear in discovery (see §2) until removal, with a deprecation marker and a pointer to the replacement. Silently dropping them breaks cached clients.

- **Adding optional fields is safe.** Removing or renaming fields, codes, or tools requires a fingerprint bump. Document the migration in the deprecation marker.

- **Treat tool rename as remove-plus-add.** Renaming a tool is a discovery-surface change (see §2) — clients that cached the old surface will break silently otherwise. Keep the old name with a deprecation pointer for the documented window.

- **Declare stability tiers if used.** `stable`, `preview`, `experimental`. Mixing tiers without labels makes every capability look stable, which is worse than labeling some as risky.

- **Stability tier is discovery metadata.** Each capability's tier is part of its discovery record so agents can filter by tier (see §2).

- **Error codes are part of the versioned surface (see §6).** Changing a code's meaning is a breaking change; introducing a new code is additive but still recorded in the fingerprint.

- **The fingerprint format itself is stable.** Changing how the fingerprint is computed (hashing algorithm, included fields) is a breaking change for any client caching by fingerprint.

Audit prompt: If a client cached this server's surface yesterday, can it tell — from the fingerprint alone — whether anything it depends on changed?
