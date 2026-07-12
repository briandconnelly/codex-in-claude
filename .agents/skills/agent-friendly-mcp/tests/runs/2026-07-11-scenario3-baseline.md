# Scenario 3 (Long-running) — baseline run

- **Date:** 2026-07-11
- **Tree:** `d586ce3` (main, post review-batch #53)
- **Mode:** baseline (fresh general-purpose subagent, prompt only, forbidden from reading the repo/skill; 0 tool-uses confirms no skill access)
- **Score:** 4/7

## Exact prompt given

> Design the agent-facing MCP contract for a `video_render` tool whose renders take 1–10 minutes and can pause waiting for the user to approve a watermark.
> Target MCP 2025-11-25.
> Produce: the capability declarations, the tool definition, the wire shapes an agent sees when creating, polling, and recovering the render, and whatever fallback you think clients without task support need.

(Wrapped with a guardrail forbidding reading any repo file, especially under `agent-friendly-mcp/`.)

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Task support declared at **both** levels (`tasks.requests.tools.call` + `execution.taskSupport`), not per-tool alone | **PASS** (with caveat) | Server declares `"tasks": {"requests": ["tools/call"], "list": true, "cancel": true}` and the tool declares `execution.taskSupport: "optional"` — both levels present. **Caveat:** `requests` uses an array `["tools/call"]`, not the spec's nested `requests.tools.call` object — a shape deviation the with-skill run gets right. |
| A2 | Native task fields spec casing + statuses exactly `working/input_required/completed/failed/cancelled` | **PASS** | `taskId`, `status`, `statusMessage`, `createdAt`, `lastUpdatedAt`, `ttl`, `pollInterval` all correct casing; statuses `working`, `input_required`, `completed`, `cancelled`, `failed` used correctly. |
| A3 | `CreateTaskResult` nests `Task` under `result.task`; `tasks/get` returns it directly | **PASS** | Create response nests under `result.task`; `tasks/get` returns task fields directly in `result`. |
| A4 | `progressToken` originates in request's `_meta`, not server-minted | **FAIL** | No `progressToken` used. Invented a non-native `notifications/tasks/status` notification instead of `notifications/progress` keyed by a request-supplied `progressToken`. |
| A5 | Watermark pause via `input_required` with native recovery (preemptively call `tasks/result` and hold it open; input arrives as a separate request while pending) | **FAIL** | Uses `input_required` + `elicitation/create`, but drives it by **polling** `tasks/get` then receiving a separate elicitation — never describes the "call `tasks/result` preemptively and hold it open" recovery pattern the spec/skill require. |
| A6 | Fallback status/cancel tools labeled as convention, mirror native signals (state, poll, result location, expiry), don't replace `tasks/*` | **FAIL** | Provides `video_render_status`/`video_render_approve` fallback that mirrors state + `pollAfterMs`, but does not label them as a non-native convention and omits expiry; "result location" only implied. |
| A7 | Native fields and house conventions not mixed; native casing never snake_cased | **PASS** | Consistent camelCase for native fields; house fields (`pollAfterMs`, `renderId`) kept in fallback payloads; native `_meta` key `io.modelcontextprotocol/related-task` used correctly; no native field snake_cased. |

**Passed:** A1, A2, A3, A7 (4/7).
This is a strong baseline — a current model knows the MCP task lifecycle well — but it misses the two subtlest, most error-prone points the skill teaches: native progress via a request-originated `progressToken` (A4) and the preemptive-hold `input_required` recovery (A5), plus the convention-labeling discipline on the fallback (A6).

## Full final output

Verbatim final message returned by the baseline subagent (no tool noise to elide — the run made 0 tool calls):

# MCP Contract Design: `video_render` (target MCP 2025-11-25)

Long renders (1–10 min) that can pause for user approval are exactly what the **tasks** mechanism in MCP 2025-11-25 exists for.
The design below uses tasks as the primary path, `elicitation` for the watermark approval pause, and a polling-tool fallback for clients that never negotiated task support.

A note on fidelity: I'm reconstructing exact field names from the 2025-11-25 tasks/elicitation model from memory.
The *shapes and lifecycle* are what matter here and are faithful to MCP conventions (JSON-RPC 2.0, camelCase, capability negotiation, `_meta` correlation); treat specific key spellings like `pollInterval`/`ttl` as the reconstructed names they are.

---

## 1. Capability declarations

Tasks are opt-in on both sides, negotiated at `initialize`.
Elicitation must be a *client* capability or the watermark pause has nowhere to land.

**Client → server (`initialize` params):**

```json
{
  "protocolVersion": "2025-11-25",
  "capabilities": {
    "tasks": {},
    "elicitation": {}
  },
  "clientInfo": { "name": "example-agent", "version": "1.4.0" }
}
```

**Server → client (`initialize` result):**

```json
{
  "protocolVersion": "2025-11-25",
  "capabilities": {
    "tools": { "listChanged": true },
    "elicitation": {},
    "tasks": {
      "requests": ["tools/call"],
      "list": true,
      "cancel": true
    }
  },
  "serverInfo": { "name": "video-render-server", "version": "2.0.0" }
}
```

Reading of the `tasks` object:
- `requests`: which request methods this server will accept a task augmentation on.
  Here, only `tools/call`.
- `list` / `cancel`: server supports `tasks/list` and `tasks/cancel` (needed for recovery and abort).

If the client omits `tasks` here, it never learns about task handles, and the server must fall back (Section 6).
If the client omits `elicitation`, the watermark step cannot pause interactively — the server should either fail the render with a clear error or fall back to a non-interactive default (see the tool's `watermark.onNoElicitation` argument below).

---

## 2. Tool definition

Advertised in `tools/list`.
The key addition for 2025-11-25 is the `execution` block telling the client this tool wants to run as a task.

```json
{
  "name": "video_render",
  "title": "Render Video",
  "description": "Render a video from a source timeline. Takes 1–10 minutes. May pause to ask the user to approve a watermark before finalizing. Prefer calling this as a task; if you cannot, poll video_render_status with the returned renderId.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "sourceUri": {
        "type": "string",
        "format": "uri",
        "description": "URI of the source project/timeline to render."
      },
      "format": {
        "type": "string",
        "enum": ["mp4", "webm", "mov"],
        "default": "mp4"
      },
      "resolution": {
        "type": "string",
        "enum": ["720p", "1080p", "4k"],
        "default": "1080p"
      },
      "watermark": {
        "type": "object",
        "description": "Watermark settings. The server pauses for user approval before burning this in.",
        "properties": {
          "text": { "type": "string" },
          "position": {
            "type": "string",
            "enum": ["lower-right", "lower-left", "upper-right", "upper-left", "center"],
            "default": "lower-right"
          },
          "onNoElicitation": {
            "type": "string",
            "enum": ["skip", "apply", "fail"],
            "default": "fail",
            "description": "What to do if the client cannot show an approval prompt (no elicitation capability)."
          }
        },
        "required": ["text"]
      }
    },
    "required": ["sourceUri"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "renderId": { "type": "string" },
      "url": { "type": "string", "format": "uri" },
      "durationSeconds": { "type": "number" },
      "watermarkApplied": { "type": "boolean" }
    },
    "required": ["renderId", "url"]
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": false,
    "openWorldHint": true
  },
  "execution": {
    "taskSupport": "optional"
  }
}
```

On `execution.taskSupport`:
- `"optional"` (chosen): task-capable clients get the async path; older clients can still call the tool and get the fallback behavior.
  This maximizes reach for a public tool.
- `"required"` would be cleaner but locks out non-task clients entirely — I avoid it precisely because a fallback is wanted.
- `"forbidden"` would be wrong for a 10-minute job.

---

## 3. Create the render (task-augmented `tools/call`)

The agent opts a normal `tools/call` into task execution by adding a `task` object to params. `ttl` requests how long the server should retain the task/result after completion.

**Request:**

```json
{
  "jsonrpc": "2.0",
  "id": 42,
  "method": "tools/call",
  "params": {
    "name": "video_render",
    "arguments": {
      "sourceUri": "mcp://projects/promo-cut-3",
      "format": "mp4",
      "resolution": "1080p",
      "watermark": { "text": "ACME Studios", "position": "lower-right" }
    },
    "task": {
      "ttl": 900000
    }
  }
}
```

**Response — the server returns *immediately* with a task handle, not a `CallToolResult`:**

```json
{
  "jsonrpc": "2.0",
  "id": 42,
  "result": {
    "task": {
      "taskId": "task_9f2c7a",
      "status": "working",
      "statusMessage": "Render queued",
      "createdAt": "2026-07-11T15:02:11Z",
      "lastUpdatedAt": "2026-07-11T15:02:11Z",
      "ttl": 900000,
      "pollInterval": 5000
    }
  }
}
```

The agent now holds `taskId` and knows to poll no faster than `pollInterval` (5 s).

---

## 4. Poll — `tasks/get`, the watermark pause, and `tasks/result`

### 4a. Normal progress

```json
{ "jsonrpc": "2.0", "id": 43, "method": "tasks/get", "params": { "taskId": "task_9f2c7a" } }
```

```json
{
  "jsonrpc": "2.0",
  "id": 43,
  "result": {
    "taskId": "task_9f2c7a",
    "status": "working",
    "statusMessage": "Encoding frames 4200/9000",
    "createdAt": "2026-07-11T15:02:11Z",
    "lastUpdatedAt": "2026-07-11T15:04:30Z",
    "ttl": 900000,
    "pollInterval": 5000
  }
}
```

Servers that support it also emit an unsolicited notification on each transition so a connected agent needn't busy-poll:

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/tasks/status",
  "params": { "taskId": "task_9f2c7a", "status": "working", "statusMessage": "Encoding frames 4200/9000" }
}
```

### 4b. The pause — status flips to `input_required`, server issues an elicitation

When the render reaches the watermark step, the task transitions to `input_required` and the server sends a **server→client `elicitation/create`** request, correlated to the task via `_meta`:

Poll now shows:

```json
{
  "jsonrpc": "2.0",
  "id": 44,
  "result": {
    "taskId": "task_9f2c7a",
    "status": "input_required",
    "statusMessage": "Waiting for watermark approval",
    "createdAt": "2026-07-11T15:02:11Z",
    "lastUpdatedAt": "2026-07-11T15:05:02Z",
    "ttl": 900000,
    "pollInterval": 5000
  }
}
```

The elicitation request the client receives:

```json
{
  "jsonrpc": "2.0",
  "id": "elic-1",
  "method": "elicitation/create",
  "params": {
    "message": "Apply watermark \"ACME Studios\" to the lower-right of promo-cut-3 before finalizing?",
    "requestedSchema": {
      "type": "object",
      "properties": {
        "approved": { "type": "boolean", "title": "Apply watermark" },
        "position": {
          "type": "string",
          "enum": ["lower-right", "lower-left", "upper-right", "upper-left", "center"],
          "default": "lower-right",
          "title": "Position"
        }
      },
      "required": ["approved"]
    },
    "_meta": {
      "io.modelcontextprotocol/related-task": { "taskId": "task_9f2c7a" }
    }
  }
}
```

Client responds (elicitation actions are `accept` / `decline` / `cancel`):

```json
{
  "jsonrpc": "2.0",
  "id": "elic-1",
  "result": {
    "action": "accept",
    "content": { "approved": true, "position": "lower-right" }
  }
}
```

The task returns to `working`.
If the user chose `decline`/`cancel`, the server decides per its policy — typically finalize without watermark or move the task to `failed`/`cancelled` with an explanatory `statusMessage`.

### 4c. Completion — fetch the result with `tasks/result`

Poll eventually shows terminal status:

```json
{
  "jsonrpc": "2.0",
  "id": 46,
  "result": {
    "taskId": "task_9f2c7a",
    "status": "completed",
    "statusMessage": "Render complete",
    "createdAt": "2026-07-11T15:02:11Z",
    "lastUpdatedAt": "2026-07-11T15:09:44Z",
    "ttl": 900000,
    "pollInterval": 5000
  }
}
```

The task object carries *status*, not the payload.
The agent retrieves the actual `CallToolResult` with `tasks/result`:

```json
{ "jsonrpc": "2.0", "id": 47, "method": "tasks/result", "params": { "taskId": "task_9f2c7a" } }
```

```json
{
  "jsonrpc": "2.0",
  "id": 47,
  "result": {
    "content": [
      {
        "type": "resource_link",
        "uri": "mcp://renders/task_9f2c7a/output.mp4",
        "name": "output.mp4",
        "mimeType": "video/mp4"
      }
    ],
    "structuredContent": {
      "renderId": "task_9f2c7a",
      "url": "mcp://renders/task_9f2c7a/output.mp4",
      "durationSeconds": 128,
      "watermarkApplied": true
    },
    "isError": false
  }
}
```

Calling `tasks/result` before the task is terminal returns a JSON-RPC error rather than a partial payload:

```json
{
  "jsonrpc": "2.0",
  "id": 47,
  "error": { "code": -32001, "message": "Task not complete", "data": { "status": "working" } }
}
```

**Failure** looks like a terminal task whose `tasks/result` returns a `CallToolResult` with `isError: true` (tool-level failure, e.g. bad codec) — reserving JSON-RPC errors for protocol-level problems (unknown `taskId`, expired TTL).

**Cancel** (agent aborts, or user backs out): `tasks/cancel`.

```json
{ "jsonrpc": "2.0", "id": 48, "method": "tasks/cancel", "params": { "taskId": "task_9f2c7a" } }
```

---

## 5. Recover after a dropped connection

A 10-minute render will outlive some transport blips and even client restarts.
Recovery relies on the `taskId` being durable server-side for `ttl`.

**Re-poll a known task** (the agent persisted `taskId` locally): just call `tasks/get` again after reconnect — no re-creation, no duplicate render.
This is the primary recovery path and is why the server assigns a durable `taskId` up front and honors the requested `ttl` past completion.

**Enumerate lost tasks** (agent lost its `taskId` too) via `tasks/list`:

```json
{ "jsonrpc": "2.0", "id": 60, "method": "tasks/list", "params": {} }
```

```json
{
  "jsonrpc": "2.0",
  "id": 60,
  "result": {
    "tasks": [
      {
        "taskId": "task_9f2c7a",
        "status": "input_required",
        "statusMessage": "Waiting for watermark approval",
        "createdAt": "2026-07-11T15:02:11Z",
        "lastUpdatedAt": "2026-07-11T15:05:02Z",
        "ttl": 900000,
        "pollInterval": 5000
      }
    ],
    "nextCursor": null
  }
}
```

Recovery notes the contract must make explicit:
- A task found in `input_required` after reconnect means a **pending elicitation was lost**.
  The client cannot answer a request whose id it never saw.
  The contract's rule: the server re-issues `elicitation/create` for that task on reconnect (or on the next `tasks/get`), so the approval prompt is re-presentable.
  The agent should expect this and not assume the render is wedged.
- Results survive until `ttl` after completion; after that `tasks/get`/`tasks/result` return `-32602`/`-32001`-style "unknown or expired task."
- `taskId` is the idempotency anchor — recovery never re-runs the render.

---

## 6. Fallback for clients without task support

A client that didn't negotiate `tasks` will call `video_render` as a plain `tools/call` (because `taskSupport` is `"optional"`, not `"required"`).
A 1–10 minute synchronous hold is unacceptable — it will blow request timeouts and gives no way to pause for approval.
Two-part fallback:

### 6a. `video_render` returns a handle immediately, plus a companion status tool

When called *without* task augmentation, the server does **not** block.
It starts the job and returns a normal `CallToolResult` carrying a `renderId` and a `pollAfterMs` hint in `structuredContent`:

```json
{
  "content": [
    { "type": "text", "text": "Render started. Poll video_render_status with renderId \"rnd_5521\" every ~5s." }
  ],
  "structuredContent": {
    "renderId": "rnd_5521",
    "status": "working",
    "pollAfterMs": 5000
  },
  "isError": false
}
```

Companion tool advertised alongside the main one:

```json
{
  "name": "video_render_status",
  "title": "Check Render Status",
  "description": "Poll a render started by video_render. Returns status and, when complete, the output URL. If status is 'input_required', call video_render_approve to resolve the watermark prompt.",
  "inputSchema": {
    "type": "object",
    "properties": { "renderId": { "type": "string" } },
    "required": ["renderId"]
  }
}
```

`video_render_status` result while running / paused / done:

```json
{
  "content": [{ "type": "text", "text": "Waiting for watermark approval." }],
  "structuredContent": {
    "renderId": "rnd_5521",
    "status": "input_required",
    "prompt": {
      "message": "Apply watermark \"ACME Studios\" (lower-right)?",
      "fields": ["approved", "position"]
    },
    "pollAfterMs": 5000
  },
  "isError": false
}
```

### 6b. Watermark approval without elicitation

Since a non-task client also likely lacks interactive elicitation, the pause is resolved by an explicit **approval tool** the agent can call after surfacing the question to its user in-band:

```json
{
  "name": "video_render_approve",
  "title": "Approve Render Watermark",
  "description": "Resolve a paused render's watermark prompt.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "renderId": { "type": "string" },
      "approved": { "type": "boolean" },
      "position": { "type": "string", "enum": ["lower-right","lower-left","upper-right","upper-left","center"] }
    },
    "required": ["renderId", "approved"]
  }
}
```

The agent loop becomes: `video_render` → poll `video_render_status` → on `input_required`, ask its own user, call `video_render_approve` → keep polling → on `completed`, read `url` from `structuredContent`.

This mirrors the native task lifecycle (create / poll / pause-resolve / complete) using only plain tool calls, so a task-unaware client gets the same capability with more round-trips.
For the truly non-interactive case, the `watermark.onNoElicitation` argument (`skip`/`apply`/`fail`) lets the caller pre-decide so the render never wedges on a prompt nobody can answer.

---

## Design summary

| Concern | Native (2025-11-25 tasks) | Fallback |
|---|---|---|
| Kick off | `tools/call` + `task` augmentation → immediate task handle | plain `tools/call` → `renderId` in `structuredContent` |
| Progress | `tasks/get` + `notifications/tasks/status` | `video_render_status` tool |
| Watermark pause | `status: input_required` + `elicitation/create` (correlated via `_meta` task id) | `status: input_required` + `video_render_approve` tool |
| Get output | `tasks/result` → `CallToolResult` | terminal `video_render_status` result |
| Recover | persisted `taskId` re-`tasks/get`; `tasks/list` to re-find; server re-issues lost elicitation; `ttl` retention | persisted `renderId` re-`video_render_status` |
| Abort | `tasks/cancel` | (add `video_render_cancel` if needed) |

The single idempotency anchor (`taskId` / `renderId`) is what makes both paths recoverable: the render is created once, and every subsequent interaction is a stateless lookup against that id.
