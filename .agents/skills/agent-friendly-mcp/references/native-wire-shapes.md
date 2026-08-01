# Native Wire Shapes

Exact native MCP request/response envelopes, field names, and casing for the methods most often confused with house conventions.
Use it to keep native examples spec-faithful and to tell at a glance whether a field is protocol or convention.
The MUST/MUST NOT language here restates the *protocol's* obligations for reference; the skill's own binding rules live only in `contract-checklist.md`, and rows that touch one cite its rule id.

**Baseline:** **MCP 2026-07-28** (see `SKILL.md` → Spec Baseline; 2025-11-25 differences are cataloged in [mcp-2025-11-25-compat.md](mcp-2025-11-25-compat.md)).
Authoritative schema and pagination rules: <https://modelcontextprotocol.io/specification/2026-07-28>.

**Not exhaustive.** This covers the high-risk native shapes — per-request metadata, list pagination and cache hints, `resources/read`, `prompts/get`, completion, the `tools/call` result envelope, MRTR, subscriptions, HTTP routing headers, and the tasks extension lifecycle — not the full protocol surface.
For methods not listed here, read the spec rather than inferring from these rows.
Convention extensions (`errors`, `repair`, `fingerprint`, `has_more`/`next_cursor` in a tool's own payload, etc.) are **not** shown as native fields; see the native-vs-convention rule in `SKILL.md`.

## Per-request metadata (every request)

There is no initialize handshake; each request is self-describing via reserved `_meta` keys in `params`:

```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": { "name": "search", "arguments": { "q": "otters" },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": { "elicitation": { "form": {} } },
      "io.modelcontextprotocol/clientInfo": { "name": "my-app", "version": "1.0" } } } }
```

- `protocolVersion` and `clientCapabilities` are **required** on every request; a request missing either is malformed (`-32602`, HTTP 400).
- `clientInfo` is SHOULD; servers SHOULD return `io.modelcontextprotocol/serverInfo` in each result's `_meta`.
  Both are self-reported, for display and debugging — never security inputs.
- Servers advertise their own capabilities via the mandatory `server/discover` method; clients MAY call it up front.
- A server MUST NOT rely on an undeclared capability: the native failure is `MissingRequiredClientCapability` (`-32021`) with `data.requiredCapabilities`.

## Results — `resultType` (every result)

Every result carries a required `resultType`: `"complete"` for ordinary results, `"input_required"` for MRTR interim results, and extension values such as the tasks extension's `"task"`.
An absent `resultType` (a pre-2026 server) MUST be read as `"complete"`; an unrecognized value is invalid.

## List pagination and cache hints (shared envelope)

`tools/list`, `resources/list`, `resources/templates/list`, and `prompts/list` share one native pagination shape.

Request — `params` carries the required `_meta` (above) and may carry an opaque `cursor`:

```json
{ "jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": { "cursor": "<opaque>", "_meta": { "...": "..." } } }
```

Result — a method-specific array key, an **optional** `nextCursor`, and the **required** `resultType` and cache hints:

```json
{ "jsonrpc": "2.0", "id": "1", "result": { "resultType": "complete", "tools": [], "nextCursor": "<opaque>", "ttlMs": 60000, "cacheScope": "private" } }
```

| Method | Result array key | Element type |
| --- | --- | --- |
| `tools/list` | `tools` | `Tool` |
| `resources/list` | `resources` | `Resource` |
| `resources/templates/list` | `resourceTemplates` | `ResourceTemplate` |
| `prompts/list` | `prompts` | `Prompt` |

Rules that examples must not contradict:

- Casing is `nextCursor` (camelCase).
  The cursor is **opaque** — do not decode or construct it — and it is part of the cache key.
- **Absence of `nextCursor` means the list is complete.** There is no native `has_more`, `next_cursor`, `estimated_total`, `total`, or `limit` on these methods, and page size is server-selected.
- `ttlMs` (integer ≥ 0 milliseconds) and `cacheScope` (`"public" | "private"`) are **required** on `resultType: "complete"` results of these four methods, **`resources/read`, and `server/discover`** (the `CacheableResult` fields, SEP-2549).
  `0` means immediately stale; the hint is not a polling schedule; a completed result produced by an MRTR retry (`inputResponses`/`requestState`) is never cacheable (`[8.cacheable-results]`).
- Servers SHOULD return `tools/list` in a deterministic order.
- A tool's *own* result payload (the body of a `tools/call` result, not a list method) MAY use a documented house pagination convention such as `has_more` / `next_cursor` / `estimated_total`.
  That is convention, lives in the tool's `structuredContent` under its `outputSchema`, and must be labeled as such — see §8 of `contract-checklist.md`.

## Resource read — `resources/read`

Request `params` carry the required `_meta` (above) plus the resource `uri`:

```json
{ "jsonrpc": "2.0", "id": "2", "method": "resources/read", "params": { "uri": "slack://channels/C0123ABCD", "_meta": { "...": "..." } } }
```

Result — the required `resultType`, the required cache hints, and a `contents` array:

```json
{ "jsonrpc": "2.0", "id": "2", "result": { "resultType": "complete", "ttlMs": 60000, "cacheScope": "private",
    "contents": [ { "uri": "slack://channels/C0123ABCD", "mimeType": "application/json", "text": "{\"channel_id\":\"C0123ABCD\"}" } ] } }
```

- `ReadResourceResult` is a `CacheableResult`, so `ttlMs` and `cacheScope` are **required** here exactly as on the four list methods.
- `contents` is **required** and is an array — a single resource read returns a list, and each element is a `TextResourceContents` (adds `text`) or a `BlobResourceContents` (adds base64 `blob`).
  Both share `uri` (required), `mimeType` (optional), and `_meta` (optional).
  There is no native `content`, `body`, `data`, or `chunks` field on a resource read.
- A read MAY answer with an MRTR interim result instead (`resultType: "input_required"`), so a client must branch on `resultType` before reading `contents`.
- House chunking, versioning, and next-chunk pointers are convention: they live inside the `text` payload under the resource's own documented shape, or under a namespaced `_meta` key — see `examples.md` ex§4.

## Prompt get — `prompts/get`

Request `params` carry the required `_meta`, the prompt `name`, and optional `arguments`:

```json
{ "jsonrpc": "2.0", "id": "3", "method": "prompts/get", "params": { "name": "release_announcement",
    "arguments": { "channels": "C0123ABCD,C0456EFGH", "pin": "true" }, "_meta": { "...": "..." } } }
```

Result:

```json
{ "jsonrpc": "2.0", "id": "3", "result": { "resultType": "complete", "description": "Draft a release announcement.",
    "messages": [ { "role": "user", "content": { "type": "text", "text": "Announce v2.4.1 in the listed channels." } } ] } }
```

- **`arguments` on the wire is a map of string to string** (`{ [key: string]: string }`) and is optional.
  There are no arrays, booleans, numbers, or nested objects in a `prompts/get` call, so a prompt argument that means a list or a flag has to document its **encoding** — see `examples.md` ex§5 for the worked shape.
- This is distinct from `PromptArgument`, which is the **discovery-record** type on `Prompt.arguments` (`prompts/list`): `name` (required), `title`, `description`, `required`.
  `PromptArgument` has no `type`, `items`, `default`, or `enum` — it is not a JSON Schema, and the value shape belongs in its `description`.
- `GetPromptResult` is **not** a `CacheableResult`: `messages` is required, `description` is optional, and there are no `ttlMs`/`cacheScope` fields — unlike `resources/read` above.
- `messages[].role` is `"user" | "assistant"` only (no `"system"`), and `content` is a single `ContentBlock`, not an array.
- `prompts/get` MAY also answer with an MRTR interim result.

## Completion — `completion/complete`

Gated on the server's `completions` capability (visible via `server/discover`).
The result nests under `completion`:

```json
{ "jsonrpc": "2.0", "id": "1", "result": { "resultType": "complete", "completion": { "values": ["C0123DEPLOYS", "C0456DEPLOYOPS"], "total": 2, "hasMore": false } } }
```

- Native fields: `values` (required), `total` (optional), `hasMore` (optional, camelCase).
- This `hasMore` is the **native completion field** — distinct from the house `has_more` pagination convention, and not a counterexample to the casing rule.

## Tool result — `tools/call`

```json
{ "jsonrpc": "2.0", "id": "1", "result": { "resultType": "complete", "content": [], "structuredContent": {}, "isError": false, "_meta": {} } }
```

- Native fields: `resultType` (required), `content` (array of content blocks), `structuredContent` (paired with the tool's `outputSchema`), `isError` (boolean), `_meta`.
- `structuredContent` may be **any JSON value** under 2026-07-28 (SEP-2106), not only an object; this skill still defaults to an object shape (`[3.structured-default]`).
- **Declared domain output — including house pagination fields — belongs in `structuredContent`** validated by `outputSchema`, so the agent can observe it.
  Only auxiliary house metadata belongs under a namespaced `_meta` key (e.g. `com.example/chunks`); `_meta` may not be surfaced to the model.
- `errors` is **not** a native `tools/call` or `Tool` field.
  Documented error catalogs are a convention extension (see `examples.md` §1).

## MRTR — `InputRequiredResult`

Servers MAY answer `tools/call`, `resources/read`, and `prompts/get` — no other methods — with an interim result instead of a final one:

```json
{ "jsonrpc": "2.0", "id": 1, "result": { "resultType": "input_required",
    "inputRequests": { "confirm-1": { "method": "elicitation/create", "params": { "mode": "form", "message": "…", "requestedSchema": { "...": "..." } } } },
    "requestState": "<opaque server blob>" } }
```

- At least one of `inputRequests` / `requestState` MUST be present.
- `inputRequests` values are `ElicitRequest`, `CreateMessageRequest`, or `ListRootsRequest` shapes, keyed by server-assigned ids unique within the request — and MUST NOT include kinds or elicitation modes the client's `clientCapabilities` did not declare.
- The client retries the **original request** (new JSON-RPC id) with `inputResponses` keyed by the same ids, echoing `requestState` verbatim.
  `requestState` is opaque to clients and attacker-controlled input to servers: integrity-protect it (HMAC/AEAD) whenever it influences authorization or business logic.
- MRTR interim results are never cacheable.

## Subscriptions — `subscriptions/listen`

One long-lived request opens a notification stream; the client opts into types via the `notifications` filter:

```json
{ "jsonrpc": "2.0", "id": 7, "method": "subscriptions/listen", "params": { "notifications": {
    "toolsListChanged": true, "resourcesListChanged": true,
    "resourceSubscriptions": ["file:///project/config.json"] }, "_meta": { "...": "..." } } }
```

- Filter types: `toolsListChanged`, `promptsListChanged`, `resourcesListChanged` (booleans), `resourceSubscriptions` (array of resource URIs — this replaces `resources/subscribe`), and — via the tasks extension — `taskIds` (array of task ids for `notifications/tasks`; the server's acknowledgement reports the accepted subset).
- Every notification on the stream carries `_meta.io.modelcontextprotocol/subscriptionId` equal to the listen request's JSON-RPC id, for demultiplexing.
- Request-scoped notifications (`notifications/progress`, `notifications/message`) do **not** ride this stream; they stay on the originating request's response stream.
- Cancel the stream with `notifications/cancelled` referencing the listen request id.

## Streamable HTTP headers

Three required headers, each with its own applicability (`[1.transport]` owns the obligation):

| Header | Required on | Value |
| --- | --- | --- |
| `MCP-Protocol-Version` | every POST | the protocol version; MUST match the body's `_meta` `protocolVersion` |
| `Mcp-Method` | every request | the JSON-RPC `method` |
| `Mcp-Name` | `tools/call`, `resources/read`, `prompts/get` — and, via the tasks extension, `tasks/get`/`tasks/update`/`tasks/cancel` | `params.name` or `params.uri`; for task methods the `taskId` (routes to the instance holding task state) |

A header/body mismatch is `HeaderMismatch` (`-32020`); non-ASCII `Mcp-Name` values use the spec's Base64 sentinel encoding.
Tools may pass custom headers via the `x-mcp-header` parameter convention (SEP-2243).

## Tasks (extension — `io.modelcontextprotocol/tasks`)

Negotiated per request: the client declares the extension in `clientCapabilities.extensions`, the server advertises it via `server/discover`.
Task creation is **server-directed**: any supported request may answer with a `CreateTaskResult` instead of its standard result; the client never asks for one, and a server MUST NOT send one to a client that did not declare the extension.
Native casing throughout: `taskId`, `status`, `statusMessage` (optional), `createdAt`, `lastUpdatedAt`, `ttlMs` (nullable; MAY change), `pollIntervalMs` (optional; MAY change). `status` ∈ `working | input_required | completed | failed | cancelled`.

`CreateTaskResult` is the request's `Result` with the `Task` fields inline, discriminated by `resultType: "task"`:

```json
{ "jsonrpc": "2.0", "id": 1, "result": { "resultType": "task",
    "taskId": "786512e2-9e0d-44bd-8f29-789f320fe840", "status": "working",
    "createdAt": "2026-07-29T10:30:00Z", "lastUpdatedAt": "2026-07-29T10:30:00Z",
    "ttlMs": 60000, "pollIntervalMs": 5000 } }
```

| Method | Result shape |
| --- | --- |
| `tasks/get` | the `Task`, with status-specific payload inline: `completed` adds `result` (the underlying result structure, **including a tool result with `isError: true`**), `failed` adds `error` (the JSON-RPC error), `input_required` adds `inputRequests` |
| `tasks/update` | `UpdateTaskResult` (`resultType: "complete"`) — carries client `inputResponses` to the task |
| `tasks/cancel` | empty acknowledgement (`resultType: "complete"` only) — cooperative intent, eventually consistent; `notifications/cancelled` MUST NOT cancel a task (`[7.cancellation]`) |
| `notifications/tasks` | optional push of full task state via the `subscriptions/listen` `taskIds` filter; requestors MUST NOT rely on it (`[7.status-notification]`) |

- `tasks/result` and `tasks/list` do not exist in this revision; terminal payloads arrive inline on `tasks/get`.
- A task MUST be durably created before `CreateTaskResult` is sent (`tasks/get` for the id already resolves); MRTR exchanges resolve synchronously before task creation.
- `failed` is reserved for JSON-RPC errors and MUST NOT represent a tool-result error — a `completed` task's `result` still needs its `isError` inspected (`[7.failed-task]`).
- The 2025-11-25 `_meta` task keys (`io.modelcontextprotocol/related-task`, `model-immediate-response`) no longer exist.

See `contract-checklist.md` §7 and `examples.md` §11 for the full task contract and the labeled domain-specific fallback.
