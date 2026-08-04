---
description: Use when designing, building, auditing, or reviewing an MCP server that AI agents will invoke directly. Symptoms include agents picking the wrong tool from many candidates, burning tokens loading hundreds of tool definitions upfront, repeated invalid tool calls due to ambiguous schemas, tools that mirror an underlying API endpoint-by-endpoint instead of completing tasks, missing or unclear resource browsing, prompts that duplicate tool contracts, token-heavy responses that should be paginated or filtered, brittle integrations that break across server versions, long-running operations with no progress, cancellation, or recoverable task status. Also use when defining or hardening tool, resource, or prompt schemas.
metadata:
    github-path: agent-friendly-mcp
    github-ref: refs/heads/main
    github-repo: https://github.com/briandconnelly/skills
    github-tree-sha: a07572d20ad77d5cc674f623638b0fb5ae3acd77
name: agent-friendly-mcp
---
# Agent-Friendly MCP

Use this skill to make MCP servers easy for agents to discover, invoke correctly, recover from, and use at low token cost without sacrificing correctness.

## Spec Baseline

This skill is written against the **MCP 2026-07-28** specification (final, released 2026-07-28); the field names, capability paths, method set, and task lifecycle it uses follow that revision, including the `io.modelcontextprotocol/tasks` extension (SEP-2663).
Status caveat: the 2026-07-28 changelog calls tasks an official extension while the ext-tasks specification repository still labels itself experimental — treat the task contract as extension-versioned and re-verify it against the extension spec when it cuts a release.
The protocol core is stateless: there is no `initialize` handshake or session id — every request carries `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` in `_meta` (both required), and servers advertise their own capabilities via the mandatory `server/discover` method.
Server-initiated requests are gone: elicitation, sampling, and roots requests ride Multi Round-Trip Requests (`resultType: "input_required"` with `inputRequests`, answered by retrying with `inputResponses`), and push notifications ride an opt-in `subscriptions/listen` stream.
Every result carries a required `resultType`, and list/read results carry the native cache hints `ttlMs` and `cacheScope`.
Roots, sampling, logging, the HTTP+SSE transport, and Dynamic Client Registration are deprecated on a twelve-month minimum window; design new servers without them.
The extensions framework is formal but demanding: an extension needs a reverse-DNS identifier, its own maintained specification, and negotiation by both peers (declared in `clientCapabilities.extensions` and `server/discover`), so a namespaced `_meta` key remains a private convention until someone does that work.
This section is the single home for spec-revision facts; other files cite it rather than restating them.
For clients that still speak **2025-11-25**, the binding rules here still govern what you build; the old revision's wire differences and version-bound failure modes are cataloged in the informative [mcp-2025-11-25-compat.md](references/mcp-2025-11-25-compat.md), and `decisions/001-mcp-2026-07-28-rebase.md` records the rebase decision, verified fact sheet, and impact matrix.

## Where The Recurring Concerns Live

A routing table, not a summary.
Each row names a concern this skill keeps returning to and the rules that govern it; the rules themselves are stated only in [contract-checklist.md](references/contract-checklist.md).
Read the cited ids — nothing here restates them, so a row is never a substitute for the rule.

| Concern | Governing rules |
| --- | --- |
| What carries the contract, and what is only advisory | `[5.scaffolding-only]`, `[5.ap-contract-container]`, `[2.instructions-advisory]` |
| Capability negotiation before optional features | `[1.negotiated-caps]` |
| Tool granularity: tasks vs endpoints | `[3.task-completing]`, `[3.hide-steps]`, `[3.ap-endpoint-wrapping]` |
| Side effects, idempotency, and rate limits | `[3.declare-effects]`, `[3.honest-annotations]`, `[3.annotation-defaults]` |
| Prerequisites and implicit state an agent can act on | `[1.implicit-state]`, `[2.summary]` |
| Structured vs rendered output | `[3.structured-default]`, `[3.output-schema]` |
| Designing for the least-capable realistic client | `[2.compact-baseline]`, `[2.progressive-disclosure]`, `[2.client-variance]` |
| Failure paths as contract | `[3.failure-contract]`, `[6.symbolic-codes]`, `[6.repair-object]` |

Cold-start first-call and first-repair success are the outcome measures this skill is tuned against: [design-workflow.md](references/design-workflow.md) Step 8 builds the measurement and Step 9 owns the regression gate.

## Native Fields vs Convention Extensions

This skill is deliberately opinionated: native MCP fields alone are often insufficient for agent-friendliness, so well-designed servers add convention extensions such as structured `errors`, `repair` hints, a capability `fingerprint`, prompt prerequisites, and detail toggles.
Keep them — but never let them masquerade as protocol.

- **Preserve native MCP field names and casing exactly; prefer `snake_case` for house/domain fields.**
  A field's provenance is determined by the MCP type that contains it, **not** by its casing — native `_meta` carries an underscore, `name`/`code`/`repair` are lowercase on both sides, and a convention object may hold a `mimeType`-style name.
  So casing is a preference for house fields, never a test for whether a field is protocol.
  Tool: `name`, `title`, `description`, `icons`, `inputSchema`, `outputSchema`, `annotations`, `_meta`.
  Resource: `uri`, `name`, `title`, `description`, `mimeType`, `size`, `icons`, `annotations`, `_meta`.
  Resource template: `uriTemplate`, `name`, `title`, `description`, `mimeType`, `icons`, `annotations`, `_meta`.
  Prompt: `name`, `title`, `description`, `icons`, `arguments`, `_meta`.
  Implementation: `name`, `title`, `version`, `description`, `icons`, `websiteUrl`.
- Put convention metadata under a namespaced `_meta` key (e.g., `com.example/chunks`) — the spec-sanctioned extension point — so it cannot collide with future MCP fields.
- Label every convention extension as such where it appears, so a reader can tell protocol from house style.
- The primary example blocks in this skill are wire-valid: convention metadata rides under a namespaced `_meta` key, never as a top-level field on a native record.
  See `examples.md` ex§1/ex§4/ex§5 for the worked `_meta` pattern; the few deliberately abbreviated blocks (e.g. ex§10) carry an explicit non-wire label.
- For the exact native request/response envelopes, field names, and casing of the methods most often confused with house conventions — per-request `_meta`, list pagination and cache hints, completion, the `tools/call` result, MRTR, subscriptions, HTTP routing headers, and the tasks-extension lifecycle — see [native-wire-shapes.md](references/native-wire-shapes.md).

## When To Use

- Designing a new MCP server that agents (Claude Code, Codex, custom agents) will invoke.
- Defining or hardening tool, resource, or prompt schemas for an existing server.
- Auditing an existing MCP server for agent-friendliness.
- Diagnosing concrete agent failures: wrong-tool selection from many candidates, repeated invalid tool calls, token waste from upfront definition loading, endpoint-mirroring tools that force long chains, broken cross-server upgrades.
- Designing long-running work: progress notifications, cancellation, tasks via the negotiated extension, and long-running operation patterns (see [contract-checklist.md](references/contract-checklist.md) §7 and [examples.md](references/examples.md) §11).

## When Not To Use

- General code review of MCP server internals that does not face agents — use your normal code-review workflow.
- Library or SDK design that is not exposed via MCP — this skill is MCP-specific.
- Trivial schema additions to an already agent-friendly server; just follow the existing contract.
- Out of scope: sampling and logging (both deprecated in 2026-07-28), server-operator dashboards, packaging/deployment, and skills-over-MCP (now a named extension in the formal extensions framework — revisit if it enters this skill's scope).
  Elicitation is in scope only as an agent-facing contract boundary; the binding rules live in [contract-checklist.md](references/contract-checklist.md) §1 (declare the dependency on the client's `elicitation` modes) and §6 (elicitation use and the non-elicitation fallback).
  Do not use this skill for designing full user-experience flows.

## Vocabulary

Shared terms — discovery surface, repair signal, state handle, capability fingerprint, negotiated capability, task-capable tool, and the rest — are defined in [vocabulary.md](references/vocabulary.md); consult it when a term in the checklist or workflows is unfamiliar.

## Checklist Map

The normative standard lives in [contract-checklist.md](references/contract-checklist.md); walk it top to bottom for any design or review.
This index orients and routes — it does not restate the rules.
State-handle discipline and long-running-operation contracts are normative in §1/§8 and §7 respectively; consult them there rather than a second copy here.
Notation: bare `§N` always means a contract-checklist section; `ex§N` means section N of [examples.md](references/examples.md).
A single rule is cited by its stable id, `` `[section.slug]` `` — `[3.naming]`, `[6.repair-object]` — which resolves to exactly one bullet in contract-checklist.md and survives rewording of that bullet.
Prefer a rule id over a section reference when you mean one specific rule; `tests/check_rule_ids.py` fails the build if a cited id does not resolve.

| § | Section | One-line rule | Worked examples |
| --- | --- | --- | --- |
| §1 | Server-Level | Identity, transport (including required HTTP routing headers), auth modes, agent-actionable prerequisites, per-request capability declaration, and workspace scope — learnable in one read. State handles are declared here: opaque IDs, lifetime, expiry, auth on every use. | ex§7, ex§8a |
| §2 | Discovery | A capability summary plus compact definitions as the universal baseline; progressive disclosure is a client-dependent optimization — pick a mechanism by cost axis (host-managed context, server-managed catalog, or client-independent surface reduction). | ex§7, ex§8 |
| §3 | Tools | Task-completing tools over endpoint mirrors; strict closed schemas; honest annotations; failure paths are contract, not prose. | ex§1, ex§2, ex§2a, ex§10, ex§12, ex§13 |
| §4 | Resources | Stable hierarchical URIs; index before body; stable chunk ids; templates + completion; subscriptions for mutable resources. | ex§3, ex§4, ex§5a, ex§5b |
| §5 | Prompts | Advisory orchestration scaffolding only — reference tools by name, never redefine their contract. | ex§5 |
| §6 | Failure Recovery | Stable symbolic codes, field-level feedback, explicit retryability, repair hints naming real callable surfaces. | ex§6 |
| §7 | Long-Running Operations | Choose blocking / progress / task-augmented deliberately; declare duration and timeout; recover via the native task lifecycle with a labeled fallback. | ex§11 |
| §8 | Token Efficiency | Concise default with a `detail` toggle; native list methods paginate with `nextCursor` (omission = done) and carry honest `ttlMs`/`cacheScope`, while a tool's own result payload may use a documented `has_more` convention; explicit truncation with a repair hint; identifiers chosen by role. | ex§2 |
| §9 | Versioning | Publish a capability fingerprint where target clients cache or pin the surface; deterministic list ordering; native list-changed notifications; discoverable deprecation. | ex§9 |

## Workflow

1. Classify the task: new MCP server or redesign vs review of an existing one.
2. For new design or redesign, follow [design-workflow.md](references/design-workflow.md).
3. For an audit, follow [review-workflow.md](references/review-workflow.md); severity scale and report format live there.
4. Use [contract-checklist.md](references/contract-checklist.md) as the detailed standard for both workflows.
5. Use [mcp-vs-cli.md](references/mcp-vs-cli.md) if deciding which surface to expose; use [examples.md](references/examples.md) for concrete schema, response, error, and discovery shapes.
6. When writing or auditing prose surfaces — server `instructions`, the capability summary, tool and resource descriptions — apply the rules-then-context discipline in [contract-checklist.md](references/contract-checklist.md) §2/§3/§4; if a separating-context-from-constraints skill is available in your environment, use it as the audit lens for those surfaces.
7. Once the contract is designed, implement it with an MCP SDK — e.g. FastMCP for Python or the official TypeScript SDK.
   This skill defines the agent-facing wire contract, not the framework; if a FastMCP (or equivalent SDK) skill is available in your environment, use it for implementation specifics.

## Done Criteria

Before declaring done, walk [contract-checklist.md](references/contract-checklist.md) against your output.

- **Design tasks**: every checklist section must have an answer in the schema set or be explicitly marked not-applicable with a one-line justification.
- **Review tasks**: every checklist section is either covered by a finding, marked `OK` with brief evidence, or noted `not-checked` with reason.
  Use the severity scale and report format defined in [review-workflow.md](references/review-workflow.md).
