# Scenario 3 (Long-running) — with-skill run

- **Date:** 2026-07-11
- **Tree:** `d586ce3` (main, post review-batch #53)
- **Mode:** with-skill (fresh general-purpose subagent; read `SKILL.md` + all seven `references/` files, explicitly skipped `tests/`; 8 tool-uses)
- **Score:** 7/7

## Exact prompt given

Same `video_render` prompt as the baseline (see `2026-07-11-scenario3-baseline.md`), preceded by an instruction to read `agent-friendly-mcp/SKILL.md` and every file under `references/` as authoritative guidance, and **not** to read anything under `tests/`.

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Task support declared at **both** levels (`tasks.requests.tools.call` + `execution.taskSupport`), not per-tool alone | **PASS** | Server: `"tasks": {"cancel": {}, "requests": {"tools": {"call": {}}}}` — the **nested** spec shape (unlike the baseline's array); tool: `execution.taskSupport: "optional"`. States "Both are declared" and "the per-tool `execution.taskSupport` flag alone is insufficient (§7)." Also correctly **withholds** `tasks/list` citing requestor-isolation. |
| A2 | Native task fields spec casing + statuses exactly `working/input_required/completed/failed/cancelled` | **PASS** | `taskId`, `status`, `statusMessage`, `createdAt`, `lastUpdatedAt`, `ttl`, `pollInterval` throughout; statuses used exactly; explicitly references `native-wire-shapes.md`. |
| A3 | `CreateTaskResult` nests `Task` under `result.task`; `tasks/get` returns it directly | **PASS** | Create response nests under `result.task` with a `model-immediate-response` `_meta`; `tasks/get` "returns … directly in `result` (not nested, and no `related-task` `_meta` — the `taskId` already travels)." |
| A4 | `progressToken` originates in request's `_meta`, not server-minted | **PASS** | Create request carries `_meta: {"progressToken": "pt_render_01"}`; `notifications/progress` echoes that token plus `io.modelcontextprotocol/related-task`. Explicitly "keyed by the requestor's `progressToken`." |
| A5 | Watermark pause via `input_required` with native recovery (preemptively call `tasks/result` and hold it open; input arrives as a separate request while pending) | **PASS** | "On seeing `input_required` the requestor **preemptively calls `tasks/result` and holds it open**. The pending input request is *not* the `tasks/result` response — it arrives as a separate server-to-client `elicitation/create` … while that call is pending"; "the already-held `tasks/result` call completes." |
| A6 | Fallback status/cancel tools labeled as convention, mirror native signals (state, poll, result location, expiry), don't replace `tasks/*` | **PASS** | Section titled "Fallback … (labeled convention)"; "mirrors the native signals (current state, when to poll again, result location, expiry) — it does not replace `tasks/*`"; fallback payloads carry `poll_after_ms`, `preview_resource_uri`, `terminal_states`, `expires_at`. |
| A7 | Native fields and house conventions not mixed; native casing never snake_cased | **PASS** | Native casing preserved everywhere; convention metadata under namespaced `_meta` (`com.video-render/errors`, `com.video-render/duration`) and labeled convention envelopes; domain fields stay `snake_case`; §7 coverage block asserts the discipline explicitly. |

**Result: 7/7.**
The three baseline gaps are closed: native progress via a request-originated `progressToken` (A4), the preemptive-hold `input_required` recovery (A5), and a convention-labeled fallback that mirrors expiry/result-location (A6).
The correct nested `tasks.requests.tools.call` shape (vs the baseline's array) also lands.

## Full final output

Verbatim final message returned by the with-skill subagent (the 8 tool calls were the skill-file reads it was instructed to perform; no other tool noise):

I've read the SKILL and all seven reference files (and deliberately skipped `tests/`).
Here is the complete design.

---

# `video_render` MCP contract (target: MCP 2025-11-25)

## Design summary and key decisions

A render takes 1–10 minutes and can pause mid-run for the user to approve a watermark.
That combination forces two decisions straight out of contract-checklist §7:

1. **Execution mode = task-augmented, not blocking.** 1–10 min exceeds a normal request/response turn, so the primary path is a task-augmented `tools/call` recovered through the native task lifecycle (`tasks/get` / `tasks/result` / `tasks/cancel`).
   A silent blocking call is wrong here.
2. **The watermark pause = native `input_required` + elicitation**, with a **labeled domain fallback** for clients that negotiate neither tasks nor elicitation.
   Tasks are experimental in 2025-11-25, so the fallback is mandatory, and it *mirrors* the native signals rather than replacing them (§7).

Granularity (§3, design-workflow Step 3): one **task-completing** `video_render` tool hides the internal encode → watermark → mux steps.
The watermark approval is a human-approval boundary — a named split exception — but it is expressed as an `input_required` turn *inside* the task, not as a mandatory pre-step tool.
The only separate tools are the recovery/fallback surface (`video_get_render_status`, `video_approve_watermark`, `video_cancel_render`), which exist because weaker clients can't use `tasks/*`.

`execution.taskSupport: "optional"` (not `"required"`): `required` would lock out every client without task support, and this tool needs to serve them via the fallback path.
On the non-task path the same tool returns immediately with a `render_id` (dispatched-but-unconfirmed, §3), and the agent drives the render with the domain tools.

---

## 1. Capability declarations

### Server capabilities (in the `initialize` result)

```json
{
  "capabilities": {
    "tasks": {
      "cancel": {},
      "requests": { "tools": { "call": {} } }
    },
    "resources": {
      "listChanged": true,
      "subscribe": true
    }
  }
}
```

Notes:
- `tasks.requests.tools.call` is the gate for task-augmented `tools/call`; the per-tool `execution.taskSupport` flag alone is insufficient (§7).
  Both are declared.
- `tasks.cancel` is advertised so `tasks/cancel` is usable.
- **`tasks/list` is deliberately omitted.** Per §7, do not advertise `tasks/list` where requestor isolation can't be enforced — it would enumerate other requestors' render handles.
  Status recovery is per-`taskId` via `tasks/get`, plus the domain `video_get_render_status` fallback.
- `resources.subscribe` / `listChanged` support the finished-video resource (delivered as a `resource_link`, §3).
- Watermark approval needs a **client** capability, negotiated from the client side: `client.capabilities.elicitation` (form and/or url mode).
  The server does not declare this; it consumes it and degrades when absent (see §6 fallback below).

### Negotiated-capability block (convention metadata, rides in the capability summary)

This is the agent-readable statement of what the server depends on and what weaker clients get.
It is a **convention extension**, but every capability string it names is a native fully-qualified path (§1).

```json
{
  "negotiated_capabilities": {
    "server": [
      "server.capabilities.tasks.requests.tools.call",
      "server.capabilities.tasks.cancel",
      "server.capabilities.resources.subscribe",
      "server.capabilities.resources.listChanged"
    ],
    "client": [
      "client.capabilities.elicitation.form",
      "client.capabilities.elicitation.url"
    ],
    "fallbacks": {
      "no_tasks": "Call video_render without task augmentation; it returns a render_id immediately. Drive the render with video_get_render_status (poll), video_approve_watermark, and video_cancel_render.",
      "no_elicitation": "The render pauses at state 'awaiting_watermark_approval'. Approve or reject via video_approve_watermark(render_id, approved); no elicitation/create is sent.",
      "no_resource_subscribe": "Poll the finished video's resource with annotations.lastModified instead of subscribing."
    }
  }
}
```

### Capability summary (§1/§2/§7 — exposed via resource, discovery tool, or `instructions`)

```json
{
  "server": {
    "name": "video-render-mcp",
    "version": "1.0.0",
    "fingerprint": "video-render-mcp@1.0.0+c41f9a2",
    "transport": "streamable-http",
    "summary": "Render a video from a source asset with optional watermark; renders run 1-10 min as recoverable tasks and can pause for watermark approval."
  },
  "does": [
    "Render a source asset to mp4/webm/mov at 720p/1080p/4k.",
    "Apply a text or image watermark, optionally gated on user approval.",
    "Run the render as a recoverable task with progress, cancel, and result retrieval.",
    "Deliver the finished video as a fetchable resource link."
  ],
  "does_not": [
    "Edit, trim, or splice clips (render only, no timeline editing).",
    "Upload or manage source assets (use the asset service; this server consumes existing source_asset_id values).",
    "Live/streaming transcode or real-time capture.",
    "Store renders past the task ttl / resource expiry."
  ],
  "error_carriers": {
    "tool_errors": "structuredContent",
    "non_tool_rpc_errors": "error.data"
  },
  "long_running": {
    "primary": "Task-augmented tools/call on video_render; poll tasks/get (respect pollInterval), retrieve with tasks/result, cancel with tasks/cancel.",
    "watermark_approval": "Native path: task enters status 'input_required' and the server sends elicitation/create (form or url) tagged with the related task. Preemptively call tasks/result and hold it open per the spec.",
    "fallback": "Clients without tasks or elicitation use video_get_render_status / video_approve_watermark / video_cancel_render, which mirror the native signals."
  },
  "prerequisites": {
    "required_scopes": ["render:write", "asset:read"],
    "negotiated_capabilities": { "...": "see negotiated_capabilities block above" }
  }
}
```

---

## 2. Tool definition (`video_render`, entry in `tools/list`)

```json
{
  "name": "video_render",
  "title": "Render video",
  "description": "Render a source asset into a finished video, optionally with a watermark.\n\nWhen to use: the user has an uploaded source asset (source_asset_id) and wants a rendered output file. Not for editing/trimming — render only.\n\nDuration: typically 1-10 min. This exceeds a normal request timeout, so call it as a task (augment tools/call with a `task` field). Task-augmented, it returns a taskId before any server timeout; poll tasks/get and retrieve with tasks/result. Clients without task support may call it un-augmented: it returns a render_id immediately and you drive it with video_get_render_status / video_approve_watermark / video_cancel_render.\n\nWatermark approval: when `watermark.mode` is not \"none\" and `require_watermark_approval` is true (the default), the render pauses after compositing a preview. On the native path the task enters status 'input_required' and the server sends elicitation/create with a preview link; call tasks/result preemptively and hold it open. On the fallback path the render sits at state 'awaiting_watermark_approval' until you call video_approve_watermark. A rejected watermark ends the render with code `watermark_rejected` (no output).\n\nIdempotency: pass `idempotency_key` to make retries after a dropped connection safe; identical keys within 30 min return the same render rather than starting a new one. Omit it and each call starts a new render.\n\nFor the failure modes this tool can return, see the error catalog under _meta (com.video-render/errors) - do not infer error semantics from this description.\n\nExample: {\"source_asset_id\": \"as_9f2\", \"output_format\": \"mp4\", \"resolution\": \"1080p\", \"watermark\": {\"mode\": \"text\", \"text\": \"CONFIDENTIAL\", \"position\": \"bottom_right\"}}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["source_asset_id"],
    "properties": {
      "source_asset_id": {
        "type": "string",
        "pattern": "^as_[a-z0-9]{3,}$",
        "description": "Id of an existing uploaded source asset (as_…). Not a filename or URL."
      },
      "output_format": {
        "type": "string",
        "enum": ["mp4", "webm", "mov"],
        "default": "mp4",
        "description": "Container/codec profile. Omitted means mp4 (the server applies this default)."
      },
      "resolution": {
        "type": "string",
        "enum": ["720p", "1080p", "4k"],
        "default": "1080p",
        "description": "Output resolution. Omitted means 1080p (the server applies this default)."
      },
      "watermark": {
        "type": "object",
        "description": "Watermark spec. Omit the whole object for no watermark (equivalent to mode='none').",
        "required": ["mode"],
        "properties": {
          "mode": {
            "type": "string",
            "enum": ["none", "text", "image"],
            "description": "Discriminator. 'text' requires `text`; 'image' requires `image_asset_id`; 'none' ignores the other fields."
          },
          "text": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "Overlay text. Required when mode='text'."
          },
          "image_asset_id": {
            "type": "string",
            "pattern": "^as_[a-z0-9]{3,}$",
            "description": "Watermark image asset (as_…). Required when mode='image'."
          },
          "position": {
            "type": "string",
            "enum": ["top_left", "top_right", "bottom_left", "bottom_right", "center"],
            "default": "bottom_right",
            "description": "Placement. Omitted means bottom_right (the server applies this default)."
          }
        },
        "additionalProperties": false
      },
      "require_watermark_approval": {
        "type": "boolean",
        "default": true,
        "description": "If true and a watermark is applied, pause for user approval before finalizing. Omitted means true (the server applies this default). Ignored when no watermark is applied."
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "description": "Optional client-supplied key; identical keys within 30 min return the same render instead of starting a new one. Omitted means no deduplication - each call starts a new render."
      }
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["render_id", "result_resource_uri", "output_format", "duration_seconds", "width", "height", "watermark_applied"],
    "properties": {
      "render_id": { "type": "string", "description": "Stable id for this render; accepted by video_get_render_status / video_approve_watermark / video_cancel_render." },
      "result_resource_uri": { "type": "string", "format": "uri", "description": "video://renders/{render_id}/output — fetch via resources/read or the resource_link in content." },
      "output_format": { "type": "string", "enum": ["mp4", "webm", "mov"] },
      "duration_seconds": { "type": "number" },
      "width": { "type": "integer" },
      "height": { "type": "integer" },
      "watermark_applied": { "type": "boolean" }
    },
    "additionalProperties": false
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": false,
    "openWorldHint": false
  },
  "execution": { "taskSupport": "optional" },
  "_meta": {
    "com.video-render/errors": [
      {"code": "source_not_found", "temporary": false, "description": "source_asset_id does not match a readable asset."},
      {"code": "invalid_field", "temporary": false, "description": "A field failed validation (e.g. mode='text' without `text`); see details.field."},
      {"code": "watermark_rejected", "temporary": false, "description": "User rejected the watermark; render ended with no output. Re-call with a corrected watermark or require_watermark_approval:false."},
      {"code": "render_failed", "temporary": true, "description": "Encoder failed; safe to retry the same request (ideally with idempotency_key)."},
      {"code": "quota_exceeded", "temporary": true, "description": "Render minutes exhausted for the window; honour retry_after_ms."},
      {"code": "rate_limited", "temporary": true, "description": "Too many concurrent renders; honour retry_after_ms."},
      {"code": "capability_not_negotiated", "temporary": false, "description": "Watermark approval needed but client negotiated neither tasks nor elicitation; use the video_approve_watermark fallback."}
    ],
    "com.video-render/duration": {"typical_seconds": [60, 600], "runs_as": "task"}
  }
}
```

Why these choices map to the checklist:
- **Annotations honest (§3):** mutating (produces a stored render) → `readOnlyHint: false`; not destructive; `idempotentHint: false` because without an `idempotency_key` each call starts a new render, which is exactly why the key is offered with a declared 30-min window. `openWorldHint: false` — it operates within the render service, not arbitrary external endpoints (source is a managed asset id, not a URL).
- **`outputSchema` scoped to success (§3):** an `isError: true` result carries the §6 envelope instead, documented via the `_meta` error catalog.
- **Watermark union avoided (§3 "prefer simple, portable"):** a `mode` discriminator with per-mode required fields, not an `anyOf`.

---

## 3. Wire shapes — CREATE (task-augmented)

**Request** — client augments `tools/call` with a `task` field and supplies a `progressToken`:

```json
{
  "jsonrpc": "2.0",
  "id": "req_create_01",
  "method": "tools/call",
  "params": {
    "name": "video_render",
    "arguments": {
      "source_asset_id": "as_9f2",
      "output_format": "mp4",
      "resolution": "1080p",
      "watermark": { "mode": "text", "text": "CONFIDENTIAL", "position": "bottom_right" },
      "idempotency_key": "run-2026-07-11-a"
    },
    "task": { "ttl": 86400000 },
    "_meta": { "progressToken": "pt_render_01" }
  }
}
```

**Response** — a `CreateTaskResult` (the `Task` nested under `result.task`, native casing):

```json
{
  "jsonrpc": "2.0",
  "id": "req_create_01",
  "result": {
    "task": {
      "taskId": "task_render_01",
      "status": "working",
      "statusMessage": "Render accepted; encoding.",
      "createdAt": "2026-07-11T17:02:00Z",
      "lastUpdatedAt": "2026-07-11T17:02:00Z",
      "ttl": 86400000,
      "pollInterval": 5000
    },
    "_meta": {
      "io.modelcontextprotocol/model-immediate-response": "Render started as task_render_01; typically 1-10 min. Results retrievable for 24h."
    }
  }
}
```

**Progress** (optional push, keyed by the requestor's `progressToken`, tagged with the related task):

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/progress",
  "params": {
    "progressToken": "pt_render_01",
    "progress": 0.4,
    "total": 1,
    "message": "Encoded 40% of frames.",
    "_meta": { "io.modelcontextprotocol/related-task": { "taskId": "task_render_01" } }
  }
}
```

---

## 4. Wire shapes — POLL (`tasks/get`) and the watermark pause

`tasks/get` with `params: {"taskId": "task_render_01"}`, respecting `pollInterval`.
The `Task` is returned directly in `result` (not nested, and no `related-task` `_meta` — the `taskId` already travels in the message).

**While rendering:**

```json
{
  "jsonrpc": "2.0",
  "id": "req_poll_1",
  "result": {
    "taskId": "task_render_01",
    "status": "working",
    "statusMessage": "Compositing watermark preview.",
    "createdAt": "2026-07-11T17:02:00Z",
    "lastUpdatedAt": "2026-07-11T17:05:12Z",
    "ttl": 86400000,
    "pollInterval": 5000
  }
}
```

**Paused for watermark approval** — status flips to `input_required`:

```json
{
  "jsonrpc": "2.0",
  "id": "req_poll_2",
  "result": {
    "taskId": "task_render_01",
    "status": "input_required",
    "statusMessage": "Watermark preview ready; awaiting approval.",
    "createdAt": "2026-07-11T17:02:00Z",
    "lastUpdatedAt": "2026-07-11T17:06:00Z",
    "ttl": 86400000,
    "pollInterval": 5000
  }
}
```

**Native `input_required` recovery (§7, fixed path).** On seeing `input_required` the requestor **preemptively calls `tasks/result` and holds it open**.
The pending input request is *not* the `tasks/result` response — it arrives as a separate server-to-client `elicitation/create` carrying the related-task `_meta` while that call is pending:

```json
{
  "jsonrpc": "2.0",
  "id": "elicit_wm_01",
  "method": "elicitation/create",
  "params": {
    "message": "Approve this watermark for task_render_01? Preview: video://renders/render_01/watermark-preview.png (text 'CONFIDENTIAL', bottom-right).",
    "requestedSchema": {
      "type": "object",
      "required": ["approved"],
      "properties": {
        "approved": { "type": "boolean", "description": "true to finalize with the watermark, false to reject." },
        "feedback": { "type": "string", "description": "Optional note, e.g. a requested change." }
      }
    },
    "_meta": { "io.modelcontextprotocol/related-task": { "taskId": "task_render_01" } }
  }
}
```

Client response to the elicitation:

```json
{
  "jsonrpc": "2.0",
  "id": "elicit_wm_01",
  "result": { "action": "accept", "content": { "approved": true } }
}
```

For clients that prefer a visual approval, the same pause may use **URL-mode** elicitation (an approval URL rendering the preview image) instead of the form schema above — declared via `client.capabilities.elicitation.url`.
Once input arrives the task returns to `working` and the **already-held `tasks/result` call completes** with the terminal result (re-call only if the call itself failed).

---

## 5. Wire shapes — RECOVER (`tasks/result`) and cancel

`tasks/result` with `params: {"taskId": "task_render_01"}` blocks until terminal and returns exactly what the un-augmented `tools/call` would have.
It **MUST** carry the related-task `_meta`.
Domain payload rides in `structuredContent`; the finished video is a `resource_link`, not inlined bytes (§3):

```json
{
  "jsonrpc": "2.0",
  "id": "req_result",
  "result": {
    "content": [
      { "type": "text", "text": "{\"render_id\":\"render_01\",\"result_resource_uri\":\"video://renders/render_01/output\",\"duration_seconds\":92.4}" },
      {
        "type": "resource_link",
        "uri": "video://renders/render_01/output",
        "name": "render_01.mp4",
        "description": "1080p mp4, watermark 'CONFIDENTIAL' bottom-right.",
        "mimeType": "video/mp4",
        "annotations": { "audience": ["user"], "priority": 0.9, "lastModified": "2026-07-11T17:08:30Z" }
      }
    ],
    "structuredContent": {
      "render_id": "render_01",
      "result_resource_uri": "video://renders/render_01/output",
      "output_format": "mp4",
      "duration_seconds": 92.4,
      "width": 1920,
      "height": 1080,
      "watermark_applied": true
    },
    "isError": false,
    "_meta": { "io.modelcontextprotocol/related-task": { "taskId": "task_render_01" } }
  }
}
```

**Rejected watermark** — terminal `failed`, carrying the §6 error envelope (not the success `outputSchema`):

```json
{
  "jsonrpc": "2.0",
  "id": "req_result",
  "result": {
    "content": [ { "type": "text", "text": "Watermark rejected; render produced no output. Re-render with a corrected watermark or require_watermark_approval:false." } ],
    "structuredContent": {
      "code": "watermark_rejected",
      "message": "User rejected the watermark; render ended with no output.",
      "temporary": false,
      "retry_after_ms": null,
      "repair": {
        "next_step": "rerender_with_new_watermark",
        "tool": "video_render",
        "arguments": {
          "source_asset_id": "as_9f2",
          "output_format": "mp4",
          "resolution": "1080p",
          "watermark": { "mode": "text", "text": "CONFIDENTIAL", "position": "top_right" }
        },
        "alternative": "Re-call video_render with require_watermark_approval:false to skip approval."
      },
      "request_id": "req_result",
      "fingerprint": "video-render-mcp@1.0.0+c41f9a2"
    },
    "isError": true,
    "_meta": { "io.modelcontextprotocol/related-task": { "taskId": "task_render_01" } }
  }
}
```

The `repair` object preserves caller intent — it re-passes every still-valid argument plus the minimal correction (§6). `retry_after_ms` is emitted as `null` (always present alongside `temporary`).

**Cancel** — `tasks/cancel` with `params: {"taskId": "task_render_01"}`.
Returns the `Task` directly in `result`, terminal `cancelled`, no related-task `_meta`:

```json
{
  "jsonrpc": "2.0",
  "id": "req_cancel",
  "result": {
    "taskId": "task_render_01",
    "status": "cancelled",
    "statusMessage": "Render cancelled by request.",
    "createdAt": "2026-07-11T17:02:00Z",
    "lastUpdatedAt": "2026-07-11T17:04:10Z",
    "ttl": 86400000
  }
}
```

An error envelope for a validation failure at create time (tool-result error, `isError: true` at top level):

```json
{
  "isError": true,
  "content": [ { "type": "text", "text": "watermark.mode='text' requires `text`. Supply text or set mode='none'." } ],
  "structuredContent": {
    "code": "invalid_field",
    "message": "Missing required field for watermark mode.",
    "details": { "field": "watermark.text", "reason": "Required when watermark.mode='text'." },
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "supply_watermark_text",
      "tool": "video_render",
      "arguments": { "source_asset_id": "as_9f2", "watermark": { "mode": "text", "text": "CONFIDENTIAL", "position": "bottom_right" } }
    },
    "request_id": "req_create_02",
    "fingerprint": "video-render-mcp@1.0.0+c41f9a2"
  }
}
```

---

## 6. Fallback for clients without task support (labeled convention)

Tasks are experimental, so a client may negotiate neither `tasks` nor `elicitation`.
The fallback **mirrors** the native signals (current state, when to poll again, result location, expiry) — it does not replace `tasks/*` (§7).

**Un-augmented `video_render`** returns immediately (dispatched-but-unconfirmed, §3): `structuredContent` carries the `render_id`, a non-terminal state, and a `follow_up` reusing the §6 `repair` shape:

```json
{
  "isError": false,
  "content": [ { "type": "text", "text": "Render render_01 started (state: rendering). Poll video_get_render_status." } ],
  "structuredContent": {
    "render_id": "render_01",
    "state": "rendering",
    "terminal": false,
    "poll_after_ms": 5000,
    "follow_up": {
      "next_step": "poll_render_status",
      "tool": "video_get_render_status",
      "arguments": { "render_id": "render_01" }
    }
  }
}
```

**`video_get_render_status`** — mirrors `tasks/get`.
When paused, it names the approval surface and the visual preview (the native path's `elicitation/create` equivalent):

```json
{
  "render_id": "render_01",
  "state": "awaiting_watermark_approval",
  "terminal": false,
  "poll_after_ms": 5000,
  "progress": 0.85,
  "preview_resource_uri": "video://renders/render_01/watermark-preview.png",
  "pending_input": {
    "next_step": "approve_watermark",
    "tool": "video_approve_watermark",
    "arguments": { "render_id": "render_01", "approved": true }
  },
  "terminal_states": ["completed", "failed", "cancelled"],
  "expires_at": "2026-07-12T17:02:00Z"
}
```

A `completed` status carries `result_resource_uri` (mirroring `tasks/result`); a `failed` one carries the §6 error envelope with `code: "watermark_rejected"` etc.

**`video_approve_watermark`** input: `{render_id (required), approved (required boolean), feedback (optional)}` — the non-elicitation equivalent of the elicitation response. **`video_cancel_render`** input: `{render_id (required)}` — mirrors `tasks/cancel`.

These three tools use the same `video_` prefix, consistent verbs, closed schemas, `snake_case` names, and the same symbolic error codes as `video_render` — so an agent that learned the native path can drive the fallback without relearning the taxonomy. *Forward-compat (§7):* keeping status/result/cancel expressible as ordinary tools is exactly the hedge that survives tasks moving to a negotiated extension in the 2026-07-28 RC.

---

## 7. Checklist coverage (design done-criteria)

- **§1 Server-Level** — distinctive name `video-render-mcp`, transport declared, scopes, negotiated-capability paths + fallbacks, state-handle discipline on `taskId`/`render_id` (opaque, ttl-bounded, auth re-checked, `tasks/list` withheld for isolation). ✓
- **§2 Discovery** — capability summary with explicit negative scope; compact tool definitions; error catalog inline-minimal with full catalog under `_meta`. ✓
- **§3 Tools** — task-completing granularity, closed schemas, discriminator not union, honest annotations, idempotency key, dispatched-vs-applied on the fallback path, `resource_link` delivery. ✓
- **§4 Resources** — finished video as `video://renders/{render_id}/output` with `mimeType`/`lastModified`; subscribe/listChanged advertised. ✓ (Full resource index/chunking is **N/A** — a single opaque binary output, not a browsable/chunkable document.)
- **§5 Prompts** — **N/A**: no prompt is load-bearing; the render task is a single tool call.
- **§6 Failure Recovery** — one envelope, two carriers named in the summary; symbolic codes; field-level `details`; `temporary`/`retry_after_ms` invariants; `repair` preserving intent; `capability_not_negotiated` → fallback. ✓
- **§7 Long-Running** — task-augmented primary, `input_required` + elicitation for the watermark pause, native poll/result/cancel, labeled domain fallback, both-level task declaration. ✓
- **§8 Token Efficiency** — structured output authoritative, video linked not inlined, immediate `model-immediate-response` string avoids a silent wait, timestamps RFC3339 UTC, `ttl`/`pollInterval` in ms per spec. ✓ (No large list payload → no `detail` toggle needed.)
- **§9 Versioning** — capability fingerprint published (HTTP transport → a caching/remote client benefits), deterministic ordering, list-changed on the resource surface. ✓

All native task/list/completion casing follows `native-wire-shapes.md`: `taskId`, `status`, `statusMessage`, `createdAt`, `lastUpdatedAt`, `ttl`, `pollInterval`, `nextCursor`; domain fields stay `snake_case`; convention metadata (`errors`, `fingerprint`, `follow_up`, `negotiated_capabilities`) rides under namespaced `_meta` or documented convention envelopes, never masquerading as protocol.
