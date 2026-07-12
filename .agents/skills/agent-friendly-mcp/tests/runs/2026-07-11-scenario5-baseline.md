# Scenario 5 (Resources) — baseline run

- **Date:** 2026-07-11
- **Tree:** `d586ce3` (main, post review-batch #53)
- **Mode:** baseline (fresh general-purpose subagent, prompt only, forbidden from reading the repo/skill; 0 tool-uses confirms no skill access)
- **Score:** 5/8

## Exact prompt given

> Design the agent-facing MCP resource surface for a server that exposes a large internal documentation wiki to an agent.
> The wiki has thousands of pages organized in spaces (e.g. `eng`, `hr`); pages can be large (some over 100 KB); page content changes during the day; and agents need to look up a specific page by space + slug as well as browse.
> Produce: the resource URI scheme, what a resource index/list entry looks like versus a full body, how large pages are delivered, how an agent discovers parameterized lookups, how an agent is notified when a page it cares about changes, and any fallback for clients that do not handle resources well.

(Wrapped with a guardrail forbidding reading any repo file, especially under `agent-friendly-mcp/`.)

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Stable, hierarchical, domain-noun URIs (not numeric ids) | **PASS** | `wiki://{space}/{slug}`, `wiki://{space}/{slug}?chunk={n}`, `wiki://{space}/{slug}@{version}` — hierarchical, stable slugs, no numeric ids. |
| A2 | Index entries carry **native** triage metadata (`title`, `description`, `mimeType`, `size`, `annotations.lastModified`), no inlined body, summary ≤3 sentences | **FAIL** | Page index entries are a custom JSON list using **non-native** field names — `summary` (not `description`), `sizeBytes` (not `size`), `updatedAt` (not `annotations.lastModified`). Bodies correctly not inlined and summaries short, but the native triage field set is not used. |
| A3 | Large bodies chunkable; every chunk has its own callable URI as a resource template (or labeled tool fallback by chunk id) | **PASS** | `wiki://{space}/{slug}?chunk={n}` chunks, template `wiki://{space}/{slug}{?chunk}` published via `resources/templates/list`; heading-boundary splitting + `outline` TOC. |
| A4 | Chunk ids stable across reads of a page version; version change observable via modification metadata | **PASS** | "Chunks are pinned to the version" (`@v210?chunk=1`); `version`/`updatedAt`/`etag` expose changes. |
| A5 | Custom metadata under a **namespaced** `_meta` key, never a new top-level field on the `Resource` record | **FAIL** | Read envelope uses `_meta`, but keys are **not namespaced** (`_meta: {space, slug, version, chunk, …}`, and notification `_meta: {previousVersion, version}`) rather than `com.wiki/…`. Index-entry JSON also carries custom fields at top level. |
| A6 | Subscriptions (`resources.subscribe` + `notifications/resources/updated`) distinguished from `list_changed` | **PASS** | Declares `resources: {subscribe:true, listChanged:true}`; per-page `notifications/resources/updated` for body edits vs `notifications/resources/list_changed` for structural changes — explicitly distinguished, incl. a `moved` edge case. |
| A7 | Parameterized lookups via resource templates **with completion** for `{space}`/`{slug}` where negotiated | **FAIL** | Resource templates present, but no `completion/complete` for template variables is mentioned. |
| A8 | Tool fallback reaches the same indexed content, self-sufficient from `tools/list` alone | **PASS** | `search_pages`, `get_page`, `list_space`, `check_updates` mirror the surface with the identical chunk envelope; explicitly the lowest-common-denominator path. |

**Passed:** A1, A3, A4, A6, A8 (5/8).
A strong baseline — the architecture (bounded enumeration, templates for lookup, chunking with an outline, version pinning, subscriptions, tool mirror) is sound.
The skill-sensitive gaps are the native-vs-convention disciplines: native triage field **names** on index entries (A2), **namespaced** `_meta` keys (A5), and completion on template variables (A7).

## Full final output

Verbatim final message returned by the baseline subagent (no tool noise to elide — the run made 0 tool calls):

The user wants a design deliverable.
I'll produce it directly from my MCP knowledge, no file reads.

# Agent-Facing MCP Resource Surface: Internal Documentation Wiki

## Design principles

MCP resources are addressed by URI and enumerated via `resources/list`.
The core tension for a large, mutable wiki is: `resources/list` cannot enumerate thousands of pages (it would blow the context window and the round-trip budget), and MCP resources have no native "get me one page by key" argument mechanism outside of templates.
So the design leans on three MCP features working together:

- **Resource templates** (`resources/templates/list`) for parameterized lookup by `space` + `slug`.
- **A small set of concrete index resources** for browsing (per-space, paged), rather than listing every page.
- **Subscriptions** (`resources/subscribe` + `notifications/resources/updated`) plus `notifications/resources/list_changed` for change awareness.
- **A tool fallback** (`search_pages` / `get_page`) for clients that don't handle resources or templates well, since tool-calling is universally supported.

---

## 1. URI scheme

A single custom scheme, hierarchical and human-legible, so an agent can construct URIs itself once it knows the pattern:

```
wiki://                                  → root / space catalog
wiki://{space}/                          → a space's page index (browse)
wiki://{space}/{slug}                    → a page (metadata + body, chunked if large)
wiki://{space}/{slug}?chunk={n}          → one chunk of a large page
wiki://{space}/{slug}@{version}          → an immutable pinned version of a page
```

Design notes:
- `space` and `slug` are stable identifiers; `slug` is the wiki's own path-slug so the agent can look up a known page directly.
- `@{version}` gives an *immutable* handle (content-addressed by version) — useful when an agent read a page, reasoned about it, and wants to cite exactly what it saw even though the live page moved.
  The bare `wiki://{space}/{slug}` always resolves to `latest`.
- Query params (`chunk`) are used for retrieval control, not identity.

---

## 2. Resource templates (how the agent discovers parameterized lookup)

`resources/templates/list` returns RFC 6570 URI templates.
This is the discovery mechanism for "look up a page by space + slug" — the agent learns the shape without me listing every page.

```json
{
  "resourceTemplates": [
    {
      "uriTemplate": "wiki://{space}/{slug}",
      "name": "wiki-page",
      "title": "Wiki page by space and slug",
      "description": "Full content of a single wiki page. `space` is a space key such as `eng` or `hr`; `slug` is the page's path slug (e.g. `onboarding/laptop-setup`). Returns page metadata plus body. Bodies over ~50 KB are returned as chunk 0 with a `nextChunk` cursor; append `?chunk={n}` to fetch subsequent chunks. Content reflects the live page and may change during the day; append `@{version}` to pin an immutable snapshot.",
      "mimeType": "text/markdown"
    },
    {
      "uriTemplate": "wiki://{space}/",
      "name": "wiki-space-index",
      "title": "Browse a space",
      "description": "Paged index of pages within a space. Returns lightweight index entries (no bodies). Use `?cursor={cursor}` from the previous response to page.",
      "mimeType": "application/json"
    },
    {
      "uriTemplate": "wiki://{space}/{slug}{?chunk}",
      "name": "wiki-page-chunk",
      "title": "One chunk of a large wiki page",
      "description": "Fetch a specific chunk of a large page body. `chunk` is a 0-based index returned in a previous response's `nextChunk`.",
      "mimeType": "text/markdown"
    }
  ]
}
```

The `description` fields are doing real work here: they tell the agent how to build the slug, how chunking behaves, and that content is mutable.
That is the primary "docs surface" an agent reads.

---

## 3. Browsing: index / list entries vs. full bodies

Two distinct shapes. **Never** put page bodies in list/index responses.

### `resources/list` (top level) — spaces only, plus templates

At the root, `resources/list` returns a *small, bounded* set: the spaces, not the pages.
This keeps the standard MCP enumeration cheap and finite.

```json
{
  "resources": [
    {
      "uri": "wiki://eng/",
      "name": "eng-space",
      "title": "Engineering space",
      "description": "1,842 pages. Architecture, runbooks, on-call, RFCs.",
      "mimeType": "application/json"
    },
    {
      "uri": "wiki://hr/",
      "name": "hr-space",
      "title": "HR space",
      "description": "career, benefits, policies.",
      "mimeType": "application/json"
    }
  ],
  "nextCursor": null
}
```

### Reading a space index resource — lightweight entries

`resources/read` on `wiki://eng/` returns a JSON list of *index entries*.
Each entry is small (a few hundred bytes): enough to decide relevance and to construct the page URI, with no body.

```json
{
  "contents": [
    {
      "uri": "wiki://eng/",
      "mimeType": "application/json",
      "text": "{ ...the JSON below, serialized... }"
    }
  ]
}
```

where the embedded JSON is:

```json
{
  "space": "eng",
  "generatedAt": "2026-07-11T14:03:22Z",
  "entries": [
    {
      "uri": "wiki://eng/onboarding/laptop-setup",
      "slug": "onboarding/laptop-setup",
      "title": "Laptop setup for new engineers",
      "updatedAt": "2026-07-11T09:12:00Z",
      "version": "v87",
      "sizeBytes": 4213,
      "summary": "Steps to provision a dev laptop: FileVault, brew bundle, VPN.",
      "labels": ["onboarding", "it"]
    },
    {
      "uri": "wiki://eng/runbooks/payments-oncall",
      "slug": "runbooks/payments-oncall",
      "title": "Payments on-call runbook",
      "updatedAt": "2026-07-11T13:58:41Z",
      "version": "v210",
      "sizeBytes": 142880,
      "chunked": true,
      "summary": "Paging, escalation, common incidents for the payments service.",
      "labels": ["runbook", "oncall", "payments"]
    }
  ],
  "nextCursor": "eyJvZmZzZXQiOjIwMH0="
}
```

Key fields for an agent: `title`, `summary`, `updatedAt`/`version` (freshness + subscription target), `sizeBytes`/`chunked` (so it knows to expect chunking before it reads), and `uri` (ready to `resources/read`).
Paging via `nextCursor` — the agent re-reads `wiki://eng/?cursor=...`.

---

## 4. Full body delivery, and large pages

`resources/read` on `wiki://eng/onboarding/laptop-setup` returns the body plus a small metadata envelope.
For pages under the chunk threshold (say ~50 KB), one content block:

```json
{
  "contents": [
    {
      "uri": "wiki://eng/onboarding/laptop-setup@v87",
      "mimeType": "text/markdown",
      "title": "Laptop setup for new engineers",
      "text": "# Laptop setup\n\n...full markdown body...",
      "_meta": {
        "space": "eng",
        "slug": "onboarding/laptop-setup",
        "version": "v87",
        "updatedAt": "2026-07-11T09:12:00Z",
        "sizeBytes": 4213,
        "chunk": 0,
        "totalChunks": 1,
        "nextChunk": null,
        "etag": "v87",
        "pinnedUri": "wiki://eng/onboarding/laptop-setup@v87"
      }
    }
  ]
}
```

Note the returned `uri` is the **resolved pinned** `@v87` form even though the request was for `latest` — so anything the agent stores refers to exactly what it read. `pinnedUri` is called out explicitly too.

### Large pages (>100 KB): chunked delivery

A single 142 KB runbook would swamp a response and often exceed practical model-input framing.
Reading the bare page URI returns **chunk 0** with a cursor, not the whole thing:

```json
{
  "contents": [
    {
      "uri": "wiki://eng/runbooks/payments-oncall@v210?chunk=0",
      "mimeType": "text/markdown",
      "title": "Payments on-call runbook (chunk 1 of 5)",
      "text": "# Payments on-call runbook\n\n## Paging\n... first ~40 KB, split on a heading boundary ...",
      "_meta": {
        "space": "eng",
        "slug": "runbooks/payments-oncall",
        "version": "v210",
        "sizeBytes": 142880,
        "chunk": 0,
        "totalChunks": 5,
        "nextChunk": 1,
        "nextChunkUri": "wiki://eng/runbooks/payments-oncall@v210?chunk=1",
        "chunkStrategy": "markdown-heading-boundary",
        "outline": [
          { "heading": "Paging", "chunk": 0 },
          { "heading": "Escalation", "chunk": 1 },
          { "heading": "Common incidents", "chunk": 2 },
          { "heading": "Runbooks by service", "chunk": 3 },
          { "heading": "Appendix: dashboards", "chunk": 4 }
        ]
      }
    }
  ]
}
```

Design choices that make chunking agent-friendly:
- **Split on structural boundaries** (headings), never mid-sentence, so each chunk is independently useful.
- **`outline` in chunk 0** is a table of contents mapping headings → chunk index.
  An agent looking for "escalation" jumps straight to `?chunk=1` instead of walking all five.
- **Chunks are pinned to the version** they came from (`@v210?chunk=1`) so a mid-read edit can't stitch two inconsistent versions together.
  If the agent requests a chunk of a version that has moved, the server returns the pinned older chunk (immutable) rather than silently serving new content.
- Threshold + chunk size are server config; `totalChunks`/`nextChunk` let the agent loop deterministically.

---

## 5. Change notifications

Two mechanisms, matching the two MCP notification types.

### Per-page subscriptions

The agent subscribes to pages it read and cares about.
Standard MCP:

```json
{ "method": "resources/subscribe", "params": { "uri": "wiki://eng/runbooks/payments-oncall" } }
```

Subscribe to the **bare (latest)** URI — you want to be told when latest moves, not pin to a version.
When the page changes, the server sends:

```json
{
  "method": "notifications/resources/updated",
  "params": {
    "uri": "wiki://eng/runbooks/payments-oncall",
    "title": "Payments on-call runbook",
    "_meta": {
      "previousVersion": "v210",
      "version": "v211",
      "updatedAt": "2026-07-11T15:20:03Z",
      "changeType": "edited",
      "pinnedUri": "wiki://eng/runbooks/payments-oncall@v211"
    }
  }
}
```

The spec-required field is just `uri`; the `_meta` block is additive so the agent can decide whether to re-read *without* a round trip (e.g. skip a trivial label change).
The agent re-reads the bare URI to get `v211`.
Unsubscribe via `resources/unsubscribe`.

### Structural changes (pages added/removed/moved)

When the set of pages in a space changes — new page, deletion, slug move — the server emits the list-changed notification so any cached index is invalidated:

```json
{ "method": "notifications/resources/list_changed" }
```

The agent responds by re-reading the relevant space index. (A page *move* also fires `resources/updated` on the old URI with `changeType: "moved"` and a `movedTo` in `_meta`, so a subscriber isn't left dangling.)

Server declares both capabilities at initialize:

```json
{
  "capabilities": {
    "resources": { "subscribe": true, "listChanged": true }
  }
}
```

---

## 6. Fallback for clients that don't handle resources well

Many clients (and some agent frameworks) implement `tools/call` robustly but ignore resources, templates, or subscriptions.
So mirror the whole surface as tools.
Tools are the lowest common denominator and every field above maps cleanly.

```json
{
  "tools": [
    {
      "name": "search_pages",
      "description": "Full-text/semantic search across the wiki. Returns lightweight index entries (title, slug, summary, uri, version) — never full bodies. Use to find pages, then get_page to read one.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": { "type": "string" },
          "space": { "type": "string", "description": "Optional space key, e.g. eng, hr" },
          "limit": { "type": "integer", "default": 10 },
          "cursor": { "type": "string" }
        },
        "required": ["query"]
      }
    },
    {
      "name": "get_page",
      "description": "Fetch a single page by space + slug. Large pages return one chunk plus nextChunk; pass chunk to page through. Content is live and may change; pass version to pin.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "space": { "type": "string" },
          "slug":  { "type": "string" },
          "chunk": { "type": "integer", "default": 0 },
          "version": { "type": "string", "description": "Optional; pin to an immutable snapshot" }
        },
        "required": ["space", "slug"]
      }
    },
    {
      "name": "list_space",
      "description": "Page through the index of a space. Returns the same lightweight entries as the browse resource.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "space": { "type": "string" },
          "cursor": { "type": "string" }
        },
        "required": ["space"]
      }
    }
  ]
}
```

`get_page` returns the **same** `_meta`/chunk envelope shape as the resource read, so agent logic is identical whichever path it took.
Because tools can't push notifications, the tool-only fallback for change-awareness is polling: `get_page` responses carry `version`/`updatedAt`, and a lightweight `check_updates` tool (accepting a list of `{uri|space+slug, knownVersion}` and returning only those that moved) lets a resource-blind client reconcile cheaply without re-downloading bodies.

```json
{
  "name": "check_updates",
  "description": "Given pages the agent has read and the versions it saw, return only those whose live version differs. Polling substitute for resource subscriptions.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "pages": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "space": { "type": "string" },
            "slug": { "type": "string" },
            "knownVersion": { "type": "string" }
          },
          "required": ["space", "slug", "knownVersion"]
        }
      }
    },
    "required": ["pages"]
  }
}
```

---

## Summary of the decision map

| Need | Mechanism |
|---|---|
| Look up known page by space+slug | Resource template `wiki://{space}/{slug}` (discovered via `resources/templates/list`) |
| Browse without enumerating thousands | Root `resources/list` = spaces only; `resources/read wiki://{space}/` = paged lightweight index |
| List entry vs body | Index entry = metadata + summary, no body; read = body + `_meta` envelope |
| Large (>100 KB) pages | Heading-boundary chunks, `outline` TOC in chunk 0, version-pinned chunk URIs, `nextChunk` cursor |
| Freshness of what was read | Returned `uri` resolves to `@version`; `_meta.etag`/`version` |
| Notified of changes | `resources/subscribe` + `notifications/resources/updated` (per page); `notifications/resources/list_changed` (structural) |
| Clients ignoring resources | Mirror tools: `search_pages`, `get_page`, `list_space`, `check_updates` (poll) — identical envelope shapes |

The through-line: keep enumeration bounded (spaces, not pages), push per-page lookup to templates, never ship a body inside a list, chunk large bodies on structure with a navigable outline, pin versions so reads and citations stay consistent under midday edits, and mirror everything as tools so a resource-blind client loses nothing but push notifications (recovered via polling).
