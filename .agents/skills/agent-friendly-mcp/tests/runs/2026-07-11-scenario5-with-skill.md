# Scenario 5 (Resources) — with-skill run

- **Date:** 2026-07-11
- **Tree:** `d586ce3` (main, post review-batch #53)
- **Mode:** with-skill (fresh general-purpose subagent; read `SKILL.md` + all seven `references/` files, explicitly skipped `tests/`; 8 tool-uses)
- **Score:** 8/8

## Exact prompt given

Same wiki resource-surface prompt as the baseline (see `2026-07-11-scenario5-baseline.md`), preceded by an instruction to read `agent-friendly-mcp/SKILL.md` and every file under `references/` as authoritative guidance, and **not** to read anything under `tests/`.

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | Stable, hierarchical, domain-noun URIs (not numeric ids) | **PASS** | `wiki://spaces/{space}/pages/{slug}` — "stable domain nouns (space key and slug), never internal page IDs, which are unstable across deployments." |
| A2 | Index entries carry **native** triage metadata (`title`, `description`, `mimeType`, `size`, `annotations.lastModified`), no inlined body, summary ≤3 sentences | **PASS** | Index entry uses native `uri`/`name`/`title`/`description`/`mimeType`/`size`/`annotations.lastModified`; custom triage under `_meta.com.acme-wiki/page`; no body; summary ≤3 sentences. |
| A3 | Large bodies chunkable; every chunk has its own callable URI as a resource template | **PASS** | `wiki://spaces/{space}/pages/{slug}/chunks/{chunk_id}` published as template `wiki_page_chunk`; `_meta.com.acme-wiki/chunks` catalog + in-band `next_chunk_uri`. |
| A4 | Chunk ids stable across reads of a page version; version change observable | **PASS** | "Stable chunk ids per version … If the page is edited, `version` changes and chunk ids may change — but that is observable via `version` / `annotations.lastModified`, never silent." |
| A5 | Custom metadata under a **namespaced** `_meta` key, never a new top-level field | **PASS** | "Every convention field rides under a namespaced `_meta` key (`com.acme-wiki/*`); native `Resource` fields are used verbatim." (`com.acme-wiki/page`, `com.acme-wiki/chunks`.) |
| A6 | Subscriptions (`resources.subscribe` + `notifications/resources/updated`) distinguished from `list_changed` | **PASS** | `notifications/resources/updated` for **body** edits vs `notifications/resources/list_changed` for **catalog** membership — "Different fact, different notification"; both capabilities declared; passive `version`/`lastModified` fallback. |
| A7 | Parameterized lookups via resource templates **with completion** for `{space}`/`{slug}` where negotiated | **PASS** | `resources/templates/list` + a full worked `completion/complete` request/response for `{slug}` (context `{space:eng}`), gated on `server.capabilities.completions`. |
| A8 | Tool fallback reaches the same indexed content, self-sufficient from `tools/list` alone | **PASS** | `wiki_get_page`/`wiki_list_pages`/`wiki_list_spaces`; "`wiki_list_pages()` with no `space` returns the spaces index … No resource read ever required." |

**Result: 8/8.**
The three baseline gaps close cleanly: native triage field **names** (A2), **namespaced** `_meta` keys (A5), and completion on template variables (A7).
The run also volunteers the skill's cross-cutting disciplines — bounded `resources/list` (spaces only, not thousands of pages), one-envelope-two-carriers resource failures (§6), house `has_more`/`next_cursor` in the index body kept distinct from native `nextCursor` (§8), and a §4 checklist-coverage table.

## Full final output

Verbatim final message returned by the with-skill subagent (the 8 tool calls were the skill-file reads it was instructed to perform; no other tool noise):

I've read the SKILL and all seven reference files (skipping `tests/` as instructed).
Here is the complete resource-surface design.

---

# `acme-wiki` MCP — Agent-Facing Resource Surface

Design for a read-only server exposing a large internal wiki (thousands of pages, `space` + `slug` addressing, pages >100 KB, content mutating during the day).
This covers the **resource surface only**; it is walked against contract-checklist **§4 (Resources)** with the cross-cutting **§2 discovery, §6 failure, §8 token efficiency, §9 versioning** rules that a resource surface must satisfy.
Every convention field rides under a namespaced `_meta` key (`com.acme-wiki/*`); native `Resource` fields are used verbatim.

## Design decisions at a glance

| Concern | Decision |
| --- | --- |
| Addressing | Hierarchical `wiki://spaces/{space}/pages/{slug}` — stable domain nouns (`eng`, `hr`, human slug), never internal page IDs |
| `resources/list` membership | **Bounded**: capability summary + `wiki://spaces` + one page-index resource per space. Page bodies and chunks are **not** enumerated (thousands) — reached via templates + per-space index |
| Index vs body | Native `Resource` triage fields in the index; body opt-in and chunked |
| Large pages | Section-stable chunks, each with its own URI (published as a template) + in-band `next_chunk_uri` |
| Parameterized lookup | `resources/templates/list` + `completion/complete` for `{space}` and `{slug}` |
| Mutation during the day | `resources/subscribe` → `notifications/resources/updated` for body edits; `notifications/resources/list_changed` for new/removed pages; `version` + `annotations.lastModified` as passive staleness check for non-subscribing clients |
| Resource-poor clients | `wiki_get_page` / `wiki_list_pages` / `wiki_list_spaces` tool fallback, self-sufficient from `tools/list` alone |

---

## 1. URI scheme

Four URI shapes.
Segments are stable domain nouns (space key and slug), so an agent can quote them back and cache them (§4 stable-URI rule).

```json
{
  "scheme": "wiki",
  "uris": {
    "spaces_index":  "wiki://spaces",
    "page_index":    "wiki://spaces/{space}/pages{?query,updated_after,cursor}",
    "page":          "wiki://spaces/{space}/pages/{slug}",
    "page_chunk":    "wiki://spaces/{space}/pages/{slug}/chunks/{chunk_id}"
  },
  "notes": {
    "space": "Stable space key, e.g. \"eng\", \"hr\". Enumerable at wiki://spaces.",
    "slug":  "Stable human page slug within a space, e.g. \"oncall-runbook\". NOT an internal numeric page id, which is unstable across deployments.",
    "chunk_id": "Section-stable id, stable for the lifetime of one page version; changes only when the page changes (observable via version / annotations.lastModified)."
  }
}
```

`resources/list` returns only the bounded top-level catalog below — the summary resource, the spaces index, and one page-index resource per space (a few dozen entries).
Page bodies and chunks are reached through the template + the per-space index, never enumerated, so the native catalog stays small and its ordering stays deterministic for cache-diffing (§2/§9).

```json
{
  "resources": [
    { "uri": "wiki://capabilities", "name": "capabilities",
      "title": "acme-wiki capability summary",
      "description": "What this server does, does not do, prerequisites, error carriers, fingerprint.",
      "mimeType": "application/json" },
    { "uri": "wiki://spaces", "name": "spaces",
      "title": "Wiki spaces index",
      "description": "All spaces the caller can read (eng, hr, ...), with page counts.",
      "mimeType": "application/vnd.acme-wiki.space-index+json" },
    { "uri": "wiki://spaces/eng/pages", "name": "eng-pages",
      "title": "Pages in space: eng",
      "description": "Paginated index of pages in the eng space; filter with query=, updated_after=.",
      "mimeType": "application/vnd.acme-wiki.page-index+json" }
  ]
}
```

---

## 2. Index / list entry vs. full body

A **page-index entry** carries only native `Resource` triage metadata — enough to decide *whether* to fetch the body and *what it will cost*, without reading it (§4 index-before-body).
Custom triage fields go under `_meta`, never as new top-level keys.

**Index entry** (one element of the `wiki://spaces/eng/pages` body):

```json
{
  "uri": "wiki://spaces/eng/pages/oncall-runbook",
  "name": "oncall-runbook",
  "title": "On-call Runbook",
  "description": "Primary/secondary rotation, paging paths, and escalation policy for the eng on-call. Large: split across 4 chunks.",
  "mimeType": "application/vnd.acme-wiki.page+json",
  "size": 148213,
  "annotations": { "lastModified": "2026-07-11T14:02:19Z" },
  "_meta": {
    "com.acme-wiki/page": {
      "space": "eng",
      "slug": "oncall-runbook",
      "version": "rev-8842",
      "chunk_count": 4,
      "labels": ["runbook", "oncall"],
      "editor": "carol"
    }
  }
}
```

Why these fields: `title`+`description` let the agent triage without the body; `size` (148 KB) signals token cost and that chunked fetch is warranted; `annotations.lastModified` lets a client skip a re-read of a version it already cached; `mimeType` names the parser; `_meta.com.acme-wiki/page.version` is the opaque staleness/etag token; `chunk_count` warns the body is chunked.
Summaries stay ≤3 sentences (§4).

The **page-index body** itself is list-shaped and can be large (a space may hold thousands of pages), so it paginates and filters.
Because `resources/read` returns a resource body (not a native list method), pagination uses the **documented house convention in the body** (§8 — `nextCursor` is reserved for native list methods only):

```json
{
  "space": "eng",
  "version_scanned_at": "2026-07-11T14:05:00Z",
  "entries": [ /* index entries as above */ ],
  "has_more": true,
  "next_cursor": "eyJhZnRlciI6Im9uY2FsbC1ydW5ib29rIn0",
  "estimated_total": 3120,
  "truncated": false
}
```
`has_more`/`next_cursor`/`estimated_total` are labeled convention (not protocol).
The agent continues by reading `wiki://spaces/eng/pages?cursor=…`.
Deterministic slug ordering means a page added mid-walk cannot strand or duplicate entries (§9).

---

## 3. Delivering large pages (chunking)

A >100 KB page is **never handed over whole by default** (§4).
The body read returns a concise wrapper: the first chunk inline, the full chunk catalog under `_meta`, and an **in-band** `next_chunk_uri` so an agent can walk chunks even on a client that never surfaces `_meta` to the model.

Each chunk is independently addressable and the pattern is published as a resource template (native `resources/read` takes only a URI, so every chunk needs its own URI — §4 callable-fetch rule).

**Body read at `wiki://spaces/eng/pages/oncall-runbook`** (first chunk + catalog):

```json
{
  "resource_uri": "wiki://spaces/eng/pages/oncall-runbook",
  "space": "eng",
  "slug": "oncall-runbook",
  "title": "On-call Runbook",
  "version": "rev-8842",
  "last_modified": "2026-07-11T14:02:19Z",
  "content_format": "markdown",
  "chunk_id": "s01-overview",
  "content": "# On-call Runbook\n\nThis runbook covers the primary/secondary rotation ...",
  "next_chunk_id": "s02-paging-paths",
  "next_chunk_uri": "wiki://spaces/eng/pages/oncall-runbook/chunks/s02-paging-paths",
  "_meta": {
    "com.acme-wiki/chunks": [
      { "id": "s01-overview",     "uri": "wiki://spaces/eng/pages/oncall-runbook/chunks/s01-overview",     "size": 4102,  "heading": "Overview" },
      { "id": "s02-paging-paths", "uri": "wiki://spaces/eng/pages/oncall-runbook/chunks/s02-paging-paths", "size": 51888, "heading": "Paging paths" },
      { "id": "s03-escalation",   "uri": "wiki://spaces/eng/pages/oncall-runbook/chunks/s03-escalation",   "size": 61240, "heading": "Escalation policy" },
      { "id": "s04-appendix",     "uri": "wiki://spaces/eng/pages/oncall-runbook/chunks/s04-appendix",     "size": 30983, "heading": "Appendix" }
    ]
  }
}
```

**A single chunk read at `.../chunks/s03-escalation`:**

```json
{
  "resource_uri": "wiki://spaces/eng/pages/oncall-runbook",
  "chunk_id": "s03-escalation",
  "version": "rev-8842",
  "heading": "Escalation policy",
  "content_format": "markdown",
  "content": "## Escalation policy\n\nIf the primary does not ack within 5 minutes ...",
  "next_chunk_id": "s04-appendix",
  "next_chunk_uri": "wiki://spaces/eng/pages/oncall-runbook/chunks/s04-appendix"
}
```

Key properties:
- **Stable chunk ids per version.** `s03-escalation` addresses the same section for the whole life of `rev-8842`.
  If the page is edited, `version` changes and chunk ids *may* change — but that is observable via `version` / `annotations.lastModified`, never silent (§4).
- **`version` travels on every chunk** so an agent that cached the index entry can detect that a chunk reference has gone stale mid-read (a real risk here, since pages change during the day).
- **Two independent walk paths:** the `_meta` catalog (random access, N+1 without re-fetching N) and the in-band `next_chunk_uri` (sequential, works even when `_meta` is hidden).
  The agent can quote `resource_uri` + `chunk_id` back to a tool to cite where it read something.

---

## 4. Discovering parameterized lookups (templates + completion)

Space + slug lookup is the primary access pattern and must be discoverable *without enumerating thousands of pages*.
Published via `resources/templates/list` (a native discovery primitive distinct from the resource list), paired with `completion/complete` for the dynamic variables (§2/§4).

**`resources/templates/list` result:**

```json
{
  "resourceTemplates": [
    {
      "uriTemplate": "wiki://spaces/{space}/pages/{slug}",
      "name": "wiki_page",
      "title": "Wiki page by space and slug",
      "description": "Read one page by its space key and slug. Large pages return the first chunk plus a chunk catalog; walk chunks via next_chunk_uri.",
      "mimeType": "application/vnd.acme-wiki.page+json"
    },
    {
      "uriTemplate": "wiki://spaces/{space}/pages/{slug}/chunks/{chunk_id}",
      "name": "wiki_page_chunk",
      "title": "One chunk of a page",
      "description": "One section-stable chunk of a page; chunk ids are stable per page version.",
      "mimeType": "application/vnd.acme-wiki.page+json"
    },
    {
      "uriTemplate": "wiki://spaces/{space}/pages{?query,updated_after,cursor}",
      "name": "wiki_page_index",
      "title": "Page index for a space",
      "description": "Browse/search pages in a space. Paginated (house has_more/next_cursor in body); filter with query= and updated_after=.",
      "mimeType": "application/vnd.acme-wiki.page-index+json"
    }
  ]
}
```

**Completion request** for `{slug}` inside space `eng` (gated on `server.capabilities.completions`):

```json
{
  "jsonrpc": "2.0", "id": "c1", "method": "completion/complete",
  "params": {
    "ref": { "type": "ref/resource", "uri": "wiki://spaces/{space}/pages/{slug}" },
    "argument": { "name": "slug", "value": "oncall" },
    "context": { "arguments": { "space": "eng" } }
  }
}
```

**Completion response** (native `completion` envelope — `values`/`total`/`hasMore`):

```json
{
  "jsonrpc": "2.0", "id": "c1",
  "result": {
    "completion": {
      "values": ["oncall-runbook", "oncall-handoff-checklist", "oncall-paging-policy"],
      "total": 3,
      "hasMore": false
    }
  }
}
```

Completion turns slug/space guessing into a proactive lookup, so agents build valid URIs instead of learning the value space through failed reads.
It does **not** complete arbitrary tool arguments — the fallback tools rely on the same value spaces via `wiki_list_spaces` / `wiki_list_pages`.

---

## 5. Notifying an agent when a page changes

Two distinct native signals, gated on negotiated capabilities (§4/§9):

- **`notifications/resources/updated`** — a subscribed page's **body** changed (an editor saved).
  This is the core answer to "content changes during the day."
- **`notifications/resources/list_changed`** — the **catalog** changed (a page was created, moved, or deleted).
  Different fact, different notification.

**Subscribe to a page the agent cares about:**

```json
{
  "jsonrpc": "2.0", "id": "s1", "method": "resources/subscribe",
  "params": { "uri": "wiki://spaces/eng/pages/oncall-runbook" }
}
```

**Body-change notification** (server → client; the agent re-reads and sees the new `version`):

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/resources/updated",
  "params": { "uri": "wiki://spaces/eng/pages/oncall-runbook" }
}
```

**Catalog-membership notification** (a new page appeared in a space):

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/resources/list_changed"
}
```

Passive fallback for clients that don't subscribe: every index entry and body read carries `annotations.lastModified` and `_meta…/version`, so an agent can poll and compare instead of subscribing.
The capability summary states this fallback explicitly:

```json
{
  "negotiated_capabilities": {
    "server": [
      "server.capabilities.resources.subscribe",
      "server.capabilities.resources.listChanged",
      "server.capabilities.completions"
    ],
    "fallbacks": {
      "no_resource_subscribe": "Re-read the page and compare `version` / `annotations.lastModified`; use `updated_after=` on the page index to find recently-changed pages.",
      "no_list_changed": "Re-read `wiki://spaces/{space}/pages` to detect new/removed pages.",
      "no_completions": "Enumerate valid values with wiki_list_spaces / wiki_list_pages, or read an invalid-field error that lists candidates."
    }
  }
}
```

---

## 6. Fallback for clients that don't handle resources well

Some clients expose resources poorly.
Because browsing/reading the wiki is the whole point, a **tool fallback reaches the same indexed content** and is **self-sufficient from `tools/list` alone** — every required value space is tool-reachable, and omitting an optional selector returns the index (§4 fallback rule).
Tools are `snake_case`, prefixed, verb-noun, so they survive code-execution clients too.

```json
{
  "name": "wiki_get_page",
  "description": "Read one wiki page (or one chunk of it) by space and slug.\n\nWhen to use: on clients that do not browse MCP resources, or to fetch a specific page directly. Large pages return the first chunk plus a chunk catalog; pass chunk_id to fetch a later chunk (ids come from the catalog's next_chunk_id). Same content as reading wiki://spaces/{space}/pages/{slug}.\n\nExample: {\"space\": \"eng\", \"slug\": \"oncall-runbook\"}",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["space", "slug"],
    "properties": {
      "space": { "type": "string", "description": "Space key, e.g. \"eng\". Enumerate via wiki_list_spaces." },
      "slug":  { "type": "string", "description": "Page slug within the space. Enumerate via wiki_list_pages(space)." },
      "chunk_id": { "type": "string", "description": "Optional. Omit for the first chunk plus chunk catalog; pass a catalog id for a later chunk." },
      "detail": { "type": "string", "enum": ["summary", "full"], "description": "Omitted means \"summary\": title, version, first chunk, and chunk catalog. \"full\" returns all chunks concatenated (may be large)." }
    },
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["resource_uri", "space", "slug", "version", "chunk_id", "content", "content_format"],
    "properties": {
      "resource_uri": { "type": "string", "format": "uri" },
      "space": { "type": "string" },
      "slug": { "type": "string" },
      "version": { "type": "string" },
      "last_modified": { "type": "string", "format": "date-time" },
      "content_format": { "type": "string", "enum": ["markdown"] },
      "chunk_id": { "type": "string" },
      "content": { "type": "string" },
      "next_chunk_id": { "type": "string" },
      "chunks": { "type": "array", "items": { "type": "object" } }
    },
    "additionalProperties": false
  },
  "annotations": { "readOnlyHint": true, "openWorldHint": false }
}
```

Companion tools that make the value spaces tool-reachable (so `wiki_get_page` is callable from `tools/list` with no resource reads):

```json
[
  {
    "name": "wiki_list_spaces",
    "description": "List all readable spaces (eng, hr, ...) with page counts. No arguments. Same content as reading wiki://spaces.",
    "inputSchema": { "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {}, "additionalProperties": false },
    "annotations": { "readOnlyHint": true, "openWorldHint": false }
  },
  {
    "name": "wiki_list_pages",
    "description": "List/search pages. Omit `space` to list spaces instead (the index); pass `space` to page through that space's pages. Filter with query= and updated_after=. Same content as reading wiki://spaces/{space}/pages.",
    "inputSchema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "properties": {
        "space": { "type": "string", "description": "Omit to return the spaces index; supply to list pages in that space." },
        "query": { "type": "string", "description": "Optional full-text filter over title and body." },
        "updated_after": { "type": "string", "format": "date-time", "description": "Optional. Only pages modified at/after this RFC3339 UTC instant — the polling path for detecting same-day edits without subscriptions." },
        "cursor": { "type": "string", "description": "Opaque continuation from a prior response's next_cursor." }
      },
      "additionalProperties": false
    },
    "annotations": { "readOnlyHint": true, "openWorldHint": false }
  }
]
```

`wiki_list_pages()` with no `space` returns the spaces index (the "omit selector → index" rule), so an agent that lands on this server with zero context can: call `wiki_list_pages()` → discover spaces → `wiki_list_pages(space="eng")` → discover slugs → `wiki_get_page(space, slug)` → walk chunks.
No resource read ever required.

---

## 7. Resource failure recovery (§6)

Resource reads are non-tool RPC methods, so failures surface as **JSON-RPC errors** carrying the unified envelope in `error.data` (only `code`/`message` renamed to `machine_code`/`human_message`).
The tool fallback carries the *same* envelope in `structuredContent` with `isError: true` — one envelope, two carriers.

**`resources/read` on a deleted page:**

```json
{
  "jsonrpc": "2.0", "id": "r9",
  "error": {
    "code": -32002,
    "message": "Resource not found.",
    "data": {
      "machine_code": "page_gone",
      "human_message": "Page eng/oncall-runbook-old was deleted or moved.",
      "details": { "field": "uri", "value": "wiki://spaces/eng/pages/oncall-runbook-old",
                   "reason": "Page deleted on 2026-07-10T22:00:00Z." },
      "temporary": false,
      "retry_after_ms": null,
      "repair": {
        "next_step": "search_then_read",
        "tool": "wiki_list_pages",
        "arguments": { "space": "eng", "query": "oncall runbook" },
        "alternative": "Browse the wiki://spaces/eng/pages index and pick a current slug."
      },
      "resource_uri": "wiki://spaces/eng/pages/oncall-runbook-old",
      "request_id": "req_01HW…",
      "fingerprint": "acme-wiki@1.0.0+3fb91c2"
    }
  }
}
```

Documented resource error codes: `space_not_found`, `page_not_found` (bad slug — repair routes to `wiki_list_pages`), `page_gone` (deleted), `chunk_not_found` (stale chunk id — repair re-reads the page body to get the current catalog and new `version`), `cursor_expired` (index walk — repair restarts the walk), `rate_limited` (`temporary: true` + `retry_after_ms`).
Branch on `machine_code`, not the numeric `-32002` (a likely RC migration point).

---

## Checklist coverage (§4 and the resource-relevant cross-cuts)

| § | Answer |
| --- | --- |
| §4 stable URIs | `wiki://spaces/{space}/pages/{slug}` — domain nouns, not internal ids ✓ |
| §4 index vs body | Native `Resource` triage fields (§2 sample); body opt-in ✓ |
| §4 chunkable + stable ids | Section-stable chunk ids per version, own URIs, in-band `next_chunk_uri`, `_meta` catalog ✓ |
| §4 templates + completion | `resources/templates/list` + `completion/complete` for `{space}`,`{slug}` ✓ |
| §4 subscriptions | `resources/subscribe` → `updated`; `list_changed` for membership; `lastModified`/`version` passive fallback ✓ |
| §4 tool fallback | `wiki_get_page`/`wiki_list_pages`/`wiki_list_spaces`, self-sufficient from `tools/list` ✓ |
| §4 write-surface = tool | N/A — read-only wiki; no mutation modeled as a resource ✓ |
| §2 discovery bounded | `resources/list` holds summary + spaces + per-space index only; thousands of pages via template, never enumerated ✓ |
| §6 failure | JSON-RPC `error.data` for reads / `structuredContent` for tool fallback; same envelope; symbolic codes + repair ✓ |
| §8 token efficiency | ≤3-sentence summaries, chunked bodies, `detail` toggle, index pagination via house `has_more`/`next_cursor`, `updated_after=` filter ✓ |
| §9 versioning | `version`/`fingerprint` for staleness, deterministic slug ordering, native `list_changed`/`updated` notifications ✓ |

**Load-bearing subtleties:** (1) page bodies are deliberately kept out of `resources/list` because thousands of native `Resource` records would bloat the catalog — templates + a paginated per-space index resource replace enumeration; (2) `version` rides on every index entry, body, and chunk so an agent can detect a same-day edit invalidating a cached chunk reference; (3) the index-body pagination uses **house** `has_more`/`next_cursor` (it is a resource body, not a native list method), while any native `resources/list` walk uses `nextCursor` — the two must not be conflated.
