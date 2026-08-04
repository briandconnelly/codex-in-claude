# MCP Server Contract Checklist

This is the normative standard for the skill, used by both `design-workflow.md` and `review-workflow.md`.
The section order serves audit walks: discovery first (what an agent sees first), then the primitives that get invoked, then the cross-cutting concerns that span all primitives, then versioning.
Walk it top to bottom when designing or reviewing a server.

**Rule IDs.** Every rule carries a stable id in the form `` `[section.slug]` `` — `[3.naming]`, `[6.repair-object]`.
The slug names what the rule is *about*, not how it is currently worded, so rewording a rule keeps its id and every citation of it stays valid.
Cite rules by id from anywhere in this skill; a citation that no longer resolves means the rule was removed or renamed, which is a change you want to see rather than a paraphrase that silently drifts.
Citation resolution only protects rules something else references, so `tests/rule-ids.txt` holds the committed id set and a deletion must drop its id there in the same commit — otherwise removing an uncited rule would pass unnoticed.
`tests/check_rule_ids.py` enforces the canonical rule form, id grammar, uniqueness, section agreement, citation resolution, and manifest agreement.
This file is the single home for these rules — other files point at ids and do not restate the rule text.

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

- `[1.name]` **Name the server distinctively.** Use a descriptive, agent-facing name with no version numbers, not a host-language convention.
  The name shows up in tool selection across multi-server contexts and is part of the discovery surface.

- `[1.name-generic]` **Avoid generic service names.** Names like `api`, `data`, or `tools` collide silently in multiplexed clients.
  Pick a name a human could disambiguate at a glance.

- `[1.transport]` **Choose transport explicitly.** Use `stdio` for local single-client; use streamable HTTP for shared or remote.
  Document the choice in the capability summary.
  Streamable HTTP has three required headers with distinct applicability: `MCP-Protocol-Version` on every POST (and it MUST match the body's `_meta` `io.modelcontextprotocol/protocolVersion`), `Mcp-Method` on every request, and `Mcp-Name` only on `tools/call`, `resources/read`, and `prompts/get` (the tasks extension adds task methods, where it carries the `taskId`); a header/body mismatch is `HeaderMismatch` (`-32020`).
  A tool may pass custom headers via the `x-mcp-header` parameter convention — design for gateways that route, authorize, and meter on these headers without parsing bodies.
  See `native-wire-shapes.md` for the header shapes.

- `[1.stdout]` **`stdio` servers MUST NOT log to stdout.** Stdout is the JSON-RPC channel; mixing log output corrupts the protocol stream.
  Send logs to stderr or a file.

- `[1.auth-model]` **Declare the auth model when it affects agent behavior.** State required scopes and permission boundaries that change capability availability, result shape, or repair.
  Keep credential setup mechanics out of the first-read summary unless the agent can act on them.

- `[1.cred-modes]` **Distinguish credential failure modes.** "Missing credential," "wrong credential," and "insufficient scope" are three different repair paths.
  Collapsing them forces the agent to guess.

- `[1.implicit-state]` **Declare agent-actionable implicit state.** List workspace/project context, default resources, caches, session data, or configuration only when it affects tool choice, results, permissions, or repair.
  Hidden deployment details belong in operator docs, not the first-read surface.

- `[1.state-handles]` **Declare state-handle discipline.** Handles for jobs, cursors, sessions, or server-side state are opaque IDs with readable labels where useful, declared lifetime, expiry behavior, auth checked on every use, and bounded retention.
  Opaque means client-opaque; the §3 security rule defines the two permitted handle modes (server-side reference or integrity-protected stateless token).
  This is now the protocol's own position: the stateless core (SEP-2567) removes sessions entirely and directs servers to carry cross-call state as explicit, server-minted identifiers passed as ordinary arguments — the model can see the handle and thread it between tools.

- `[1.observability]` **Surface observability in responses, not dashboards.** Rate limits, timeouts, retry hints, deprecation notices, and the capability fingerprint (where published, §9) belong in the response payload an agent reads.
  Operator dashboards are out of scope here.

- `[1.metadata-contract]` **Treat server metadata as contract.** Name, version, fingerprint (where published, §9), and summary are part of the discovery surface.
  Changes to them are discoverable changes (see §9).
  See `examples.md` §7 for a capability summary that carries server identity, negative scope, and actionable prerequisites.

- `[1.display-metadata]` **Declare display metadata where humans choose capabilities.** Use native `title` and `icons` on tools, resources, resource templates, prompts, and implementation info when a client has a human-facing picker.
  These fields are not a substitute for precise names and descriptions, but they reduce wrong selection in mixed human/agent workflows.

- `[1.negotiated-caps]` **Record negotiated capabilities as part of the contract.** There is no initialization handshake: every request carries the client's protocol version and capabilities in `_meta` (`io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities`, both required), and the server advertises its own capabilities via the mandatory `server/discover` method; optional features are usable only when the relevant capability travels on the request or appears in discovery.
  A design or audit must say which capability paths it depends on — for example the server's `completions` or `resources.subscribe` capability, the client's `elicitation` modes (`form`, `url`), or an extension entry such as `io.modelcontextprotocol/tasks` in `clientCapabilities.extensions` — and what fallback weaker clients receive.
  A server MUST NOT rely on a capability the request did not declare; the native failure is `MissingRequiredClientCapability` (`-32021`) with `data.requiredCapabilities` (see `[6.capability-missing]`).

- `[1.spec-revision]` **Declare the target protocol revision, and judge the contract against it.** State in the capability summary which revision(s) and which extensions the server targets — modern (2026-07-28), legacy (2025-11-25), or dual-era — naming extensions individually (e.g. `io.modelcontextprotocol/tasks`), because they version independently of the core revision.
  Revision-independent rules — naming, descriptions, granularity, annotations, failure envelopes, token efficiency, security — bind regardless of the declared target; only revision-sensitive wire mechanics (the negotiation carrier, subscription delivery, the task lifecycle, MRTR/elicitation delivery, cache hints, numeric codes) are read through the informative [mcp-2025-11-25-compat.md](mcp-2025-11-25-compat.md) matrix for a declared-legacy target, so a legacy wire shape on a declared-legacy surface is baseline conformance, not a finding.
  What such a server can still earn here: an undeclared revision target, and — for each deprecated feature it uses — the lack of a migration plan inside that feature's deprecation window.

- `[1.roots]` **Express workspace scope as ordinary tool arguments; treat roots as a deprecated compatibility path.** Roots is deprecated in 2026-07-28 (twelve-month window): new designs pass directories and files via tool parameters, resource URIs, or server configuration.
  Where a target client still declares the `roots` capability, obtain roots via MRTR (`ListRootsRequest` inside `InputRequiredResult.inputRequests` — `notifications/roots/list_changed` no longer exists), stay within them unless the tool contract explicitly says otherwise, and document the migration.
  Roots guide server behavior and reduce path ambiguity; they are not access control, so still enforce filesystem permissions independently.

- `[1.auth-repair]` **Expose auth mechanics that affect repair.** For HTTP authorization, document the canonical server URI and resource indicator used for token audience binding, never pass through tokens issued for a different resource, and surface incremental or step-up scope challenges as structured repair (`required_scopes`, `resource`, `authorization_url` or elicitation URL where appropriate).
  For stdio, document where credentials come from only when the agent can act on it.
  For client registration, Dynamic Client Registration is formally deprecated in favor of Client ID Metadata Documents (twelve-month window); the client priority order is pre-registered credentials, then CIMD, then DCR, then prompting the user.
  Target CIMD and keep DCR only as the backward-compatibility fallback for authorization servers that do not advertise `client_id_metadata_document_supported`.

Audit prompt: Can an agent learn what this server does, what it doesn't, and which prerequisites affect use, in a single read?

---

## 2. Discovery Primitives

*Worked shapes: `examples.md` §7 (server capability summary), §8 (`search_tools` response shape).*

- `[2.summary]` **Provide a server capability summary.** A concise overview of what the server does, what it does not do, and any prerequisites that affect whether or how an agent should use it.
  Expose it via a resource, discovery tool, or instructions field, whichever the client honors.

- `[2.discover-first]` **Treat `server/discover` as the discovery surface's native first read.** Its mandate and required content are `[1.negotiated-caps]`'s concern; this rule owns the discovery consequence: the identity, supported versions, and capability advertisement it returns are what a modern client can read before any house surface, so the capability summary, the catalogs, and any fingerprint claim must agree with what it advertises.
  The optional `instructions` field rides this result under 2026-07-28 — it is no longer carried by an `initialize` response — and remains advisory wherever it travels (`[2.instructions-advisory]`).
  Say in the capability summary how an agent gets from `server/discover` to the house surfaces, because a capability advertisement does not carry the task-level guidance `[2.summary]` requires.

- `[2.instructions-advisory]` **Treat server `instructions` as advisory, never as the sole carrier.** Some clients never surface the `instructions` field to the model, so behavior that exists only there is invisible to those agents.
  Essential selection, prerequisite, safety, and repair behavior must also reach the agent through a surface it is guaranteed to see — the tool and resource schemas, or a discovery tool or resource the client does honor.
  Using `instructions` is fine; relying on it alone is not.

- `[2.negative-scope]` **State negative scope explicitly.** What the server does NOT do is as important as what it does.
  Negative scope prevents wasted exploration and wrong-tool selection.

- `[2.rules-then-context]` **Write the capability summary and `instructions` prose as rules-then-context.** Lead with what the server does and does not do.
  Then state each binding rule — prerequisite, ordering requirement, safety constraint — as its own imperative sentence or list item whose strength is explicit: mandatory (e.g. external sends require an approved `sender_id`) or a default with its override condition (e.g. prefer `detail: "summary"` unless the task needs full field density).
  A hedge that leaves a statement's strength unclear ("generally", "try to", "be careful") is not a rule; replace it with the observable condition it stands for, or drop it.
  Background — implementation history, rationale, folklore — comes after the rules or not at all, never interleaved with them.

- `[2.compact-baseline]` **Compact definitions are the universal baseline.** Every entry `tools/list` returns is a complete `Tool` record — the protocol has no summary-only or filtered mode — so the one discovery cost you can lower regardless of client is the serialized size of each definition.
  Use a tight `inputSchema`, no redundant prose, and descriptions precise enough to select and repair but no longer.
  Measure serialized tokens, not tool count — and do not compress away the selection or safety information an agent needs.
  Measure the serialized wire response — `tools/list` as a client receives it — not your source models: generated output schemas and per-entry `$defs` expansion often dominate the bytes.
  Whether a client fetches every page and exposes all of it to the model is client-dependent; compact definitions pay off either way, which is what makes them the baseline.

- `[2.pagination]` **Paginate large or unbounded catalogs, but treat it as scalability, not token savings.** Native `tools/list` cursor pagination caps peak response size, improves time-to-first-page, and lets a client stop early.
  A client that walks every page still pays for every definition plus cursor overhead, so pagination lowers token cost only when a client intentionally declines to fetch or expose later pages.
  Keep `nextCursor` semantics native (omission = done, see §8) and ordering deterministic so a catalog change mid-walk cannot strand or duplicate tools (see §9).

- `[2.progressive-disclosure]` **Progressive disclosure of tool definitions is a client-dependent optimization, not a universal guarantee.** The least-capable realistic client preloads the whole catalog and exposes it to the model, and adding discovery tools cannot shrink that for it.
  Design for that client, then choose a mechanism by which cost axis you can actually move on the clients you target:
  - `[2.pd-host]` **Host/client-managed context disclosure** — `search_tools` / `describe_tool`, or a resource catalog of operation metadata.
    The agent loads summaries, then pulls the few full definitions it needs.
    This lowers *model-visible context* only when the host withholds native definitions and injects selected schemas on demand (or routes execution through a stable generic call tool).
    On a host that still preloads `tools/list`, these are extra tools and round trips with no disclosure benefit, so document the host integration it assumes.
    On hosts that retrieve tools by lexical search over names and descriptions before the model sees any definitions, each description doubles as a retrieval document: include the natural-language task phrases an agent would plausibly query, as readable prose rather than keyword dumps.
    Add retrieval phrasing only where captured evidence and evals show retrieval improves (design-workflow Step 8), and charge it against the compact-definition budget — retrieval text inflates every `tools/list` byte a preloading client pays.
  - `[2.pd-server]` **Server-managed catalog disclosure** — expose a small initial catalog and reveal more as *declared* state changes (authorization, configuration, workspace, or external state), emitting `notifications/tools/list_changed` to clients that opted in via `subscriptions/listen` and bounding staleness for everyone else with an honest `ttlMs` (see `[8.cacheable-results]`).
    `listChanged` is cache *invalidation*, not discovery: it tells a client to refetch, communicates no relevance, and reaches only opted-in listeners — a client that never opens a listen stream learns of the change no sooner than its `ttlMs` expiry.
    Tie every reveal to such a declared change rather than to unrelated calls, so the surface still satisfies the stability rule below; a catalog that mutates as a hidden side effect of ordinary calls violates it.
    Requires verified host refresh behavior, a stable fallback catalog for non-refreshing clients, and deterministic snapshot semantics so a change mid-pagination cannot drop or duplicate tools (see §9).
  - `[2.pd-reduction]` **Client-independent surface reduction** — shrink the catalog *every* client sees.
    Options: task-oriented consolidation (fewer, task-completing tools — see §3); a compact dispatcher tool (`{operation, arguments}` plus an on-demand `describe`/catalog operation — **not** a fat union that re-embeds every operation schema, which is consolidation at best and costs the same tokens); narrowly-scoped servers; deployment profiles; or stable authorization-scoped catalogs (§9).
    This is the only axis that helps a client that preloads and never lazy-loads.
    Trade-off: a dispatcher collapses per-operation descriptions, annotations, validation, and client approval into one tool, so enforce operation-level validation and policy server-side, and document that native annotations and approval apply to the dispatcher as a whole.

- `[2.selective]` **Make discovery selective — through the right surface.** A server with 80 tools and no way to narrow them is functionally undiscoverable.
  But native `tools/list` accepts only an opaque pagination cursor: it has no query, namespace, detail, or summary parameter.
  Provide filtering and summary/detail modes through a discovery tool, a resource catalog, configuration, or authorization-scoped catalogs, and label them as such — never as a portable `tools/list` capability.

- `[2.completion]` **Use native completion for hard-to-guess prompt and resource-template arguments.** If the server has prompt arguments or resource-template variables with large, dynamic, or enum-like value sets, advertise the `completions` capability (via `server/discover`) and implement `completion/complete`.
  Use it proactively to reduce invalid IDs, paths, project keys, channel names, and URI components; keep tool-argument repair in normal tool errors because MCP completion does not complete arbitrary tool arguments.

- `[2.token-rules]` **Discovery obeys token-efficiency rules.** Keep each definition compact (the baseline above); where a discovery tool or resource catalog returns its own list-shaped payload, paginate it, support filtering, and return concise summaries by default with an opt-in detailed mode (see §8).

- `[2.client-variance]` **Design for client variance.** Some clients preload tools, paginate discovery, ignore annotations, or expose resources poorly; keep discovery usable for the least-capable realistic client.

- `[2.index-not-bodies]` **Index resources; do not inline bodies.** Catalog entries carry metadata sufficient to triage; bodies are fetched on demand (see §4).

- `[2.surface-is-contract]` **The discovery surface itself is contract.** Renaming a tool, changing its namespace, or removing it from a catalog is a discoverable change to clients; treat it as a versioned event (see §9).

- `[2.resource-catalog]` **Resource catalogs are part of discovery.** A catalog that omits new resources or returns inconsistent metadata silently breaks agent planning.
  Treat the catalog as authoritative.

- `[2.fingerprint-in-discovery]` **Include the capability fingerprint in discovery responses where you publish one (§9).**
  Clients can short-circuit a re-walk if nothing has changed.

- `[2.auth-scoped-stable]` **Discovery may vary by authorization context, but never by hidden side effects.** The tool and resource lists an agent sees may legitimately differ across auth scopes — an unauthorized scope simply does not see a capability.
  They MUST NOT drift as a hidden side effect of unrelated calls: the protocol is stateless, list results no longer vary per-connection, and the permissible inputs to catalog shape are the request's own metadata, the authorization context, and declared server-global state — the same authorized client presenting the same request metadata gets the same surface in the same order (see §9), so a cached client can trust it within the advertised `ttlMs`.
  Make differences auth-scoped, declared, and stable, not per-request surprises.

Audit prompt: On the clients this server actually targets, what must an agent load before its first useful call — and is that the smallest surface those clients allow, given that the least-capable realistic client preloads every tool definition?

---

## 3. Tools

*Worked shapes: `examples.md` §1 (namespaced tool schema), §2 (structured tool response), §2a (dispatched-but-unconfirmed mutation result), §10 (worked task: API mirroring vs. task completion), §12 (response-delivery artifact), §13 (tool result with resource link).*

- `[3.naming]` **Name with `snake_case`, prefix, verb, noun.** `slack_send_message`, not `send_message`.
  Generic verbs collide across servers in multi-server contexts.
  Omit the service prefix only when every target host — including code-execution surfaces that flatten tools into one module namespace — preserves a per-server namespace, and document that host assumption if you do.

- `[3.verbs]` **Reuse verbs consistently.** `list`, `get`, `create`, `update`, `delete`, `send`, `search` should mean the same thing across tools.
  Inconsistent verbs make the agent second-guess otherwise-obvious calls.

- `[3.descriptions]` **Write descriptions that are narrow and unambiguous.** Cover when to use, edge cases, and an example invocation.
  Descriptions are the primary input to tool selection.

- `[3.constraint-separation]` **Separate binding constraints from background in description prose.** Each constraint is its own sentence with explicit strength — mandatory or default-with-override — phrased against observable behavior: "set `purge: true` only for messages the user explicitly asked to hard-delete", not "be careful with `purge`".
  Mechanics, rationale, and history stay out of the constraint sentences, so a reader can extract every rule without parsing narrative.
  The same rules-then-context discipline as the §2 capability summary applies here in compact form.

- `[3.param-names]` **Disambiguate parameter names.** Use `user_id`, not `user`; `channel_id`, not `channel`; `started_after`, not `since`.
  Ambiguous names cause wrong-shape arguments on the first call.

- `[3.required-optional]` **Apply required-vs-optional discipline strictly.** Required parameters must be necessary; every optional parameter declares its omission semantics — what the server does when the field is absent — in its schema description.
  Use JSON Schema `default` only when the server actually applies that value; `default` is annotation, not behavior, and no validator injects it into the call.
  Omission need not mean a substituted value: "omit `thread_ts` to post a new top-level message" is complete omission semantics with no default at all.
  For partial-update tools, omission semantics extend to a three-way distinction defined per optional field: absent (leave unchanged) vs `null` vs empty string/list.
  If clearing a field is supported, make the clearing form explicit — a documented `null`-clears semantic or a dedicated sentinel — and test it.
  Agents commonly guess that `null` clears, so an undefined convention silently drops intended clears or silently clobbers.

- `[3.strict-types]` **Use strict types.** Enums where the value set is fixed; formats (`date-time`, `uri`, `email`) where the shape is conventional; `integer` vs `number` chosen deliberately.

- `[3.dialect]` **Declare schema dialect where supported.** A schema without a dialect forces clients and validators to infer semantics.

- `[3.portable-schema]` **Prefer simple, portable schema constructs — a deliberate preference, not a protocol limit.** The protocol accepts any JSON Schema 2020-12 keywords in `inputSchema`/`outputSchema` (SEP-2106); this skill still prefers flat closed objects, enums, and discriminated modes over deep `anyOf`/`oneOf` unions, recursive `$ref`s, `patternProperties`, and dependent constraints — clients, validators, and model planners mishandle these, producing validation drift, broken argument forms, and wrong-shape first calls.
  When a parameter's shape genuinely varies by mode, use a discriminator field with per-mode documentation, or split tools per the granularity rule — not a union.
  Use a complex construct only when every target host's handling of it is verified by captured evidence (design-workflow Step 8 `host_capture` fixture, or equivalent captured responses in a review).

- `[3.closed-schemas]` **Close object schemas.** Use `additionalProperties: false` on all object schemas unless unknown extension fields are an intentional, documented contract.

- `[3.stringified-shim]` **Tolerate stringified container arguments only as a measured compatibility shim.** When captured host evidence (a design-workflow Step 8 `host_capture` fixture, or captured tool-call arguments in a review) shows a target client serializing object or array arguments to JSON strings, coerce the string before validation: parse once, bound size and depth, and let a malformed string fail as an ordinary field-level validation error naming the parameter (§6).
  Never widen the published schema — advertising `string | object` teaches every client a false contract; the shim is server-side leniency, not contract.
  Log each coercion so the shim stays a measured, droppable workaround rather than a permanent invisible layer.

- `[3.output-schema]` **Publish an `outputSchema` and return `structuredContent` when targeting MCP versions that support them.** This is the normative target, not an optional nicety: when a tool declares an `outputSchema`, servers MUST return conforming `structuredContent` on success results, and clients SHOULD validate against it (the `isError: true` carve-out is the next item).
  The obligation is per tool, not per catalog: evaluate every tool independently against this rule, and one worked example on a flagship tool does not discharge it for the rest.
  The carve-out is narrow: it reaches only a tool whose success result carries nothing a caller parses or branches on — a rendered image, an audio clip, a prose answer.
  What it permits is exactly one thing: a qualifying tool may ship no `outputSchema` and return no `structuredContent`.
  Nothing else in this rule is waived.
  Ask what the result contains, not where the tool puts it: data a caller parses out of `content` is machine-contract data misplaced (`[3.content-types]`), not an absent contract, so omitting `structuredContent` never establishes the carve-out.
  Note a qualifying omission in the tool's description.
  Evaluate `resource_link` results under `[3.resource-links]`.
  Keep parser-compatible JSON in `content` as a fallback for older or weaker clients; support varies across MCP versions, so the fallback stays useful — but it is the fallback, not the contract.

- `[3.output-schema-scope]` **Scope `outputSchema` to success results — a deliberate reading of an unsettled point.** The spec does not say whether `outputSchema` binds `isError: true` results.
  This skill takes the position that it governs success results only: an `isError: true` result carries the documented error envelope in `structuredContent` (see §6) instead of the success shape, and is not validated against `outputSchema`.
  Document the error envelope per tool — or union it into `outputSchema` — so validators never have to guess which shape applies; do not leave the two contracts in silent conflict.

- `[3.structured-default]` **Default to structured output.** Structured data is authoritative; text or markdown is supplemental rendering for human-facing clients.
  `structuredContent` may carry any JSON value under 2026-07-28, but an object shape remains this skill's default because `outputSchema`-validated objects are what field-level repair (§6) and detail toggles (§8) attach to.
  Token-efficiency rules for responses live in §8.

- `[3.advertised-fields]` **Advertise only response fields you populate.** Every required field and every documented always-present field appears on the wire; conditional fields document and satisfy their appearance conditions.
  An advertised field that never appears — an `outputSchema` property or an "every response carries it" claim the implementation cannot populate — is a contract violation, not schema slack.

- `[3.content-types]` **Use rich content types deliberately.** Tool results may include `text`, `image`, `audio`, `resource_link`, embedded `resource`, and `structuredContent`.
  Put machine-contract fields in `structuredContent`; use `content` for human rendering, linked artifacts, and compatibility fallbacks.

- `[3.resource-links]` **Prefer resource links over inline bulk or binary payloads.** For large documents, generated charts, exports, or files, return a concise `structuredContent` summary plus a `resource_link` with `uri`, `name`, `description`, `mimeType`, `size` where known, and `annotations.lastModified` where useful.
  Resource links returned by tools may not appear in `resources/list`, so include enough metadata for the agent to fetch, subscribe, cite, or discard them.

- `[3.content-annotations]` **Use content annotations to steer attention, not correctness.** `annotations.audience`, `priority`, and `lastModified` help capable clients decide what to show the user vs. model and when to re-read linked content.
  Never make safety or parsing depend on annotations being visible.

- `[3.declare-effects]` **Declare side effects, idempotency, and rate limits as first-class contract.** Use tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) and structured response fields.

- `[3.honest-annotations]` **Set annotations honestly.** `readOnlyHint: true` on a tool that mutates is worse than no annotation, because clients will skip safety prompts.

- `[3.annotation-defaults]` **Know the annotation defaults and gates.** An omitted annotation is not neutral: the spec defaults are `readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: false`, and `openWorldHint: true`, so an unannotated tool already reads as a possibly-destructive mutator.
  Declare annotations explicitly anyway so the contract is visible rather than inherited — but never report a missing `destructiveHint` as if omission declared the tool safe.
  `destructiveHint` and `idempotentHint` are meaningful only when `readOnlyHint` is `false`; omit them on read-only tools rather than asserting semantics the protocol does not assign there.
  Because omission still carries the spec defaults, annotate semantically equivalent sibling tools identically: one omitted annotation in a family whose peers declare non-default values silently asserts the *default* — different semantics — and even where the omitted value happens to match, the visible inconsistency teaches an agent to distrust the hint across the whole server.
  Where siblings genuinely differ (a parameter changes idempotency), keep the differing annotation and state the reason in that tool's description.

- `[3.mutation-scope]` **Define mutation by observable scope, not by I/O — a deliberate reading of an ambiguous hint.** The MCP spec glosses `readOnlyHint` only as "the tool does not modify its environment," which is broad enough to read either way for a local write.
  This skill takes the position that `readOnlyHint` should track whether the call changes state that outlives the response contract: shared systems, persistent records, other users' data, or persistent state in the caller's environment that other calls or tools can observe.
  Under that reading it is not about whether the tool performs any I/O at all, and a write to the caller's filesystem does not by itself count as mutation.
  Take the reading deliberately, because it has a safety consequence: clients use `readOnlyHint: true` to gate auto-approval, so a tool annotated this way may execute without confirmation on a trusted server.
  That is the point — a semantically read-only call should not pay mutation-grade friction — but it means the artifact must genuinely be scoped to the response (declared TTL, no shared visibility), and the §3 rule below still prefers a resource or resource link over a local file wherever one fits.
  A transient artifact written purely as response delivery — for example, a CSV or Parquet result file with a declared TTL, scoped to this call, and no shared visibility — is treated as part of the response, not a side effect, so the tool stays `readOnlyHint: true`.

- `[3.document-reading]` **Document which `readOnlyHint` reading the server uses.** The observable-scope reading is a judgment call, not settled spec: a reviewer who reads "environment" literally may disagree, so document the choice rather than asserting it, and apply one reading consistently across tools.

- `[3.large-results]` **Prefer resource or resource-link delivery for large results.** Return large results as resources or resource links with TTL metadata where you can; reserve the response-delivery-artifact pattern for cases where an inline or linked resource does not fit.

- `[3.artifact-disclosure]` **Disclose response-delivery artifacts through the structured response and the description.** Use a structured response field (e.g., `result_artifact: {path, ttl_hours, mime_type}` — a house convention object, so its sub-fields use `snake_case`, not the native `Resource.mimeType`) and the tool description, never annotation flipping.
  The local-path artifact pattern is valid only when server and caller share a filesystem (co-located stdio); a server reachable over HTTP or by remote clients must return the result as a fetchable resource, `resource_link`, or URL instead, because a returned filesystem path is dead weight to a remote client.
  See `examples.md` §12 for a worked response-delivery artifact.

- `[3.annotations-not-security]` **Annotations are hints, not security.** Declare them so agents can plan; do not rely on them for access control.
  Enforcement lives in the implementation.

- `[3.annotation-visibility]` **Do not rely on annotation visibility.** Some clients do not surface annotations to the model, so annotations are advisory and cannot be the only safety prompt.

- `[3.idempotency-key]` **Give unsafe mutations an idempotency key.** For create/send/pay-style tools where a duplicate is costly, accept a client-supplied `idempotency_key`, and declare its deduplication window and scope.
  Without one, an agent that retries after a timeout cannot avoid double-posting.

- `[3.dispatched-vs-applied]` **Distinguish dispatched from applied, and define ambiguous-outcome recovery.** When a mutating tool returns before its effect is confirmed, the structured result carries the dispatch fact and a confirmation status (e.g. `command_sent: true`, `status: "applied" | "pending_verification"`), plus a `follow_up` object naming the verification surface (see `examples.md` §2a).
  `follow_up` reuses the §6 `repair` object shape exactly — `{next_step, tool, arguments, alternative}` with `alternative` optional, `tool` a registered `tools/list` name, `arguments` literally callable — the same next-step vocabulary on the success carrier, not a second convention.
  The dispatch fact must survive onto the timeout path: report a verification timeout as a dispatched-but-unconfirmed success result with a reconcile `follow_up`, never as a plain failure, or agents blind-retry non-idempotent actions.
  The same obligation covers transport drops the server never sees: the tool description and timeout-adjacent repair hints name the lookup or status surface that resolves the ambiguity, so the agent reconciles instead of blind-retrying.

- `[3.partial-success]` **Declare partial-success semantics for multi-item operations.** Say whether the operation is atomic or per-item.
  Per-item results carry a stable item id, outcome, and retryability, and the retry surface accepts just the failed items — never force the agent to re-run completed effects to recover the failures.

- `[3.version-precondition]` **Protect read-modify-write with version preconditions.** Where concurrent edits are possible, accept an optional `expected_version` (or `if_match`) input, fail with a stable `conflict` error code when it mismatches, and make the repair path re-read-then-reapply.
  Without it, an agent can silently overwrite changes made by users or other agents between its read and its update.
  Where the backing store has no version counter, the token may be a content-derived hash computed over a canonicalized serialization, returned by every read surface and compared on write.
  Document the canonicalization (key and list ordering) and the collision assumptions, and keep the conflict repair path re-read-then-reapply — the re-read returns the fresh token.
  A content-derived token keeps optimistic locking stateless.

- `[3.patch-ops]` **Mutate large documents through bounded patch operations, not round-trips.** For tools that edit large mutable documents (dashboards, configuration trees, long structured records), offer targeted server-side patch or field operations guarded by the version precondition above, so the agent need not read the full body, rewrite it in context, and send it back — two full-document round-trips per edit is a token-efficiency failure (§8) as well as a concurrency hazard.
  Offer a dry-run or diff preview where a wrong patch is costly, surface post-write validation results in the structured response, and give patch failures the same field-level repair as any other error (§6).
  Executable transform expressions are never the default edit path; their sandboxing and resource limits are implementation security, out of scope here — route them to a dedicated security review (§3 Security).

- `[3.task-completing]` **Prefer task-completing tools over endpoint mirrors.** Tool granularity follows user/agent tasks, not the underlying API's resource map.
  Valid split exceptions are recorded in `design-workflow.md` Step 3.

- `[3.hide-steps]` **Hide internal step granularity.** When a task requires multiple steps internally, expose the task — not the steps — unless the steps are themselves separately useful tasks.

- `[3.failure-contract]` **Failure paths are part of the contract.** Repair signals belong in tool result errors (see §6), not in description prose.

- `[3.declare-params]` **Declare every parameter the tool reads.** Hidden parameters that affect behavior belong in the agent-actionable implicit state declaration (see §1), not implicit in tool behavior.

- `[3.code-exec-clients]` **Design tool surfaces to survive code-execution clients.** When an agent imports the server as a code API instead of calling tools through a chat UI (see [mcp-vs-cli.md](mcp-vs-cli.md)), tool and parameter names become identifiers in source and errors cross a language boundary.
  Keep names import-friendly (`snake_case`, no collisions when flattened into one module namespace), keep `structuredContent` the authoritative return so it maps to a native value, and choose symbolic error `code`s that translate cleanly into language-native exceptions.
  A task-completing tool composes in code far better than an endpoint chain the agent must re-orchestrate by hand.

### Anti-patterns

- `[3.ap-endpoint-wrapping]` **Wrapping every API endpoint as a tool.** More tools dilute discovery, increase token cost on every definition load, and make selection harder.
  A 60-tool server where every tool maps to a REST endpoint typically expresses 6–10 actual user tasks.
  Collapse endpoint chains into the task they serve.

- `[3.ap-prose-effects]` **Burying side effects, idempotency, or rate limits in description prose.** Agents do not reliably read prose for safety-relevant signals.
  If a tool mutates state, say so via `destructiveHint`; if it can be retried safely, say so via `idempotentHint`; if it has a per-minute call limit, surface that in the response, not the description.

- `[3.ap-readonly-substitute]` **Using `readOnlyHint` as a substitute for artifact disclosure.** Disclose transient response artifacts through the structured response and the tool description — flipping the annotation is not disclosure.
  Under this skill's observable-scope reading, the tool stays `readOnlyHint: true`, because clients use `false` to gate auto-approval and a semantically read-only call gains friction without safety.
  A server adopting the literal reading may set `false` instead — but it must document that reading and apply it consistently across tools (see the mutation-scope rule above); the anti-pattern is undocumented flipping or mixing the two readings, not the literal reading itself.

- `[3.ap-mode-annotations]` **Mode-dependent side effects under one static annotation.** A multi-modal tool whose discriminator argument (`action`, `mode`) spans read and mutating modes cannot be honestly described by one static annotation set.
  Prefer splitting the read modes into a separate `readOnlyHint: true` tool — this is granularity pressure, the same force as the §2 dispatcher trade-off, which already requires per-operation policy server-side.
  If the tool stays combined, annotate for its worst mode, state in the description that annotations describe the whole tool rather than the selected mode, and enforce per-mode authorization server-side regardless.
  Failure shape: a manage-style tool combined a common pure-read default action with mutating modes under one honest worst-mode annotation (`destructiveHint: true`), so its most common call paid mutation-grade confirmation friction — the annotation was right; the granularity was wrong.

### Security

- `[3.sec-untrusted-content]` **Treat external content as untrusted.** Tool output must not smuggle instructions that override the user's task or the server contract.

- `[3.sec-sanitize]` **Sanitize output before returning it.** Strip or escape credentials, control sequences, and content that would corrupt the client's rendering or parser.

- `[3.sec-exfiltration]` **Minimize data exfiltration paths.** Open-world tools need explicit data-flow review because they can send local or retrieved data to external services.

- `[3.sec-least-privilege]` **Use least-privilege scopes.** Request only the scopes needed for the exposed tasks, and surface insufficient-scope repair separately from missing credentials.

- `[3.sec-confirmation]` **Define confirmation boundaries.** Destructive, paid, external-send, or broad-read operations need clear preconditions before execution.

- `[3.sec-handle-modes]` **Keep state handles client-opaque, in one of two modes.** Either a handle is a high-entropy reference to server-side state, or — where statelessness is required, typically cursors — it is an integrity-protected token authenticated with deployment-managed key material that survives multi-process routing and restarts.
  Tokens never contain credentials and never expose internal record IDs; encrypt token payloads that carry sensitive state.
  Every continuation re-applies authorization, expiry, and budget checks — integrity protection is not authorization.

- `[3.sec-bounded]` **Keep this subsection bounded.** These are agent-facing contract rules only; full threat modeling (trust boundaries, attack trees, data-flow analysis) is out of scope here — route it to a dedicated security review.

Audit prompt: For each tool, can an agent decide to use it, call it correctly, and recover from a failure — using only the schema and structured response, never the prose?

---

## 4. Resources

*Worked shapes: `examples.md` §3 (resource index entry), §4 (resource body with chunking), §5a (resource template with completion), §5b (resource subscription).*

- `[4.uri-stable]` **Use stable, hierarchical, predictable URI patterns.** Resource URIs are identifiers agents quote back to the server and may cache; instability breaks repeat calls.

- `[4.uri-nouns]` **URI segments use stable domain nouns.** Not internal identifiers that change between deployments.
  The URI is part of the contract.

- `[4.index-vs-body]` **Distinguish lightweight indexes from bodies.** Resource lists return summaries with metadata sufficient for the agent to decide whether to fetch the body.

- `[4.summary-first]` **Surface a summary before the body for large resources.** Force the agent to opt in to bulk content rather than handing it whole documents by default.

- `[4.chunkable]` **Make body content chunkable with stable identifiers.** Agents need to fetch chunk N+1 without re-fetching N, and to cite a chunk back to a tool.

- `[4.chunk-stability]` **Chunk identifiers are stable across reads of the same resource version.** If the resource changes, identifiers may change — but the change is observable via the resource's modification metadata.

- `[4.chunk-fetch-path]` **Every chunk needs a callable fetch path.** Native `resources/read` takes only a URI, so give each chunk its own URI (and publish the pattern as a resource template) or provide a labeled tool fallback that accepts the chunk id.
  A chunk catalog under `_meta` is auxiliary metadata some clients never surface to the model; the in-band next-chunk pointer in the body (`next_chunk_id`/`next_chunk_uri`) and the chunk URIs are what the agent can actually act on.

- `[4.triage-metadata]` **Include the metadata fields agents use to triage.** Use native `Resource` fields: `title`, `description`, `mimeType`, `size`, and `annotations.lastModified`.
  Missing metadata pushes the agent into fetching bodies blindly; custom triage fields with no native home go under a namespaced `_meta` key, not a new top-level field.

- `[4.templates]` **Expose URI-shaped resources through resource templates.** When resources are parameterized by a URI pattern, publish `resourceTemplates` via `resources/templates/list` (with `uriTemplate`, `name`, `title`, `description`, `mimeType`) so agents can discover the shape without enumerating every instance.
  Templates are a native discovery primitive distinct from the resource list itself.

- `[4.template-completion]` **Pair resource templates with completion where values are dynamic.** For template variables such as `{project}`, `{channel_id}`, `{schema}`, or `{path}`, implement `completion/complete` when clients negotiate `completions`.
  This gives agents a proactive way to build valid URIs instead of learning the value space through failed reads.

- `[4.short-summaries]` **Keep summaries short.** Resource summaries are at most three sentences, never paragraphs.
  They appear in lists of dozens; long summaries defeat the index.

- `[4.description-constraints]` **Resource `description` prose follows the §3 constraint-separation rule.** Any binding constraint in a resource or resource-template description is its own explicit-strength sentence, kept apart from background — same discipline, compact form.

- `[4.subscriptions]` **Support resource subscriptions for mutable resources.** If a resource can change during a long-lived agent session and stale reads matter, advertise `resources.subscribe` and serve the `resourceSubscriptions` filter of `subscriptions/listen` — the client names the URIs it watches on the listen request (`resources/subscribe`/`unsubscribe` no longer exist) — emitting `notifications/resources/updated` on that stream, tagged with `io.modelcontextprotocol/subscriptionId`.
  Use the `resourcesListChanged` filter type for catalog membership changes; use per-resource updates for body changes.
  Subscriptions are opt-in streams a client may never open, so also bound staleness for non-listening clients with an honest `ttlMs` on `resources/read` (see `[8.cacheable-results]`).

- `[4.failure-contract]` **Resources share the failure-recovery contract.** Missing-credential, not-found, gone, and rate-limit signals must be machine-readable (see §6).

- `[4.jsonrpc-errors]` **Resource failures use JSON-RPC errors.** Put the repair contract in structured `error.data` using the same unified error envelope as tool results (§6) — `machine_code`, `human_message`, `details`, `temporary`, `retry_after_ms`, and a single `repair` object — not a separate vocabulary.

- `[4.tool-fallback]` **Some clients do not expose resources well.** If discoverability matters for a read-oriented capability, provide a tool fallback that reaches the same indexed content.
  The fallback must be self-sufficient from `tools/list` alone: callable without values learnable only from resources — for example, omitting an optional selector returns the index — and every required, non-obvious constrained value space has a tool-reachable lookup or enumeration.

- `[4.token-rules]` **Pagination, filtering, and truncation rules live in §8.** Reference them; do not duplicate.

- `[4.write-is-tool]` **A write-surface "resource" is a tool.** Resources are read-oriented; if mutation is the point, model it as a tool with the appropriate annotations (see §3).

Audit prompt: Can an agent decide whether to fetch a resource — and which chunk of it — without first reading the full body?

---

## 5. Prompts

- `[5.when-to-use]` **State when to use the prompt explicitly.** The agent needs to recognize the matching task, not infer it from the title.

- `[5.prerequisites]` **List prerequisites.** Which tools, which resources, and which permission or context assumptions the prompt relies on.
  Missing prerequisites surface as confusing failures partway through execution.

- `[5.completion]` **Offer completion for prompt arguments with dynamic value sets.** If a prompt argument asks for a project, workspace, repository, environment, channel, or similar value the agent should not guess, support `completion/complete` when the server advertises the `completions` capability.

- `[5.name-followups]` **Reference expected follow-on tools and resources by name.** A prompt that doesn't tell the agent what to invoke next is half a scaffold.

- `[5.scaffolding-only]` **Keep prompts as orchestration scaffolding, not contract.** Essential behavior — argument shapes, side effects, error shapes — belongs in tool and resource schemas.
  See `examples.md` §5 for a prompt scaffold that references tools and resources without redefining them.

- `[5.no-redefine]` **Prompts may reference; they may not redefine.** A prompt may name a tool by canonical name, but it must not redefine the tool's contract or override its arguments.
  The schema is authoritative.

- `[5.optional]` **Prompts are optional.** A server with no prompts is fine; a server whose prompts are load-bearing for correctness is broken.

### Anti-patterns

- `[5.ap-contract-container]` **Prompts as contract container.** Encoding required behavior in prompt text rather than schema means the agent must read prose to call correctly, and any client that bypasses prompts (most code-execution clients do) will call incorrectly.
  Orchestration scaffolding is not a substitute for schema.
  If behavior is essential, encode it in tool/resource schemas.

Audit prompt: If every prompt on this server were removed, would any tool or resource become incorrect or unsafe to call?

---

## 6. Failure Recovery

*(Cross-cutting; co-equal with first-call success.)*

- `[6.symbolic-codes]` **Use stable, machine-readable error codes.** Codes are symbolic strings (e.g., `not_found`, `rate_limited`, `invalid_field`), not numeric exit codes.
  The symbolic code is the authoritative branch key for agents.

- `[6.jsonrpc-code-allocation]` **Allocate numeric JSON-RPC `error.code` values per the spec's partition of the reserved range.** This rule governs only the numeric code on JSON-RPC failures (`[6.resource-errors]`, and the admissibility exceptions in `[6.tool-errors]`); the symbolic branch key stays with `[6.symbolic-codes]`.
  `-32020` to `-32099` is reserved for the MCP specification: emit a code from this band only when the spec defines it, and only with its specified meaning — the assigned codes are cited where their conditions live (`[1.transport]`, `[6.capability-missing]`, `[6.tool-errors]`).
  `-32000` to `-32019` is a closed legacy band: new codes MUST NOT be allocated there, new implementations SHOULD NOT use it at all, and receivers MUST NOT assume a specific meaning for codes received from it.
  A server-defined error purpose the spec does not cover SHOULD take a code outside the JSON-RPC reserved range (`-32768` to `-32000`); the rest of the integer space is open for application-defined codes.
  Codes retired by earlier revisions stay reserved: never emit the retired `-32002` (folded into `-32602`; clients SHOULD still accept it from 2025-11-25 servers) or the removed `-32042`.

- `[6.document-codes]` **Document every error code per tool — without bloating every definition.** An undocumented code is an undiscoverable code; agents cannot branch on it reliably.
  But a full error catalog embedded in every `tools/list` entry inflates the definition each preloading client pays for (see §2), so choose placement by cost: keep only selection- and repair-critical codes inline in the definition, and serve the complete per-tool catalog through an on-demand surface (`describe_tool`, a resource, or the capability summary) that repair hints can reference.

- `[6.code-changes-breaking]` **Code semantic changes are breaking.** Introducing a new additive code is safe; changing or renaming an existing code's meaning is a breaking change.
  Where a fingerprint is published, both are recorded in it (see §9).

- `[6.field-feedback]` **Provide field-level validation feedback.** Which field, why it's invalid, and which values are allowed. "Invalid input" without a field name forces the agent to guess.

- `[6.offending-value]` **Include the offending value when known-safe; omit it only with disclosure, never silently.** The agent's repair attempt depends on knowing what the server actually received.
  Emit `details.value` when the received value itself is known-safe: the server minted it (a handle or URI it issued), or it is one of the schema's published enum members arriving where it is invalid.
  A received value that failed a format check is not known-safe however innocent the parameter — any free-form parameter can receive a mispasted secret, so safety is a property of the value, not of the schema field.
  Reliable redaction is a precondition for echoing a potentially sensitive value; best-effort pattern matching does not qualify, because a plain secret matches no pattern.
  For parameters whose received values' sensitivity the server cannot reliably determine, a blanket omission of `value` is conformant when the policy is disclosed on an agent-visible surface (the error-envelope schema/resource or the capability summary) — that disclosure is what "never omit silently" requires.
  An undisclosed omission is the defect.

- `[6.retryability]` **Signal retryability and rate limits explicitly.** Use `retry_after_ms`, `temporary: true|false`, and `rate_limit_remaining` where applicable.
  Agents need to distinguish "wait and retry" from "stop and reconsider."
  Define the invariants so servers don't emit incompatible combinations: `temporary: true` means *the same operation, unchanged, may succeed later* (transient condition); `temporary: false` means it will not — repair or escalate instead. `retry_after_ms` is a non-negative integer only when a delay is known, and `null` otherwise; it must be `null` when `temporary: false`.
  Retryability is independent of repairability: a `temporary: false` error can still carry a `repair` (a *different* corrective call), and a `temporary: true` one may carry none.

- `[6.repair-hints]` **Include "what to do next" repair hints.** The corrective call, parameter, or filter.
  The first repair attempt is as important as the first call.

- `[6.repair-intent]` **Repair preserves caller intent.** When corrected arguments are already known, `repair.tool` names the failing tool and `repair.arguments` carries every still-valid, non-sensitive original argument plus the minimal correction; route to a different tool only when the correction must first be discovered or performed there.
  Forcing the agent to re-orchestrate a task that one corrected argument would have completed wastes the repair.

- `[6.repair-callable]` **Repair hints reference real, callable surfaces.** Tool names, parameter names, valid enum values — not free-form prose.
  `repair.arguments` holds literally callable values, never placeholders such as `"<one of the listed slugs>"`; when a required value must be discovered first, make the primary repair call the lookup or enumeration tool that returns it.

- `[6.capability-missing]` **Surface capability-missing failures explicitly — the native way.** If a request needs a capability its `_meta` `clientCapabilities` did not declare, the spec's answer is `MissingRequiredClientCapability` (`-32021`, HTTP 400) with `data.requiredCapabilities` naming what is missing; return it rather than inventing a house code for the same fact.
  Where a weaker-client fallback exists (a tool, resource, or prompt that covers the path without the capability), name it in the same `error.data` alongside the native fields so the agent can reroute instead of stopping.

- `[6.elicitation]` **Use elicitation only behind declared support and clear fallback.** When a server needs missing user input, user confirmation, or sensitive external interaction during a call, prefer MCP elicitation for clients whose per-request `clientCapabilities` declare it — and only the modes they declare (`form`, `url`; an empty capabilities object means form-only).
  Elicitation is delivered via MRTR: the call returns `resultType: "input_required"` with the `ElicitRequest` inside `inputRequests`, and the client retries the original call with `inputResponses` (plus any server `requestState`, echoed verbatim); server-initiated `elicitation/create` no longer exists, and `InputRequiredResult` is permitted only on `tools/call`, `resources/read`, and `prompts/get`.
  Form mode MUST NOT collect passwords, API keys, payment credentials, or other secrets; URL mode is the safe path for those, with cross-retry correlation carried in `requestState` (integrity-protected per the MRTR rules — it is attacker-controlled input; `elicitationId` and `notifications/elicitation/complete` no longer exist).
  Servers MUST NOT assume the client will fulfill the requests or retry; for clients without elicitation, return an actionable error or task `input_required` status that names the next callable surface or external action.

- `[6.tool-errors]` **Tool semantic errors return as tool result errors.** Set `isError: true` on the tool result.
  JSON-RPC errors are reserved for transport, protocol, and non-tool RPC methods (such as `resources/read` and `resources/list`); raising a JSON-RPC error from `tools/call` strips the structured-response contract from the failure path.
  The exceptions are protocol-level conditions the spec assigns a code to, which stay JSON-RPC even on `tools/call`: malformed or version-mismatched per-request `_meta` (`-32602`, `-32022`), header mismatch (`-32020`), and a required-but-undeclared client capability (`-32021`, §6 above) — including the tasks extension's "cannot proceed without tasking" case (§7).
  These are conditions about the call's admissibility, not about the tool's semantics — the rule is that *semantic* failures never leave the tool-result carrier.
  A `resultType: "input_required"` interim result is neither carrier: it is not a failure, and it never substitutes for one (see `[6.elicitation]`).
  See `examples.md` §6 for an actionable tool-result error payload.

- `[6.resource-errors]` **Resource semantic errors return as JSON-RPC errors.** `resources/read` and `resources/list` are non-tool RPC methods, so failures surface through the JSON-RPC envelope; carry the same unified error envelope (below) in structured `error.data`, renaming only `code`→`machine_code` and `message`→`human_message` (`[6.rename]`).
  The numeric `error.code` on that envelope is allocated per `[6.jsonrpc-code-allocation]`.

- `[6.name-carrier]` **Name the error carrier in the capability summary.** State where the envelope travels — `structuredContent` on the tool result, JSON-RPC `error.data`, and any disclosed degraded mode (below) — so agents know where to parse a failure before the first one occurs.

- `[6.correlation]` **Errors include correlation context.** A `request_id`, the offending parameter, and (where applicable) the resource URI.
  Agents need to correlate failures with the requests that caused them.
  The OpenTelemetry `_meta` keys (`traceparent`, `tracestate`, `baggage`) are operator-side trace plumbing; they complement `request_id` but do not replace it, because `_meta` may never be surfaced to the model.

### One error envelope, two carriers

The failure-recovery contract is **one envelope** with identical field semantics regardless of where it surfaces.
Tool-result errors carry it in `structuredContent` (alongside `isError: true`); resource and other non-tool RPC failures carry it in JSON-RPC `error.data`.
The *only* permitted divergence is renaming `code`/`message` to `machine_code`/`human_message` on the JSON-RPC side (`[6.rename]` below) — every other field is the same name, shape, and cardinality on both surfaces.
Do not invent surface-specific aliases (e.g. `repair_hints`) or surface-specific flags (e.g. `recoverable`).

| Field | Tool result (`structuredContent`) | JSON-RPC (`error.data`) | Required? | Notes |
| --- | --- | --- | --- | --- |
| symbolic code | `code` | `machine_code` | yes | Stable symbolic string; the authoritative branch key. Renamed on the JSON-RPC side (`[6.rename]`). |
| human text | `message` | `human_message` | yes | Short human-readable summary. Renamed on the JSON-RPC side (`[6.rename]`). |
| field detail | `details` | `details` | where applicable | `{field, value, reason}` for one parameter, `{fields, reason}` for a cross-parameter constraint (see below); emit, redact, or omit `value` per `[6.offending-value]`. |
| transient? | `temporary` | `temporary` | yes | See retryability invariants above. |
| retry delay | `retry_after_ms` | `retry_after_ms` | yes (nullable) | Always present alongside `temporary`; a non-negative integer when a delay is known, else `null` (and always `null` when `temporary: false`). Always emit the key so agents distinguish `null` from a number without special-casing a missing field. |
| rate budget | `rate_limit_remaining` | `rate_limit_remaining` | on rate-limit errors | Non-negative integer of remaining calls in the current window, where the surface exposes one. |
| repair | `repair` | `repair` | where a corrective path exists | A single object `{next_step, tool, arguments, alternative}` — see below. Omit the field entirely when no repair exists (never emit `null` or an empty array). |
| correlation | `request_id`, `resource_uri`, `fingerprint` | `request_id`, `resource_uri`, `fingerprint` | where applicable | `resource_uri` where the failure is tied to a resource. |

- `[6.rename]` **The JSON-RPC-side rename is mandatory, and disclosure does not waive it.** The rename is not about key collision — inside `error.data` nothing collides with the native `error.code`/`error.message`.
  It serves two readers instead: an agent reading the *serialized* error object, where a second `"code"` key (a symbolic string beside the numeric JSON-RPC code) invites grabbing the wrong one; and a parser written once against this contract, which finds the branch key under the same spelling on every conforming server.
  A capability summary that discloses keeping `code`/`message` inside `error.data` repairs neither: the model reading a failure payload may not have the summary in context at that moment, and per-server spellings break the portable parser.
  Contrast `[6.degraded-carrier]`, where disclosure sanctions a deviation only because a framework *cannot* comply — a preferred spelling is not an inability.

- `[6.details-field]` **`details.field` names a published parameter.** For a tool argument-validation failure, `field` is a single property path from the failing tool's published `inputSchema` (dotted for nested properties); for a constraint spanning several parameters, use `fields` — a non-empty array of unique published property paths — and emit exactly one of `field` or `fields`, never both.
  For a non-tool RPC failure, `field` names the offending request parameter of that method (`uri` for `resources/read`), under the same one-of rule.
  Translate internal names at the MCP boundary; never expose internal or synthetic names the surface does not accept (an internal `office_id` for a tool that takes `office`, a synthetic `bbox` for tools that take `south`/`west`/`north`/`east`).
  Emit, redact, or omit `value` per `[6.offending-value]`; error-code-specific detail keys (such as `required_scopes` on an `insufficient_scope` error) are permitted alongside `reason` when documented with the code.

- `[6.presence]` **Presence convention.** Fields marked *yes* are always emitted on both surfaces — `retry_after_ms` is the one nullable required field (it is bound to the always-present `temporary`, and `null` meaningfully signals "no known delay").
  Every other field is omitted entirely when it does not apply; do not send a placeholder `null` or empty array for an absent optional field.

- `[6.repair-object]` **`repair` is one object, not an array, on both surfaces.** Its shape is `{next_step, tool, arguments, alternative}`: `next_step` a stable symbolic label, `tool` and `arguments` the single primary corrective call (a real, callable surface), and `alternative` an *optional human-readable* fallback sentence for when the primary call doesn't fit.
  A single deterministic next action beats a ranked list the agent must choose from; if no corrective call exists, omit `repair` entirely rather than emitting `null` or an empty array. `alternative` is the one prose field in the envelope — it is fallback guidance for a human/agent to interpret, not a second machine-actionable hint (contrast the "real, callable surfaces" rule, which governs `tool`/`arguments`).
  The §3 dispatched-vs-applied rule reuses this exact `{next_step, tool, arguments, alternative}` object as a success-side `follow_up`; only the carrying field name differs, so there is one next-step vocabulary, not two.

- `[6.degraded-carrier]` **A degraded carrier is disclosed, never silent.** The two carriers above are the only contract carriers.
  As a disclosed degraded mode only — when a framework cannot place `structuredContent` on an `isError: true` result — `content[0].text` MAY carry the serialized envelope JSON: the capability summary names the limitation and its trigger, the envelope shape stays identical, and the mirror is removed once the framework supports the native carrier.
  An undisclosed text-only envelope is a third contract, not a fallback.

- `[6.no-recoverable]` **There is no `recoverable` flag.** Whether the *same* resource or operation can succeed again is already carried by the symbolic `code`/`machine_code` (e.g. `resource_gone`) plus `temporary`; whether recovery is possible by *another* path is carried by the presence of a `repair`.
  A separate `recoverable` boolean is redundant and was prone to contradicting an accompanying repair.

Audit prompt: For each failure mode, does the agent receive enough structured signal to either retry, repair, or escalate — without parsing the message field?
And does the same failure carry the identical envelope whether it surfaces as a tool-result or a JSON-RPC error?

---

## 7. Long-Running Operations

*(Cross-cutting; rules apply when an operation may outlive a normal request/response turn.)*

- `[7.execution-mode]` **Choose the execution mode deliberately.** Use blocking `tools/call` for short operations, progress notifications for bounded multi-step work, and a task (via the negotiated tasks extension) when clients need later status or result recovery.
  A blocking call that returns dispatched-but-unconfirmed follows the §3 dispatched-vs-applied rule; escalate to progress or tasks only when confirmation itself is long-running.

- `[7.declare-duration]` **Declare long-running behavior in the tool contract.** Tool descriptions or schemas include expected duration, timeout behavior, and whether partial progress is observable.
  Because task creation is server-directed (the client never requests it), the description also says when the tool may answer with a task instead of a direct result, so an agent is not surprised by `resultType: "task"`.

- `[7.progress-token]` **Support `progressToken` where progress exists.** Send rate-limited `notifications/progress` updates that identify current phase, completed work, and remaining work when knowable.
  Progress is request-scoped: it flows on the originating request's response stream, never on a `subscriptions/listen` stream — once the call has returned (including with a task), progress for the continuing work is carried by task status (`statusMessage`) instead.

- `[7.cancellation]` **Support cancellation where work can continue after the call starts.** Request-bound cancellation is transport-specific: on Streamable HTTP the client closes the request's response stream (a disconnect MUST be treated as cancellation; no notification is sent), while on stdio the client sends `notifications/cancelled`.
  Tasks cancel only via `tasks/cancel` — `notifications/cancelled` MUST NOT be used for a task — and the ack is an empty result signalling received intent, not stopped work: cancellation is cooperative and eventually consistent, so the task MAY still finish in another terminal status, observed via `tasks/get`.

- `[7.task-support]` **Gate tasks on the negotiated extension, at both ends of the exchange.** Tasks are the `io.modelcontextprotocol/tasks` extension (SEP-2663; see `SKILL.md` Spec Baseline for its status caveat): the client declares it in each request's `clientCapabilities.extensions`, and the server advertises it in its `server/discover` capabilities.
  Task creation is server-directed and per-request — there is no per-tool task flag and the client never signals task preference; a negotiated client MUST be prepared for either the standard result or a `CreateTaskResult` on any supported request.
  A server MUST NOT return a task to a client whose request did not declare the extension, regardless of earlier requests; if it cannot serve such a request without tasking, it returns the missing-capability error (`MissingRequiredClientCapability`, `-32021` — the ext-tasks draft still prints the pre-renumbering `-32003`) naming the extension in `data.requiredCapabilities`.
  That error stays JSON-RPC even on `tools/call` because it concerns the call's admissibility, not the tool's semantics (§6 owns the carrier rule).

- `[7.task-operations]` **Use native task operations for status and result retrieval.** Poll with `tasks/get` (respecting `pollIntervalMs`, which MAY change between polls), supply requested input with `tasks/update`, and cancel with `tasks/cancel`; `tasks/result` and `tasks/list` no longer exist — terminal payloads arrive inline on `tasks/get`.
  Task objects use the extension's fields and casing — `taskId`, `status`, optional `statusMessage`, `createdAt`, `lastUpdatedAt`, `ttlMs` (nullable; MAY change), optional `pollIntervalMs` — and `status` is one of `working`, `input_required`, `completed`, `failed`, `cancelled`.
  A `CreateTaskResult` is the request's `Result` with the task fields inline and `resultType: "task"`, and MUST NOT be sent before the task is durably created — a `tasks/get` for the returned `taskId` must already resolve.
  Resolve any MRTR exchange synchronously before creating the task; over Streamable HTTP, task methods carry the `taskId` in the `Mcp-Name` header so intermediaries can route to the instance holding the state.
  Give agents durable footing: encourage persisting `taskId`s so polling survives a crash, and keep `statusMessage` meaningful at every status.

- `[7.failed-task]` **`failed` means protocol failure; a tool error completes the task.** The terminal statuses split by fault domain: `completed` carries the underlying `result` — *including* a `tools/call` result with `isError: true` and its §6 envelope — while `failed` is reserved for JSON-RPC errors during execution and carries the JSON-RPC `error` object (`statusMessage` SHOULD summarize it).
  `failed` MUST NOT be used for a tool-result error, so an agent MUST inspect the `result` payload of a `completed` task before declaring success — `completed` is a delivery statement, not a success statement.
  State this loudly in the server's capability summary; agents trained on the 2025-11-25 coupling (tool error ⇒ `failed`) will otherwise misread `completed`.

- `[7.status-notification]` **Treat `notifications/tasks` as optional push, not contract.** Servers MAY emit it with the full task state to clients that opted in by naming the task in the `taskIds` filter of `subscriptions/listen` — the server's acknowledgement reports which `taskIds` it accepted; requestors MUST NOT rely on receiving it.
  Keep polling `tasks/get` as the authoritative status path, and use the notification only to poll sooner.

- `[7.input-required]` **Define `input_required` recovery.** The native path is fixed: when polling (or a `notifications/tasks` push) shows `input_required`, the `tasks/get` response carries the outstanding `inputRequests`; the client fulfills them with one or more `tasks/update` calls carrying `inputResponses`, then keeps observing until a terminal status.
  `inputRequests` obeys the MRTR capability rule — only request kinds and elicitation modes the client declared may appear — and the server MUST NOT assume the client will answer.
  Say which input mechanism the task uses — form elicitation, URL-mode elicitation, or a domain-specific fallback surface for clients without the capability — and what happens to the task when input never arrives (timeout to `failed`, `ttlMs` expiry, or a documented default).
  The task status alone is not enough; the agent needs the next operation and the capabilities required to perform it.

- `[7.task-handles]` **Task handles are state handles.** Apply the §1 state-handle discipline to `taskId`s: high-entropy opaque ids, authorization checked on every `tasks/get`, `tasks/update`, and `tasks/cancel`, bounded retention via `ttlMs` (a server MAY fail and then delete a task once its TTL elapses, so document the window).

- `[7.task-fallback]` **Degrade deliberately for clients without the extension.** Tasks are an extension, not core: a client that never declares `io.modelcontextprotocol/tasks` can neither receive nor operate on tasks, so a server whose work is genuinely long-running MAY expose a domain-specific status/cancel tool as a labeled fallback — mirroring the native signals (current status, when to poll again, result location, expiry), not replacing `tasks/*`.
  Keep status/result/cancel expressible as ordinary tools; that is also what code-execution clients compose best (§3).

Audit prompt: Can an agent monitor, cancel, and recover a long-running operation without guessing at server state?

---

## 8. Token Efficiency

*(Cross-cutting; rules apply to both tools and resources.)*

- `[8.concise-default]` **Default to a concise response.** Offer richer variants via an explicit detail toggle (e.g., `detail: "summary" | "full"`), not a free `response_format` parameter.
  See `examples.md` §2 for the concise-vs-detailed response pattern.

- `[8.detail-orthogonal]` **Detail is orthogonal to format.** Changing detail level changes the density of fields included, not the schema's shape.
  The same parser handles both modes.
  Make the concise fields a strict subset of the detailed fields — never rename a field between modes (a `preview` that becomes `text` in detail mode is two contracts, not one).

- `[8.detail-row-count]` **Detail toggles change field density, never row count.** A result that scales with a requested window or range needs its own default bound, independent of the detail level: a server-side cap with `truncated: true` and a hint naming a callable narrowing or aggregation parameter, or an aggregate default whose explicit raw mode is itself bounded or paginated.

- `[8.cursor-pagination]` **Use cursor-based pagination by default.** Offset-based pagination is acceptable only when ordering is stable and the result set is small enough that pages don't shift between calls.

- `[8.cursor-lifetime]` **Declare cursor lifetime and expiry recovery.** Cursors are state handles (§1): declare how long they stay valid, return a stable `cursor_expired` error with restart guidance when one lapses, and say whether a paginated walk sees a consistent snapshot or best-effort consistency — silent skips and duplicates mid-walk break agent planning.
  A cursor that encodes state rather than referencing it follows the §3 two-mode handle rule: integrity-protected, with authorization, expiry, and budget checks re-applied on every continuation.

- `[8.native-pagination]` **Native list methods use the protocol pagination shape, not a house convention.** `tools/list`, `resources/list`, `resources/templates/list`, and `prompts/list` accept an optional opaque `cursor` request param and return an optional `nextCursor` in the result; **absence of `nextCursor` signals completion**.
  There is no native `has_more`, `next_cursor`, `estimated_total`, or `limit` on these methods, and page size is server-selected — do not rename `nextCursor` to snake_case or bolt a house convention onto a native list.
  See [native-wire-shapes.md](native-wire-shapes.md).

- `[8.cacheable-results]` **Populate the native cache hints honestly.** `server/discover`, the four list methods, and `resources/read` MUST carry `ttlMs` (integer ≥ 0 milliseconds of freshness) and `cacheScope` (`"public"` or `"private"`) on every `resultType: "complete"` result.
  Choose the values from the data's actual volatility: `0` for surfaces that change per call, a real window for stable catalogs, and `"private"` whenever the response varies by authorization context (§2) — a `"public"` scope on an auth-scoped catalog leaks one principal's surface into another's shared cache.
  `ttlMs` is a freshness hint, not a polling schedule; the paging `cursor` is part of the cache key.
  Never cache the MRTR path: interim `input_required` results carry no hints, and a completed result produced by a retry carrying `inputResponses` or `requestState` MUST NOT be cached either — its content depends on inputs outside the cache key.

- `[8.house-pagination]` **A tool's own result payload MAY carry a documented house pagination convention.** When a `tools/call` result paginates domain data (not a native list method), `has_more` is acceptable: if `has_more` is true, include a navigation token (`next_cursor`) and, where available, `estimated_total`.
  These are declared domain output and belong in `structuredContent` under the tool's `outputSchema` so the agent can observe them — not under `_meta`, which clients may not surface to the model.
  Label them as convention, never as protocol fields.

- `[8.filters]` **Provide filters that meaningfully reduce response size.** `since=`, `query=`, `category=`, `field=`.
  Filters that don't change wire size are noise.

- `[8.truncation]` **Truncate explicitly with a repair hint.** `"truncated": true, "truncation_hint": "hit cap of 200; narrow with since= or query="`.
  Silent truncation breaks agent planning.
  Truncation bounds the result set; pagination continues it: `truncated` means items beyond a cap will never be returned by paging, so it is not a synonym for `has_more` — a response may legitimately carry both (more pages exist, and the set they page through is capped).

- `[8.identifier-roles]` **Choose identifiers by role.** Domain IDs with natural meaning can stay readable; state handles use opaque stable IDs with labels or summaries; security-sensitive references are opaque and never leak structure.

- `[8.per-capability-detail]` **Support per-capability detail levels.** Progressive disclosure applies to both definitions (in discovery, see §2) and responses.
  The agent should be able to ask for "summary" before "full" at every level.

- `[8.strip-nulls]` **Strip null and default-valued fields from concise responses.** Where they add no information, they cost tokens and clutter parsing; detail mode may include them for completeness.
  Exception: fields a contract marks always-present — such as the §6 error envelope's `retry_after_ms` — are emitted even when `null`.

- `[8.locale-independent]` **Use locale-independent wire values.** Timestamps are RFC3339 UTC, currency uses ISO-4217 plus minor units, sort keys are stable, and display localization stays out of machine fields.

### Anti-patterns

- `[8.ap-response-format]` **Free `response_format` toggles** (markdown vs. json vs. xml as parallel contracts on the same tool).
  Format proliferation creates ambiguous contracts: which format is authoritative?
  Which one carries error signals?
  Which gets versioned when the schema changes?
  Prefer one structured default with optional supplemental text or markdown rendering.

- `[8.ap-fixed-prose]` **Fixed prose attached to every response.** Never attach a constant explanatory block (usage notes, timezone conventions, format explanations) to every response envelope; state invariants once, in the capability summary or an `outputSchema` field description.
  Per-call metadata is compact, machine-readable, and present only when it varies per response or changes this response's interpretation.
  This does not touch contract-mandated always-present fields (such as the §6 envelope's `retry_after_ms`) — the anti-pattern is repeated prose, not required machine fields.
  Failure shape: a server wrapped every result in `{data, metadata}` with ~250 characters of identical prose, paid on every call of every wrapped tool.

Audit prompt: Could an agent complete a typical task on this server in a single context window, including discovery, calls, and one round of repair?

---

## 9. Versioning and Compatibility

- `[9.fingerprint]` **Publish a capability fingerprint when a target client caches or pins the server surface.**
  A versioned identity for the server's surface lets such a client detect drift cheaply, without re-walking discovery.
  It is a house convention whose value tracks the consumer: long-lived clients, caching clients, and code-execution clients that pin against the surface justify it; a server whose target clients always rediscover and never compare surface identity may omit it.
  The native obligations in this section — list-changed notifications, deterministic ordering, discoverable deprecation — are mandatory either way, and the remaining fingerprint rules below bind any server that does publish one.
  See `examples.md` §9 for fingerprint evolution across deprecation and removal.

- `[9.list-changed]` **Advertise and emit protocol-native list-changed notifications.** Declare the `listChanged` capability where supported, and emit `notifications/tools/list_changed`, `notifications/resources/list_changed`, and `notifications/prompts/list_changed` when the corresponding list changes.
  Delivery is opt-in: these notifications reach only clients that subscribed to the matching type (`toolsListChanged`, `resourcesListChanged`, `promptsListChanged`) on a `subscriptions/listen` stream, so pair them with an honest `ttlMs` (`[8.cacheable-results]`) that bounds staleness for clients that never listen.

- `[9.list-vs-update]` **Distinguish list changes from resource updates.** `listChanged` means the catalog changed; `notifications/resources/updated` means a subscribed resource's contents changed.
  Emit both only when both facts are true.

- `[9.deterministic-order]` **Keep list ordering deterministic.** `tools/list` and resource catalogs use stable ordering so clients can diff and cache predictably.
  This is now native guidance: the spec says servers SHOULD return `tools/list` in a deterministic order to enable client caching and stable upstream prompt caches.

- `[9.fingerprint-additive]` **Treat fingerprints as additive signals.** A capability fingerprint helps clients short-circuit discovery, but it does not replace native change notifications, native cache hints (`ttlMs`/`cacheScope`), or stable list ordering.

- `[9.fingerprint-coverage]` **The fingerprint covers the full agent-visible surface.** Tool definitions, resource catalogs, resource templates, prompt scaffolds, completion support, subscription behavior, negotiated-capability expectations, error codes, and the server capability summary.
  Anything an agent can plan against is part of the fingerprint input — tool descriptions included, because they are the primary input to tool selection (§3).
  Where a fingerprint is published, disclose its coverage in documented agent-readable metadata such as a `covers` field (see `examples.md` §9); the disclosure does not permit excluding any agent-visible surface.
  A separately named schema-only hash, if published, is per-capability diagnostic metadata; it must not be used to decide that discovery can be skipped, and it never narrows or replaces the fingerprint.

- `[9.deprecation-semantics]` **Define deprecation semantics.** Commit to how long a deprecated capability remains available before removal, and publish that window where agents and integrators can read it.
  Deprecation is a contract, not a sticky note: a marker with no committed window is an announcement, not a deprecation.
  The marker's own field set — including what replaces the capability — is `[9.deprecation-marker]`; state the window once here rather than restating the shape.

- `[9.deprecated-discoverable]` **Deprecated capabilities remain discoverable.** They continue to appear in discovery (see §2) until removal, with a deprecation marker and a pointer to the replacement.
  Silently dropping them breaks cached clients.

- `[9.additive-direction]` **Adding optional fields is additive by direction, not universally safe.** Removing or renaming fields, codes, or tools is a breaking change — bump the fingerprint where you publish one.
  Document the migration in the deprecation marker.
  Additions are not symmetric: a new optional *input* property is additive, because existing calls that omit it stay valid.
  A new *output* property is additive only for tolerant consumers — this skill closes object schemas with `additionalProperties: false` (§3), and clients SHOULD validate `structuredContent` against the published `outputSchema` (§3), so a client still holding a cached closed schema rejects the very field you added.
  Ship output additions behind a rediscovery signal: bump the fingerprint where you publish one, emit `notifications/tools/list_changed` for listening clients, and keep `ttlMs` short enough that non-listening cachers refetch before they see the new field.

- `[9.rename]` **Treat tool rename as remove-plus-add.** Renaming a tool is a discovery-surface change (see §2) — clients that cached the old surface will break silently otherwise.
  Keep the old name with a deprecation pointer for the documented window.
  A rename sweeps every agent-visible reference atomically with the alias window: repair hints (`repair.tool`), server `instructions`, the capability summary, prompts, and other tools' descriptions.

- `[9.stability-tiers]` **Declare stability tiers if used, from a closed set: `stable`, `preview`, `experimental`.** Mixing tiers without labels makes every capability look stable, which is worse than labeling some as risky.
  The set is closed because `stability` answers one question — how much the contract may still change — and agents filter on it directly.
  **Deprecation is an orthogonal axis and never a tier value.** A deprecated capability keeps the maturity tier it earned and carries the deprecation marker beside it (`[9.deprecation-marker]`), so `stability: "deprecated"` is not a legal value.
  An agent deciding whether to adopt a capability reads both fields: `stability` for churn risk, the deprecation marker for remaining life.

- `[9.deprecation-marker]` **The deprecation marker is one object with a fixed field set.** `since` (the version the deprecation took effect), `removal_at_or_after` (the earliest version the capability may disappear), `replaced_by` (the successor, or `null` when there is none), and `migration` (concrete prose telling an agent what to change).
  Presence of the marker *is* the deprecation signal; absence means not deprecated.
  `replaced_by` identifies the successor in the terms of the record it sits on — a tool `name`, a resource `uri`, a resource template `uriTemplate`, or a prompt `name` — because those identifier spaces are not interchangeable.
  This is a convention extension, not a native MCP structure.

- `[9.tier-metadata]` **Stability and deprecation ride each capability's own discovery record.** Each capability's tier (`[9.stability-tiers]`) and deprecation marker (`[9.deprecation-marker]`) are part of its discovery record so agents can filter by tier and spot a dying capability without a second lookup (see §2).
  Native `Tool`, `Resource`, `ResourceTemplate`, and `Prompt` records have no `stability` or `deprecation` field, so both ride under one namespaced `_meta` key — `<reverse-dns>/lifecycle` — on all four record types, per the native-vs-convention rule in `SKILL.md`.
  Publishing them only on a house surface such as a fingerprint or a `search_tools` envelope does not satisfy this rule: a client reading native `tools/list` would see no tier at all.
  Because `_meta` is a convention extension, an off-the-shelf client ignores it unless it was built to read that key — so the same facts stay available through the capability summary and the fingerprint where one is published.
  See `examples.md` §9a for the worked native record.

- `[9.error-codes]` **Error codes are part of the versioned surface (see §6).** Changing a code's meaning is a breaking change; introducing a new code is additive but still recorded in the fingerprint where one is published.

- `[9.fingerprint-format]` **The fingerprint format itself is stable.** Changing how the fingerprint is computed (hashing algorithm, included fields) is a breaking change for any client caching by fingerprint.

Audit prompt: If a client cached this server's surface yesterday, can it tell — from the fingerprint alone — whether anything it depends on changed?
