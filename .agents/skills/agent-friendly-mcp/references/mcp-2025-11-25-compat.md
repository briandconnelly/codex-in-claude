# MCP 2025-11-25 Compatibility Notes (informative)

**This file is informative, never normative.**
Every binding rule lives in [contract-checklist.md](contract-checklist.md) under its stable id; this file only catalogs how the deprecated 2025-11-25 revision differs on the wire and how version mismatch fails, for servers that must interoperate with older clients during the twelve-month deprecation window.
The rebase decision, verified fact sheet, and impact matrix live in `decisions/001-mcp-2026-07-28-rebase.md`.

## How an old client fails against a 2026-07-28 server

These failures happen before any tool is invoked, so diagnose them at the protocol layer, not in tool contracts:

| Old-client behavior | 2026-07-28 outcome |
| --- | --- |
| Sends `initialize` / expects `notifications/initialized` | Modern-only servers fail it as an unknown method / malformed request (implementation-specific on stdio; HTTP 400 for missing required headers); dual-era servers MAY still serve it. `-32022` `UnsupportedProtocolVersion` is reserved for a *modern* request declaring a version the server does not support — a modern client seeing any other error treats the server as legacy |
| Omits per-request `_meta` (`protocolVersion`, `clientCapabilities`) | Malformed request: `-32602`, HTTP 400 |
| Expects `Mcp-Session-Id` continuity | No sessions; cross-call state must be explicit handles in arguments |
| Omits `Mcp-Method`/`Mcp-Name` HTTP headers | `HeaderMismatch` (`-32020`) |
| Calls `resources/subscribe` / `tasks/result` / `tasks/list` / `ping` / `logging/setLevel` | Method not found (`-32601`) |
| Expects server-initiated `elicitation/create` / `sampling/createMessage` / `roots/list` | Never arrives; 2026-07-28 servers deliver these inside `InputRequiredResult.inputRequests` |
| Reads results without `resultType` awareness | Harmless downgrade: pre-2026 clients treat everything as a final result, so never send them `input_required` interim results |

## Wire-shape differences (2025-11-25 → 2026-07-28)

| Surface | 2025-11-25 | 2026-07-28 |
| --- | --- | --- |
| Capability exchange | `initialize` handshake, per-session | Per-request `_meta` + `server/discover` (`[1.negotiated-caps]`) |
| Server `instructions` | Carried on the `initialize` result | Optional field on the `server/discover` result (`[2.discover-first]`) |
| Results | No `resultType` | Required `resultType` (`complete` / `input_required` / extension values) |
| List/read results | No cache fields | Required `ttlMs` + `cacheScope` (`[8.cacheable-results]`) |
| Resource not found | `-32002` | `-32602` (still accept `-32002` from old servers) |
| URL elicitation gate | `-32042` `URLElicitationRequiredError`, `elicitationId`, `notifications/elicitation/complete` | All removed; MRTR `inputRequests` + `requestState` (`[6.elicitation]`) |
| Subscriptions | `resources/subscribe` / `unsubscribe` + HTTP GET stream | `subscriptions/listen` with per-type filters (`[4.subscriptions]`) |
| Tasks | Experimental core: client opts in per request (`task` param), `execution.taskSupport` per tool, `server.capabilities.tasks.requests.tools.call`, `tasks/result` (blocking) + `tasks/list`, fields `ttl`/`pollInterval`, `CreateTaskResult` nests `result.task`, `_meta` keys `related-task`/`model-immediate-response`, tool `isError` ⇒ task `failed` | Extension `io.modelcontextprotocol/tasks`: server-directed creation, `resultType: "task"` inline, `tasks/get`/`tasks/update`/`tasks/cancel` (cancel acks empty; cooperative), fields `ttlMs`/`pollIntervalMs`, tool `isError` ⇒ task `completed` (§7) |
| Task input | Preemptive `tasks/result` hold; input arrives as separate `elicitation/create` | `tasks/get` carries `inputRequests`; answer via `tasks/update` (`[7.input-required]`) |
| Roots | `roots/list` server request + `notifications/roots/list_changed` | Deprecated; MRTR `ListRootsRequest` for declaring clients (`[1.roots]`) |
| Error code renumbering | `HeaderMismatch` `-32001`, `MissingRequiredClientCapability` `-32003`, `UnsupportedProtocolVersion` `-32004` | `-32020` / `-32021` / `-32022` |

The most treacherous difference is the inverted `failed` coupling: a 2025-11-25 agent reads task `failed` as "tool errored," while 2026-07-28 reserves `failed` for JSON-RPC faults and delivers tool errors inside `completed` results — see `[7.failed-task]` for the binding rule and the disclosure it requires.

## Retired rule ids

None: every rule id survived the 2026-07-28 rebase because each rule's concern survived its mechanism change (see `decisions/001-mcp-2026-07-28-rebase.md`, "Rule-id retirement").
`[8.cacheable-results]` was added.
If a future revision retires an id, record it here as `id → replacement or removal rationale`.
