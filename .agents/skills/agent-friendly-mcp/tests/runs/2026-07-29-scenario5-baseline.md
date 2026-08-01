# Scenario 5 (Resources) — baseline run (2026-07-28 rebase re-run)

- **Date:** 2026-07-29
- **Tree:** `1b0b743` (main, post #130 rebase + #133 §6 fixes)
- **Mode:** baseline (fresh general-purpose subagent on `claude-fable-5`, prompt only, forbidden from reading the repo/skill or the web; 0 tool-uses confirms no skill access)
- **Score:** 6/8

## Exact prompt given

The Scenario 5 prompt from `tests/scenarios.md`, verbatim, wrapped with a guardrail forbidding all tool use (no file reads — especially `agent-friendly-mcp/` — no search, no web).

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | URIs stable, hierarchical, domain nouns, not internal numeric ids | **PASS** | `wiki://{space}/{slug}` hierarchy; "slugs are stable, lowercase, URL-safe, and never contain version or date — the URI is the page's identity". |
| A2 | Index entries carry native triage metadata (`title`, `description`, `mimeType`, `size`, `annotations.lastModified`), no inlined body, ≤3-sentence summary | **PASS** | List entry uses exactly the native field names, including `size` and `annotations.lastModified`; one-line description; body only via `resources/read`. (The 2026-07-11 baseline failed this with `summary`/`sizeBytes`/`updatedAt` — this baseline knows the native names.) |
| A3 | Large bodies chunkable with per-chunk callable URIs published via `resources/templates/list` (or labeled tool fallback with chunk id) | **PASS** | Outline resource + per-section `wiki://{space}/{slug}#{anchor}`, published as the `wiki-page-section` template; tool fallback `get_page(space, slug, section?)`. |
| A4 | Chunk identifiers stable across reads of the same page version; version change observable via modification metadata | **FAIL** | `lastModified` makes version change observable, but chunk-id stability is never addressed: anchors are heading-derived and nothing pins them to a page version or states they are stable across reads. |
| A5 | Custom/index metadata rides under namespaced `_meta`, never top-level `Resource` fields | **PASS** (vacuous) | The design adds no custom fields at all — only native fields and `annotations` — so nothing violates the rule; `_meta` namespacing is never mentioned. |
| A6 | Subscriptions: advertises `resources.subscribe`, serves the `resourceSubscriptions` filter of `subscriptions/listen`, emits `notifications/resources/updated` tagged with `subscriptionId`, distinguished from `list_changed` | **FAIL** | Uses the retired 2025-11-25 mechanism: a `resources/subscribe { uri }` request, no `subscriptions/listen`, no `resourceSubscriptions` filter, no `subscriptionId` tagging. The updated-vs-list_changed distinction itself is correct. |
| A7 | Parameterized lookups via resource templates with completion for `{space}`/`{slug}` where `completions` negotiated | **PASS** | Three RFC 6570 templates; `completion/complete` for `ref/resource` with `context.arguments.space` bound when completing `slug`; `completions` capability declared. |
| A8 | Tool fallback reaches the same indexed content, self-sufficient from `tools/list` alone | **PASS** | `search_pages`/`list_pages`/`get_page`/`get_recent_changes` mirror the whole surface; results carry `resource_link` back to canonical URIs. |

**Passed:** A1, A2, A3, A5, A7, A8 (6/8).
The baseline improved from 5/8 (2026-07-11) on native triage names and avoids the custom-field trap by adding no custom metadata, but it reproduces the retired subscription wire contract from memory (A6) — the exact 2026-07-28 delta the rebase teaches — and never states chunk-id/version coupling (A4).

## Full final output

Verbatim final message returned by the baseline subagent (0 tool calls):

# MCP resource surface for an internal wiki

## 1. URI scheme

One custom scheme, hierarchical, mirroring how humans already address pages:

```
wiki://                          root index (list of spaces)
wiki://{space}                   space index (page directory for that space)
wiki://{space}/{slug}            canonical full page body
wiki://{space}/{slug}/outline    headings/anchors/sizes for one page
wiki://{space}/{slug}#{anchor}   one section of a page
```

Rules: slugs are stable, lowercase, URL-safe, and never contain version or date — the URI is the page's identity, so subscriptions and agent memory survive edits. `resources/read` responses echo the canonical URI in `contents[].uri`, so a server that accepts a sloppy slug still teaches the client the canonical one.

## 2. Index entry vs. full body

**Do not enumerate thousands of pages in `resources/list`.** List only the cheap, high-value entries: the root index, one entry per space, and optionally a curated handful of "start here" pages. Everything else is reachable via templates (below) and the index resources. If you do expose more, use the list's pagination cursor — never one giant page.

A **list entry** is metadata only — enough for the agent to decide whether reading is worth the tokens:

```json
{
  "uri": "wiki://eng/deploy-process",
  "name": "deploy-process",
  "title": "Engineering Deploy Process",
  "description": "Runbook for production deploys; owner: platform team.",
  "mimeType": "text/markdown",
  "size": 148733,
  "annotations": {
    "audience": ["assistant"],
    "priority": 0.7,
    "lastModified": "2026-07-29T14:03:00Z"
  }
}
```

`size` and `lastModified` are the two fields that make the surface agent-friendly: they let the agent avoid re-reading unchanged pages and avoid slurping 100 KB blind.

The **space index resource** (`wiki://eng`) is itself a small markdown document: a table of `title — wiki://eng/{slug} — one-line summary — size — last modified`. This is how an agent browses without the client having to page through `resources/list`.

A **full body** is the `resources/read` result: `contents: [{ uri, mimeType: "text/markdown", text }]` — the whole rendered-to-markdown page, images referenced as links rather than embedded blobs.

## 3. Large pages

`resources/read` has no range parameter, so chunking must be modeled as URIs:

1. `size` in every listing/index entry warns the agent before it reads.
2. For any page over a threshold (~20 KB), the agent is expected to read `wiki://{space}/{slug}/outline` first — a cheap resource listing each heading, its anchor, and its approximate size.
3. Each section is addressable as `wiki://{space}/{slug}#{anchor}` and reads as a normal small resource.
4. The full canonical URI still returns the complete body for clients/agents that want it — chunking is an option, not a gate.

Put this protocol in the server instructions and in the template descriptions ("pages over ~20 KB: read the outline first, then fetch sections") — discovery text is where agents learn the intended access pattern.

## 4. Discovering parameterized lookup

`resources/templates/list` advertises the lookup shapes (RFC 6570):

```json
[
  { "uriTemplate": "wiki://{space}/{slug}",
    "name": "wiki-page",
    "title": "Wiki page by space and slug",
    "description": "Full page body. Check size via the space index first; for large pages read the outline template instead.",
    "mimeType": "text/markdown" },
  { "uriTemplate": "wiki://{space}/{slug}/outline", "name": "wiki-page-outline", "...": "..." },
  { "uriTemplate": "wiki://{space}/{slug}{#anchor}", "name": "wiki-page-section", "...": "..." }
]
```

Declare the `completions` capability and implement `completion/complete` for `ref/resource`: completing `space` returns the space list; completing `slug` (with `context.arguments.space` already bound) returns matching slugs. That turns "I half-remember the page name" into a cheap two-round-trip lookup instead of a browse.

## 5. Change notification

Declare `capabilities.resources = { subscribe: true, listChanged: true }`.

- Agent cares about a page → client sends `resources/subscribe { uri: "wiki://eng/deploy-process" }`.
- On edit, server sends `notifications/resources/updated` with that URI; the client/agent re-reads (or re-reads just the outline and changed sections). The notification carries no body — it is an invalidation signal only.
- Coalesce server-side: wiki pages get saved in bursts, so debounce (e.g. one notification per URI per 30 s).
- Page created/deleted/renamed → `notifications/resources/list_changed` (coarse; agent re-reads the space index).
- Subscriptions die with the session; the fallback for non-subscribing clients (most of them) is polling `lastModified` on the index entry, which is why it must be present and accurate.

## 6. Fallback for resource-weak clients

Many hosts surface tools far better than resources, and full-text search is not expressible as a resource at all. Mirror the surface as four tools:

- `search_pages(query, space?, limit?)` — snippets + URIs; needed regardless of client quality.
- `list_pages(space, cursor?)` — same metadata as the index resource.
- `get_page(space, slug, section?)` — returns the body; for oversized pages returns the outline plus an explicit instruction to call again with `section`.
- `get_recent_changes(space?, since?)` — polling substitute for subscriptions.

Each tool result includes both the text content **and** a `resource_link` content item pointing at the canonical `wiki://` URI, so resource-capable clients can bridge back to subscriptions and caching, and every answer the agent produces can cite a stable URI.

The one-sentence design rule tying it together: **resources carry identity, metadata, and bodies; templates and completion carry lookup; subscriptions carry freshness; tools carry search and serve as the compatibility layer — all pointing at the same canonical `wiki://space/slug` URIs.**
