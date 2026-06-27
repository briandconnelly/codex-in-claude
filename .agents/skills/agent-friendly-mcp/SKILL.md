---
description: Use when designing, building, auditing, or reviewing an MCP server that AI agents will invoke directly. Symptoms include agents picking the wrong tool from many candidates, burning tokens loading hundreds of tool definitions upfront, repeated invalid tool calls due to ambiguous schemas, tools that mirror an underlying API endpoint-by-endpoint instead of completing tasks, missing or unclear resource browsing, prompts that duplicate tool contracts, token-heavy responses that should be paginated or filtered, brittle integrations that break across server versions, long-running operations with no progress, cancellation, or recoverable task status. Also use when defining or hardening tool, resource, or prompt schemas.
metadata:
    github-path: agent-friendly-mcp
    github-ref: refs/heads/main
    github-repo: https://github.com/briandconnelly/skills
    github-tree-sha: 7cbe114835f9d75940f0961e249c3b0dbd0ceb58
name: agent-friendly-mcp
---
# Agent-Friendly MCP

Use this skill to make MCP servers easy for agents to discover, invoke correctly, recover from, and use at low token cost without sacrificing correctness.

## Spec Baseline

This skill is written against the stable **MCP 2025-11-25** specification; the field names, capability paths, and task lifecycle it uses follow that revision.

A **2026-07-28 release candidate** is in flight — not yet ratified, and still subject to change before it finalizes.
It is expected to make the protocol stateless (removing the `initialize`/`initialized` handshake and per-session ids, with client info and capabilities traveling per request), move **tasks** from experimental core to a negotiated **extension**, deprecate **roots**, **sampling**, and **logging** on long retention windows, and formalize a reverse-DNS **extensions framework** that gives the convention metadata below an official home.
Treat init-time capability negotiation, native tasks, and roots as **likely migration points**: design against them today, but keep server logic able to absorb their restructuring, and branch on stable symbolic codes rather than numeric or transport-level details where you have the choice.
Revisit this skill when 2026-07-28 finalizes.

## Core Standard

- Tool and resource schemas are the operational contract; prompts are advisory scaffolding for orchestration. Do not hide essential behavior in prompts.
- Server-level `instructions` are advisory and may not be surfaced by every client.
  Do not put essential selection, prerequisite, safety, or repair behavior only in `instructions`.
- Optional MCP features only exist for an agent after protocol version and capability negotiation.
  Gate roots, completions, resource subscriptions, elicitation, list-change notifications, and tasks on the initialized capabilities.
- Optimize for the first successful tool call **and** the first successful repair from a cold start.
- Design around user/agent tasks, not the underlying API's endpoint surface.
- Make side effects, idempotency, rate limits, and agent-actionable prerequisites visible.
- Default to compact, deterministic, structured output; structured data is authoritative and text or markdown is supplemental rendering for human-facing clients.
- Provide explicit discovery primitives, but keep every definition compact: the least-capable realistic client preloads the whole catalog from `tools/list`, so compact schemas and concise descriptions are the universal baseline, and selective on-demand loading is a client-dependent optimization layered on top.
- Design for the least-capable realistic client: some preload tools, paginate discovery, ignore annotations, or expose resources poorly.

## Native Fields vs Convention Extensions

This skill is deliberately opinionated: native MCP fields alone are often insufficient for agent-friendliness, so well-designed servers add convention extensions such as structured `errors`, `repair` hints, a capability `fingerprint`, prompt prerequisites, and detail toggles.
Keep them — but never let them masquerade as protocol.

- **Preserve native MCP field names and casing exactly; prefer `snake_case` for house/domain fields.**
  A field's provenance is determined by the MCP type that contains it, **not** by its casing — native `_meta` carries an underscore, `name`/`code`/`repair` are lowercase on both sides, and a convention object may hold a `mimeType`-style name. So casing is a preference for house fields, never a test for whether a field is protocol.
  Tool: `name`, `title`, `description`, `icons`, `inputSchema`, `outputSchema`, `annotations`, `execution`, `_meta`.
  Resource: `uri`, `name`, `title`, `description`, `mimeType`, `size`, `icons`, `annotations`, `_meta`.
  Resource template: `uriTemplate`, `name`, `title`, `description`, `mimeType`, `icons`, `annotations`, `_meta`.
  Prompt: `name`, `title`, `description`, `icons`, `arguments`, `_meta`.
  Implementation: `name`, `title`, `version`, `description`, `icons`, `websiteUrl`.
- Put convention metadata under a namespaced `_meta` key (e.g., `com.example/chunks`) — the spec-sanctioned extension point — so it cannot collide with future MCP fields.
- Label every convention extension as such where it appears, so a reader can tell protocol from house style.
- The examples in this skill keep some conventions inline at the top level for readability; production servers SHOULD namespace convention metadata under `_meta`. See `examples.md` §3/§4 for the worked `_meta` pattern.
- For the exact native request/response envelopes, field names, and casing of the methods most often confused with house conventions — list pagination, completion, the `tools/call` result, and the task lifecycle — see [native-wire-shapes.md](references/native-wire-shapes.md).

## When To Use

- Designing a new MCP server that agents (Claude Code, Codex, custom agents) will invoke.
- Defining or hardening tool, resource, or prompt schemas for an existing server.
- Auditing an existing MCP server for agent-friendliness.
- Diagnosing concrete agent failures: wrong-tool selection from many candidates, repeated invalid tool calls, token waste from upfront definition loading, endpoint-mirroring tools that force long chains, broken cross-server upgrades.
- Designing long-running work: progress notifications, cancellation, task-augmented requests, and long-running operation patterns (see [contract-checklist.md](references/contract-checklist.md) §7 and [examples.md](references/examples.md) §11).

## When Not To Use

- General code review of MCP server internals that does not face agents — use your normal code-review workflow.
- Library or SDK design that is not exposed via MCP — this skill is MCP-specific.
- Trivial schema additions to an already agent-friendly server; just follow the existing contract.
- Out of scope: sampling, server logging streams, server-operator dashboards, packaging/deployment, and skills-over-MCP (experimental at https://github.com/modelcontextprotocol/experimental-ext-skills — revisit when stable).
  Elicitation is in scope only as an agent-facing contract boundary: when a server needs missing user input, confirmation, or sensitive external interaction, declare whether it supports MCP elicitation and how non-elicitation clients recover.
  Do not use this skill for designing full user-experience flows.

## Vocabulary

Shared terms — discovery surface, repair signal, state handle, capability fingerprint, negotiated capability, task-capable tool, and the rest — are defined in [vocabulary.md](references/vocabulary.md); consult it when a term in the checklist or workflows is unfamiliar.

## Checklist Map

The normative standard lives in [contract-checklist.md](references/contract-checklist.md); walk it top to bottom for any design or review.
This index orients and routes — it does not restate the rules. State-handle discipline and long-running-operation contracts are normative in §1/§8 and §7 respectively; consult them there rather than a second copy here.
Notation: bare `§N` always means a contract-checklist section; `ex§N` means section N of [examples.md](references/examples.md).

| § | Section | One-line rule | Worked examples |
| --- | --- | --- | --- |
| §1 | Server-Level | Identity, transport, auth modes, agent-actionable prerequisites, negotiated capabilities, and roots — learnable in one read. State handles are declared here: opaque IDs, lifetime, expiry, auth on every use. | ex§7, ex§8a |
| §2 | Discovery | A capability summary plus compact definitions as the universal baseline; progressive disclosure is a client-dependent optimization — pick a mechanism by cost axis (host-managed context, server-managed catalog, or client-independent surface reduction). | ex§7, ex§8 |
| §3 | Tools | Task-completing tools over endpoint mirrors; strict closed schemas; honest annotations; failure paths are contract, not prose. | ex§1, ex§2, ex§10, ex§12, ex§13 |
| §4 | Resources | Stable hierarchical URIs; index before body; stable chunk ids; templates + completion; subscriptions for mutable resources. | ex§3, ex§4, ex§5a, ex§5b |
| §5 | Prompts | Advisory orchestration scaffolding only — reference tools by name, never redefine their contract. | ex§5 |
| §6 | Failure Recovery | Stable symbolic codes, field-level feedback, explicit retryability, repair hints naming real callable surfaces. | ex§6 |
| §7 | Long-Running Operations | Choose blocking / progress / task-augmented deliberately; declare duration and timeout; recover via the native task lifecycle with a labeled fallback. | ex§11 |
| §8 | Token Efficiency | Concise default with a `detail` toggle; native list methods paginate with `nextCursor` (omission = done), while a tool's own result payload may use a documented `has_more` convention; explicit truncation with a repair hint; identifiers chosen by role. | ex§2 |
| §9 | Versioning | Publish a capability fingerprint; deterministic list ordering; native list-changed notifications; discoverable deprecation. | ex§9 |

## Workflow

1. Classify the task: new MCP server or redesign vs review of an existing one.
2. For new design or redesign, follow [design-workflow.md](references/design-workflow.md).
3. For an audit, follow [review-workflow.md](references/review-workflow.md); severity scale and report format live there.
4. Use [contract-checklist.md](references/contract-checklist.md) as the detailed standard for both workflows.
5. Use [mcp-vs-cli.md](references/mcp-vs-cli.md) if deciding which surface to expose; use [examples.md](references/examples.md) for concrete schema, response, error, and discovery shapes.
6. Once the contract is designed, implement it with an MCP SDK — e.g. FastMCP for Python or the official TypeScript SDK. This skill defines the agent-facing wire contract, not the framework; if a FastMCP (or equivalent SDK) skill is available in your environment, use it for implementation specifics.

## Done Criteria

Before declaring done, walk [contract-checklist.md](references/contract-checklist.md) against your output.

- **Design tasks**: every checklist section must have an answer in the schema set or be explicitly marked not-applicable with a one-line justification.
- **Review tasks**: every checklist section is either covered by a finding, marked `OK` with brief evidence, or noted `not-checked` with reason. Use the severity scale and report format defined in [review-workflow.md](references/review-workflow.md).
