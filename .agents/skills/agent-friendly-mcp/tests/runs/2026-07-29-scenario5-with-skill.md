# Scenario 5 (Resources) — with-skill run (2026-07-28 rebase re-run)

- **Date:** 2026-07-29
- **Tree:** `1b0b743` (main, post #130 rebase + #133 §6 fixes)
- **Mode:** with-skill (fresh general-purpose subagent on `claude-fable-5`; read `SKILL.md` + all eight `references/` files, explicitly forbidden from `tests/` and `decisions/`; 11 tool-uses, all reads)
- **Score:** 8/8

## Exact prompt given

Same wiki prompt as the baseline (see `2026-07-29-scenario5-baseline.md`), preceded by an instruction to read `agent-friendly-mcp/SKILL.md` and every file under `references/` as authoritative guidance, and **not** to read anything under `tests/` or `decisions/`.

## Per-assertion scoring

| # | Assertion | Result | Evidence |
| --- | --- | --- | --- |
| A1 | URIs stable, hierarchical, domain nouns, not internal numeric ids | **PASS** | `wiki://spaces/{space}/pages/{slug}` built from "domain nouns the wiki already guarantees — space keys and page slugs, never internal page ids" (`[4.uri-stable]`, `[4.uri-nouns]` cited). |
| A2 | Index entries carry native triage metadata, no inlined body, ≤3-sentence summary | **PASS** | Entry uses native `uri`/`name`/`title`/`description`/`mimeType`/`size`/`annotations.lastModified`; "`description` is capped at three sentences"; indexes "never bodies". |
| A3 | Chunkable large bodies with per-chunk callable URIs via `resources/templates/list` (or labeled tool fallback) | **PASS** | `…/chunks/{chunk_id}` URIs with a published chunk template ("because native `resources/read` takes only a URI"); tool fallback `wiki_get_page` accepts `chunk_id`. |
| A4 | Chunk ids stable across reads of the same page version; version change observable | **PASS** | "Chunk ids derive from heading anchors, so they are **stable across reads of the same page version**; when the page changes, ids may change but the change is observable via `lastModified`/`version`"; a stale chunk read returns `chunk_stale` with a re-read repair. |
| A5 | Custom metadata under namespaced `_meta`, never top-level `Resource` fields | **PASS** | Index entries are native-fields-only; "anything custom rides under a namespaced `_meta` key"; the chunk catalog is deliberately kept out of `_meta`-only delivery because clients may not surface `_meta`. |
| A6 | Subscriptions: `resources.subscribe` advertised, `resourceSubscriptions` filter of `subscriptions/listen`, `notifications/resources/updated` tagged with `subscriptionId`, distinguished from `list_changed` | **PASS** | Exactly the 2026-07-28 mechanism: `subscriptions/listen` with `resourceSubscriptions: [uri]` ("the replacement for the removed `resources/subscribe`"), notifications tagged `_meta.io.modelcontextprotocol/subscriptionId`, catalog changes routed via the `resourcesListChanged` filter → `list_changed`; `ttlMs` + `lastModified` stated as the freshness bound for non-subscribers. |
| A7 | Templates + completion for `{space}`/`{slug}` where `completions` negotiated | **PASS** | Templates published; `completion/complete` for `{space}` (enum-like) and `{slug}` (prefix, scoped by entered `{space}`); no-completions fallback declared. |
| A8 | Tool fallback self-sufficient from `tools/list` alone | **PASS** | `wiki_list_spaces`/`wiki_search_pages`/`wiki_get_page` — "callable without any value learnable only from resources"; `wiki_list_spaces` explicitly provides the tool-reachable enumeration of the constrained `space` value. |

**Passed:** all (8/8).
Notable beyond the assertions: honest `ttlMs`/`cacheScope` on every read (`[8.cacheable-results]`), rename-as-remove-plus-add with a `page_moved` repair window, and the `[6.rename]` spellings on the JSON-RPC carrier — all 2026-07-28 rebase content, all absent from the baseline.

## Full final output

Verbatim final message returned by the with-skill subagent (11 tool-uses, all file reads):

All nine files read (SKILL.md plus the eight references). Design below follows the contract-checklist rules; rule ids cited inline.

---

# Resource surface design: `docs-wiki` MCP server

**Revision target** (design-workflow Step 0, `[1.spec-revision]`): MCP **2026-07-28**, no extensions required. Depends on server capabilities `resources.subscribe`, `resources.listChanged`, and `completions`; every dependency has a declared weaker-client fallback (below). Cache hints (`ttlMs`/`cacheScope`) and `subscriptions/listen` are used natively.

## 1. URI scheme

Stable, hierarchical, built from domain nouns the wiki already guarantees — space keys and page slugs, never internal page ids (`[4.uri-stable]`, `[4.uri-nouns]`):

```
wiki://server/capabilities                       capability summary (read this first)
wiki://spaces                                    index of spaces
wiki://spaces/{space}/pages{?query,updated_after,cursor}   filtered, paginated page index for one space
wiki://spaces/{space}/pages/{slug}               one page (summary-gated body, see §3)
wiki://spaces/{space}/pages/{slug}/chunks/{chunk_id}       one chunk of a large page
```

`resources/list` stays small and deterministic (`[9.deterministic-order]`): the capability summary, `wiki://spaces`, and one index resource per space — a dozen entries, not thousands of pages (`[2.index-not-bodies]`). Page enumeration happens by *reading* a space's index resource; its `cursor`/`query`/`updated_after` variables are published in the resource template so filtering and continuation are discoverable, in-band, and don't pretend to be native list-method parameters (`[8.native-pagination]` governs only the four native list methods, which here return the small catalog with native `nextCursor` semantics — omission means done).

Every `resources/list` and `resources/read` result carries the required `ttlMs` and `cacheScope` (`[8.cacheable-results]`). Because wiki content changes during the day and page visibility follows per-user ACLs: page bodies and indexes get `ttlMs: 300000` (5 min), `cacheScope: "private"`; the space list gets `ttlMs: 3600000`. These are honest volatility statements, and they are the staleness bound for clients that never open a listen stream (see §5).

## 2. Index entry vs full body

An index read (`wiki://spaces/eng/pages?query=deploy`) returns triage metadata only — never bodies (`[4.index-vs-body]`). Each entry uses **native `Resource` fields only**; anything custom rides under a namespaced `_meta` key (`[4.triage-metadata]`, SKILL.md native-vs-convention rule):

```json
{
  "uri": "wiki://spaces/eng/pages/deploy-runbook",
  "name": "deploy-runbook",
  "title": "Production deploy runbook",
  "description": "Step-by-step production deploy procedure for the api and worker fleets. Covers rollback and the freeze calendar.",
  "mimeType": "text/markdown",
  "size": 148230,
  "annotations": {"lastModified": "2026-07-29T14:02:11Z"}
}
```

`description` is capped at three sentences (`[4.short-summaries]`); `size` lets the agent estimate token cost before fetching; `annotations.lastModified` is the version signal it compares against a cached copy. The index body itself carries in-band continuation (`next_cursor` as a URI-buildable value for the template's `{cursor}` variable) and explicit truncation with a repair hint naming `query`/`updated_after` when a cap is hit (`[8.truncation]`).

## 3. Large-page delivery

Summary-first, with stable chunking (`[4.summary-first]`, `[4.chunkable]`):

- **Small pages** (≤ ~16 KB): `resources/read` of the page URI returns the full markdown body. One read, no ceremony.
- **Large pages**: the page URI returns a *summary document* — an abstract, the heading outline, and a chunk map — so the agent opts in to bulk content instead of receiving 100 KB by default:

```json
{
  "resource_uri": "wiki://spaces/eng/pages/deploy-runbook",
  "version": "2026-07-29T14:02:11Z",
  "summary": "Production deploy procedure: preflight checks, staged rollout, verification, rollback.",
  "outline": [
    {"chunk_id": "preflight", "title": "Preflight checks", "size": 9210},
    {"chunk_id": "rollout", "title": "Staged rollout", "size": 41022},
    {"chunk_id": "rollback", "title": "Rollback", "size": 18734}
  ],
  "next_chunk_id": "preflight",
  "next_chunk_uri": "wiki://spaces/eng/pages/deploy-runbook/chunks/preflight"
}
```

Chunk ids derive from heading anchors, so they are **stable across reads of the same page version**; when the page changes, ids may change but the change is observable via `lastModified`/`version` (`[4.chunk-stability]`). Each chunk body echoes `resource_uri`, `chunk_id`, `version`, and an in-band `next_chunk_uri` — the agent can walk chunk N+1 without the outline, and can cite `resource_uri` + `chunk_id` back to any tool. Every chunk has its own URI published as a resource template, because native `resources/read` takes only a URI (`[4.chunk-fetch-path]`); a chunk catalog under `_meta` would be auxiliary only, since clients may never surface `_meta` to the model.

## 4. Discovering parameterized lookup (space + slug)

Two native primitives, together:

**Resource templates** (`[4.templates]`) — `resources/templates/list` publishes the shapes, so an agent that knows a space and slug constructs the URI directly instead of browsing:

```json
{
  "uriTemplate": "wiki://spaces/{space}/pages/{slug}",
  "name": "wiki_page",
  "title": "Wiki page",
  "description": "One wiki page by space key and page slug. Large pages return a summary plus chunk map; fetch chunks via the wiki_page_chunk template.",
  "mimeType": "text/markdown"
}
```

(plus templates for the space index and `.../chunks/{chunk_id}`.)

**Completion** (`[4.template-completion]`, `[2.completion]`) — the server advertises `completions` via `server/discover` and implements `completion/complete` for `{space}` (small, enum-like) and `{slug}` (prefix match, scoped by the already-entered `{space}`). This is exactly the "large, dynamic, hard-to-guess value set" completion exists for: the agent builds a valid URI proactively instead of learning the slug space through failed reads. For clients that never negotiate completions, the fallback is the structured not-found error whose `repair` routes to the search tool (§6).

## 5. Change notification

Content changes during the day, so mutable-resource subscriptions are advertised (`[4.subscriptions]`):

- The server declares `resources.subscribe`. A client watching a page opens `subscriptions/listen` with `resourceSubscriptions: ["wiki://spaces/eng/pages/deploy-runbook"]` (the 2026-07-28 replacement for the removed `resources/subscribe`), and the server emits `notifications/resources/updated` on that stream, tagged with `_meta.io.modelcontextprotocol/subscriptionId`. The notification says *re-read*; the fresh read carries the new `lastModified`, and the new chunk map if chunk ids moved.
- Catalog membership changes (page created, deleted, or re-slugged; space added) are a different fact: `resourcesListChanged` filter → `notifications/resources/list_changed` (`[9.list-vs-update]` — emit both only when both are true).
- Subscriptions are opt-in streams a client may never open, so they are **not** the freshness contract: the honest `ttlMs: 300000` on every `resources/read` plus `annotations.lastModified` bound staleness for everyone else.
- A page rename is remove-plus-add on the catalog: the old slug URI keeps resolving for a documented window, returning a `page_moved` error whose `repair.arguments` carries the new URI (`[9.rename]` discipline applied to resources).

## 6. Failure contract on the resource surface

Resource failures are JSON-RPC errors (`-32602` for not-found under 2026-07-28) carrying the unified §6 envelope in `error.data` — `machine_code`/`human_message` (the mandatory rename), `details`, `temporary`, `retry_after_ms`, one `repair` object naming a real callable surface (`[4.jsonrpc-errors]`, `[6.rename]`, `[6.repair-callable]`):

```json
{
  "machine_code": "page_not_found",
  "human_message": "No page 'deploy-runbok' in space 'eng'.",
  "details": {"field": "uri", "value": "wiki://spaces/eng/pages/deploy-runbok", "reason": "Slug does not match any page in this space."},
  "temporary": false,
  "retry_after_ms": null,
  "repair": {
    "next_step": "search_then_read",
    "tool": "wiki_search_pages",
    "arguments": {"query": "deploy runbok", "space": "eng"},
    "alternative": "Read the wiki://spaces/eng/pages index and pick a listed slug."
  },
  "resource_uri": "wiki://spaces/eng/pages/deploy-runbok",
  "request_id": "req_01J9WIKI7F"
}
```

A stale chunk read (`version` moved) returns `chunk_stale`, `temporary: false`, with `repair` pointing at re-reading the page URI to obtain the fresh chunk map — re-read-then-refetch, never guess.

## 7. Fallback for clients that handle resources poorly

Some clients expose resources badly or not at all, so the same indexed content is reachable through three read-only tools, **self-sufficient from `tools/list` alone** — callable without any value learnable only from resources (`[4.tool-fallback]`):

| Tool | Contract |
| --- | --- |
| `wiki_list_spaces` | No required args; returns the space index. This is the tool-reachable enumeration for the constrained `space` value. |
| `wiki_search_pages` | `query` required; `space`, `updated_after` optional (omission = all spaces / no time bound, declared per `[3.required-optional]`). Returns index entries (same shape as §2), paginated via a documented house `has_more`/`next_cursor` in `structuredContent` (`[8.house-pagination]`), with explicit truncation hints. |
| `wiki_get_page` | `space` + `slug` required; optional `chunk_id` (omission = full body for small pages, summary + chunk map for large — identical gating to the resource path, one contract, not two). |

All three: `readOnlyHint: true`, `openWorldHint: false` (internal wiki, closed world); `destructiveHint`/`idempotentHint` omitted because they are meaningful only when `readOnlyHint` is false (`[3.annotation-defaults]`). Each publishes an `outputSchema` and returns `structuredContent` as the authoritative payload with a text fallback in `content` (`[3.output-schema]`); tool-side failures carry the *same* envelope as §6, just as `isError: true` tool results with `code`/`message` unrenamed (`[6.tool-errors]`).

## 8. Capability summary (the first read)

Exposed at `wiki://server/capabilities`, mirrored in `instructions` as advisory only (`[2.instructions-advisory]`), and reachable via `wiki_list_spaces`-adjacent prose for resource-blind clients. It declares: what the server does (read/search/browse one wiki) and does **not** do (no page editing — writes would be tools, `[4.write-is-tool]` — no attachments over 10 MB, no cross-wiki federation); the revision target; the error carriers (`structuredContent` on tool results, `error.data` on RPC); and the negotiated-capability fallback table — `no_completions` → structured not-found errors with `repair` to `wiki_search_pages`; `no_subscriptions` → `ttlMs` + `lastModified` re-read guidance; `poor_resource_support` → the three tools above.

**Done criteria walk** (checklist §1–§9): §1 identity/caps declared in the summary; §2 compact catalog, index-not-bodies, completion; §3 fallback tools with honest annotations and closed schemas; §4 fully answered above; §5 not applicable — no prompts, none load-bearing (`[5.optional]`); §6 unified envelope, both carriers; §7 not applicable — all reads complete within a normal turn; §8 summary-first, chunking, honest cache hints, explicit truncation; §9 deterministic ordering, list-changed notifications, rename-as-remove-plus-add; a fingerprint is optional here and warranted only if a target client pins the surface (`[9.fingerprint]`).
