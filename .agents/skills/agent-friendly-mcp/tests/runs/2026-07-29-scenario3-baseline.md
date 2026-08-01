# Scenario 3 (Long-running) — baseline run (2026-07-28 rebase re-run)

- **Date:** 2026-07-29
- **Tree:** `1b0b743` (main, post #130 rebase + #133 §6 fixes)
- **Mode:** baseline (fresh general-purpose subagent on `claude-fable-5`, prompt only, forbidden from reading the repo/skill or the web; 0 tool-uses confirms no skill access)
- **Score:** 2/8

## Exact prompt given

The Scenario 3 prompt from `tests/scenarios.md`, verbatim (targeting MCP 2026-07-28 + `io.modelcontextprotocol/tasks`), wrapped with a guardrail forbidding all tool use.

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Extension gated at both ends (per-request `clientCapabilities.extensions` + `server/discover`); no `execution.taskSupport`, no per-request task opt-in | **FAIL** | Capabilities declared at `initialize` (removed in 2026-07-28), evaluated "once, at init"; no `server/discover`, no per-request `_meta`. Reinvents per-tool task support as `_meta.io.modelcontextprotocol/tasks.taskSupport: "optional"` and puts a client-requested `ttl` in the call's task `_meta` — a per-request opt-in analog. |
| A2 | Extension field casing (`ttlMs`, `pollIntervalMs`, `lastUpdatedAt`, …); statuses exactly the five | **FAIL** | Uses the retired `ttl`/`pollInterval` spellings throughout and omits `lastUpdatedAt` — the predicted 2025-11-25-from-memory failure. Statuses themselves are correct. |
| A3 | `CreateTaskResult` inline with `resultType: "task"`; `tasks/get`/`update`/`cancel` are `resultType: "complete"`; no `tasks/result`/`tasks/list` | **FAIL** | Nests the task under `result.task` (old shape); no `resultType` anywhere; recovery layer 2 is the removed `tasks/list` with `nextCursor`. |
| A4 | `progressToken` request-originated; post-creation progress via `statusMessage`/`notifications/tasks`, not `notifications/progress` | **PASS** (caveat) | Task-mode progress rides `statusMessage` ("the agent needs no separate progress channel in task mode"); fallback `progressToken` is client-sent on the originating call; push is optional and explicitly lossy with `tasks/get` authoritative. Caveat: invents `notifications/tasks/status` rather than `notifications/tasks` via `subscriptions/listen`. |
| A5 | Watermark pause via `input_required` with `tasks/get`→`inputRequests`, answered via `tasks/update` `inputResponses`, honoring declared elicitation modes | **FAIL** | Uses the removed server-initiated `elicitation/create` tied to the task via the removed `io.modelcontextprotocol/related-task` `_meta`; no `inputRequests`, no `tasks/update`. |
| A6 | Tool error = `completed` task whose `result.isError` is true; `failed` reserved for JSON-RPC errors | **FAIL** | "Failure is the same shape with `status: "failed"` and the embedded result carrying `isError: true`" — exactly the inverted coupling the 2026-07-28 extension retired. |
| A7 | Fallback status/cancel tools labeled convention, mirroring native signals, not replacing `tasks/*` | **FAIL** | Deliberately omits fallback tools; the non-task fallback is a 1–10-minute *blocking* `tools/call` ("allow ~12 minutes"), with no status/cancel surface and no recovery after a dropped connection — the §7 execution-mode anti-pattern for work this long. |
| A8 | Native and house conventions not mixed; convention metadata namespaced/labeled; no snake_cased native fields | **PASS** | Native fields stay camelCase; the house idempotency echo rides a namespaced `_meta` key (`com.example.render/idempotencyKey`); stale-native keys are scored under A1/A5, not here. |

**Passed:** A4, A8 (2/8).
The baseline reproduces the 2025-11-25 task contract from memory almost verbatim — precisely the drift the rebased scenario was redesigned to detect (it even flags its own uncertainty: "treat specific key spellings … as the reconstructed names they are").
Its non-protocol judgment is good (idempotency keys, decline-does-not-default, human-stays-in-the-loop), which sharpens the contrast: what the skill supplies here is the current wire contract, not design taste.

## Full final output

Verbatim final message returned by the baseline subagent (0 tool calls):

# `video_render` — MCP 2026-07-28 contract with `io.modelcontextprotocol/tasks`

Design stance: the tool has one identity (`video_render`) in both modes — task-augmented when both sides negotiated the extension, blocking-with-progress otherwise. No parallel `video_render_status` tool; the task methods are the single authority for status. One honesty note up front: this is written from spec knowledge, not from a live schema file, so where the published extension spec fixes an exact key name, the spec governs; the shapes below follow its namespaced-`_meta` conventions.

## 1. Capability declarations

Server (`initialize` result):

```json
{
  "capabilities": {
    "tools": { "listChanged": true },
    "extensions": {
      "io.modelcontextprotocol/tasks": {
        "requests": { "tools/call": {} },
        "list": true,
        "cancel": true
      }
    }
  }
}
```

Client (`initialize` request):

```json
{
  "capabilities": {
    "elicitation": {},
    "extensions": { "io.modelcontextprotocol/tasks": {} }
  }
}
```

Gate logic (this is the whole negotiation — evaluate it once, at init):

- Task mode is permitted iff **both** sides declared the extension and the server listed `tools/call` under `requests`.
- The watermark pause (`watermark: "ask"`) is permitted iff the client declared `elicitation`.
- Neither is per-call negotiable; absence of either capability selects the fallbacks in §5.

## 2. Tool definition

```json
{
  "name": "video_render",
  "title": "Render video",
  "description": "Render a video project to a delivery preset. Renders take 1-10 minutes and may pause awaiting user approval of the watermark. If task support was negotiated, calls to this tool return a task: persist the taskId (and your idempotency_key) before doing anything else — they are your only recovery handles. Poll tasks/get no faster than pollInterval. Without task support this call blocks up to ~12 minutes and streams progress notifications; set your request timeout accordingly and pass watermark explicitly if your client cannot answer elicitation.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "project_uri":  { "type": "string", "description": "URI of the project or timeline to render." },
      "preset":       { "type": "string", "enum": ["720p-h264", "1080p-h264", "2160p-hevc"] },
      "watermark":    { "type": "string", "enum": ["ask", "apply", "omit"], "default": "ask",
                        "description": "'ask' pauses the render for user approval (requires client elicitation). Pass 'apply' or 'omit' to render without pausing." },
      "idempotency_key": { "type": "string",
                        "description": "Client-chosen key. Re-calling with the same key returns the existing render (or its task) instead of starting a duplicate. Always set this." }
    },
    "required": ["project_uri", "preset"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "uri":              { "type": "string" },
      "mimeType":         { "type": "string" },
      "durationSeconds":  { "type": "number" },
      "preset":           { "type": "string" },
      "watermarkApplied": { "type": "boolean" }
    },
    "required": ["uri", "mimeType", "watermarkApplied"]
  },
  "annotations": { "destructiveHint": false, "openWorldHint": false },
  "_meta": {
    "io.modelcontextprotocol/tasks": { "taskSupport": "optional" }
  }
}
```

`taskSupport: "optional"` (not `"required"`) is deliberate — it keeps the tool callable by non-task clients via §5 instead of failing them at call time.

## 3. Wire shapes, task mode

### 3.1 Create

```json
{ "jsonrpc": "2.0", "id": 42, "method": "tools/call",
  "params": {
    "name": "video_render",
    "arguments": {
      "project_uri": "res://projects/spring-launch",
      "preset": "1080p-h264",
      "watermark": "ask",
      "idempotency_key": "render-spring-launch-7f3a"
    },
    "_meta": { "io.modelcontextprotocol/tasks": { "ttl": 14400000 } }
  }
}
```

Immediate response — a task, not a `CallToolResult`:

```json
{ "jsonrpc": "2.0", "id": 42,
  "result": {
    "task": {
      "taskId": "task_01HZYX8Q4R",
      "status": "working",
      "statusMessage": "Queued (position 2)",
      "createdAt": "2026-07-29T18:02:11Z",
      "ttl": 14400000,
      "pollInterval": 5000
    }
  }
}
```

`ttl` is how long the server retains the task **including its result after completion** (server may clamp the requested value — trust the response, not the request). `pollInterval` is a floor, not a suggestion; the server recalculates it per poll (long during encode, short when nearing completion).

### 3.2 Poll

```json
{ "jsonrpc": "2.0", "id": 43, "method": "tasks/get",
  "params": { "taskId": "task_01HZYX8Q4R" } }
```

```json
{ "jsonrpc": "2.0", "id": 43,
  "result": {
    "task": {
      "taskId": "task_01HZYX8Q4R",
      "status": "working",
      "statusMessage": "Encoding: 37%",
      "createdAt": "2026-07-29T18:02:11Z",
      "ttl": 14400000,
      "pollInterval": 5000
    }
  }
}
```

Progress lives in `statusMessage`; the agent needs no separate progress channel in task mode.

### 3.3 Pause for watermark approval

Task transitions to `input_required` and the server issues an elicitation tied to the task:

```json
{ "jsonrpc": "2.0", "id": "srv-7", "method": "elicitation/create",
  "params": {
    "message": "Render 'spring-launch' is ready to finalize. Apply the studio watermark?",
    "requestedSchema": {
      "type": "object",
      "properties": { "apply_watermark": { "type": "boolean", "title": "Apply watermark" } },
      "required": ["apply_watermark"]
    },
    "_meta": {
      "io.modelcontextprotocol/related-task": { "taskId": "task_01HZYX8Q4R" }
    }
  }
}
```

Meanwhile polls return:

```json
{ "task": { "taskId": "task_01HZYX8Q4R", "status": "input_required",
            "statusMessage": "Waiting for user approval of watermark",
            "pollInterval": 15000 } }
```

The `related-task` `_meta` is what lets a client that reconnected mid-pause re-associate a re-delivered elicitation with the task. Client answers `{"action": "accept", "content": {"apply_watermark": true}}` → status returns to `working`. `{"action": "decline"}` or `{"action": "cancel"}` does **not** silently pick a default: the render fails with a message telling the agent to re-call with an explicit `watermark` value. Unanswered approval: the task sits in `input_required` until `ttl` expiry, then fails with the same guidance.

### 3.4 Completion — result embedded in `tasks/get`

There is no separate `tasks/result` call in 2026-07-28; the terminal `tasks/get` carries the `CallToolResult`:

```json
{ "jsonrpc": "2.0", "id": 51,
  "result": {
    "task": { "taskId": "task_01HZYX8Q4R", "status": "completed",
              "statusMessage": "Render complete", "ttl": 14400000 },
    "result": {
      "content": [
        { "type": "resource_link",
          "uri": "https://cdn.example.com/renders/01HZYX8Q4R.mp4",
          "name": "spring-launch-1080p.mp4",
          "mimeType": "video/mp4",
          "description": "1080p H.264, 02:14, watermark applied" },
        { "type": "text",
          "text": "Render complete: 1080p H.264, 2m14s, watermark applied. Link valid for the task ttl (4h)." }
      ],
      "structuredContent": {
        "uri": "https://cdn.example.com/renders/01HZYX8Q4R.mp4",
        "mimeType": "video/mp4",
        "durationSeconds": 134,
        "preset": "1080p-h264",
        "watermarkApplied": true
      },
      "isError": false
    }
  }
}
```

Failure is the same shape with `status: "failed"` and the embedded result carrying `isError: true` plus an actionable message ("Source clip 3 is missing audio track; re-call after fixing res://... or pass preset 720p-h264"). Tool-domain failures stay inside the `CallToolResult` so the model sees them; only protocol-level problems (unknown taskId, bad params) are JSON-RPC errors.

### 3.5 Cancel

```json
{ "jsonrpc": "2.0", "id": 60, "method": "tasks/cancel",
  "params": { "taskId": "task_01HZYX8Q4R" } }
```

Returns the task with `status: "cancelled"`. Cancelling during `input_required` also resolves the outstanding elicitation server-side.

### 3.6 Recovery

Three layers, in order of preference:

1. **Persisted taskId** → just resume `tasks/get`. TaskIds are bound to the authenticated principal, not the transport session, so they survive reconnects — this is a server design commitment, stated in the tool description, not something the extension gives for free.
2. **Lost taskId** → `tasks/list`:

```json
{ "jsonrpc": "2.0", "id": 61, "method": "tasks/list", "params": { "cursor": null } }
```
```json
{ "result": {
    "tasks": [
      { "taskId": "task_01HZYX8Q4R", "status": "input_required",
        "statusMessage": "Waiting for user approval of watermark",
        "createdAt": "2026-07-29T18:02:11Z", "ttl": 14400000,
        "_meta": { "com.example.render/idempotencyKey": "render-spring-launch-7f3a" } } ],
    "nextCursor": null } }
```
   The server echoes the idempotency key in task `_meta` precisely so a recovering agent can match tasks to intents.
3. **TTL expired / task unknown** (`-32602 "Task not found"`) → re-call `tools/call` with the **same `idempotency_key`**. If the render artifact still exists server-side, the server returns a fresh already-`completed` task pointing at it; otherwise it starts a new render. Either way the agent never double-renders by accident.

Optional push: the server MAY emit `notifications/tasks/status` on transitions; agents may use it to poll immediately but MUST treat `tasks/get` as the source of truth (notifications are lossy across reconnects).

## 4. Agent-visible state machine

`working ⇄ input_required → completed | failed | cancelled` — terminal states are final; results are fetchable via `tasks/get` until `ttl` expiry, then gone.

## 5. Fallback for clients without task support

**Primary fallback — synchronous call with progress.** The server detects the missing client extension capability and runs the render inline on the original `tools/call`:

- Client should send a `progressToken`; server emits `notifications/progress` at least every 10 s so spec-conforming clients reset their request timeout:

```json
{ "jsonrpc": "2.0", "method": "notifications/progress",
  "params": { "progressToken": "vr-42", "progress": 37, "total": 100, "message": "Encoding: 37%" } }
```

- Watermark pause uses the same `elicitation/create`, just without the `related-task` meta, mid-call.
- The eventual response is the ordinary `CallToolResult` from §3.4.
- The tool description explicitly warns the agent to allow ~12 minutes for this tool, because a fixed 60 s client timeout is the most common way this fallback dies.

**Degraded fallback — no elicitation either.** `watermark: "ask"` cannot pause, so it fails fast rather than deciding for the user:

```json
{ "content": [ { "type": "text",
    "text": "This client cannot collect watermark approval. Ask the user, then re-call video_render with watermark: \"apply\" or \"omit\" and the same idempotency_key." } ],
  "isError": true }
```

The human decision stays with the human; the agent relays the question in conversation and retries with an explicit value. `idempotency_key` again guarantees the retry attaches to the same render slot.

**Deliberately omitted:** a `video_render_status` polling-tool shim. It would duplicate the task methods' authority, and the sync fallback already covers non-task clients for a job this short; a second status surface is exactly the kind of second home for one rule that drifts silently.
