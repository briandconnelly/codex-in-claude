# Native Wire Shapes

Exact native MCP request/response envelopes, field names, and casing for the methods most often confused with house conventions.
Use it to keep native examples spec-faithful and to tell at a glance whether a field is protocol or convention.

**Baseline:** stable **MCP 2025-11-25** (see `SKILL.md` → Spec Baseline for the in-flight 2026-07-28 RC and its likely migration points).
Authoritative schema and pagination rules: <https://modelcontextprotocol.io/specification/2025-11-25>.

**Not exhaustive.** This covers the high-risk native shapes — list pagination, completion, the `tools/call` result envelope, and the experimental task lifecycle — not the full protocol surface.
For methods not listed here, read the spec rather than inferring from these rows.
Convention extensions (`errors`, `repair`, `fingerprint`, `has_more`/`next_cursor` in a tool's own payload, etc.) are **not** shown as native fields; see the native-vs-convention rule in `SKILL.md`.

## List pagination (shared envelope)

`tools/list`, `resources/list`, `resources/templates/list`, and `prompts/list` share one native pagination shape.

Request — `params` is **optional**; when present it may carry an opaque `cursor` (and `_meta`):

```json
{ "jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": { "cursor": "<opaque>" } }
```

Result — a method-specific array key plus an **optional** `nextCursor`:

```json
{ "jsonrpc": "2.0", "id": "1", "result": { "tools": [], "nextCursor": "<opaque>" } }
```

| Method | Result array key | Element type |
| --- | --- | --- |
| `tools/list` | `tools` | `Tool` |
| `resources/list` | `resources` | `Resource` |
| `resources/templates/list` | `resourceTemplates` | `ResourceTemplate` |
| `prompts/list` | `prompts` | `Prompt` |

Rules that examples must not contradict:

- Casing is `nextCursor` (camelCase).
  The cursor is **opaque** — do not decode or construct it.
- **Absence of `nextCursor` means the list is complete.** There is no native `has_more`, `next_cursor`, `estimated_total`, `total`, or `limit` on these methods, and page size is server-selected.
- A tool's *own* result payload (the body of a `tools/call` result, not a list method) MAY use a documented house pagination convention such as `has_more` / `next_cursor` / `estimated_total`.
  That is convention, lives in the tool's `structuredContent` under its `outputSchema`, and must be labeled as such — see §8 of `contract-checklist.md`.

## Completion — `completion/complete`

Gated on `server.capabilities.completions`.
The result nests under `completion`:

```json
{ "jsonrpc": "2.0", "id": "1", "result": { "completion": { "values": ["C0123DEPLOYS", "C0456DEPLOYOPS"], "total": 2, "hasMore": false } } }
```

- Native fields: `values` (required), `total` (optional), `hasMore` (optional, camelCase).
- This `hasMore` is the **native completion field** — distinct from the house `has_more` pagination convention, and not a counterexample to the casing rule.

## Tool result — `tools/call`

```json
{ "jsonrpc": "2.0", "id": "1", "result": { "content": [], "structuredContent": {}, "isError": false, "_meta": {} } }
```

- Native fields: `content` (array of content blocks), `structuredContent` (object, paired with the tool's `outputSchema`), `isError` (boolean), `_meta`.
- **Declared domain output — including house pagination fields — belongs in `structuredContent`** validated by `outputSchema`, so the agent can observe it.
  Only auxiliary house metadata belongs under a namespaced `_meta` key (e.g. `com.example/chunks`); `_meta` may not be surfaced to the model.
- `errors` is **not** a native `tools/call` or `Tool` field.
  Documented error catalogs are a convention extension (see `examples.md` §1).

## Tasks (experimental — MCP 2025-11-25)

Task augmentation needs both `server.capabilities.tasks.requests.tools.call` and the tool's `execution.taskSupport`.
The lifecycle uses several **distinct** result shapes — do not collapse them into one envelope.
Native casing throughout: `taskId`, `status`, `statusMessage` (optional), `createdAt`, `lastUpdatedAt`, `ttl`, `pollInterval` (do not snake_case these). `status` ∈ `working | input_required | completed | failed | cancelled`.

| Method | Result shape |
| --- | --- |
| `tools/call` with a `task` augmentation | `CreateTaskResult` — the `Task` nested under `result.task` |
| `tasks/get` | the `Task` directly in `result` |
| `tasks/cancel` | the `Task` directly in `result` |
| `tasks/list` | `{ "tasks": [], "nextCursor": "<opaque>" }` (shares the list-pagination shape above) |
| `tasks/result` | the underlying tool result; **MUST** carry `io.modelcontextprotocol/related-task` in `_meta` |
| `notifications/tasks/status` | optional push of full task state; requestors MUST NOT rely on receiving it |

`ttl` and `pollInterval` are milliseconds.
Carry `io.modelcontextprotocol/related-task` in `_meta` only where the payload does not already name the task (required on `tasks/result`; `tasks/get`/`tasks/list`/`tasks/cancel` SHOULD NOT include it).
`tasks/result` blocks until the task is terminal and always returns the underlying result; on `input_required` the requestor SHOULD call it preemptively and hold it open — the pending input request arrives as a separate receiver-to-requestor request (tagged with related-task `_meta`) while the call is pending, not as the `tasks/result` response.
A `CreateTaskResult` MAY carry `io.modelcontextprotocol/model-immediate-response` (a string) in `_meta` for the host to hand the model while the task runs.
See `contract-checklist.md` §7 and `examples.md` §11 for the full task contract and the labeled domain-specific fallback.
