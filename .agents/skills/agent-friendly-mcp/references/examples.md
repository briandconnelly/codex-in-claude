# MCP Server Examples

Worked examples for a fictional MCP server, `slack-mcp`, that exposes Slack messaging, channels, and users to an agent. Every example is tagged with the [contract-checklist.md](contract-checklist.md) section(s) it demonstrates; decorative examples are not allowed. Use these as concrete shapes to mimic — not as schemas to copy verbatim.

## 1. Namespaced tool schema

Tool definition for `slack_send_message`. *Demonstrates §3 tool-shape rules.*

```json
{
  "name": "slack_send_message",
  "description": "Post a message to a Slack channel or DM.\n\nWhen to use: any time the agent has composed a message and a destination (channel id, user id, or thread ts) and wants to deliver it. Prefer this over `slack_create_channel` + post when the channel already exists.\n\nUsage notes:\n- DMs to users require a `user_id`, not a channel name; resolve via `slack_lookup_user` first.\n- Threaded replies require both `channel_id` and `thread_ts`; omitting `thread_ts` posts a new top-level message.\n- If a call times out, do not blind-retry: re-send with the same `idempotency_key`, or check delivery via `slack_list_messages` before retrying without one.\n\nFor the failure modes this tool can return (channel archived, not found, rate limited, etc.), see the `errors` field below — do not infer error semantics from this description.\n\nExample: {\"channel_id\": \"C0123ABCD\", \"text\": \"Deploy finished.\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["channel_id", "text"],
    "properties": {
      "channel_id": {
        "type": "string",
        "pattern": "^[CDG][A-Z0-9]{8,}$",
        "description": "Slack channel id (C…), DM id (D…), or group id (G…). Not a channel name."
      },
      "text": {
        "type": "string",
        "minLength": 1,
        "maxLength": 40000,
        "description": "Message body. Markdown subset per Slack mrkdwn rules."
      },
      "thread_ts": {
        "type": "string",
        "pattern": "^[0-9]+\\.[0-9]+$",
        "description": "Parent message timestamp; if set, this becomes a threaded reply."
      },
      "broadcast_to_channel": {
        "type": "boolean",
        "default": false,
        "description": "If `thread_ts` is set, also surface this reply in the main channel. Omitted means false (the server applies this default)."
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "description": "Optional client-supplied key; re-sends with the same key within 10 minutes are deduplicated to one post. Omitted means no deduplication — each call posts."
      }
    },
    "additionalProperties": false
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": false,
    "openWorldHint": true
  },
  "errors": [
    {"code": "channel_archived", "temporary": false, "description": "Channel was archived; unarchive via slack_unarchive_channel before retrying."},
    {"code": "channel_not_found", "temporary": false, "description": "channel_id does not match any channel the bot can see."},
    {"code": "rate_limited", "temporary": true, "description": "Slack rate limit hit; honour `retry_after_ms` from the error payload."},
    {"code": "msg_too_long", "temporary": false, "description": "`text` exceeded 40000 characters; split into multiple messages."}
  ]
}
```

What to notice: the description orients the agent (when-to-use, usage notes, example invocation) but does **not** carry failure semantics — those live in the structured `errors` array, where each code has a `temporary` flag and a concrete repair direction.
Parameter names are disambiguated (`channel_id`, `thread_ts`); `required` lists only what's truly necessary.
Every optional parameter declares its omission semantics: `thread_ts` omitted means a top-level post (omission semantics with no `default` at all), while `broadcast_to_channel` declares both the `default` and that the server applies it (per §3).
Annotations are honest — `idempotentHint: false` because re-sending creates a duplicate post, not a no-op — which is exactly why the schema offers `idempotency_key` with a declared deduplication window and the description names the ambiguous-timeout recovery path (`slack_list_messages`), per the §3 mutation-safety rules.
(`errors` is a convention extension, not a native `Tool` field — see the native-vs-convention rule in `SKILL.md`; §3 of the contract-checklist requires side effects, idempotency, and rate limits to be first-class contract, not prose.)
This four-code catalog is small enough to inline; a tool with a long error taxonomy should keep only the repair-critical codes inline and serve the full catalog on demand (per §6), because every byte of this definition is paid by each preloading client.

## 2. Structured tool response

Two responses from `slack_list_messages` showing the concise vs detailed result pattern: a structured concise default with an opt-in detail mode. *Demonstrates §3 output rules and §8 token efficiency together.*

Concise (default, `detail: "summary"`):

```json
{
  "messages": [
    {
      "ts": "1714600000.001200",
      "channel": "#deploys",
      "author": "alice",
      "preview": "Deploy started for api@v2.4.1"
    },
    {
      "ts": "1714600055.001300",
      "channel": "#deploys",
      "author": "deploy-bot",
      "preview": "All services green."
    }
  ],
  "has_more": true,
  "next_cursor": "eyJ0cyI6IjE3MTQ2MDAwNTUuMDAxMzAwIn0",
  "estimated_total": 3210,
  "truncated": true,
  "truncation_hint": "result set capped at 500 of ~3210 matches; narrow with `since=` or `query=`"
}
```

Detail (`detail: "full"`):

```json
{
  "messages": [
    {
      "ts": "1714600000.001200",
      "channel_id": "C0123ABCD",
      "channel": "#deploys",
      "author_id": "U07ALICE",
      "author": "alice",
      "preview": "Deploy started for api@v2.4.1",
      "text": "Deploy started for api@v2.4.1",
      "blocks": [{"type": "rich_text", "elements": []}],
      "reactions": [],
      "thread_ts": null,
      "edited": null,
      "client_msg_id": "8f4c6e1a-7b22-4d6a-91a1-3aa72c4e9b07",
      "permalink": "https://slack.com/archives/C0123ABCD/p1714600000001200"
    }
  ],
  "has_more": true,
  "next_cursor": "eyJ0cyI6IjE3MTQ2MDAwNTUuMDAxMzAwIn0",
  "estimated_total": 3210,
  "truncated": true,
  "truncation_hint": "result set capped at 500 of ~3210 matches; narrow with `since=` or `query=`"
}
```

What to notice: the concise fields are a strict subset of the detailed fields — `preview` stays present in both modes, so one parser handles both, and detail adds the raw domain IDs (`channel_id`, `author_id`, `client_msg_id`), the full `text` and `blocks`, plus null-valued fields.
Pagination and truncation are distinct signals carried together: `has_more`/`next_cursor` continue the walk page by page, while `truncated: true` says the walk will stop at the 500-item cap even though ~3210 messages match — and the `truncation_hint` names the repair (`since=` or `query=`) so the agent doesn't have to guess.
On clients that support it, deliver this payload as `structuredContent` paired with a published `outputSchema`; the same JSON shape goes in `content` as a textual fallback for clients that do not.

## 2a. Dispatched-but-unconfirmed mutation result

A success result from `hvac_set_zone_temperature` where the command was dispatched but its effect is not yet confirmed.
*Demonstrates the §3 dispatched-vs-applied rule: the dispatch fact, a confirmation status, and a `follow_up` that reuses the §6 `repair` object shape on the success carrier.*

```json
{
  "command_sent": true,
  "status": "pending_verification",
  "zone_id": "z-12",
  "follow_up": {
    "next_step": "verify_setpoint_applied",
    "tool": "hvac_get_zone_state",
    "arguments": {"zone_id": "z-12"}
  }
}
```

What to notice: `command_sent` and `status` make dispatch and application separately observable, so a verification timeout can return this same shape rather than a plain failure — "never landed" and "unconfirmed" stay distinguishable, and the agent reconciles via `follow_up` instead of blind-retrying a non-idempotent action.
`follow_up.tool` is a registered `tools/list` name with literally callable `arguments` — the same `{next_step, tool, arguments, alternative}` shape as §6 `repair` (`alternative` is optional and, per the §6 presence convention, omitted here rather than sent as `null`), not a second convention.

## 3. Resource index entry

A single entry from a `slack://channels/C0123ABCD/messages` index listing. *Demonstrates §4 resource indexing.*

```json
{
  "uri": "slack://channels/C0123ABCD/messages/1714600000.001200",
  "name": "1714600000.001200",
  "title": "Deploy started for api@v2.4.1",
  "description": "Alice announces the start of the v2.4.1 API deploy; thread contains 12 follow-up replies including the green-light from deploy-bot.",
  "mimeType": "application/vnd.slack.message+json",
  "size": 8421,
  "annotations": {"lastModified": "2026-05-01T18:14:32Z"}
}
```

What to notice: every field here is a native `Resource` field (`uri`, `name`, `title`, `description`, `mimeType`, `size`, `annotations.lastModified`) — see the native-vs-convention rule in `SKILL.md`.
`uri` is hierarchical and stable (channel id + message ts); `title` and `description` together let the agent decide whether to fetch the body without reading it; `size` lets the agent estimate token cost; `annotations.lastModified` lets the agent skip a re-fetch if it already cached this version; `mimeType` tells the agent which parser to use.
Custom index metadata that has no native field goes under `_meta` (demonstrated in §4), not as a new top-level key.

## 4. Resource body with chunking

The body fetched at `slack://channels/C0123ABCD/messages/1714600000.001200`, chunked because the thread is long. *Demonstrates §4 chunking with stable identifiers.*

Index entry that points at it:

```json
{
  "uri": "slack://channels/C0123ABCD/messages/1714600000.001200",
  "name": "1714600000.001200",
  "title": "Deploy started for api@v2.4.1",
  "description": "Deploy thread; 12 replies across 3 chunks.",
  "mimeType": "application/vnd.slack.thread+json",
  "size": 8421,
  "annotations": {"lastModified": "2026-05-01T18:14:32Z"},
  "_meta": {
    "com.slack-mcp/chunks": [
      {"id": "root", "uri": "slack://channels/C0123ABCD/messages/1714600000.001200/chunks/root", "size": 412},
      {"id": "replies-1-6", "uri": "slack://channels/C0123ABCD/messages/1714600000.001200/chunks/replies-1-6", "size": 3902},
      {"id": "replies-7-12", "uri": "slack://channels/C0123ABCD/messages/1714600000.001200/chunks/replies-7-12", "size": 4107}
    ]
  }
}
```

Each chunk is addressable through its own URI, published as a resource template so agents can construct chunk reads without the catalog:

```json
{
  "uriTemplate": "slack://channels/{channel_id}/messages/{ts}/chunks/{chunk_id}",
  "name": "thread_chunk",
  "title": "Thread chunk",
  "description": "One chunk of a long thread; chunk ids are stable per resource version.",
  "mimeType": "application/vnd.slack.thread+json"
}
```

Body fragment read at `slack://channels/C0123ABCD/messages/1714600000.001200/chunks/replies-1-6`:

```json
{
  "resource_uri": "slack://channels/C0123ABCD/messages/1714600000.001200",
  "chunk_id": "replies-1-6",
  "version": "2026-05-01T18:14:32Z",
  "content": [
    {"ts": "1714600012.001210", "author": "bob", "text": "Watching dashboards."},
    {"ts": "1714600025.001220", "author": "carol", "text": "Cache warming queued."},
    {"ts": "1714600040.001230", "author": "deploy-bot", "text": "Stage 1/3 complete."}
  ],
  "next_chunk_id": "replies-7-12",
  "next_chunk_uri": "slack://channels/C0123ABCD/messages/1714600000.001200/chunks/replies-7-12"
}
```

What to notice: every chunk has a callable fetch path — native `resources/read` takes only a URI, so each chunk gets its own URI and the pattern is published as a resource template (per the §4 callable-fetch rule).
The `_meta` chunk catalog is auxiliary: clients may never surface `_meta` to the model, which is why the body carries `next_chunk_uri` in-band — the agent can walk chunk N+1 without the catalog or the index.
The chunk catalog is a server-specific convention with no native `Resource` field, so it rides under a namespaced `_meta` key (`com.slack-mcp/chunks`) rather than a new top-level field — this is the worked `_meta` namespacing pattern the rest of the skill points at.
Chunk ids are domain-readable (`replies-1-6`), scoped to the resource they chunk, and double as the template's `{chunk_id}` variable — substituting a catalog id into the template yields exactly the chunk URI, so the two identifiers can never drift apart.
`version` matches the index's `annotations.lastModified` so the agent can detect stale chunk references.
The agent can quote `resource_uri` plus `chunk_id` back to a tool that asks "where did you read that?"

## 5. Prompt scaffold

A prompt that orchestrates posting a release announcement across multiple channels. *Demonstrates §5 prompt scaffolding.*

```json
{
  "name": "announce_release",
  "description": "Compose and post a release announcement to one or more channels, with optional thread for follow-up Q&A.",
  "when_to_use": "The user has shipped a release (version, summary, optional changelog link) and wants to announce it. Skip if the user only wants to draft text without sending.",
  "prerequisites": {
    "permissions": ["chat:write", "chat:write.public"],
    "tools": ["slack_lookup_channel", "slack_send_message", "slack_pin_message"],
    "resources": ["slack://channels"],
    "context_assumptions": {
      "default_channel": "Optional default announcement channel used when `channels` is omitted."
    }
  },
  "arguments": [
    {"name": "version", "type": "string", "required": true},
    {"name": "summary", "type": "string", "required": true},
    {"name": "channels", "type": "array", "items": {"type": "string"}, "required": false},
    {"name": "pin", "type": "boolean", "default": false}
  ],
  "expected_followups": [
    "Call `slack_lookup_channel` for each name in `channels` to resolve to channel ids.",
    "Call `slack_send_message` per resolved channel id with the composed text.",
    "If `pin` is true, call `slack_pin_message` with each returned message ts. (Default is false — skip pinning unless explicitly requested.)",
    "Surface the resulting permalinks back to the user."
  ]
}
```

What to notice: `when_to_use` distinguishes this from "draft a message"; `prerequisites` lists the required permissions, tool names, resource URIs, and context assumptions in one place; `expected_followups` names tools by their canonical schema name — the prompt references but does not redefine.
`when_to_use`, `prerequisites`, and `expected_followups` are convention extensions, not native MCP `Prompt` fields (native is `name`, `title`, `description`, `arguments`, `_meta`); a portable server carries them in the prompt `description` text or under a namespaced `_meta` key (see the native-vs-convention rule in `SKILL.md`).
The same applies inside `arguments`: native `PromptArgument` fields are `name`, `title`, `description`, and `required`, so the `type`, `items`, and `default` keys shown here are convention too — for portable servers, carry value-shape guidance in each argument's `description`.

## 5a. Resource template with completion

A parameterized resource for channel history, discoverable without enumerating every channel.
*Demonstrates §2 completion and §4 resource templates.*

Resource template entry from `resources/templates/list`:

```json
{
  "uriTemplate": "slack://channels/{channel_id}/messages{?started_after,ended_before}",
  "name": "channel_messages",
  "title": "Channel messages",
  "description": "Read Slack messages for one channel, optionally bounded by RFC3339 timestamps.",
  "mimeType": "application/vnd.slack.messages+json"
}
```

Completion request for the dynamic `channel_id` variable:

```json
{
  "jsonrpc": "2.0",
  "id": "req_complete_channel",
  "method": "completion/complete",
  "params": {
    "ref": {
      "type": "ref/resource",
      "uri": "slack://channels/{channel_id}/messages{?started_after,ended_before}"
    },
    "argument": {
      "name": "channel_id",
      "value": "dep"
    }
  }
}
```

Completion response:

```json
{
  "jsonrpc": "2.0",
  "id": "req_complete_channel",
  "result": {
    "completion": {
      "values": ["C0123DEPLOYS", "C0456DEPLOYOPS"],
      "total": 2,
      "hasMore": false
    }
  }
}
```

What to notice: `uriTemplate` is native resource-template metadata, and `completion/complete` is native completion for a resource reference.
The server must advertise `server.capabilities.completions` during initialization before clients can rely on this path.
Completion is useful here because Slack channel ids are dynamic and hard to guess; it does not replace normal validation or repair errors for tool-call arguments.

## 5b. Resource subscription

A mutable flags resource that can change during a long-lived agent session.
*Demonstrates §4 resource subscriptions and §9 update-vs-list-change behavior.*

Subscribe request:

```json
{
  "jsonrpc": "2.0",
  "id": "req_subscribe_flags",
  "method": "resources/subscribe",
  "params": {
    "uri": "slack://workspace/T0123/flags"
  }
}
```

Update notification:

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/resources/updated",
  "params": {
    "uri": "slack://workspace/T0123/flags"
  }
}
```

What to notice: the update notification tells the agent to re-read a resource body it already cares about.
It is different from `notifications/resources/list_changed`, which means the resource catalog membership or metadata changed.
A server must advertise `resources.subscribe` before accepting subscriptions; `annotations.lastModified` remains useful as a passive staleness check for clients that do not subscribe.

## 6. Actionable error payload

A failure response from `slack_send_message` when the channel is archived. *Demonstrates §6 failure recovery.*

```json
{
  "isError": true,
  "content": [
    {"type": "text", "text": "Cannot post to archived channel C0123ABCD. Unarchive it with slack_unarchive_channel, or choose a different channel_id."}
  ],
  "structuredContent": {
    "code": "channel_archived",
    "message": "Cannot post to archived channel.",
    "details": {
      "field": "channel_id",
      "value": "C0123ABCD",
      "reason": "Channel was archived on 2026-04-12T09:00:00Z."
    },
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "unarchive_then_retry",
      "tool": "slack_unarchive_channel",
      "arguments": {"channel_id": "C0123ABCD"},
      "alternative": "Choose a different `channel_id` and re-call `slack_send_message`."
    },
    "request_id": "req_01HXYZ7K3M9ABCDEF",
    "fingerprint": "slack-mcp@1.4.0+s7ab21c"
  }
}
```

What to notice: this is a tool-result error, so `isError: true` sits at the top level of the result, the convention repair fields ride in `structuredContent` (the machine contract, per §3), and `content` carries a plain-text fallback for clients that do not surface `structuredContent`.
The `code` is a stable symbolic string the agent can branch on; `details` names the offending field and its value; `temporary: false` with `retry_after_ms: null` tells the agent not to back off and try again — this isn't transient, so don't retry; `repair` points at a real tool name and argument shape — the agent's first repair attempt has everything it needs.
`request_id` and `fingerprint` give the agent correlation context.
Note that the fields inside `structuredContent` — `code`, `temporary`, `retry_after_ms`, `repair`, `request_id`, `fingerprint` — are a convention extension on top of MCP's error shape; a client that does not share this convention still gets `isError: true` plus the `content` text.
Because this is an `isError: true` result, `structuredContent` carries the error envelope rather than the tool's success-shape `outputSchema` — see the §3 position on `outputSchema` scope in `contract-checklist.md`, and document the envelope per tool so validators know which shape applies.
Mirror the pattern, but document the envelope your server emits.

A more common first-call failure — an unknown channel name passed where an id was required — should route the agent to the lookup tool rather than a dead end:

```json
{
  "isError": true,
  "content": [
    {"type": "text", "text": "No channel matches 'deploys'. Resolve the name to an id with slack_lookup_channel, then re-call slack_send_message."}
  ],
  "structuredContent": {
    "code": "channel_not_found",
    "message": "channel_id does not match any channel the bot can see.",
    "details": {"field": "channel_id", "value": "deploys", "reason": "Looks like a channel name, not a C… id."},
    "temporary": false,
    "retry_after_ms": null,
    "repair": {
      "next_step": "lookup_then_retry",
      "tool": "slack_lookup_channel",
      "arguments": {"name": "deploys"},
      "alternative": "Browse the `slack://channels` resource index and pick a valid id."
    },
    "request_id": "req_01HXYZ7K3M9ABCDEH",
    "fingerprint": "slack-mcp@1.4.0+s7ab21c"
  }
}
```

What to notice: the channel-name-vs-id mistake is the predictable first-call failure for a tool whose `channel_id` is a hard-to-guess `C…` id (see §1), and MCP completion does not cover tool arguments — so the repair carries the agent across the gap by naming `slack_lookup_channel` and the exact argument to pass, with the resource index as a fallback.

A resource read failure (e.g., `resources/read` against a deleted thread) uses a JSON-RPC error instead of a tool-result error, but carries the **same error/repair envelope** in `error.data`, with one deliberate exception: `code`/`message` are renamed to `machine_code`/`human_message` so they do not shadow the native JSON-RPC `code`/`message` that wrap them. Every other field — `temporary`, `retry_after_ms`, and (where applicable) `details`, the single `repair` object, and the correlation fields — uses the same name, shape, and cardinality on both surfaces. `resource_uri` is one of those optional correlation fields, shared by both surfaces; it is populated here because this failure is tied to a specific resource (see the unified envelope table in `contract-checklist.md` §6).

```json
{
  "jsonrpc": "2.0",
  "id": "req_01HXYZ7K3M9ABCDEG",
  "error": {
    "code": -32002,
    "message": "Resource not found.",
    "data": {
      "machine_code": "resource_gone",
      "human_message": "Message thread was deleted or is no longer visible.",
      "details": {
        "field": "uri",
        "value": "slack://channels/C0123ABCD/messages/1714600000.001200",
        "reason": "Thread was deleted on 2026-04-12T09:00:00Z."
      },
      "temporary": false,
      "retry_after_ms": null,
      "repair": {
        "next_step": "search_then_read",
        "tool": "slack_search_messages",
        "arguments": {"query": "Deploy started for api@v2.4.1"},
        "alternative": "Browse the `slack://channels/C0123ABCD/messages` index and pick a current thread."
      },
      "resource_uri": "slack://channels/C0123ABCD/messages/1714600000.001200",
      "request_id": "req_01HXYZ7K3M9ABCDEG",
      "fingerprint": "slack-mcp@1.4.0+s7ab21c"
    }
  }
}
```

What to notice: the `data` block carries the same error envelope as the tool-result error's `structuredContent`, with the optional `resource_uri` correlation field populated because this failure is tied to a specific resource. `machine_code: resource_gone` with `temporary: false` and `retry_after_ms: null` tells the agent the exact resource is permanently gone — don't back off and retry the same read — while `repair` still routes it to a real next call (`slack_search_messages`) to recover the underlying information a different way. (This is why no separate `recoverable` flag is needed: `machine_code` + `temporary` already say whether *this* resource can return, and a non-empty `repair` already says recovery is possible by another path.) `request_id` here mirrors the envelope `id`, and `fingerprint` ties the failure to the server contract version — the same correlation context the tool surface carries.

`-32002` is MCP's resource-not-found JSON-RPC code under the 2025-11-25 baseline; the `data` block carries the repair contract.
Branch on `machine_code`, not the numeric code — the numeric code is a likely migration point (see `SKILL.md` Spec Baseline).

## 7. Server capability summary

The capability summary exposed via a resource, discovery tool, or instructions field, whichever the client honors. *Demonstrates §1 server-level identity and §2 discovery primitives.*

```json
{
  "server": {
    "name": "slack-mcp",
    "version": "1.4.0",
    "fingerprint": "slack-mcp@1.4.0+s7ab21c",
    "transport": "stdio",
    "summary": "Send and read messages, manage channels and DMs, and look up users in a single Slack workspace."
  },
  "does": [
    "Post messages to channels, DMs, and threads.",
    "List, search, archive, and unarchive channels.",
    "Look up users by id, email, or display name.",
    "Read message history with cursor pagination."
  ],
  "does_not": [
    "Manage workspace settings, billing, or admin operations.",
    "Cross-workspace search or Enterprise Grid org-level operations.",
    "File uploads larger than 5MB (use Slack's web client).",
    "Real-time event subscriptions (this is a request/response server)."
  ],
  "error_carriers": {
    "tool_errors": "structuredContent",
    "non_tool_rpc_errors": "error.data"
  },
  "prerequisites": {
    "workspace_scope": "One Slack workspace per server instance.",
    "required_scopes": ["chat:write", "channels:read", "users:read"],
    "negotiated_capabilities": {
      "server": [
        "server.capabilities.resources.listChanged",
        "server.capabilities.resources.subscribe",
        "server.capabilities.completions",
        "server.capabilities.tasks.requests.tools.call"
      ],
      "client": [
        "client.capabilities.roots",
        "client.capabilities.elicitation.form",
        "client.capabilities.elicitation.url"
      ],
      "fallbacks": {
        "no_completions": "Return invalid-field errors with allowed channel ids where safe.",
        "no_resource_subscribe": "Use annotations.lastModified and explicit re-read guidance.",
        "no_tasks": "Use slack_get_export_status and slack_cancel_export fallback tools."
      }
    },
    "default_context": {
      "announce_channel": "Optional default channel for the `announce_release` prompt when its `channels` argument is omitted. Tools like `slack_send_message` still require an explicit `channel_id`."
    },
    "auth": {
      "mode": "stdio environment credential",
      "credential_source": "SLACK_BOT_TOKEN",
      "http_variant": {
        "only_when_exposed_over_http": true,
        "canonical_server_uri": "https://slack-mcp.example.com/mcp",
        "resource_indicator": "https://slack-mcp.example.com/mcp",
        "step_up_scopes": ["admin.conversations:write"]
      }
    },
    "failure_codes": {
      "missing_credential": "server is not connected to Slack",
      "invalid_credential": "Slack rejected the configured credential",
      "insufficient_scope": "configured credential lacks one or more required scopes; see `details.required_scopes`"
    }
  }
}
```

What to notice: an agent reads this once, through whatever summary surface the client exposes, and knows the server's name, scope, negative scope, permission boundaries, and the small amount of implicit context that can change behavior.
The summary does not spend first-read tokens on credential wiring details the agent cannot act on; those remain operator documentation and structured failure responses.
The transport choice (`stdio`) is declared.
The negotiated-capability block is convention metadata, but the capabilities it names are native MCP features.
It tells the agent which paths are fast paths and which fallbacks to expect on weaker clients.
Capability strings use fully qualified `server.capabilities.*` and `client.capabilities.*` paths so the server/client side of negotiation is unambiguous.
The auth block separates stdio credential sourcing from the optional HTTP variant.
The `http_variant` fields apply only if the same contract is also exposed over streamable HTTP; stdio-only servers should omit that object.
It does not ask the model to handle bearer tokens directly.
The `error_carriers` block names where the §6 error envelope travels on each surface; a server whose framework forces a `content[0].text` mirror on error results would declare that degraded mode here, with its trigger (§6).
The fingerprint appears here too so agents can short-circuit re-discovery (see §9).
This summary is a convention surface, not a native MCP structure — expose it through whatever the client honors (a resource, a discovery tool, or the server `instructions` field) and keep its shape documented (see the native-vs-convention rule in `SKILL.md`).

## 8. `search_tools` response shape

Response from `search_tools(query="send message")`. *Demonstrates §2 host/client-managed context disclosure.* **One valid shape for that one cost axis — not a universal requirement.** `search_tools` / `describe_tool` are themselves extra entries in native `tools/list`; they lower an agent's model-visible context **only** on a host that withholds native definitions and injects the schemas these return on demand (or routes execution through a stable generic call tool). On a host that preloads the full catalog, they add tools and round trips with no disclosure benefit — see the client-dependency caveat in §2.

```json
{
  "results": [
    {
      "name": "slack_send_message",
      "summary": "Post a message to a Slack channel or DM.",
      "namespace": "slack",
      "stability": "stable",
      "score": 0.94
    },
    {
      "name": "slack_send_ephemeral",
      "summary": "Post a message visible only to a single user in a channel.",
      "namespace": "slack",
      "stability": "stable",
      "score": 0.71
    },
    {
      "name": "slack_schedule_message",
      "summary": "Schedule a message for future delivery.",
      "namespace": "slack",
      "stability": "preview",
      "score": 0.62
    }
  ],
  "has_more": false,
  "fingerprint": "slack-mcp@1.4.0+s7ab21c",
  "load_definition_with": "describe_tool"
}
```

What to notice: only summaries come back, not full schemas; the agent calls `describe_tool(name)` — which returns the full native `Tool` record (`name`, `title`, `description`, `inputSchema`, `outputSchema`, `annotations`, `execution`, `_meta`) plus the server's documented `errors` catalog as a separately labeled convention extension (`errors` is not a native `Tool` field — see `examples.md` §1 and the native-vs-convention rule in `SKILL.md`) — to load the definitions it actually needs. `stability` is included so the agent can filter out preview tools. `score` is the search-relevance score for the supplied query, ranked descending. The fingerprint travels with the response so a cached client can detect drift. Other shapes on this same axis: a tool catalog endpoint, a topic-tagged tool index, a paginated `list_tools` with filtering — all custom surfaces layered over native discovery, and all subject to the same host-integration requirement. None of them shrink the native `tools/list` payload a preloading client receives; to lower cost on a client that never lazy-loads, reduce the catalog itself (compact definitions, consolidation, a compact dispatcher, or authorization-scoped catalogs — see §2).
Fields like `summary`, `stability`, `score`, and `load_definition_with` are convention, not native; native `tools/list` returns `Tool` records, so a server layering search on top documents this envelope (see the native-vs-convention rule in `SKILL.md`).

## 8a. Roots-aware workspace behavior

A local code-search server asks the client which project roots are relevant before indexing.
*Demonstrates §1 roots and capability gating.*

Request:

```json
{
  "jsonrpc": "2.0",
  "id": "req_roots",
  "method": "roots/list"
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": "req_roots",
  "result": {
    "roots": [
      {
        "uri": "file:///Users/alice/work/frontend",
        "name": "frontend"
      },
      {
        "uri": "file:///Users/alice/work/backend",
        "name": "backend"
      }
    ]
  }
}
```

What to notice: the server only sends `roots/list` after the client advertised `roots` during initialization.
It treats those roots as the workspace scope for search and path resolution, and listens for `notifications/roots/list_changed`.
Roots are not access control; the implementation still needs normal filesystem checks and must not assume that a root URI grants permission to every path below it.

## 9. Capability fingerprint with deprecation

A fingerprint snapshot showing one tool transitioning from `stable` through `deprecated` to removal. *Demonstrates §9 versioning and compatibility.*

Fingerprint at `slack-mcp@1.3.0`:

```json
{
  "fingerprint": "slack-mcp@1.3.0+a112de9",
  "covers": ["tools", "resources", "resource_templates", "prompts", "completion_support", "subscription_support", "error_codes", "summary"],
  "tools": [
    {"name": "slack_post", "stability": "stable", "schema_hash": "sha256:9c…"},
    {"name": "slack_list_channels", "stability": "stable", "schema_hash": "sha256:7a…"}
  ]
}
```

Fingerprint at `slack-mcp@1.4.0` — `slack_post` is deprecated, `slack_send_message` replaces it:

```json
{
  "fingerprint": "slack-mcp@1.4.0+s7ab21c",
  "covers": ["tools", "resources", "resource_templates", "prompts", "completion_support", "subscription_support", "error_codes", "summary"],
  "tools": [
    {
      "name": "slack_post",
      "stability": "deprecated",
      "schema_hash": "sha256:9c…",
      "deprecation": {
        "since": "1.4.0",
        "removal_at_or_after": "2.0.0",
        "replaced_by": "slack_send_message",
        "migration": "Rename `slack_post` to `slack_send_message`. Argument shape is unchanged; the new tool adds an optional `broadcast_to_channel` field."
      }
    },
    {"name": "slack_send_message", "stability": "stable", "schema_hash": "sha256:b4…"},
    {"name": "slack_list_channels", "stability": "stable", "schema_hash": "sha256:7a…"}
  ]
}
```

Fingerprint at `slack-mcp@2.0.0` — `slack_post` removed:

```json
{
  "fingerprint": "slack-mcp@2.0.0+f0e1d2c",
  "covers": ["tools", "resources", "resource_templates", "prompts", "completion_support", "subscription_support", "error_codes", "summary"],
  "tools": [
    {"name": "slack_send_message", "stability": "stable", "schema_hash": "sha256:b4…"},
    {"name": "slack_list_channels", "stability": "stable", "schema_hash": "sha256:7a…"}
  ],
  "removed_in_this_version": [
    {"name": "slack_post", "deprecated_since": "1.4.0", "replaced_by": "slack_send_message"}
  ]
}
```

What to notice: the deprecated tool stays discoverable in `1.4.0` with a stability tier change, a `replaced_by` pointer, and concrete migration text — clients that cached the old surface get a discoverable signal, not a silent break.
The removal in `2.0.0` is itself recorded under `removed_in_this_version` so a client jumping `1.3.0 → 2.0.0` can still trace what happened.
Every fingerprint string changes when any covered surface changes, including resource templates, completion behavior, and subscription support.
The fingerprint and its `covers`, `deprecation`, and `schema_hash` fields are a convention extension, not a native MCP structure (see the native-vs-convention rule in `SKILL.md`).
When tool, resource, or prompt lists change, emit the corresponding native `notifications/*/list_changed` message and keep list ordering deterministic; the fingerprint is additive, not a substitute.

## 10. Worked task: API mirroring vs. task completion

The same user task — "schedule a 30-minute meeting with Alice and Bob next Tuesday afternoon and send the invite" — expressed two ways. *Demonstrates §3 tool granularity and §2 discovery impact. The failure mode of (a) is shown via transcript, not narrated.*

### (a) Endpoint-mirroring tools

Available tools (one per REST endpoint):

```json
[
  {"name": "calendar_find_availability", "summary": "Query a user's free/busy windows."},
  {"name": "calendar_create_event", "summary": "Create a calendar event with attendees."},
  {"name": "calendar_get_attendees", "summary": "List attendees on an existing event."},
  {"name": "calendar_add_attendees", "summary": "Add attendees to an existing event."},
  {"name": "calendar_send_invite", "summary": "Send an existing event's invite to its attendees."},
  {"name": "email_send", "summary": "Send an arbitrary email."},
  {"name": "email_send_template", "summary": "Send an email from a saved template."}
]
```

Agent transcript probe:

```
agent: I need to find a 30-min slot. Calling calendar_find_availability for alice@…, bob@…, Tue afternoon.
tool:  {"slots": [{"start": "2026-05-05T13:00:00Z", "end": "2026-05-05T13:30:00Z"}, ...]}

agent: Picking 13:00. Now I'll call calendar_create_event.
tool:  {"event_id": "evt_88f1", "attendees": ["alice@…", "bob@…"], "invites_sent": false}

agent: The event exists but invites_sent is false. I see calendar_send_invite and email_send_template — both look plausible for sending the invite. The names overlap. Trying email_send_template since the user asked to "send the invite."
tool:  {"isError": true, "code": "template_not_found", "message": "No template named 'invite' exists."}

agent: Retrying with email_send.
tool:  {"isError": true, "code": "missing_required_field", "details": {"field": "to"}}

agent: Re-reading docs. I think I actually wanted calendar_send_invite. Calling it now.
tool:  {"event_id": "evt_88f1", "invites_sent": true}
```

Five tool calls, two of them wrong, because `email_send` / `email_send_template` / `calendar_send_invite` collide on the same intent ("send the invite") and the agent could not disambiguate from summaries alone.

### (b) Task-completing tool

Available tool:

```json
{
  "name": "calendar_schedule_meeting",
  "summary": "Find a slot, create the event, and send invites in one call.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["attendees", "duration_minutes", "time_window", "subject"],
    "properties": {
      "attendees": {"type": "array", "items": {"type": "string", "format": "email"}},
      "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 480},
      "time_window": {
        "type": "object",
        "required": ["earliest", "latest"],
        "properties": {
          "earliest": {"type": "string", "format": "date-time"},
          "latest": {"type": "string", "format": "date-time"}
        },
        "additionalProperties": false
      },
      "subject": {"type": "string"},
      "description": {"type": "string"},
      "send_invites": {"type": "boolean", "default": true}
    },
    "additionalProperties": false
  }
}
```

Agent transcript probe:

```
agent: Calling calendar_schedule_meeting.
tool:  {
  "event_id": "evt_88f1",
  "scheduled_at": "2026-05-05T13:00:00Z",
  "duration_minutes": 30,
  "attendees": ["alice@…", "bob@…"],
  "invites_sent": true,
  "permalink": "https://cal.example.com/evt_88f1"
}
```

One tool call. The agent never sees a near-name collision because the task is the unit, not the endpoint. Internal steps (find availability, create event, send invites) are hidden behind the task contract.

What to notice: (a) costs five round-trips and two failed attempts because endpoint-mirroring tools force the agent to re-invent the workflow on every task and to disambiguate near-name overlaps from summary text; (b) costs one. The same Slack server with 60 endpoint-mirror tools typically expresses 6–10 actual user tasks (see §3 anti-patterns).

## 11. Long-running operation

Exporting a wide date range can take minutes, so `slack_export_history` declares task support and recovers through the native MCP task lifecycle rather than hiding the work behind a silent blocking call. *Demonstrates §7 long-running operations.*

Tasks are **experimental** in MCP 2025-11-25, so this example leads with native task operations and keeps a domain-specific status/cancel fallback (below) for servers or clients that do not implement tasks.

**Capability negotiation.** Native task recovery requires two declarations, not one — the server advertises the `tasks` capability, and the tool advertises `execution.taskSupport`. The per-tool flag alone is insufficient.

```json
{
  "capabilities": {
    "tasks": {
      "list": {},
      "cancel": {},
      "requests": {"tools": {"call": {}}}
    }
  }
}
```

**Tool definition** (entry in `tools/list`):

```json
{
  "name": "slack_export_history",
  "description": "Export Slack history for channels and a date range; wide exports run as recoverable tasks.\n\nDuration: typically ~45s, up to ~180s for wide ranges. Run as a task, the call returns a taskId before any server timeout and the result is retrievable until `ttl` elapses.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["channel_ids", "started_after", "ended_before"],
    "properties": {
      "channel_ids": {"type": "array", "items": {"type": "string", "pattern": "^C[A-Z0-9]{8,}$"}},
      "started_after": {"type": "string", "format": "date-time"},
      "ended_before": {"type": "string", "format": "date-time"}
    },
    "additionalProperties": false
  },
  "execution": {"taskSupport": "optional"}
}
```

**Create the task.** The client augments its `tools/call` with a `task` field in `params`; the receiver returns a `CreateTaskResult` carrying the native `Task` object under `result.task`, not the tool result.

Request:

```json
{
  "jsonrpc": "2.0",
  "id": "req_01HXYZ",
  "method": "tools/call",
  "params": {
    "name": "slack_export_history",
    "arguments": {
      "channel_ids": ["C0123ABCD"],
      "started_after": "2026-01-01T00:00:00Z",
      "ended_before": "2026-05-01T00:00:00Z"
    },
    "task": {"ttl": 86400000},
    "_meta": {"progressToken": "pt_01J9EXPORT"}
  }
}
```

Response (`CreateTaskResult`):

```json
{
  "jsonrpc": "2.0",
  "id": "req_01HXYZ",
  "result": {
    "task": {
      "taskId": "task_01J9EXPORT",
      "status": "working",
      "statusMessage": "Export accepted; collecting messages.",
      "createdAt": "2026-05-01T18:14:32Z",
      "lastUpdatedAt": "2026-05-01T18:14:32Z",
      "ttl": 86400000,
      "pollInterval": 5000
    },
    "_meta": {"io.modelcontextprotocol/model-immediate-response": "Export accepted as task_01J9EXPORT; results retrievable for 24h."}
  }
}
```

**Progress** is keyed by the `progressToken` the requestor supplied in the request's `_meta` — the server echoes that opaque value, never mints its own — and, like other task-associated notifications, carries `io.modelcontextprotocol/related-task` in `_meta`.

Notification:

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/progress",
  "params": {
    "progressToken": "pt_01J9EXPORT",
    "progress": 0.62,
    "total": 1,
    "message": "Exported 31 of 50 channel-days.",
    "_meta": {"io.modelcontextprotocol/related-task": {"taskId": "task_01J9EXPORT"}}
  }
}
```

**Poll** with `tasks/get` (`params: {"taskId": "task_01J9EXPORT"}`) until a terminal status or `input_required`, respecting `pollInterval`; the response carries the `Task` directly in `result`.

Response:

```json
{
  "jsonrpc": "2.0",
  "id": "req_poll_2",
  "result": {
    "taskId": "task_01J9EXPORT",
    "status": "completed",
    "createdAt": "2026-05-01T18:14:32Z",
    "lastUpdatedAt": "2026-05-01T18:21:48Z",
    "ttl": 86400000,
    "pollInterval": 5000
  }
}
```

**Retrieve** the result with `tasks/result` (`params: {"taskId": "task_01J9EXPORT"}`); it blocks until the task is terminal, then returns exactly what the original `tools/call` would have, including the related-task `_meta`.
Domain payload (export location, counts) rides in `structuredContent`.
If polling shows `input_required`, call `tasks/result` preemptively and hold it open: the pending input request arrives as a separate server-to-client request (an `elicitation/create` carrying the related-task `_meta`) while the call is pending — not as the `tasks/result` response — and once input arrives the task returns to `working` and the same held call completes with the terminal result (re-call only if the call itself fails).

Response:

```json
{
  "jsonrpc": "2.0",
  "id": "req_result",
  "result": {
    "content": [{"type": "text", "text": "Export ready: 9,214 messages across 50 channel-days."}],
    "structuredContent": {
      "result_resource_uri": "slack://exports/task_01J9EXPORT/result.json",
      "message_count": 9214
    },
    "isError": false,
    "_meta": {"io.modelcontextprotocol/related-task": {"taskId": "task_01J9EXPORT"}}
  }
}
```

**Cancel** task-augmented work with `tasks/cancel` (`params: {"taskId": "task_01J9EXPORT"}`) — not `notifications/cancelled`, which cancels request-bound non-task calls. The receiver transitions the task to the terminal `cancelled` status before responding.

Response:

```json
{
  "jsonrpc": "2.0",
  "id": "req_cancel",
  "result": {
    "taskId": "task_01J9EXPORT",
    "status": "cancelled",
    "statusMessage": "Export cancelled by request.",
    "createdAt": "2026-05-01T18:14:32Z",
    "lastUpdatedAt": "2026-05-01T18:19:02Z",
    "ttl": 86400000
  }
}
```

**Fallback for clients without task support** (convention, not native). When the server cannot rely on the experimental task capability, expose a domain-specific status tool and cancel tool that surface the same signals the native lifecycle would — current state, when to poll again, the result location, and expiry.

```json
{
  "status": {
    "tool": "slack_get_export_status",
    "export_id": "exp_01J9EXPORT",
    "state": "running",
    "terminal": false,
    "poll_after_ms": 5000,
    "progress": 0.62,
    "terminal_states": ["succeeded", "failed", "cancelled"]
  },
  "cancel": {"tool": "slack_cancel_export", "arguments": {"export_id": "exp_01J9EXPORT"}}
}
```

What to notice: native recovery needs both the server `server.capabilities.tasks.requests.tools.call` declaration and the tool's `execution.taskSupport` — the per-tool flag alone does nothing.
The `progressToken` originates in the request's `_meta`; the `model-immediate-response` `_meta` string on the `CreateTaskResult` gives the host something to hand the model immediately instead of silence while the task runs.
Native task fields use the spec's casing exactly: `taskId`, `status`, `statusMessage`, `createdAt`, `lastUpdatedAt`, `ttl`, `pollInterval` — do not rename them to the snake_case used by domain fields.
`ttl` and `pollInterval` are both milliseconds per the spec, and the names don't encode the unit — so `86400000` here is a 24-hour TTL and `5000` is a 5-second poll interval; `createdAt`/`lastUpdatedAt` are RFC3339 timestamps.
Status is one of `working`, `input_required`, `completed`, `failed`, `cancelled`; there is no `running`, `succeeded`, or `expired` — expiry is `ttl` elapsing, after which the receiver may delete the task.
`CreateTaskResult` nests the `Task` under `result.task`; `tasks/get` and `tasks/cancel` return the `Task` directly in `result`; `tasks/result` returns the underlying tool result — read each shape from the spec rather than assuming one envelope.
Carry `io.modelcontextprotocol/related-task` in `_meta` only where the payload does not already name the task: it is required on `tasks/result` responses, while `tasks/get`, `tasks/list`, and `tasks/cancel` SHOULD NOT include it because the `taskId` already travels in the message — which is why the poll and cancel responses above omit it.
The domain-specific status/cancel tools are a labeled fallback for the experimental-task gap, not a replacement for `tasks/*`.

## 12. Response-delivery artifact

A read-only query tool whose result is delivered as a local CSV file rather than inlined in the response. *Demonstrates §3 annotation honesty when the tool writes a transient artifact.*

```json
{
  "name": "warehouse_query",
  "description": "Run a read-only SQL query against the analytics warehouse.\n\nResults are delivered as a CSV under the local cache dir (path returned in `result_artifact.path`); files self-expire after 24h. The artifact is the response, not a side effect — this tool does not mutate warehouse state, and the cache directory is per-session and not shared across users.\n\nExample: {\"sql\": \"SELECT user_id, signup_at FROM users WHERE signup_at > '2026-05-01' LIMIT 10000\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["sql"],
    "properties": {
      "sql": {"type": "string", "minLength": 1, "maxLength": 100000}
    },
    "additionalProperties": false
  },
  "annotations": {
    "readOnlyHint": true,
    "openWorldHint": true
  }
}
```

A successful response:

```json
{
  "row_count": 8472,
  "result_artifact": {
    "path": "/var/cache/warehouse-mcp/results/q_01J9XYZ.csv",
    "mime_type": "text/csv",
    "size_bytes": 412908,
    "ttl_hours": 24,
    "expires_at": "2026-05-11T18:14:32Z"
  },
  "schema": [
    {"name": "USER_ID", "type": "string"},
    {"name": "SIGNUP_AT", "type": "timestamp_ntz"}
  ],
  "fingerprint": "warehouse-mcp@2.1.0+a4b7c1d"
}
```

What to notice: this skill treats `readOnlyHint: true` as defensible here — the call doesn't mutate the warehouse, doesn't change shared state, and the CSV is response delivery (scoped to this call, declared TTL, no shared visibility), not persistent state that outlives the response contract.
That is a deliberate reading of an ambiguous hint, not settled spec (see contract-checklist §3): a reviewer who reads `readOnlyHint` literally as "does not modify its environment" may count the local write as mutation, so where you can, prefer returning the result as a resource or resource link with TTL metadata rather than a local-file artifact.
`destructiveHint` and `idempotentHint` are omitted because the spec makes them meaningful only when `readOnlyHint` is `false` — declaring them here would assert semantics the protocol does not assign (contrast `slack_send_message` in §1, where `readOnlyHint: false` makes `idempotentHint: false` meaningful: re-sending compounds — two messages posted, not a no-op).
`openWorldHint: true` reflects that the tool reaches an external warehouse.
The artifact is disclosed in the structured response — `result_artifact` (a convention field) with `path`, `mime_type`, `ttl_hours`, `expires_at` — and in the tool description, never by flipping the annotation.
Its sub-fields use house `snake_case` (`mime_type`, not the native `Resource.mimeType`) because `result_artifact` is a convention object, not a native `Resource`; if a server instead returns a real native `resource_link`, it carries the native `mimeType` casing (see `contract-checklist.md` §3 and the native-vs-convention rule in `SKILL.md`).
Flipping `readOnlyHint` to `false` here would gate auto-approval on a call this skill considers read-only and create friction with no safety benefit; a server that takes the literal reading instead should say so and annotate consistently.

## 13. Tool result with resource link

A chart-rendering tool returns a small machine summary plus a linked image resource.
*Demonstrates §3 rich tool-result content and §8 token efficiency.*

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"chart_uri\":\"slack://reports/deploys/chart.png\",\"series_count\":3,\"point_count\":90}"
    },
    {
      "type": "resource_link",
      "uri": "slack://reports/deploys/chart.png",
      "name": "deploys-chart.png",
      "description": "PNG chart of deploy count and failure rate for the last 30 days.",
      "mimeType": "image/png",
      "annotations": {
        "audience": ["user"],
        "priority": 0.8,
        "lastModified": "2026-05-11T18:14:32Z"
      }
    }
  ],
  "structuredContent": {
    "chart_uri": "slack://reports/deploys/chart.png",
    "series_count": 3,
    "point_count": 90,
    "summary": "Deploy volume increased 12% over the prior 30 days; failure rate stayed below 2%."
  },
  "isError": false
}
```

What to notice: `structuredContent` carries the authoritative fields an agent should parse.
The text block serializes the same core JSON for older clients.
The PNG is linked as a resource instead of base64-inlined, so a capable client can fetch or show it only when needed.
`audience` and `priority` help a client decide that the image is primarily for the user, but correctness does not depend on those annotations being surfaced.
