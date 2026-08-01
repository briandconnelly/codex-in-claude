# Scenario 3 (Long-running) — with-skill run (2026-07-28 rebase re-run)

- **Date:** 2026-07-29
- **Tree:** `1b0b743` (main, post #130 rebase + #133 §6 fixes)
- **Mode:** with-skill (fresh general-purpose subagent on `claude-fable-5`; read `SKILL.md` + all eight `references/` files, explicitly forbidden from `tests/` and `decisions/`; 11 tool-uses, all reads)
- **Score:** 8/8

## Exact prompt given

Same Scenario 3 prompt as the baseline (see `2026-07-29-scenario3-baseline.md`), preceded by an instruction to read `agent-friendly-mcp/SKILL.md` and every file under `references/` as authoritative guidance, and **not** to read anything under `tests/` or `decisions/`.

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Extension gated at both ends (per-request `clientCapabilities.extensions` + `server/discover`); no `execution.taskSupport`, no per-request task opt-in | **PASS** | `server/discover` result advertises `extensions: {"io.modelcontextprotocol/tasks": {}}` (with required `ttlMs`/`cacheScope`); every request carries `_meta.io.modelcontextprotocol/clientCapabilities`; "the extension declaration on *this request* is the gate (`[7.task-support]`)"; task creation is server-directed ("always answers with a task" when declared). |
| A2 | Extension field casing; statuses exactly the five | **PASS** | `taskId`/`status`/`statusMessage`/`createdAt`/`lastUpdatedAt`/`ttlMs`/`pollIntervalMs` throughout; `pollIntervalMs` MAY change between polls noted. |
| A3 | `CreateTaskResult` inline with `resultType: "task"`; task methods return `resultType: "complete"`; no `tasks/result`/`tasks/list` | **PASS** | §3.1 create response is `resultType: "task"` with inline task fields ("task durably created before this is sent"); §3.2/§3.4 polls are `resultType: "complete"` with inline terminal payload ("there is no `tasks/result`"); cancel is an empty `{"resultType": "complete"}` ack; recovery uses `tasks/get` + a fallback tool, never `tasks/list`. |
| A4 | Request-originated progress only; post-creation progress via `statusMessage`/`notifications/tasks` | **PASS** | "Progress after creation rides `statusMessage` (`[7.progress-token]` — request-scoped `notifications/progress` ended when the call returned)"; optional `subscriptions/listen` with the `taskIds` filter → `notifications/tasks`, polling authoritative. |
| A5 | `input_required` recovery: `tasks/get` carries `inputRequests`, answered via `tasks/update` `inputResponses`, honoring declared elicitation modes | **PASS** | §3.3 poll carries `inputRequests` with a form `elicitation/create` request; answered via `tasks/update` `inputResponses`; "the server MUST NOT put an `ElicitRequest` in `inputRequests` it didn't declare" with a named non-elicitation fallback (`vidforge_approve_watermark`) and a 30-minute bound ("the server never assumes input will arrive"). |
| A6 | Tool error = `completed` task with `result.isError: true`; `failed` reserved for JSON-RPC errors | **PASS** | "**`completed` is delivery, not success** (`[7.failed-task]`)"; worked `watermark_rejected` example is `status: "completed"` with an inner `isError: true` §6 envelope; "`failed` is reserved for JSON-RPC faults … and carries `error`, not `result`"; the capability summary repeats the inversion for agents. |
| A7 | Fallback status/cancel tools labeled convention, mirroring native signals, not replacing `tasks/*` | **PASS** | "These are a **labeled fallback** mirroring `tasks/*`, not a replacement"; `vidforge_get_render_status` mirrors state/`terminal`/`poll_after_ms`/`status_message`/`expires_at`/`terminal_states` and result location; cancel mirrors the cooperative-ack semantics. |
| A8 | Native vs convention never mixed; no snake_cased native fields | **PASS** | Native task fields camelCase; house fields snake_case inside `structuredContent`; convention error catalog under namespaced `_meta` (`com.vidforge/errors`); nothing invented under `io.modelcontextprotocol/*`. |

**Passed:** all (8/8).
Notable beyond the assertions: the run applies both #133 rules on the wire — the `task_expired` JSON-RPC error uses the `[6.rename]` spellings in `error.data`, and emits `details.value` with the explicit justification "the server minted this handle — known-safe per `[6.offending-value]`".
The transport section (`Mcp-Name` = `taskId` on task methods) and the reused `{next_step, tool, arguments, alternative}` `follow_up` on the accepted fallback both match the rebased checklist.

## Full final output

Verbatim final message returned by the with-skill subagent (11 tool-uses, all file reads):

I read `SKILL.md` and all eight reference files (`contract-checklist.md`, `design-workflow.md`, `review-workflow.md`, `examples.md`, `native-wire-shapes.md`, `mcp-2025-11-25-compat.md`, `mcp-vs-cli.md`, `vocabulary.md`). Here is the design.

---

# `vidforge` — agent-facing MCP contract for video rendering

**Revision target (Step 0, `[1.spec-revision]`):** MCP **2026-07-28** + extension **`io.modelcontextprotocol/tasks`** (extension-versioned; re-verify when ext-tasks cuts a release). Transport: streamable HTTP — `MCP-Protocol-Version` on every POST, `Mcp-Method` on every request, `Mcp-Name` on `tools/call` (tool name) and on `tasks/get`/`tasks/update`/`tasks/cancel` (the `taskId`, so gateways route to the instance holding task state).

**Naming note (`[3.naming]`):** the requested `video_render` becomes `vidforge_render_video` — prefix + verb + noun, so it can't collide in multiplexed clients. Companion fallback tools: `vidforge_get_render_status`, `vidforge_approve_watermark`, `vidforge_cancel_render`.

**One state handle:** the render job id and the `taskId` are the *same* opaque, high-entropy handle (`task_01JVID…`). Task operations honor it for `ttlMs` (24 h); the fallback tools and the result resource honor it for 7 days. Auth is re-checked on every `tasks/get`/`tasks/update`/`tasks/cancel` and every fallback-tool call (`[1.state-handles]`, `[7.task-handles]`).

---

## 1. Capability declarations

**Server side** — `server/discover` result (excerpt; `ttlMs`/`cacheScope` required on it, `[8.cacheable-results]`):

```json
{
  "resultType": "complete",
  "capabilities": {
    "tools": { "listChanged": true },
    "resources": {},
    "extensions": { "io.modelcontextprotocol/tasks": {} }
  },
  "ttlMs": 3600000,
  "cacheScope": "private"
}
```

**Client side** — every request carries the required per-request `_meta` (`[1.negotiated-caps]`); a task-capable, elicitation-capable client sends:

```json
"_meta": {
  "io.modelcontextprotocol/protocolVersion": "2026-07-28",
  "io.modelcontextprotocol/clientCapabilities": {
    "elicitation": { "form": {} },
    "extensions": { "io.modelcontextprotocol/tasks": {} }
  }
}
```

**Capability-summary block** (exposed via a `vidforge://capabilities` resource *and* a `vidforge_get_capabilities` tool, because `instructions` alone is advisory — `[2.instructions-advisory]`, `[4.tool-fallback]`). Convention surface, not native:

```json
{
  "server": { "name": "vidforge", "version": "1.0.0", "fingerprint": "vidforge@1.0.0+c41f9a2",
    "transport": "streamable-http",
    "spec_target": { "revision": "2026-07-28", "extensions": ["io.modelcontextprotocol/tasks"] },
    "summary": "Render source video assets to finished videos, with optional watermark requiring user approval mid-render." },
  "does": [ "Render an uploaded asset to mp4/webm/mov at 720p–4k.",
            "Pause a render for user watermark approval, then resume.",
            "Report status, deliver results as linked resources, cancel in-flight renders." ],
  "does_not": [ "Upload or manage source assets (use your asset pipeline).",
                "Edit, trim, or transcode interactively.",
                "Stream partial video output." ],
  "error_carriers": { "tool_errors": "structuredContent", "non_tool_rpc_errors": "error.data" },
  "task_semantics": {
    "note": "`completed` is a delivery statement, not a success statement: a render that fails semantically (including watermark rejection or approval timeout) arrives as a completed task whose result has isError: true. `failed` is reserved for JSON-RPC faults. Always inspect result.isError.",
    "task_ttl_ms": 86400000,
    "result_retention": "render results and status remain reachable via vidforge_get_render_status and the vidforge://videos/{render_id}/final.{ext} resource for 7 days"
  },
  "negotiated_capabilities": {
    "server": ["capabilities.extensions[io.modelcontextprotocol/tasks]"],
    "client": ["clientCapabilities.extensions[io.modelcontextprotocol/tasks]",
               "clientCapabilities.elicitation.form"],
    "fallbacks": {
      "no_tasks": "vidforge_render_video returns status:'accepted' with a render_id; poll vidforge_get_render_status, cancel via vidforge_cancel_render.",
      "no_elicitation": "watermark approval is requested via the task's input_required status message and satisfied by calling vidforge_approve_watermark; no ElicitRequest is sent.",
      "approval_never_arrives": "after 30 minutes awaiting approval the render ends as a completed task whose result carries the watermark_approval_timeout error envelope."
    }
  }
}
```

Because the fallback tools exist, the server never needs `MissingRequiredClientCapability` (`-32021`) for tasks — it can always serve the request without tasking (`[7.task-support]`, `[6.capability-missing]`).

---

## 2. Tool definition — `vidforge_render_video`

```json
{
  "name": "vidforge_render_video",
  "title": "Render video",
  "description": "Render a source video asset to a finished video, optionally with a watermark.\n\nWhen to use: the source asset is already uploaded (a vidforge://assets/… URI) and you want a finished file. Do not use to trim, edit, or transcode interactively.\n\nDuration and execution mode: renders take 1–10 minutes. When the request declares the io.modelcontextprotocol/tasks extension, this tool always answers with a task (resultType: \"task\"); poll tasks/get, respecting pollIntervalMs. Without the extension it returns immediately with status: \"accepted\" and a render_id — poll vidforge_get_render_status.\n\nWatermark approval: if `watermark` is set, the render pauses before final encode for user approval of a preview. On the task path this surfaces as task status input_required (form elicitation when your request declares elicitation.form; otherwise call vidforge_approve_watermark). If approval does not arrive within 30 minutes, the render ends as a completed task whose result carries the watermark_approval_timeout error. Omit `watermark` to render without a watermark and without any pause.\n\nRetries: each call starts a new paid render. If a call times out or the transport drops, do not blind-retry — re-send with the same `idempotency_key` (returns the existing job), or check vidforge_get_render_status first.\n\nExample: {\"source_asset_uri\": \"vidforge://assets/a_9F3K\", \"output_format\": \"mp4\", \"resolution\": \"1080p\", \"watermark\": {\"image_asset_uri\": \"vidforge://assets/logo_2B\", \"position\": \"bottom_right\", \"opacity\": 0.6}}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["source_asset_uri", "output_format", "resolution"],
    "properties": {
      "source_asset_uri": { "type": "string", "format": "uri",
        "description": "Uploaded source asset URI (vidforge://assets/…). Not a local path or external URL." },
      "output_format": { "type": "string", "enum": ["mp4", "webm", "mov"] },
      "resolution": { "type": "string", "enum": ["720p", "1080p", "4k"] },
      "watermark": {
        "type": "object",
        "required": ["image_asset_uri"],
        "properties": {
          "image_asset_uri": { "type": "string", "format": "uri",
            "description": "Watermark image asset URI (vidforge://assets/…)." },
          "position": { "type": "string",
            "enum": ["top_left", "top_right", "bottom_left", "bottom_right", "center"],
            "description": "Omitted means bottom_right (the server applies this default).",
            "default": "bottom_right" },
          "opacity": { "type": "number", "minimum": 0, "maximum": 1,
            "description": "Omitted means 1.0 (the server applies this default).", "default": 1.0 }
        },
        "additionalProperties": false,
        "description": "Omit entirely to render without a watermark; when present, the render pauses for user approval of a preview."
      },
      "idempotency_key": { "type": "string", "minLength": 1, "maxLength": 128,
        "description": "Optional client-supplied key; calls with the same key within 24h return the same render job instead of starting a new one. Omitted means no deduplication — each call renders." }
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["status"],
    "properties": {
      "status": { "type": "string", "enum": ["completed", "accepted"],
        "description": "Discriminator. 'completed': the render finished (fields video_uri, duration_seconds, size_bytes, resolution, watermark_applied, expires_at are present). 'accepted': non-task clients only — the render was started (fields render_id, state, poll_after_ms, expires_at, follow_up are present)." },
      "video_uri": { "type": "string", "format": "uri" },
      "duration_seconds": { "type": "number" },
      "size_bytes": { "type": "integer" },
      "resolution": { "type": "string" },
      "watermark_applied": { "type": "boolean" },
      "expires_at": { "type": "string", "format": "date-time",
        "description": "When the result resource (completed) or the render handle (accepted) expires." },
      "render_id": { "type": "string" },
      "state": { "type": "string", "enum": ["queued", "rendering", "awaiting_watermark_approval"] },
      "poll_after_ms": { "type": "integer", "minimum": 0 },
      "follow_up": {
        "type": "object",
        "required": ["next_step", "tool", "arguments"],
        "properties": {
          "next_step": { "type": "string" },
          "tool": { "type": "string" },
          "arguments": { "type": "object", "additionalProperties": true },
          "alternative": { "type": "string" }
        },
        "additionalProperties": false
      }
    },
    "additionalProperties": false
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": false,
    "openWorldHint": false
  },
  "_meta": {
    "com.vidforge/errors": [
      { "code": "source_not_found", "temporary": false, "description": "source_asset_uri resolves to no asset this credential can read." },
      { "code": "unsupported_codec", "temporary": false, "description": "The source asset's codec cannot be rendered; details.reason names it." },
      { "code": "watermark_rejected", "temporary": false, "description": "User declined the watermark preview; adjust `watermark` and re-call." },
      { "code": "watermark_approval_timeout", "temporary": false, "description": "No approval within 30 minutes; re-call to start a new render." },
      { "code": "render_quota_exceeded", "temporary": true, "description": "Concurrent-render quota hit; honour retry_after_ms." },
      { "code": "render_failed", "temporary": true, "description": "Renderer fault; safe to re-call (a new job is started unless idempotency_key is reused)." }
    ]
  }
}
```

Annotation reasoning (`[3.honest-annotations]`, `[3.mutation-scope]`): the tool creates a persistent, 7-day-visible artifact and spends paid render time — that outlives the response contract, so `readOnlyHint: false`; it destroys nothing (`destructiveHint: false`); each call starts a new paid render (`idempotentHint: false` — which is exactly why `idempotency_key` exists, `[3.idempotency-key]`); the render farm is the server's own closed system (`openWorldHint: false`). The inline error catalog carries only repair-critical codes; the full catalog lives in the capability summary (`[6.document-codes]`).

---

## 3. Wire shapes — task path

### 3.1 Create

Request (ordinary `tools/call`; the extension declaration on *this request* is the gate — `[7.task-support]`):

```json
{ "jsonrpc": "2.0", "id": 21, "method": "tools/call",
  "params": {
    "name": "vidforge_render_video",
    "arguments": {
      "source_asset_uri": "vidforge://assets/a_9F3K",
      "output_format": "mp4", "resolution": "1080p",
      "watermark": { "image_asset_uri": "vidforge://assets/logo_2B", "position": "bottom_right", "opacity": 0.6 },
      "idempotency_key": "render-launch-video-2026-07-29"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {
        "elicitation": { "form": {} },
        "extensions": { "io.modelcontextprotocol/tasks": {} } } } } }
```

Response — `CreateTaskResult` (task durably created before this is sent; a `tasks/get` for the id already resolves):

```json
{ "jsonrpc": "2.0", "id": 21, "result": {
    "resultType": "task",
    "taskId": "task_01JVID8Q2R",
    "status": "working",
    "statusMessage": "Render queued; encoding starts shortly.",
    "createdAt": "2026-07-29T14:02:11Z",
    "lastUpdatedAt": "2026-07-29T14:02:11Z",
    "ttlMs": 86400000,
    "pollIntervalMs": 10000 } }
```

Persist `taskId` immediately — it is the recovery handle. Replaying the same `idempotency_key` returns this same task.

### 3.2 Poll (working)

`tasks/get` — `params: {"taskId": "task_01JVID8Q2R", "_meta": {…}}`; over HTTP, `Mcp-Name: task_01JVID8Q2R`. Progress after creation rides `statusMessage` (`[7.progress-token]` — request-scoped `notifications/progress` ended when the call returned):

```json
{ "jsonrpc": "2.0", "id": 22, "result": {
    "resultType": "complete",
    "taskId": "task_01JVID8Q2R",
    "status": "working",
    "statusMessage": "Rendering: 42% (frame 3021/7200), pass 1 of 2.",
    "createdAt": "2026-07-29T14:02:11Z",
    "lastUpdatedAt": "2026-07-29T14:04:40Z",
    "ttlMs": 86400000,
    "pollIntervalMs": 10000 } }
```

Respect `pollIntervalMs`; it MAY change between polls (it lengthens to 30 000 while awaiting approval).

### 3.3 Watermark pause — `input_required`

The preview is exposed as a readable resource so any client (and the human) can look at it before deciding. Poll response carries the outstanding `inputRequests` inline (`[7.input-required]`); only modes this request's `clientCapabilities` declared may appear:

```json
{ "jsonrpc": "2.0", "id": 23, "result": {
    "resultType": "complete",
    "taskId": "task_01JVID8Q2R",
    "status": "input_required",
    "statusMessage": "Waiting for user approval of the watermark preview at vidforge://renders/task_01JVID8Q2R/watermark-preview.png. Unanswered approvals time out after 30 minutes.",
    "createdAt": "2026-07-29T14:02:11Z",
    "lastUpdatedAt": "2026-07-29T14:06:02Z",
    "ttlMs": 86400000,
    "pollIntervalMs": 30000,
    "inputRequests": {
      "watermark-approval-1": {
        "method": "elicitation/create",
        "params": {
          "mode": "form",
          "message": "Approve this watermark? Preview: vidforge://renders/task_01JVID8Q2R/watermark-preview.png (bottom_right, opacity 0.6). Rejecting ends the render.",
          "requestedSchema": {
            "type": "object",
            "required": ["approved"],
            "properties": {
              "approved": { "type": "boolean", "description": "true to continue the render with this watermark; false to reject and end it." },
              "note": { "type": "string", "description": "Optional reason or requested adjustment when rejecting." }
            } } } } } } }
```

Client answers with `tasks/update` (then keeps polling until a terminal status):

```json
{ "jsonrpc": "2.0", "id": 24, "method": "tasks/update",
  "params": {
    "taskId": "task_01JVID8Q2R",
    "inputResponses": {
      "watermark-approval-1": { "action": "accept", "content": { "approved": true } } },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {
        "elicitation": { "form": {} },
        "extensions": { "io.modelcontextprotocol/tasks": {} } } } } }
```

**Tasks-capable client without elicitation:** the server MUST NOT put an `ElicitRequest` in `inputRequests` it didn't declare, so the task still shows `input_required`, and the `statusMessage` names the callable surface instead: *"call `vidforge_approve_watermark` with `{render_id: 'task_01JVID8Q2R', approved: true|false}`"*. That tool resolves the pending input server-side and the task resumes (`[6.elicitation]`'s non-elicitation fallback). The server never assumes input will arrive: the 30-minute timeout below bounds the wait.

### 3.4 Terminal — `completed` (success payload inline; there is no `tasks/result`)

```json
{ "jsonrpc": "2.0", "id": 25, "result": {
    "resultType": "complete",
    "taskId": "task_01JVID8Q2R",
    "status": "completed",
    "createdAt": "2026-07-29T14:02:11Z",
    "lastUpdatedAt": "2026-07-29T14:09:47Z",
    "ttlMs": 86400000,
    "pollIntervalMs": 30000,
    "result": {
      "resultType": "complete",
      "isError": false,
      "content": [
        { "type": "text", "text": "{\"status\":\"completed\",\"video_uri\":\"vidforge://videos/task_01JVID8Q2R/final.mp4\",\"duration_seconds\":184.2,\"size_bytes\":389442118}" },
        { "type": "resource_link",
          "uri": "vidforge://videos/task_01JVID8Q2R/final.mp4",
          "name": "final.mp4",
          "description": "Rendered 1080p mp4 with approved watermark; retained 7 days.",
          "mimeType": "video/mp4",
          "size": 389442118,
          "annotations": { "audience": ["user"], "priority": 0.9, "lastModified": "2026-07-29T14:09:47Z" } }
      ],
      "structuredContent": {
        "status": "completed",
        "video_uri": "vidforge://videos/task_01JVID8Q2R/final.mp4",
        "duration_seconds": 184.2,
        "size_bytes": 389442118,
        "resolution": "1080p",
        "watermark_applied": true,
        "expires_at": "2026-08-05T14:09:47Z"
      } } } }
```

The video is delivered as a `resource_link`, never base64-inlined (`[3.resource-links]`, `[3.large-results]`).

**`completed` is delivery, not success** (`[7.failed-task]`). A rejected watermark arrives like this — same `status: "completed"`, but the inner result is an `isError: true` §6 envelope:

```json
"result": {
  "resultType": "complete",
  "isError": true,
  "content": [ { "type": "text", "text": "User rejected the watermark ('logo too large'). Adjust `watermark` and re-call vidforge_render_video." } ],
  "structuredContent": {
    "code": "watermark_rejected",
    "message": "User declined the watermark preview; the render was not finalized.",
    "details": { "field": "watermark", "reason": "User note: 'logo too large'." },
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "revise_watermark_and_rerender",
      "tool": "vidforge_render_video",
      "arguments": {
        "source_asset_uri": "vidforge://assets/a_9F3K",
        "output_format": "mp4", "resolution": "1080p",
        "watermark": { "image_asset_uri": "vidforge://assets/logo_2B", "position": "bottom_right", "opacity": 0.6 }
      },
      "alternative": "Adjust `watermark.position` or `watermark.opacity` per the user's note before re-calling, or omit `watermark` to render without one."
    },
    "request_id": "req_01JVIDERR7",
    "fingerprint": "vidforge@1.0.0+c41f9a2"
  } }
```

`repair` preserves caller intent — the still-valid original arguments, literally callable (`[6.repair-intent]`, `[6.repair-callable]`); the adjustment the user wants is prose in `alternative` because it must be discovered from the user. An unanswered approval ends the same way with `code: "watermark_approval_timeout"`. `failed` is reserved for JSON-RPC faults during execution and carries `error`, not `result`.

### 3.5 Cancel

`tasks/cancel` — `params: {"taskId": "task_01JVID8Q2R", "_meta": {…}}` → empty ack (`{"resultType": "complete"}`). Cooperative and eventually consistent: the ack means intent received; the task may still finish in another terminal status, observed via `tasks/get`. `notifications/cancelled` MUST NOT be used for a task (`[7.cancellation]`).

### 3.6 Recovery after a crash

Within 24 h (`ttlMs`): re-issue `tasks/get` with the persisted `taskId` — the terminal payload is inline, so nothing is lost. Optional poll-sooner push: subscribe via `subscriptions/listen` with `"taskIds": ["task_01JVID8Q2R"]` and receive `notifications/tasks`; polling stays the authoritative path (`[7.status-notification]`).

After `ttlMs`, `tasks/get` fails as JSON-RPC with the same envelope in `error.data` (renamed keys per `[6.rename]`), routing to the longer-lived surface:

```json
{ "jsonrpc": "2.0", "id": 30, "error": {
    "code": -32602, "message": "Unknown or expired task.",
    "data": {
      "machine_code": "task_expired",
      "human_message": "Task state was deleted after its 24h TTL; render results remain reachable for 7 days.",
      "details": { "field": "taskId", "value": "task_01JVID8Q2R", "reason": "ttlMs elapsed at 2026-07-30T14:02:11Z." },
      "temporary": false,
      "retry_after_ms": null,
      "repair": {
        "next_step": "check_render_status",
        "tool": "vidforge_get_render_status",
        "arguments": { "render_id": "task_01JVID8Q2R" },
        "alternative": "If the render completed, read vidforge://videos/task_01JVID8Q2R/final.mp4 directly."
      },
      "request_id": "req_01JVIDERR9",
      "fingerprint": "vidforge@1.0.0+c41f9a2" } } }
```

(`details.value` is emitted because the server minted this handle — known-safe per `[6.offending-value]`.)

---

## 4. Fallback for clients without the tasks extension

The extension gate is per-request: a request whose `clientCapabilities.extensions` lacks `io.modelcontextprotocol/tasks` can neither receive nor operate on tasks. The server still serves it — never blocking for 10 minutes, never erroring — by returning the **accepted** mode of the same `outputSchema` and mirroring the native lifecycle signals through ordinary tools (`[7.task-fallback]`), which is also what code-execution clients compose best.

**Create** — same `tools/call`, no extension declared → immediate `resultType: "complete"`:

```json
{ "resultType": "complete", "isError": false,
  "content": [ { "type": "text", "text": "Render accepted as task_01JVID8Q2R; poll vidforge_get_render_status." } ],
  "structuredContent": {
    "status": "accepted",
    "render_id": "task_01JVID8Q2R",
    "state": "queued",
    "poll_after_ms": 10000,
    "expires_at": "2026-08-05T14:02:11Z",
    "follow_up": {
      "next_step": "poll_render_status",
      "tool": "vidforge_get_render_status",
      "arguments": { "render_id": "task_01JVID8Q2R" } } } }
```

`follow_up` reuses the §6 `{next_step, tool, arguments, alternative}` shape on the success carrier — one next-step vocabulary (`[3.dispatched-vs-applied]`).

**Fallback tools** (compact contracts):

- **`vidforge_get_render_status`** — `readOnlyHint: true`. Input: `{render_id}` (required, `additionalProperties: false`). Output mirrors the native signals: `render_id`, `state` (`queued | rendering | awaiting_watermark_approval | succeeded | failed | cancelled`), `terminal` (bool), `poll_after_ms`, `progress` (0–1, when knowable), `status_message`, `expires_at`, `terminal_states`, plus on `succeeded` the same completed fields (`video_uri`, `duration_seconds`, `size_bytes`, `resolution`, `watermark_applied`), on `failed` the full §6 error envelope, and on `awaiting_watermark_approval`:

```json
{ "render_id": "task_01JVID8Q2R",
  "state": "awaiting_watermark_approval",
  "terminal": false,
  "poll_after_ms": 15000,
  "progress": 0.83,
  "status_message": "Waiting for watermark approval; times out 2026-07-29T14:36:02Z.",
  "watermark_preview_uri": "vidforge://renders/task_01JVID8Q2R/watermark-preview.png",
  "expires_at": "2026-08-05T14:02:11Z",
  "terminal_states": ["succeeded", "failed", "cancelled"],
  "follow_up": {
    "next_step": "approve_watermark",
    "tool": "vidforge_approve_watermark",
    "arguments": { "render_id": "task_01JVID8Q2R", "approved": true },
    "alternative": "Pass approved: false with a `note` to reject; rejection ends the render." } }
```

- **`vidforge_approve_watermark`** — input `{render_id, approved (required bool), note (optional string)}`. Annotations: `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true` (replaying the same decision is a no-op; a conflicting second decision fails with `code: "approval_already_recorded"`, `temporary: false`). This tool also serves tasks-capable clients that lack elicitation (§3.3).
- **`vidforge_cancel_render`** — input `{render_id}`. `readOnlyHint: false`, `destructiveHint: false` (stops pending work; source assets and any completed results are untouched, and a render is recreatable by re-calling — reasoning stated in the description), `idempotentHint: true`. Like `tasks/cancel`, the result is an intent acknowledgement (`{"acknowledged": true, "state": "cancelling"}`); confirm via the status tool.

These are a **labeled fallback** mirroring `tasks/*`, not a replacement; the capability summary says so, and task-capable clients should prefer the native lifecycle.

---

## 5. Checklist walk (design done-criteria)

| § | Answer |
|---|---|
| §1 | Distinct name (`vidforge`); streamable HTTP with the three routing headers; auth re-checked per task/tool call; handle discipline declared (taskId = render_id; 24 h task TTL, 7-day result retention); revision + extension target declared; negotiated capabilities and fallbacks in the summary. |
| §2 | Capability summary via resource **and** tool (instructions treated as advisory); negative scope stated; four compact tool definitions — no progressive-disclosure machinery needed at this catalog size. |
| §3 | One task-completing tool (render = validate + queue + encode + approve + mux hidden behind one contract); closed schemas, strict enums, omission semantics on every optional; honest annotations with the mutation-scope reading documented; `idempotency_key` for the paid create; discriminated `status` output instead of a union; dispatched-vs-applied covered by the accepted envelope + `follow_up`. |
| §4 | Result videos and watermark previews are resources with native triage metadata and declared retention; result delivered as `resource_link`, never inline. |
| §5 | No prompts — nothing here needs orchestration scaffolding, and no tool depends on one. N/A. |
| §6 | Symbolic codes; one envelope on two carriers (worked `watermark_rejected` tool-result error and `task_expired` JSON-RPC error); `temporary`/`retry_after_ms` invariants respected; repairs name callable surfaces and preserve intent; elicitation gated on declared modes with a named non-elicitation fallback. |
| §7 | Always-task on extension-declaring requests, declared in the description; poll via `tasks/get` (inline terminal payloads), input via `tasks/update`, cancel via `tasks/cancel` (cooperative); `input_required` mechanism + 30-min timeout defined; `completed`-is-delivery stated loudly in the summary; `notifications/tasks` optional; labeled domain fallback for non-declaring clients. |
| §8 | Concise structured results; bulk bytes live behind resource links; `poll_after_ms`/`pollIntervalMs` bound polling; RFC3339 UTC timestamps; no fixed prose blocks. No list-shaped tool payloads, so no house pagination needed. |
| §9 | Fingerprint `vidforge@1.0.0+c41f9a2` in the summary and every error envelope (clients cache the surface across multi-day renders); deterministic `tools/list` order; `toolsListChanged` advertised; error codes part of the versioned surface. |

Remaining work before shipping (design-workflow Steps 8–9): an eval suite with cold-start, first-repair (`watermark_rejected` → one-hop re-render), long-running poll/cancel/recover fixtures, a `forced_error` fixture per tool asserting carrier and envelope on the serialized wire, and a `discovery_size_budget` fixture for the four-tool `tools/list`.
