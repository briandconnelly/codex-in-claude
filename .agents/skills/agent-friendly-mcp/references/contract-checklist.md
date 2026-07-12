# MCP Server Contract Checklist

This is the normative standard for the skill, used by both `design-workflow.md` and `review-workflow.md`.
The section order serves audit walks: discovery first (what an agent sees first), then the primitives that get invoked, then the cross-cutting concerns that span all primitives, then versioning.
Walk it top to bottom when designing or reviewing a server.

**On this page:**

- [1. Server-Level](#1-server-level)
- [2. Discovery Primitives](#2-discovery-primitives)
- [3. Tools](#3-tools)
- [4. Resources](#4-resources)
- [5. Prompts](#5-prompts)
- [6. Failure Recovery](#6-failure-recovery)
- [7. Long-Running Operations](#7-long-running-operations)
- [8. Token Efficiency](#8-token-efficiency)
- [9. Versioning and Compatibility](#9-versioning-and-compatibility)

---

## 1. Server-Level

*Worked shapes: `examples.md` §7 (server capability summary), §8a (roots-aware workspace behavior).*

- **Name the server distinctively.** Use a descriptive, agent-facing name with no version numbers, not a host-language convention.
  The name shows up in tool selection across multi-server contexts and is part of the discovery surface.

- **Avoid generic service names.** Names like `api`, `data`, or `tools` collide silently in multiplexed clients.
  Pick a name a human could disambiguate at a glance.

- **Choose transport explicitly.** Use `stdio` for local single-client; use streamable HTTP for shared or remote.
  Document the choice in the capability summary.

- **`stdio` servers MUST NOT log to stdout.** Stdout is the JSON-RPC channel; mixing log output corrupts the protocol stream.
  Send logs to stderr or a file.

- **Declare the auth model when it affects agent behavior.** State required scopes and permission boundaries that change capability availability, result shape, or repair.
  Keep credential setup mechanics out of the first-read summary unless the agent can act on them.

- **Distinguish credential failure modes.** "Missing credential," "wrong credential," and "insufficient scope" are three different repair paths.
  Collapsing them forces the agent to guess.

- **Declare agent-actionable implicit state.** List workspace/project context, default resources, caches, session data, or configuration only when it affects tool choice, results, permissions, or repair.
  Hidden deployment details belong in operator docs, not the first-read surface.

- **Declare state-handle discipline.** Handles for jobs, cursors, sessions, or server-side state are opaque IDs with readable labels where useful, declared lifetime, expiry behavior, auth checked on every use, and bounded retention.
  Opaque means client-opaque; the §3 security rule defines the two permitted handle modes (server-side reference or integrity-protected stateless token).

- **Surface observability in responses, not dashboards.** Rate limits, timeouts, retry hints, deprecation notices, and the capability fingerprint (where published, §9) belong in the response payload an agent reads.
  Operator dashboards are out of scope here.

- **Treat server metadata as contract.** Name, version, fingerprint (where published, §9), and summary are part of the discovery surface.
  Changes to them are discoverable changes (see §9).
  See `examples.md` §7 for a capability summary that carries server identity, negative scope, and actionable prerequisites.

- **Declare display metadata where humans choose capabilities.** Use native `title` and `icons` on tools, resources, resource templates, prompts, and implementation info when a client has a human-facing picker.
  These fields are not a substitute for precise names and descriptions, but they reduce wrong selection in mixed human/agent workflows.

- **Record negotiated capabilities as part of the contract.** During initialization, client and server exchange protocol versions and capabilities; optional features are usable only if negotiated.
  A design or audit must say which fully qualified paths it depends on (for example, `server.capabilities.completions`, `server.capabilities.resources.subscribe`, `client.capabilities.roots`, `client.capabilities.elicitation`, or `server.capabilities.tasks.requests.tools.call`) and what fallback weaker clients receive.
  *Forward-compat:* the negotiation carrier is a likely migration point in the 2026-07-28 RC (see `SKILL.md` Spec Baseline); the obligation itself — declare what you depend on and what weaker clients get — is stable.

- **Handle roots deliberately for workspace-scoped servers.** If a server reads or writes local project content, request `roots/list` from clients that advertise `roots`, stay within those declared roots unless the tool contract explicitly says otherwise, and handle `notifications/roots/list_changed`.
  Roots guide server behavior and reduce path ambiguity; they are not access control, so still enforce filesystem permissions independently.
  *Forward-compat:* roots is slated for deprecation in the 2026-07-28 RC (see `SKILL.md` Spec Baseline); keep workspace scope expressible as ordinary tool arguments so it survives roots' removal.

- **Expose auth mechanics that affect repair.** For HTTP authorization, document the canonical server URI and resource indicator used for token audience binding, never pass through tokens issued for a different resource, and surface incremental or step-up scope challenges as structured repair (`required_scopes`, `resource`, `authorization_url` or elicitation URL where appropriate).
  For stdio, document where credentials come from only when the agent can act on it.

Audit prompt: Can an agent learn what this server does, what it doesn't, and which prerequisites affect use, in a single read?

---

## 2. Discovery Primitives

*Worked shapes: `examples.md` §7 (server capability summary), §8 (`search_tools` response shape).*

- **Provide a server capability summary.** A concise overview of what the server does, what it does not do, and any prerequisites that affect whether or how an agent should use it.
  Expose it via a resource, discovery tool, or instructions field, whichever the client honors.

- **State negative scope explicitly.** What the server does NOT do is as important as what it does.
  Negative scope prevents wasted exploration and wrong-tool selection.

- **Write the capability summary and `instructions` prose as rules-then-context.** Lead with what the server does and does not do.
  Then state each binding rule — prerequisite, ordering requirement, safety constraint — as its own imperative sentence or list item whose strength is explicit: mandatory (e.g. external sends require an approved `sender_id`) or a default with its override condition (e.g. prefer `detail: "summary"` unless the task needs full field density).
  A hedge that leaves a statement's strength unclear ("generally", "try to", "be careful") is not a rule; replace it with the observable condition it stands for, or drop it.
  Background — implementation history, rationale, folklore — comes after the rules or not at all, never interleaved with them.

- **Compact definitions are the universal baseline.** Every entry `tools/list` returns is a complete `Tool` record — the protocol has no summary-only or filtered mode — so the one discovery cost you can lower regardless of client is the serialized size of each definition.
  Use a tight `inputSchema`, no redundant prose, and descriptions precise enough to select and repair but no longer.
  Measure serialized tokens, not tool count — and do not compress away the selection or safety information an agent needs.
  Measure the serialized wire response — `tools/list` as a client receives it — not your source models: generated output schemas and per-entry `$defs` expansion often dominate the bytes.
  Whether a client fetches every page and exposes all of it to the model is client-dependent; compact definitions pay off either way, which is what makes them the baseline.

- **Paginate large or unbounded catalogs, but treat it as scalability, not token savings.** Native `tools/list` cursor pagination caps peak response size, improves time-to-first-page, and lets a client stop early.
  A client that walks every page still pays for every definition plus cursor overhead, so pagination lowers token cost only when a client intentionally declines to fetch or expose later pages.
  Keep `nextCursor` semantics native (omission = done, see §8) and ordering deterministic so a catalog change mid-walk cannot strand or duplicate tools (see §9).

- **Progressive disclosure of tool definitions is a client-dependent optimization, not a universal guarantee.** The least-capable realistic client preloads the whole catalog and exposes it to the model, and adding discovery tools cannot shrink that for it.
  Design for that client, then choose a mechanism by which cost axis you can actually move on the clients you target:
  - **Host/client-managed context disclosure** — `search_tools` / `describe_tool`, or a resource catalog of operation metadata.
    The agent loads summaries, then pulls the few full definitions it needs.
    This lowers *model-visible context* only when the host withholds native definitions and injects selected schemas on demand (or routes execution through a stable generic call tool).
    On a host that still preloads `tools/list`, these are extra tools and round trips with no disclosure benefit, so document the host integration it assumes.
    On hosts that retrieve tools by lexical search over names and descriptions before the model sees any definitions, each description doubles as a retrieval document: include the natural-language task phrases an agent would plausibly query, as readable prose rather than keyword dumps.
    Add retrieval phrasing only where captured evidence and evals show retrieval improves (design-workflow Step 8), and charge it against the compact-definition budget — retrieval text inflates every `tools/list` byte a preloading client pays.
  - **Server-managed catalog disclosure** — expose a small initial catalog and reveal more as *declared* state changes (authorization, configuration, workspace, or external state), emitting `notifications/tools/list_changed`.
    `listChanged` is cache *invalidation*, not discovery: it tells a client to refetch, communicates no relevance, and strands tools on clients that ignore it.
    Tie every reveal to such a declared change rather than to unrelated calls, so the surface still satisfies the stability rule below; a catalog that mutates as a hidden side effect of ordinary calls violates it.
    Requires verified host refresh behavior, a stable fallback catalog for non-refreshing clients, and deterministic snapshot semantics so a change mid-pagination cannot drop or duplicate tools (see §9).
  - **Client-independent surface reduction** — shrink the catalog *every* client sees.
    Options: task-oriented consolidation (fewer, task-completing tools — see §3); a compact dispatcher tool (`{operation, arguments}` plus an on-demand `describe`/catalog operation — **not** a fat union that re-embeds every operation schema, which is consolidation at best and costs the same tokens); narrowly-scoped servers; deployment profiles; or stable authorization-scoped catalogs (§9).
    This is the only axis that helps a client that preloads and never lazy-loads.
    Trade-off: a dispatcher collapses per-operation descriptions, annotations, validation, and client approval into one tool, so enforce operation-level validation and policy server-side, and document that native annotations and approval apply to the dispatcher as a whole.

- **Make discovery selective — through the right surface.** A server with 80 tools and no way to narrow them is functionally undiscoverable.
  But native `tools/list` accepts only an opaque pagination cursor: it has no query, namespace, detail, or summary parameter.
  Provide filtering and summary/detail modes through a discovery tool, a resource catalog, configuration, or authorization-scoped catalogs, and label them as such — never as a portable `tools/list` capability.

- **Use native completion for hard-to-guess prompt and resource-template arguments.** If the server has prompt arguments or resource-template variables with large, dynamic, or enum-like value sets, advertise `server.capabilities.completions` and implement `completion/complete`.
  Use it proactively to reduce invalid IDs, paths, project keys, channel names, and URI components; keep tool-argument repair in normal tool errors because MCP completion does not complete arbitrary tool arguments.

- **Discovery obeys token-efficiency rules.** Keep each definition compact (the baseline above); where a discovery tool or resource catalog returns its own list-shaped payload, paginate it, support filtering, and return concise summaries by default with an opt-in detailed mode (see §8).

- **Design for client variance.** Some clients preload tools, paginate discovery, ignore annotations, or expose resources poorly; keep discovery usable for the least-capable realistic client.

- **Index resources; do not inline bodies.** Catalog entries carry metadata sufficient to triage; bodies are fetched on demand (see §4).

- **The discovery surface itself is contract.** Renaming a tool, changing its namespace, or removing it from a catalog is a discoverable change to clients; treat it as a versioned event (see §9).

- **Resource catalogs are part of discovery.** A catalog that omits new resources or returns inconsistent metadata silently breaks agent planning.
  Treat the catalog as authoritative.

- **Include the capability fingerprint in discovery responses where you publish one (§9).**
  Clients can short-circuit a re-walk if nothing has changed.

- **Discovery may vary by authorization context, but never by hidden side effects.** The tool and resource lists an agent sees may legitimately differ across auth scopes — an unauthorized scope simply does not see a capability.
  They MUST NOT drift as a side effect of unrelated calls within a connection: the same authorized client gets the same surface in the same order (see §9), so a cached client can trust it.
  Make differences auth-scoped, declared, and stable, not per-request surprises.

Audit prompt: On the clients this server actually targets, what must an agent load before its first useful call — and is that the smallest surface those clients allow, given that the least-capable realistic client preloads every tool definition?

---

## 3. Tools

*Worked shapes: `examples.md` §1 (namespaced tool schema), §2 (structured tool response), §2a (dispatched-but-unconfirmed mutation result), §10 (worked task: API mirroring vs. task completion), §12 (response-delivery artifact), §13 (tool result with resource link).*

- **Name with `snake_case`, prefix, verb, noun.** `slack_send_message`, not `send_message`.
  Generic verbs collide across servers in multi-server contexts.
  Omit the service prefix only when every target host — including code-execution surfaces that flatten tools into one module namespace — preserves a per-server namespace, and document that host assumption if you do.

- **Reuse verbs consistently.** `list`, `get`, `create`, `update`, `delete`, `send`, `search` should mean the same thing across tools.
  Inconsistent verbs make the agent second-guess otherwise-obvious calls.

- **Write descriptions that are narrow and unambiguous.** Cover when to use, edge cases, and an example invocation.
  Descriptions are the primary input to tool selection.

- **Separate binding constraints from background in description prose.** Each constraint is its own sentence with explicit strength — mandatory or default-with-override — phrased against observable behavior: "set `purge: true` only for messages the user explicitly asked to hard-delete", not "be careful with `purge`".
  Mechanics, rationale, and history stay out of the constraint sentences, so a reader can extract every rule without parsing narrative.
  The same rules-then-context discipline as the §2 capability summary applies here in compact form.

- **Disambiguate parameter names.** Use `user_id`, not `user`; `channel_id`, not `channel`; `started_after`, not `since`.
  Ambiguous names cause wrong-shape arguments on the first call.

- **Apply required-vs-optional discipline strictly.** Required parameters must be necessary; every optional parameter declares its omission semantics — what the server does when the field is absent — in its schema description.
  Use JSON Schema `default` only when the server actually applies that value; `default` is annotation, not behavior, and no validator injects it into the call.
  Omission need not mean a substituted value: "omit `thread_ts` to post a new top-level message" is complete omission semantics with no default at all.
  For partial-update tools, omission semantics extend to a three-way distinction defined per optional field: absent (leave unchanged) vs `null` vs empty string/list.
  If clearing a field is supported, make the clearing form explicit — a documented `null`-clears semantic or a dedicated sentinel — and test it.
  Agents commonly guess that `null` clears, so an undefined convention silently drops intended clears or silently clobbers.

- **Use strict types.** Enums where the value set is fixed; formats (`date-time`, `uri`, `email`) where the shape is conventional; `integer` vs `number` chosen deliberately.

- **Declare schema dialect where supported.** A schema without a dialect forces clients and validators to infer semantics.

- **Prefer simple, portable schema constructs.** Prefer flat closed objects, enums, and discriminated modes over deep `anyOf`/`oneOf` unions, recursive `$ref`s, `patternProperties`, and dependent constraints — clients, validators, and model planners mishandle these, producing validation drift, broken argument forms, and wrong-shape first calls.
  When a parameter's shape genuinely varies by mode, use a discriminator field with per-mode documentation, or split tools per the granularity rule — not a union.
  Use a complex construct only when every target host's handling of it is verified by captured evidence (design-workflow Step 8 `host_capture` fixture, or equivalent captured responses in a review).

- **Close object schemas.** Use `additionalProperties: false` on all object schemas unless unknown extension fields are an intentional, documented contract.

- **Tolerate stringified container arguments only as a measured compatibility shim.** When captured host evidence (a design-workflow Step 8 `host_capture` fixture, or captured tool-call arguments in a review) shows a target client serializing object or array arguments to JSON strings, coerce the string before validation: parse once, bound size and depth, and let a malformed string fail as an ordinary field-level validation error naming the parameter (§6).
  Never widen the published schema — advertising `string | object` teaches every client a false contract; the shim is server-side leniency, not contract.
  Log each coercion so the shim stays a measured, droppable workaround rather than a permanent invisible layer.

- **Publish an `outputSchema` and return `structuredContent` when targeting MCP versions that support them.** This is the normative target, not an optional nicety: when a tool declares an `outputSchema`, servers MUST return conforming `structuredContent` on success results, and clients SHOULD validate against it (the `isError: true` carve-out is the next item).
  Keep parser-compatible JSON in `content` as a fallback for older or weaker clients; support varies across MCP versions, so the fallback stays useful — but it is the fallback, not the contract.

- **Scope `outputSchema` to success results — a deliberate reading of an unsettled point.** The spec does not say whether `outputSchema` binds `isError: true` results.
  This skill takes the position that it governs success results only: an `isError: true` result carries the documented error envelope in `structuredContent` (see §6) instead of the success shape, and is not validated against `outputSchema`.
  Document the error envelope per tool — or union it into `outputSchema` — so validators never have to guess which shape applies; do not leave the two contracts in silent conflict.

- **Default to structured output.** Structured data is authoritative; text or markdown is supplemental rendering for human-facing clients.
  Token-efficiency rules for responses live in §8.

- **Advertise only response fields you populate.** Every required field and every documented always-present field appears on the wire; conditional fields document and satisfy their appearance conditions.
  An advertised field that never appears — an `outputSchema` property or an "every response carries it" claim the implementation cannot populate — is a contract violation, not schema slack.

- **Use rich content types deliberately.** Tool results may include `text`, `image`, `audio`, `resource_link`, embedded `resource`, and `structuredContent`.
  Put machine-contract fields in `structuredContent`; use `content` for human rendering, linked artifacts, and compatibility fallbacks.

- **Prefer resource links over inline bulk or binary payloads.** For large documents, generated charts, exports, or files, return a concise `structuredContent` summary plus a `resource_link` with `uri`, `name`, `description`, `mimeType`, `size` where known, and `annotations.lastModified` where useful.
  Resource links returned by tools may not appear in `resources/list`, so include enough metadata for the agent to fetch, subscribe, cite, or discard them.

- **Use content annotations to steer attention, not correctness.** `annotations.audience`, `priority`, and `lastModified` help capable clients decide what to show the user vs. model and when to re-read linked content.
  Never make safety or parsing depend on annotations being visible.

- **Declare side effects, idempotency, and rate limits as first-class contract.** Use tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) and structured response fields.

- **Set annotations honestly.** `readOnlyHint: true` on a tool that mutates is worse than no annotation, because clients will skip safety prompts.

- **Know the annotation defaults and gates.** An omitted annotation is not neutral: the spec defaults are `readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: false`, and `openWorldHint: true`, so an unannotated tool already reads as a possibly-destructive mutator.
  Declare annotations explicitly anyway so the contract is visible rather than inherited — but never report a missing `destructiveHint` as if omission declared the tool safe.
  `destructiveHint` and `idempotentHint` are meaningful only when `readOnlyHint` is `false`; omit them on read-only tools rather than asserting semantics the protocol does not assign there.
  Because omission still carries the spec defaults, annotate semantically equivalent sibling tools identically: one omitted annotation in a family whose peers declare non-default values silently asserts the *default* — different semantics — and even where the omitted value happens to match, the visible inconsistency teaches an agent to distrust the hint across the whole server.
  Where siblings genuinely differ (a parameter changes idempotency), keep the differing annotation and state the reason in that tool's description.

- **Define mutation by observable scope, not by I/O — a deliberate reading of an ambiguous hint.** The MCP spec glosses `readOnlyHint` only as "the tool does not modify its environment," which is broad enough to read either way for a local write.
  This skill takes the position that `readOnlyHint` should track whether the call changes state that outlives the response contract: shared systems, persistent records, other users' data, or persistent state in the caller's environment that other calls or tools can observe.
  Under that reading it is not about whether the tool performs any I/O at all, and a write to the caller's filesystem does not by itself count as mutation.
  A transient artifact written purely as response delivery — for example, a CSV or Parquet result file with a declared TTL, scoped to this call, and no shared visibility — is treated as part of the response, not a side effect, so the tool stays `readOnlyHint: true`.

- **Document which `readOnlyHint` reading the server uses.** The observable-scope reading is a judgment call, not settled spec: a reviewer who reads "environment" literally may disagree, so document the choice rather than asserting it, and apply one reading consistently across tools.

- **Prefer resource or resource-link delivery for large results.** Return large results as resources or resource links with TTL metadata where you can; reserve the response-delivery-artifact pattern for cases where an inline or linked resource does not fit.

- **Disclose response-delivery artifacts through the structured response and the description.** Use a structured response field (e.g., `result_artifact: {path, ttl_hours, mime_type}` — a house convention object, so its sub-fields use `snake_case`, not the native `Resource.mimeType`) and the tool description, never annotation flipping.
  The local-path artifact pattern is valid only when server and caller share a filesystem (co-located stdio); a server reachable over HTTP or by remote clients must return the result as a fetchable resource, `resource_link`, or URL instead, because a returned filesystem path is dead weight to a remote client.
  See `examples.md` §12 for a worked response-delivery artifact.

- **Annotations are hints, not security.** Declare them so agents can plan; do not rely on them for access control.
  Enforcement lives in the implementation.

- **Do not rely on annotation visibility.** Some clients do not surface annotations to the model, so annotations are advisory and cannot be the only safety prompt.

- **Give unsafe mutations an idempotency key.** For create/send/pay-style tools where a duplicate is costly, accept a client-supplied `idempotency_key`, and declare its deduplication window and scope.
  Without one, an agent that retries after a timeout cannot avoid double-posting.

- **Distinguish dispatched from applied, and define ambiguous-outcome recovery.** When a mutating tool returns before its effect is confirmed, the structured result carries the dispatch fact and a confirmation status (e.g. `command_sent: true`, `status: "applied" | "pending_verification"`), plus a `follow_up` object naming the verification surface (see `examples.md` §2a).
  `follow_up` reuses the §6 `repair` object shape exactly — `{next_step, tool, arguments, alternative}` with `alternative` optional, `tool` a registered `tools/list` name, `arguments` literally callable — the same next-step vocabulary on the success carrier, not a second convention.
  The dispatch fact must survive onto the timeout path: report a verification timeout as a dispatched-but-unconfirmed success result with a reconcile `follow_up`, never as a plain failure, or agents blind-retry non-idempotent actions.
  The same obligation covers transport drops the server never sees: the tool description and timeout-adjacent repair hints name the lookup or status surface that resolves the ambiguity, so the agent reconciles instead of blind-retrying.

- **Declare partial-success semantics for multi-item operations.** Say whether the operation is atomic or per-item.
  Per-item results carry a stable item id, outcome, and retryability, and the retry surface accepts just the failed items — never force the agent to re-run completed effects to recover the failures.

- **Protect read-modify-write with version preconditions.** Where concurrent edits are possible, accept an optional `expected_version` (or `if_match`) input, fail with a stable `conflict` error code when it mismatches, and make the repair path re-read-then-reapply.
  Without it, an agent can silently overwrite changes made by users or other agents between its read and its update.
  Where the backing store has no version counter, the token may be a content-derived hash computed over a canonicalized serialization, returned by every read surface and compared on write.
  Document the canonicalization (key and list ordering) and the collision assumptions, and keep the conflict repair path re-read-then-reapply — the re-read returns the fresh token.
  A content-derived token keeps optimistic locking stateless.

- **Mutate large documents through bounded patch operations, not round-trips.** For tools that edit large mutable documents (dashboards, configuration trees, long structured records), offer targeted server-side patch or field operations guarded by the version precondition above, so the agent need not read the full body, rewrite it in context, and send it back — two full-document round-trips per edit is a token-efficiency failure (§8) as well as a concurrency hazard.
  Offer a dry-run or diff preview where a wrong patch is costly, surface post-write validation results in the structured response, and give patch failures the same field-level repair as any other error (§6).
  Executable transform expressions are never the default edit path; their sandboxing and resource limits are implementation security, out of scope here — route them to a dedicated security review (§3 Security).

- **Prefer task-completing tools over endpoint mirrors.** Tool granularity follows user/agent tasks, not the underlying API's resource map.
  Valid split exceptions are recorded in `design-workflow.md` Step 3.

- **Hide internal step granularity.** When a task requires multiple steps internally, expose the task — not the steps — unless the steps are themselves separately useful tasks.

- **Failure paths are part of the contract.** Repair signals belong in tool result errors (see §6), not in description prose.

- **Declare every parameter the tool reads.** Hidden parameters that affect behavior belong in the agent-actionable implicit state declaration (see §1), not implicit in tool behavior.

- **Design tool surfaces to survive code-execution clients.** When an agent imports the server as a code API instead of calling tools through a chat UI (see [mcp-vs-cli.md](mcp-vs-cli.md)), tool and parameter names become identifiers in source and errors cross a language boundary.
  Keep names import-friendly (`snake_case`, no collisions when flattened into one module namespace), keep `structuredContent` the authoritative return so it maps to a native value, and choose symbolic error `code`s that translate cleanly into language-native exceptions.
  A task-completing tool composes in code far better than an endpoint chain the agent must re-orchestrate by hand.

### Anti-patterns

- **Wrapping every API endpoint as a tool.** More tools dilute discovery, increase token cost on every definition load, and make selection harder.
  A 60-tool server where every tool maps to a REST endpoint typically expresses 6–10 actual user tasks.
  Collapse endpoint chains into the task they serve.

- **Burying side effects, idempotency, or rate limits in description prose.** Agents do not reliably read prose for safety-relevant signals.
  If a tool mutates state, say so via `destructiveHint`; if it can be retried safely, say so via `idempotentHint`; if it has a per-minute call limit, surface that in the response, not the description.

- **Using `readOnlyHint` as a substitute for artifact disclosure.** Disclose transient response artifacts through the structured response and the tool description — flipping the annotation is not disclosure.
  Under this skill's observable-scope reading, the tool stays `readOnlyHint: true`, because clients use `false` to gate auto-approval and a semantically read-only call gains friction without safety.
  A server adopting the literal reading may set `false` instead — but it must document that reading and apply it consistently across tools (see the mutation-scope rule above); the anti-pattern is undocumented flipping or mixing the two readings, not the literal reading itself.

- **Mode-dependent side effects under one static annotation.** A multi-modal tool whose discriminator argument (`action`, `mode`) spans read and mutating modes cannot be honestly described by one static annotation set.
  Prefer splitting the read modes into a separate `readOnlyHint: true` tool — this is granularity pressure, the same force as the §2 dispatcher trade-off, which already requires per-operation policy server-side.
  If the tool stays combined, annotate for its worst mode, state in the description that annotations describe the whole tool rather than the selected mode, and enforce per-mode authorization server-side regardless.
  Failure shape: a manage-style tool combined a common pure-read default action with mutating modes under one honest worst-mode annotation (`destructiveHint: true`), so its most common call paid mutation-grade confirmation friction — the annotation was right; the granularity was wrong.

### Security

- **Treat external content as untrusted.** Tool output must not smuggle instructions that override the user's task or the server contract.

- **Sanitize output before returning it.** Strip or escape credentials, control sequences, and content that would corrupt the client's rendering or parser.

- **Minimize data exfiltration paths.** Open-world tools need explicit data-flow review because they can send local or retrieved data to external services.

- **Use least-privilege scopes.** Request only the scopes needed for the exposed tasks, and surface insufficient-scope repair separately from missing credentials.

- **Define confirmation boundaries.** Destructive, paid, external-send, or broad-read operations need clear preconditions before execution.

- **Keep state handles client-opaque, in one of two modes.** Either a handle is a high-entropy reference to server-side state, or — where statelessness is required, typically cursors — it is an integrity-protected token authenticated with deployment-managed key material that survives multi-process routing and restarts.
  Tokens never contain credentials and never expose internal record IDs; encrypt token payloads that carry sensitive state.
  Every continuation re-applies authorization, expiry, and budget checks — integrity protection is not authorization.

- **Keep this subsection bounded.** These are agent-facing contract rules only; full threat modeling (trust boundaries, attack trees, data-flow analysis) is out of scope here — route it to a dedicated security review.

Audit prompt: For each tool, can an agent decide to use it, call it correctly, and recover from a failure — using only the schema and structured response, never the prose?

---

## 4. Resources

*Worked shapes: `examples.md` §3 (resource index entry), §4 (resource body with chunking), §5a (resource template with completion), §5b (resource subscription).*

- **Use stable, hierarchical, predictable URI patterns.** Resource URIs are identifiers agents quote back to the server and may cache; instability breaks repeat calls.

- **URI segments use stable domain nouns.** Not internal identifiers that change between deployments.
  The URI is part of the contract.

- **Distinguish lightweight indexes from bodies.** Resource lists return summaries with metadata sufficient for the agent to decide whether to fetch the body.

- **Surface a summary before the body for large resources.** Force the agent to opt in to bulk content rather than handing it whole documents by default.

- **Make body content chunkable with stable identifiers.** Agents need to fetch chunk N+1 without re-fetching N, and to cite a chunk back to a tool.

- **Chunk identifiers are stable across reads of the same resource version.** If the resource changes, identifiers may change — but the change is observable via the resource's modification metadata.

- **Every chunk needs a callable fetch path.** Native `resources/read` takes only a URI, so give each chunk its own URI (and publish the pattern as a resource template) or provide a labeled tool fallback that accepts the chunk id.
  A chunk catalog under `_meta` is auxiliary metadata some clients never surface to the model; the in-band next-chunk pointer in the body (`next_chunk_id`/`next_chunk_uri`) and the chunk URIs are what the agent can actually act on.

- **Include the metadata fields agents use to triage.** Use native `Resource` fields: `title`, `description`, `mimeType`, `size`, and `annotations.lastModified`.
  Missing metadata pushes the agent into fetching bodies blindly; custom triage fields with no native home go under a namespaced `_meta` key, not a new top-level field.

- **Expose URI-shaped resources through resource templates.** When resources are parameterized by a URI pattern, publish `resourceTemplates` via `resources/templates/list` (with `uriTemplate`, `name`, `title`, `description`, `mimeType`) so agents can discover the shape without enumerating every instance.
  Templates are a native discovery primitive distinct from the resource list itself.

- **Pair resource templates with completion where values are dynamic.** For template variables such as `{project}`, `{channel_id}`, `{schema}`, or `{path}`, implement `completion/complete` when clients negotiate `completions`.
  This gives agents a proactive way to build valid URIs instead of learning the value space through failed reads.

- **Keep summaries short.** Resource summaries are at most three sentences, never paragraphs.
  They appear in lists of dozens; long summaries defeat the index.

- **Resource `description` prose follows the §3 constraint-separation rule.** Any binding constraint in a resource or resource-template description is its own explicit-strength sentence, kept apart from background — same discipline, compact form.

- **Support resource subscriptions for mutable resources.** If a resource can change during a long-lived agent session and stale reads matter, advertise `resources.subscribe`, accept `resources/subscribe`, and emit `notifications/resources/updated` for subscribed URIs.
  Use `notifications/resources/list_changed` for catalog membership changes; use per-resource updates for body changes.

- **Resources share the failure-recovery contract.** Missing-credential, not-found, gone, and rate-limit signals must be machine-readable (see §6).

- **Resource failures use JSON-RPC errors.** Put the repair contract in structured `error.data` using the same unified error envelope as tool results (§6) — `machine_code`, `human_message`, `details`, `temporary`, `retry_after_ms`, and a single `repair` object — not a separate vocabulary.

- **Some clients do not expose resources well.** If discoverability matters for a read-oriented capability, provide a tool fallback that reaches the same indexed content.
  The fallback must be self-sufficient from `tools/list` alone: callable without values learnable only from resources — for example, omitting an optional selector returns the index — and every required, non-obvious constrained value space has a tool-reachable lookup or enumeration.

- **Pagination, filtering, and truncation rules live in §8.** Reference them; do not duplicate.

- **A write-surface "resource" is a tool.** Resources are read-oriented; if mutation is the point, model it as a tool with the appropriate annotations (see §3).

Audit prompt: Can an agent decide whether to fetch a resource — and which chunk of it — without first reading the full body?

---

## 5. Prompts

- **State when to use the prompt explicitly.** The agent needs to recognize the matching task, not infer it from the title.

- **List prerequisites.** Which tools, which resources, and which permission or context assumptions the prompt relies on.
  Missing prerequisites surface as confusing failures partway through execution.

- **Offer completion for prompt arguments with dynamic value sets.** If a prompt argument asks for a project, workspace, repository, environment, channel, or similar value the agent should not guess, support `completion/complete` when `server.capabilities.completions` is negotiated.

- **Reference expected follow-on tools and resources by name.** A prompt that doesn't tell the agent what to invoke next is half a scaffold.

- **Keep prompts as orchestration scaffolding, not contract.** Essential behavior — argument shapes, side effects, error shapes — belongs in tool and resource schemas.
  See `examples.md` §5 for a prompt scaffold that references tools and resources without redefining them.

- **Prompts may reference; they may not redefine.** A prompt may name a tool by canonical name, but it must not redefine the tool's contract or override its arguments.
  The schema is authoritative.

- **Prompts are optional.** A server with no prompts is fine; a server whose prompts are load-bearing for correctness is broken.

### Anti-patterns

- **Prompts as contract container.** Encoding required behavior in prompt text rather than schema means the agent must read prose to call correctly, and any client that bypasses prompts (most code-execution clients do) will call incorrectly.
  Orchestration scaffolding is not a substitute for schema.
  If behavior is essential, encode it in tool/resource schemas.

Audit prompt: If every prompt on this server were removed, would any tool or resource become incorrect or unsafe to call?

---

## 6. Failure Recovery

*(Cross-cutting; co-equal with first-call success.)*

- **Use stable, machine-readable error codes.** Codes are symbolic strings (e.g., `not_found`, `rate_limited`, `invalid_field`), not numeric exit codes.
  The symbolic code is the authoritative branch key for agents.

- **Document every error code per tool — without bloating every definition.** An undocumented code is an undiscoverable code; agents cannot branch on it reliably.
  But a full error catalog embedded in every `tools/list` entry inflates the definition each preloading client pays for (see §2), so choose placement by cost: keep only selection- and repair-critical codes inline in the definition, and serve the complete per-tool catalog through an on-demand surface (`describe_tool`, a resource, or the capability summary) that repair hints can reference.

- **Code semantic changes are breaking.** Introducing a new additive code is safe; changing or renaming an existing code's meaning is a breaking change.
  Where a fingerprint is published, both are recorded in it (see §9).

- **Provide field-level validation feedback.** Which field, why it's invalid, and which values are allowed. "Invalid input" without a field name forces the agent to guess.

- **Include the offending value when safe.** The agent's repair attempt depends on knowing what it sent.
  Redact when sensitive; never omit silently.

- **Signal retryability and rate limits explicitly.** Use `retry_after_ms`, `temporary: true|false`, and `rate_limit_remaining` where applicable.
  Agents need to distinguish "wait and retry" from "stop and reconsider."
  Define the invariants so servers don't emit incompatible combinations: `temporary: true` means *the same operation, unchanged, may succeed later* (transient condition); `temporary: false` means it will not — repair or escalate instead. `retry_after_ms` is a non-negative integer only when a delay is known, and `null` otherwise; it must be `null` when `temporary: false`.
  Retryability is independent of repairability: a `temporary: false` error can still carry a `repair` (a *different* corrective call), and a `temporary: true` one may carry none.

- **Include "what to do next" repair hints.** The corrective call, parameter, or filter.
  The first repair attempt is as important as the first call.

- **Repair preserves caller intent.** When corrected arguments are already known, `repair.tool` names the failing tool and `repair.arguments` carries every still-valid, non-sensitive original argument plus the minimal correction; route to a different tool only when the correction must first be discovered or performed there.
  Forcing the agent to re-orchestrate a task that one corrected argument would have completed wastes the repair.

- **Repair hints reference real, callable surfaces.** Tool names, parameter names, valid enum values — not free-form prose.
  `repair.arguments` holds literally callable values, never placeholders such as `"<one of the listed slugs>"`; when a required value must be discovered first, make the primary repair call the lookup or enumeration tool that returns it.

- **Surface capability-missing failures explicitly.** If a path needs an optional feature the client did not negotiate, return a structured error such as `capability_not_negotiated` with `required_capability`, the affected operation, and the fallback tool/resource/prompt when one exists.

- **Use elicitation only behind negotiated support and clear fallback.** When a server needs missing user input, user confirmation, or sensitive external interaction during a call, prefer MCP elicitation for clients that advertise it.
  URL-mode elicitation is the safe path for passwords, API keys, payment credentials, OAuth, or other sensitive exchanges.
  For clients without elicitation, return an actionable error or task `input_required` status that names the next callable surface or external action.

- **Tool semantic errors return as tool result errors.** Set `isError: true` on the tool result.
  JSON-RPC errors are reserved for transport, protocol, and non-tool RPC methods (such as `resources/read` and `resources/list`); raising a JSON-RPC error from `tools/call` strips the structured-response contract from the failure path.
  See `examples.md` §6 for an actionable tool-result error payload.

- **Resource semantic errors return as JSON-RPC errors.** `resources/read` and `resources/list` are non-tool RPC methods, so failures surface through the JSON-RPC envelope; carry the same unified error envelope (below) in structured `error.data`, renaming only `code`→`machine_code` and `message`→`human_message`.

- **Name the error carrier in the capability summary.** State where the envelope travels — `structuredContent` on the tool result, JSON-RPC `error.data`, and any disclosed degraded mode (below) — so agents know where to parse a failure before the first one occurs.

- **Errors include correlation context.** A `request_id`, the offending parameter, and (where applicable) the resource URI.
  Agents need to correlate failures with the requests that caused them.

### One error envelope, two carriers

The failure-recovery contract is **one envelope** with identical field semantics regardless of where it surfaces.
Tool-result errors carry it in `structuredContent` (alongside `isError: true`); resource and other non-tool RPC failures carry it in JSON-RPC `error.data`.
The *only* permitted divergence is renaming `code`/`message` on the JSON-RPC side, because the native `code`/`message` already occupy those keys — every other field is the same name, shape, and cardinality on both surfaces.
Do not invent surface-specific aliases (e.g. `repair_hints`) or surface-specific flags (e.g. `recoverable`).

| Field | Tool result (`structuredContent`) | JSON-RPC (`error.data`) | Required? | Notes |
| --- | --- | --- | --- | --- |
| symbolic code | `code` | `machine_code` | yes | Stable symbolic string; the authoritative branch key. Renamed on JSON-RPC only to avoid shadowing native `code`. |
| human text | `message` | `human_message` | yes | Short human-readable summary. Renamed on JSON-RPC only to avoid shadowing native `message`. |
| field detail | `details` | `details` | where applicable | `{field, value, reason}` for one parameter, `{fields, reason}` for a cross-parameter constraint (see below); redact `value` when sensitive, never omit silently. |
| transient? | `temporary` | `temporary` | yes | See retryability invariants above. |
| retry delay | `retry_after_ms` | `retry_after_ms` | yes (nullable) | Always present alongside `temporary`; a non-negative integer when a delay is known, else `null` (and always `null` when `temporary: false`). Always emit the key so agents distinguish `null` from a number without special-casing a missing field. |
| rate budget | `rate_limit_remaining` | `rate_limit_remaining` | on rate-limit errors | Non-negative integer of remaining calls in the current window, where the surface exposes one. |
| repair | `repair` | `repair` | where a corrective path exists | A single object `{next_step, tool, arguments, alternative}` — see below. Omit the field entirely when no repair exists (never emit `null` or an empty array). |
| correlation | `request_id`, `resource_uri`, `fingerprint` | `request_id`, `resource_uri`, `fingerprint` | where applicable | `resource_uri` where the failure is tied to a resource. |

- **`details.field` names a published parameter.** For a tool argument-validation failure, `field` is a single property path from the failing tool's published `inputSchema` (dotted for nested properties); for a constraint spanning several parameters, use `fields` — a non-empty array of unique published property paths — and emit exactly one of `field` or `fields`, never both.
  For a non-tool RPC failure, `field` names the offending request parameter of that method (`uri` for `resources/read`), under the same one-of rule.
  Translate internal names at the MCP boundary; never expose internal or synthetic names the surface does not accept (an internal `office_id` for a tool that takes `office`, a synthetic `bbox` for tools that take `south`/`west`/`north`/`east`).
  Include `value` only when safe and meaningful; error-code-specific detail keys (such as `required_scopes` on an `insufficient_scope` error) are permitted alongside `reason` when documented with the code.

- **Presence convention.** Fields marked *yes* are always emitted on both surfaces — `retry_after_ms` is the one nullable required field (it is bound to the always-present `temporary`, and `null` meaningfully signals "no known delay").
  Every other field is omitted entirely when it does not apply; do not send a placeholder `null` or empty array for an absent optional field.

- **`repair` is one object, not an array, on both surfaces.** Its shape is `{next_step, tool, arguments, alternative}`: `next_step` a stable symbolic label, `tool` and `arguments` the single primary corrective call (a real, callable surface), and `alternative` an *optional human-readable* fallback sentence for when the primary call doesn't fit.
  A single deterministic next action beats a ranked list the agent must choose from; if no corrective call exists, omit `repair` entirely rather than emitting `null` or an empty array. `alternative` is the one prose field in the envelope — it is fallback guidance for a human/agent to interpret, not a second machine-actionable hint (contrast the "real, callable surfaces" rule, which governs `tool`/`arguments`).
  The §3 dispatched-vs-applied rule reuses this exact `{next_step, tool, arguments, alternative}` object as a success-side `follow_up`; only the carrying field name differs, so there is one next-step vocabulary, not two.

- **A degraded carrier is disclosed, never silent.** The two carriers above are the only contract carriers.
  As a disclosed degraded mode only — when a framework cannot place `structuredContent` on an `isError: true` result — `content[0].text` MAY carry the serialized envelope JSON: the capability summary names the limitation and its trigger, the envelope shape stays identical, and the mirror is removed once the framework supports the native carrier.
  An undisclosed text-only envelope is a third contract, not a fallback.

- **There is no `recoverable` flag.** Whether the *same* resource or operation can succeed again is already carried by the symbolic `code`/`machine_code` (e.g. `resource_gone`) plus `temporary`; whether recovery is possible by *another* path is carried by the presence of a `repair`.
  A separate `recoverable` boolean is redundant and was prone to contradicting an accompanying repair.

Audit prompt: For each failure mode, does the agent receive enough structured signal to either retry, repair, or escalate — without parsing the message field?
And does the same failure carry the identical envelope whether it surfaces as a tool-result or a JSON-RPC error?

---

## 7. Long-Running Operations

*(Cross-cutting; rules apply when an operation may outlive a normal request/response turn.)*

- **Choose the execution mode deliberately.** Use blocking `tools/call` for short operations, progress notifications for bounded multi-step work, and task-augmented requests when clients need later status or result recovery.
  A blocking call that returns dispatched-but-unconfirmed follows the §3 dispatched-vs-applied rule; escalate to progress or tasks only when confirmation itself is long-running.

- **Declare long-running behavior in the tool contract.** Tool descriptions or schemas include expected duration, timeout behavior, and whether partial progress is observable.

- **Support `progressToken` where progress exists.** Send rate-limited `notifications/progress` updates that identify current phase, completed work, and remaining work when knowable.

- **Support cancellation where work can continue after the call starts.** Honor `notifications/cancelled` for request-bound work and `tasks/cancel` for task-capable tools.

- **Declare task support at both levels.** Native task augmentation requires the server to advertise the `tasks` capability (`server.capabilities.tasks.requests.tools.call`) AND the tool to declare `execution.taskSupport` as `optional`, `required`, or `forbidden`.
  The per-tool flag alone is insufficient — without the server capability, clients must not attempt task augmentation.

- **Use native task operations for status and result retrieval.** Poll with `tasks/get` (respecting the returned `pollInterval`), retrieve the result with `tasks/result`, and cancel with `tasks/cancel`.
  `tasks/result` blocks until the task reaches a terminal status and its response is always the underlying result, never an intermediate payload; an agent may keep polling `tasks/get` in parallel while it waits.
  Task objects use the spec's fields and casing — `taskId`, `status`, optional `statusMessage`, `createdAt`, `lastUpdatedAt`, `ttl`, `pollInterval` — and `status` is one of `working`, `input_required`, `completed`, `failed`, `cancelled`.
  A `CreateTaskResult` may carry `io.modelcontextprotocol/model-immediate-response` in `_meta` — a string the host can hand the model immediately while the task runs; provide it so task-accepting calls do not go silent.
  Carry `io.modelcontextprotocol/related-task` in `_meta` on task-associated messages whose payload does not already name the task: `tasks/result` responses MUST include it, while `tasks/get`, `tasks/list`, and `tasks/cancel` SHOULD NOT, because the `taskId` already travels in the message itself.

- **Treat `notifications/tasks/status` as optional push, not contract.** Receivers MAY emit it on status changes with the full task state; requestors MUST NOT rely on receiving it.
  Keep polling `tasks/get` as the authoritative status path, and use the notification only to poll sooner.

- **Define `input_required` recovery.** The native path is fixed: when polling shows `input_required`, the requestor SHOULD preemptively call `tasks/result` and hold it open.
  The pending input request is not the `tasks/result` response — it arrives as a separate receiver-to-requestor request (an `elicitation/create` carrying the related-task `_meta`) while the call is pending; once input arrives the task returns to `working`, and the same held call completes with the terminal result (re-call only if the call itself fails).
  Say which input mechanism rides that channel — native elicitation (`elicitation/create`), URL-mode elicitation, or a domain-specific fallback for clients without the negotiated capability.
  The task status alone is not enough; the agent needs the next operation and the capabilities required to perform it.

- **Task handles are state handles.** Apply the §1 state-handle discipline to `taskId`s: high-entropy opaque ids, authorization checked on every `tasks/get`, `tasks/result`, and `tasks/cancel`, bounded retention via `ttl` — and do not advertise `tasks/list` where requestor isolation cannot be enforced, because it enumerates other requestors' handles.

- **Tasks are experimental; degrade deliberately.** Tasks were introduced in MCP 2025-11-25 and are still experimental, so a server MAY also expose a domain-specific status/cancel tool as a labeled fallback for clients without task support — but it should mirror the native signals (current status, when to poll again, result location, expiry), not replace `tasks/*`.
  *Forward-compat:* the 2026-07-28 RC is expected to move tasks out of core into a negotiated extension (see `SKILL.md` Spec Baseline); this labeled fallback is the hedge that survives that move — keep status/result/cancel expressible as ordinary tools.

Audit prompt: Can an agent monitor, cancel, and recover a long-running operation without guessing at server state?

---

## 8. Token Efficiency

*(Cross-cutting; rules apply to both tools and resources.)*

- **Default to a concise response.** Offer richer variants via an explicit detail toggle (e.g., `detail: "summary" | "full"`), not a free `response_format` parameter.
  See `examples.md` §2 for the concise-vs-detailed response pattern.

- **Detail is orthogonal to format.** Changing detail level changes the density of fields included, not the schema's shape.
  The same parser handles both modes.
  Make the concise fields a strict subset of the detailed fields — never rename a field between modes (a `preview` that becomes `text` in detail mode is two contracts, not one).

- **Detail toggles change field density, never row count.** A result that scales with a requested window or range needs its own default bound, independent of the detail level: a server-side cap with `truncated: true` and a hint naming a callable narrowing or aggregation parameter, or an aggregate default whose explicit raw mode is itself bounded or paginated.

- **Use cursor-based pagination by default.** Offset-based pagination is acceptable only when ordering is stable and the result set is small enough that pages don't shift between calls.

- **Declare cursor lifetime and expiry recovery.** Cursors are state handles (§1): declare how long they stay valid, return a stable `cursor_expired` error with restart guidance when one lapses, and say whether a paginated walk sees a consistent snapshot or best-effort consistency — silent skips and duplicates mid-walk break agent planning.
  A cursor that encodes state rather than referencing it follows the §3 two-mode handle rule: integrity-protected, with authorization, expiry, and budget checks re-applied on every continuation.

- **Native list methods use the protocol pagination shape, not a house convention.** `tools/list`, `resources/list`, `resources/templates/list`, and `prompts/list` accept an optional opaque `cursor` request param and return an optional `nextCursor` in the result; **absence of `nextCursor` signals completion**.
  There is no native `has_more`, `next_cursor`, `estimated_total`, or `limit` on these methods, and page size is server-selected — do not rename `nextCursor` to snake_case or bolt a house convention onto a native list.
  See [native-wire-shapes.md](native-wire-shapes.md).

- **A tool's own result payload MAY carry a documented house pagination convention.** When a `tools/call` result paginates domain data (not a native list method), `has_more` is acceptable: if `has_more` is true, include a navigation token (`next_cursor`) and, where available, `estimated_total`.
  These are declared domain output and belong in `structuredContent` under the tool's `outputSchema` so the agent can observe them — not under `_meta`, which clients may not surface to the model.
  Label them as convention, never as protocol fields.

- **Provide filters that meaningfully reduce response size.** `since=`, `query=`, `category=`, `field=`.
  Filters that don't change wire size are noise.

- **Truncate explicitly with a repair hint.** `"truncated": true, "truncation_hint": "hit cap of 200; narrow with since= or query="`.
  Silent truncation breaks agent planning.
  Truncation bounds the result set; pagination continues it: `truncated` means items beyond a cap will never be returned by paging, so it is not a synonym for `has_more` — a response may legitimately carry both (more pages exist, and the set they page through is capped).

- **Choose identifiers by role.** Domain IDs with natural meaning can stay readable; state handles use opaque stable IDs with labels or summaries; security-sensitive references are opaque and never leak structure.

- **Support per-capability detail levels.** Progressive disclosure applies to both definitions (in discovery, see §2) and responses.
  The agent should be able to ask for "summary" before "full" at every level.

- **Strip null and default-valued fields from concise responses.** Where they add no information, they cost tokens and clutter parsing; detail mode may include them for completeness.
  Exception: fields a contract marks always-present — such as the §6 error envelope's `retry_after_ms` — are emitted even when `null`.

- **Use locale-independent wire values.** Timestamps are RFC3339 UTC, currency uses ISO-4217 plus minor units, sort keys are stable, and display localization stays out of machine fields.

### Anti-patterns

- **Free `response_format` toggles** (markdown vs. json vs. xml as parallel contracts on the same tool).
  Format proliferation creates ambiguous contracts: which format is authoritative?
  Which one carries error signals?
  Which gets versioned when the schema changes?
  Prefer one structured default with optional supplemental text or markdown rendering.

- **Fixed prose attached to every response.** Never attach a constant explanatory block (usage notes, timezone conventions, format explanations) to every response envelope; state invariants once, in the capability summary or an `outputSchema` field description.
  Per-call metadata is compact, machine-readable, and present only when it varies per response or changes this response's interpretation.
  This does not touch contract-mandated always-present fields (such as the §6 envelope's `retry_after_ms`) — the anti-pattern is repeated prose, not required machine fields.
  Failure shape: a server wrapped every result in `{data, metadata}` with ~250 characters of identical prose, paid on every call of every wrapped tool.

Audit prompt: Could an agent complete a typical task on this server in a single context window, including discovery, calls, and one round of repair?

---

## 9. Versioning and Compatibility

- **Publish a capability fingerprint when a target client caches or pins the server surface.**
  A versioned identity for the server's surface lets such a client detect drift cheaply, without re-walking discovery.
  It is a house convention whose value tracks the consumer: long-lived clients, caching clients, and code-execution clients that pin against the surface justify it; a server whose target clients always rediscover and never compare surface identity may omit it.
  The native obligations in this section — list-changed notifications, deterministic ordering, discoverable deprecation — are mandatory either way, and the remaining fingerprint rules below bind any server that does publish one.
  See `examples.md` §9 for fingerprint evolution across deprecation and removal.

- **Advertise and emit protocol-native list-changed notifications.** Declare the `listChanged` capability where supported, and emit `notifications/tools/list_changed`, `notifications/resources/list_changed`, and `notifications/prompts/list_changed` when the corresponding list changes.

- **Distinguish list changes from resource updates.** `listChanged` means the catalog changed; `notifications/resources/updated` means a subscribed resource's contents changed.
  Emit both only when both facts are true.

- **Keep list ordering deterministic.** `tools/list` and resource catalogs use stable ordering so clients can diff and cache predictably.

- **Treat fingerprints as additive signals.** A capability fingerprint helps clients short-circuit discovery, but it does not replace native change notifications or stable list ordering.

- **The fingerprint covers the full agent-visible surface.** Tool definitions, resource catalogs, resource templates, prompt scaffolds, completion support, subscription behavior, negotiated-capability expectations, error codes, and the server capability summary.
  Anything an agent can plan against is part of the fingerprint input — tool descriptions included, because they are the primary input to tool selection (§3).
  Where a fingerprint is published, disclose its coverage in documented agent-readable metadata such as a `covers` field (see `examples.md` §9); the disclosure does not permit excluding any agent-visible surface.
  A separately named schema-only hash, if published, is per-capability diagnostic metadata; it must not be used to decide that discovery can be skipped, and it never narrows or replaces the fingerprint.

- **Define deprecation semantics.** How a tool, resource, or prompt is marked deprecated, how long it remains available, and what replaces it.
  Deprecation is a contract, not a sticky note.

- **Deprecated capabilities remain discoverable.** They continue to appear in discovery (see §2) until removal, with a deprecation marker and a pointer to the replacement.
  Silently dropping them breaks cached clients.

- **Adding optional fields is safe.** Removing or renaming fields, codes, or tools is a breaking change — bump the fingerprint where you publish one.
  Document the migration in the deprecation marker.

- **Treat tool rename as remove-plus-add.** Renaming a tool is a discovery-surface change (see §2) — clients that cached the old surface will break silently otherwise.
  Keep the old name with a deprecation pointer for the documented window.
  A rename sweeps every agent-visible reference atomically with the alias window: repair hints (`repair.tool`), server `instructions`, the capability summary, prompts, and other tools' descriptions.

- **Declare stability tiers if used.** `stable`, `preview`, `experimental`.
  Mixing tiers without labels makes every capability look stable, which is worse than labeling some as risky.

- **Stability tier is discovery metadata.** Each capability's tier is part of its discovery record so agents can filter by tier (see §2).

- **Error codes are part of the versioned surface (see §6).** Changing a code's meaning is a breaking change; introducing a new code is additive but still recorded in the fingerprint where one is published.

- **The fingerprint format itself is stable.** Changing how the fingerprint is computed (hashing algorithm, included fields) is a breaking change for any client caching by fingerprint.

Audit prompt: If a client cached this server's surface yesterday, can it tell — from the fingerprint alone — whether anything it depends on changed?
